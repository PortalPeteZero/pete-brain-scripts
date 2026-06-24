#!/usr/bin/env python3
"""
payroll-backup.py — nightly export of the owner-private CC `payroll` schema to a dated snapshot
in the Sygma Solutions Private Drive.

Once the Payroll app becomes the live source (the Payroll Master gsheet steps down to a backup),
it has moved off the gsheet's automatic version history — so this dated JSON snapshot guarantees it
is never a single point of failure (household-finance plan, Phase 5). Owner-private only: it writes
to `Pete & Mic / Sygma Solutions Private / Payroll / backups/`, NEVER the vault or the Hub.

One file per day (re-runs overwrite the day's snapshot). Keeps the last ~120 daily snapshots.
"""
# drive-cloudstorage-allowed: this backup writes a dated JSON snapshot directly into the owner-private
# `Sygma Solutions Private/Payroll/backups/` Drive folder. Filesystem-shape is intentional and correct
# here — the snapshot must land in that exact synced owner-private location; drive-api.py is for API
# reads of synced caches, not for placing an owner-private backup file. See [[external-service-routing]].
import json, os, sys, datetime, urllib.request, urllib.error
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
TOKEN = open(os.path.join(VAULT, "Library/processes/secrets/supabase-token")).read().strip()
REF = "zhexcaflgahdcbzvbyfq"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
      "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
DRIVE = "/Users/peterashcroft/Library/CloudStorage/GoogleDrive-pete.ashcroft@sygma-solutions.com/Shared drives/Pete & Mic/Sygma Solutions Private/Payroll/backups"
TABLES = ["staff", "payroll_month", "payroll_fy", "disciplinary", "edit_audit"]
KEEP = 120


def q(sql):
    req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{REF}/database/query",
                                 data=json.dumps({"query": sql}).encode(), method="POST", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    if not os.path.isdir(os.path.dirname(DRIVE)):
        print(f"payroll-backup: owner-private Drive path not present ({os.path.dirname(DRIVE)}) — Drive Desktop offline? Skipping.", file=sys.stderr)
        return 2
    snap = {"generated": datetime.datetime.now().isoformat(), "source": "Command Centre payroll schema (zhexcaflgahdcbzvbyfq)"}
    total = 0
    for t in TABLES:
        rows = q(f"select * from payroll.{t} order by 1")
        snap[t] = rows
        total += len(rows)
    os.makedirs(DRIVE, exist_ok=True)
    today = datetime.date.today().isoformat()
    path = os.path.join(DRIVE, f"{today}-payroll-snapshot.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, default=str, ensure_ascii=False)
    # prune old snapshots (keep newest KEEP)
    snaps = sorted([x for x in os.listdir(DRIVE) if x.endswith("-payroll-snapshot.json")])
    for old in snaps[:-KEEP]:
        try:
            os.remove(os.path.join(DRIVE, old))
        except OSError:
            pass
    print(f"payroll-backup: wrote {path} ({total} rows across {len(TABLES)} tables; {len(snaps[-KEEP:])} snapshots retained)")
    return 0


if __name__ == "__main__":
    sys.exit(main())