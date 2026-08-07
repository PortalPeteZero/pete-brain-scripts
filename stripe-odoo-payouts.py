#!/usr/bin/env python3
"""stripe-odoo-payouts.py — make every Stripe payout explain itself on the Odoo bank line.

THE PROBLEM (Nicola, 7 Aug 2026): a Stripe payout arrives in Odoo as one line reading
`TRANSFER PAYMENT FROM STRIPE STRIPE` with no reference and no breakdown. It is net of card fees and
bundles several invoices, so it can NEVER match an invoice total. Nobody could tell what it was for
without asking Pete.

WHAT THIS DOES: for each paid Stripe payout, reads the balance transactions behind it (each charge
carries its Odoo invoice number as the description), finds the matching unreconciled bank statement
line in Odoo, and writes the breakdown onto that line — a readable label plus the full itemisation.

WHAT IT DOES NOT DO: it posts NO journal entries and reconciles NOTHING. It only writes three text
fields on a statement line. The worst case is a wrong label on one line. Auto-reconciliation is
Phase 3 of [[plan-stripe-odoo-payouts]] and is deliberately gated until this has been proven on two
real payouts.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/stripe-odoo-payouts.py                 # dry run, prints what it would write
  VAULT=/tmp/pbs python3 /tmp/pbs/stripe-odoo-payouts.py --apply         # write to Odoo
  VAULT=/tmp/pbs python3 /tmp/pbs/stripe-odoo-payouts.py --days 90       # widen the lookback (default 45)
  VAULT=/tmp/pbs python3 /tmp/pbs/stripe-odoo-payouts.py --account odoo  # one Stripe account only

Exit code 1 if a payout could not be matched to a bank line, so it can gate a script.

# CRON-META
# what: writes each Stripe payout's invoice-by-invoice breakdown onto its Odoo bank statement line
# why: a payout is net of fees and bundles invoices, so it never matches one and nobody could tell what it was for
# reads: Stripe payouts + balance transactions (both Camello Blanco accounts); Odoo account.bank.statement.line
# writes: payment_ref / ref / narration on unreconciled Odoo bank statement lines (no journal entries, no reconciliation)
# entity: canary-detect
# report:
# schedule: 25 7 * * *
# timezone: Atlantic/Canary
# CRON-META-END
"""
import argparse
import datetime as dt
import importlib.util
import os
import sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
MARKER = "Stripe payout"          # our own label prefix — makes the run idempotent
ODOO_BANK_JOURNAL = 11            # Sabadell — where Stripe settles today
DATE_WINDOW_DAYS = 4              # bank posting can lag the Stripe arrival date
STRIPE_ACCOUNTS = ["odoo", "leakguard"]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stripe_api = _load("stripe_api", f"{VAULT}/stripe-api.py")
odoo_api = _load("odoo_api", f"{VAULT}/odoo-api.py")


def _money(cents):
    return cents / 100.0


def _fmt(amount):
    return f"{amount:,.2f}"


def payout_breakdown(payout_id, account):
    """Return (charges, fee_total, other) for one payout, read from Stripe."""
    txns = stripe_api.stripe(
        "GET", "/v1/balance_transactions",
        {"payout": payout_id, "limit": 100},
        live=True, account=account,
    ).get("data", [])

    charges, fees, other = [], 0.0, []
    for t in txns:
        if t["type"] == "payout":
            continue                                   # the payout's own -amount line
        if t["type"] == "charge":
            charges.append({
                "desc": (t.get("description") or "").strip() or "(no invoice reference)",
                "gross": _money(t["amount"]),
                "fee": _money(t["fee"]),
                "net": _money(t["net"]),
            })
        elif t["type"] in ("stripe_fee", "application_fee", "fee"):
            fees += _money(-t["net"])                   # net is negative for a fee
        else:
            other.append({"type": t["type"], "net": _money(t["net"]),
                          "desc": (t.get("description") or "").strip()})
    return charges, fees, other


