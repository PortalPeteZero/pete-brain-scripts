---
name: cd-week-ahead-sunday
description: CD week-ahead team briefing + Pete's week-ahead personal briefing. Friday 18:00 (moved from Sunday 16:15 on 2026-06-15). taskId retains '-sunday' for continuity but now fires Friday. Team recipients now include Marcos.
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

You are running the CD week-ahead scheduled task.

Fires every Friday at 18:00 Atlantic/Canary local time. Each run sends two emails covering the UPCOMING working week — Monday to Sunday. The script computes next Monday from the run date, so a Friday run still briefs the upcoming Mon–Sun (not the weekend):

1. Team week-ahead (CD field jobs from Odoo, grouped by day -> engineer) -- recipients: Pete (Sygma), Dave, Tom, Marcos, Nicola, Jane
2. Personal week-ahead (Pete's Google Calendar, grouped by day) -- recipients: Pete only (Sygma)

It runs Friday evening so the team sees the week ahead before the weekend.

## Steps

Run both scripts in --window week mode:

```bash
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/cd-team-briefing.py" --window week
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/pete-personal-briefing.py" --window week
```

## Capture + log

Each script prints window briefed, recipients, subject, sent message id (or error). If either fails, continue with the other.

Append to today's `/Users/peterashcroft/Second Brain/Daily/{today YYYY-MM-DD}.md` (re-read first):

```
## CD Week-Ahead Briefing (Automated, Friday)
- Team week-ahead: {sent OK with message id | failed with error}
- Personal week-ahead: {sent OK with message id | failed with error}
- Briefed for: w/c {next Monday's date}
```

## Notes

- Both scripts read live from Odoo / Google Calendar at run time.
- Team briefing filters to events with a linked CRM lead/opportunity.
- Recipients hard-coded inside the scripts (cd-team-briefing.py RECIPIENTS — Marcos added 15 Jun 2026).
- Companion day-view tasks: cd-daily-briefing-weekdays (Mon–Fri 18:15, tomorrow's jobs), cd-daily-briefing-sunday (Sun 18:15, Monday's jobs).
- NOTE: the taskId is still `cd-week-ahead-sunday` (kept for continuity); it now fires Friday, not Sunday.