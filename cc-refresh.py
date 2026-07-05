#!/usr/bin/env python3
"""cc-refresh.py — refresh the CC's snapshot data in one command. While the crons are frozen (→ Railway,
Part H), the CC's derived/snapshot tables (data-map · Pete's task mirror) are
hand-run. This runs all of them in sequence so "keep the CC current" is one command. At Part H this is
the job Railway schedules.

Runs: cc-cron.py status · cc-data-map-sync.py · cc-knowledge-sync.py (the one embedder)
Usage: python3 cc-refresh.py [--dry]
"""
import subprocess, sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
DRY = "--dry" in sys.argv
STEPS = [
    ("cron registry → public.crons live status (+ timeline)", "cc-cron.py", ["status"]),
    ("data-map → public.data_map", "cc-data-map-sync.py", []),
    ("knowledge sync: ingest changed docs + embed (keeps search current)", "cc-knowledge-sync.py", []),
]
print(f"cc-refresh — {len(STEPS)} steps{' (dry)' if DRY else ''}\n")
fails = 0
for label, script, extra in STEPS:
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        print(f"  ⚠ {label}: {script} not found — skipped"); continue
    args = ["python3", path] + extra + (["--dry"] if DRY and script not in ("cc-cron.py", "cc-knowledge-sync.py") else [])
    print(f"▶ {label}")
    r = subprocess.run(args, capture_output=True, text=True)
    out = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode == 0:
        print(f"  ✓ {out}\n")
    else:
        fails += 1
        print(f"  ✗ FAILED: {(r.stderr.strip().splitlines() or [out])[-1][:160]}\n")
print(f"done — {len(STEPS) - fails}/{len(STEPS)} ok" + (f" · {fails} failed" if fails else ""))
sys.exit(1 if fails else 0)
