#!/usr/bin/env python3
"""
CD Monthly Finance Email -- generates the monthly report and emails it to the team.

Mirror of cd-weekly-finance-email.py, but for a calendar-month window. Keeps the
Top Customers + Payment State sections that the weekly drops (a month's window
gives both meaningful signal).

Usage:
  python3 cd-monthly-finance-email.py                  # last completed month, live recipients
  python3 cd-monthly-finance-email.py --preview        # send to Pete only (test)
  python3 cd-monthly-finance-email.py --dry-run        # render, no send
  python3 cd-monthly-finance-email.py --month 2026-03  # specific month

NOT scheduled (yet). Manual run when needed; if Pete wants this scheduled at
month-end he can ask separately.
"""
from __future__ import annotations

import argparse
import os
import datetime as dt
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
VAULT_ROOT = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = VAULT_ROOT / "Businesses/canary-detect/finance/monthly-turnover-reports"
TZ = ZoneInfo("Atlantic/Canary")

RECIPIENTS_LIVE = [
    "pete.ashcroft@sygma-solutions.com",
    "dave.poxon@canary-detect.com",
    "nicola.brown@canary-detect.com",
]
RECIPIENTS_PREVIEW = ["pete.ashcroft@sygma-solutions.com"]
SENDER = "pete@canary-detect.com"

# Brand colours
NAVY = "#1f2c47"
ORANGE = "#f7951d"
BG_ALT = "#F8FAFC"
BORDER = "#E2E8F0"
MUTED = "#64748B"
TEXT = "#1E293B"
SUCCESS = "#22c55e"
WARN = "#dc3545"


