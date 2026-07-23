#!/usr/bin/env python3
# CRON-META
# what: One-way sync of Google Contacts into the CC mirror public.google_contacts
# why: Google Contacts is the SSOT for people who are neither Sygma staff (hub.staff_directory) nor
#      training customers (Portal public.contacts). Without a mirror the CC cannot answer "who is X",
#      which is why looking people up kept failing - the locator had no entry for Contacts at all.
# reads: Google People API (people/me/connections, all contact fields)
# writes: CC public.google_contacts (full refresh; the table's trigger blocks every other writer)
# entity: PA-Command-Centre
# report: stdout
# secrets: google-seo-service-account.json, command-centre-supabase-keys.json
# schedule: 20 6 * * *
# timezone: Atlantic/Canary
# CRON-META-END
"""google-contacts-sync.py -- pull Google Contacts into the CC mirror.

DIRECTION IS ONE WAY, ALWAYS. Google Contacts is the master for this slice of people.
Nothing in the CC writes back, and public.google_contacts refuses writes from anything but
this job (BEFORE INSERT/UPDATE/DELETE trigger on `app.gc_sync`). So a person added to the
mirror by mistake fails loudly instead of being silently wiped by the next run.

New people go in GOOGLE CONTACTS, never here.

Phone numbers are stored twice: `phones` as Pete entered them, and `phones_e164` normalised to
full international form so they can be matched against the Portal CRM (which stores UK numbers
with the leading zero stripped). Matching on a phone alone NEVER proves identity - 130 CRM numbers
are shared by more than one person (company switchboards) - so a shared number means same
organisation, not same person.

Usage:  VAULT=/tmp/pbs python3 google-contacts-sync.py [--dry-run] [--json]
Exit 0 = ran (whatever it found). Exit 1 = could not sync.
"""
import os, sys, json, re, time, ssl, socket, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SECRETS = os.path.join(VAULT, "Library", "processes", "secrets")
FIELDS = ("names,emailAddresses,phoneNumbers,organizations,biographies,"
          "memberships,metadata,occupations")

# ---------------------------------------------------------------- phone normalisation
# Google holds international (+34 / +44); the Portal CRM strips the leading zero off everything,
# mobiles and landlines alike. Both conventions have to land on the same key.
CCS = ['353','351','352','356','380','420','421','44','34','33','49','39','31','32','41','43',
       '45','46','47','48','61','64','1','7']

def e164(v, default_cc='44'):
    """Full international form, digits only. None when it cannot be resolved safely."""
    if not v:
        return None
    s = str(v).strip()
    d = re.sub(r'\D', '', s)
    if len(d) < 6:
        return None
    if s.startswith('+') or d.startswith('00'):
        d = d[2:] if d.startswith('00') else d
        for cc in CCS:                                   # e.g. 353 0872609638 -> drop the trunk zero
            if d.startswith(cc) and d[len(cc):len(cc)+1] == '0':
                return cc + d[len(cc)+1:]
        return d
    if d.startswith('0'):
        return default_cc + d[1:]
    if len(d) == 10 and d[0] in '12378':                 # UK with the trunk zero stripped
        return '44' + d
    if len(d) == 9 and d[0] in '6789':                   # bare Spanish
        return '34' + d
    for cc in CCS:
        if len(cc) >= 2 and d.startswith(cc) and len(d) >= len(cc) + 9:
            if d[len(cc):len(cc)+1] == '0':
                return cc + d[len(cc)+1:]
            return d
    return None

# ---------------------------------------------------------------- transport
def _retry(fn, tries=5, base=0.6):
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise
            if i == tries - 1:
                raise
        except (urllib.error.URLError, ssl.SSLError, socket.timeout, OSError):
            if i == tries - 1:
                raise
        time.sleep(base * (2 ** i))

