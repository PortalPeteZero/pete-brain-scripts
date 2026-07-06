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
# secrets: GOOGLE_SA_JSON
# schedule: 0 6 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import json, os, re, sys, importlib.util, urllib.request, urllib.parse
import html as _html
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
_SECRETS = (Path(os.environ["VAULT"]) / "Library/processes/secrets") if os.environ.get("VAULT") else (SCRIPT_DIR.parent / "secrets")
KEYS = json.load(open(_SECRETS / "command-centre-supabase-keys.json"))
SRK = KEYS["service_role_key"]; BASE = KEYS["url"] + "/rest/v1"

DEFAULT_SHEET_ID = "1XMMJDuvx95K2nV-jE7VjAr3UnaE1pSd-OnhpXZsigCM"
TRACKER_URL = "https://docs.google.com/spreadsheets/d/1XMMJDuvx95K2nV-jE7VjAr3UnaE1pSd-OnhpXZsigCM/edit"
SHEET_RANGE = "Placements!A4:H200"  # data rows only (row1 title, row2 subtitle, row3 header)
CC_MODULE_KEY = "sygma-backlink-tracker"  # CC module that renders the mirror (module_content.html)

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

def render_tracker_html(rows):
    """Faithful HTML replica of the shared tracker sheet (same columns + look) for the CC module."""
    def esc(x): return _html.escape(str(x)) if x not in (None, "") else ""
    def domain(u): return re.sub(r"^https?://(www\.)?", "", u).split("/")[0] if u else ""
    def chip(s):
        s = (s or "").lower()
        if s in ("live", "crawled", "counted"): return f'<span class="chip live">{esc(s.title())}</span>'
        if s == "submitted": return '<span class="chip sub">Submitted</span>'
        return f'<span class="chip other">{esc(s.title())}</span>' if s else ""
    def urlcell(r):
        u = r.get("article_url")
        return f'<a href="{esc(u)}" target="_blank" rel="noopener">{esc(domain(u))} &#8599;</a>' if u else '<span class="need">URL needed</span>'
    trs = "".join(
        "<tr>"
        f'<td class="c">{esc(r.get("date"))}</td><td>{esc(r.get("publisher"))}</td>'
        f'<td class="c">{esc(r.get("dr") or "")}</td><td>{esc(r.get("title"))}</td>'
        f'<td>{urlcell(r)}</td><td>{esc(r.get("target_page"))}</td>'
        f'<td class="c">{chip(r.get("status"))}</td><td>{esc(r.get("notes"))}</td></tr>'
        for r in rows)
    live_n = sum(1 for r in rows if (r.get("status") or "").lower() in ("live", "crawled", "counted"))
    need_n = sum(1 for r in rows if not r.get("article_url"))
    stamp = datetime.now(timezone.utc).strftime("%-d %b %Y %H:%M UTC")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Sygma Backlink Placement Tracker</title><style>
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1f2733;background:#f6f8fa}}
.wrap{{max-width:1180px;margin:0 auto;padding:16px}}
.banner{{background:#0B3C5D;color:#fff;border-radius:10px 10px 0 0;padding:15px 20px}}
.banner h1{{margin:0;font-size:18px;font-weight:700}}.banner p{{margin:4px 0 0;font-size:12px;color:#b9c9d6}}
.mirror{{background:#eef3f6;border:1px solid #d6dee6;border-top:none;padding:8px 20px;font-size:12px;color:#5a6a78}}
.mirror a{{color:#2563eb;font-weight:600;text-decoration:none}}
table{{width:100%;border-collapse:collapse;background:#fff;font-size:13px;border:1px solid #d6dee6;border-top:none}}
th{{background:#17547A;color:#fff;text-align:left;padding:9px 11px;font-weight:600;font-size:12px}}
td{{padding:9px 11px;border-top:1px solid #e6ecf1;vertical-align:top}}
td.c,th.c{{text-align:center}}tr:nth-child(even) td{{background:#f7fafc}}
a{{color:#2563eb;text-decoration:none}}a:hover{{text-decoration:underline}}
.chip{{display:inline-block;padding:2px 9px;border-radius:11px;font-size:11px;font-weight:700;white-space:nowrap}}
.chip.live{{background:#e4f3e7;color:#1e7a34}}.chip.sub{{background:#fbebd2;color:#9a6400}}.chip.other{{background:#eef2f6;color:#55606c}}
.need{{display:inline-block;background:#fce3c2;color:#8a5a00;font-weight:700;padding:2px 9px;border-radius:6px;font-size:12px;white-space:nowrap}}
</style></head><body><div class="wrap">
<div class="banner"><h1>Sygma Solutions &nbsp;&middot;&nbsp; Backlink Placement Tracker</h1>
<p>Live shared record, maintained with Appear Online &nbsp;&middot;&nbsp; {live_n} live &nbsp;&middot;&nbsp; {need_n} awaiting live URL &nbsp;&middot;&nbsp; green = live, amber = awaiting URL</p></div>
<div class="mirror">Mirrors the shared Google Sheet, refreshed daily (last sync {stamp}). &nbsp;<a href="{TRACKER_URL}" target="_blank">Open the live sheet &#8599;</a></div>
<table><thead><tr><th class="c">Date</th><th>Publisher</th><th class="c">DR</th><th>Article</th><th>Live URL</th><th>Target page</th><th class="c">Status</th><th>Notes</th></tr></thead>
<tbody>{trs}</tbody></table></div></body></html>"""

def publish_cc_module(rows):
    """Upsert the rendered mirror into the CC module_content (public schema) for CC_MODULE_KEY."""
    html_doc = render_tracker_html(rows)
    body = {"module_key": CC_MODULE_KEY, "html": html_doc, "updated_at": datetime.now(timezone.utc).isoformat()}
    _sb("POST", "module_content", body=body, profile="public",
        prefer="resolution=merge-duplicates,return=minimal")
    return len(html_doc)

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

    # SCOPED mirror: only the Appear-Online-sourced rows are owned by the sheet.
    # Rows from other actors (jane's directories, sygma earned/pipeline) are LEFT ALONE —
    # bl.work_items is the broad ledger; the sheet is SSOT for Appear Online placements only.
    kept = len(snap) - sum(1 for r in snap if r.get("source_ref") == "appear-online-sheet")
    _sb("DELETE", "work_items", params="?source_ref=eq.appear-online-sheet")
    _sb("POST", "work_items", body=parsed, prefer="return=minimal")
    after = json.loads(_sb("GET", "work_items", params="?select=id&limit=1000"))
    print(f"Mirrored: {len(parsed)} Appear Online rows from the sheet; {kept} other-actor rows preserved. "
          f"bl.work_items now {len(after)} rows.")

    # regenerate the CC mirror page (module_content.html) so /m/sygma-backlink-tracker matches the sheet
    n = publish_cc_module(parsed)
    print(f"CC mirror page refreshed: module_content[{CC_MODULE_KEY}] ({n} bytes). Done.")

if __name__ == "__main__":
    main()
