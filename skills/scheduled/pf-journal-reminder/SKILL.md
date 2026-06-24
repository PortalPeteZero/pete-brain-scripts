---
name: pf-journal-reminder
description: Daily 6pm reminder email for the PF framework journal practice. Pete arrives in Cowork to do the 10-minute structured self-coaching against the 4 Core Behaviours and HF Matrix.
---

PF Journal Reminder — daily 6pm Atlantic/Canary

## What this does

Sends Pete a reminder email at 6pm local every evening, nudging him to open Cowork and do the 10-minute PF framework journal.

## Execution, READ THIS FIRST

Run via Desktop Commander, NOT workspace bash (per [[Library/lessons/2026-05-02-scheduled-task-skill-md-uses-dc]]).

## Source of truth

The canonical reference for this practice is `/Users/peterashcroft/Second Brain/Library/processes/pf-journal.md`. The script + run pattern below is duplicated there in the "## Cron execution" section — if the two diverge, that file wins. Read it for full context.

## Steps

1. Read `/Users/peterashcroft/Second Brain/Library/processes/pf-journal.md` to confirm current state of the cron behaviour (in case it has been updated).
2. Write the Python script below to `/tmp/pf-journal-reminder.py`.
3. Run it via `mcp__Desktop_Commander__start_process` with nohup + log polling.
4. Poll `/tmp/pf-journal-reminder.log` for the `SENT` line. Capture message ID.
5. Append a short status line to today's daily note at `/Users/peterashcroft/Second Brain/Daily/{today}.md` under a `## PF Journal Reminder (Automated)` section.

## Script

Save to `/tmp/pf-journal-reminder.py`:

```python
import sys, os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import importlib.util

VAULT = "/Users/peterashcroft/Second Brain"

# Compute dates in Pete's local timezone (NEVER bake dates into the SKILL.md per
# Library/lessons/2026-05-19-scheduled-task-skill-md-no-baked-in-time-labels).
tz = ZoneInfo("Atlantic/Canary")
today = datetime.now(tz).date()
yesterday = today - timedelta(days=1)

# Read yesterday's journal entry if it exists
yest_path = f"{VAULT}/Personal/passion-fit/journal/{yesterday.isoformat()}.md"
continuity = None
if os.path.exists(yest_path):
    text = open(yest_path).read()
    marker = "## One lesson for tomorrow"
    if marker in text:
        after = text.split(marker, 1)[1].strip()
        nh = after.find("\n## ")
        if nh > 0:
            after = after[:nh]
        nh = after.find("\n---")
        if nh > 0:
            after = after[:nh]
        continuity = after.strip() or None

# Compose body (Pete voice per voice-principles: no em dashes, no semicolons, plain text).
# Recovery data is NOT included here — it lives in Personal/health/garmin/{yesterday}.md
# written by the twice-daily garmin-daily-pull cron (07:00 + 17:00). This email is a phone-nudge only.
lines = [
    "Hey Pete,",
    "",
    "10 mins on the PF framework. Open Cowork when you are ready and say \"journal\".",
    "",
]
if continuity:
    lines.extend([
        "Yesterday's lesson for today was:",
        "",
        f"  {continuity}",
        "",
        "How did that land?",
        "",
    ])
lines.append("See you in Cowork.")
body = "\n".join(lines)

# Send via gmail-api.py
spec = importlib.util.spec_from_file_location(
    "gmail_api", f"{VAULT}/Library/processes/scripts/gmail-api.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
g = mod.GmailAPI()
result = g.send(
    to="pete.ashcroft@sygma-solutions.com",
    subject="Journal time — 10 mins",
    body=body,
)
print(f"SENT msg_id={result.get('id')} thread_id={result.get('threadId')} continuity={'yes' if continuity else 'no'}")
```

## Run via Desktop Commander

```
mcp__Desktop_Commander__start_process(
    command='nohup python3 /tmp/pf-journal-reminder.py > /tmp/pf-journal-reminder.log 2>&1 &',
    timeout_ms=10000,
)
```

Then poll `/tmp/pf-journal-reminder.log` (max ~10s — the script is fast).

## Reporting

In today's daily note, append under `## PF Journal Reminder (Automated)`:

- Sent: yes/no
- Message ID (if sent)
- Continuity included: yes (yesterday's lesson surfaced) / no (no prior entry)

If error, log it under the same section but do NOT retry — Pete will notice the missing email and we can investigate next session.

## Why this exists

Originated 2026-05-23 after Pete's response to Tom Ward's cohort experiment surfaced that IPSATIVE goal-setting had collapsed through six months of high external load, with knock-on Commitment Continuum drift, prioritisation chaos, and disengagement from coach communication. The daily practice is the corrective. Full origin record at `/Users/peterashcroft/Second Brain/Personal/passion-fit/coaching/2026-05-23-toms-experiment-response.md`.

## Related lessons

PF-journal sibling process: [[Library/processes/pf-weekly-loop]]. Lessons that apply across both the daily journal and the weekly loop:

- [[Library/lessons/2026-05-31-coaching-feedback-backfill-note-is-vault-not-xhale]] — `backfill_note` in coaching/feedback files records VAULT copy date, not Xhale upload date.
- [[Library/lessons/2026-05-31-pf-weekly-reflection-lives-in-closing-week-file]] — PF weekly Reflection lives in the closing week's file under `## Reflection on the week`, NOT in the new week's file.
- [[Library/lessons/2026-05-31-pf-weekly-use-goals-not-ipsative]] — use "goals", not "IPSATIVE goals", in PF entry prose.
- [[Library/lessons/2026-05-31-pf-weekly-yardstick-is-petes-plan]] — PF weekly yardstick is Pete's written plan, not Loren's suggestion.
