---
name: consolidate-memory-monthly
description: Monthly 1st 08:00 — reflective pass over Pete's auto-memory directory. Merge duplicates, fix stale facts, prune MEMORY.md index. Pairs with consolidate-lessons-monthly which runs 30 minutes after.
---


## Execution -- READ THIS FIRST

This task runs script invocations via Desktop Commander, NOT workspace bash. Workspace bash has a 45s sandbox cap that silently truncates longer runs; Desktop Commander runs natively from the host with no cap.

For each `python3 ...` call below, use this pattern:

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "<absolute_path>" [args] > /tmp/<taskid>.log 2>&1 & echo "PID=$!"
  timeout_ms: 5000
```

Then poll `ps -p $PID` until exit, then read the log for output. Reference: [[Library/lessons/2026-05-02-scheduled-task-skill-md-uses-dc]].

---

Run the `consolidate-memory` skill end-to-end against Pete's auto-memory directory.

## Skill to invoke

The `consolidate-memory` skill (anthropic-skills:consolidate-memory). Read its SKILL.md and follow its workflow exactly. The skill describes:

- Phase 1 — Take stock: list memory directory, read `MEMORY.md`, skim each topic file
- Phase 2 — Consolidate: separate durable vs dated, merge overlaps, fix time refs, drop easy-to-re-find
- Phase 3 — Tidy the index: update MEMORY.md, keep under 200 lines / ~25KB

## Memory directory

`/Users/peterashcroft/Library/Application Support/Claude/local-agent-mode-sessions/9c907cf0-e04f-4863-a7d8-62e7546b0475/79f9c779-32bc-46bc-b712-667704de7c95/spaces/2ff87d1a-b738-43a4-b21b-35e30dc7f5a1/memory/`

## Pairing

This task runs FIRST in the monthly maintenance pair. `consolidate-lessons-monthly` runs at 08:30 (30 minutes later) and consolidates `Library/lessons/` — the deeper-read tier. Order matters: memory is fast-lookup (consolidate first); lessons are deep-read (consolidate after).

## Output

At the end, email Pete (pete.ashcroft@sygma-solutions.com) via gmail-api.py with subject `Memory consolidation — {YYYY-MM-DD}` and a short summary: how many files touched, what merged, what retired, what newly added. Use the Sygma helper at `Library/processes/scripts/gmail-api.py`.

If nothing changed (clean state), email anyway with a one-liner saying "0 changes — memory is consolidated."

## On error

Email Pete with subject `consolidate-memory-monthly: failed` and the error details. Do not silently fail.

## Why this exists

Memory accumulates rules + facts faster than it's pruned. Monthly reflection keeps MEMORY.md tight and sharp. Same drift-prevention discipline as `vault-drift-check`. See [[Library/lessons/2026-05-03-vault-rot-audit-and-drift-prevention]].
