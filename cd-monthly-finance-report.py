#!/usr/bin/env python3
"""
CD Monthly Finance Report -- generates a structured turnover report for a given
calendar month, pulling live from CD's Odoo (camello-blanco-sl.odoo.com).

Read-only. Nothing is sent, nothing is written to Odoo. Pure reporting.

Output: a Markdown file at
  Businesses/canary-detect/finance/monthly-turnover-reports/turnover-{YYYY}-{MM}.md

Usage:
  # Default -- last completed month (today's month - 1)
  python3 Library/processes/scripts/cd-monthly-finance-report.py

  # Specific month
  python3 Library/processes/scripts/cd-monthly-finance-report.py --month 2026-03

  # A whole year (writes 12 separate files)
  python3 Library/processes/scripts/cd-monthly-finance-report.py --year 2026

The output template has 7 sections (locked-in 2026-04-30 with Pete):

  1. Headline                -- gross / net / untaxed / invoice count vs prior 2 months
  2. Revenue split           -- core service vs Sub Let vs Goods (high-level)
  3. By category (rollup)    -- 6-row breakdown of each Odoo product.category
  4. By product nested       -- per-category, every product detail
  5. Top customers           -- 15 by amount + tail
  6. Payment state           -- of this month's invoices, as of today
  7. Outstanding (all-vintage) -- every overdue invoice

Built 2026-04-30 after extensive cleanup of CD's product/category structure.
Locked-in template. To extend, add new sections at the bottom of build_report().
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VAULT_ROOT = SCRIPT_DIR.parent.parent.parent  # .../Second Brain
OUTPUT_DIR = VAULT_ROOT / "Businesses/canary-detect/finance/monthly-turnover-reports"


def _odoo():
    """Load the odoo-api.py helper as a module (file has a hyphen, normal import won't work)."""
    spec = importlib.util.spec_from_file_location("odoo_api", str(SCRIPT_DIR / "odoo-api.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    start = dt.date(year, month, 1)
    end = dt.date(year + (1 if month == 12 else 0), (month % 12) + 1, 1)
    return start, end


def _shift(year: int, month: int, delta_months: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta_months
    return total // 12, (total % 12) + 1


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


def build_report(year: int, month: int, odoo) -> str:
    month_start, month_end = _month_bounds(year, month)
    prev_y, prev_m = _shift(year, month, -1)
    pp_y, pp_m = _shift(year, month, -2)
    prev_start, prev_end = _month_bounds(prev_y, prev_m)
    pp_start, pp_end = _month_bounds(pp_y, pp_m)
    month_label = month_start.strftime("%B %Y")
    today = dt.date.today()

    print(f"Pulling {month_label} + 2 prior months...", file=sys.stderr)
    inv = _fetch_invoices(odoo, month_start, month_end)
    ref = _fetch_invoices(odoo, month_start, month_end, "out_refund")
    prev_inv = _fetch_invoices(odoo, prev_start, prev_end)
    prev_ref = _fetch_invoices(odoo, prev_start, prev_end, "out_refund")
    pp_inv = _fetch_invoices(odoo, pp_start, pp_end)
    pp_ref = _fetch_invoices(odoo, pp_start, pp_end, "out_refund")

    lines = _fetch_lines(odoo, inv)

    # Capture EVERY revenue-bearing line (any line with non-zero price_subtotal),
    # regardless of display_type or whether a product is set. This is the strict
    # rule: if it contributes to the invoice total, it must appear in the report.
    revenue_lines = [l for l in lines if abs(l.get("price_subtotal", 0)) > 0.005]

    # All product IDs that appear (excluding None for product-less lines)
    prod_ids = list(set(l["product_id"][0] for l in revenue_lines if l.get("product_id")))
    # Search with active_test=False so archived products still resolve names
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
    pp_gross = sum(i["amount_total"] for i in pp_inv)
    pp_net = pp_gross - sum(r["amount_total"] for r in pp_ref)

    # category -> product -> stats. Lines with no product land in
    # category="(NO CATEGORY)", product="(NO PRODUCT)" so they're visible, not silently dropped.
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

    # Service vs non-service (untaxed, line-level)
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

    # ── Reconciliation checks ──────────────────────────────────────────────────
    # 1. Per-invoice: sum of line subtotals should equal invoice.amount_untaxed
    #    sum of line price_total should equal invoice.amount_total
    # 2. Aggregate: total revenue_lines subtotal should equal total invoice untaxed
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
                "invoice": i["name"],
                "partner": i["partner_id"][1] if i.get("partner_id") else "?",
                "header_untaxed": i["amount_untaxed"],
                "line_untaxed": line_subtotal,
                "delta_untaxed": delta_untaxed,
                "header_total": i["amount_total"],
                "line_total": line_total,
                "delta_total": delta_gross,
            })

    # ── Render ────────────────────────────────────────────────────────────────

    out = []
    out.append(f"# CD {month_label} Turnover Report")
    out.append("")
    out.append(f"_Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}. "
               f"Period: {month_start.strftime('%-d %B')}–{(month_end - dt.timedelta(days=1)).strftime('%-d %B %Y')} "
               f"inclusive. Source: CD Odoo (camello-blanco-sl.odoo.com)._")
    out.append("")

    # Section 1
    out.append("## 1. Headline")
    out.append("")
    out.append(f"| Metric | {month_label} | {prev_start.strftime('%B')} | {pp_start.strftime('%B')} |")
    out.append("|---|---:|---:|---:|")
    out.append(f"| **Gross turnover** | **€{gross:,.2f}** | €{prev_gross:,.2f} | €{pp_gross:,.2f} |")
    out.append(f"| Net (after credits) | €{net:,.2f} | €{prev_net:,.2f} | €{pp_net:,.2f} |")
    out.append(f"| Untaxed | €{untaxed:,.2f} | – | – |")
    out.append(f"| Invoices issued | {len(inv)} | {len(prev_inv)} | {len(pp_inv)} |")
    out.append(f"| Credit notes | {len(ref)} (€{refunds:,.2f}) | {len(prev_ref)} | {len(pp_ref)} |")
    out.append("")
    if prev_gross > 0:
        out.append(f"**vs {prev_start.strftime('%B')}**: {((gross - prev_gross) / prev_gross * 100):+.1f}% gross")
    if pp_gross > 0:
        out.append(f"**vs {pp_start.strftime('%B')}**: {((gross - pp_gross) / pp_gross * 100):+.1f}% gross")
    out.append("")

    # Section 2
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

    # Section 3
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

    # Section 4 -- single grouped table, category section rows + product rows
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

    # Section 5
    out.append("## 5. Top customers (by amount)")
    out.append("")
    out.append("| # | Customer | Amount (gross) | Invoices |")
    out.append("|---:|---|---:|---:|")
    sorted_partners = sorted(by_partner.items(), key=lambda kv: kv[1]["gross"], reverse=True)
    for idx, (n, v) in enumerate(sorted_partners[:15], 1):
        out.append(f"| {idx} | {n} | €{v['gross']:,.2f} | {v['count']} |")
    if len(sorted_partners) > 15:
        rest_gross = sum(v["gross"] for _, v in sorted_partners[15:])
        rest_count = sum(v["count"] for _, v in sorted_partners[15:])
        out.append(f"| | _({len(sorted_partners) - 15} more)_ | €{rest_gross:,.2f} | {rest_count} |")
    out.append("")
    out.append(f"**Total customers invoiced this month: {len(sorted_partners)}**")
    out.append("")

    # Section 6
    out.append("## 6. Payment state of this month's invoices (as of today)")
    out.append("")
    out.append("| State | Count | Amount | Residual outstanding |")
    out.append("|---|---:|---:|---:|")
    for state, v in sorted(by_pay.items()):
        out.append(f"| {state} | {v['count']} | €{v['amount']:,.2f} | €{v['residual']:,.2f} |")
    out.append("")

    # Section 7
    out.append("## 7. Outstanding invoices (all vintages, residual > 0)")
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

    # Section 8 -- Reconciliation check
    out.append("## 8. Reconciliation check")
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
        out.append("⚠ **Aggregate reconciliation: FAIL** — line totals don't match invoice headers. Investigate per-invoice breakdown below.")
    out.append("")
    out.append(f"**Per-invoice check**: validated all {len(inv)} posted out_invoice records this month.")
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
        out.append("These need investigating — the report's line-level breakdown doesn't tell the full revenue story for these invoices.")
    out.append("")

    if no_product_lines:
        out.append("### Lines with no product set")
        out.append("")
        out.append(f"⚠ Found {len(no_product_lines)} revenue-bearing line(s) with no product set. These are visible in Section 4 under 'NO CATEGORY' / '(NO PRODUCT)'.")
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
    p = argparse.ArgumentParser(description="Generate CD monthly turnover report from Odoo.")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--month", help="Target month YYYY-MM (default: last completed month).")
    grp.add_argument("--year", type=int, help="Generate all 12 months for the given year.")
    args = p.parse_args()

    odoo = _odoo()

    today = dt.date.today()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.year:
        max_month = 12 if args.year < today.year else today.month
        for m in range(1, max_month + 1):
            md = build_report(args.year, m, odoo)
            fname = f"turnover-{args.year:04d}-{m:02d}.md"
            path = OUTPUT_DIR / fname
            path.write_text(md)
            print(f"Saved {path}")
    else:
        if args.month:
            year, month = map(int, args.month.split("-"))
        else:
            # last completed month
            if today.month == 1:
                year, month = today.year - 1, 12
            else:
                year, month = today.year, today.month - 1

        md = build_report(year, month, odoo)
        fname = f"turnover-{year:04d}-{month:02d}.md"
        path = OUTPUT_DIR / fname
        path.write_text(md)
        print(f"Saved {path}")


if __name__ == "__main__":
    main()
