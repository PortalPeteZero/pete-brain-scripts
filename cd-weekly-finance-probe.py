#!/usr/bin/env python3
"""
CD Weekly Finance Report -- PROBE script.

Run this LOCALLY from your Mac Terminal (sandbox can't reach Odoo Online):

  cd "~/Second Brain"
  python3 Library/processes/scripts/cd-weekly-finance-probe.py

It will:
  1. Authenticate to CD's Odoo (camello-blanco-sl.odoo.com)
  2. Probe what fields exist on account.move (invoices) and sale.order
  3. Pull last 7 days + previous 7 days + previous 4-week-rolling data
  4. Compute turnover, breakdown by partner, invoice/payment status
  5. Render a sample weekly report (HTML + plain text)
  6. Save output to Businesses/canary-detect/finance/probes/weekly-finance-sample-{date}.{md,html}
  7. Print a summary to stdout

NOTHING is sent. NOTHING is written to Odoo. Read-only probe + sample render.

Once we've reviewed the sample and tuned the shape, this becomes
cd-weekly-finance-report.py with a sender step + scheduled task.

Optional flags:
  --week-of YYYY-MM-DD    Treat that Monday as "this week" (default: this week)
  --no-render             Skip rendering, just dump field probes
  --raw-dump              Save raw JSON of pulled data to _archive/
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR.parent / "odoo-api-configuration.md"
VAULT_ROOT = SCRIPT_DIR.parent.parent.parent  # .../Second Brain
SAMPLE_DIR = VAULT_ROOT / "Businesses/canary-detect/finance/probes"
ARCHIVE_DIR = VAULT_ROOT / "Businesses/canary-detect/finance/probes/_archive"
TZ = ZoneInfo("Atlantic/Canary")


# ── Config + Odoo client (mirror of cd-team-briefing.py pattern) ──────────────


def load_odoo_config() -> dict:
    text = CONFIG_FILE.read_text()

    def grab(label: str) -> str | None:
        m = re.search(rf"\*\*{re.escape(label)}\*\*\s*\|\s*`([^`]+)`", text)
        return m.group(1) if m else None

    cfg = {
        "url": grab("Instance URL"),
        "db": grab("Database name"),
        "login": grab("Login (API user)"),
        "api_key": grab("API key"),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        sys.exit(f"odoo config missing fields: {missing}")
    return cfg


class Odoo:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._uid: int | None = None

    def _rpc(self, service: str, method: str, args: list) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": 1,
        }
        req = urllib.request.Request(
            f"{self.cfg['url']}/jsonrpc",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"odoo http {e.code}: {e.read().decode('utf-8', 'ignore')[:500]}") from e
        if "error" in body:
            err = body["error"]
            msg = err.get("data", {}).get("message") or err.get("message", "unknown")
            raise RuntimeError(f"odoo error: {msg}")
        return body.get("result")

    def _auth(self) -> int:
        if self._uid:
            return self._uid
        uid = self._rpc("common", "authenticate", [self.cfg["db"], self.cfg["login"], self.cfg["api_key"], {}])
        if not uid:
            raise RuntimeError("odoo auth failed -- check login + api key in odoo-api-configuration.md")
        self._uid = uid
        return uid

    def execute(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        uid = self._auth()
        return self._rpc(
            "object", "execute_kw",
            [self.cfg["db"], uid, self.cfg["api_key"], model, method, args, kwargs or {}],
        )

    def search_read(self, model: str, domain: list, fields: list, **kwargs) -> list[dict]:
        return self.execute(model, "search_read", [domain], {"fields": fields, **kwargs})

    def fields_get(self, model: str) -> dict:
        return self.execute(model, "fields_get", [], {"attributes": ["string", "type", "required", "readonly"]})


# ── Probes ────────────────────────────────────────────────────────────────────


def probe_field_shapes(odoo: Odoo) -> dict:
    """Return field dictionaries for the 3 main finance models."""
    out = {}
    for model in ["account.move", "sale.order", "account.payment"]:
        try:
            fields = odoo.fields_get(model)
            # Just the top-level interesting ones
            interesting = {
                k: v for k, v in fields.items()
                if k in (
                    # account.move
                    "name", "move_type", "state", "payment_state", "invoice_date",
                    "invoice_date_due", "amount_total", "amount_residual",
                    "amount_untaxed", "amount_tax", "currency_id", "partner_id",
                    "invoice_user_id", "team_id", "team_section_id", "company_id",
                    "ref", "narration", "invoice_origin",
                    # sale.order
                    "date_order", "amount_total", "state", "user_id",
                    # account.payment
                    "payment_type", "amount", "payment_date", "date",
                )
            }
            out[model] = interesting
        except Exception as e:
            out[model] = {"_error": str(e)}
    return out


def fetch_invoices(odoo: Odoo, start: dt.date, end: dt.date) -> list[dict]:
    """Pull customer invoices (out_invoice) within [start, end) by invoice_date.
    Excludes drafts; counts both posted (issued) and paid statuses."""
    domain = [
        ["move_type", "=", "out_invoice"],
        ["state", "=", "posted"],
        ["invoice_date", ">=", start.isoformat()],
        ["invoice_date", "<", end.isoformat()],
    ]
    fields = [
        "id", "name", "partner_id", "invoice_date", "invoice_date_due",
        "amount_total", "amount_residual", "amount_untaxed", "currency_id",
        "payment_state", "state", "invoice_origin",
    ]
    return odoo.search_read("account.move", domain, fields, order="invoice_date desc", limit=500)


def fetch_credit_notes(odoo: Odoo, start: dt.date, end: dt.date) -> list[dict]:
    """Pull customer credit notes (out_refund) for offsets."""
    domain = [
        ["move_type", "=", "out_refund"],
        ["state", "=", "posted"],
        ["invoice_date", ">=", start.isoformat()],
        ["invoice_date", "<", end.isoformat()],
    ]
    fields = [
        "id", "name", "partner_id", "invoice_date",
        "amount_total", "currency_id", "state",
    ]
    return odoo.search_read("account.move", domain, fields, order="invoice_date desc", limit=200)


def fetch_outstanding_invoices(odoo: Odoo, as_of: dt.date) -> list[dict]:
    """Open invoices (any vintage) with residual > 0."""
    domain = [
        ["move_type", "=", "out_invoice"],
        ["state", "=", "posted"],
        ["payment_state", "in", ["not_paid", "partial", "in_payment"]],
        ["invoice_date", "<=", as_of.isoformat()],
    ]
    fields = [
        "id", "name", "partner_id", "invoice_date", "invoice_date_due",
        "amount_total", "amount_residual", "currency_id", "payment_state",
    ]
    return odoo.search_read("account.move", domain, fields, order="invoice_date_due asc", limit=500)


def fetch_sales_orders(odoo: Odoo, start: dt.date, end: dt.date) -> list[dict]:
    """Confirmed sales orders within [start, end)."""
    start_dt = f"{start.isoformat()} 00:00:00"
    end_dt = f"{end.isoformat()} 00:00:00"
    domain = [
        ["state", "in", ["sale", "done"]],
        ["date_order", ">=", start_dt],
        ["date_order", "<", end_dt],
    ]
    fields = ["id", "name", "partner_id", "date_order", "amount_total", "state", "user_id"]
    return odoo.search_read("sale.order", domain, fields, order="date_order desc", limit=300)


# ── Aggregation ───────────────────────────────────────────────────────────────


def summarise(invoices: list[dict], credit_notes: list[dict]) -> dict:
    gross = sum(i["amount_total"] for i in invoices)
    refunds = sum(c["amount_total"] for c in credit_notes)
    net = gross - refunds
    by_partner = defaultdict(lambda: {"gross": 0.0, "count": 0})
    for i in invoices:
        pid = i["partner_id"][0] if i.get("partner_id") else 0
        pname = i["partner_id"][1] if i.get("partner_id") else "(unknown)"
        by_partner[(pid, pname)]["gross"] += i["amount_total"]
        by_partner[(pid, pname)]["count"] += 1
    sorted_partners = sorted(
        [(name, v["gross"], v["count"]) for (pid, name), v in by_partner.items()],
        key=lambda t: t[1], reverse=True,
    )
    by_payment = defaultdict(lambda: {"count": 0, "amount": 0.0, "residual": 0.0})
    for i in invoices:
        s = i.get("payment_state", "?")
        by_payment[s]["count"] += 1
        by_payment[s]["amount"] += i["amount_total"]
        by_payment[s]["residual"] += i.get("amount_residual", 0.0)
    return {
        "invoice_count": len(invoices),
        "credit_note_count": len(credit_notes),
        "gross": gross,
        "refunds": refunds,
        "net": net,
        "by_partner": sorted_partners,
        "by_payment_state": dict(by_payment),
    }


def render_markdown(this_week: dict, last_week: dict, four_week: list[float],
                    week_start: dt.date, outstanding: list[dict],
                    sales_orders: list[dict], invoices_this_week: list[dict]) -> str:
    week_end = week_start + dt.timedelta(days=6)

    def pct(curr, prev):
        if prev == 0:
            return "n/a"
        d = (curr - prev) / prev * 100
        sign = "+" if d >= 0 else ""
        return f"{sign}{d:.1f}%"

    avg_4w = sum(four_week) / 4 if four_week else 0
    out = []
    out.append(f"# CD Weekly Finance Report -- w/c {week_start:%-d %b %Y}")
    out.append("")
    out.append(f"_Sample probe run, {dt.datetime.now(TZ):%Y-%m-%d %H:%M %Z}._")
    out.append("")
    out.append(f"## Headline ({week_start:%-d %b}–{week_end:%-d %b})")
    out.append("")
    out.append("| Metric | This week | Last week | 4-week avg |")
    out.append("|---|---:|---:|---:|")
    out.append(f"| Gross turnover | €{this_week['gross']:,.2f} | €{last_week['gross']:,.2f} ({pct(this_week['gross'], last_week['gross'])}) | €{avg_4w:,.2f} ({pct(this_week['gross'], avg_4w)}) |")
    out.append(f"| Net (after credits) | €{this_week['net']:,.2f} | €{last_week['net']:,.2f} | -- |")
    out.append(f"| Invoices issued | {this_week['invoice_count']} | {last_week['invoice_count']} | -- |")
    out.append(f"| Credit notes | {this_week['credit_note_count']} | {last_week['credit_note_count']} | -- |")
    out.append("")
    out.append("## Breakdown by customer (this week)")
    out.append("")
    if this_week["by_partner"]:
        out.append("| Customer | Amount | Invoices |")
        out.append("|---|---:|---:|")
        for name, gross, count in this_week["by_partner"][:20]:
            out.append(f"| {name} | €{gross:,.2f} | {count} |")
    else:
        out.append("_No invoices issued this week._")
    out.append("")
    out.append("## Payment state of this week's invoices")
    out.append("")
    out.append("| State | Count | Total | Residual outstanding |")
    out.append("|---|---:|---:|---:|")
    for state, v in this_week["by_payment_state"].items():
        out.append(f"| {state} | {v['count']} | €{v['amount']:,.2f} | €{v['residual']:,.2f} |")
    out.append("")
    out.append("## Outstanding invoices (all vintages, not paid)")
    out.append("")
    if outstanding:
        out.append("| Customer | Invoice | Issued | Due | Residual |")
        out.append("|---|---|---|---|---:|")
        today = dt.date.today()
        for inv in outstanding[:30]:
            partner = inv["partner_id"][1] if inv.get("partner_id") else "?"
            issued = inv.get("invoice_date", "?")
            due = inv.get("invoice_date_due", "?")
            try:
                due_d = dt.date.fromisoformat(due)
                overdue = (today - due_d).days
                due_marker = f" ({'+'+str(overdue)+'d overdue' if overdue > 0 else 'in '+str(-overdue)+'d'})" if due else ""
            except Exception:
                due_marker = ""
            out.append(f"| {partner} | {inv['name']} | {issued} | {due}{due_marker} | €{inv['amount_residual']:,.2f} |")
        total_outstanding = sum(i["amount_residual"] for i in outstanding)
        out.append("")
        out.append(f"**Total outstanding: €{total_outstanding:,.2f} across {len(outstanding)} invoices.**")
    else:
        out.append("_No outstanding invoices._")
    out.append("")
    out.append("## Sales orders confirmed this week")
    out.append("")
    if sales_orders:
        out.append(f"{len(sales_orders)} confirmed sales order(s), total €{sum(o['amount_total'] for o in sales_orders):,.2f}")
        out.append("")
        out.append("| Customer | SO | Date | Amount | State |")
        out.append("|---|---|---|---:|---|")
        for o in sales_orders[:20]:
            partner = o["partner_id"][1] if o.get("partner_id") else "?"
            out.append(f"| {partner} | {o['name']} | {o['date_order']} | €{o['amount_total']:,.2f} | {o['state']} |")
    else:
        out.append("_No sales orders confirmed this week._")
    out.append("")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--week-of", help="Monday of the week to report on (YYYY-MM-DD). Default: current week's Monday.")
    p.add_argument("--no-render", action="store_true", help="Skip rendering, just dump field probes.")
    p.add_argument("--raw-dump", action="store_true", help="Also save raw JSON of pulled data to _archive/.")
    args = p.parse_args()

    today = dt.datetime.now(TZ).date()
    if args.week_of:
        week_start = dt.date.fromisoformat(args.week_of)
        # snap to Monday
        week_start = week_start - dt.timedelta(days=week_start.weekday())
    else:
        week_start = today - dt.timedelta(days=today.weekday())
    week_end = week_start + dt.timedelta(days=7)
    last_start = week_start - dt.timedelta(days=7)
    last_end = week_start
    four_week_start = week_start - dt.timedelta(days=28)

    print(f"Week of   : {week_start} -- {week_end - dt.timedelta(days=1)}")
    print(f"Last week : {last_start} -- {last_end - dt.timedelta(days=1)}")
    print(f"Rolling 4w: from {four_week_start}")
    print()

    cfg = load_odoo_config()
    odoo = Odoo(cfg)

    # Auth check
    print("Authenticating to Odoo... ", end="", flush=True)
    uid = odoo._auth()
    print(f"OK (uid={uid})")
    print()

    # Field probes
    print("=== FIELD SHAPES ===")
    shapes = probe_field_shapes(odoo)
    for model, fields in shapes.items():
        print(f"\n{model}:")
        if "_error" in fields:
            print(f"  ERROR: {fields['_error']}")
            continue
        for k, v in sorted(fields.items()):
            t = v.get("type", "?") if isinstance(v, dict) else "?"
            label = v.get("string", "?") if isinstance(v, dict) else "?"
            print(f"  {k:30s}  {t:15s}  {label}")
    print()

    if args.no_render:
        return

    # Pulls
    print("=== DATA PULLS ===")
    print(f"Pulling this-week invoices ({week_start} → {week_end})... ", end="", flush=True)
    inv_this = fetch_invoices(odoo, week_start, week_end)
    print(f"{len(inv_this)} invoices")

    print(f"Pulling last-week invoices ({last_start} → {last_end})... ", end="", flush=True)
    inv_last = fetch_invoices(odoo, last_start, last_end)
    print(f"{len(inv_last)} invoices")

    print(f"Pulling credit notes (this week)... ", end="", flush=True)
    cn_this = fetch_credit_notes(odoo, week_start, week_end)
    print(f"{len(cn_this)} credit notes")

    print(f"Pulling credit notes (last week)... ", end="", flush=True)
    cn_last = fetch_credit_notes(odoo, last_start, last_end)
    print(f"{len(cn_last)} credit notes")

    print(f"Pulling outstanding (open) invoices as of today... ", end="", flush=True)
    outstanding = fetch_outstanding_invoices(odoo, today)
    print(f"{len(outstanding)} invoices")

    print(f"Pulling sales orders confirmed this week... ", end="", flush=True)
    so_this = fetch_sales_orders(odoo, week_start, week_end)
    print(f"{len(so_this)} sales orders")

    # 4-week rolling
    four_week_totals = []
    for i in range(4):
        wstart = week_start - dt.timedelta(days=7 * (i + 1))
        wend = wstart + dt.timedelta(days=7)
        wk_inv = fetch_invoices(odoo, wstart, wend)
        wk_cn = fetch_credit_notes(odoo, wstart, wend)
        wk_gross = sum(x["amount_total"] for x in wk_inv) - sum(x["amount_total"] for x in wk_cn)
        four_week_totals.append(wk_gross)
        print(f"  Week {wstart} -- {wend - dt.timedelta(days=1)}: €{wk_gross:,.2f}")
    print()

    this_summary = summarise(inv_this, cn_this)
    last_summary = summarise(inv_last, cn_last)

    # Render
    md = render_markdown(this_summary, last_summary, four_week_totals, week_start, outstanding, so_this, inv_this)

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    md_path = SAMPLE_DIR / f"weekly-finance-sample-{week_start}.md"
    md_path.write_text(md)

    print(f"=== SAMPLE REPORT ===")
    print(f"Saved markdown sample: {md_path}")
    print()

    if args.raw_dump:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        dump = {
            "week_start": week_start.isoformat(),
            "this_week_invoices": inv_this,
            "last_week_invoices": inv_last,
            "this_week_credit_notes": cn_this,
            "last_week_credit_notes": cn_last,
            "outstanding": outstanding,
            "sales_orders_this_week": so_this,
            "four_week_totals": four_week_totals,
            "field_shapes": shapes,
        }
        dump_path = ARCHIVE_DIR / f"weekly-finance-raw-{week_start}.json"
        dump_path.write_text(json.dumps(dump, indent=2, default=str))
        print(f"Saved raw dump: {dump_path}")
        print()

    print("Headline numbers:")
    print(f"  This week gross: €{this_summary['gross']:,.2f} (net €{this_summary['net']:,.2f}) from {this_summary['invoice_count']} invoices")
    print(f"  Last week gross: €{last_summary['gross']:,.2f} from {last_summary['invoice_count']} invoices")
    print(f"  4-week avg     : €{sum(four_week_totals)/4 if four_week_totals else 0:,.2f}")
    print(f"  Outstanding    : €{sum(i['amount_residual'] for i in outstanding):,.2f} across {len(outstanding)} invoices")
    print()
    print(f"Open the sample report:")
    print(f"  open '{md_path}'")


if __name__ == "__main__":
    main()
