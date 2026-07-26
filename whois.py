#!/usr/bin/env python3
"""
whois.py -- find a PERSON across every store that holds one. The people half of `whereis.py`.

WHY THIS EXISTS
  People were "hard to find" not because the data was missing but because nothing ever told a
  session where to look. Sygma staff, Sygma customers/suppliers, Canary Detect contacts and Pete's
  phone are four separate systems with four different conventions; a session would check one, find
  nothing, and report "no such person". This asks all four and says which one answered.

  Plan: [[plan-finding-and-adding-things]] step B1. Companion to `whereis.py` (which finds THINGS).

THE FOUR STORES, and nothing else
  hub.staff_directory   (Sygma platform, rsczwfstwkthaybxhszy)  Sygma staff
  public.contacts       (Sygma platform, same project)          Sygma customers / suppliers / partners
  res.partner           (Odoo)                                   Canary Detect customers + suppliers
  public.google_contacts(Command Centre, zhexcaflgahdcbzvbyfq)   what is actually on Pete's phone

  Provenance is kept UPSTREAM in each business's own system; the phone is downstream of them. The CC
  is NOT a people home -- it holds the phone mirror and bank details, neither of which is a person's
  record. So this tool reads four stores and never tries to merge them into a fifth.

THE FIVE RULES (from the plan, each one earned)
  1. Search name, email AND normalised number -- never a raw string match. Google holds the
     international form (+447980...), the CRM strips the leading zero, Odoo is its own thing again.
     So every number is reduced to its last 9 digits before comparison.
  2. A SHARED NUMBER MEANS SAME ORGANISATION, NEVER SAME PERSON. 122 numbers are shared in the CRM
     and the busiest sits on 11 people. A naive lookup merges whole teams into one human. When a
     phone match returns several different names, they are shown as an organisation group and
     labelled as such -- never collapsed.
  3. Always name WHICH STORE answered, so a wrong answer is traceable to its source.
  4. Say plainly when someone is in NONE of them. Silence must not read as "no such person".
  5. Refresh-on-use, no cron (Pete, 23 Jul: a full refresh measured 3.4s, so a cron is not worth it).
     --refresh re-syncs the phone mirror inline before searching.

USAGE
  VAULT=/tmp/pbs python3 /tmp/pbs/whois.py "Wayne Clarke"
  VAULT=/tmp/pbs python3 /tmp/pbs/whois.py "07980 779665"
  VAULT=/tmp/pbs python3 /tmp/pbs/whois.py "@clancygroup"      # domain -> everyone there
  VAULT=/tmp/pbs python3 /tmp/pbs/whois.py "Wayne" --json
  VAULT=/tmp/pbs python3 /tmp/pbs/whois.py "Wayne" --refresh   # re-sync the phone mirror first

FAIL-SOFT: a store that errors is REPORTED as unreachable, never silently skipped -- a missing
store must not look like a missing person (rule 4 applied to the tool's own failures).
"""
import re
import json, os, re, sys, subprocess, urllib.request, urllib.parse, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.join(VAULT, "Library", "processes", "secrets")

# ---------------------------------------------------------------- helpers

def _digits(s):
    return re.sub(r"\D", "", s or "")


def norm_phone(s):
    """Last 9 significant digits -- the only form that matches across all four stores.

    +447980779665 (Google) / 07980779665 (CRM) / 44 7980 779665 (Odoo) all reduce to 980779665.
    Nine, not ten: UK mobiles are 10 digits after the 0, but landlines and the Spanish numbers in
    Odoo vary, and 9 is the longest suffix that is stable across every format seen in the data.
    """
    d = _digits(s)
    return d[-9:] if len(d) >= 9 else ""


def looks_like_phone(q):
    d = _digits(q)
    return len(d) >= 7 and len(d) >= len(re.sub(r"[\s+()\-.]", "", q)) - 1


def looks_like_email_or_domain(q):
    return "@" in q or bool(re.match(r"^[a-z0-9.\-]+\.[a-z]{2,}$", q.strip().lower()))