def build_text(payout, charges, account_fees, other):
    """Return (label, reference, narration_html) for the Odoo statement line."""
    arrival = dt.datetime.fromtimestamp(payout["arrival_date"], dt.UTC).strftime("%-d %b")
    gross = sum(c["gross"] for c in charges)
    card_fees = sum(c["fee"] for c in charges)
    total_fees = card_fees + account_fees

    if charges:
        inline = " + ".join(f"{c['desc']} {_fmt(c['gross'])}" for c in charges[:3])
        if len(charges) > 3:
            inline += f" + {len(charges) - 3} more"
        label = f"{MARKER} {arrival}: {inline}, less {_fmt(total_fees)} fees"
    else:
        label = f"{MARKER} {arrival}: no customer charges, {_fmt(total_fees)} fees"

    rows = [f"{c['desc']}: {_fmt(c['gross'])} collected, {_fmt(c['fee'])} card fee, "
            f"{_fmt(c['net'])} net" for c in charges]
    if account_fees:
        rows.append(f"Stripe account charges: {_fmt(account_fees)}")
    for o in other:
        rows.append(f"{o['type']}: {_fmt(o['net'])} {o['desc']}".rstrip())
    rows.append(f"TOTAL: {_fmt(gross)} collected, {_fmt(total_fees)} fees, "
                f"{_fmt(_money(payout['amount']))} banked")

    reference = f"{payout['id']} ({len(charges)} invoice{'s' if len(charges) != 1 else ''})"
    narration = ("<p><b>What is in this Stripe payout</b></p><ul>"
                 + "".join(f"<li>{r}</li>" for r in rows)
                 + "</ul><p>A payout is Stripe paying over what it has collected, net of card fees, "
                   "so it never matches a single invoice. Written automatically by "
                   "stripe-odoo-payouts.py — no entries were posted.</p>")
    return label, reference, narration


def find_statement_line(amount, arrival_ts):
    """Find the unreconciled Odoo bank line for this payout. Returns a row or None."""
    arrival = dt.datetime.fromtimestamp(arrival_ts, dt.UTC).date()
    lo = (arrival - dt.timedelta(days=DATE_WINDOW_DAYS)).isoformat()
    hi = (arrival + dt.timedelta(days=DATE_WINDOW_DAYS)).isoformat()
    rows = odoo_api._execute(
        "account.bank.statement.line", "search_read",
        [[["journal_id", "=", ODOO_BANK_JOURNAL],
          ["amount", ">", amount - 0.005], ["amount", "<", amount + 0.005],
          ["date", ">=", lo], ["date", "<=", hi]]],
        {"fields": ["date", "payment_ref", "amount", "is_reconciled", "ref"], "limit": 5},
    )
    if len(rows) > 1:
        return {"_ambiguous": rows}
    return rows[0] if rows else None


def run(days, apply_changes, accounts):
    since = int((dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).timestamp())
    unmatched = 0

    for account in accounts:
        payouts = stripe_api.stripe(
            "GET", "/v1/payouts",
            {"limit": 100, "status": "paid", "created[gte]": since},
            live=True, account=account,
        ).get("data", [])
        print(f"\n== Stripe account '{account}': {len(payouts)} paid payout(s) in the last {days}d")

        for p in payouts:
            amount = _money(p["amount"])
            arrival = dt.datetime.fromtimestamp(p["arrival_date"], dt.UTC).strftime("%Y-%m-%d")
            charges, account_fees, other = payout_breakdown(p["id"], account)
            label, reference, narration = build_text(p, charges, account_fees, other)

            line = find_statement_line(amount, p["arrival_date"])
            if line is None:
                print(f"  {arrival}  {_fmt(amount)}  NO ODOO BANK LINE FOUND  ({p['id']})")
                unmatched += 1
                continue
            if "_ambiguous" in line:
                ids = ", ".join(str(r["id"]) for r in line["_ambiguous"])
                print(f"  {arrival}  {_fmt(amount)}  AMBIGUOUS — {len(line['_ambiguous'])} "
                      f"lines match (ids {ids}); left alone")
                unmatched += 1
                continue
            if (line.get("payment_ref") or "").startswith(MARKER):
                print(f"  {arrival}  {_fmt(amount)}  already labelled, skipping")
                continue

            print(f"  {arrival}  {_fmt(amount)}  line {line['id']}")
            print(f"      was : {line.get('payment_ref')}")
            print(f"      now : {label}")
            if apply_changes:
                odoo_api._execute("account.bank.statement.line", "write",
                                  [[line["id"]], {"payment_ref": label,
                                                  "ref": reference,
                                                  "narration": narration}])
                print("      WRITTEN")

    if not apply_changes:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
    return 1 if unmatched else 0


