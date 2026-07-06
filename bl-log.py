#!/usr/bin/env python3
"""bl-log.py -- the Backlink Engine capture helper (the te-log for backlinks).

ONE commit point for a backlink email touch, writing every home in lockstep so nothing drifts:
  1. The shared Google Sheet  -- the SSOT for Appear Online placements (fill live URLs / add rows / set status)
  2. CC public.work_log       -- the log line (entity Sygma, area backlinks)
  3. Gmail                     -- FILE the thread (label Suppliers/SY-AppearOnline + archive)
Then it runs bl-sheet-sync so bl.work_items + the CC mirror page (/m/sygma-backlink-tracker) reflect it at once.

This is the systematic handler triage routes an Appear Online / backlink email to -- the same shape as the
Enquiry Engine's te-log. Claude reads the email and builds the payload; bl-log applies it the same way every time.
Dry-run by DEFAULT; --apply writes. --manifest makes a run reversible.

Usage:
  VAULT=/tmp/pbs python3 bl-log.py --in touch.json                 # dry run (preview)
  VAULT=/tmp/pbs python3 bl-log.py --in touch.json --apply         # apply
  cat touch.json | VAULT=/tmp/pbs python3 bl-log.py --apply --manifest /tmp/bl-manifest.jsonl

Payload shape:
{
  "thread_id": "gmail-thread-id",            # optional; FILED on --apply (label SY-AppearOnline + archive)
  "placements": [                            # updates to the sheet (match on publisher; append if new)
     {"publisher": "The Boss Magazine", "live_url": "https://...", "status": "live"},
     {"publisher": "New Site", "date": "2026-07-10", "dr": 40, "article": "Title",
      "target_page": "cable avoidance training", "status": "submitted", "notes": "..."}
  ],
  "worklog": "June placements landed live: Boss, BDC, Build Review",   # optional work_log title
  "file": true                               # label SY-AppearOnline + archive the thread (default true if thread_id)
}
"""
import os, sys, json, re, subprocess, importlib.util, urllib.request, urllib.parse
from datetime import datetime, timezone

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SECRETS = f"{VAULT}/Library/processes/secrets"
SHEET_ID = "1XMMJDuvx95K2nV-jE7VjAr3UnaE1pSd-OnhpXZsigCM"
TAB = "Placements"
DATA_START_ROW = 4  # row1 title, row2 subtitle, row3 header, data from row 4
AO_LABEL = "Suppliers/SY-AppearOnline"

def _load(mod, path):
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
sheets = _load("sheets_api", f"{VAULT}/sheets-api.py")

def _norm(s): return re.sub(r"\s+", " ", (s or "").strip().lower())
def _domain(u): return re.sub(r"^https?://(www\.)?", "", u).split("/")[0] if u else ""
def _hyperlink(u): return f'=HYPERLINK("{u}","{_domain(u)}")' if u else ""

# ---- Sheet (SSOT) ----------------------------------------------------------------------------
COLS = {"date": "A", "publisher": "B", "dr": "C", "article": "D",
        "live_url": "E", "target_page": "F", "status": "G", "notes": "H"}

def read_placements():
    rng = urllib.parse.quote(f"{TAB}!A{DATA_START_ROW}:H200")
    rows = sheets.api("GET", f"/{SHEET_ID}/values/{rng}", {"valueRenderOption": "FORMATTED_VALUE"}).get("values", [])
    out = []
    for i, r in enumerate(rows):
        c = (r + [""] * 8)[:8]
        if not str(c[1]).strip():
            continue
        out.append({"row": DATA_START_ROW + i, "publisher": str(c[1]).strip(),
                    "target_page": str(c[5]).strip(), "status": str(c[6]).strip(),
                    "live_url": str(c[4]).strip()})
    return out

def plan_sheet(placements):
    """Return (updates, appends): updates=[(range,value,label)], appends=[[8 cells]] for new publishers."""
    existing = read_placements()
    updates, appends, notes = [], [], []
    for p in placements:
        pub = p.get("publisher")
        if not pub:
            continue
        match = [e for e in existing if _norm(e["publisher"]) == _norm(pub)]
        if p.get("target_page") and len(match) > 1:
            m2 = [e for e in match if _norm(e["target_page"]) == _norm(p["target_page"])]
            match = m2 or match
        if match:
            row = match[0]["row"]
            for field, col in COLS.items():
                if field == "publisher":
                    continue  # match key — never re-write it
                if field in p and p[field] not in (None, ""):
                    val = _hyperlink(p[field]) if field == "live_url" else str(p[field])
                    updates.append((f"{TAB}!{col}{row}", val, f"{pub} · {field} → {p[field]}"))
        else:
            appends.append([p.get("date", ""), pub, p.get("dr", ""), p.get("article", ""),
                            _hyperlink(p.get("live_url", "")), p.get("target_page", ""),
                            p.get("status", "submitted"), p.get("notes", "")])
            notes.append(f"{pub} (new row)")
    return updates, appends, notes

