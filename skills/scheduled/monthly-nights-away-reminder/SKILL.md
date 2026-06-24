---
name: monthly-nights-away-reminder
description: Last Friday of each month at 08:00 — email card-holding trainers to remind them to log their nights-away count on Sunday's calendar entry.
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

You are running the **monthly-nights-away reminder** task.

# What this task does

Cron fires every Friday at 08:00 local (Atlantic/Canary). This task ONLY does work on the **last Friday of the month** — otherwise it exits silently. On the last Friday, it sends a personalised email to each of the 6 card-holding trainers reminding them to fill in their "Nights worked away" calendar event, which appears on the Sunday two days later (the last Sunday of the month).

# Step 1 — Last-Friday-of-month guard

Today's date is given by the system. Compute: is today + 7 days in a different month from today? If yes, today is the last Friday of the month — proceed. If no, today is NOT the last Friday — log a one-line note in the daily file and exit immediately.

```python
from datetime import datetime, timedelta
import zoneinfo
now = datetime.now(zoneinfo.ZoneInfo("Atlantic/Canary"))
is_last_friday = (now + timedelta(days=7)).month != now.month
```

If `is_last_friday` is False, append to `Daily/YYYY-MM-DD.md`:

```markdown
## Monthly nights-away reminder (Automated)
Skipped — today is not the last Friday of the month.
```

Then exit.

# Step 2 — Compute the upcoming Sunday date

```python
upcoming_sunday = now + timedelta(days=2)   # Friday + 2 = Sunday
sunday_str = upcoming_sunday.strftime("%-d %B %Y")   # e.g. "26 April 2026"
month_str  = now.strftime("%B")                       # e.g. "April"
```

# Step 3 — Send a personalised email to each of the 6 trainers

Trainer roster (hard-coded — DO NOT pull from anywhere else; if Pete adds/removes a card-holding trainer, update this list AND the keep-list in [[monthly-nights-away-tracking]]):

```python
TRAINERS = [
    ("Andy",    "andy.bartholomew@sygma-solutions.com"),
    ("Andrew",  "andrew.foster@sygma-solutions.com"),
    ("Gareth",  "gareth.phillips@sygma-solutions.com"),
    ("Geoff",   "geoff.astley@sygma-solutions.com"),
    ("Mark",    "mark.pearce@sygma-solutions.com"),
    ("Neal",    "neal.sadd@sygma-solutions.com"),
]
```

For each trainer, send via `Library/processes/scripts/gmail-api.py`. The canonical method is **`g.send(to=..., subject=..., body=...)`** — NOT `send_message`, NOT `send_email`. The helper impersonates `pete.ashcroft@sygma-solutions.com` via service-account DWD.

Subject format (must include month name to keep months as separate Gmail threads):
```
Nights worked away — please log {month_str}
```

Body:
```
Hi {first_name},

Quick reminder — this Sunday {sunday_str} you'll see a "Nights worked away" event in your calendar.

Please open it and add your total nights away from home for {month_str} (a number is fine, e.g. "11" or "12 nights").

Why it helps: lets us cross-reference Soldo card spend (hotels, food, fuel, vehicle) against actual time on the road, so we can spot anything that needs reviewing and keep your accounts clean.

Cheers,
Pete
```

Working snippet:

```python
import sys
sys.path.insert(0, "/Users/peterashcroft/Second Brain/Library/processes/scripts")
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("gmail_api", "/Users/peterashcroft/Second Brain/Library/processes/scripts/gmail-api.py").load_module()
g = m.GmailAPI(user="pete.ashcroft@sygma-solutions.com")
g.send(to=email, subject=subject, body=body)
```

# Step 4 — Log results to daily note

Append to `Daily/YYYY-MM-DD.md`:

```markdown
## Monthly nights-away reminder (Automated)
**Date:** {today}  (last Friday of {month_str} {year})
**Sunday reference:** {sunday_str}
**Sent to:** {comma-list of trainer first names}
**Errors:** {any send errors, or "none"}
```

# Constraints / behaviour

- **No Asana task.** Fire-and-forget reminder.
- **No retries.** If a send fails, log the error in the daily note and move on.
- **No CC/BCC.** Each trainer gets a clean 1:1 email.
- **Subject must include {month_str}** so months don't merge into one Gmail thread.
- **Do not send if `is_last_friday` is False.** Critical guard.
- **Do not call any Soldo or Xero API** — this task is purely a Gmail send + daily-note log.
- **Live data only.** Do not read previous run outputs.
- **Method is `g.send()`** — verified working 2026-04-28. Do not invent `send_message` / `send_email` / `create_and_send` — those don't exist on `GmailAPI`.

# Cross-references in the vault

- SOP: `Businesses/sygma-solutions/training/sops/monthly-nights-away-tracking.md`
- Trainer profiles: `Businesses/sygma-solutions/training/people/{name}.md`
- Soldo API config: `Library/processes/soldo-api-configuration.md`
- Calendar API helper: `Library/processes/scripts/calendar-api.py`
- Gmail API helper: `Library/processes/scripts/gmail-api.py`
