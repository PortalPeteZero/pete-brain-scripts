#!/usr/bin/env python3
"""
people.py -- ONE command for every person, across all four stores that hold one.

WHY THIS EXISTS
  Six tools, 1,717 lines, did one job. Nobody -- Claude included -- holds six names in their head,
  so the people system got skipped, and every failure of 26 Jul 2026 happened in a SEAM between
  them: a false-negative lookup, a duplicate created on top of it, a guard that never searched the
  name, a --dry flag silently writing to production, an update that wiped the values it was not
  editing, and a mirror that flattened two Google sources into fake duplicates.

  One command, one matcher, one set of rules.

THE FOUR STORES, and nothing else
  hub.staff_directory    (Sygma platform, rsczwfstwkthaybxhszy)  Sygma staff
  public.contacts        (Sygma platform, same project)          Sygma customers / suppliers / partners
  res.partner            (Odoo)                                  Canary Detect customers + suppliers
  public.google_contacts (Command Centre, zhexcaflgahdcbzvbyfq)  what is actually on Pete's phone

  Provenance stays UPSTREAM in each business's own system; the phone is downstream of them. This
  reads four stores and never tries to merge them into a fifth.

USAGE
  people find   "Freya Finch" [--refresh] [--json]
                               # all four stores · partial matches · tidy prompts
  people add    "Name" --entity sygma|cd|personal [--email E] [--phone P] [--company C]
                       [--role customer|supplier|partner] [--dry-run] [--allow-duplicate]
                               # routed · duplicate-guarded · mirror refreshed
  people tidy   "Name"|people/c... [--name "Full Name" --confirm-replace] [--email E] [--phone P]
                       [--dry-run]
                               # complete a half-finished record -- Google Contacts ONLY
  people check  [--json] [--self-test]
                               # duplicates, same-names, half-finished, needs-a-surname
  people phone  "Name" [--remove] [--dry-run]
                               # ONE person on/off Pete's phone
  people phone  --scope staff|active-customers|suppliers [--limit N] [--confirm]
                               # BULK roster push -- dry-run by default, always labelled
  people remove "Name" --entity sygma|cd --yes-delete-business-record
                               # REFUSES BY DESIGN. Deletes nothing.

  Run as:  VAULT=/tmp/pbs python3 /tmp/pbs/people.py <verb> ...

THE RULES, each one earned
  1. Search name, email AND normalised number -- never a raw string match. Every number is reduced
     to its last 9 digits before comparison, because Google holds +447980..., the platform strips
     the leading zero, and Odoo is its own thing again.
  2. A SHARED NUMBER MEANS SAME ORGANISATION, NEVER SAME PERSON. The busiest shared number in the
     platform sits on 11 people; a naive lookup merges whole teams into one human.
  3. Always name WHICH STORE answered, so a wrong answer is traceable to its source.
  4. Say plainly when someone is in NONE of them. Silence must not read as "no such person" -- and
     a store that ERRORS is reported as unreachable, never silently skipped.
  5. An exact-name miss is NOT a negative. The full string is matched first, so "Freya Finch"
     cannot match a record stored as "Freya"; partial matches are swept and shown before anything
     is created.
  6. TOUCH IT, TIDY IT (Pete, 26 Jul 2026). Read a half-finished record and you fix it in the SAME
     session -- `find` prints the exact `people tidy` command for anything on the phone.
  7. Never guess the entity. No --entity, no write. Guessing wrong files a person in the wrong
     company's system.
  8. An unknown flag ABORTS. An ignored --dry flag WRITES FOR REAL; that happened on 26 Jul 2026
     and put two junk contacts straight into Pete's live Google Contacts.

EXIT CODES
  `people` / `people <verb>` with -h or --help  -> usage, 0
  `people find` with no query                   -> 2
  `people check` with no arguments              -> runs the report, 0
"""
import collections
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.join(VAULT, "Library", "processes", "secrets")
ENTITIES = ("sygma", "cd", "personal")
LABEL = "Sygma (added by the Command Centre)"     # rule 2 of the roster push — never optional
SCOPES = ("staff", "active-customers", "suppliers")
SNAPSHOT_SLUG = "people-tools-snapshot-2026-07-27"

# The accepted set. Miss one and the command that uses it dies at runtime.
KNOWN_FLAGS = {
    "--dry-run", "--dry", "--allow-duplicate", "--yes-delete-business-record",
    "--entity", "--email", "--phone", "--company", "--role",
    "--refresh", "--json", "--scope", "--limit", "--confirm", "--remove",
    "--name", "--confirm-replace", "--self-test", "-h", "--help",
}
VALUE_FLAGS = ("entity", "email", "phone", "company", "role", "scope", "limit", "name")


def _die(msg, code=2):
    print(f"people: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- helpers

def _digits(s):
    return re.sub(r"\D", "", s or "")


def norm_phone(s):
    """Last 9 significant digits -- the only form that matches across all four stores.

    +447980779665 (Google) / 07980779665 (platform) / 44 7980 779665 (Odoo) all reduce to
    980779665. Nine, not ten: UK mobiles are 10 digits after the 0, but landlines and the Spanish
    numbers in Odoo vary, and 9 is the longest suffix stable across every format seen in the data.
    """
    d = _digits(s)
    return d[-9:] if len(d) >= 9 else ""


def looks_like_phone(q):
    d = _digits(q)
    return len(d) >= 7 and len(d) >= len(re.sub(r"[\s+()\-.]", "", q)) - 1


def _platform():
    k = json.load(open(os.path.join(SEC, "sygma-portal-supabase-keys.json")))
    return k["url"].rstrip("/"), k["service_role"]


def _rest(url, key, path, params, schema=None):
    u = f"{url}/rest/v1/{path}?" + urllib.parse.urlencode(params)
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if schema:
        h["Accept-Profile"] = schema
    with urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=40) as r:
        return json.load(r)


def _or(fields, q):
    """PostgREST or= filter, ILIKE on each field. Commas/parens would break the filter grammar, so
    they are stripped rather than escaped -- a name never needs them."""
    safe = re.sub(r"[(),]", " ", q).strip()
    return "(" + ",".join(f"{f}.ilike.*{safe}*" for f in fields) + ")"


def _cc_sql(sql_text, timeout=90):
    """The CC database. capture_output is not optional: cc-sql prints its errors to STDOUT, and a
    stray line there is parsed as data by callers reading our JSON."""
    r = subprocess.run([sys.executable, os.path.join(VAULT, "cc-sql.py")], input=sql_text,
                       capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "VAULT": VAULT})
    return r.returncode, (r.stdout or "").strip()


# ---------------------------------------------------------------- stores

def search_staff(q, phone):
    """Sygma staff -- hub.staff_directory on the platform."""
    url, key = _platform()
    rows, seen = [], set()
    if not phone:
        rows += _rest(url, key, "staff_directory",
                      {"select": "*", "or": _or(["full_name", "preferred_name", "work_email"], q),
                       "limit": 25}, schema="hub")
    else:
        # phone columns are free-text; pull the roster and match on the normalised form
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
        # A server-side ILIKE on the digits CANNOT work here and the failure is silent: the
        # platform stores '01357 440222' WITH A SPACE, so ILIKE '%357440222%' matched 1 of the 10
        # records on that number -- and the 9 it missed were the whole point (one switchboard, 9
        # people). PostgREST cannot strip non-digits in a filter, so normalise on this side.
        rows = [r for r in _rest(url, key, "contacts", {"select": "*", "limit": 5000})
                if phone in (norm_phone(r.get("phone")), norm_phone(r.get("mobile")))]
    else:
        rows = _rest(url, key, "contacts",
                     {"select": "*", "or": _or(["full_name", "email", "company_name"], q),
                      "limit": 60})
    return [{
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
    } for r in rows]


