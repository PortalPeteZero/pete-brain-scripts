#!/usr/bin/env python3
"""
phone-push.py -- copy business people ONTO Pete's phone, one way, clearly labelled. (Plan step B9.)

WHAT IT IS FOR
  Staff and active customers should be reachable from the phone without Pete typing them in. The
  business systems stay the record; the phone is a VIEW of them.

THE THREE RULES THAT MAKE THIS SAFE
  1. ONE WAY OUT, NEVER READ BACK. Nothing here ever writes to the platform, Odoo, or the CC
     mirror. If the phone and the record disagree, the RECORD wins -- always.
  2. EVERYTHING IT ADDS IS LABELLED. Every contact it creates gets the group label below, so what
     the system put on the phone stays separable from what Pete added himself, forever. Without
     that label there is no way to undo a bad push, which is why it is not optional.
  3. IT WILL NOT RUN WITHOUT --CONFIRM. This writes to Pete's PERSONAL phone in bulk. A dry run is
     the default and prints exactly what would be created; the real push needs an explicit flag and
     an explicit scope.

USAGE
  phone-push.py --scope staff                    # dry run (the default) -- 18 people
  phone-push.py --scope active-customers         # dry run
  phone-push.py --scope staff --confirm          # actually push
  phone-push.py --scope staff --limit 5 --confirm

  --scope staff             Sygma staff, hub.staff_directory (employment_status = Active)
  --scope active-customers  Sygma contacts with customer_rank>0 AND account_status='active'
  --scope suppliers         Sygma contacts with supplier_rank>0

WHY NO CRON (Pete, 23 Jul 2026)
  A full refresh measures ~3.4s, so there is nothing to schedule around. Run it when the roster
  actually changes. A cron here would also mean unattended bulk writes to a personal phone, which
  is precisely the thing rule 3 exists to prevent.
"""
import json, os, re, sys, subprocess, urllib.request, urllib.parse

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.join(VAULT, "Library", "processes", "secrets")
LABEL = "Sygma (added by the Command Centre)"     # rule 2 — never make this optional
SCOPES = ("staff", "active-customers", "suppliers")


def _platform():
    k = json.load(open(os.path.join(SEC, "sygma-portal-supabase-keys.json")))
    return k["url"].rstrip("/"), k["service_role"]


def _rest(path, params, schema=None):
    url, key = _platform()
    u = f"{url}/rest/v1/{path}?" + urllib.parse.urlencode(params)
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if schema:
        h["Accept-Profile"] = schema
    with urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=40) as r:
        return json.load(r)


def _norm(s):
    d = re.sub(r"\D", "", s or "")
    return d[-9:] if len(d) >= 9 else ""


def gather(scope):
    """Read the RECORD. Never the phone -- the phone is downstream of this, never a source."""
    if scope == "staff":
        rows = _rest("staff_directory", {"select": "*", "limit": "500"}, schema="hub")
        return [{"name": r.get("full_name"),
                 "email": r.get("work_email"),
                 "phone": r.get("work_mobile") or r.get("work_phone"),
                 "org": r.get("job_title") or "Sygma Solutions",
                 "from": "hub.staff_directory"}
                for r in rows if (r.get("employment_status") or "").lower() == "active"]
    if scope == "active-customers":
        rows = _rest("contacts", {"select": "*", "limit": "5000"})
        return [{"name": r.get("full_name"), "email": r.get("email"),
                 "phone": r.get("mobile") or r.get("phone"),
                 "org": r.get("company_name"), "from": "public.contacts"}
                for r in rows
                if (r.get("customer_rank") or 0) > 0 and r.get("account_status") == "active"]
    if scope == "suppliers":
        rows = _rest("contacts", {"select": "*", "limit": "5000"})
        return [{"name": r.get("full_name"), "email": r.get("email"),
                 "phone": r.get("mobile") or r.get("phone"),
                 "org": r.get("company_name"), "from": "public.contacts"}
                for r in rows if (r.get("supplier_rank") or 0) > 0]
    return []