def write_sheet(updates, appends):
    if updates:
        body = {"valueInputOption": "USER_ENTERED",
                "data": [{"range": rng, "values": [[val]]} for rng, val, _ in updates]}
        sheets.api("POST", f"/{SHEET_ID}/values:batchUpdate", body=body)
    for row in appends:
        sheets.append_row(SHEET_ID, TAB, json.dumps(row))

# ---- Gmail filing ----------------------------------------------------------------------------
def file_thread(thread_id):
    g = _load("gmail_api", f"{VAULT}/gmail-api.py").GmailAPI()
    names = {l["name"]: l["id"] for l in g.list_labels()}
    add = [names[AO_LABEL]] if AO_LABEL in names else []
    g.modify_thread(thread_id, add=add or None, remove=["INBOX"])
    print(f"   • filed thread {thread_id} → {AO_LABEL} + archived")

# ---- work_log --------------------------------------------------------------------------------
def log_worklog(title, thread_id):
    cmd = ["python3", f"{VAULT}/worklog.py", "--entity", "Sygma",
           "--property", "Sygma Solutions Website", "--project", "SY-Website",
           "--area", "backlinks", "--title", title, "--actor", "claude",
           "--source-ref", f"appear-online-email:{thread_id or 'manual'}"]
    r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    print(f"   • work_log: {title}" + ("" if r.returncode == 0 else f"  ⚠ {r.stderr.strip()[:200]}"))

# ---- main ------------------------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    if "--sheet-id" in args:
        global SHEET_ID
        SHEET_ID = args[args.index("--sheet-id") + 1]
    payload = None
    if "--in" in args:
        payload = json.load(open(args[args.index("--in") + 1]))
    else:
        raw = sys.stdin.read().strip()
        if raw:
            payload = json.loads(raw)
    if not payload:
        print("no payload (use --in file.json or pipe JSON)"); sys.exit(1)
    manpath = args[args.index("--manifest") + 1] if "--manifest" in args else None
    manifest = open(manpath, "a") if (apply and manpath) else None

    placements = payload.get("placements", [])
    updates, appends, newnotes = plan_sheet(placements)
    thread_id = payload.get("thread_id")
    do_file = payload.get("file", bool(thread_id))
    worklog = payload.get("worklog")

    mode = "APPLY" if apply else "DRY-RUN (nothing written; add --apply)"
    print(f"=== bl-log {mode} ===")
    print(f"Sheet updates ({len(updates)}):")
    for _, _, label in updates: print(f"   ~ {label}")
    print(f"New rows appended ({len(appends)}): " + (", ".join(newnotes) or "none"))
    print(f"File thread: {thread_id + ' → ' + AO_LABEL + ' + archive' if (do_file and thread_id) else 'no'}")
    print(f"work_log: {worklog or '(none)'}")

    if not apply:
        print("\nDry run only. Re-run with --apply to write."); return

    if updates or appends:
        write_sheet(updates, appends)
        manifest and manifest.write(json.dumps({"kind": "sheet", "updates": len(updates), "appends": len(appends)}) + "\n")
        print(f"   • sheet written: {len(updates)} cells, {len(appends)} new rows")
    if worklog:
        log_worklog(worklog, thread_id)
        manifest and manifest.write(json.dumps({"kind": "worklog", "title": worklog}) + "\n")
    if do_file and thread_id:
        try:
            file_thread(thread_id)
            manifest and manifest.write(json.dumps({"kind": "file", "thread_id": thread_id}) + "\n")
        except Exception as e:
            print(f"   ⚠ could not file thread: {e}")
    # propagate to bl.work_items + the CC mirror page immediately
    r = subprocess.run(["python3", f"{VAULT}/bl-sheet-sync.py"], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    print(f"   • synced to CC: {tail}")
    if manifest: manifest.close()
    print("Done.")

if __name__ == "__main__":
    main()