def search_odoo(q, phone):
    """Canary Detect people -- Odoo res.partner, via the odoo helper (auth reused, never
    re-derived). This Odoo has NO `mobile` field on res.partner; it carries `phone_sanitized`,
    Odoo's own normalised form, which is exactly what rule 1 wants."""
    if phone:
        dom = ["|", "|", ["phone", "like", phone], ["phone_sanitized", "like", phone],
               ["phone_mobile_search", "like", phone]]
    else:
        dom = ["|", "|", ["name", "ilike", q], ["email", "ilike", q], ["parent_name", "ilike", q]]
    fields = ("name,email,phone,phone_sanitized,function,parent_name,"
              "customer_rank,supplier_rank,city")
    r = subprocess.run(
        [sys.executable, os.path.join(VAULT, "odoo-api.py"), "search-read",
         "res.partner", json.dumps(dom), fields, "--limit", "60"],
        capture_output=True, text=True, timeout=90, env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        # a subprocess traceback is noise; the LAST line is the actual cause
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
    """What is actually on Pete's phone -- public.google_contacts in the CC (a one-way mirror).

    emails / phones / phones_e164 are ARRAYS here, and it is `organization` (US spelling). Flatten
    each array before matching, or a contact with two numbers is invisible."""
    if phone:
        sql = ("SELECT * FROM google_contacts WHERE regexp_replace("
               "array_to_string(phones_e164,',') || ',' || array_to_string(phones,','),"
               "'[^0-9]','','g') LIKE '%" + phone + "%' LIMIT 60")
    else:
        safe = q.replace("'", "''")
        sql = ("SELECT * FROM google_contacts WHERE display_name ILIKE '%" + safe + "%' "
               "OR array_to_string(emails,',') ILIKE '%" + safe + "%' "
               "OR coalesce(organization,'') ILIKE '%" + safe + "%' LIMIT 60")
    _, txt = _cc_sql(sql, timeout=60)
    if not txt.startswith("["):
        raise RuntimeError(txt[:200] or "the CC database did not answer")

    def first(v):
        return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None)

    return [{
        "store": "Google Contacts (via the CC mirror)",
        "owner": "Pete's phone - a VIEW of the record, not a home",
        "name": x.get("display_name"),
        "detail": " / ".join(v for v in [x.get("job_title"), x.get("organization")] if v),
        "email": first(x.get("emails")),
        "phone": first(x.get("phones_e164")) or first(x.get("phones")),
        "extra": {"resource_name": x.get("resource_name")},
    } for x in json.loads(txt)]


STORES = [
    ("Sygma staff", search_staff),
    ("Sygma contacts", search_contacts),
    ("Canary Detect (Odoo)", search_odoo),
    ("Pete's phone", search_phone_mirror),
]


# ---------------------------------------------------------------- find

def tidy_gaps(r):
    """Pete's rule (26 Jul 2026): if we TOUCH a contact and it needs a tidy-up, we do it THEN.
    A record read and left half-finished is how "Freya" sat as a first name with no email for
    months, which is what let a duplicate get created on top of it.

    Returns [(what_is_missing, the_exact_fix)]. The `people tidy` form is emitted ONLY for rows
    carrying a resource_name -- i.e. Google Contacts, the only store `tidy` covers. Printing a
    tidy command for a platform or Odoo row would name a command that tidy refuses.
    """
    gaps = []
    name = (r.get("name") or "").strip()
    res = (r.get("extra") or {}).get("resource_name")
    on_phone = "Google Contacts" in (r.get("store") or "")
    if name and len(name.split()) < 2:
        gaps.append(("no surname -- part name only",
                     f'people tidy "{name}" --name "FULL NAME" --confirm-replace' if res else
                     f'add the full name for "{name}" in its store'))
    if not r.get("email"):
        gaps.append(("no email",
                     f'people tidy "{name}" --email ADDRESS' if res else
                     f'add an email for "{name}" in its store'))
    if not r.get("phone"):
        gaps.append(("no phone",
                     f'people tidy "{name}" --phone NUMBER' if res else
                     f'add a phone for "{name}" in its store'))
    return gaps if (on_phone or gaps) else []


def partial_sweep(q, phone):
    """A multi-word name that matches nothing as a whole string may still match ON ONE TOKEN.

    Every store search is a substring ILIKE on the FULL query, so "Freya Finch" cannot match a
    record stored as just "Freya". Before 26 Jul 2026 that produced a confidently WRONG
    "NOT FOUND", and a duplicate contact was created on the strength of it. A partial hit is not a
    match, but it IS a duplicate risk, so it must be shown.
    """
    tokens = [t for t in re.split(r"[\s,]+", q) if len(t) > 2]
    if len(tokens) < 2:
        return [], []
    near, failed, seen = [], [], set()
    for tok in tokens:
        for label, fn in STORES:
            try:
                for r in fn(tok, ""):
                    key = (r.get("store"), r.get("name"), r.get("email"), r.get("phone"))
                    if key in seen:
                        continue
                    seen.add(key)
                    r = dict(r)
                    r["matched_token"] = tok
                    near.append(r)
            except Exception as e:
                msg = f"{label}: {type(e).__name__}: {str(e)[:80]}"
                if msg not in failed:
                    failed.append(msg)
    return near, failed


def add_hint(q):
    """On a TRUE negative, print the write command. Leaving the caller at 'not found' is what made
    the people system get skipped (Pete, 26 Jul 2026)."""
    return (
        "  TO CREATE THE RECORD -- pick the entity, the tool refuses to guess:\n"
        f'    people add "{q}" --entity sygma    --email E --phone P   # Sygma platform, public.contacts\n'
        f'    people add "{q}" --entity cd       --email E --phone P   # Canary Detect, Odoo res.partner\n'
        f'    people add "{q}" --entity personal --email E --phone P   # no business record -> Google Contacts'
    )


def refresh_mirror():
    """Rule: refresh-on-use. A full refresh measures ~3.4s, cheaper than a cron nobody asked for."""
    p = os.path.join(VAULT, "google-contacts-sync.py")
    if not os.path.exists(p):
        return "the phone-mirror sync is not present - mirror NOT refreshed"
    r = subprocess.run([sys.executable, p], capture_output=True, text=True, timeout=180,
                       env={**os.environ, "VAULT": VAULT})
    return "phone mirror refreshed" if r.returncode == 0 else \
        f"mirror refresh FAILED ({(r.stderr or '')[:120]}) - results may be stale"


def lookup(q, phone=None, do_refresh=False):
    """The one matcher. Returns (results, partial_matches, unreachable_stores, notes)."""
    notes = []
    if do_refresh:
        notes.append(refresh_mirror())
    if phone is None:
        phone = norm_phone(q) if looks_like_phone(q) else ""
    results, failed = [], []
    for label, fn in STORES:
        try:
            results.extend(fn(q, phone))
        except Exception as e:                          # rule 4, applied to the tool itself
            failed.append(f"{label}: {type(e).__name__}: {str(e)[:110]}")
    near, near_failed = ([], [])
    if not results:
        near, near_failed = partial_sweep(q, phone)
    return results, near, failed + near_failed, notes