def google_token():
    sys.path.insert(0, VAULT)
    import importlib.util
    spec = importlib.util.spec_from_file_location("people_api", os.path.join(VAULT, "people-api.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)                            # people-api.py has an __main__ guard
    return m.get_token()

def fetch_contacts(tok):
    out, page = [], None
    while True:
        url = ("https://people.googleapis.com/v1/people/me/connections"
               "?pageSize=1000&personFields=" + FIELDS)
        if page:
            url += "&pageToken=" + page
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
        d = _retry(lambda: json.loads(urllib.request.urlopen(req, timeout=60).read().decode()))
        out += d.get("connections", [])
        page = d.get("nextPageToken")
        if not page:
            break
    return out

def group_names(tok):
    req = urllib.request.Request("https://people.googleapis.com/v1/contactGroups?pageSize=200",
                                 headers={"Authorization": "Bearer " + tok})
    d = _retry(lambda: json.loads(urllib.request.urlopen(req, timeout=40).read().decode()))
    return {g["resourceName"]: (g.get("formattedName") or "") for g in d.get("contactGroups", [])}

# ---------------------------------------------------------------- CC write
CC_REF = "zhexcaflgahdcbzvbyfq"

def cc_token():
    """Same env-first, file-fallback path as cc-sql.py (the house pattern for CC DDL/DML)."""
    t = (os.environ.get("SUPABASE_TOKEN") or "").strip()
    if t:
        return t
    return open(os.path.join(SECRETS, "supabase-token")).read().strip()

def cc_sql(sql):
    """Run SQL against the CC via the Supabase Management API - the same route cc-sql.py uses.
    Multi-statement is supported, which is what lets the whole refresh run in one transaction."""
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/%s/database/query" % CC_REF,
        data=json.dumps({"query": sql}).encode(), method="POST",
        headers={"Authorization": "Bearer " + cc_token(), "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
    return _retry(lambda: urllib.request.urlopen(req, timeout=120).read().decode())

def q(s):
    return "NULL" if s is None else "'" + str(s).replace("'", "''") + "'"

def arr(vals):
    vals = [v for v in vals if v]
    if not vals:
        return "'{}'::text[]"
    return "ARRAY[" + ",".join(q(v) for v in vals) + "]::text[]"

# ---------------------------------------------------------------- main
def row_for(p, groups):
    names = (p.get("names") or [{}])[0]
    org = (p.get("organizations") or [{}])[0]
    phones = [(x.get("value") or "").strip() for x in (p.get("phoneNumbers") or [])]
    emails = [(x.get("value") or "").strip().lower() for x in (p.get("emailAddresses") or [])]
    e164s = []
    for v in phones:
        k = e164(v)
        if k and k not in e164s:
            e164s.append(k)
    mems = []
    for m in (p.get("memberships") or []):
        rn = ((m.get("contactGroupMembership") or {}).get("contactGroupResourceName"))
        if rn and groups.get(rn):
            mems.append(groups[rn])
    meta = p.get("metadata") or {}
    upd = None
    for s in (meta.get("sources") or []):
        upd = (s.get("updateTime") or upd)
    return {
        "resource_name": p["resourceName"],
        "display_name": (names.get("displayName") or "").strip() or None,
        "given_name": names.get("givenName"),
        "family_name": names.get("familyName"),
        "emails": emails, "phones": phones, "phones_e164": e164s,
        "organization": (org.get("name") or "").strip() or None,
        "job_title": (org.get("title") or (p.get("occupations") or [{}])[0].get("value") or None),
        "notes": ((p.get("biographies") or [{}])[0].get("value") or None),
        "groups": mems, "google_updated": upd,
    }

def main():
    dry = "--dry-run" in sys.argv
    as_json = "--json" in sys.argv
    tok = google_token()
    groups = group_names(tok)
    people = fetch_contacts(tok)
    rows = [row_for(p, groups) for p in people]
    if dry:
        out = {"fetched": len(rows), "with_phone": sum(1 for r in rows if r["phones"]),
               "with_email": sum(1 for r in rows if r["emails"]),
               "resolvable_e164": sum(1 for r in rows if r["phones_e164"]), "wrote": 0}
        print(json.dumps(out) if as_json else out)
        return 0

    values = []
    for r in rows:
        values.append("(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())" % (
            q(r["resource_name"]), q(r["display_name"]), q(r["given_name"]), q(r["family_name"]),
            arr(r["emails"]), arr(r["phones"]), arr(r["phones_e164"]),
            q(r["organization"]), q(r["job_title"]), q(r["notes"]), arr(r["groups"]),
            q(r["google_updated"]) + "::timestamptz" if r["google_updated"] else "NULL"))

    # Full refresh inside ONE transaction, with the guard flag set for this transaction only.
    sql = ["BEGIN;", "SET LOCAL app.gc_sync = 'on';", "DELETE FROM public.google_contacts;"]
    for i in range(0, len(values), 200):
        sql.append("INSERT INTO public.google_contacts (resource_name,display_name,given_name,"
                   "family_name,emails,phones,phones_e164,organization,job_title,notes,groups,"
                   "google_updated,synced_at) VALUES " + ",".join(values[i:i+200]) + ";")
    sql.append("COMMIT;")
    cc_sql("\n".join(sql))

    out = {"fetched": len(rows), "wrote": len(values),
           "with_phone": sum(1 for r in rows if r["phones"]),
           "with_email": sum(1 for r in rows if r["emails"]),
           "resolvable_e164": sum(1 for r in rows if r["phones_e164"])}
    print(json.dumps(out) if as_json else
          "google-contacts-sync: %(fetched)d fetched, %(wrote)d written "
          "(%(with_phone)d with a phone, %(with_email)d with an email, "
          "%(resolvable_e164)d numbers normalised)" % out)
    return 0

if __name__ == "__main__":
    sys.exit(main())
