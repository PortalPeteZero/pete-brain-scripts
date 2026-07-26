#!/usr/bin/env python3
"""
contact.py -- ADD / PHONE / REMOVE a person, routed to the right home. The write half of whois.py.

WHY THIS EXISTS (the verb was doing two jobs)
  "Add to contacts" used to be ambiguous in two separate ways and both caused real mistakes:

  1. CREATE-A-RECORD vs PUT-THEM-ON-MY-PHONE are DIFFERENT JOBS. Conflating them is what made the
     original verb feel wrong (Pete, 23 Jul 2026). They are separate commands here.
  2. "Contacts" has no single home any more. A Sygma person belongs in the Sygma platform; a Canary
     Detect person belongs in Odoo; someone with no business record at all belongs in Google
     Contacts. One verb, three destinations.

  THE ROUTING IS THE HARD PART, NOT THE WORDS. This tool therefore REFUSES to guess the entity --
  the caller must say which business it is -- and it always states which home it wrote to, so a
  wrong call is visible immediately instead of being discovered months later.

THE COMMANDS
  contact.py add "Name" --entity sygma|cd|personal [--email E] [--phone P] [--company C]
                        [--role customer|supplier|partner|lead]
      Creates the BUSINESS record in that entity's own system.
        sygma    -> the Sygma platform, public.contacts (with the per-role rank set)
        cd       -> Odoo res.partner (customer_rank / supplier_rank per --role)
        personal -> Google Contacts (someone with no business record: family, trades, friends)

  contact.py phone "Name"          Push an EXISTING person onto Pete's phone (Google Contacts).
  contact.py remove-phone "Name"   Take them OFF the phone. Reversible, so no confirmation.
  contact.py remove-record "Name" --entity sygma|cd --yes-delete-business-record
      Delete a BUSINESS record. A business record going away always needs an explicit yes, so the
      long flag is mandatory and there is no short form.

  Add --dry-run to any command to see exactly what WOULD happen, writing nothing.

SAFETY RULES BUILT IN
  * Never guesses the entity. No --entity, no write.
  * Always prints the home it wrote to, by name.
  * Checks for an existing match FIRST (via the same normalisation whois uses) and refuses to
    create a duplicate unless --allow-duplicate is passed. Odoo already carries a duplicate
    "Indelasa" stub precisely because something created a record that already existed.
  * Deleting a business record needs --yes-delete-business-record. Removing from the phone does not,
    because the phone is a VIEW of the record and re-pushing costs nothing.
"""
import re
import json, os, re, sys, subprocess, urllib.request, urllib.parse, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.join(VAULT, "Library", "processes", "secrets")
ENTITIES = ("sygma", "cd", "personal")


def _die(msg, code=2):
    print(f"contact: {msg}", file=sys.stderr)
    sys.exit(code)


def _platform():
    k = json.load(open(os.path.join(SEC, "sygma-portal-supabase-keys.json")))
    return k["url"].rstrip("/"), k["service_role"]


def _norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-9:] if len(d) >= 9 else ""


def find_existing(name, email, phone):
    """Reuse whois.py rather than re-implementing four lookups -- one matcher, one set of rules.

    Checks the NAME **and** the email **and** the phone. It used to check only the first non-empty
    one (`email or phone or name`), so passing --email meant the NAME WAS NEVER SEARCHED. That is
    the actual root cause of the Freya Finch duplicate on 26 Jul 2026: the guard looked up the
    address, found nothing, and waved through a name that already existed.
    """
    hits, seen = [], set()
    for q in [x for x in (name, email, phone) if x]:
        for h in _whois_json(q):
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


def _whois_json(q):
    try:
        r = subprocess.run([sys.executable, os.path.join(VAULT, "whois.py"), q, "--json"],
                           capture_output=True, text=True, timeout=120,
                           env={**os.environ, "VAULT": VAULT})
        d = json.loads(r.stdout or "{}")
        # PARTIAL matches count as hits for the duplicate guard. A full-string miss is NOT proof
        # nobody exists -- "Freya Finch" could not match a record stored as "Freya", so the guard
        # waved through a duplicate on 26 Jul 2026. Near matches now block too (--allow-duplicate
        # is the deliberate override).
        out = list(d.get("results", []))
        for pm in d.get("partial_matches", []):
            pm = dict(pm); pm["_partial"] = True
            out.append(pm)
        return out
    except Exception:
        return []           # fail-soft: a duplicate check that errors must not block a legitimate add


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
        # writing it fails. `company_name` is the free-text company on the partner itself; linking to
        # a real parent company needs parent_id (an ID), which the caller does not have here.
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


