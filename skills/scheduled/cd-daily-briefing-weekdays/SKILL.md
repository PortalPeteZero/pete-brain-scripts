---
name: cd-daily-briefing-weekdays
description: CD team briefing (Odoo) + Pete's personal briefing (GCal) at 18:15 weekdays. Covers Tue-Sat. Fires AFTER the 18:00 cd-tom-jobs-calendar-sync-evening so Tom's calendar is up to date when the briefing reads it.
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

You are running the CD daily briefing scheduled task.

This task fires every weekday (Mon-Fri) at 17:00 Atlantic/Canary local time. Each run sends two emails about TOMORROW's events:

1. Team briefing (CD field jobs from Odoo) -- recipients: Pete (Sygma email), Dave, Tom, Nicola, Jane
2. Personal briefing (Pete's Google Calendar) -- recipients: Pete only (Sygma email)

## Steps

Run both scripts in order. They are self-contained -- auto-target tomorrow, render HTML, send via Gmail API helper.

```bash
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/cd-team-briefing.py"
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/pete-personal-briefing.py"
```

## Capture results

Each script prints to stdout: date briefed, recipient list, subject, sent message id (or error). If either fails, continue with the other.

## Daily note append

Append to today's `/Users/peterashcroft/Second Brain/Daily/{today YYYY-MM-DD}.md` (re-read first):

```
## CD Daily Briefing (Automated, weekday)
- Team briefing: {sent OK with message id | failed with error}
- Personal briefing: {sent OK with message id | failed with error}
- Briefed for: {tomorrow's date}
```

## Notes

- Both scripts read live from Odoo / Google Calendar at run time.
- Team briefing filters to events with a linked CRM lead/opportunity (real field work only).
- Personal briefing pulls every event from Pete's primary Google Calendar.
- Recipients are hard-coded inside the scripts.