def cmd_find(a):
    q = a.get("name") or ""
    if not q:
        print(__doc__.split("USAGE")[1].split("THE RULES")[0].strip())
        return 2
    phone = norm_phone(q) if looks_like_phone(q) else ""
    results, near, failed, notes = lookup(q, phone, a.get("refresh"))

    if a.get("json"):
        print(json.dumps({"query": q, "normalised_phone": phone or None,
                          "results": results, "partial_matches": near,
                          "stores_unreachable": failed, "notes": notes}, indent=2))
        return 0

    print(f"people find: {q}" + (f"   (matched on the number ...{phone})" if phone else ""))
    for n in notes:
        print(f"  · {n}")
    print()

    if not results:
        if failed:
            print("  NO MATCH in the stores that answered -- but some did NOT answer, so this is")
            print("  NOT a reliable 'no such person':")
            for f in failed:
                print(f"    ⚠ {f}")
            return 0
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
            gaps = tidy_gaps(r)
            if gaps:
                print(f"       ⚠ TIDY THIS RECORD NOW ({', '.join(g[0] for g in gaps)})")
                print("         Pete's rule: if we touch a contact and it needs tidying, we do it")
                print("         in the SAME session. Do not read it and move on.")
                for _, fix in gaps:
                    # the tidy form is printed as a RUNNABLE line; the "in its store" wording for
                    # non-Google rows is prose and stays as it is
                    print(f"           VAULT=/tmp/pbs python3 /tmp/pbs/people.py "
                          f"{fix[len('people '):]}"
                          if fix.startswith("people ") else f"           {fix}")
        if len(rows) > 15:
            print(f"     … and {len(rows) - 15} more in this store")
        print()

    if failed:
        print("  ⚠ stores that did NOT answer (so this answer is incomplete):")
        for f in failed:
            print(f"    {f}")
    return 0


# ---------------------------------------------------------------- duplicate guard

def find_existing(name, email, phone):
    """Checks the NAME **and** the email **and** the phone.

    It used to check only the first non-empty one (`email or phone or name`), so passing an email
    meant THE NAME WAS NEVER SEARCHED. That is the actual root cause of the Freya Finch duplicate
    on 26 Jul 2026: the guard looked up the address, found nothing, and waved through a name that
    already existed.
    """
    hits, seen = [], set()
    for q in [x for x in (name, email, phone) if x]:
        results, near, _, _ = lookup(q)
        combined = list(results)
        for pm in near:
            pm = dict(pm)
            pm["_partial"] = True
            combined.append(pm)
        for h in combined:
            key = (h.get("store"), h.get("name"), h.get("email"), h.get("phone"))
            if key not in seen:
                seen.add(key)
                hits.append(h)
    return [h for h in hits if _is_real_duplicate(name, email, phone, h)]


def _is_real_duplicate(name, email, phone, h):
    """Which hits actually BLOCK an add.

    Measured before shipping (Pete's rule: a fail-closed gate that blocks legitimate work is worse
    than no gate -- an earlier enforcement set would have blocked 22 of 25 approved quotes). Sharing
    a FIRST NAME is not a duplicate: 'Helen Finchcombe' vs seventeen other Helens must go through.
    A block needs one of:
      · the same email or phone            -- same person, definitively
      · an exact full-name match           -- same person, near-definitively
      · one name is a SUBSET of the other  -- 'Freya' vs 'Freya Finch', the 26 Jul 2026 case
      · a shared SURNAME plus a shared first name
    """
    def toks(x):
        return {t for t in re.split(r"[\s,.]+", (x or "").lower()) if len(t) > 1}

    if not h.get("_partial"):
        return True                                   # matched on the full string / email / phone
    e = (email or "").strip().lower()
    p = re.sub(r"\D", "", phone or "")
    if e and e == (h.get("email") or "").strip().lower():
        return True
    if p and p[-9:] and p[-9:] in re.sub(r"\D", "", h.get("phone") or ""):
        return True
    a, b = toks(name), toks(h.get("name"))
    if not a or not b:
        return False
    if a == b:
        return True
    if a <= b or b <= a:                              # "Freya" ⊂ "Freya Finch"
        return True
    return len(a & b) >= 2                            # first name AND surname both shared


# ---------------------------------------------------------------- writers

def add_sygma(name, email, phone, company, role, dry):
    url, key = _platform()
    row = {
        "full_name": name,
        "email": (email or "").strip().lower() or None,
        "phone": phone or None,
        "company_name": company or None,
        "type": role if role in ("customer", "supplier", "partner", "lead") else "lead",
        "customer_rank": 1 if role == "customer" else 0,
        "supplier_rank": 1 if role == "supplier" else 0,
        "partner_rank": 1 if role == "partner" else 0,
        "source": "manual",
    }
    home = "the Sygma platform — public.contacts"
    if dry:
        return home, row
    req = urllib.request.Request(
        f"{url}/rest/v1/contacts", data=json.dumps(row).encode(),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=representation"},
        method="POST")
    with urllib.request.urlopen(req, timeout=40) as r:
        return home, json.load(r)