def add_personal(name, email, phone, company, role, dry):
    home = "Google Contacts (Pete's phone)"
    if dry:
        return home, {"name": name, "email": email, "phone": phone, "org": company}
    if not email:
        _die("Google Contacts needs an email (people-api add requires one). "
             "Pass --email, or add them to a business system instead.")
    args = [sys.executable, os.path.join(VAULT, "people-api.py"), "add", name, email]
    if phone:
        args.append(phone)
    if company:
        args.append(company)
    r = subprocess.run(args, capture_output=True, text=True, timeout=90,
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        _die("google contacts add failed: " + (r.stderr or r.stdout)[:200])
    out = (r.stdout or "").strip()[:400]
    # Google Contacts is the HOME, but whois.py reads the CC mirror `public.google_contacts`.
    # Without this the person is invisible to the reader until the next sync, so the system
    # contradicts itself the moment it is used (found 26 Jul 2026: contact.py created Freya Finch,
    # whois still answered "NOT FOUND"). Write then re-sync, so read-after-write always holds.
    try:
        m = subprocess.run([sys.executable, os.path.join(VAULT, "google-contacts-sync.py")],
                           capture_output=True, text=True, timeout=180,
                           env={**os.environ, "VAULT": VAULT})
        out += ("\n  mirror: refreshed so whois.py can see them immediately"
                if m.returncode == 0 else
                "\n  ⚠ mirror refresh FAILED -- whois.py will not find this person until "
                "google-contacts-sync.py runs: " + (m.stderr or m.stdout)[:120])
    except Exception as e:
        out += f"\n  ⚠ mirror refresh FAILED ({type(e).__name__}) -- run google-contacts-sync.py"
    return home, out


ADDERS = {"sygma": add_sygma, "cd": add_cd, "personal": add_personal}


# ---------------------------------------------------------------- commands

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
                    print(f"      VAULT={VAULT} python3 {VAULT}/people-api.py update {res} name \"{name}\"")
                    for fld, val in (("email", a.get("email")), ("phone", a.get("phone"))):
                        if val and not h.get(fld):
                            print(f"      VAULT={VAULT} python3 {VAULT}/people-api.py update {res} {fld} {val}")
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


def _google_lookup(name):
    r = subprocess.run([sys.executable, os.path.join(VAULT, "people-api.py"), "search", name],
                       capture_output=True, text=True, timeout=90,
                       env={**os.environ, "VAULT": VAULT})
    return (r.stdout or "").strip()


def cmd_phone(a):
    """Push an existing person onto the phone. Looks them up FIRST so we push a real record."""
    hits = find_existing(a["name"], None, None)
    if not hits:
        print(f"NOT FOUND: '{a['name']}' is in none of the four stores, so there is nothing to push.")
        print("Create the business record first:  contact.py add \"Name\" --entity sygma|cd")
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
    home, out = add_personal(src.get("name") or a["name"], email, phone, src.get("detail"), None, False)
    print(f"PUSHED to {home}: {src.get('name')}")
    print(f"  source of truth remains: {src.get('store')} (the phone is a VIEW, not the record)")
    return 0


def cmd_remove_phone(a):
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
    # This USED to dead-end, claiming the CC mirror does not carry resourceName. It does
    # (google_contacts.resource_name), so the claim was simply wrong and it sent Pete off to do it
    # by hand for no reason. Fixed 26 Jul 2026 -- now fully automated.
    done = 0
    for h in hits:
        res = (h.get("extra") or {}).get("resource_name")
        if not res:
            print(f"  ⚠ no resource_name for {h.get('name')} — skipped; re-run "
                  f"google-contacts-sync.py and try again")
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
        m = subprocess.run([sys.executable, os.path.join(VAULT, "google-contacts-sync.py")],
                           capture_output=True, text=True, timeout=180,
                           env={**os.environ, "VAULT": VAULT})
        print("  mirror: refreshed" if m.returncode == 0
              else "  ⚠ mirror refresh FAILED — run google-contacts-sync.py")
    print("\nThe business record is untouched — this only takes them off the phone.")
    return 0 if done else 1


def cmd_remove_record(a):
    if not a.get("confirm_delete"):
        _die("REFUSED. Deleting a BUSINESS record needs --yes-delete-business-record.\n"
             "  A business record going away is not the same as taking someone off the phone.\n"
             "  If you only want them off Pete's phone, use:  contact.py remove-phone \"Name\"")
    _die("not implemented on purpose. Deleting a live customer/supplier record is a destructive,\n"
         "  hard-to-reverse write on a business system; it should be done in the app (or Odoo) where\n"
         "  the consequences (linked bookings, invoices, deliverables) are visible. This tool will\n"
         "  not do it blind from a command line.")


# ---------------------------------------------------------------- cli

def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    # An unknown flag must ABORT. "--dry" was silently ignored on 26 Jul 2026 and two junk contacts
    # were written straight into Pete's live Google Contacts by what was meant to be a dry run.
    KNOWN = {"--dry-run", "--dry", "--allow-duplicate", "--yes-delete-business-record",
             "--entity", "--email", "--phone", "--company", "--role"}
    unknown = [x for x in rest if x.startswith("--") and x.split("=")[0] not in KNOWN]
    if unknown:
        _die("unknown flag(s): " + ", ".join(unknown) +
             "\n  Refusing rather than ignoring them -- an ignored --dry flag WRITES FOR REAL."
             "\n  Known flags: " + ", ".join(sorted(KNOWN)))
    a = {"dry": ("--dry-run" in rest or "--dry" in rest),
         "allow_duplicate": "--allow-duplicate" in rest,
         "confirm_delete": "--yes-delete-business-record" in rest}
    pos = [x for x in rest if not x.startswith("--")]
    # strip flag VALUES out of the positional list
    vals = {}
    for f in ("entity", "email", "phone", "company", "role"):
        if f"--{f}" in rest:
            i = rest.index(f"--{f}")
            if i + 1 < len(rest):
                vals[f] = rest[i + 1]
                if rest[i + 1] in pos:
                    pos.remove(rest[i + 1])
    a.update(vals)
    if not pos:
        _die("a name is required")
    a["name"] = pos[0]

    if cmd == "add":
        return cmd_add(a)
    if cmd == "phone":
        return cmd_phone(a)
    if cmd == "remove-phone":
        return cmd_remove_phone(a)
    if cmd == "remove-record":
        return cmd_remove_record(a)
    _die(f"unknown command '{cmd}'. Use: add | phone | remove-phone | remove-record")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
