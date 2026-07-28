#!/usr/bin/env python3
"""Attach a Spanish PDF copy to posted Canary Detect customer invoices.

# CRON-META
# key: odoo-invoice-es-copy
# schedule: NOT DEPLOYED YET (proposed: every 15 min) — manual runs only until Pete approves
# what: for each recently posted customer invoice, render the invoice PDF in Spanish
#       (es_ES) via Odoo's real report pipeline and attach it to the invoice as
#       "<INV name> ES.pdf" so Nicola always has a Spanish copy for water-company /
#       insurance claims (Aguas de Lanzarote require Spanish docs from an authorised
#       installer).
# why-session-auth: /report/pdf is the ONLY route that renders the final "Factura"
#       title (the portal token route renders "Factura proforma"). Web sessions cannot
#       be opened with an API key, so this uses the dedicated local session password
#       (secret: odoo-session-password). The odoo.com OAuth login is unaffected.

Mechanics per invoice:
  1. Skip if partner lang is already es_ES (their native PDF is Spanish already).
  2. Skip if an "<name> ES.pdf" attachment already exists (idempotent).
  3. Temporarily set partner lang to es_ES, GET the report PDF, revert lang
     (try/finally, so a crash can never leave a customer flipped to Spanish).
  4. Create ir.attachment on the move.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/odoo-invoice-es-copy.py [--days 14] [--ids 11288,11290]
                                                          [--dry-run]
"""
import argparse
import base64
import http.cookiejar
import importlib.util
import json
import os
import sys
import urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")

_spec = importlib.util.spec_from_file_location("odoo_api", os.path.join(VAULT, "odoo-api.py"))
od = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(od)

REPORT = "account.report_invoice_with_payments"


def _session_opener():
    """Authenticated web-session opener (needed for /report/pdf)."""
    with open(os.path.join(VAULT, "Library", "processes", "secrets", "odoo-session-password")) as f:
        pw = f.read().strip()
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    payload = json.dumps({"jsonrpc": "2.0", "method": "call",
                          "params": {"db": od.CFG["db"], "login": od.CFG["login"],
                                     "password": pw}}).encode()
    req = urllib.request.Request(od.CFG["url"] + "/web/session/authenticate",
                                 data=payload, headers={"Content-Type": "application/json"})
    r = json.load(op.open(req, timeout=30))
    if r.get("error"):
        raise RuntimeError("Odoo session auth failed: " +
                           str(r["error"].get("data", {}).get("message") or r["error"]))
    return op


def es_name(inv_name):
    return inv_name.replace("/", "_") + " ES.pdf"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--ids", help="comma-separated account.move ids (overrides --days)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.ids:
        dom = [["id", "in", [int(x) for x in args.ids.split(",")]]]
    else:
        import datetime
        cutoff = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
        dom = [["invoice_date", ">=", cutoff]]
    dom += [["move_type", "=", "out_invoice"], ["state", "=", "posted"]]

    moves = od._execute("account.move", "search_read",
                        [dom, ["name", "partner_id"]], {"order": "id"})
    if not moves:
        print("no matching posted invoices")
        return

    opener = None
    done = skipped = 0
    for mv in moves:
        pid = mv["partner_id"][0]
        lang = od._execute("res.partner", "read", [[pid], ["lang"]])[0]["lang"]
        if lang == "es_ES":
            skipped += 1
            continue
        fname = es_name(mv["name"])
        existing = od._execute("ir.attachment", "search_count",
                               [[["res_model", "=", "account.move"],
                                 ["res_id", "=", mv["id"]],
                                 ["name", "=", fname]]])
        if existing:
            skipped += 1
            continue
        if args.dry_run:
            print(f"DRY RUN would attach {fname} to {mv['name']}")
            continue
        if opener is None:
            opener = _session_opener()
        od._execute("res.partner", "write", [[pid], {"lang": "es_ES"}])
        try:
            pdf = opener.open(f"{od.CFG['url']}/report/pdf/{REPORT}/{mv['id']}",
                              timeout=120).read()
        finally:
            od._execute("res.partner", "write", [[pid], {"lang": lang}])
        if not pdf.startswith(b"%PDF"):
            print(f"WARN {mv['name']}: response was not a PDF, skipped", file=sys.stderr)
            continue
        od._execute("ir.attachment", "create", [{
            "name": fname,
            "res_model": "account.move",
            "res_id": mv["id"],
            "mimetype": "application/pdf",
            "datas": base64.b64encode(pdf).decode(),
        }])
        print(f"attached {fname} ({len(pdf)} bytes) to {mv['name']}")
        done += 1
    print(f"done: {done} attached, {skipped} skipped (already have copy / Spanish partner)")


if __name__ == "__main__":
    main()
