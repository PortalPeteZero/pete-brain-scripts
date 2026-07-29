#!/usr/bin/env python3
"""Stripe weekly report -- both Camello Blanco Stripe accounts, emailed every Friday.

Covers the two accounts that exist under Camello Blanco S.L. (see stripe-api.py):
  leakguard  acct_1TaXNS7NHUwYrNfo  LeakGuard / Canary Detect billing (the CRM edge functions)
  odoo       acct_1TyDyr955lH6hyUF  card payments on Odoo invoices + quotes (live 28 Jul 2026)

Per account it reports the live balance, every balance transaction in the window grouped by type
(charges, refunds, fees, payouts, adjustments), the individual movements, payouts made, and the
payout schedule. Balance transactions are used rather than charges because they are the account's
own ledger: they carry the Stripe fee and the net, so gross/fee/net always reconcile to the balance.

Flags raised in the report:
  * payouts set to manual while money is sitting available (it never reaches the bank on its own)
  * refunds or disputes in the window
  * an account that cannot be read (bad or rotated key)

Usage:
  python3 stripe-weekly-report.py                 # last 7 days, email Pete
  python3 stripe-weekly-report.py --dry-run       # render + print, send nothing
  python3 stripe-weekly-report.py --days 30       # different window
  python3 stripe-weekly-report.py --to a@b.com,c@d.com
"""
# CRON-META
# what: Weekly Stripe report for both Camello Blanco accounts (LeakGuard + Odoo) to Pete.
# why: Pete sees card takings, fees, balances and payouts across both Stripe accounts without logging in.
# reads: Stripe API (both accounts, live keys)
# writes: HTML email + reports.snapshots row (stripe-weekly)
# entity: canary-detect
# report: stripe-weekly
# schedule: 0 18 * * 5
# timezone: Atlantic/Canary
# CRON-META-END
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
TZ = ZoneInfo("Atlantic/Canary")

RECIPIENTS_DEFAULT = ["pete.ashcroft@sygma-solutions.com"]
SENDER = "pete@canary-detect.com"

NAVY = "#1f2c47"
ORANGE = "#f7951d"
GREY = "#667085"
RED = "#b42318"
GREEN = "#067647"
LINE = "#e2e6f0"

# Human labels for the account slugs stripe-api.py knows about.
ACCOUNTS = [
    ("leakguard", "LeakGuard", "LeakGuard and Canary Detect billing"),
    ("odoo", "Odoo invoices", "Card payments on Odoo invoices and quotes"),
]

# Balance-transaction types worth naming individually; anything else falls through to "other".
TYPE_LABELS = {
    "charge": "Card payments",
    "payment": "Card payments",
    "refund": "Refunds",
    "payment_refund": "Refunds",
    "adjustment": "Disputes and adjustments",
    "stripe_fee": "Stripe billing fees",
    "payout": "Payouts to bank",
    "payout_cancel": "Payouts reversed",
    "transfer": "Transfers out",
}
MONEY_IN_TYPES = {"charge", "payment"}
BAD_TYPES = {"refund", "payment_refund", "adjustment"}