def _odoo():
    spec = importlib.util.spec_from_file_location("odoo_api", str(SCRIPT_DIR / "odoo-api.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gmail():
    spec = importlib.util.spec_from_file_location("gmail_api", str(SCRIPT_DIR / "gmail-api.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GmailAPI()


def _monthly_report():
    spec = importlib.util.spec_from_file_location(
        "cd_monthly_finance_report",
        str(SCRIPT_DIR / "cd-monthly-finance-report.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _search_read(odoo, model, domain, fields, **kw):
    return odoo._execute(model, "search_read", [domain], {"fields": fields, **kw})


def _month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    start = dt.date(year, month, 1)
    end = dt.date(year + (1 if month == 12 else 0), (month % 12) + 1, 1)
    return start, end


def _shift(year: int, month: int, delta_months: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta_months
    return total // 12, (total % 12) + 1


def fetch_month_data(odoo, year: int, month: int) -> dict:
    month_start, month_end = _month_bounds(year, month)
    prev_y, prev_m = _shift(year, month, -1)
    pp_y, pp_m = _shift(year, month, -2)
    prev_start, prev_end = _month_bounds(prev_y, prev_m)
    pp_start, pp_end = _month_bounds(pp_y, pp_m)

    inv = _search_read(odoo, "account.move",
        [["move_type","=","out_invoice"], ["state","=","posted"],
         ["invoice_date",">=",month_start.isoformat()], ["invoice_date","<",month_end.isoformat()]],
        ["id","name","partner_id","invoice_date","invoice_date_due","amount_total",
         "amount_residual","amount_untaxed","payment_state","invoice_line_ids"],
        order="invoice_date desc", limit=1000)
    ref = _search_read(odoo, "account.move",
        [["move_type","=","out_refund"], ["state","=","posted"],
         ["invoice_date",">=",month_start.isoformat()], ["invoice_date","<",month_end.isoformat()]],
        ["id","name","amount_total"], limit=200)
    prev_inv = _search_read(odoo, "account.move",
        [["move_type","=","out_invoice"], ["state","=","posted"],
         ["invoice_date",">=",prev_start.isoformat()], ["invoice_date","<",prev_end.isoformat()]],
        ["id","amount_total","amount_untaxed"], limit=1000)
    prev_ref = _search_read(odoo, "account.move",
        [["move_type","=","out_refund"], ["state","=","posted"],
         ["invoice_date",">=",prev_start.isoformat()], ["invoice_date","<",prev_end.isoformat()]],
        ["amount_total"], limit=200)
    pp_inv = _search_read(odoo, "account.move",
        [["move_type","=","out_invoice"], ["state","=","posted"],
         ["invoice_date",">=",pp_start.isoformat()], ["invoice_date","<",pp_end.isoformat()]],
        ["id","amount_total","amount_untaxed"], limit=1000)
    pp_ref = _search_read(odoo, "account.move",
        [["move_type","=","out_refund"], ["state","=","posted"],
         ["invoice_date",">=",pp_start.isoformat()], ["invoice_date","<",pp_end.isoformat()]],
        ["amount_total"], limit=200)

    line_ids = []
    for i in inv:
        line_ids.extend(i.get("invoice_line_ids", []))
    lines = _search_read(odoo, "account.move.line",
        [["id","in",line_ids]],
        ["id","name","product_id","quantity","price_subtotal","price_total","display_type","move_id"],
        limit=5000) if line_ids else []
    revenue_lines = [l for l in lines if abs(l.get("price_subtotal", 0)) > 0.005]

    prod_ids = list(set(l["product_id"][0] for l in revenue_lines if l.get("product_id")))
    prods = odoo._execute("product.product", "search_read",
                         [[["id","in",prod_ids]]],
                         {"fields": ["id","name","categ_id","type","active"],
                          "context": {"active_test": False}}) if prod_ids else []
    prod_to_cat = {p["id"]: (p["categ_id"][1] if p.get("categ_id") else "(none)") for p in prods}
    prod_to_type = {p["id"]: p.get("type") for p in prods}
    prod_to_active = {p["id"]: p.get("active", True) for p in prods}

    gross = sum(i["amount_total"] for i in inv)
    untaxed = sum(i["amount_untaxed"] for i in inv)
    refunds = sum(r["amount_total"] for r in ref)
    net = gross - refunds
    prev_gross = sum(i["amount_total"] for i in prev_inv)
    prev_net = prev_gross - sum(r["amount_total"] for r in prev_ref)
    pp_gross = sum(i["amount_total"] for i in pp_inv)
    pp_net = pp_gross - sum(r["amount_total"] for r in pp_ref)

    cat_to_products = defaultdict(lambda: defaultdict(lambda: {
        "subtotal": 0.0, "lines": 0, "customers": set()}))
    move_to_partner = {i["id"]: (i["partner_id"][1] if i.get("partner_id") else "?") for i in inv}
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
        e = cat_to_products[cat][pname]
        e["subtotal"] += l.get("price_subtotal", 0)
        e["lines"] += 1
        e["customers"].add(move_to_partner.get(l["move_id"][0], "?"))

    by_partner = defaultdict(lambda: {"gross": 0.0, "count": 0})
    for i in inv:
        n = i["partner_id"][1] if i.get("partner_id") else "?"
        by_partner[n]["gross"] += i["amount_total"]
        by_partner[n]["count"] += 1

    by_pay = defaultdict(lambda: {"count": 0, "amount": 0.0, "residual": 0.0})
    for i in inv:
        s = i.get("payment_state", "?")
        by_pay[s]["count"] += 1
        by_pay[s]["amount"] += i["amount_total"]
        by_pay[s]["residual"] += i.get("amount_residual", 0)

    def _pid(l): return l["product_id"][0] if l.get("product_id") else None
    sublet = sum(l["price_subtotal"] for l in revenue_lines
                 if _pid(l) is not None and prod_to_cat.get(_pid(l)) == "Sub Let")
    goods = sum(l["price_subtotal"] for l in revenue_lines
                if _pid(l) is not None and prod_to_type.get(_pid(l)) in ("consu", "goods"))
    core_service = sum(l["price_subtotal"] for l in revenue_lines
                       if _pid(l) is not None and prod_to_type.get(_pid(l)) == "service"
                       and prod_to_cat.get(_pid(l)) != "Sub Let")
    unclassified = sum(l["price_subtotal"] for l in revenue_lines if _pid(l) is None)

    invoice_lines_by_move = defaultdict(list)
    for l in revenue_lines:
        invoice_lines_by_move[l["move_id"][0]].append(l)
    recon_failures = []
    for i in inv:
        line_subtotal = sum(l.get("price_subtotal", 0) for l in invoice_lines_by_move.get(i["id"], []))
        line_total = sum(l.get("price_total", 0) for l in invoice_lines_by_move.get(i["id"], []))
        if abs(line_subtotal - i["amount_untaxed"]) > 0.01 or abs(line_total - i["amount_total"]) > 0.01:
            recon_failures.append({"invoice": i["name"]})

    return {
        "month_start": month_start, "month_end": month_end,
        "month_label": month_start.strftime("%B %Y"),
        "gross": gross, "untaxed": untaxed, "refunds": refunds, "net": net,
        "prev_gross": prev_gross, "prev_net": prev_net, "prev_label": prev_start.strftime("%B"),
        "pp_gross": pp_gross, "pp_net": pp_net, "pp_label": pp_start.strftime("%B"),
        "invoice_count": len(inv), "ref_count": len(ref),
        "prev_invoice_count": len(prev_inv), "pp_invoice_count": len(pp_inv),
        "core_service": core_service, "sublet": sublet, "goods": goods, "unclassified": unclassified,
        "cat_to_products": cat_to_products,
        "by_partner": dict(by_partner),
        "by_pay": dict(by_pay),
        "recon_pass": len(recon_failures) == 0,
        "recon_failures": recon_failures,
    }


def fetch_outstanding(odoo) -> list:
    today = dt.date.today()
    return _search_read(odoo, "account.move",
        [["move_type","=","out_invoice"], ["state","=","posted"],
         ["payment_state","in",["not_paid","partial","in_payment"]],
         ["invoice_date","<=",today.isoformat()]],
        ["id","name","partner_id","invoice_date","invoice_date_due","amount_residual"],
        order="invoice_date_due asc", limit=300)


def fmt_eur(amount: float) -> str:
    return f"€{amount:,.2f}"


def pct_badge(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    color = SUCCESS if pct >= 0 else WARN
    return f'<span style="color:{color};font-weight:600;">{sign}{pct:.1f}%</span>'


def render_email(data: dict, outstanding: list) -> str:
    delta_prev = ((data["gross"] - data["prev_gross"]) / data["prev_gross"] * 100) if data["prev_gross"] > 0 else 0
    delta_pp = ((data["gross"] - data["pp_gross"]) / data["pp_gross"] * 100) if data["pp_gross"] > 0 else 0

    html = []
    html.append('<!DOCTYPE html><html><head><meta charset="utf-8"></head>')
    html.append(f'<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,Helvetica,Arial,sans-serif;color:{TEXT};">')
    html.append('<div style="max-width:760px;margin:0 auto;padding:24px;">')

    # Brand header
    html.append(f'<div style="background:{NAVY};padding:20px 24px;border-radius:8px 8px 0 0;text-align:center;">')
    html.append('<img src="https://commandcentre.info/cds/canary-detect-logo.png" alt="Canary Detect" style="height:50px;" />')
    html.append('</div>')

    # Title strip
    html.append(f'<div style="background:white;padding:24px;border:1px solid {BORDER};border-top:0;border-radius:0 0 8px 8px;margin-bottom:24px;">')
    html.append(f'<h1 style="margin:0;color:{NAVY};font-size:24px;font-weight:700;">CD Monthly Finance Report</h1>')
    html.append(f'<div style="margin-top:6px;color:{MUTED};font-size:14px;">{data["month_label"]} · Generated {dt.datetime.now(TZ).strftime("%a %-d %b %Y %H:%M %Z")}</div>')
    html.append('</div>')

    # Main report block
    html.append(f'<div style="background:#ffffff;border:1px solid {BORDER};border-left:5px solid {ORANGE};border-radius:8px;padding:24px;margin-bottom:24px;">')
    html.append(f'<div style="font-size:13px;color:{MUTED};text-transform:uppercase;letter-spacing:1px;font-weight:600;margin-bottom:4px;">Monthly turnover</div>')
    period_label = f"{data['month_start'].strftime('%-d %b')} – {(data['month_end'] - dt.timedelta(days=1)).strftime('%-d %b %Y')}"
    html.append(f'<h2 style="margin:0 0 4px 0;color:{NAVY};font-size:22px;font-weight:700;">{period_label}</h2>')

    # KPI hero
    html.append(f'<div style="margin:16px 0 20px 0;padding:16px;background:{NAVY};border-radius:6px;color:white;">')
    html.append('<div style="font-size:13px;opacity:0.85;text-transform:uppercase;letter-spacing:1px;">Gross turnover</div>')
    html.append(f'<div style="font-size:32px;font-weight:700;color:{ORANGE};margin:4px 0 8px 0;">{fmt_eur(data["gross"])}</div>')
    html.append(f'<div style="font-size:14px;opacity:0.9;">{data["invoice_count"]} invoices · vs {data["prev_label"]} {pct_badge(delta_prev)} · vs {data["pp_label"]} {pct_badge(delta_pp)}</div>')
    html.append('</div>')

    # Headline table
    html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:14px;">')
    html.append(f'<tr style="background:{BG_ALT};"><th style="padding:8px 12px;text-align:left;border:1px solid {BORDER};">Metric</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">{data["month_label"]}</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">{data["prev_label"]}</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">{data["pp_label"]}</th></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};"><b>Gross</b></td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};"><b>{fmt_eur(data["gross"])}</b></td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["prev_gross"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["pp_gross"])}</td></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Net (after credits)</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["net"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["prev_net"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["pp_net"])}</td></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Untaxed</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["untaxed"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">–</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">–</td></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Invoices</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{data["invoice_count"]}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{data["prev_invoice_count"]}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{data["pp_invoice_count"]}</td></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Credit notes</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{data["ref_count"]} ({fmt_eur(data["refunds"])})</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">–</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">–</td></tr>')
    html.append('</table>')

    # Revenue split
    html.append(f'<h3 style="margin:16px 0 8px 0;color:{NAVY};font-size:16px;">Revenue split</h3>')
    html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px;">')
    html.append(f'<tr style="background:{BG_ALT};"><th style="padding:8px 12px;text-align:left;border:1px solid {BORDER};">Type</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Amount (untaxed)</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Share</th></tr>')
    if data["untaxed"] > 0:
        html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};"><b>Core service</b> (services in proper categories)</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["core_service"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{data["core_service"]/data["untaxed"]*100:.1f}%</td></tr>')
        html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Sub Let (rental income)</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["sublet"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{data["sublet"]/data["untaxed"]*100:.1f}%</td></tr>')
        html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Goods (physical product sales)</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["goods"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{data["goods"]/data["untaxed"]*100:.1f}%</td></tr>')
        if abs(data["unclassified"]) > 0.005:
            html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};color:{WARN};">⚠ Unclassified (no product set on line)</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{WARN};">{fmt_eur(data["unclassified"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{WARN};">{data["unclassified"]/data["untaxed"]*100:.1f}%</td></tr>')
    html.append(f'<tr style="background:{BG_ALT};font-weight:700;"><td style="padding:6px 12px;border:1px solid {BORDER};">Total</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["untaxed"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">100%</td></tr>')
    html.append('</table>')

    # By category
    if data["cat_to_products"]:
        total_cat = sum(sum(p["subtotal"] for p in v.values()) for v in data["cat_to_products"].values())
        html.append(f'<h3 style="margin:16px 0 8px 0;color:{NAVY};font-size:16px;">By category</h3>')
        html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px;">')
        html.append(f'<tr style="background:{NAVY};color:white;"><th style="padding:8px 12px;text-align:left;border:1px solid {BORDER};">Category</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Amount</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Share</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Lines</th></tr>')
        for cat, products in sorted(data["cat_to_products"].items(),
                                    key=lambda kv: sum(p["subtotal"] for p in kv[1].values()),
                                    reverse=True):
            cat_total = sum(p["subtotal"] for p in products.values())
            cat_lines = sum(p["lines"] for p in products.values())
            pct = cat_total/total_cat*100 if total_cat else 0
            html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};"><b>{cat}</b></td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(cat_total)}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{pct:.1f}%</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{cat_lines}</td></tr>')
        html.append(f'<tr style="background:{BG_ALT};font-weight:700;"><td style="padding:6px 12px;border:1px solid {BORDER};">Total</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(total_cat)}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">100%</td><td></td></tr>')
        html.append('</table>')

    # By product (single grouped table)
    if data["cat_to_products"]:
        grand_total = sum(sum(p["subtotal"] for p in v.values()) for v in data["cat_to_products"].values())
        html.append(f'<h3 style="margin:16px 0 8px 0;color:{NAVY};font-size:16px;">By product</h3>')
        html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px;">')
        html.append(f'<tr style="background:{NAVY};color:white;">'
                    f'<th style="padding:8px 12px;text-align:left;border:1px solid {BORDER};">Product</th>'
                    f'<th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Amount</th>'
                    f'<th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Share</th>'
                    f'<th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Lines</th>'
                    f'<th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Avg / line</th>'
                    f'<th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Customers</th>'
                    f'</tr>')
        for cat, products in sorted(data["cat_to_products"].items(),
                                    key=lambda kv: sum(p["subtotal"] for p in kv[1].values()),
                                    reverse=True):
            cat_total = sum(p["subtotal"] for p in products.values())
            cat_lines = sum(p["lines"] for p in products.values())
            cat_pct = cat_total / grand_total * 100 if grand_total else 0
            html.append(f'<tr style="background:{BG_ALT};">'
                        f'<td style="padding:8px 12px;border:1px solid {BORDER};font-weight:700;color:{NAVY};">{cat}</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};font-weight:700;color:{NAVY};">{fmt_eur(cat_total)}</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};font-weight:700;color:{NAVY};">{cat_pct:.1f}%</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};font-weight:700;color:{NAVY};">{cat_lines}</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">—</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">—</td>'
                        f'</tr>')
            for pname, v in sorted(products.items(), key=lambda kv: kv[1]["subtotal"], reverse=True):
                avg = v["subtotal"]/v["lines"] if v["lines"] else 0
                share = v["subtotal"]/cat_total*100 if cat_total else 0
                html.append(f'<tr>'
                            f'<td style="padding:6px 12px 6px 28px;border:1px solid {BORDER};">{pname}</td>'
                            f'<td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(v["subtotal"])}</td>'
                            f'<td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{share:.1f}%</td>'
                            f'<td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{v["lines"]}</td>'
                            f'<td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{fmt_eur(avg)}</td>'
                            f'<td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{len(v["customers"])}</td>'
                            f'</tr>')
        html.append('</table>')

    # Top customers (kept on monthly)
    sorted_partners = sorted(data["by_partner"].items(), key=lambda kv: kv[1]["gross"], reverse=True)
    if sorted_partners:
        html.append(f'<h3 style="margin:16px 0 8px 0;color:{NAVY};font-size:16px;">Top customers</h3>')
        html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px;">')
        html.append(f'<tr style="background:{NAVY};color:white;"><th style="padding:8px 12px;text-align:left;border:1px solid {BORDER};">#</th><th style="padding:8px 12px;text-align:left;border:1px solid {BORDER};">Customer</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Amount (gross)</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Invoices</th></tr>')
        for idx, (n, v) in enumerate(sorted_partners[:15], 1):
            html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};color:{MUTED};">{idx}</td><td style="padding:6px 12px;border:1px solid {BORDER};">{n}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(v["gross"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{v["count"]}</td></tr>')
        if len(sorted_partners) > 15:
            rest_gross = sum(v["gross"] for _,v in sorted_partners[15:])
            rest_count = sum(v["count"] for _,v in sorted_partners[15:])
            html.append(f'<tr><td colspan="2" style="padding:6px 12px;border:1px solid {BORDER};color:{MUTED};font-style:italic;">({len(sorted_partners) - 15} more)</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{fmt_eur(rest_gross)}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{rest_count}</td></tr>')
        html.append('</table>')
        html.append(f'<div style="margin-top:-8px;margin-bottom:16px;font-size:13px;color:{MUTED};">{len(sorted_partners)} customers invoiced this month</div>')

    # Payment state (kept on monthly)
    if data["by_pay"]:
        html.append(f'<h3 style="margin:16px 0 8px 0;color:{NAVY};font-size:16px;">Payment state of this month\'s invoices (as of today)</h3>')
        html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px;">')
        html.append(f'<tr style="background:{BG_ALT};"><th style="padding:8px 12px;text-align:left;border:1px solid {BORDER};">State</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Count</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Amount</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Residual outstanding</th></tr>')
        for state, v in sorted(data["by_pay"].items()):
            html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">{state}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{v["count"]}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(v["amount"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{fmt_eur(v["residual"])}</td></tr>')
        html.append('</table>')

    # Reconciliation badge
    if data["recon_pass"]:
        html.append(f'<div style="margin-top:12px;padding:10px 14px;background:#dcfce7;border-left:4px solid {SUCCESS};border-radius:4px;font-size:13px;color:#166534;">✓ Reconciliation: PASS — line totals match invoice headers, all {data["invoice_count"]} invoices reconcile within €0.01</div>')
    else:
        html.append(f'<div style="margin-top:12px;padding:10px 14px;background:#fee2e2;border-left:4px solid {WARN};border-radius:4px;font-size:13px;color:#991b1b;">⚠ Reconciliation FAIL — {len(data["recon_failures"])} invoice(s) don\'t reconcile.</div>')

    html.append('</div>')  # close main report block

    # Outstanding section
    today = dt.date.today()
    total_out = sum(i["amount_residual"] for i in outstanding)
    html.append(f'<div style="background:#ffffff;border:1px solid {BORDER};border-left:5px solid {WARN};border-radius:8px;padding:24px;margin-bottom:24px;">')
    html.append(f'<h2 style="margin:0 0 4px 0;color:{NAVY};font-size:18px;">Outstanding invoices (all vintages)</h2>')
    html.append(f'<div style="margin:8px 0 16px 0;font-size:18px;color:{WARN};font-weight:700;">{fmt_eur(total_out)} across {len(outstanding)} invoices</div>')
    if outstanding:
        html.append(f'<table style="width:100%;border-collapse:collapse;font-size:13px;">')
        html.append(f'<tr style="background:{NAVY};color:white;"><th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Customer</th><th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Invoice</th><th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Issued</th><th style="padding:8px 10px;text-align:right;border:1px solid {BORDER};">Residual</th><th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Status</th></tr>')
        for inv_o in outstanding[:20]:
            partner = inv_o["partner_id"][1] if inv_o.get("partner_id") else "?"
            issued = inv_o.get("invoice_date", "?")
            due = inv_o.get("invoice_date_due", "?")
            try:
                due_d = dt.date.fromisoformat(due)
                overdue = (today - due_d).days
                status = f'<span style="color:{WARN};">{overdue}d overdue</span>' if overdue > 0 else f'in {-overdue}d'
            except Exception:
                status = "-"
            html.append(f'<tr><td style="padding:6px 10px;border:1px solid {BORDER};">{partner}</td><td style="padding:6px 10px;border:1px solid {BORDER};font-family:monospace;font-size:12px;">{inv_o["name"]}</td><td style="padding:6px 10px;border:1px solid {BORDER};color:{MUTED};">{issued}</td><td style="padding:6px 10px;text-align:right;border:1px solid {BORDER};">{fmt_eur(inv_o["amount_residual"])}</td><td style="padding:6px 10px;border:1px solid {BORDER};font-size:12px;">{status}</td></tr>')
        if len(outstanding) > 20:
            rest = sum(i["amount_residual"] for i in outstanding[20:])
            html.append(f'<tr><td colspan="3" style="padding:6px 10px;border:1px solid {BORDER};color:{MUTED};font-style:italic;">({len(outstanding)-20} older invoices)</td><td style="padding:6px 10px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{fmt_eur(rest)}</td><td style="border:1px solid {BORDER};"></td></tr>')
        html.append('</table>')
    html.append('</div>')

    # Footer
    html.append(f'<div style="margin-top:24px;padding:16px;text-align:center;font-size:12px;color:{MUTED};">')
    html.append('<div>Pulled live from Odoo (camello-blanco-sl.odoo.com) — no cached data.</div>')
    html.append('<div style="margin-top:6px;">Run on demand. Markdown archive: Businesses/canary-detect/finance/monthly-turnover-reports/</div>')
    html.append('<div style="margin-top:12px;color:#9ca3af;">Canary Detect — "The Leaky Finders" · Camello Blanco S.L.</div>')
    html.append('</div>')

    html.append('</div></body></html>')
    return "".join(html)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--month", help="Target month YYYY-MM. Default: last completed month.")
    p.add_argument("--preview", action="store_true", help="Send to Pete only.")
    p.add_argument("--dry-run", action="store_true", help="Render, do not send.")
    p.add_argument("--no-send", action="store_true", help="Publish the CC snapshot only; do NOT email (backfill mode).")
    p.add_argument("--to-override", help="Comma-separated override recipients.")
    args = p.parse_args()

    today = dt.datetime.now(TZ).date()
    if args.month:
        year, month = map(int, args.month.split("-"))
    else:
        if today.month == 1:
            year, month = today.year - 1, 12
        else:
            year, month = today.year, today.month - 1

    odoo = _odoo()
    print(f"Pulling {year}-{month:02d} data + 2 prior months...", file=sys.stderr)
    data = fetch_month_data(odoo, year, month)
    print("Pulling outstanding...", file=sys.stderr)
    outstanding = fetch_outstanding(odoo)

    # Save markdown archive
    print("Saving markdown archive...", file=sys.stderr)
    monthly_mod = _monthly_report()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    md = monthly_mod.build_report(year, month, odoo)
    path = OUTPUT_DIR / f"turnover-{year:04d}-{month:02d}.md"
    path.write_text(md)
    print(f"  Saved {path.name}", file=sys.stderr)

    html = render_email(data, outstanding)
    subject = f"CD Monthly Finance Report — {data['month_label']}"

    if args.dry_run:
        print(f"Subject: {subject}\n=== HTML ({len(html)} chars) ===")
        print(html[:2000])
        return

    if args.no_send:
        try:
            import importlib.util as _il
            _spec = _il.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
            _cc = _il.module_from_spec(_spec); _spec.loader.exec_module(_cc)
            ok = _cc.publish("cd-finance-monthly", dt.date(year, month, 1).isoformat(), {"subject": subject, "html": html})
            print(f"[no-send] CC snapshot {'published' if ok else 'FAILED'} for {year}-{month:02d}; email skipped.")
        except Exception as _e:
            print(f"  CC PUBLISH FAILED: {_e}")
        return

    if args.to_override:
        recipients = [r.strip() for r in args.to_override.split(",") if r.strip()]
    elif args.preview or os.environ.get("FINANCE_LIVE") != "1":
        # send-gate (migration): route to Pete only until FINANCE_LIVE=1 verifies the real recipients
        recipients = RECIPIENTS_PREVIEW
    else:
        recipients = RECIPIENTS_LIVE

    g = _gmail()
    result = g.send(
        to=", ".join(recipients), subject=subject, body=html, html=True, from_=SENDER,
    )
    print(f"\nSent: id={result.get('id')}  threadId={result.get('threadId')}")
    # --- Command Centre publish (P5, 2026-06-11): snapshot to reports.snapshots; the email above is unchanged. Non-fatal.
    try:
        import importlib.util as _il, datetime as _dt
        _spec = _il.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
        _cc = _il.module_from_spec(_spec); _spec.loader.exec_module(_cc)
        _cc.publish("cd-finance-monthly", dt.date(year, month, 1).isoformat(), {"subject": subject, "html": html})
        print("  CC: snapshot published")
    except Exception as _e:
        print(f"  CC PUBLISH FAILED: {_e}")

    print(f"From: {SENDER}\nTo: {', '.join(recipients)}\nSubject: {subject}")


if __name__ == "__main__":
    main()