# ---------------------------------------------------------------- stores

def _platform():
    k = json.load(open(os.path.join(SEC, "sygma-portal-supabase-keys.json")))
    return k["url"].rstrip("/"), k["service_role"]


def _rest(url, key, path, params, schema=None):
    u = f"{url}/rest/v1/{path}?" + urllib.parse.urlencode(params)
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if schema:
        h["Accept-Profile"] = schema
    with urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=30) as r:
        return json.load(r)


def _or(fields, q):
    """PostgREST or= filter, ILIKE on each field. Commas/parens in q would break the filter
    grammar, so they are stripped rather than escaped -- a name never needs them."""
    safe = re.sub(r"[(),]", " ", q).strip()
    return "(" + ",".join(f"{f}.ilike.*{safe}*" for f in fields) + ")"


def search_staff(q, phone):
    """Sygma staff -- hub.staff_directory on the platform."""
    url, key = _platform()
    rows = []
    seen = set()
    if not phone:
        rows += _rest(url, key, "staff_directory",
                      {"select": "*", "or": _or(["full_name", "preferred_name", "work_email"], q),
                       "limit": 25}, schema="hub")
    else:
        # phone columns are free-text; pull the roster (18 people) and match on the normalised form
        allrows = _rest(url, key, "staff_directory", {"select": "*", "limit": 200}, schema="hub")
        rows += [r for r in allrows
                 if phone in (norm_phone(r.get("work_mobile")), norm_phone(r.get("work_phone")))]
    out = []
    for r in rows:
        if r.get("employee_ref") in seen:
            continue
        seen.add(r.get("employee_ref"))
        out.append({
            "store": "Sygma platform - hub.staff_directory",
            "owner": "the Sygma platform (staff are managed in the app)",
            "name": r.get("full_name"),
            "detail": " / ".join(x for x in [r.get("job_title"), r.get("sub_business")] if x),
            "email": r.get("work_email"),
            "phone": r.get("work_mobile") or r.get("work_phone"),
            "extra": {k: v for k, v in {
                "status": r.get("employment_status"), "reports_to": r.get("reports_to"),
                "worker_type": r.get("worker_type"),
            }.items() if v},
        })
    return out


def search_contacts(q, phone):
    """Sygma customers / suppliers / partners -- public.contacts on the platform."""
    url, key = _platform()
    if phone:
        # A server-side ILIKE on the digits CANNOT work here and the failure is silent: the CRM
        # stores '01357 440222' WITH A SPACE, so ILIKE '%357440222%' matched 1 of the 10 records on
        # that number -- and the 9 it missed were the whole point (one QTS switchboard, 9 people).
        # PostgREST cannot strip non-digits in a filter, so normalise on this side instead. 1,611
        # rows is one request; correctness beats saving it. This IS rule 1, and it bit immediately.
        rows = [r for r in _rest(url, key, "contacts", {"select": "*", "limit": 5000})
                if phone in (norm_phone(r.get("phone")), norm_phone(r.get("mobile")))]
    else:
        rows = _rest(url, key, "contacts",
                     {"select": "*", "or": _or(["full_name", "email", "company_name"], q),
                      "limit": 60})
    out = []
    for r in rows:
        out.append({
            "store": "Sygma platform - public.contacts",
            "owner": "the Sygma platform (customers/suppliers are managed in the app)",
            "name": r.get("full_name"),
            "detail": " / ".join(x for x in [r.get("job_title"), r.get("company_name")] if x),
            "email": r.get("email"),
            "phone": r.get("mobile") or r.get("phone"),
            "extra": {k: v for k, v in {
                "type": r.get("type"), "account_status": r.get("account_status"),
                "source": r.get("source"),
            }.items() if v},
        })
    return out


