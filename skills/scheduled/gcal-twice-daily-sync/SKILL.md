---
name: gcal-twice-daily-sync
description: DISABLED 2026-05-28 — migrated to native launchd at ~/Library/LaunchAgents/com.peterashcroft.gcal-twice-daily-sync.plist. Same schedule (07:00 + 18:00 Atlantic/Canary). Logs at ~/Library/Logs/gcal-twice-daily-sync.{out,err}.log. Re-enable only if rolling back.
---

Run the twice-daily Xhale → GCal sync + folded colour-coder pass.

## Execution, READ THIS FIRST

Use Desktop Commander, NOT workspace bash (workspace bash has a 45s cap and the Calendar API + Anthropic LLM calls can push runtime past that).

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/xhale-sync.py" run > /tmp/gcal-twice-daily-sync.log 2>&1 &
  timeout_ms: 5000
```

Then poll `/tmp/gcal-twice-daily-sync.log` every 15-30 seconds (via `mcp__Desktop_Commander__read_file`) until the file contains a final summary line of the shape `Xhale: N in feed, ... | Colour: ... | Status: ok` (or `Status: errors (N)`).

## What the script does

`xhale-sync.py` runs both phases in one pass:

**Phase 1 — Xhale → GCal**
1. Fetch ICS from Train Xhale feed
2. Parse VEVENTs, filter to today−7d → today+90d
3. Classify each (training / travel / update / journal / rest_day / filtered / unknown)
4. Journal miss-detection: if yesterday has no `journal`-classified entry → urgent email
5. Travel: verify a flight exists in Pete's diary on that date; flag if missing
6. Rest days (`rest day` / bare `rest` / `rest …`, case-insensitive): never created in GCal; previously-created rest-day events (`gcal_match_type=created-by-sync`, not manually modified, future) are deleted on the next sync (per Pete 2026-05-25)
7. Training: parse time via Haiku 4.5 → dedupe against ledger + same-day GCal → create / patch / skip
8. Deletions: removed-from-feed UIDs we created → delete in GCal (unless manually-modified)

**Phase 2 — Colour-coder fold**
8. `calendar_colour.run('apply-recent', 2, 365)` — Sygma=9, CD=10, Personal=2 (Sage, post 2026-05-24 flip), Travel=6.

Operational rules + Loren-exclusion list + time-parser test cases: [[Library/processes/xhale-sync/README]]. Full plan: [[Library/processes/xhale-sync/plan-2026-05-24]].

## After the run

Read `/tmp/gcal-twice-daily-sync.log` and extract the final summary line. The script already appends to today's daily note under `## GCal twice-daily sync (Automated)` and to `Library/processes/xhale-sync/run-log.md`.

- `Status: ok` and 0 attention lines → silent run, no further action.
- `Status: ok` with attention lines (unknown classifications, travel-missing, journal-missing) → script has already emailed Pete + appended to daily note; no further action.
- `Status: errors (N)` → re-read the log, report first 3 errors verbatim.

## Rules

- Don't modify event content (titles, times, dates). Phase 1 creates / patches via ledger; Phase 2 only changes `colorId`.
- Idempotent — safe to re-run.
- Edit categorisation in `calendar-colour.py`, Loren-exclusions in `xhale-sync/README.md` — those are the sources of truth.
- Time parser uses Haiku 4.5 with strict JSON; regression cases live in the README time-parser table.