# ---------------------------------------------------------------------------
# PHASE 3 — reconcile the payout line automatically.
#
# GATED ON PURPOSE. This is the only part that posts accounting entries, so it
# refuses to run until the labelling in Phase 1 has been proven on at least
# PROOF_REQUIRED real payouts. The gate is a live count out of Odoo, not a
# comment somebody can ignore.
#
# KNOWN OPEN QUESTION — READ BEFORE ENABLING (7 Aug 2026)
# This logic was written for the PRE-Phase-2 shape, where a card payment sat as
# an outstanding line in 572991 and the payout had to be matched against each of
# them. Phase 2 repointed the Stripe provider at its own bank journal, so a
# payment now posts straight to 572006 and Odoo may treat it as already landed —
# in which case the payout is a simple transfer (one Cr 572006 + the fee line)
# and no per-payment matching is needed at all.
# The next payout still covers charges booked the OLD way, so this code is right
# for it. What happens to the FIRST payment booked the new way has NOT been
# observed and must be checked against a real one before this is trusted there.
# ---------------------------------------------------------------------------
PROOF_REQUIRED = 2
FEE_ACCOUNT_CODE = "626000"          # Servicios bancarios y similares
OUTSTANDING_ACCOUNTS = ["572991", "572006"]   # pre- and post-Phase-2 homes


def _account_id(code):
    rows = odoo_api._execute("account.account", "search_read",
                             [[["code", "=", code]]], {"fields": ["id"], "limit": 1})
    if not rows:
        raise SystemExit(f"account {code} not found in the chart")
    return rows[0]["id"]


def proof_count():
    """How many payouts have already been labelled by Phase 1? The gate."""
    return odoo_api._execute(
        "account.bank.statement.line", "search_count",
        [[["payment_ref", "=like", f"{MARKER}%"]]])


def outstanding_line_for(payment_intent, account_ids):
    """The unreconciled outstanding line for one Stripe charge.

    Joined on the Stripe payment-intent id, which Odoo writes into the payment
    move's name (e.g. 'PBNK4/2026/00046 (INV/2026/00283 - pi_3TyuIg…)'). An exact
    key, not an amount guess — two customers can pay the same amount on one day.
    """
    rows = odoo_api._execute(
        "account.move.line", "search_read",
        [[["account_id", "in", account_ids], ["reconciled", "=", False],
          ["parent_state", "=", "posted"], ["move_id.name", "like", payment_intent]]],
        {"fields": ["id", "debit", "credit", "move_id", "partner_id"], "limit": 5})
    if len(rows) != 1:
        return None
    return rows[0]