def already_on_phone():
    """The CC mirror of the phone. Read-only -- see rule 1."""
    r = subprocess.run([sys.executable, os.path.join(VAULT, "cc-sql.py")],
                       input="SELECT display_name, emails, phones_e164, phones FROM google_contacts",
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "VAULT": VAULT})
    txt = (r.stdout or "").strip()
    if not txt.startswith("["):
        return set(), set()
    emails, phones = set(), set()
    for x in json.loads(txt):
        for e in (x.get("emails") or []):
            if e:
                emails.add(e.strip().lower())
        for p in (x.get("phones_e164") or []) + (x.get("phones") or []):
            n = _norm(p)
            if n:
                phones.add(n)
    return emails, phones


def main():
    a = sys.argv[1:]
    if not a or "-h" in a or "--help" in a:
        print(__doc__)
        return 0
    scope = None
    if "--scope" in a and a.index("--scope") + 1 < len(a):
        scope = a[a.index("--scope") + 1]
    if scope not in SCOPES:
        print(f"--scope is required and must be one of: {', '.join(SCOPES)}", file=sys.stderr)
        return 2
    confirm = "--confirm" in a
    limit = None
    if "--limit" in a and a.index("--limit") + 1 < len(a):
        limit = int(a[a.index("--limit") + 1])

    people = gather(scope)
    emails, phones = already_on_phone()

    todo, skipped = [], 0
    for p in people:
        if not p.get("name"):
            continue
        e = (p.get("email") or "").strip().lower()
        n = _norm(p.get("phone"))
        if (e and e in emails) or (n and n in phones):
            skipped += 1
            continue
        if not e:
            skipped += 1          # Google Contacts needs an email to create the entry
            continue
        todo.append(p)
    if limit:
        todo = todo[:limit]

    print(f"phone-push — scope '{scope}'")
    print(f"  in the record          : {len(people)}")
    print(f"  already on the phone / no email (skipped): {skipped}")
    print(f"  WOULD ADD              : {len(todo)}")
    print(f"  every one labelled     : {LABEL}")
    print()

    # RULE 2 IS NOT YET ENFORCEABLE, so --confirm is refused rather than quietly dropping it.
    # people-api.py's add sets names/email/phone/organization and NO contactGroup membership, so
    # nothing this pushed would carry the label. Without the label there is no way to tell what the
    # system added from what Pete added, and therefore no way to undo a bad push -- which is the
    # whole reason rule 2 exists. Shipping the push with the label silently missing would be a
    # safety property claimed in the docstring and absent in the code.
    if confirm:
        print("  REFUSED — the group label is NOT implemented yet.", file=sys.stderr)
        print(f"  Rule 2 says every pushed contact carries '{LABEL}' so that what the system added", file=sys.stderr)
        print("  stays separable from what Pete added. people-api.py's `add` writes no contactGroup", file=sys.stderr)
        print("  membership, so these would be indistinguishable from Pete's own contacts and a bad", file=sys.stderr)
        print("  push could not be undone. Implement contactGroups membership first.", file=sys.stderr)
        return 2

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
        args = [sys.executable, os.path.join(VAULT, "people-api.py"), "add",
                p["name"], p["email"]]
        if p.get("phone"):
            args.append(p["phone"])
        if p.get("org"):
            args.append(p["org"])
        r = subprocess.run(args, capture_output=True, text=True, timeout=60,
                           env={**os.environ, "VAULT": VAULT})
        if r.returncode == 0:
            added += 1
        else:
            failed += 1
            print(f"    FAILED {p['name']}: {(r.stderr or r.stdout)[:100]}", file=sys.stderr)
    print(f"  ADDED {added}, FAILED {failed}")
    print("  The business systems remain the record — this was one way out, never read back.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
