#!/usr/bin/env python3
"""account-delivery-check.py — is the customer's DELIVERED-WORK log current?

Born 19 Jul 2026: Pete spotted the Clancy delivery log had not moved since 1 Jul while a
month of panel reviews, site revisits and damage reports had been delivered (all goodwill).
Nothing checked it, so it silently rotted — the exact thing the account store exists to prevent.

The test: every meeting attended, every report published and every damage reviewed is a piece
of delivered work. If those exist and `account_deliverables` has nothing on or after that date,
the log is behind. Report-only — it never writes; the session decides what was actually
delivered and logs it (closeout check A11).

Usage:
  VAULT=/tmp/pbs python3 account-delivery-check.py                  # all customers
  VAULT=/tmp/pbs python3 account-delivery-check.py --customer clancy
  VAULT=/tmp/pbs python3 account-delivery-check.py --json

Exit 0 = current · 1 = behind (a session should log the missing work) · 2 = error.
"""
import argparse, json, os, sys, urllib.request, urllib.error

KEYS = os.path.expanduser("~/.config/pete-secrets/command-centre-supabase-keys.json")


def cc(path: str):
    k = json.load(open(KEYS))
    req = urllib.request.Request(
        f"{k['url'].rstrip('/')}/rest/v1/{path}",
        headers={"apikey": k["service_role_key"], "Authorization": f"Bearer {k['service_role_key']}"},
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def newest(rows, field):
    vals = [r[field] for r in rows if r.get(field)]
    return max(vals) if vals else None


def check(customer: str) -> dict:
    deliv = cc(f"account_deliverables?customer=eq.{customer}&select=date")
    meets = cc(f"account_meetings?customer=eq.{customer}&select=date,title")
    last_deliv = newest(deliv, "date")

    # Evidence of work done: meetings attended + (Clancy only) reports published / damages reviewed.
    evidence = [{"kind": "meeting", "date": m["date"], "what": m.get("title", "")} for m in meets if m.get("date")]
    if customer == "clancy":
        for r in cc("clancy_reports?select=report_date,title,report_type"):
            if r.get("report_date"):
                evidence.append({"kind": r["report_type"], "date": r["report_date"], "what": r["title"]})

    behind = sorted(
        [e for e in evidence if last_deliv is None or e["date"] > last_deliv],
        key=lambda e: e["date"], reverse=True,
    )
    return {
        "customer": customer,
        "last_delivery_logged": last_deliv,
        "unlogged_since": len(behind),
        "items": behind[:12],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        customers = [a.customer] if a.customer else sorted(
            {r["customer"] for r in cc("account_config?select=customer")}
        )
        results = [check(c) for c in customers]
    except urllib.error.HTTPError as e:
        print(f"account-delivery-check: API error {e.code}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps(results, indent=1))
    else:
        for r in results:
            if r["unlogged_since"] == 0:
                print(f"✓ {r['customer']}: delivery log current (last logged {r['last_delivery_logged']})")
            else:
                print(f"⚠ {r['customer']}: {r['unlogged_since']} piece(s) of work AFTER the last logged "
                      f"delivery ({r['last_delivery_logged']}) — log what was actually delivered:")
                for i in r["items"]:
                    print(f"    {i['date']}  [{i['kind']}]  {i['what'][:78]}")
    return 1 if any(r["unlogged_since"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