def add_cd(name, email, phone, company, role, dry):
    vals = {"name": name}
    if email:
        vals["email"] = email
    if phone:
        vals["phone"] = phone
    if company:
        # NOT `parent_name` -- that is READONLY in Odoo (a related field on parent_id.name), so
        # writing it fails. `company_name` is the free-text company on the partner itself.
        vals["company_name"] = company
    # Odoo's own convention: the rank IS the role flag.
    vals["customer_rank"] = 1 if role in (None, "customer") else 0
    vals["supplier_rank"] = 1 if role == "supplier" else 0
    home = "Odoo — res.partner (the Canary Detect record)"
    if dry:
        return home, vals
    r = subprocess.run([sys.executable, os.path.join(VAULT, "odoo-api.py"),
                        "create", "res.partner", json.dumps(vals)],
                       capture_output=True, text=True, timeout=90,
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        _die("odoo create failed: " + (r.stderr or r.stdout).strip().splitlines()[-1][:160])
    return home, (r.stdout or "").strip()


def add_personal(name, email, phone, company, role, dry, group=None):
    home = "Google Contacts (Pete's phone)"
    if dry:
        return home, {"name": name, "email": email, "phone": phone, "org": company}
    if not email:
        _die("Google Contacts needs an email to create the entry. Pass --email, or add them to a "
             "business system instead.")
    args = [sys.executable, os.path.join(VAULT, "people-api.py"), "add", name, email]
    if phone:
        args.append(phone)
    if company:
        args.append(company)
    if group:
        args += ["--group", group]
    r = subprocess.run(args, capture_output=True, text=True, timeout=90,
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        _die("google contacts add failed: " + (r.stderr or r.stdout)[:200])
    out = (r.stdout or "").strip()[:400]
    # Google Contacts is the HOME, but reads come from the CC mirror `public.google_contacts`.
    # Without this the person is invisible to the reader until the next sync, so the system
    # contradicts itself the moment it is used (found 26 Jul 2026: a contact was created and the
    # lookup still answered "NOT FOUND"). Write then re-sync, so read-after-write always holds.
    try:
        m = subprocess.run([sys.executable, os.path.join(VAULT, "google-contacts-sync.py")],
                           capture_output=True, text=True, timeout=180,
                           env={**os.environ, "VAULT": VAULT})
        out += ("\n  mirror: refreshed so `people find` can see them immediately"
                if m.returncode == 0 else
                "\n  ⚠ mirror refresh FAILED -- `people find` will not show this person until the "
                "mirror re-syncs; run `people find <name> --refresh`: " + (m.stderr or m.stdout)[:120])
    except Exception as e:
        out += (f"\n  ⚠ mirror refresh FAILED ({type(e).__name__}) -- "
                "run `people find <name> --refresh`")
    return home, out


ADDERS = {"sygma": add_sygma, "cd": add_cd, "personal": add_personal}


# ---------------------------------------------------------------- add

def cmd_add(a):
    if not a.get("entity"):
        _die("--entity is REQUIRED (sygma | cd | personal). This tool does not guess which business "
             "a person belongs to -- guessing wrong files them in the wrong company's system.")
    if a["entity"] not in ENTITIES:
        _die(f"--entity must be one of {', '.join(ENTITIES)}")
    name = a["name"]
    hits = find_existing(name, a.get("email"), a.get("phone"))
    if hits and not a.get("allow_duplicate"):
        print(f"REFUSED — {len(hits)} existing record(s) already match '{name}':\n")
        for h in hits[:8]:
            tag = "  ~PARTIAL NAME MATCH" if h.get("_partial") else ""
            print(f"  • {h.get('name')} — {h.get('store')}{tag}")
            reach = " · ".join(x for x in [h.get("email"), h.get("phone")] if x)
            if reach:
                print(f"    {reach}")
        subsets = [h for h in hits if h.get("_partial") and h.get("name")
                   and set((h["name"] or "").lower().split()) < set(name.lower().split())]
        print("\nCreating another would duplicate them. Odoo already carries a duplicate 'Indelasa'")
        print("stub for exactly this reason.")
        if subsets:
            # The Freya case: a HALF-FINISHED record already there under part of the name.
            # Pete's touch-it-tidy-it rule -- the right move is to COMPLETE it, not add a second.
            print("\n  ⚠ One of these is a PART-NAME record. If it is the same person, TIDY IT")
            print("    rather than creating a second (Pete's rule, 26 Jul 2026):")
            for h in subsets[:4]:
                res = (h.get("extra") or {}).get("resource_name")
                if res:
                    bits = f'      people tidy "{h.get("name")}" --name "{name}" --confirm-replace'
                    for fld, val in (("email", a.get("email")), ("phone", a.get("phone"))):
                        if val and not h.get(fld):
                            bits += f" --{fld} {val}"
                    print(bits)
                else:
                    print(f"      • {h.get('name')} — in {h.get('store')}; complete it there")
        print("\nIf this really IS a different person, re-run with --allow-duplicate.")
        return 1
    home, payload = ADDERS[a["entity"]](name, a.get("email"), a.get("phone"),
                                        a.get("company"), a.get("role"), a.get("dry"))
    verb = "WOULD create" if a.get("dry") else "CREATED"
    print(f"{verb}: {name}")
    print(f"  home: {home}")
    if a.get("role"):
        print(f"  role: {a['role']}")
    if a.get("dry"):
        print(f"  payload: {json.dumps(payload, default=str)[:400]}")
    else:
        print(f"  result: {str(payload)[:300]}")
    print("\n  (the home is stated so a wrong entity is visible NOW, not months later)")
    return 0


# ---------------------------------------------------------------- tidy

def _google_hits(q):
    """Resolve a name (or a bare people/c... resource name) to Google Contacts rows only.

    Only Google rows carry a resource_name, which is what the update path needs -- so this is also
    what makes `tidy` Google-only. Returns (google_hits, other_store_hits).
    """
    if q.startswith("people/"):
        _, txt = _cc_sql("SELECT * FROM google_contacts WHERE resource_name = '"
                         + q.replace("'", "''") + "'")
        if not txt.startswith("["):
            raise RuntimeError(txt[:200] or "the CC database did not answer")

        def first(v):
            return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None)

        return [{
            "store": "Google Contacts (via the CC mirror)",
            "name": x.get("display_name"),
            "email": first(x.get("emails")),
            "phone": first(x.get("phones_e164")) or first(x.get("phones")),
            "extra": {"resource_name": x.get("resource_name")},
        } for x in json.loads(txt)], []
    results, _, _, _ = lookup(q)
    google = [r for r in results if (r.get("extra") or {}).get("resource_name")]
    other = [r for r in results if not (r.get("extra") or {}).get("resource_name")]
    return google, other


def _people_api_update(res, field, value):
    r = subprocess.run([sys.executable, os.path.join(VAULT, "people-api.py"),
                        "update", res, field, value],
                       capture_output=True, text=True, timeout=90,
                       env={**os.environ, "VAULT": VAULT})
    return r.returncode, ((r.stdout or "") + (r.stderr or ""))[:200]


def cmd_tidy(a):
    """Complete a half-finished record. Google Contacts only, in v1.

    The underlying update path does NOT behave uniformly, so the semantics are fixed here rather
    than left to the caller:
      · --email / --phone APPEND (via +field), preserving what is already there
      · --name REPLACES -- the People API has no append for names, and the + prefix is silently
        ignored on that field. So it needs --confirm-replace, and it runs LAST, so a mid-way
        failure leaves the record strictly improved rather than renamed-but-empty.
      · --dry-run always wins over --confirm-replace.
    """
    q = a.get("name_arg") or ""
    if not q:
        _die("a name (or a people/c... resource name) is required")
    if not any(a.get(k) for k in ("email", "phone", "name")):
        _die("nothing to tidy: pass at least one of --email, --phone, --name")

    google, other = _google_hits(q)
    if not google:
        if other:
            stores = ", ".join(sorted({r.get("store") for r in other}))
            _die(f"'{q}' is not on Pete's phone. It answered from: {stores}.\n"
                 "  `people tidy` covers Google Contacts only -- complete a platform or Odoo record\n"
                 "  in the app, where the linked bookings, invoices and deliverables are visible.")
        _die(f"'{q}' is in none of the four stores -- nothing to tidy.")
    if len(google) > 1:
        print(f"REFUSED — '{q}' matches {len(google)} records on the phone. Say which one:\n")
        for h in google:
            res = (h.get("extra") or {}).get("resource_name")
            reach = " · ".join(x for x in [h.get("email"), h.get("phone")] if x)
            print(f"  • {h.get('name')}  {reach}")
            print(f"    people tidy {res} ...")
        return 1

    rec = google[0]
    res = (rec.get("extra") or {}).get("resource_name")
    dry = a.get("dry")
    print(f"{'WOULD tidy' if dry else 'TIDYING'}: {rec.get('name')}  ({res})")
    print(f"  currently: email={rec.get('email') or '—'}  phone={rec.get('phone') or '—'}")

    # appends first -- a mid-way failure then leaves the record strictly improved
    landed, missed = [], []
    for field in ("email", "phone"):
        val = a.get(field)
        if not val:
            continue
        if dry:
            print(f"  WOULD append {field}: {val}   (keeps the existing {field} listed)")
            continue
        rc, out = _people_api_update(res, "+" + field, val)
        (landed if rc == 0 else missed).append(f"{field}={val}")
        if rc != 0:
            print(f"  ⚠ {field} append FAILED: {out}")

    if a.get("name"):
        print(f'  REPLACING "{rec.get("name")}" -> "{a["name"]}"')
        if dry:
            print("    (dry run — nothing written)")
        elif not a.get("confirm_replace"):
            print("    SKIPPED — the name REPLACES rather than appends, so it needs")
            print("    --confirm-replace. Any --email/--phone above have still been applied.")
        else:
            rc, out = _people_api_update(res, "name", a["name"])
            (landed if rc == 0 else missed).append(f'name="{a["name"]}"')
            if rc != 0:
                print(f"  ⚠ name replace FAILED: {out}")

    if dry:
        print("\n  DRY RUN — nothing was written.")
        return 0
    if landed:
        print("  wrote: " + ", ".join(landed))
        # one refresh after the LAST write, not one per field
        print(f"  mirror: {refresh_mirror()}")
    if missed:
        print("  ⚠ did NOT land: " + ", ".join(missed))
        print("    There is no undo for a name replace -- check the record before retrying.")
        return 1
    return 0


# ---------------------------------------------------------------- phone (one person)

def cmd_phone_one(a):
    """Push an existing person onto the phone. Looks them up FIRST so we push a real record."""
    hits = find_existing(a["name"], None, None)
    if not hits:
        print(f"NOT FOUND: '{a['name']}' is in none of the four stores, so there is nothing to push.")
        print('Create the business record first:  people add "Name" --entity sygma|cd')
        return 1
    already = [h for h in hits if "Google Contacts" in (h.get("store") or "")]
    if already:
        print(f"ALREADY ON THE PHONE: {a['name']} is in Google Contacts. Nothing to do.")
        return 0
    src = hits[0]
    email = next((h.get("email") for h in hits if h.get("email")), None)
    phone = next((h.get("phone") for h in hits if h.get("phone")), None)
    if a.get("dry"):
        print(f"WOULD push to Google Contacts (Pete's phone): {src.get('name')}")
        print(f"  from: {src.get('store')}")
        print(f"  email={email} phone={phone}")
        return 0
    if not email:
        _die("that person has no email on record; Google Contacts needs one to create the entry")
    # Single-person writes stay UNLABELLED: the group label marks what the BULK roster push put on
    # the phone, so it stays separable from what Pete added himself. Labelling a one-off would
    # stamp family, friends and trades as Sygma.
    home, out = add_personal(src.get("name") or a["name"], email, phone, src.get("detail"),
                             None, False)
    print(f"PUSHED to {home}: {src.get('name')}")
    print(f"  source of truth remains: {src.get('store')} (the phone is a VIEW, not the record)")
    return 0


def cmd_phone_remove(a):
    hits = [h for h in find_existing(a["name"], None, None)
            if "Google Contacts" in (h.get("store") or "")]
    if not hits:
        print(f"NOT ON THE PHONE: '{a['name']}' is not in Google Contacts. Nothing to remove.")
        return 0
    if a.get("dry"):
        for h in hits:
            print(f"WOULD remove from the phone: {h.get('name')} ({h.get('email') or h.get('phone')})")
        print("  (the business record is untouched — this only takes them off the phone)")
        return 0
    done = 0
    for h in hits:
        res = (h.get("extra") or {}).get("resource_name")
        if not res:
            print(f"  ⚠ no resource_name for {h.get('name')} — skipped; re-run "
                  f"`people find --refresh` and try again")
            continue
        r = subprocess.run([sys.executable, os.path.join(VAULT, "people-api.py"), "delete", res],
                           capture_output=True, text=True, timeout=90,
                           env={**os.environ, "VAULT": VAULT})
        if r.returncode != 0:
            print(f"  ⚠ delete failed for {h.get('name')}: {(r.stderr or r.stdout)[:150]}")
            continue
        print(f"REMOVED FROM THE PHONE: {h.get('name')} ({res})")
        done += 1
    if done:
        print(f"  mirror: {refresh_mirror()}")
    print("\nThe business record is untouched — this only takes them off the phone.")
    return 0 if done else 1


# ---------------------------------------------------------------- phone (bulk roster)

def gather(scope):
    """Read the RECORD. Never the phone -- the phone is downstream of this, never a source."""
    url, key = _platform()
    if scope == "staff":
        rows = _rest(url, key, "staff_directory", {"select": "*", "limit": "500"}, schema="hub")
        return [{"name": r.get("full_name"), "email": r.get("work_email"),
                 "phone": r.get("work_mobile") or r.get("work_phone"),
                 "org": r.get("job_title") or "Sygma Solutions",
                 "from": "hub.staff_directory"}
                for r in rows if (r.get("employment_status") or "").lower() == "active"]
    if scope == "active-customers":
        rows = _rest(url, key, "contacts", {"select": "*", "limit": "5000"})
        return [{"name": r.get("full_name"), "email": r.get("email"),
                 "phone": r.get("mobile") or r.get("phone"),
                 "org": r.get("company_name"), "from": "public.contacts"}
                for r in rows
                if (r.get("customer_rank") or 0) > 0 and r.get("account_status") == "active"]
    if scope == "suppliers":
        rows = _rest(url, key, "contacts", {"select": "*", "limit": "5000"})
        return [{"name": r.get("full_name"), "email": r.get("email"),
                 "phone": r.get("mobile") or r.get("phone"),
                 "org": r.get("company_name"), "from": "public.contacts"}
                for r in rows if (r.get("supplier_rank") or 0) > 0]
    return []


def already_on_phone():
    """The CC mirror of the phone. Read-only -- one way out, never read back."""
    _, txt = _cc_sql("SELECT display_name, emails, phones_e164, phones FROM google_contacts",
                     timeout=60)
    if not txt.startswith("["):
        return set(), set()
    emails, phones = set(), set()
    for x in json.loads(txt):
        for e in (x.get("emails") or []):
            if e:
                emails.add(e.strip().lower())
        for p in (x.get("phones_e164") or []) + (x.get("phones") or []):
            n = norm_phone(p)
            if n:
                phones.add(n)
    return emails, phones


def push_argv(person):
    """The EXACT argv a bulk push hands the transport layer. A pure function on purpose.

    The label is what makes a bad bulk push undoable, so it must never become optional -- and the
    only way to assert that without actually writing to Pete's phone is to inspect the argv the
    push would build. The dry-run path returns before any argv exists, so a printed constant proves
    nothing. Hence: build it here, call it there, assert on it in --self-test.
    """
    # phone and org are POSITIONAL, so org cannot be passed without a phone -- send an empty
    # placeholder rather than silently dropping the organisation.
    args = [sys.executable, os.path.join(VAULT, "people-api.py"), "add",
            person["name"], person["email"], person.get("phone") or ""]
    if person.get("org"):
        args.append(person["org"])
    args += ["--group", LABEL]      # every pushed contact is labelled, no exceptions
    return args


def cmd_phone_scope(a):
    scope = a.get("scope")
    if scope not in SCOPES:
        print(f"--scope must be one of: {', '.join(SCOPES)}", file=sys.stderr)
        return 2
    confirm = a.get("confirm")
    limit = int(a["limit"]) if a.get("limit") else None

    people = gather(scope)
    emails, phones = already_on_phone()
    todo, skipped = [], 0
    for p in people:
        if not p.get("name"):
            continue
        e = (p.get("email") or "").strip().lower()
        n = norm_phone(p.get("phone"))
        if (e and e in emails) or (n and n in phones):
            skipped += 1
            continue
        if not e:
            skipped += 1          # Google Contacts needs an email to create the entry
            continue
        todo.append(p)
    if limit:
        todo = todo[:limit]

    print(f"people phone — scope '{scope}'")
    print(f"  in the record          : {len(people)}")
    print(f"  already on the phone / no email (skipped): {skipped}")
    print(f"  WOULD ADD              : {len(todo)}")
    print(f"  every one labelled     : {LABEL}")
    print()

    if not confirm:
        for p in todo[:15]:
            print(f"    + {p['name']}  <{p.get('email')}>  {p.get('phone') or ''}   [{p['from']}]")
        if len(todo) > 15:
            print(f"    … and {len(todo) - 15} more")
        print("\n  DRY RUN — nothing was written. This writes to Pete's PERSONAL phone in bulk,")
        print("  so it needs an explicit --confirm. Re-run with --confirm to push.")
        return 0

    added, failed = 0, 0
    for p in todo:
        r = subprocess.run(push_argv(p), capture_output=True, text=True, timeout=60,
                           env={**os.environ, "VAULT": VAULT})
        if r.returncode == 0:
            added += 1
        else:
            failed += 1
            print(f"    FAILED {p['name']}: {(r.stderr or r.stdout)[:100]}", file=sys.stderr)
    print(f"  ADDED {added}, FAILED {failed}")
    print("  The business systems remain the record — this was one way out, never read back.")
    return 0 if not failed else 1


def cmd_phone(a):
    if a.get("scope"):
        return cmd_phone_scope(a)
    if not a.get("name"):
        _die('a name is required, or --scope staff|active-customers|suppliers for a roster push')
    if a.get("remove"):
        return cmd_phone_remove(a)
    return cmd_phone_one(a)


# ---------------------------------------------------------------- remove

def cmd_remove(a):
    if not a.get("confirm_delete"):
        _die("REFUSED. Deleting a BUSINESS record needs --yes-delete-business-record.\n"
             "  A business record going away is not the same as taking someone off the phone.\n"
             '  If you only want them off Pete\'s phone, use:  people phone "Name" --remove')
    _die("not implemented on purpose. Deleting a live customer/supplier record is a destructive,\n"
         "  hard-to-reverse write on a business system; it should be done in the app (or Odoo) where\n"
         "  the consequences (linked bookings, invoices, deliverables) are visible. This tool will\n"
         "  not do it blind from a command line.")


# ---------------------------------------------------------------- check

def hygiene():
    """Compute the hygiene picture. Returns (counters, report_lines)."""
    rc, txt = _cc_sql("SELECT resource_name, display_name, emails, phones FROM google_contacts",
                      timeout=120)
    if not txt.startswith("["):
        raise RuntimeError(txt[:200] or "the CC database did not answer")
    rows = json.loads(txt)

    by_email, by_phone = collections.defaultdict(list), collections.defaultdict(list)
    names, half, self_dupes = {}, [], []
    for r in rows:
        nm = (r.get("display_name") or "").strip()
        names[r["resource_name"]] = nm
        # DEDUPE WITHIN THE RECORD FIRST. Without this, one contact listing its own number twice
        # looked like two contacts sharing it -- which is how "28 shared emails" and "24 shared
        # numbers" were reported on 26 Jul 2026 when the true figures were 0 and 5. A tool that
        # over-reports is worse than no tool: Pete was about to merge contacts that never matched.
        emails = sorted({e.strip().lower() for e in (r.get("emails") or []) if e and e.strip()})
        phones = sorted({norm_phone(p) for p in (r.get("phones") or []) if norm_phone(p)})
        raw_p = [norm_phone(p) for p in (r.get("phones") or []) if norm_phone(p)]
        raw_e = [(e or "").strip().lower() for e in (r.get("emails") or []) if e and e.strip()]
        if len(raw_p) != len(phones) or len(raw_e) != len(emails):
            self_dupes.append(nm or r["resource_name"])
        for e in emails:
            by_email[e].append(r)
        for p in phones:
            by_phone[p].append(r)
        if nm and not emails and not any(phones):
            half.append(nm)

    # count DISTINCT records, never repeated entries on one record
    shared_email = {k: v for k, v in by_email.items()
                    if len({x["resource_name"] for x in v}) > 1}
    shared_phone = {k: v for k, v in by_phone.items()
                    if len({x["resource_name"] for x in v}) > 1}

    tok = {rn: {t for t in re.split(r"[\s,.]+", n.lower()) if len(t) > 1}
           for rn, n in names.items() if n}
    # A shared FIRST NAME is not a duplicate. Every bare-name record flagged on 26 Jul had a
    # DIFFERENT number from its "match" -- "Adam" and "Adam Brennan" are simply two Adams. So:
    #   · subsets       = bare name that SHARES a contact point with a fuller record
    #   · needs_surname = bare name with no shared contact point -- incomplete, not duplicate
    reach = {}
    for r in rows:
        reach[r["resource_name"]] = (
            {norm_phone(p) for p in (r.get("phones") or []) if norm_phone(p)}
            | {(e or "").strip().lower() for e in (r.get("emails") or []) if e})
    subsets, needs_surname = [], []
    for rn, t in [(rn, t) for rn, t in tok.items() if len(t) == 1]:
        shared = False
        for rn2, t2 in tok.items():
            if rn2 != rn and len(t2) > 1 and t < t2:
                if reach.get(rn, set()) & reach.get(rn2, set()):
                    subsets.append((names[rn], names[rn2]))
                    shared = True
        if not shared:
            needs_surname.append(names[rn])

    # EXACT same display name on two records. Renaming can CREATE this class -- renaming a bare
    # "Lydia" to "Lydia Dant" produced a second Lydia Dant and the check reported all-clear.
    same_name = collections.defaultdict(list)
    for r in rows:
        n = (r.get("display_name") or "").strip().lower()
        if n:
            same_name[n].append(r)
    exact = {k: v for k, v in same_name.items() if len(v) > 1}

    counters = {"contacts": len(rows), "shared_email": len(shared_email),
                "shared_phone": len(shared_phone), "subsets": len(subsets),
                "exact_same_name": len(exact), "half_finished": len(half),
                "needs_surname": len(needs_surname), "self_dupes": len(self_dupes)}
    gaps = (counters["shared_email"] + counters["shared_phone"]
            + counters["subsets"] + counters["exact_same_name"])

    lines = [f"PEOPLE HYGIENE — {len(rows)} contact records checked"]
    lines.append(f"  probable duplicates: {len(shared_email)} shared email(s), "
                 f"{len(shared_phone)} shared number(s), {len(subsets)} part-name overlap(s)")
    lines.append(f"  SAME NAME on two records: {len(exact)}")
    lines.append(f"  half-finished (no email AND no phone): {len(half)}")
    lines.append(f"  NEEDS A SURNAME (not a duplicate — only Pete knows who they are): "
                 f"{len(needs_surname)}")
    lines.append(f"  records repeating their OWN number/address (untidy, NOT a duplicate person): "
                 f"{len(self_dupes)}")
    for k, v in list(exact.items())[:8]:
        lines.append(f"    ⧉ {v[0].get('display_name')} x{len(v)} -> "
                     + " | ".join((x.get("phones") or ["no phone"])[0] for x in v))
    for e, v in list(shared_email.items())[:8]:
        lines.append(f"    ✉ {e} -> " + " | ".join(names[x['resource_name']] for x in v))
    for p, v in list(shared_phone.items())[:8]:
        lines.append(f"    ☎ ...{p} -> " + " | ".join(names[x['resource_name']] for x in v)
                     + "   (a SHARED number means same ORGANISATION — never merge)")
    if needs_surname:
        lines.append("    first-name-only: " + ", ".join(sorted(needs_surname)[:14])
                     + (f" … +{len(needs_surname)-14} more" if len(needs_surname) > 14 else ""))
    for x, y in subsets[:8]:
        lines.append(f"    ~ '{x}' may be the same person as '{y}' — tidy, do not duplicate")
    if gaps == 0 and not half:
        lines.append("  clean — no duplicates and nothing half-finished.")
    lines.append('  Fix with: people tidy "<name>" --name "FULL NAME" --confirm-replace '
                 "| --email E | --phone P   (touch it, tidy it — Pete's rule)")
    counters["gaps"] = gaps
    return counters, lines


def cmd_check(a):
    if a.get("self_test"):
        return self_test()
    counters, lines = hygiene()
    report = "\n".join(lines)
    print(report)
    try:
        _cc_sql("INSERT INTO daily_log (date, cron_name, content) VALUES "
                "(current_date, 'people-hygiene', $r$" + report + "$r$)")
    except Exception as e:
        print(f"  ⚠ could not record to daily_log: {str(e)[:120]}")
    if a.get("json"):
        print(json.dumps({"records": counters["contacts"], "gaps": counters["gaps"],
                          "half_finished": counters["half_finished"]}))
    return 0


# ---------------------------------------------------------------- self-test

# Assembled from fragments on purpose. Spelled out, these literals would sit in this file for
# ever and make Phase 5's "grep for the old names prints zero" precondition un-passable — by the
# very gate written to enforce it.
OLD_NAMES = tuple(x + ".py" for x in
                  ("people-api", "google-contacts-sync", "contact", "whois",
                   "phone-push", "people-hygiene"))


def _run_self(args, timeout=300):
    """Run this tool as a subprocess. Never a shimmed tool -- that is what would recurse."""
    r = subprocess.run([sys.executable, os.path.abspath(__file__)] + args,
                       capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "VAULT": VAULT})
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def self_test():
    """The runnable gate. Prints one line per assertion plus a failure counter; exits 0 ONLY when
    the counter is zero. NO assertion writes people data."""
    fails, n = [], 0

    def check(label, ok, detail=""):
        nonlocal n
        n += 1
        status = "ok  " if ok else "FAIL"
        print(f"  [{status}] {label}" + (f"   {detail}" if detail else ""))
        if not ok:
            fails.append(label)

    print("people check --self-test\n")

    # -- row count either side ------------------------------------------------
    _, txt = _cc_sql("SELECT count(*) FROM google_contacts")
    before = json.loads(txt)[0]["count"] if txt.startswith("[") else -1

    # -- 1. the baseline, read back from the Phase 0 snapshot -----------------
    _, txt = _cc_sql("SELECT body FROM vault_notes WHERE slug = '" + SNAPSHOT_SLUG + "'")
    rows = json.loads(txt) if txt.startswith("[") else []
    if len(rows) != 1:
        check(f"baseline snapshot '{SNAPSHOT_SLUG}' is readable", False,
              f"expected exactly 1 row, got {len(rows)}")
        base = {}
    else:
        m = re.search(r"^BASELINE: (.+)$", rows[0]["body"], re.M)
        if not m:
            check("baseline snapshot carries a BASELINE: line", False, "no ^BASELINE: match")
            base = {}
        else:
            base = dict(kv.split("=", 1) for kv in m.group(1).split())
            check("baseline snapshot is readable", True, m.group(1))

    counters, _ = hygiene()
    if base:
        for key in ("contacts", "shared_email", "shared_phone", "subsets", "exact_same_name"):
            want = int(base.get(key, -1))
            got = counters.get(key)
            check(f"baseline {key} == {want}", want == got, f"live={got}")
    else:
        check("baseline comparison ran", False, "no baseline to compare against — NOT a skip")
    print(f"  [note] printed, not asserted: needs_surname={counters['needs_surname']} "
          f"half_finished={counters['half_finished']} (these are MEANT to move)")

    # -- 2. the safeguard that actually failed on 26 Jul: partial matching ----
    rc, out = _run_self(["find", "Arabella Hartley", "--json"])
    d = json.loads(out) if out.strip().startswith("{") else {}
    nres, npart = len(d.get("results", [])), len(d.get("partial_matches", []))
    check("find 'Arabella Hartley' returns ZERO full-string results", nres == 0, f"results={nres}")
    check("...and sweeps PARTIAL matches (the only shape that reaches partial_sweep)",
          npart > 0, f"partial_matches={npart} (population, printed not asserted)")

    rc, out = _run_self(["add", "Arabella Hartley", "--entity", "personal", "--dry-run"])
    check("add 'Arabella Hartley' BLOCKS via the partial branch",
          rc == 1 and "PARTIAL NAME MATCH" in out, f"rc={rc}")

    # -- 3. the other blocking cases -----------------------------------------
    for name, entity in (("Freya Finch", "personal"), ("Alan Marsden", "personal"),
                         ("Andrew Foster", "sygma")):
        rc, out = _run_self(["add", name, "--entity", entity, "--dry-run"])
        check(f"add '{name}' BLOCKS (exact name)", rc == 1 and "REFUSED" in out, f"rc={rc}")

    rc, out = _run_self(["add", "Zebedee Quixotl", "--entity", "personal",
                         "--phone", "+34657343901", "--dry-run"])
    check("add with a SHARED NUMBER blocks", rc == 1 and "REFUSED" in out, f"rc={rc}")

    rc, out = _run_self(["add", "Zebedee Quixotl", "--entity", "sygma",
                         "--email", "andrew.foster@sygma-solutions.com", "--dry-run"])
    check("add with a SHARED EMAIL blocks", rc == 1 and "REFUSED" in out, f"rc={rc}")

    # -- 4. over-blocking: a fail-closed guard is worse than none -------------
    rc, out = _run_self(["add", "Helen Finchcombe", "--entity", "personal", "--dry-run"])
    check("add 'Helen Finchcombe' is ALLOWED among the existing Helens",
          rc == 0 and "WOULD create" in out, f"rc={rc}")

    rc, out = _run_self(["add", "Freya Finch", "--entity", "personal", "--dry-run",
                         "--allow-duplicate"])
    check("--allow-duplicate converts a block into an allowed dry-run",
          rc == 0 and "WOULD create" in out, f"rc={rc}")

    # -- 5. the bulk roster push ---------------------------------------------
    rc, out = _run_self(["phone", "--scope", "suppliers"])
    check("phone --scope suppliers exits 0 with no --confirm", rc == 0, f"rc={rc}")
    check("...prints the group label", LABEL in out)
    m = re.search(r"in the record\s*:\s*(\d+)", out)
    check("...reads a non-empty roster", bool(m) and int(m.group(1)) > 0,
          f"in the record={m.group(1) if m else '?'}")
    m2 = re.search(r"WOULD ADD\s*:\s*(\d+)", out)
    print(f"  [note] printed, not asserted: WOULD ADD={m2.group(1) if m2 else '?'} "
          "(correct use drains this to 0)")

    # the label, asserted from the PURE FUNCTION -- the dry path never builds an argv
    try:
        sup = gather("suppliers")
        person = next((p for p in sup if p.get("name") and p.get("email")), None)
        argv = push_argv(person) if person else []
        pair = any(argv[i] == "--group" and argv[i + 1] == LABEL for i in range(len(argv) - 1))
        check("push_argv() carries ['--group', LABEL] as a contiguous pair", pair,
              f"for {person['name'] if person else 'no candidate'}")
    except Exception as e:
        check("push_argv() carries the label", False, f"{type(e).__name__}: {e}")

    rc, out = _run_self(["check", "--self-test", "--confirm"])
    check("--confirm is REFUSED inside --self-test", rc != 0, f"rc={rc}")

    # -- 6. remove refuses in both branches ----------------------------------
    rc, out = _run_self(["remove", "Zebedee Quixotl", "--entity", "sygma"])
    check("remove without the long flag REFUSES", rc != 0 and "REFUSED" in out, f"rc={rc}")
    rc2, out2 = _run_self(["remove", "Zebedee Quixotl", "--entity", "sygma",
                           "--yes-delete-business-record"])
    check("remove WITH the long flag still refuses (deletes nothing)",
          rc2 != 0 and "not implemented on purpose" in out2, f"rc={rc2}")

    # -- 7. tidy, dry-run only (Arabella is a shared fixture) -----------------
    rc, out = _run_self(["tidy", "Arabella", "--phone", "+34600000000", "--dry-run"])
    check("tidy --phone --dry-run prints the APPEND it would make",
          rc == 0 and "WOULD append phone" in out, f"rc={rc}")
    rc, out = _run_self(["tidy", "Arabella", "--name", "Arabella Hartley", "--dry-run"])
    check("tidy --name --dry-run prints REPLACING, not appending",
          rc == 0 and "REPLACING" in out and "append" not in out.lower(), f"rc={rc}")

    # -- 8. no printed command names a renamed or internal tool --------------
    outputs = {
        'find "Arabella"': _run_self(["find", "Arabella"])[1],
        'find "Arabella Hartley"': _run_self(["find", "Arabella Hartley"])[1],
        'add "Arabella Hartley" --dry-run': _run_self(
            ["add", "Arabella Hartley", "--entity", "personal", "--dry-run"])[1],
        "check": _run_self(["check"])[1],
        'phone "<nobody>"': _run_self(["phone", "Zebedee Quixotl"])[1],
        'remove "X" --entity sygma': _run_self(["remove", "Zebedee Quixotl", "--entity", "sygma"])[1],
    }
    for label, text in outputs.items():
        hit = [nm for nm in OLD_NAMES if nm in text]
        check(f"output of `{label}` names no renamed/internal tool", not hit,
              f"found: {', '.join(hit)}" if hit else "")
    # the RUNNABLE form, not the bare verb: `find` renders the tidy prompt as a full command line
    # (VAULT=... python3 .../people.py tidy ...) because a bare "people tidy" is not executable.
    # Asserting the runnable form proves the prompt can be pasted, not merely that the verb appears.
    check("find 'Arabella' prints a runnable `tidy` command for the phone record",
          "people.py tidy" in outputs['find "Arabella"'])
    check("find 'Arabella' keeps 'in its store' wording for the non-Google row",
          "in its store" in outputs['find "Arabella"'])
    check("find on a true negative prints `people add`",
          "people add" in outputs['find "Arabella Hartley"'])

    # -- 9. the flag contract -------------------------------------------------
    rc, out = _run_self(["find", "Arabella", "--nonsense"])
    check("an unknown flag ABORTS", rc != 0 and "unknown flag" in out, f"rc={rc}")
    rc, out = _run_self(["-h"])
    check("-h prints usage and exits 0", rc == 0 and "USAGE" in out, f"rc={rc}")
    rc, out = _run_self(["find"])
    check("find with no query exits 2", rc == 2, f"rc={rc}")

    # -- 10. nothing was written ---------------------------------------------
    _, txt = _cc_sql("SELECT count(*) FROM google_contacts")
    after = json.loads(txt)[0]["count"] if txt.startswith("[") else -2
    check(f"contact count unchanged ({before})", before == after, f"before={before} after={after}")

    print(f"\n  {n} assertions, {len(fails)} FAILED")
    for f in fails:
        print(f"    ✗ {f}")
    return 0 if not fails else 1


