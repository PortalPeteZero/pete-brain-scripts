#!/usr/bin/env python3
"""dashboard-sync.py — vault → Command Centre health-module JSON mirror + git push.

Single canonical sync from the vault's authoritative JSON copies into the
command-centre clone (repointed 2026-06-11 — the health dashboard was ported
into the Command Centre at commandcentre.info/m/health; old repo
pete-health-dashboard archived), then optional git commit + push so Vercel
auto-deploys.

Usage:
  python3 dashboard-sync.py                # sync zones + all feedback, commit + push
  python3 dashboard-sync.py --zones        # zones only
  python3 dashboard-sync.py --feedback     # feedback only
  python3 dashboard-sync.py --dry-run      # show what would be done, no writes
  python3 dashboard-sync.py --no-push      # mirror + commit but skip push

Vault sources (canonical):
  Personal/passion-fit/coaching/data/training-zones.json
  Personal/passion-fit/coaching/feedback/data/*.json

Dashboard repo destinations:
  data/training-zones.json
  data/coaching/*.json

Notes:
- Uses the same git rebase-first pattern as garmin-daily-pull's push_dashboard
  to avoid non-fast-forward rejections when the cron clone has shared writers.
- Idempotent — if a JSON is byte-identical to the destination, no copy, no commit.
- Per Library/lessons/2026-05-25-garmin-daily-pull-must-rebase-before-push.

Author: 2026-05-28
"""

from __future__ import annotations
import argparse
import filecmp
import shutil
import subprocess
import sys
from pathlib import Path
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = Path(VAULT)
DASHBOARD = Path.home() / "code/command-centre"  # repointed 2026-06-11 (was code/pete-health-dashboard, archived)

SRC_ZONES = VAULT / "Personal/passion-fit/coaching/data/training-zones.json"
DST_ZONES = DASHBOARD / "data/training-zones.json"

SRC_FEEDBACK_DIR = VAULT / "Personal/passion-fit/coaching/feedback/data"
DST_FEEDBACK_DIR = DASHBOARD / "data/coaching"


def log(msg: str) -> None:
    print(f"  {msg}")


def copy_if_changed(src: Path, dst: Path, dry: bool) -> bool:
    """Copy src→dst if content differs. Returns True if a copy happened."""
    if not src.exists():
        log(f"SKIP: source missing: {src}")
        return False
    if dst.exists() and filecmp.cmp(src, dst, shallow=False):
        log(f"unchanged: {dst.relative_to(DASHBOARD)}")
        return False
    log(f"{'WOULD COPY' if dry else 'copying'}: {src.relative_to(VAULT)} → {dst.relative_to(DASHBOARD)}")
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return True


def sync_zones(dry: bool) -> bool:
    print("== Zones ==")
    return copy_if_changed(SRC_ZONES, DST_ZONES, dry)


def sync_feedback(dry: bool) -> bool:
    print("== Feedback ==")
    if not SRC_FEEDBACK_DIR.exists():
        log(f"no feedback dir yet: {SRC_FEEDBACK_DIR}")
        return False
    any_changed = False
    for src in sorted(SRC_FEEDBACK_DIR.glob("*.json")):
        dst = DST_FEEDBACK_DIR / src.name
        if copy_if_changed(src, dst, dry):
            any_changed = True
    return any_changed


def git_run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(DASHBOARD), *args], check=check, capture_output=True, text=True)


def git_push(commit_msg: str, dry: bool) -> str:
    if dry:
        log("WOULD: git fetch + pull --rebase + commit + push")
        return "dry-run"
    log("git fetch + pull --rebase --autostash")
    git_run(["fetch", "origin", "main"])
    git_run(["pull", "--rebase", "--autostash", "origin", "main"])

    status = git_run(["status", "--porcelain"]).stdout.strip()
    if not status:
        log("no changes to commit")
        return "no-changes"

    log("git add data/")
    git_run(["add", "data/"])
    log(f"git commit -m '{commit_msg}'")
    git_run(["commit", "-m", commit_msg])
    log("git push origin main")
    try:
        git_run(["push", "origin", "main"])
    except subprocess.CalledProcessError as e:
        log(f"PUSH FAILED: {e.stderr.strip()[:200]}")
        return "FAILED"
    return "pushed"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zones", action="store_true", help="sync zones only")
    ap.add_argument("--feedback", action="store_true", help="sync feedback only")
    ap.add_argument("--dry-run", action="store_true", help="show plan, no writes")
    ap.add_argument("--no-push", action="store_true", help="mirror + commit only, skip push")
    args = ap.parse_args()

    do_zones = args.zones or not args.feedback
    do_feedback = args.feedback or not args.zones

    changed = False
    if do_zones:
        changed = sync_zones(args.dry_run) or changed
    if do_feedback:
        changed = sync_feedback(args.dry_run) or changed

    if not changed:
        print("\nNothing to sync. Done.")
        return 0

    if args.no_push:
        log("--no-push set; skipping git step")
        return 0

    print("\n== Git ==")
    summary_parts = []
    if do_zones:
        summary_parts.append("zones")
    if do_feedback:
        summary_parts.append("coaching feedback")
    msg = f"data: vault sync ({'+'.join(summary_parts)})"
    status = git_push(msg, args.dry_run)
    print(f"\nresult: {status}")
    return 0 if status in ("pushed", "no-changes", "dry-run") else 2


if __name__ == "__main__":
    sys.exit(main())