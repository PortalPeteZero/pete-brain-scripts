#!/usr/bin/env python3
"""
CD Weekly Finance Email -- generates the weekly report and emails it to the team.

Designed to run as a scheduled task every Tuesday at 18:00 Atlantic/Canary
(cron `0 18 * * 2`).

Each run:
  1. Pulls live data from Odoo (no cached / stored data)
  2. Generates the LAST COMPLETED WEEK report (primary)
  3. Re-generates the WEEK BEFORE THAT (refresh -- catches any late invoices
     that came in on Mon/Tue this week but belong to the prior week)
  4. Renders both into a single beautifully-formatted HTML email
  5. Saves the markdown reports to the vault for archive
  6. Sends to Pete (Sygma), Dave, Nicola
  7. Appends a confirmation block to today's daily note

Usage:
  python3 cd-weekly-finance-email.py                  # send live to all recipients
  python3 cd-weekly-finance-email.py --preview        # send to Pete only (test)
  python3 cd-weekly-finance-email.py --dry-run        # render, no send
  python3 cd-weekly-finance-email.py --week 2026-04-20  # specific reference week
"""
# CRON-META
# what: CD weekly finance turnover report to Pete, Dave, Nicola.
# why: Pete + CD finance see weekly turnover from Odoo without anyone running a report.
# reads: Odoo finance
# writes: 2 markdown reports + HTML email
# entity: canary-detect
# report: cd-finance
# schedule: 0 19 * * 2
# timezone: Atlantic/Canary
# CRON-META-END
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
OUTPUT_DIR = VAULT_ROOT / "Businesses/canary-detect/finance/weekly-turnover-reports"
TZ = ZoneInfo("Atlantic/Canary")

# Recipients
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
ORANGE_DARK = "#e0860f"
TEAL = "#2BBFBF"
BG_ALT = "#F8FAFC"
BORDER = "#E2E8F0"
MUTED = "#64748B"
TEXT = "#1E293B"
SUCCESS = "#22c55e"
WARN = "#dc3545"


# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _weekly_report():
    """Load the existing cd-weekly-finance-report.py for its build_report helper."""
    spec = importlib.util.spec_from_file_location(
        "cd_weekly_finance_report",
        str(SCRIPT_DIR / "cd-weekly-finance-report.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _search_read(odoo, model, domain, fields, **kw):
    return odoo._execute(model, "search_read", [domain], {"fields": fields, **kw})


def _last_completed_monday(today: dt.date) -> dt.date:
    """Returns the Monday of the most-recently completed Mon-Sun week."""
    days_since_monday = today.weekday()  # 0=Mon, 6=Sun
    last_sun = today - dt.timedelta(days=days_since_monday + 1)  # last Sunday
    return last_sun - dt.timedelta(days=6)


# ── Data extraction (mirrors cd-weekly-finance-report.py) ─────────────────────

def fetch_week_data(odoo, monday: dt.date) -> dict:
    """Pull all data needed for one week's report. Identical query shape to
    cd-weekly-finance-report.py's build_report -- just returns the raw aggregations
    so we can render them however we want."""
    week_start = monday
    week_end = monday + dt.timedelta(days=7)
    prev_start = monday - dt.timedelta(days=7)
    prev_end = monday
    today = dt.date.today()

    inv = _search_read(odoo, "account.move",
        [["move_type","=","out_invoice"], ["state","=","posted"],
         ["invoice_date",">=",week_start.isoformat()], ["invoice_date","<",week_end.isoformat()]],
        ["id","name","partner_id","invoice_date","invoice_date_due","amount_total",
         "amount_residual","amount_untaxed","payment_state","invoice_line_ids"],
        order="invoice_date desc", limit=500)
    ref = _search_read(odoo, "account.move",
        [["move_type","=","out_refund"], ["state","=","posted"],
         ["invoice_date",">=",week_start.isoformat()], ["invoice_date","<",week_end.isoformat()]],
        ["id","name","partner_id","invoice_date","amount_untaxed","amount_total"], limit=200)
    prev_inv = _search_read(odoo, "account.move",
        [["move_type","=","out_invoice"], ["state","=","posted"],
         ["invoice_date",">=",prev_start.isoformat()], ["invoice_date","<",prev_end.isoformat()]],
        ["id","amount_total","amount_untaxed"], limit=500)

    # 4-week rolling baseline before the report week
    four_week_grosses = []
    for i in range(1, 5):
        ws = monday - dt.timedelta(days=7 * i)
        we = ws + dt.timedelta(days=7)
        wk_inv = _search_read(odoo, "account.move",
            [["move_type","=","out_invoice"], ["state","=","posted"],
             ["invoice_date",">=",ws.isoformat()], ["invoice_date","<",we.isoformat()]],
            ["amount_total"], limit=500)
        wk_ref = _search_read(odoo, "account.move",
            [["move_type","=","out_refund"], ["state","=","posted"],
             ["invoice_date",">=",ws.isoformat()], ["invoice_date","<",we.isoformat()]],
            ["amount_total"], limit=200)
        four_week_grosses.append(sum(x["amount_total"] for x in wk_inv) - sum(x["amount_total"] for x in wk_ref))

    # Lines
    line_ids = []
    for i in inv:
        line_ids.extend(i.get("invoice_line_ids", []))
    lines = _search_read(odoo, "account.move.line",
        [["id","in",line_ids]],
        ["id","name","product_id","quantity","price_subtotal","price_total","display_type","move_id"],
        limit=2000) if line_ids else []
    revenue_lines = [l for l in lines if abs(l.get("price_subtotal", 0)) > 0.005]

    # Product metadata (active_test=False to resolve archived names)
    prod_ids = list(set(l["product_id"][0] for l in revenue_lines if l.get("product_id")))
    prods = odoo._execute("product.product", "search_read",
                         [[["id","in",prod_ids]]],
                         {"fields": ["id","name","categ_id","type","active"],
                          "context": {"active_test": False}}) if prod_ids else []
    prod_to_cat = {p["id"]: (p["categ_id"][1] if p.get("categ_id") else "(none)") for p in prods}
    prod_to_type = {p["id"]: p.get("type") for p in prods}
    prod_to_active = {p["id"]: p.get("active", True) for p in prods}

    # Aggregations
    gross = sum(i["amount_total"] for i in inv)
    untaxed = sum(i["amount_untaxed"] for i in inv)
    refunds = sum(r["amount_total"] for r in ref)
    net = gross - refunds
    prev_gross = sum(i["amount_total"] for i in prev_inv)
    avg4 = sum(four_week_grosses) / 4 if four_week_grosses else 0

    # Category x product
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

    by_partner = defaultdict(lambda: {"gross":0.0, "count":0})
    for i in inv:
        n = i["partner_id"][1] if i.get("partner_id") else "?"
        by_partner[n]["gross"] += i["amount_total"]
        by_partner[n]["count"] += 1

    by_pay = defaultdict(lambda: {"count":0, "amount":0.0, "residual":0.0})
    for i in inv:
        s = i.get("payment_state", "?")
        by_pay[s]["count"] += 1
        by_pay[s]["amount"] += i["amount_total"]
        by_pay[s]["residual"] += i.get("amount_residual", 0)

    # Reconciliation
    invoice_lines_by_move = defaultdict(list)
    for l in revenue_lines:
        invoice_lines_by_move[l["move_id"][0]].append(l)
    recon_failures = []
    for i in inv:
        line_subtotal = sum(l.get("price_subtotal",0) for l in invoice_lines_by_move.get(i["id"],[]))
        line_total = sum(l.get("price_total",0) for l in invoice_lines_by_move.get(i["id"],[]))
        if abs(line_subtotal - i["amount_untaxed"]) > 0.01 or abs(line_total - i["amount_total"]) > 0.01:
            recon_failures.append({"invoice": i["name"], "delta": line_subtotal - i["amount_untaxed"]})

    return {
        "monday": monday,
        "gross": gross, "untaxed": untaxed, "refunds": refunds, "net": net,
        "prev_gross": prev_gross,
        "avg4": avg4,
        "four_week_grosses": four_week_grosses,
        "invoice_count": len(inv),
        "ref_count": len(ref),
        "invoices": inv,
        "refunds_list": ref,
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


# ── HTML rendering ─────────────────────────────────────────────────────────────

def fmt_eur(amount: float) -> str:
    return f"€{amount:,.2f}"


def pct_badge(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    color = SUCCESS if pct >= 0 else WARN
    return f'<span style="color:{color};font-weight:600;">{sign}{pct:.1f}%</span>'


def render_week_block(label: str, data: dict, is_primary: bool = True) -> str:
    """Render a single week's data as an HTML block."""
    monday = data["monday"]
    week_end = monday + dt.timedelta(days=6)
    week_label = f"Mon {monday.strftime('%-d %b')} – Sun {week_end.strftime('%-d %b %Y')}"

    html = []
    block_bg = "#ffffff" if is_primary else BG_ALT
    accent_bar = ORANGE if is_primary else MUTED

    html.append(f'<div style="background:{block_bg};border:1px solid {BORDER};border-left:5px solid {accent_bar};border-radius:8px;padding:24px;margin-bottom:24px;">')

    # Block header
    html.append(f'<div style="font-size:13px;color:{MUTED};text-transform:uppercase;letter-spacing:1px;font-weight:600;margin-bottom:4px;">{label}</div>')
    html.append(f'<h2 style="margin:0 0 4px 0;color:{NAVY};font-size:22px;font-weight:700;">{week_label}</h2>')

    # KPI summary
    delta_prev = ((data["gross"] - data["prev_gross"]) / data["prev_gross"] * 100) if data["prev_gross"] > 0 else 0
    delta_avg = ((data["gross"] - data["avg4"]) / data["avg4"] * 100) if data["avg4"] > 0 else 0

    html.append(f'<div style="margin:16px 0 20px 0;padding:16px;background:{NAVY};border-radius:6px;color:white;">')
    html.append(f'<div style="font-size:13px;opacity:0.85;text-transform:uppercase;letter-spacing:1px;">Gross turnover</div>')
    html.append(f'<div style="font-size:32px;font-weight:700;color:{ORANGE};margin:4px 0 8px 0;">{fmt_eur(data["gross"])}</div>')
    html.append(f'<div style="font-size:14px;opacity:0.9;">{data["invoice_count"]} invoices · vs last week {pct_badge(delta_prev)} · vs 4-week avg {pct_badge(delta_avg)}</div>')
    html.append(f'</div>')

    # Headline numbers table
    html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px;">')
    html.append(f'<tr style="background:{BG_ALT};"><th style="padding:8px 12px;text-align:left;border:1px solid {BORDER};">Metric</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">This week</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Last week</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">4-week avg</th></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};"><b>Gross</b></td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};"><b>{fmt_eur(data["gross"])}</b></td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["prev_gross"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["avg4"])}</td></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Net (after credits)</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["net"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">–</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">–</td></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Untaxed</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["untaxed"])}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">–</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">–</td></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Invoices</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{data["invoice_count"]}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">–</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">–</td></tr>')
    html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};">Credit notes</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{data["ref_count"]} ({fmt_eur(data["refunds"])})</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">–</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">–</td></tr>')
    html.append('</table>')

    # Per-invoice list (what makes up the gross figure)
    html.append(render_invoices_block(data))

    # 4-week trend
    html.append(f'<div style="margin-bottom:20px;font-size:13px;color:{MUTED};">')
    html.append(f'<b style="color:{NAVY};">4-week baseline:</b> ')
    parts = []
    for i in range(4, 0, -1):
        ws = monday - dt.timedelta(days=7*i)
        parts.append(f'{ws.strftime("%-d %b")}: {fmt_eur(data["four_week_grosses"][i-1])}')
    html.append(' · '.join(parts))
    html.append('</div>')

    # By category
    if data["cat_to_products"]:
        total = sum(sum(p["subtotal"] for p in v.values()) for v in data["cat_to_products"].values())
        html.append(f'<h3 style="margin:16px 0 8px 0;color:{NAVY};font-size:16px;">By category</h3>')
        html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px;">')
        html.append(f'<tr style="background:{NAVY};color:white;"><th style="padding:8px 12px;text-align:left;border:1px solid {BORDER};">Category</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Amount</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Share</th><th style="padding:8px 12px;text-align:right;border:1px solid {BORDER};">Lines</th></tr>')
        for cat, products in sorted(data["cat_to_products"].items(),
                                    key=lambda kv: sum(p["subtotal"] for p in kv[1].values()),
                                    reverse=True):
            cat_total = sum(p["subtotal"] for p in products.values())
            cat_lines = sum(p["lines"] for p in products.values())
            pct = cat_total/total*100 if total else 0
            html.append(f'<tr><td style="padding:6px 12px;border:1px solid {BORDER};"><b>{cat}</b></td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(cat_total)}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{pct:.1f}%</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{cat_lines}</td></tr>')
        html.append(f'<tr style="background:{BG_ALT};font-weight:700;"><td style="padding:6px 12px;border:1px solid {BORDER};">Total</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">{fmt_eur(total)}</td><td style="padding:6px 12px;text-align:right;border:1px solid {BORDER};">100%</td><td></td></tr>')
        html.append('</table>')

    # By product -- single grouped table, category section rows + product rows beneath
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
            # Category section row
            html.append(f'<tr style="background:{BG_ALT};">'
                        f'<td style="padding:8px 12px;border:1px solid {BORDER};font-weight:700;color:{NAVY};">{cat}</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};font-weight:700;color:{NAVY};">{fmt_eur(cat_total)}</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};font-weight:700;color:{NAVY};">{cat_pct:.1f}%</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};font-weight:700;color:{NAVY};">{cat_lines}</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">—</td>'
                        f'<td style="padding:8px 12px;text-align:right;border:1px solid {BORDER};color:{MUTED};">—</td>'
                        f'</tr>')
            # Product rows beneath
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

    # Reconciliation badge
    if data["recon_pass"]:
        html.append(f'<div style="margin-top:12px;padding:10px 14px;background:#dcfce7;border-left:4px solid {SUCCESS};border-radius:4px;font-size:13px;color:#166534;">✓ Reconciliation: PASS — line totals match invoice headers, all {data["invoice_count"]} invoices reconcile within €0.01</div>')
    else:
        html.append(f'<div style="margin-top:12px;padding:10px 14px;background:#fee2e2;border-left:4px solid {WARN};border-radius:4px;font-size:13px;color:#991b1b;">⚠ Reconciliation FAIL — {len(data["recon_failures"])} invoice(s) don\'t reconcile. Investigate.</div>')

    html.append('</div>')  # close block
    return "".join(html)


def render_invoices_block(data: dict) -> str:
    """Per-invoice list backing the week's gross figure. One row per invoice
    sorted by date asc then invoice number, plus a credit-notes mini-table if any."""
    invoices = sorted(
        data.get("invoices", []),
        key=lambda i: (i.get("invoice_date") or "", i.get("name") or ""),
    )
    refunds = sorted(
        data.get("refunds_list", []),
        key=lambda i: (i.get("invoice_date") or "", i.get("name") or ""),
    )
    if not invoices and not refunds:
        return ""

    html = []
    html.append(f'<h3 style="margin:16px 0 8px 0;color:{NAVY};font-size:16px;">Invoices this week ({len(invoices)})</h3>')

    if invoices:
        html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:8px;font-size:13px;">')
        html.append(
            f'<tr style="background:{NAVY};color:white;">'
            f'<th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Invoice</th>'
            f'<th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Date</th>'
            f'<th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Customer</th>'
            f'<th style="padding:8px 10px;text-align:right;border:1px solid {BORDER};">Untaxed</th>'
            f'<th style="padding:8px 10px;text-align:right;border:1px solid {BORDER};">Gross</th>'
            f'</tr>'
        )
        for i in invoices:
            partner = i["partner_id"][1] if i.get("partner_id") else "—"
            html.append(
                f'<tr>'
                f'<td style="padding:6px 10px;border:1px solid {BORDER};font-family:monospace;font-size:12px;">{i["name"]}</td>'
                f'<td style="padding:6px 10px;border:1px solid {BORDER};color:{MUTED};">{i.get("invoice_date","—")}</td>'
                f'<td style="padding:6px 10px;border:1px solid {BORDER};">{partner}</td>'
                f'<td style="padding:6px 10px;text-align:right;border:1px solid {BORDER};">{fmt_eur(i.get("amount_untaxed",0))}</td>'
                f'<td style="padding:6px 10px;text-align:right;border:1px solid {BORDER};">{fmt_eur(i.get("amount_total",0))}</td>'
                f'</tr>'
            )
        # Total row
        html.append(
            f'<tr style="background:{BG_ALT};font-weight:700;color:{NAVY};">'
            f'<td colspan="3" style="padding:8px 10px;border:1px solid {BORDER};">Total ({len(invoices)} invoices)</td>'
            f'<td style="padding:8px 10px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["untaxed"])}</td>'
            f'<td style="padding:8px 10px;text-align:right;border:1px solid {BORDER};">{fmt_eur(data["gross"])}</td>'
            f'</tr>'
        )
        html.append('</table>')

    if refunds:
        html.append(f'<h3 style="margin:16px 0 8px 0;color:{NAVY};font-size:14px;">Credit notes this week ({len(refunds)})</h3>')
        html.append(f'<table style="width:100%;border-collapse:collapse;margin-bottom:8px;font-size:13px;">')
        html.append(
            f'<tr style="background:{NAVY};color:white;">'
            f'<th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Credit note</th>'
            f'<th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Date</th>'
            f'<th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Customer</th>'
            f'<th style="padding:8px 10px;text-align:right;border:1px solid {BORDER};">Untaxed</th>'
            f'<th style="padding:8px 10px;text-align:right;border:1px solid {BORDER};">Gross</th>'
            f'</tr>'
        )
        for r in refunds:
            partner = r["partner_id"][1] if r.get("partner_id") else "—"
            html.append(
                f'<tr>'
                f'<td style="padding:6px 10px;border:1px solid {BORDER};font-family:monospace;font-size:12px;">{r["name"]}</td>'
                f'<td style="padding:6px 10px;border:1px solid {BORDER};color:{MUTED};">{r.get("invoice_date","—")}</td>'
                f'<td style="padding:6px 10px;border:1px solid {BORDER};">{partner}</td>'
                f'<td style="padding:6px 10px;text-align:right;border:1px solid {BORDER};color:{WARN};">−{fmt_eur(r.get("amount_untaxed",0))}</td>'
                f'<td style="padding:6px 10px;text-align:right;border:1px solid {BORDER};color:{WARN};">−{fmt_eur(r.get("amount_total",0))}</td>'
                f'</tr>'
            )
        html.append('</table>')

    return "".join(html)


def render_outstanding_block(outstanding: list) -> str:
    today = dt.date.today()
    total = sum(i["amount_residual"] for i in outstanding)
    html = []
    html.append(f'<div style="background:#ffffff;border:1px solid {BORDER};border-left:5px solid {WARN};border-radius:8px;padding:24px;margin-bottom:24px;">')
    html.append(f'<h2 style="margin:0 0 4px 0;color:{NAVY};font-size:18px;">Outstanding invoices (all vintages)</h2>')
    html.append(f'<div style="margin:8px 0 16px 0;font-size:18px;color:{WARN};font-weight:700;">{fmt_eur(total)} across {len(outstanding)} invoices</div>')

    if outstanding:
        html.append(f'<table style="width:100%;border-collapse:collapse;font-size:13px;">')
        html.append(f'<tr style="background:{NAVY};color:white;"><th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Customer</th><th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Invoice</th><th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Issued</th><th style="padding:8px 10px;text-align:right;border:1px solid {BORDER};">Residual</th><th style="padding:8px 10px;text-align:left;border:1px solid {BORDER};">Status</th></tr>')
        for inv_o in outstanding[:15]:
            partner = inv_o["partner_id"][1] if inv_o.get("partner_id") else "?"
            issued = inv_o.get("invoice_date","?")
            due = inv_o.get("invoice_date_due","?")
            try:
                due_d = dt.date.fromisoformat(due)
                overdue = (today - due_d).days
                status = f'<span style="color:{WARN};">{overdue}d overdue</span>' if overdue > 0 else f'in {-overdue}d'
            except Exception:
                status = "-"
            html.append(f'<tr><td style="padding:6px 10px;border:1px solid {BORDER};">{partner}</td><td style="padding:6px 10px;border:1px solid {BORDER};font-family:monospace;font-size:12px;">{inv_o["name"]}</td><td style="padding:6px 10px;border:1px solid {BORDER};color:{MUTED};">{issued}</td><td style="padding:6px 10px;text-align:right;border:1px solid {BORDER};">{fmt_eur(inv_o["amount_residual"])}</td><td style="padding:6px 10px;border:1px solid {BORDER};font-size:12px;">{status}</td></tr>')
        if len(outstanding) > 15:
            rest = sum(i["amount_residual"] for i in outstanding[15:])
            html.append(f'<tr><td colspan="3" style="padding:6px 10px;border:1px solid {BORDER};color:{MUTED};font-style:italic;">({len(outstanding)-15} older invoices)</td><td style="padding:6px 10px;text-align:right;border:1px solid {BORDER};color:{MUTED};">{fmt_eur(rest)}</td><td style="border:1px solid {BORDER};"></td></tr>')
        html.append('</table>')
    html.append('</div>')
    return "".join(html)


def render_email(this_week: dict, prev_week: dict, outstanding: list) -> str:
    """Build the full HTML email body."""
    monday = this_week["monday"]
    week_end = monday + dt.timedelta(days=6)
    subject_label = f"w/c {monday.strftime('%-d %b %Y')}"

    html = []
    html.append('<!DOCTYPE html><html><head><meta charset="utf-8"></head>')
    html.append(f'<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,Helvetica,Arial,sans-serif;color:{TEXT};">')
    html.append('<div style="max-width:760px;margin:0 auto;padding:24px;">')

    # Brand header
    html.append(f'<div style="background:{NAVY};padding:20px 24px;border-radius:8px 8px 0 0;text-align:center;">')
    html.append(f'<img src="https://commandcentre.info/cds/canary-detect-logo.png" alt="Canary Detect" style="height:50px;" />')
    html.append('</div>')

    # Title strip
    html.append(f'<div style="background:white;padding:24px;border:1px solid {BORDER};border-top:0;border-radius:0 0 8px 8px;margin-bottom:24px;">')
    html.append(f'<h1 style="margin:0;color:{NAVY};font-size:24px;font-weight:700;">CD Weekly Finance Report</h1>')
    html.append(f'<div style="margin-top:6px;color:{MUTED};font-size:14px;">{subject_label} · Generated {dt.datetime.now(TZ).strftime("%a %-d %b %Y %H:%M %Z")}</div>')
    html.append(f'<div style="margin-top:12px;padding:10px 14px;background:{BG_ALT};border-left:3px solid {ORANGE};border-radius:4px;font-size:13px;color:{TEXT};">')
    html.append('This email contains <b>two reports</b>: the most recently completed week, plus a <b>refresh of the week before</b>. Late invoices that came in this Mon/Tue but belong to the prior week will now be reflected in the older week\'s numbers — that\'s why both are sent.')
    html.append('</div>')
    html.append('</div>')

    # This week
    html.append(render_week_block("This week — primary report", this_week, is_primary=True))

    # Last week refresh
    html.append(render_week_block("Week before — refreshed", prev_week, is_primary=False))

    # Outstanding (combined)
    html.append(render_outstanding_block(outstanding))

    # Footer
    html.append(f'<div style="margin-top:24px;padding:16px;text-align:center;font-size:12px;color:{MUTED};">')
    html.append('<div>Auto-generated weekly. Pulled live from Odoo (camello-blanco-sl.odoo.com) — no cached data.</div>')
    html.append('<div style="margin-top:6px;">Schedule: every Tuesday at 18:00 Atlantic/Canary.</div>')
    html.append('<div style="margin-top:12px;color:#9ca3af;">Canary Detect — "The Leaky Finders" · Camello Blanco S.L.</div>')
    html.append('</div>')

    html.append('</div></body></html>')
    return "".join(html)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--week", help="Reference week's Monday (YYYY-MM-DD). Default: last completed week.")
    p.add_argument("--preview", action="store_true", help="Send to Pete only.")
    p.add_argument("--dry-run", action="store_true", help="Render, do not send.")
    p.add_argument("--no-send", action="store_true", help="Publish the CC snapshot only; do NOT email (backfill mode).")
    p.add_argument("--to-override", help="Comma-separated override recipients.")
    args = p.parse_args()

    today = dt.datetime.now(TZ).date()
    if args.week:
        d = dt.date.fromisoformat(args.week)
        monday = d - dt.timedelta(days=d.weekday())
    else:
        monday = _last_completed_monday(today)
    prev_monday = monday - dt.timedelta(days=7)

    odoo = _odoo()

    print(f"Pulling this-week data (w/c {monday})...", file=sys.stderr)
    this_week = fetch_week_data(odoo, monday)
    print(f"Pulling week-before data (w/c {prev_monday})...", file=sys.stderr)
    prev_week = fetch_week_data(odoo, prev_monday)
    print(f"Pulling outstanding invoices...", file=sys.stderr)
    outstanding = fetch_outstanding(odoo)

    # Save markdown reports too (for archive)
    print(f"Saving markdown archives...", file=sys.stderr)
    weekly_mod = _weekly_report()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for w in (monday, prev_monday):
        md = weekly_mod.build_report(w, odoo)
        path = OUTPUT_DIR / f"turnover-week-{w.isoformat()}.md"
        path.write_text(md)
        print(f"  Saved {path.name}", file=sys.stderr)

    html = render_email(this_week, prev_week, outstanding)
    subject = f"CD Weekly Finance Report — w/c {monday.strftime('%-d %b %Y')} (incl. refresh of w/c {prev_monday.strftime('%-d %b')})"

    if args.dry_run:
        print(f"\nSubject: {subject}")
        print(f"\n=== HTML ({len(html)} chars) ===")
        print(html[:2000] + "..." if len(html) > 2000 else html)
        return

    if args.no_send:
        try:
            import importlib.util as _il
            _spec = _il.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
            _cc = _il.module_from_spec(_spec); _spec.loader.exec_module(_cc)
            ok = _cc.publish("cd-finance-weekly", monday.isoformat(), {"subject": subject, "html": html})
            print(f"[no-send] CC snapshot {'published' if ok else 'FAILED'} for w/c {monday}; email skipped.")
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
        to=", ".join(recipients),
        subject=subject,
        body=html,
        html=True,
        from_=SENDER,
    )
    print(f"\nSent: id={result.get('id')}  threadId={result.get('threadId')}")
    # --- Command Centre publish (P5, 2026-06-11): snapshot to reports.snapshots; the email above is unchanged. Non-fatal.
    try:
        import importlib.util as _il, datetime as _dt
        _spec = _il.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
        _cc = _il.module_from_spec(_spec); _spec.loader.exec_module(_cc)
        _cc.publish("cd-finance-weekly", monday.isoformat(), {"subject": subject, "html": html})
        print("  CC: snapshot published")
    except Exception as _e:
        print(f"  CC PUBLISH FAILED: {_e}")

    print(f"From: {SENDER}")
    print(f"To: {', '.join(recipients)}")
    print(f"Subject: {subject}")


if __name__ == "__main__":
    main()
