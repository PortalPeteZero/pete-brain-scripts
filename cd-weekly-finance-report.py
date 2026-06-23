#!/usr/bin/env python3
"""
CD Weekly Finance Report -- generates a structured turnover report for a given
calendar week (Mon-Sun), pulling live from CD's Odoo.

Read-only. Nothing is sent, nothing is written to Odoo. Pure reporting.

Output: a Markdown file at
  Businesses/canary-detect/finance/weekly-turnover-reports/turnover-week-{YYYY-MM-DD}.md
  (filename uses the Monday of the week)

Usage:
  # Default -- last completed week (most recent Sun-ending week)
  python3 Library/processes/scripts/cd-weekly-finance-report.py

  # Specific week (any date inside it -- snaps to the Monday)
  python3 Library/processes/scripts/cd-weekly-finance-report.py --week 2026-04-21

Output template mirrors the monthly report (cd-monthly-finance-report.py) so
both formats look identical -- just a different time window:

  1. Headline                -- gross / net / untaxed / invoice count vs prior week + 4-week avg
  2. Revenue split           -- core service vs Sub Let vs Goods
  3. By category (rollup)
  4. By product nested
  5. Top customers
  6. Payment state           -- of this week's invoices, as of today
  7. Outstanding (all-vintage)
  8. Reconciliation check    -- per-invoice line totals == invoice header totals

Same hardening as monthly:
  - captures every revenue-bearing line, not just product-typed ones
  - flags lines without a product set
  - validates aggregate + per-invoice reconciliation
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VAULT_ROOT = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = VAULT_ROOT / "Businesses/canary-detect/finance/weekly-turnover-reports"


def _odoo():
    spec = importlib.util.spec_from_file_location("odoo_api", str(SCRIPT_DIR / "odoo-api.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _week_bounds(monday: dt.date) -> tuple[dt.date, dt.date]:
    """Returns (start, end_exclusive) for a 7-day Mon-Sun window starting on `monday`."""
    return monday, monday + dt.timedelta(days=7)


def _shift_weeks(monday: dt.date, delta: int) -> dt.date:
    return monday + dt.timedelta(days=7 * delta)


def _search_read(odoo, model, domain, fields, **kw):
    return odoo._execute(model, "search_read", [domain], {"fields": fields, **kw})


def _fetch_invoices(odoo, start, end, move_type="out_invoice"):
    return _search_read(
        odoo, "account.move",
        [["move_type", "=", move_type], ["state", "=", "posted"],
         ["invoice_date", ">=", start.isoformat()], ["invoice_date", "<", end.isoformat()]],
        ["id", "name", "partner_id", "invoice_date", "invoice_date_due",
         "amount_total", "amount_residual", "amount_untaxed",
         "payment_state", "invoice_user_id", "team_id", "invoice_line_ids"],
        order="invoice_date desc", limit=1000,
    )


def _fetch_lines(odoo, invoices):
    line_ids = []
    for i in invoices:
        line_ids.extend(i.get("invoice_line_ids", []))
    if not line_ids:
        return []
    return _search_read(
        odoo, "account.move.line", [["id", "in", line_ids]],
        ["id", "name", "product_id", "quantity", "price_subtotal", "price_total",
         "price_unit", "display_type", "move_id"],
        limit=5000,
    )


def _fetch_outstanding(odoo, as_of: dt.date):
    return _search_read(
        odoo, "account.move",
        [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
         ["payment_state", "in", ["not_paid", "partial", "in_payment"]],
         ["invoice_date", "<=", as_of.isoformat()]],
        ["id", "name", "partner_id", "invoice_date", "invoice_date_due",
         "amount_total", "amount_residual"],
        order="invoice_date_due asc", limit=300,
    )


def build_report(monday: dt.date, odoo) -> str:
    week_start, week_end = _week_bounds(monday)
    prev_start, prev_end = _week_bounds(_shift_weeks(monday, -1))
    week_label = f"w/c {week_start.strftime('%-d %b %Y')}"
    today = dt.date.today()

    print(f"Pulling {week_label} + 5 prior weeks...", file=sys.stderr)
    inv = _fetch_invoices(odoo, week_start, week_end)
    ref = _fetch_invoices(odoo, week_start, week_end, "out_refund")
    prev_inv = _fetch_invoices(odoo, prev_start, prev_end)
    prev_ref = _fetch_invoices(odoo, prev_start, prev_end, "out_refund")

    # 4-week rolling baseline (4 weeks before the report week)
    four_week_grosses = []
    four_week_nets = []
    for i in range(1, 5):
        ws, we = _week_bounds(_shift_weeks(monday, -i))
        wk_inv = _fetch_invoices(odoo, ws, we)
        wk_ref = _fetch_invoices(odoo, ws, we, "out_refund")
        wk_gross = sum(x["amount_total"] for x in wk_inv)
        wk_refunds = sum(x["amount_total"] for x in wk_ref)
        four_week_grosses.append(wk_gross)
        four_week_nets.append(wk_gross - wk_refunds)

    lines = _fetch_lines(odoo, inv)
    revenue_lines = [l for l in lines if abs(l.get("price_subtotal", 0)) > 0.005]

    prod_ids = list(set(l["product_id"][0] for l in revenue_lines if l.get("product_id")))
    prods = odoo._execute("product.product", "search_read",
                         [[["id", "in", prod_ids]]],
                         {"fields": ["id", "name", "categ_id", "type", "active"],
                          "context": {"active_test": False}}) if prod_ids else []
    prod_to_cat = {p["id"]: (p["categ_id"][1] if p.get("categ_id") else "(none)") for p in prods}
    prod_to_type = {p["id"]: p.get("type") for p in prods}
    prod_to_active = {p["id"]: p.get("active", True) for p in prods}

    outstanding = _fetch_outstanding(odoo, today)

    # ── Aggregations ──────────────────────────────────────────────────────────
    gross = sum(i["amount_total"] for i in inv)
    untaxed = sum(i["amount_untaxed"] for i in inv)
    refunds = sum(r["amount_total"] for r in ref)
    net = gross - refunds

    prev_gross = sum(i["amount_total"] for i in prev_inv)
    prev_net = prev_gross - sum(r["amount_total"] for r in prev_ref)

    avg4_gross = sum(four_week_grosses) / 4 if four_week_grosses else 0
    avg4_net = sum(four_week_nets) / 4 if four_week_nets else 0

    # category -> product -> stats
    cat_to_products = defaultdict(lambda: defaultdict(lambda: {
        "subtotal": 0.0, "total": 0.0, "lines": 0, "qty": 0, "customers": set(),
    }))
    move_to_partner = {i["id"]: (i["partner_id"][1] if i.get("partner_id") else "?") for i in inv}
    no_product_lines = []
    for l in revenue_lines:
        if l.get("product_id"):
            pid = l["product_id"][0]
            pname = l["product_id"][1]
            if not prod_to_active.get(pid, True):
                pname = pname + " [archived]"
            cat = prod_to_cat.get(pid, "(NO CATEGORY)")
        else:
            pname = "(NO PRODUCT)"
            cat = "(NO CATEGORY)"
            no_product_lines.append(l)
        e = cat_to_products[cat][pname]
        e["subtotal"] += l.get("price_subtotal", 0)
        e["total"] += l.get("price_total", 0)
        e["lines"] += 1
        e["qty"] += l.get("quantity", 0)
        partner = move_to_partner.get(l["move_id"][0], "?")
        e["customers"].add(partner)

    by_partner = defaultdict(lambda: {"gross": 0.0, "count": 0, "untaxed": 0.0})
    for i in inv:
        n = i["partner_id"][1] if i.get("partner_id") else "?"
        by_partner[n]["gross"] += i["amount_total"]
        by_partner[n]["untaxed"] += i["amount_untaxed"]
        by_partner[n]["count"] += 1

    by_pay = defaultdict(lambda: {"count": 0, "amount": 0.0, "residual": 0.0})
    for i in inv:
        s = i.get("payment_state", "?")
        by_pay[s]["count"] += 1
        by_pay[s]["amount"] += i["amount_total"]
        by_pay[s]["residual"] += i.get("amount_residual", 0)

    def _line_pid(l): return l["product_id"][0] if l.get("product_id") else None
    sublet = sum(l["price_subtotal"] for l in revenue_lines
                 if _line_pid(l) is not None and prod_to_cat.get(_line_pid(l)) == "Sub Let")
    goods = sum(l["price_subtotal"] for l in revenue_lines
                if _line_pid(l) is not None and prod_to_type.get(_line_pid(l)) in ("consu", "goods"))
    core_service = sum(l["price_subtotal"] for l in revenue_lines
                       if _line_pid(l) is not None
                       and prod_to_type.get(_line_pid(l)) == "service"
                       and prod_to_cat.get(_line_pid(l)) != "Sub Let")
    unclassified = sum(l["price_subtotal"] for l in revenue_lines if _line_pid(l) is None)

    # Reconciliation
    invoice_lines_by_move = defaultdict(list)
    for l in revenue_lines:
        invoice_lines_by_move[l["move_id"][0]].append(l)
    recon_failures = []
    for i in inv:
        line_subtotal = sum(l.get("price_subtotal", 0) for l in invoice_lines_by_move.get(i["id"], []))
        line_total = sum(l.get("price_total", 0) for l in invoice_lines_by_move.get(i["id"], []))
        delta_untaxed = line_subtotal - i["amount_untaxed"]
        delta_gross = line_total - i["amount_total"]
        if abs(delta_untaxed) > 0.01 or abs(delta_gross) > 0.01:
            recon_failures.append({
                "invoice": i["name"], "partner": move_to_partner.get(i["id"], "?"),
                "header_untaxed": i["amount_untaxed"], "line_untaxed": line_subtotal,
                "delta_untaxed": delta_untaxed,
                "header_total": i["amount_total"], "line_total": line_total,
                "delta_total": delta_gross,
            })

    # ── Render ────────────────────────────────────────────────────────────────
    out = []
    out.append(f"# CD Weekly Finance Report — {week_label}")
    out.append("")
    out.append(f"_Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}. "
               f"Period: {week_start.strftime('%a %-d %b')} – {(week_end - dt.timedelta(days=1)).strftime('%a %-d %b %Y')} "
               f"(Mon–Sun, 7 days). Source: CD Odoo (camello-blanco-sl.odoo.com)._")
    out.append("")

    # Section 1: Headline
    out.append("## 1. Headline")
    out.append("")
    out.append(f"| Metric | This week | Last week | 4-week avg |")
    out.append("|---|---:|---:|---:|")
    out.append(f"| **Gross turnover** | **€{gross:,.2f}** | €{prev_gross:,.2f} | €{avg4_gross:,.2f} |")
    out.append(f"| Net (after credits) | €{net:,.2f} | €{prev_net:,.2f} | €{avg4_net:,.2f} |")
    out.append(f"| Untaxed | €{untaxed:,.2f} | – | – |")
    out.append(f"| Invoices issued | {len(inv)} | {len(prev_inv)} | – |")
    out.append(f"| Credit notes | {len(ref)} (€{refunds:,.2f}) | {len(prev_ref)} | – |")
    out.append("")
    if prev_gross > 0:
        out.append(f"**vs last week**: {((gross - prev_gross) / prev_gross * 100):+.1f}% gross")
    if avg4_gross > 0:
        out.append(f"**vs 4-week avg**: {((gross - avg4_gross) / avg4_gross * 100):+.1f}% gross")
    out.append("")
    out.append("**4-week baseline detail** (the 4 weeks immediately before this report's week):")
    out.append("")
    out.append("| Week starting | Gross |")
    out.append("|---|---:|")
    for i in range(4, 0, -1):
        ws = _shift_weeks(monday, -i)
        out.append(f"| Mon {ws.strftime('%-d %b')} | €{four_week_grosses[i-1]:,.2f} |")
    out.append("")

    # Section 2: Revenue split
    out.append("## 2. Revenue split")
    out.append("")
    out.append("| Type | Amount (untaxed) | Share |")
    out.append("|---|---:|---:|")
    if untaxed > 0:
        out.append(f"| **Core service** (services in proper categories) | €{core_service:,.2f} | {core_service / untaxed * 100:.1f}% |")
        out.append(f"| Sub Let (rental income) | €{sublet:,.2f} | {sublet / untaxed * 100:.1f}% |")
        out.append(f"| Goods (physical product sales) | €{goods:,.2f} | {goods / untaxed * 100:.1f}% |")
        if abs(unclassified) > 0.005:
            out.append(f"| ⚠ **Unclassified** (no product set on line) | €{unclassified:,.2f} | {unclassified / untaxed * 100:.1f}% |")
    out.append(f"| **Total** | **€{untaxed:,.2f}** | 100% |")
    out.append("")

    # Section 3: Category rollup
    out.append("## 3. By category (rollup)")
    out.append("")
    out.append("| Category | Amount | Share | # products | # lines |")
    out.append("|---|---:|---:|---:|---:|")
    total = sum(sum(p["subtotal"] for p in v.values()) for v in cat_to_products.values())
    for cat, products in sorted(cat_to_products.items(),
                                key=lambda kv: sum(p["subtotal"] for p in kv[1].values()),
                                reverse=True):
        cat_total = sum(p["subtotal"] for p in products.values())
        pct = cat_total / total * 100 if total else 0
        out.append(f"| **{cat}** | €{cat_total:,.2f} | {pct:.1f}% | {len(products)} | {sum(p['lines'] for p in products.values())} |")
    out.append(f"| **Total** | **€{total:,.2f}** | 100% | | |")
    out.append("")

    # Section 4: By product -- single grouped table, category section rows + product rows
    out.append("## 4. By product")
    out.append("")
    grand_total = sum(sum(p["subtotal"] for p in v.values()) for v in cat_to_products.values())
    out.append("| Product | Amount | Share | Lines | Avg / line | Customers |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for cat, products in sorted(cat_to_products.items(),
                                key=lambda kv: sum(p["subtotal"] for p in kv[1].values()),
                                reverse=True):
        cat_total = sum(p["subtotal"] for p in products.values())
        cat_lines = sum(p["lines"] for p in products.values())
        cat_pct = cat_total / grand_total * 100 if grand_total else 0
        # Category section row (bold)
        out.append(f"| **{cat}** | **€{cat_total:,.2f}** | **{cat_pct:.1f}%** | **{cat_lines}** | — | — |")
        # Product rows beneath
        for pname, v in sorted(products.items(), key=lambda kv: kv[1]["subtotal"], reverse=True):
            share = v["subtotal"] / cat_total * 100 if cat_total else 0
            avg = v["subtotal"] / v["lines"] if v["lines"] else 0
            out.append(f"| &nbsp;&nbsp;&nbsp;{pname} | €{v['subtotal']:,.2f} | {share:.1f}% | {v['lines']} | €{avg:,.2f} | {len(v['customers'])} |")
    out.append("")

    # Section 5: Outstanding (all-vintage)
    # (Top customers + Payment state sections deliberately removed for weekly --
    # short window means few customers + most invoices haven't had time to
    # be paid yet. Both retained on the monthly report.)
    out.append("## 5. Outstanding invoices (all vintages, residual > 0)")
    out.append("")
    total_outstanding = sum(i["amount_residual"] for i in outstanding)
    out.append(f"**Total outstanding: €{total_outstanding:,.2f} across {len(outstanding)} invoices.**")
    out.append("")
    out.append("| Customer | Invoice | Issued | Due | Residual | Status |")
    out.append("|---|---|---|---|---:|---|")
    for inv_o in outstanding[:20]:
        partner = inv_o["partner_id"][1] if inv_o.get("partner_id") else "?"
        issued = inv_o.get("invoice_date", "?")
        due = inv_o.get("invoice_date_due", "?")
        try:
            due_d = dt.date.fromisoformat(due)
            overdue_days = (today - due_d).days
            status = f"+{overdue_days}d overdue" if overdue_days > 0 else f"in {-overdue_days}d"
        except Exception:
            status = "-"
        out.append(f"| {partner} | {inv_o['name']} | {issued} | {due} | €{inv_o['amount_residual']:,.2f} | {status} |")
    if len(outstanding) > 20:
        rest = sum(i["amount_residual"] for i in outstanding[20:])
        out.append(f"| _({len(outstanding) - 20} older)_ | | | | €{rest:,.2f} | |")
    out.append("")

    # Section 6: Reconciliation
    out.append("## 6. Reconciliation check")
    out.append("")
    total_line_subtotal = sum(l.get("price_subtotal", 0) for l in revenue_lines)
    total_line_total = sum(l.get("price_total", 0) for l in revenue_lines)
    delta_aggregate_untaxed = total_line_subtotal - untaxed
    delta_aggregate_total = total_line_total - gross
    out.append("**Aggregate** (sum of all lines vs sum of all invoice headers):")
    out.append("")
    out.append("| Source | Untaxed | Gross |")
    out.append("|---|---:|---:|")
    out.append(f"| Sum of all line items in this report | €{total_line_subtotal:,.2f} | €{total_line_total:,.2f} |")
    out.append(f"| Sum of invoice header totals | €{untaxed:,.2f} | €{gross:,.2f} |")
    out.append(f"| **Delta** | **€{delta_aggregate_untaxed:,.2f}** | **€{delta_aggregate_total:,.2f}** |")
    out.append("")
    if abs(delta_aggregate_untaxed) <= 0.01 and abs(delta_aggregate_total) <= 0.01:
        out.append("✅ **Aggregate reconciliation: PASS** — line-level totals match invoice header totals exactly.")
    else:
        out.append("⚠ **Aggregate reconciliation: FAIL** — line totals don't match invoice headers.")
    out.append("")
    out.append(f"**Per-invoice check**: validated all {len(inv)} posted out_invoice records this week.")
    out.append("")
    if not recon_failures:
        out.append(f"✅ **All {len(inv)} invoices reconcile**: line subtotals = `amount_untaxed`, line totals = `amount_total` (within €0.01 rounding).")
    else:
        out.append(f"⚠ **{len(recon_failures)} invoice(s) fail reconciliation:**")
        out.append("")
        out.append("| Invoice | Customer | Header untaxed | Line untaxed | Δ untaxed | Header gross | Line gross | Δ gross |")
        out.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for r in recon_failures[:30]:
            out.append(f"| {r['invoice']} | {r['partner']} | €{r['header_untaxed']:,.2f} | €{r['line_untaxed']:,.2f} | €{r['delta_untaxed']:,.2f} | €{r['header_total']:,.2f} | €{r['line_total']:,.2f} | €{r['delta_total']:,.2f} |")
        out.append("")
    if no_product_lines:
        out.append("### Lines with no product set")
        out.append("")
        out.append(f"⚠ Found {len(no_product_lines)} revenue-bearing line(s) with no product set.")
        out.append("")
        out.append("| Invoice | Description | Amount |")
        out.append("|---|---|---:|")
        for l in no_product_lines[:20]:
            move_name = next((i["name"] for i in inv if i["id"] == l["move_id"][0]), "?")
            desc = (l.get("name") or "")[:80].replace("\n", " | ")
            out.append(f"| {move_name} | {desc} | €{l.get('price_subtotal', 0):,.2f} |")
        out.append("")

    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description="Generate CD weekly turnover report from Odoo.")
    p.add_argument("--week", help="Any date inside the week (YYYY-MM-DD), snaps to that week's Monday. Default: last completed Mon-Sun week.")
    args = p.parse_args()

    odoo = _odoo()
    today = dt.date.today()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.week:
        d = dt.date.fromisoformat(args.week)
        monday = d - dt.timedelta(days=d.weekday())
    else:
        # Last completed week = previous Monday
        # If today is Mon -> last completed week starts 7 days ago
        # If today is Sun -> last completed week starts 6 days ago
        days_since_monday = today.weekday()  # 0=Mon, 6=Sun
        last_sun = today - dt.timedelta(days=days_since_monday + 1)  # last Sunday
        monday = last_sun - dt.timedelta(days=6)

    md = build_report(monday, odoo)
    fname = f"turnover-week-{monday.isoformat()}.md"
    path = OUTPUT_DIR / fname
    path.write_text(md)
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
