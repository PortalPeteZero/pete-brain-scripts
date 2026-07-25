#!/usr/bin/env python3
"""
cd-supplier-reconcile.py -- reconcile Canary Detect payees against Odoo. (Plan step B6.)

WHAT THE PLAN GOT WRONG, corrected here from live data (25 Jul 2026)

  1. "Flag Indelasa" is WRONG as written. Odoo holds TWO records:
         id=54  "Indelasa"                                        supplier_rank 0
         id=55  "Industriales de construcción de Lanzarote, S.A."  supplier_rank 145
     The real supplier record already exists and is correctly flagged. id=54 is a DUPLICATE STUB.
     Setting supplier_rank on it would create a SECOND supplier for the same company -- the exact
     duplication this reconciliation exists to remove. The fix is to merge/retire the stub, which is
     a DELETE, and deletes need Pete's explicit yes. So this tool reports it and does not touch it.

  2. The plan says 8 payees are missing from Odoo (7 after excluding Infiniti). Live count is 11
     (10 after Infiniti). Three the plan never listed: Carburos Metalicos, Hermanos Tavio Santana
     S.L, Pedro Santana y Hijos SL.

  3. Infiniti is excluded by standing instruction (the Daryl payment -- leave it, never re-surface).

MATCHING IS NAME-SIMILARITY, SO NOTHING IS CREATED BLIND
  A payee is judged "missing" by comparing normalised names. That is good enough to raise a question
  and NOT good enough to create a company record on, which is why --confirm needs an explicit
  selection. The plan's own rule: "confirm each against the real record before creating a supplier."

USAGE
  cd-supplier-reconcile.py                       # report only (default)
  cd-supplier-reconcile.py --create "Gazette Life,Villas Now" --confirm
"""
import json, os, re, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
EXCLUDED = {"Infiniti": "standing instruction — the Daryl payment, leave it, never re-surface"}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _run(args, timeout=120):
    r = subprocess.run([sys.executable] + args, capture_output=True, text=True,
                       timeout=timeout, env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1][:180])
    return r.stdout


def cd_payees():
    out = _run([f"{VAULT}/cc-sql.py",
                "SELECT label FROM bank_accounts WHERE entity='Canary Detect' AND kind='payee' ORDER BY label"])
    return [x["label"] for x in json.loads(out)]


def odoo_partners():
    out = _run([f"{VAULT}/odoo-api.py", "search-read", "res.partner", "[]",
                "name,supplier_rank,customer_rank,email,is_company", "--limit", "3000"])
    return json.loads(out)


def classify():
    payees, partners = cd_payees(), odoo_partners()
    idx = {_norm(p["name"]): p for p in partners}
    ok, unflagged, missing = [], [], []
    for label in payees:
        core = _norm(label.split("(")[0].split("—")[0])
        hit = next((p for k, p in idx.items() if core and len(k) > 4 and (core in k or k in core)), None)
        if not hit and core:
            # Fall back to the EMAIL DOMAIN. A trading name often shares nothing with the registered
            # name: "Indelasa" vs "Industriales de construcción de Lanzarote, S.A." have no words in
            # common, but the company emails from indelasa.net. Without this the payee reads as
            # MISSING after the two were merged, and a later --create would rebuild the very
            # duplicate the merge removed (25 Jul 2026).
            for p in partners:
                dom = (p.get("email") or "").split("@")[-1]
                if dom and core in _norm(dom):
                    hit = p
                    break
        if not hit:
            missing.append(label)
        elif (hit.get("supplier_rank") or 0) == 0:
            unflagged.append((label, hit))
        else:
            ok.append((label, hit))
    return ok, unflagged, missing, partners


def main():
    a = sys.argv[1:]
    if "-h" in a or "--help" in a:
        print(__doc__)
        return 0
    confirm = "--confirm" in a
    create = None
    if "--create" in a and a.index("--create") + 1 < len(a):
        create = {x.strip() for x in a[a.index("--create") + 1].split(",") if x.strip()}

    ok, unflagged, missing, partners = classify()
    print(f"cd-supplier-reconcile — {len(ok) + len(unflagged) + len(missing)} Canary Detect payees\n")
    print(f"  correctly flagged suppliers in Odoo : {len(ok)}")

    if unflagged:
        print(f"\n  IN ODOO but supplier_rank = 0 ({len(unflagged)}):")
        for label, hit in unflagged:
            print(f"    {label[:40]:42} id={hit['id']} '{hit['name']}'")
            # Is there ALREADY a flagged supplier for the same company?
            # Name matching alone CANNOT see this: the Indelasa stub is called "Indelasa" while the
            # real record is "Industriales de construcción de Lanzarote, S.A." -- no shared words.
            # The link is the EMAIL DOMAIN (info@indelasa.net), so match on that as well as name.
            core = _norm(label.split("(")[0])
            dupes = []
            for p in partners:
                if (p.get("supplier_rank") or 0) <= 0 or p["id"] == hit["id"]:
                    continue
                # A CONTACT PERSON at the company is not a duplicate COMPANY. Ubaldo
                # (taller@indelasa.com) shares the domain but is a child contact, not a second
                # supplier record -- flagging him would send Pete chasing a merge that isn't one.
                if not p.get("is_company"):
                    continue
                by_name = core and (core in _norm(p["name"]) or _norm(p["name"]).startswith(core[:6]))
                dom = (p.get("email") or "").split("@")[-1] if p.get("email") else ""
                by_domain = bool(core) and core in _norm(dom)
                if by_name or by_domain:
                    dupes.append(p)
            for d in dupes:
                print(f"      ⚠ DUPLICATE — a flagged supplier for this company already exists:")
                print(f"        id={d['id']} '{d['name']}' supplier_rank={d['supplier_rank']}")
                print(f"        Do NOT set the flag on id={hit['id']} — that makes a SECOND supplier.")
                print(f"        The fix is to merge/retire the stub, which is a DELETE and needs Pete's yes.")

    real_missing = [m for m in missing if m not in EXCLUDED]
    print(f"\n  NOT IN ODOO AT ALL ({len(missing)}, {len(real_missing)} after exclusions):")
    for m in missing:
        note = f"   EXCLUDED — {EXCLUDED[m]}" if m in EXCLUDED else ""
        print(f"    {m[:44]:46}{note}")
    print("\n  Matched on NAME SIMILARITY — good enough to raise the question, not good enough")
    print("  to create a company record on. Each must be checked against the real record first.")

    if not confirm or not create:
        print("\n  REPORT ONLY — nothing written. To create, name them explicitly:")
        print('    cd-supplier-reconcile.py --create "Gazette Life,Villas Now" --confirm')
        return 0

    made = 0
    for label in sorted(create):
        if label in EXCLUDED:
            print(f"    SKIPPED {label} — {EXCLUDED[label]}")
            continue
        if label not in missing:
            print(f"    SKIPPED {label} — not in the missing list (already in Odoo?)")
            continue
        out = _run([f"{VAULT}/odoo-api.py", "create", "res.partner",
                    json.dumps({"name": label, "supplier_rank": 1, "customer_rank": 0,
                                "is_company": True})])
        print(f"    created in Odoo: {label} -> {out.strip()}")
        made += 1
    print(f"\n  CREATED {made}. Indelasa was NOT touched — it needs a merge decision.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"cd-supplier-reconcile: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
