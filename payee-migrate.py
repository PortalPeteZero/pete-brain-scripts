#!/usr/bin/env python3
"""
payee-migrate.py -- move the Sygma payees into the platform as supplier contacts. (Plan step B5.)

READ THIS FIRST -- B5 IS NOT A "MOVE"
  The plan says "move the 12 Sygma payees onto the supplier record". Checked against live data:
  **0 of the 12 exist as platform contacts.** So this CREATES 12 business records in a live CRM,
  which the plan's own safety rule covers -- "confirm before creating". Hence: nothing runs without
  an explicit --confirm AND an explicit selection.

  And at least two of the twelve are NOT SUPPLIERS AT ALL. They are payment destinations:
      HMRC Cumbernauld — Class 1A NIC (P11D)     a tax payment
      Invoice IU019465 (NL / RABO)               a one-off invoice reference
  Creating those as supplier contacts would put permanent non-entities in the CRM. They are marked
  NOT-A-SUPPLIER below and are EXCLUDED unless explicitly named.

WHERE THE BANK DETAILS GO
  public.contact_bank_accounts -- a SEPARATE, admin-only table, NOT columns on public.contacts.
  Reason (verified): contacts RLS grants SELECT to `trainer` and `authenticated` holds SELECT on
  every column, so bank details on the contact row would be readable by all 12 trainers. Proven:
  as a real trainer the bank table returns 0 rows while contacts returns 1,611.

THE CC COPY IS NOT DELETED
  This COPIES. `public.bank_accounts` in the Command Centre keeps its rows, so the migration is
  reversible and nothing is destroyed. Retiring the CC copy is a separate decision for Pete, per
  "nothing deleted without Pete's explicit yes".

USAGE
  payee-migrate.py                          # dry run: show all 12, what would be created
  payee-migrate.py --suppliers-only         # dry run for the 10 genuine suppliers
  payee-migrate.py --suppliers-only --confirm
  payee-migrate.py --only "PHMG,Rausch UK Ltd" --confirm
"""
import json, os, re, sys, subprocess, urllib.request, urllib.parse

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.join(VAULT, "Library", "processes", "secrets")

# Not suppliers -- payment destinations. Excluded unless named explicitly with --only.
NOT_A_SUPPLIER = {
    "HMRC Cumbernauld — Class 1A NIC (P11D)": "a tax payment destination, not a company we buy from",
    "Invoice IU019465 (NL / RABO)": "a one-off invoice reference, not a standing supplier",
}
BANK_FIELDS = ("holder_name", "bank_name", "sort_code", "account_number",
               "iban", "bic_swift", "currency", "bank_address", "reference",
               "source_doc", "verified_on", "notes")


def _platform():
    k = json.load(open(os.path.join(SEC, "sygma-portal-supabase-keys.json")))
    return k["url"].rstrip("/"), k["service_role"]


def _post(path, rows):
    url, key = _platform()
    req = urllib.request.Request(
        f"{url}/rest/v1/{path}", data=json.dumps(rows).encode(),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=representation"},
        method="POST")
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def cc_payees():
    r = subprocess.run([sys.executable, os.path.join(VAULT, "cc-sql.py")],
                       input="SELECT * FROM bank_accounts WHERE entity='Sygma' AND kind='payee' ORDER BY label",
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "VAULT": VAULT})
    txt = (r.stdout or "").strip()
    return json.loads(txt) if txt.startswith("[") else []


def existing_contacts():
    url, key = _platform()
    u = f"{url}/rest/v1/contacts?" + urllib.parse.urlencode(
        {"select": "id,full_name,company_name,supplier_rank", "limit": "5000"})
    with urllib.request.urlopen(urllib.request.Request(
            u, headers={"apikey": key, "Authorization": f"Bearer {key}"}), timeout=40) as r:
        return json.load(r)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main():
    a = sys.argv[1:]
    if "-h" in a or "--help" in a:
        print(__doc__)
        return 0
    confirm = "--confirm" in a
    suppliers_only = "--suppliers-only" in a
    only = None
    if "--only" in a and a.index("--only") + 1 < len(a):
        only = {x.strip() for x in a[a.index("--only") + 1].split(",") if x.strip()}

    payees = cc_payees()
    contacts = existing_contacts()
    idx = {}
    for c in contacts:
        for f in (c.get("company_name"), c.get("full_name")):
            if f:
                idx.setdefault(_norm(f), c)

    todo, skipped = [], []
    for p in payees:
        label = p["label"]
        if only is not None and label not in only:
            continue
        if only is None and suppliers_only and label in NOT_A_SUPPLIER:
            skipped.append((label, NOT_A_SUPPLIER[label]))
            continue
        core = _norm(label.split("(")[0].split("—")[0])
        hit = next((c for k, c in idx.items() if core and len(k) > 4 and (core in k or k in core)), None)
        if hit:
            skipped.append((label, f"already a contact ({hit.get('company_name') or hit.get('full_name')})"))
            continue
        todo.append(p)

    print("payee-migrate — Sygma payees -> platform supplier contacts + admin-only bank rows\n")
    for p in todo:
        flag = "  ⚠ NOT A SUPPLIER" if p["label"] in NOT_A_SUPPLIER else ""
        has = [f for f in ("sort_code", "account_number", "iban") if p.get(f)]
        print(f"  + {p['label'][:46]:48} bank: {','.join(has) or 'NONE'}{flag}")
    for label, why in skipped:
        print(f"  - {label[:46]:48} SKIPPED — {why}")
    print(f"\n  would create: {len(todo)} supplier contact(s) + {len(todo)} admin-only bank row(s)")
    print("  the Command Centre copy in public.bank_accounts is NOT deleted — this copies.")

    if not confirm:
        print("\n  DRY RUN — nothing written. This CREATES business records in a live CRM, so it")
        print("  needs --confirm plus a selection (--suppliers-only or --only \"A,B\").")
        return 0
    if only is None and not suppliers_only:
        print("\n  REFUSED — pass --suppliers-only or --only \"...\". Creating all 12 blind would put",
              file=sys.stderr)
        print("  a tax-payment destination and a one-off invoice reference in the CRM permanently.",
              file=sys.stderr)
        return 2

    made = 0
    for p in todo:
        c = _post("contacts", [{
            "full_name": p["label"], "company_name": p["label"],
            "type": "supplier", "supplier_rank": 1, "customer_rank": 0, "partner_rank": 0,
            "source": "bank-payee-migration",
        }])[0]
        row = {"contact_id": c["id"], "label": p["label"]}
        for f in BANK_FIELDS:
            if p.get(f):
                row[f] = p[f]
        _post("contact_bank_accounts", [row])
        made += 1
        print(f"    created: {p['label']} -> contact {c['id'][:8]} + bank row")
    print(f"\n  CREATED {made}. The CC copy is untouched — reversible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