def search_odoo(q, phone):
    """Canary Detect people -- Odoo res.partner, via the existing odoo-api helper (auth reused,
    never re-derived)."""
    # This Odoo has NO `mobile` field on res.partner (verified via `odoo-api.py fields`). It carries
    # `phone_sanitized` -- Odoo's own normalised form -- which is exactly what rule 1 wants.
    if phone:
        dom = ["|", "|", ["phone", "like", phone], ["phone_sanitized", "like", phone],
               ["phone_mobile_search", "like", phone]]
    else:
        dom = ["|", "|", ["name", "ilike", q], ["email", "ilike", q],
               ["parent_name", "ilike", q]]
    # odoo-api takes fields COMMA-SEPARATED, not JSON (see its _parse_fields)
    fields = ("name,email,phone,phone_sanitized,function,parent_name,"
              "customer_rank,supplier_rank,city")
    r = subprocess.run(
        [sys.executable, os.path.join(VAULT, "odoo-api.py"), "search-read",
         "res.partner", json.dumps(dom), fields, "--limit", "60"],
        capture_output=True, text=True, timeout=90,
        env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        # a subprocess traceback is noise; the LAST line is the actual cause and is what a reader
        # needs to see next to "this store did not answer"
        err = (r.stderr or r.stdout or "odoo failed").strip().splitlines()
        raise RuntimeError((err[-1] if err else "odoo failed")[:160])
    rows = json.loads(r.stdout or "[]")
    if phone:
        rows = [x for x in rows
                if phone in (norm_phone(x.get("phone")), norm_phone(x.get("phone_sanitized")))]
    out = []
    for x in rows:
        roles = []
        if (x.get("customer_rank") or 0) > 0:
            roles.append("customer")
        if (x.get("supplier_rank") or 0) > 0:
            roles.append("supplier")
        out.append({
            "store": "Odoo - res.partner",
            "owner": "Odoo (the Canary Detect record)",
            "name": x.get("name"),
            "detail": " / ".join(v for v in [x.get("function"), x.get("parent_name")] if v),
            "email": x.get("email") or None,
            "phone": x.get("phone") or x.get("phone_sanitized") or None,
            "extra": {k: v for k, v in {
                "role": ", ".join(roles) or None, "city": x.get("city") or None,
            }.items() if v},
        })
    return out


def search_phone_mirror(q, phone):
    """What is actually on Pete's phone -- public.google_contacts in the CC (a one-way mirror)."""
    # emails / phones / phones_e164 are ARRAYS in this table, and it is `organization` (US spelling).
    # Flatten each array to a string before matching, or a contact with two numbers is invisible.
    if phone:
        sql = ("SELECT * FROM google_contacts WHERE regexp_replace("
               "array_to_string(phones_e164,',') || ',' || array_to_string(phones,','),"
               "'[^0-9]','','g') LIKE '%" + phone + "%' LIMIT 60")
    else:
        safe = q.replace("'", "''")
        sql = ("SELECT * FROM google_contacts WHERE display_name ILIKE '%" + safe + "%' "
               "OR array_to_string(emails,',') ILIKE '%" + safe + "%' "
               "OR coalesce(organization,'') ILIKE '%" + safe + "%' LIMIT 60")
    r = subprocess.run([sys.executable, os.path.join(VAULT, "cc-sql.py")], input=sql,
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "VAULT": VAULT})
    txt = (r.stdout or "").strip()
    if not txt.startswith("["):
        raise RuntimeError(txt[:200] or "cc-sql failed")
    out = []
    def first(v):
        return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None)
    for x in json.loads(txt):
        out.append({
            "store": "Google Contacts (via the CC mirror)",
            "owner": "Pete's phone - a VIEW of the record, not a home",
            "name": x.get("display_name"),
            "detail": " / ".join(v for v in [x.get("job_title"), x.get("organization")] if v),
            "email": first(x.get("emails")),
            "phone": first(x.get("phones_e164")) or first(x.get("phones")),
            "extra": {"resource_name": x.get("resource_name")},
        })
    return out




