#!/usr/bin/env python3
"""
migration_ledger.py — append-only JSONL ledger for the Sygma Hub build.

Used by every per-phase build script to record what was migrated, when, and
to where. Enables idempotent re-runs (check_already_migrated() skips items
that already have an entry) and provides an audit trail independent of Drive
metadata.

Default ledger path:
    Projects/SY-Sygma-Hub-Build/files/migration-ledger.jsonl

Entry schema (append-only, one JSON object per line):
    {
      "ts": "2026-05-12T14:23:00Z",        # ISO 8601 UTC, when this op happened
      "phase": "C-eus-cat1-pilot",         # which build phase
      "operator": "claude-...",            # who ran it
      "action": "copied" | "shortcut-source" | "shortcut" | "skipped" | "superseded",
      "src_drive": "Sygma Trainers",       # human-readable source drive
      "src_drive_id": "0AP9_VgbvNGyEUk9PVA",
      "src_id": "1xxx",                    # source file ID (the canonical source on the OLD drive)
      "src_path": "Awarding.../EUSR Course Agenda.docx",
      "src_size": 12345,
      "src_modifiedTime": "2025-09-22T...",
      "tgt_drive": "Sygma Hub",            # always Sygma Hub during this build
      "tgt_drive_id": "0APzpyHHfvUyIUk9PVA",
      "tgt_id": "1yyy",                    # for action=copied: the new file ID
                                           # for action=shortcut: the shortcut's ID
                                           # for action=shortcut-source: the canonical's ID inside _Common/
                                           # for action=superseded: the canonical it deferred to
                                           # for action=skipped: null
      "tgt_path": "Courses/EUS CAT1/2. Agenda & Lesson Plan/EUSR Course Agenda.docx",
      "notes": ""                          # optional free text (e.g. skip reason, supersedes by, etc.)
    }

CLI usage:
    python3 migration_ledger.py append <json-string>     # append a single entry
    python3 migration_ledger.py read [PHASE]             # dump all entries (filter by phase)
    python3 migration_ledger.py count                    # totals per phase + per action
    python3 migration_ledger.py find-src SRC_ID          # find entries by source ID
    python3 migration_ledger.py check SRC_ID             # exit 0 if migrated, 1 if not

Library usage:
    from migration_ledger import Ledger
    ledger = Ledger()                          # default vault path
    ledger.append({...})
    if not ledger.check_already_migrated(src_id):
        # do the migration
        ledger.append(make_entry(...))
"""

import json
import os
import sys
import datetime
from collections import Counter
from typing import Iterator, Optional


# Default ledger location — derived from this script's own location so it works
# both on Pete's Mac (~/Second Brain/...) and inside any Cowork sandbox mount.
_HERE = os.path.dirname(os.path.abspath(__file__))                       # .../Library/processes/scripts
_VAULT_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))     # .../Second Brain
DEFAULT_LEDGER_PATH = os.path.join(
    _VAULT_ROOT, "Projects", "SY-Sygma-Hub-Build", "files", "migration-ledger.jsonl"
)

# Drive-name → drive-id quick lookup for known shared drives we touch in this build.
KNOWN_DRIVES = {
    "Sygma Hub": "0APzpyHHfvUyIUk9PVA",
    "Sygma Office": "0AHJWd6QBeXtAUk9PVA",
    "Sygma Trainers": "0AP9_VgbvNGyEUk9PVA",
    "Sygma Mala": "0ANYL9DOJQtmQUk9PVA",
    "External Sygma Solutions": "0AOTm_FPU_iRmUk9PVA",
}

VALID_ACTIONS = {"copied", "shortcut-source", "shortcut", "skipped", "superseded", "created", "folder-created"}
# 'created' = fresh file authored on Sygma Hub (no source migration; e.g. CLAUDE.md / per-folder READMEs / _course-info.md)
# 'folder-created' = fresh folder created on Sygma Hub


