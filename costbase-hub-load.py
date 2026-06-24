#!/usr/bin/env python3
"""Load the monthly Soldo-audit final-data.json into hub.trainer_cost_base.

Repoints the Trainer Cost Base from the standalone site
(sygma-trainer-cost-base.vercel.app) to the Sygma Internal Hub. The Hub page
(sygmaportal.com/hub/cost-base) renders straight from this table. Run as the final
step of the monthly Soldo audit (see [[monthly-soldo-audit]]).

Usage:
    costbase-hub-load.py [path-to-final-data.json]
    (default: the latest *-final-data.json in the audit-data folder)

The data lives in the Portal's Supabase project (rsczwfstwkthaybxhszy), hub schema,
behind staff-read RLS — written here via the Supabase Management API (account token).
"""
import json
import os
import re
import sys
import glob
import urllib.request
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
AUDIT_DIR = f"{VAULT}/Businesses/sygma-solutions/finance/audit-data"
TOKEN_FILE = f"{VAULT}/Library/processes/supabase-access-token.md"
REF = "rsczwfstwkthaybxhszy"  # Portal Supabase project
MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def sbp_token():
    m = re.search(r"sbp_[A-Za-z0-9]+", open(TOKEN_FILE).read())
    if not m:
        sys.exit("No sbp_ token found in supabase-access-token.md")
    return m.group(0)


def run_sql(sql, token):
    body = json.dumps({"query": sql}).encode()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def slim_audit(year, mm):
    """Merge the month's full Soldo audit so the Hub cost-base can show the Anomalies +
    Per-transaction-audit sections, not just the summary. Reads
    {AUDIT_DIR}/{year}-{mm}-audit.json if present. Only the latest month's audit.json is
    retained by the monthly run, so older months show summary-only (same as the standalone did)."""
    p = f"{AUDIT_DIR}/{year}-{mm}-audit.json"
    if not os.path.exists(p):
        return None
    try:
        a = json.load(open(p))
    except Exception:
        return None
    txns = []
    for t in a.get("transactions", []):
        ec = (t.get("expense_category") or {}).get("name")
        m = t.get("merchant") or {}
        txns.append({
            "trainer": t.get("_trainer") or t.get("wallet_name"),
            "date": (t.get("date") or "")[:10],
            "merchant": m.get("name") or m.get("raw_name") or "",
            "amount": t.get("amount"),
            "category": ec or t.get("category") or "(uncategorised)",
            "note": t.get("user_notes") or t.get("notes") or "",
            "sign": t.get("transaction_sign"),
            "flags": t.get("flags") or [],
            "status": t.get("status"),
            "uncategorised": ec is None,
        })
    txns.sort(key=lambda x: x.get("date") or "")
    return {
        "transactions": txns,
        "by_cat": a.get("by_trainer_cat", {}),
        "nights": a.get("by_trainer_nights", {}),
        "categories": a.get("all_categories", []),
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else (
        sorted(glob.glob(f"{AUDIT_DIR}/*-final-data.json"))[-1]
        if glob.glob(f"{AUDIT_DIR}/*-final-data.json") else None
    )
    if not path or not os.path.exists(path):
        sys.exit("No final-data.json found. Pass a path explicitly.")
    fn = os.path.basename(path)
    ym = re.match(r"(\d{4})-(\d{2})", fn)
    if not ym:
        sys.exit(f"Can't derive the year from filename '{fn}' (expected YYYY-MM-...).")
    year = ym.group(1)

    data = json.load(open(path))
    token = sbp_token()
    stmts, loaded = [], []
    for key, val in data.items():
        mm = MONTHS.get(str(key).lower())
        if mm and val:
            month = f"{year}-{mm}"
            aud = slim_audit(year, mm)
            if aud:
                val = {**val, "audit": aud}
            js = json.dumps(val).replace("'", "''")
            stmts.append(
                f"insert into hub.trainer_cost_base (month, data, updated_at) "
                f"values ('{month}', '{js}'::jsonb, now()) "
                f"on conflict (month) do update set data = excluded.data, updated_at = now();"
            )
            loaded.append(month)
    if not stmts:
        sys.exit("No month data found in the file (keys must be month names with non-null data).")

    stmts.append("select count(*) as months from hub.trainer_cost_base;")
    res = run_sql("\n".join(stmts), token)
    print(f"Loaded {len(loaded)} month(s) from {fn}: {', '.join(loaded)}")
    print(f"hub.trainer_cost_base now holds {res[-1]['months']} month(s) total.")

    # The Command Centre cost-base page (/m/sygma-soldo/cost-base) was REMOVED on 2026-06-14 —
    # the Trainer Cost Base lives on the Sygma Platform (/hub/cost-base) only now, which this
    # script already loaded above. (costbase-cc-publish.py is retired, no longer called.)


if __name__ == "__main__":
    main()