def tidy_gaps(r):
    """Pete's rule (26 Jul 2026): if we TOUCH a contact and it needs a tidy-up, we do it THEN --
    correct a part-name to the full name, add the missing email, add the missing number. A record
    that is read and left half-finished is how "Freya" sat as a first name with no email for months,
    which is what let a duplicate get created on top of it.

    Returns a list of (what_is_missing, the_exact_fix_command).
    """
    gaps = []
    name = (r.get("name") or "").strip()
    res = (r.get("extra") or {}).get("resource_name")
    on_phone = "Google Contacts" in (r.get("store") or "")
    if name and len(name.split()) < 2:
        gaps.append(("no surname -- part name only",
                     f'people-api.py update {res} name "FULL NAME"' if res else
                     f'add the full name for "{name}" in its store'))
    if not r.get("email"):
        gaps.append(("no email",
                     f'people-api.py update {res} email ADDRESS' if res else
                     f'add an email for "{name}" in its store'))
    if not r.get("phone"):
        gaps.append(("no phone",
                     f'people-api.py update {res} phone NUMBER' if res else
                     f'add a phone for "{name}" in its store'))
    return gaps if (on_phone or gaps) else []


def partial_sweep(q, phone):
    """A multi-word name that matches nothing as a whole string may still match ON ONE TOKEN.

    Every store search is a substring ILIKE on the FULL query, so "Freya Finch" cannot match a
    record stored as just "Freya". Before 26 Jul 2026 that produced a confidently WRONG
    "NOT FOUND ... they genuinely have no record", and a duplicate contact was created on the
    strength of it. A partial hit is not a match, but it IS a duplicate risk, so it must be shown.
    """
    tokens = [t for t in re.split(r"[\s,]+", q) if len(t) > 2]
    if len(tokens) < 2:
        return [], []
    near, failed = [], []
    seen = set()
    for tok in tokens:
        for label, fn in STORES:
            try:
                for r in fn(tok, ""):
                    key = (r.get("store"), r.get("name"), r.get("email"), r.get("phone"))
                    if key in seen:
                        continue
                    seen.add(key)
                    r = dict(r); r["matched_token"] = tok
                    near.append(r)
            except Exception as e:
                msg = f"{label}: {type(e).__name__}: {str(e)[:80]}"
                if msg not in failed:
                    failed.append(msg)
    return near, failed


def add_hint(q):
    """On a TRUE negative, print the write command. whois is the read half of a pair; leaving the
    caller at 'not found' is what made the people system get skipped (Pete, 26 Jul 2026)."""
    return (
        "  TO CREATE THE RECORD -- pick the entity, the tool refuses to guess:\n"
        f"    contact.py add \"{q}\" --entity sygma    --email E --phone P   # Sygma platform, public.contacts\n"
        f"    contact.py add \"{q}\" --entity cd       --email E --phone P   # Canary Detect, Odoo res.partner\n"
        f"    contact.py add \"{q}\" --entity personal --email E --phone P   # no business record -> Google Contacts\n"
        "  (run as: VAULT=/tmp/pbs python3 /tmp/pbs/contact.py ...)"
    )


STORES = [
    ("Sygma staff", search_staff),
    ("Sygma contacts", search_contacts),
    ("Canary Detect (Odoo)", search_odoo),
    ("Pete's phone", search_phone_mirror),
]


# ---------------------------------------------------------------- output

