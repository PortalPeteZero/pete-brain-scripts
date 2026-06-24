---
name: cd-daily-briefing-sunday
description: CD team briefing (Odoo) + Pete's personal briefing (GCal) at 18:15 Sunday. Covers Monday. Fires AFTER the 18:00 cd-tom-jobs-calendar-sync-evening so Tom's calendar is up to date when the briefing reads it.
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

You are running the CD Sunday briefing scheduled task.

This task fires every Sunday at 16:00 Atlantic/Canary local time, covering Monday's events.

Each run sends two emails about TOMORROW (i.e. Monday's events):

1. Team briefing (CD field jobs from Odoo) -- recipients: Pete (Sygma email), Dave, Tom, Nicola, Jane
2. Personal briefing (Pete's Google Calendar) -- recipients: Pete only (Sygma email)

## Steps

Run both scripts in order:

```bash
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/cd-team-briefing.py"
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/pete-personal-briefing.py"
```

## Capture + log

Each script prints sent message id or error. If either fails, continue with the other.

Append to today's `/Users/peterashcroft/Second Brain/Daily/{today YYYY-MM-DD}.md` (re-read first):

```
## CD Daily Briefing (Automated, Sunday)
- Team briefing: {sent OK with message id | failed with error}
- Personal briefing: {sent OK with message id | failed with error}
- Briefed for: {tomorrow's date}
```

## Notes

- Companion weekday task is cd-daily-briefing-weekdays (cron 0 17 * * 1-5); shares the same scripts.
- Both scripts read live from Odoo / Google Calendar.
- Recipients hard-coded inside the scripts.