def utcnow_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def make_entry(
    *,
    phase: str,
    action: str,
    src_drive: str = "",
    src_id: str = "",
    src_path: str = "",
    tgt_path: Optional[str] = None,
    tgt_id: Optional[str] = None,
    tgt_drive: str = "Sygma Hub",
    src_size: Optional[int] = None,
    src_modified_time: Optional[str] = None,
    notes: str = "",
    operator: str = "claude-session",
) -> dict:
    """Build a well-formed ledger entry. All required fields are validated.
    For 'created' / 'folder-created' actions, src_* fields can be empty."""
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action '{action}'. Allowed: {sorted(VALID_ACTIONS)}")
    src_drive_id = KNOWN_DRIVES.get(src_drive, "")
    tgt_drive_id = KNOWN_DRIVES.get(tgt_drive, "")
    return {
        "ts": utcnow_iso(),
        "phase": phase,
        "operator": operator,
        "action": action,
        "src_drive": src_drive,
        "src_drive_id": src_drive_id,
        "src_id": src_id,
        "src_path": src_path,
        "src_size": src_size,
        "src_modifiedTime": src_modified_time,
        "tgt_drive": tgt_drive,
        "tgt_drive_id": tgt_drive_id,
        "tgt_id": tgt_id,
        "tgt_path": tgt_path,
        "notes": notes,
    }


class Ledger:
    """Append-only JSONL ledger, with cheap re-read on each query.

    For the volume we'll see (probably 5-50k entries total over the whole build)
    a flat JSONL with linear-scan queries is fine. If we ever need indexed
    access, build a sqlite cache that mirrors the JSONL.
    """

    def __init__(self, path: str = DEFAULT_LEDGER_PATH):
        self.path = path
        # Make sure parent dir exists (vault path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # Touch file if missing
        if not os.path.exists(self.path):
            open(self.path, "a", encoding="utf-8").close()

    def append(self, entry: dict) -> None:
        """Append one entry as a JSONL line. Atomic per call (single write)."""
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_all(self) -> Iterator[dict]:
        """Stream all entries (avoids loading everything into memory for big ledgers)."""
        with open(self.path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"Warning: malformed line {line_no} in ledger: {e}", file=sys.stderr)

    def find_by_src_id(self, src_id: str) -> list:
        return [e for e in self.read_all() if e.get("src_id") == src_id]

    def find_by_phase(self, phase: str) -> list:
        return [e for e in self.read_all() if e.get("phase") == phase]

    def find_by_tgt_path(self, tgt_path: str) -> list:
        return [e for e in self.read_all() if e.get("tgt_path") == tgt_path]

    def check_already_migrated(self, src_id: str) -> bool:
        """True if any entry for src_id has action != 'skipped'.
        Skipped items can be re-checked on a re-run (skip is reversible)."""
        for e in self.read_all():
            if e.get("src_id") == src_id and e.get("action") != "skipped":
                return True
        return False

    def stats(self) -> dict:
        phase_count = Counter()
        action_count = Counter()
        total = 0
        for e in self.read_all():
            phase_count[e.get("phase", "?")] += 1
            action_count[e.get("action", "?")] += 1
            total += 1
        return {
            "total_entries": total,
            "by_phase": dict(phase_count),
            "by_action": dict(action_count),
            "ledger_path": self.path,
        }


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _cli():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    ledger = Ledger()

    if cmd == "append":
        if len(args) < 2:
            print("Usage: migration_ledger.py append '<json-string>'"); sys.exit(1)
        entry = json.loads(args[1])
        ledger.append(entry)
        print(f"Appended to {ledger.path}: {entry.get('action')} {entry.get('src_id')} → {entry.get('tgt_id')}")

    elif cmd == "read":
        phase_filter = args[1] if len(args) > 1 else None
        n = 0
        for e in ledger.read_all():
            if phase_filter and e.get("phase") != phase_filter:
                continue
            print(json.dumps(e))
            n += 1
        print(f"\n({n} entries)", file=sys.stderr)

    elif cmd == "count" or cmd == "stats":
        s = ledger.stats()
        print(json.dumps(s, indent=2))

    elif cmd == "find-src":
        if len(args) < 2:
            print("Usage: migration_ledger.py find-src SRC_ID"); sys.exit(1)
        for e in ledger.find_by_src_id(args[1]):
            print(json.dumps(e, indent=2))

    elif cmd == "check":
        if len(args) < 2:
            print("Usage: migration_ledger.py check SRC_ID"); sys.exit(1)
        if ledger.check_already_migrated(args[1]):
            print(f"MIGRATED: {args[1]}")
            sys.exit(0)
        else:
            print(f"NOT MIGRATED: {args[1]}")
            sys.exit(1)

    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)


if __name__ == "__main__":
    _cli()
