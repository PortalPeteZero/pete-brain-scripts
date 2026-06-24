---
name: cd-tom-jobs-calendar-sync-evening
description: Twice-daily Odoo -> Tom Google Calendar sync (evening run, 18:00). Pair with the noon run. Appends summary to daily note.
---

Run the evening Odoo -> Tom Google Calendar sync (catches afternoon bookings).

## Execution -- READ THIS FIRST

**You MUST invoke this script via Desktop Commander (`mcp__Desktop_Commander__start_process`), not workspace bash.** Workspace bash's 45s cap will kill it mid-run. Desktop Commander runs natively on Pete's Mac with no cap.

**Pattern:**

```python
mcp__Desktop_Commander__start_process(
    command='cd "/Users/peterashcroft/Second Brain/Library/processes/scripts" && '
            'nohup python3 -u cd-tom-jobs-calendar-sync.py '
            '> /tmp/calsync-evening-cron.log 2>&1 & '
            'echo "PID=$!"',
    timeout_ms=10000,
)
# Poll ps -p $PID until done. Final summary: grep "run-summary" /tmp/calsync-evening-cron.log
```

Same script, same logic as `cd-tom-jobs-calendar-sync-noon`. **The 18:00 run is the one that appends the summary block** to today's `Daily/YYYY-MM-DD.md`:

```
## CD Tom Jobs Calendar Sync (Automated)
- Runs at 12:30 + 18:00 (Atlantic/Canary)
- Latest run: 18:00
- Events created: N, patched: N, deleted (orphans): N, unchanged: N
- Maps links generated: N ROOFTOP, N RANGE_INTERPOLATED, N GEOMETRIC_CENTER, N APPROXIMATE
- Status: ok
```

(The noon run is silent in the vault note to avoid clutter.)

Spec doc: `Library/processes/tom-jobs-calendar-sync.md`. See `cd-tom-jobs-calendar-sync-noon` SKILL.md for the full behavioural detail.

If the script errors out, append a failure note to the daily note and email Pete with the traceback.