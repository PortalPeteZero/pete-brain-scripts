---
name: hub-reconcile
description: Daily 17:30 — Hub READMEs + hub-content-index from the Drive Changes delta (noise-filtered since 12 Jun), THEN sorts the delta itself: classifies new folders vs established conventions, updates the map, renames locked-convention violations, nudges repeat offenders, pulls vault mirrors. Digest = what was done, not a to-do list.
---

Run the Sygma Hub reconcile job, then sort what it found. This keeps the Hub's per-folder READMEs and the vault map (Library/processes/hub-content-index.md) current with what staff have added/edited/moved/deleted that day — and does the classification/filing work itself instead of leaving it for Pete. Full protocol: Library/processes/hub-maintenance.md.

EXECUTION — READ THIS FIRST. Do NOT run this via workspace bash (45s cap; this writes to Drive and can take longer). Use Desktop Commander:
1. Start it detached: mcp__Desktop_Commander__start_process running
   nohup python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/hub-reconcile.py" run > /tmp/hub-reconcile.log 2>&1 &
2. Poll /tmp/hub-reconcile.log (mcp__Desktop_Commander__read_file) every ~15s until it prints a line starting "run complete:" (or an error/traceback).
3. If it prints "no token -- running init first" on a first-ever run, that is fine — it self-initialises a baseline and will process changes from the next run.

STAGE 1 — WHAT THE SCRIPT DOES (all automatic, whole Hub, no hard-coded folder list):
- Pulls the Drive Changes-API delta since the last run (token in Library/processes/hub-reconcile-state.json).
- For every folder whose contents changed, regenerates its README.md auto-index block (between the <!-- HUB-INDEX:AUTO START/END --> markers); creates a README if the folder lacks one, so new folders are covered automatically.
- Appends a dated change-log entry to Library/processes/hub-content-index.md.
- NOISE FILTER (2026-06-12): the script's own README auto-refreshes and ACL/share/move-only surfacings are excluded from the digest counts automatically.
- Emails Pete (pete.ashcroft@sygma-solutions.com) a digest — but only if there were human changes.

STAGE 2 — SORT THE DELTA (the session does this; added 2026-06-12 after Pete: "do I manually come here every night to sort it"). Read the log's digest content, then for each item:

NEW FOLDERS — classify before reporting:
1. Resolve each new folder's full Hub path + creator (Drive API; the script module hub-reconcile.py exposes _req/DRIVE/HUB_DRIVE_ID for queries).
2. Compare against the established pattern of its SIBLINGS in that location (e.g. DDMMYYYY course-date folders are the standing convention under Course Records / Training Certificates / {Customer} / {Year} AND Booking Forms — those are convention-consistent, NO action, no Pete-flag).
3. Genuinely NEW structure (new top-level folder, new customer branch, new category): add it to hub-content-index.md (at-a-glance table + detail section) per hub-maintenance.md.
4. Violates a LOCKED convention (e.g. month folders must be "June 2026" not "Jun" — Karen, told 2026-06-11; self-describing course filenames per the Hub routing rules lesson):
   - If a rename alone fixes it and the convention is locked: rename in Drive, log it in the digest.
   - If the person has ALREADY been told the convention before: send them the standard short nudge email (Pete-voice, dash-grep clean) and note it in the digest.
   - First-time issue: do NOT email; put a ready-to-send draft in the digest for Pete.
5. NEVER touch (escalate only): HR/Staff trees, anything owner-private-adjacent, deletions, moves between top-level sections, anything ambiguous after checking conventions. When in doubt: flag, don't act.

FILES — scan the added/edited list for obvious misfiles (file type/content vs folder purpose; classify-by-content rule from the Hub migration lessons). Flag suspected misfiles in the digest; do not move files autonomously.

MIRRORS — if the delta touched any folder mirrored to the vault (Library/sy-* per hub-sync registry): run
   nohup python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/hub-sync.py" pull > /tmp/hub-sync-pull.log 2>&1 &
via Desktop Commander and confirm the pull completed, so the vault mirrors are current the same night.

REPORT — reply-email Pete on the digest thread ONLY IF Stage 2 actually did something or needs him (subject: "Re: Sygma Hub reconcile - {date}"), three short sections: WHAT CHANGED (one line) / SORTED AUTOMATICALLY (map lines added, renames, mirror pulls, convention-consistent classifications) / NEEDS YOU (usually "nothing"). If Stage 2 had nothing to do beyond classification and everything was convention-consistent, do not send a second email — the script's digest already carries the noise-free summary.

AFTER IT FINISHES:
- Append a one-line summary to today's daily note under "## Hub reconcile (Automated)": counts + what Stage 2 did (READ the daily note before editing — other crons write to it).
- If the script errored, report the traceback from the log; do not claim success.

Idempotent and safe to re-run.