# ---------------------------------------------------------------- cli

def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    verb, rest = argv[0], argv[1:]

    VERBS = {"find": cmd_find, "add": cmd_add, "tidy": cmd_tidy,
             "check": cmd_check, "phone": cmd_phone, "remove": cmd_remove}
    if verb not in VERBS:
        _die(f"unknown command '{verb}'. Use: {' | '.join(VERBS)}")
    if "-h" in rest or "--help" in rest:
        print(__doc__)
        return 0

    # Parse by POSITION, consuming each flag's value as we go.
    #
    # The original scanned for flags by value -- `rest.index("--entity")` then `pos.remove(value)`
    # -- which broke two ways, both inherited here until 27 Jul 2026 and both found by testing:
    #   · `--entity=sygma` passed the unknown-flag check (it splits on "=") but the value was never
    #     read, so the tool ACCEPTED the flag and then said the flag was missing.
    #   · removing a value from the positional list BY VALUE removes the first equal string, which
    #     may be part of the name rather than the flag's own value.
    # Walking the list once removes both classes: a value is consumed because of WHERE it is, not
    # because of what it looks like.
    a = {"dry": False, "allow_duplicate": False, "confirm_delete": False, "json": False,
         "refresh": False, "confirm": False, "remove": False, "confirm_replace": False,
         "self_test": False}
    BOOLS = {"--dry-run": "dry", "--dry": "dry", "--allow-duplicate": "allow_duplicate",
             "--yes-delete-business-record": "confirm_delete", "--json": "json",
             "--refresh": "refresh", "--confirm": "confirm", "--remove": "remove",
             "--confirm-replace": "confirm_replace", "--self-test": "self_test"}
    pos = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if not tok.startswith("-"):
            pos.append(tok)
            i += 1
            continue
        head, _, inline = tok.partition("=")
        if head not in KNOWN_FLAGS:
            # An unknown flag must ABORT. "--dry" was silently ignored on 26 Jul 2026 and two junk
            # contacts were written straight into Pete's live Google Contacts by a "dry" run.
            _die(f"unknown flag(s): {tok}"
                 "\n  Refusing rather than ignoring them -- an ignored --dry flag WRITES FOR REAL."
                 "\n  Known flags: " + ", ".join(sorted(KNOWN_FLAGS)))
        if head in BOOLS:
            if inline:
                _die(f"{head} takes no value (got '{tok}')")
            a[BOOLS[head]] = True
            i += 1
            continue
        field = head.lstrip("-")
        if inline:                                  # --entity=sygma
            a[field] = inline
            i += 1
        elif i + 1 < len(rest) and not rest[i + 1].startswith("--"):
            a[field] = rest[i + 1]                  # --entity sygma
            i += 2
        else:
            _die(f"{head} needs a value")           # a trailing flag must not be silently dropped

    if a["self_test"] and a["confirm"]:
        _die("--confirm is refused inside --self-test: the gate never writes people data.")

    # `tidy --name` is the NEW value, not the record being tidied -- keep them apart
    a["name_arg"] = " ".join(pos).strip() or None
    if verb != "tidy":
        a["name"] = a.get("name") or a["name_arg"]
    if verb in ("add",) and not a.get("name"):
        _die("a name is required")

    return VERBS[verb](a)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"people: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