def _stripe_mod():
    spec = importlib.util.spec_from_file_location("stripe_api", str(SCRIPT_DIR / "stripe-api.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gmail():
    spec = importlib.util.spec_from_file_location("gmail_api", str(SCRIPT_DIR / "gmail-api.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GmailAPI()


def money(cents, currency="eur"):
    sym = {"eur": "€", "gbp": "£", "usd": "$"}.get((currency or "eur").lower(), "")
    return f"{sym}{cents / 100:,.2f}"


def fetch_all(sapi, path, params, account, limit_pages=20):
    """Page through a Stripe list endpoint. Returns (rows, error_or_None)."""
    rows, starting_after = [], None
    for _ in range(limit_pages):
        p = dict(params, limit=100)
        if starting_after:
            p["starting_after"] = starting_after
        r = sapi.stripe("GET", path, p, live=True, account=account)
        if isinstance(r, dict) and "error" in r:
            err = r["error"]
            msg = err.get("error", {}).get("message") if isinstance(err, dict) else str(err)
            return rows, (msg or str(err))[:200]
        data = r.get("data", [])
        rows.extend(data)
        if not r.get("has_more") or not data:
            return rows, None
        starting_after = data[-1]["id"]
    return rows, None


def collect(sapi, slug, since_ts):
    """Everything the report needs for one account. Never raises -- errors ride in the dict."""
    out = {"slug": slug, "error": None, "available": defaultdict(int), "pending": defaultdict(int),
           "txns": [], "payouts": [], "schedule": {}, "account_id": None, "display_name": None}

    acct = sapi.stripe("GET", "/v1/account", None, live=True, account=slug)
    if isinstance(acct, dict) and "error" in acct:
        err = acct["error"]
        msg = err.get("error", {}).get("message") if isinstance(err, dict) else str(err)
        out["error"] = f"Could not read the account: {msg}"
        return out
    out["account_id"] = acct.get("id")
    out["display_name"] = (acct.get("settings", {}).get("dashboard", {}) or {}).get("display_name")
    out["schedule"] = (acct.get("settings", {}).get("payouts", {}) or {}).get("schedule", {}) or {}
    out["payouts_enabled"] = acct.get("payouts_enabled")
    out["charges_enabled"] = acct.get("charges_enabled")

    bal = sapi.stripe("GET", "/v1/balance", None, live=True, account=slug)
    if isinstance(bal, dict) and "error" not in bal:
        for entry in bal.get("available", []):
            out["available"][entry["currency"]] += entry["amount"]
        for entry in bal.get("pending", []):
            out["pending"][entry["currency"]] += entry["amount"]

    txns, err = fetch_all(sapi, "/v1/balance_transactions", {"created[gte]": since_ts}, slug)
    if err:
        out["error"] = f"Could not read transactions: {err}"
    out["txns"] = sorted(txns, key=lambda t: t["created"], reverse=True)

    payouts, _ = fetch_all(sapi, "/v1/payouts", {"created[gte]": since_ts}, slug)
    out["payouts"] = sorted(payouts, key=lambda p: p["created"], reverse=True)
    return out


def summarise(acc):
    """Group the window's transactions by type. Returns (groups, totals)."""
    groups = defaultdict(lambda: {"count": 0, "gross": 0, "fee": 0, "net": 0, "currency": "eur"})
    totals = {"gross_in": 0, "fees": 0, "net_in": 0, "bad": 0}
    for t in acc["txns"]:
        label = TYPE_LABELS.get(t["type"], "Other")
        g = groups[label]
        g["count"] += 1
        g["gross"] += t["amount"]
        g["fee"] += t["fee"]
        g["net"] += t["net"]
        g["currency"] = t.get("currency", "eur")
        if t["type"] in MONEY_IN_TYPES:
            totals["gross_in"] += t["amount"]
            totals["fees"] += t["fee"]
            totals["net_in"] += t["net"]
        if t["type"] in BAD_TYPES:
            totals["bad"] += 1
    return groups, totals


def flags_for(acc, totals):
    out = []
    if acc["error"]:
        out.append(("red", acc["error"]))
        return out
    interval = (acc["schedule"] or {}).get("interval")
    avail = sum(acc["available"].values())
    if interval == "manual" and avail > 0:
        out.append(("red", f"Payouts are set to manual and {money(avail)} is sitting in Stripe. "
                           f"It will not reach the bank until someone pays it out."))
    elif interval == "manual":
        out.append(("amber", "Payouts are set to manual, so nothing moves to the bank on its own."))
    if acc.get("charges_enabled") is False:
        out.append(("red", "This account cannot currently take card payments."))
    if acc.get("payouts_enabled") is False:
        out.append(("red", "This account cannot currently pay out to the bank."))
    if totals["bad"]:
        out.append(("amber", f"{totals['bad']} refund or dispute in the window. Worth a look."))
    return out


def render(accounts, start, end):
    def h(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    grand_avail = sum(sum(a["available"].values()) for a in accounts if not a["error"])
    grand_pending = sum(sum(a["pending"].values()) for a in accounts if not a["error"])
    grand_in = 0
    parts = []

    for acc, (label, blurb) in zip(accounts, [(l, b) for _, l, b in ACCOUNTS]):
        groups, totals = summarise(acc)
        grand_in += totals["net_in"]
        flags = flags_for(acc, totals)

        rows = ""
        for name, g in sorted(groups.items(), key=lambda kv: -abs(kv[1]["gross"])):
            colour = GREEN if g["gross"] > 0 else (RED if g["gross"] < 0 else NAVY)
            rows += (f"<tr><td style='padding:7px 10px;border-top:1px solid {LINE}'>{h(name)}</td>"
                     f"<td style='padding:7px 10px;border-top:1px solid {LINE};text-align:center;color:{GREY}'>{g['count']}</td>"
                     f"<td style='padding:7px 10px;border-top:1px solid {LINE};text-align:right;color:{colour}'>{money(g['gross'], g['currency'])}</td>"
                     f"<td style='padding:7px 10px;border-top:1px solid {LINE};text-align:right;color:{GREY}'>{money(g['fee'], g['currency'])}</td>"
                     f"<td style='padding:7px 10px;border-top:1px solid {LINE};text-align:right;font-weight:700'>{money(g['net'], g['currency'])}</td></tr>")
        if not rows:
            rows = (f"<tr><td colspan='5' style='padding:14px 10px;border-top:1px solid {LINE};color:{GREY}'>"
                    f"Nothing moved on this account in the window.</td></tr>")

        lines = ""
        for t in acc["txns"][:40]:
            when = dt.datetime.fromtimestamp(t["created"], TZ)
            desc = t.get("description") or t.get("type")
            colour = GREEN if t["net"] > 0 else RED
            lines += (f"<tr><td style='padding:5px 10px;border-top:1px solid {LINE};color:{GREY};white-space:nowrap'>{when:%a %d %b %H:%M}</td>"
                      f"<td style='padding:5px 10px;border-top:1px solid {LINE}'>{h(desc)}</td>"
                      f"<td style='padding:5px 10px;border-top:1px solid {LINE};color:{GREY}'>{h(t['type'])}</td>"
                      f"<td style='padding:5px 10px;border-top:1px solid {LINE};text-align:right;color:{colour}'>{money(t['net'], t['currency'])}</td></tr>")
        more = ""
        if len(acc["txns"]) > 40:
            more = f"<p style='margin:6px 0 0;color:{GREY};font-size:12px'>Showing the newest 40 of {len(acc['txns'])}.</p>"

        pay = ""
        if acc["payouts"]:
            for p in acc["payouts"][:10]:
                arr = dt.datetime.fromtimestamp(p["arrival_date"], TZ)
                pay += (f"<tr><td style='padding:5px 10px;border-top:1px solid {LINE};color:{GREY};white-space:nowrap'>"
                        f"{dt.datetime.fromtimestamp(p['created'], TZ):%d %b}</td>"
                        f"<td style='padding:5px 10px;border-top:1px solid {LINE}'>{money(p['amount'], p['currency'])}</td>"
                        f"<td style='padding:5px 10px;border-top:1px solid {LINE};color:{GREY}'>{h(p['status'])}, arrives {arr:%d %b}</td></tr>")
            pay = (f"<table style='border-collapse:collapse;width:100%;margin-top:6px;font-size:13px'>{pay}</table>")
        else:
            pay = f"<p style='margin:6px 0 0;color:{GREY};font-size:13px'>No payouts to the bank in this window.</p>"

        flag_html = ""
        for level, msg in flags:
            bg = "#fef3f2" if level == "red" else "#fffaeb"
            bd = RED if level == "red" else ORANGE
            flag_html += (f"<div style='margin:10px 0 0;padding:9px 12px;background:{bg};"
                          f"border-left:3px solid {bd};font-size:13px'>{h(msg)}</div>")

        sched = (acc["schedule"] or {}).get("interval") or "unknown"
        delay = (acc["schedule"] or {}).get("delay_days")
        sched_h = f"{sched}" + (f", {delay} day delay" if delay is not None else "")

        parts.append(f"""
        <div style="background:#fff;border:1px solid {LINE};border-radius:10px;padding:16px 18px;margin:0 0 18px">
          <div style="font-size:17px;font-weight:700;color:{NAVY}">{h(label)}</div>
          <div style="font-size:12px;color:{GREY};margin:2px 0 12px">{h(blurb)} &middot; {h(acc['account_id'] or 'no account id')} &middot; payouts {h(sched_h)}</div>
          <table style="border-collapse:collapse;width:100%;font-size:13px">
            <tr>
              <td style="padding:0 10px 8px 0;color:{GREY}">Available now</td>
              <td style="padding:0 0 8px;text-align:right;font-size:19px;font-weight:700;color:{NAVY}">{money(sum(acc['available'].values()))}</td>
              <td style="padding:0 0 8px 18px;color:{GREY}">Pending</td>
              <td style="padding:0 0 8px;text-align:right;font-weight:700;color:{GREY}">{money(sum(acc['pending'].values()))}</td>
            </tr>
          </table>
          <table style="border-collapse:collapse;width:100%;font-size:13px;margin-top:8px">
            <tr style="background:{NAVY};color:#fff">
              <th style="padding:7px 10px;text-align:left">In the window</th>
              <th style="padding:7px 10px;text-align:center">No.</th>
              <th style="padding:7px 10px;text-align:right">Gross</th>
              <th style="padding:7px 10px;text-align:right">Fees</th>
              <th style="padding:7px 10px;text-align:right">Net</th>
            </tr>
            {rows}
          </table>
          {flag_html}
          <div style="margin:16px 0 4px;font-size:13px;font-weight:700;color:{NAVY}">Payouts to the bank</div>
          {pay}
          <div style="margin:16px 0 4px;font-size:13px;font-weight:700;color:{NAVY}">Every movement</div>
          <table style="border-collapse:collapse;width:100%;font-size:13px">{lines or f"<tr><td style='padding:8px 10px;color:{GREY}'>None.</td></tr>"}</table>
          {more}
        </div>""")

    return f"""<div style="font:14px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f5f7fb;padding:20px">
      <div style="max-width:760px;margin:0 auto">
        <div style="background:{NAVY};color:#fff;border-radius:10px;padding:16px 18px;margin:0 0 18px">
          <div style="font-size:19px;font-weight:700">Stripe weekly report</div>
          <div style="font-size:13px;opacity:.85;margin-top:3px">{start:%a %d %b %H:%M} to {end:%a %d %b %H:%M} (Canary time)</div>
          <div style="margin-top:12px;font-size:13px">
            Sitting in Stripe across both accounts: <strong style="font-size:17px">{money(grand_avail)}</strong> available,
            {money(grand_pending)} pending. Taken this week after fees: <strong>{money(grand_in)}</strong>.
          </div>
        </div>
        {''.join(parts)}
        <div style="color:{GREY};font-size:11px;text-align:center;padding:6px 0">
          Generated by stripe-weekly-report.py from the Stripe API. Figures are live, not cached.
        </div>
      </div>
    </div>"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7, help="Window length in days (default 7).")
    p.add_argument("--dry-run", action="store_true", help="Render and print; send nothing, publish nothing.")
    p.add_argument("--to", help="Comma-separated recipients (default: Pete).")
    p.add_argument("--no-publish", action="store_true", help="Email only; skip the CC snapshot.")
    args = p.parse_args()

    end = dt.datetime.now(TZ)
    start = end - dt.timedelta(days=args.days)
    since_ts = int(start.timestamp())

    sapi = _stripe_mod()
    accounts = [collect(sapi, slug, since_ts) for slug, _, _ in ACCOUNTS]

    html = render(accounts, start, end)
    total = sum(sum(a["available"].values()) for a in accounts if not a["error"])
    subject = f"Stripe weekly — {money(total)} in Stripe — w/e {end:%d %b %Y}"

    for a, (_, label, _b) in zip(accounts, ACCOUNTS):
        state = a["error"] or f"{len(a['txns'])} movements, available {money(sum(a['available'].values()))}"
        print(f"  {label}: {state}")

    if args.dry_run:
        out = Path("/tmp/stripe-weekly-preview.html")
        out.write_text(html)
        print(f"\n[dry-run] nothing sent. Preview written to {out}")
        print(f"[dry-run] subject: {subject}")
        return

    recipients = [x.strip() for x in args.to.split(",")] if args.to else RECIPIENTS_DEFAULT
    g = _gmail()
    res = g.send(to=", ".join(recipients), subject=subject, body=html, html=True, from_=SENDER)
    print(f"\nSent: id={res.get('id')} to {', '.join(recipients)}")

    if not args.no_publish:
        try:
            spec = importlib.util.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
            cc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cc)
            cc.publish("stripe-weekly", end.date().isoformat(), {"subject": subject, "html": html})
            print("  CC: snapshot published")
        except Exception as e:
            print(f"  CC PUBLISH FAILED: {e}")


if __name__ == "__main__":
    main()