def reconcile(days, apply_changes, accounts):
    have = proof_count()
    if have < PROOF_REQUIRED:
        print(f"REFUSED: reconciliation is gated until Phase 1 has been proven on "
              f"{PROOF_REQUIRED} real payouts. Odoo shows {have} labelled so far.")
        print("Label the next payout first (run without --reconcile), then come back.")
        return 2

    fee_account = _account_id(FEE_ACCOUNT_CODE)
    out_accounts = [_account_id(c) for c in OUTSTANDING_ACCOUNTS]
    since = int((dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).timestamp())
    failed = 0

    for account in accounts:
        payouts = stripe_api.stripe("GET", "/v1/payouts",
                                    {"limit": 100, "status": "paid", "created[gte]": since},
                                    live=True, account=account).get("data", [])
        for p in payouts:
            line = find_statement_line(_money(p["amount"]), p["arrival_date"])
            if not line or "_ambiguous" in line or line.get("is_reconciled"):
                continue
            if not (line.get("payment_ref") or "").startswith(MARKER):
                continue                                   # label it before reconciling it

            txns = stripe_api.stripe("GET", "/v1/balance_transactions",
                                     {"payout": p["id"], "limit": 100},
                                     live=True, account=account).get("data", [])
            charges = [t for t in txns if t["type"] == "charge"]
            fees = sum(_money(-t["net"]) for t in txns
                       if t["type"] in ("stripe_fee", "application_fee", "fee"))
            fees += sum(_money(t["fee"]) for t in charges)

            # resolve every charge to its outstanding line BEFORE writing anything
            matched, missing = [], []
            for c in charges:
                pi = c.get("payment_intent")
                ol = outstanding_line_for(pi, out_accounts) if pi else None
                (matched if ol else missing).append((c, ol))
            if missing:
                print(f"  {p['id']}: {len(missing)} charge(s) have no single outstanding "
                      f"line in Odoo — left alone, nothing written")
                failed += 1
                continue

            print(f"  {p['id']}  line {line['id']}  {len(matched)} payment(s), "
                  f"{_fmt(fees)} fees -> {FEE_ACCOUNT_CODE}")
            if not apply_changes:
                continue

            suspense = [l for l in odoo_api._execute(
                "account.move.line", "search_read",
                [[["move_id", "=", line["move_id"][0] if isinstance(line.get("move_id"), list)
                   else line["move_id"]]]],
                {"fields": ["id", "account_id", "debit", "credit"]})
                if l["account_id"][0] not in out_accounts and l["credit"] > 0]

            cmds = [(2, s["id"]) for s in suspense]
            for c, ol in matched:
                cmds.append((0, 0, {"account_id": ol["account_id"][0] if isinstance(
                    ol.get("account_id"), list) else out_accounts[0],
                    "credit": _money(c["amount"]), "debit": 0.0,
                    "partner_id": ol["partner_id"][0] if ol.get("partner_id") else False,
                    "name": c.get("description") or c["id"]}))
            if fees:
                cmds.append((0, 0, {"account_id": fee_account, "debit": fees, "credit": 0.0,
                                    "name": f"Stripe fees {p['id']}"}))

            move_id = line["move_id"][0] if isinstance(line.get("move_id"), list) else line["move_id"]
            odoo_api._execute("account.move", "write", [[move_id], {"line_ids": cmds}])

            # now match the new credit lines against the payments' own debit lines
            fresh = odoo_api._execute(
                "account.move.line", "search_read",
                [[["move_id", "=", move_id], ["account_id", "in", out_accounts],
                  ["reconciled", "=", False]]], {"fields": ["id", "credit"]})
            for (c, ol), f in zip(matched, sorted(fresh, key=lambda x: -x["credit"])):
                odoo_api._execute("account.move.line", "reconcile", [[f["id"], ol["id"]]])
            print("      RECONCILED")

    if not apply_changes:
        print("\nDRY RUN — nothing posted. Re-run with --reconcile --apply.")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write to Odoo (default is a dry run)")
    ap.add_argument("--days", type=int, default=45, help="payout lookback window (default 45)")
    ap.add_argument("--account", choices=STRIPE_ACCOUNTS, help="one Stripe account only")
    ap.add_argument("--reconcile", action="store_true",
                    help="PHASE 3: also post the reconciliation (gated on 2 proven payouts)")
    a = ap.parse_args()
    accounts = [a.account] if a.account else STRIPE_ACCOUNTS
    if a.reconcile:
        sys.exit(reconcile(a.days, a.apply, accounts))
    sys.exit(run(a.days, a.apply, accounts))


if __name__ == "__main__":
    main()
