---
name: calendar-colour-coder
description: DISABLED 2026-05-24 — folded into gcal-twice-daily-sync (07:00 + 18:00). Kept here for recovery only; do not re-enable without retiring the new combined task first. Original: daily 06:30 colour pass over Pete's primary calendar.
---

Run the daily calendar colour-coding pass for Pete's primary Google Calendar.

## Execution, READ THIS FIRST

Use Desktop Commander, NOT workspace bash (workspace bash has a 45s cap and Calendar API rate-limit retries can push runtime past that).

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/calendar-colour.py" apply --days-back 2 --days-ahead 365 > /tmp/calendar-colour-cron.log 2>&1 &
  timeout_ms: 5000
```

Then poll `/tmp/calendar-colour-cron.log` every 15-30 seconds (via mcp__Desktop_Commander__read_file) until the file contains `Applied colours to ` (success line).

## What the script does

Walks Pete's primary calendar from 2 days back to 365 days ahead. For each event with no colorId, classifies via rules in calendar-colour.py and applies:

- Sygma work → colourId 9 (Blueberry)
- CD work → colourId 10 (Basil)
- Personal → colourId 1 (Lavender)
- Travel → colourId 6 (Tangerine)

Already-coloured events are skipped (Pete's manual choices preserved). Birthday + some fromGmail events are skipped (Google API blocks colour mutation — not an error).

## After the run

Read /tmp/calendar-colour-cron.log and extract `Applied colours to N events.`

- 0 applied + only skipped_already_coloured non-zero: silent run, no action.
- N > 0 applied: append a 1-line summary under `## Calendar colour-coder` in today's daily note `/Users/peterashcroft/Second Brain/Daily/{YYYY-MM-DD}.md`.
- Errors (N) appears with genuine errors (not the auto-skipped birthday/fromGmail kind): surface error count + first 3 titles in the daily-note line.

## Rules

- Edit categorisation in calendar-colour.py — that's the source of truth.
- Don't add/delete/move events. ONLY change colorId.
- Idempotent — safe to re-run.