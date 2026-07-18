#!/usr/bin/env python3
"""drive-path-rebuild.py — rebuild drive_files.path from the parent_id tree (the truth).

WHY THIS EXISTS
  drive_files.path is DENORMALISED — a text string built from the parent chain at index time.
  Nothing recomputes it. So:
    • Renaming a folder strands every descendant on the old path. They never self-heal, because
      the descendants themselves did not change and so are never re-upserted by the changes-watch.
      (18 Jul 2026: renaming one project folder stranded 19 rows. Renaming 'Course Records' in the
      Sygma Hub would strand 56,912.)
    • Moving a file/folder has the same effect.
    • A subtree indexed without full ancestry records a path relative to the subtree, not the drive.
  parent_id + name ARE authoritative, and coverage is effectively total (151,858 of 151,860 rows),
  so the correct path is always derivable. This rebuilds it.

REPORT-ONLY by default (the house pattern). --apply writes. --json for machine output.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/drive-path-rebuild.py            # report drift
  VAULT=/tmp/pbs python3 /tmp/pbs/drive-path-rebuild.py --json
  VAULT=/tmp/pbs python3 /tmp/pbs/drive-path-rebuild.py --apply    # fix it
"""
import json, os, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# Anchor: rows whose parent is NOT itself indexed = top-level in their drive. Recurse down from there.
TREE = ("WITH RECURSIVE tree AS ("
        " SELECT d.drive_file_id, d.name::text AS computed FROM drive_files d"
        " LEFT JOIN drive_files p ON p.drive_file_id = d.parent_id WHERE p.drive_file_id IS NULL"
        " UNION ALL"
        " SELECT c.drive_file_id, t.computed || '/' || c.name FROM drive_files c"
        " JOIN tree t ON c.parent_id = t.drive_file_id)")


def q(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       env={**os.environ, "VAULT": VAULT}, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        sys.stderr.write(f"[drive-path-rebuild] query failed: {r.stderr[:200]}\n")
        return None                      # None = errored, NEVER an empty result
    try:
        return json.loads(r.stdout)
    except Exception:
        return []


def main():
    as_json, apply = "--json" in sys.argv, "--apply" in sys.argv

    counts = q(TREE + " SELECT count(*) AS reachable,"
                      " count(*) FILTER (WHERE d.path IS DISTINCT FROM t.computed) AS drifted"
                      " FROM tree t JOIN drive_files d ON d.drive_file_id = t.drive_file_id")
    total = q("SELECT count(*) AS n FROM drive_files")
    if counts is None or total is None:
        msg = "drive-path-rebuild: a lookup ERRORED — aborting, nothing changed. Re-run."
        if as_json:   # must stay valid JSON, and must NOT read as 0 gaps — same rule as the locator
            print(json.dumps({"gaps": 1, "gap_types": ["aborted"],
                              "findings": [{"rule": "aborted", "subject": "drive-path-rebuild",
                                            "detail": msg, "severity": "high"}],
                              "info": [], "aborted": True}, indent=1))
        else:
            print(msg)
        sys.exit(99)

    reachable, drifted = counts[0]["reachable"], counts[0]["drifted"]
    unreachable = total[0]["n"] - reachable

    samples = []
    if drifted:
        s = q(TREE + " SELECT d.drive, d.path AS stored, t.computed FROM tree t"
                     " JOIN drive_files d ON d.drive_file_id = t.drive_file_id"
                     " WHERE d.path IS DISTINCT FROM t.computed LIMIT 8")
        samples = s or []

    if apply and drifted:
        res = q(TREE + " UPDATE drive_files d SET path = t.computed FROM tree t"
                       " WHERE d.drive_file_id = t.drive_file_id AND d.path IS DISTINCT FROM t.computed")
        if res is None:
            print("drive-path-rebuild: the UPDATE errored — nothing written. Re-run.")
            sys.exit(99)
        after = q(TREE + " SELECT count(*) FILTER (WHERE d.path IS DISTINCT FROM t.computed) AS drifted"
                         " FROM tree t JOIN drive_files d ON d.drive_file_id = t.drive_file_id")
        remaining = after[0]["drifted"] if after else "?"
        print(f"repaired {drifted} path(s); remaining drift: {remaining}")
        sys.exit(0)

    if as_json:
        print(json.dumps({"gaps": drifted,
                          "gap_types": (["path-drift"] if drifted else []),
                          "findings": [{"rule": "path-drift", "subject": s["stored"],
                                        "detail": f"tree says {s['computed']}", "severity": "medium"}
                                       for s in samples],
                          "info": [{"subject": "coverage",
                                    "detail": f"{reachable} rows reachable from the tree, {unreachable} unreachable"}]},
                         indent=1))
    else:
        print(f"=== drive_files path check — {drifted} drifted / {reachable} reachable ===")
        for s in samples:
            print(f"  [{s['drive']}]\n     stored:   {s['stored']}\n     computed: {s['computed']}")
        if unreachable:
            print(f"  NOTE: {unreachable} row(s) not reachable from any root — not checked.")
        if not drifted:
            print("  clean — every stored path matches the folder tree.")
        else:
            print("\n  run with --apply to rebuild them from the tree")
    sys.exit(0)


if __name__ == "__main__":
    main()
