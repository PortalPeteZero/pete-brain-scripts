#!/usr/bin/env python3
"""bl-sheet-sync.py — mirror the shared Sygma backlink tracker (Google Sheet = SSOT) into bl.work_items.

One-way sync: the Google Sheet is the single source of truth (Appear Online + Pete edit it);
the CC table bl.work_items is a read-only mirror that feeds the backlink reports + disavow system.
Each run: snapshot bl.work_items -> read the sheet -> rewrite the table to match the sheet.

Safety:
  - SNAPSHOT-GUARDED: every run stores the pre-sync table to reports.snapshots (key bl-work-items-presync)
    before touching anything, so a bad sheet edit is always recoverable.
  - ZERO-ROW GUARD: if the sheet parses to 0 placement rows (a failed/empty read), the sync ABORTS
    and leaves the table untouched — it can never blank the CC off an empty read.

Routing / where this lives: Library/processes/seo/sygma-backlink-tracker-routing.md
Sheet: External Sygma Solutions / Appear Online External (appProperty purpose=sygma-backlink-tracker).

Usage:
  VAULT=/tmp/pbs python3 bl-sheet-sync.py            # sync (sheet -> bl.work_items)
  VAULT=/tmp/pbs python3 bl-sheet-sync.py --dry-run  # parse + diff only, write nothing
  VAULT=/tmp/pbs python3 bl-sheet-sync.py --sheet-id ID   # override the tracker id
"""
# CRON-META
# what: Mirror the shared Sygma backlink tracker (Google Sheet SSOT) into bl.work_items
# why: keep the CC + backlink reports in step with the placement list Appear Online maintains
# reads: Google Sheet (Sygma Backlinks tracker); bl.work_items (CC)
# writes: bl.work_items (CC); reports.snapshots key bl-work-items-presync
# entity: sygma
# schedule: 0 6 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import json, os, re, sys, importlib.util, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
_SECRETS = (Path(os.environ["VAULT"]) / "Library/processes/secrets") if os.environ.get("VAULT") else (SCRIPT_DIR.parent / "secrets")
KEYS = json.load(open(_SECRETS / "command-centre-supabase-keys.json"))
SRK = KEYS["service_role_key"]; BASE = KEYS["url"] + "/rest/v1"

DEFAULT_SHEET_ID = "1XMMJDuvx95K2nV-jE7VjAr3UnaE1pSd-OnhpXZsigCM"
SHEET_RANGE = "Placements!A4:H200"  # data rows only (row1 title, row2 subtitle, row3 header)

# load the sheets-api helper by path (hyphenated filename)
_spec = importlib.util.spec_from_file_location("sheets_api", str(SCRIPT_DIR / "sheets-api.py"))
sheets = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sheets)

_HL = re.compile(r'=HYPERLINK\(\s*"([^"]+)"', re.I)

def _sb(method, path, body=None, params="", profile="bl", prefer="return=minimal"):
    headers = {"apikey": SRK, "Authorization": f"Bearer {SRK}", "Content-Type": "application/json",
               "Content-Profile": profile, "Accept-Profile": profile, "Prefer": prefer}
    req = urllib.request.Request(f"{BASE}/{path}{params}", data=(json.dumps(body).encode() if body else None),
                                 method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=60).read()

def extract_url(cell):
    """From a Live URL cell (may be =HYPERLINK(...), a raw URL, 'URL needed', or blank) -> url or None."""
    if not cell: return None
    c = str(cell).strip()
    m = _HL.match(c)
    if m: return m.group(1).strip()
    if c.lower().startswith("http"): return c
    return None  # 'URL needed' / anything non-URL

def read_sheet(sheet_id):
    path = f"/{sheet_id}/values/{urllib.parse.quote(SHEET_RANGE)}"
    # FORMATTED gives human-readable dates + labels; FORMULA gives the real HYPERLINK URLs.
    fmt = sheets.api("GET", path, {"valueRenderOption": "FORMATTED_VALUE"}).get("values", [])
    fml = sheets.api("GET", path, {"valueRenderOption": "FORMULA"}).get("values", [])
    out = []
    for i, row in enumerate(fmt):
        cells = (row + [""] * 8)[:8]
        date, publisher, dr, article, live, target, status, notes = [str(x).strip() for x in cells]
        if not publisher:  # skip blank / spacer rows
            continue
        # pull the live URL from the FORMULA read (same row index), fall back to formatted cell
        fml_row = (fml[i] + [""] * 8)[:8] if i < len(fml) else [""] * 8
        live = str(fml_row[4]).strip() or live
        out.append({
            "date": date or None,
            "actor": "appear-online",
            "kind": "placement",
            "publisher": publisher,
            "dr": int(dr) if dr.isdigit() else None,
            "title": article or None,
            "article_url": extract_url(live),
            "target_page": target or None,
            "status": (status or "").lower() or None,
            "source_ref": "appear-online-sheet",
            "notes": notes or None,
        })
    return out

def snapshot():
    rows = json.loads(_sb("GET", "work_items", params="?select=*&limit=1000"))
    payload = {"count": len(rows), "rows": rows, "captured_at": datetime.now(timezone.utc).isoformat()}
    _sb("POST", "snapshots", profile="reports", prefer="return=minimal",
        body={"report_key": "bl-work-items-presync",
              "period_date": datetime.now(timezone.utc).date().isoformat(),
              "payload": payload})
    return rows

def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    sheet_id = DEFAULT_SHEET_ID
    if "--sheet-id" in argv:
        sheet_id = argv[argv.index("--sheet-id") + 1]

    parsed = read_sheet(sheet_id)
    print(f"Sheet parsed: {len(parsed)} placement rows ({sum(1 for r in parsed if r['article_url'])} with live URL, "
          f"{sum(1 for r in parsed if not r['article_url'])} awaiting).")

    # ZERO-ROW GUARD
    if not parsed:
        print("ABORT: sheet parsed to 0 rows — leaving bl.work_items untouched (guard against wiping off an empty read).")
        sys.exit(2)

    current = json.loads(_sb("GET", "work_items", params="?select=publisher,article_url,status&limit=1000"))
    print(f"CC bl.work_items currently: {len(current)} rows.")

    if dry:
        print("\n--dry-run — no writes. Parsed rows:")
        for r in parsed:
            print(f"  {r['publisher']:<42} | {r['status'] or '-':<10} | {r['article_url'] or 'URL needed'}")
        return

    # snapshot BEFORE any write
    snap = snapshot()
    print(f"Snapshot stored: {len(snap)} rows -> reports.snapshots/bl-work-items-presync.")

    # full mirror: clear the table, insert the sheet's rows
    _sb("DELETE", "work_items", params="?id=not.is.null")
    _sb("POST", "work_items", body=parsed, prefer="return=minimal")
    after = json.loads(_sb("GET", "work_items", params="?select=id&limit=1000"))
    print(f"Mirrored: bl.work_items now {len(after)} rows (== sheet). Done.")

if __name__ == "__main__":
    main()
