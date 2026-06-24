---
name: consolidate-lessons-monthly
description: Monthly 1st 08:30 — reflective pass over Library/lessons/. Merge duplicates, retire stale incidents, rebuild lessons/README.md, surface deployment gaps. Pairs with consolidate-memory-monthly which runs 30 minutes before.
---

Run the `consolidate-lessons` skill end-to-end against Pete's `Library/lessons/` directory.

## Skill to invoke

The `consolidate-lessons` skill, source at `/Users/peterashcroft/Second Brain/Library/skills/consolidate-lessons/SKILL.md`. Read it and follow its workflow exactly. The skill describes:

- Phase 1 — Take stock: list `Library/lessons/`, read `Library/lessons/README.md`, cross-check via drift-check, skim each lesson file
- Phase 2 — Consolidate: separate durable vs dated, retire dated to `_archive/`, mark superseded lessons, merge duplicates, fix time references, identify promotion candidates
- Phase 3 — Tidy the README index: lesson count matches on-disk count, every entry has ≤150-char one-liner, grouped by topic
- Phase 4 — Verify via drift-check (run via Desktop Commander)
- Phase 5 — Report short summary

## Lessons directory

`/Users/peterashcroft/Second Brain/Library/lessons/`

## Pairing

This task runs SECOND in the monthly maintenance pair. `consolidate-memory-monthly` runs at 08:00 (30 minutes earlier) and consolidates the auto-memory tier. Order matters: memory is fast-lookup (consolidate first); lessons are deep-read (consolidate after).

## Output

At the end, email Pete (pete.ashcroft@sygma-solutions.com) via gmail-api.py with subject `Lesson consolidation — {YYYY-MM-DD}` and a short summary:
- Lessons touched (kept / retired / merged / promoted)
- New canonical-lesson candidates surfaced
- Open deployment gaps left for Pete to action (specific (lesson, skill) pairs in the matrix at `Library/audits/2026-05-16-lesson-deployment-matrix.md` that don't have citations yet)

Use the Sygma helper at `Library/processes/scripts/gmail-api.py`.

If nothing changed (clean state), email anyway with a one-liner saying "0 changes — lessons are consolidated."

## On error

Email Pete with subject `consolidate-lessons-monthly: failed` and the error details. Do not silently fail.

## Drift-check verification step

Phase 4 of the skill requires running `vault-drift-check.py`. Use Desktop Commander (not workspace bash — 45s cap):

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/vault-drift-check.py" > /tmp/lessons-consolidate-drift.log 2>&1 & echo "PID=$!"
  timeout_ms: 5000
```

Poll completion, then verify `check_lesson_index_parity` and `check_lessons_cited_from_skills` both pass.

## Why this exists

Lessons accumulate faster than they're pruned. Monthly reflection keeps the lessons tier tight and prevents the silent rot that built up across April-May 2026. See [[Library/lessons/2026-05-03-vault-rot-audit-and-drift-prevention]].