def refresh_mirror():
    """Rule 5: refresh-on-use. 3.4s is cheaper than a cron nobody asked for."""
    p = os.path.join(VAULT, "google-contacts-sync.py")
    if not os.path.exists(p):
        return "google-contacts-sync.py not present - mirror NOT refreshed"
    r = subprocess.run([sys.executable, p], capture_output=True, text=True, timeout=180,
                       env={**os.environ, "VAULT": VAULT})
    return "phone mirror refreshed" if r.returncode == 0 else \
           f"mirror refresh FAILED ({(r.stderr or '')[:120]}) - results may be stale"


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    do_refresh = "--refresh" in args
    q = " ".join(a for a in args if not a.startswith("--")).strip()
    if not q:
        print(__doc__.split("USAGE")[1].strip() if "USAGE" in __doc__ else "usage: whois.py <name|phone|email>")
        return 2

    notes = []
    if do_refresh:
        notes.append(refresh_mirror())

    phone = norm_phone(q) if looks_like_phone(q) else ""
    results, failed = [], []
    for label, fn in STORES:
        try:
            results.extend(fn(q, phone))
        except Exception as e:                      # rule 4, applied to the tool itself
            failed.append(f"{label}: {type(e).__name__}: {str(e)[:110]}")

    if as_json:
        print(json.dumps({"query": q, "normalised_phone": phone or None,
                          "results": results, "stores_unreachable": failed,
                          "notes": notes}, indent=2))
        return 0

    print(f"whois: {q}" + (f"   (matched on the number ...{phone})" if phone else ""))
    for n in notes:
        print(f"  · {n}")
    print()

    if not results:
        # rule 4 -- say it plainly, and never let an unreachable store read as "no such person"
        if failed:
            print("  NO MATCH in the stores that answered -- but some did NOT answer, so this is")
            print("  NOT a reliable 'no such person':")
            for f in failed:
                print(f"    ⚠ {f}")
        else:
            near, near_failed = partial_sweep(q, phone)
            if near:
                print(f"  NO EXACT MATCH for '{q}' -- but {len(near)} record(s) match PART of that name.")
                print("  DO NOT create a new record before ruling these out; that is how duplicates are made.")
                print()
                for r in near[:15]:
                    bits = [b for b in (r.get("email"), r.get("phone"), r.get("detail")) if b]
                    print(f"    ~ {r.get('name')}  [matched on '{r.get('matched_token')}']")
                    print(f"        {r.get('store')}" + (f" -- {' / '.join(bits)}" if bits else ""))
                print()
                print("  If none of these is the person, create the record:")
                print(add_hint(q))
                return 0
            print("  NOT FOUND in any of the four stores (staff, Sygma contacts, Odoo, the phone).")
            print("  That is a real negative: all four answered. They genuinely have no record.")
            print()
            print(add_hint(q))
        return 0

    # rule 2 -- a shared number is an ORGANISATION, never one person
    if phone:
        names = {(r["name"] or "").strip().lower() for r in results if r.get("name")}
        if len(names) > 1:
            print(f"  ⚠ THIS NUMBER IS SHARED BY {len(names)} DIFFERENT PEOPLE.")
            print("    A shared number means SAME ORGANISATION, never the same person.")
            print("    They are listed separately below and must not be merged.\n")

    by_store = {}
    for r in results:
        by_store.setdefault(r["store"], []).append(r)

    for store, rows in by_store.items():
        print(f"  ── {store}   ({len(rows)})")
        print(f"     owner: {rows[0]['owner']}")
        for r in rows[:15]:
            line = f"     • {r['name'] or '(no name)'}"
            if r.get("detail"):
                line += f" — {r['detail']}"
            print(line)
            reach = " · ".join(x for x in [r.get("email"), r.get("phone")] if x)
            if reach:
                print(f"       {reach}")
            extra = {k: v for k, v in (r.get("extra") or {}).items() if v}
            if extra:
                print("       " + " · ".join(f"{k}: {v}" for k, v in extra.items()))
            # Pete's touch-it-tidy-it rule -- fix the record NOW, in this session, not "one day"
            gaps = tidy_gaps(r)
            if gaps:
                print(f"       ⚠ TIDY THIS RECORD NOW ({', '.join(g[0] for g in gaps)})")
                print("         Pete's rule: if we touch a contact and it needs tidying, we do it")
                print("         in the SAME session. Do not read it and move on.")
                for _, fix in gaps:
                    print(f"           VAULT=/tmp/pbs python3 /tmp/pbs/{fix}"
                          if fix.startswith("people-api.py") else f"           {fix}")
        if len(rows) > 15:
            print(f"     … and {len(rows) - 15} more in this store")
        print()

    if failed:
        print("  ⚠ stores that did NOT answer (so this answer is incomplete):")
        for f in failed:
            print(f"    {f}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"whois: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
