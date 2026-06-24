---
name: utilisation-tracker-refresh
description: Daily Sygma trainer utilisation refresh -- 5 trainer diaries + master Sheets (native Google Sheets) -> utilisation report.xlsx, summary to Management chat with day-over-day diff and discrepancies.
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

Run the daily Sygma trainer utilisation refresh.

Execute:

```
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/utilisation-tracker-refresh.py"
```

This script (no arguments needed) does the full job:
- Reads UK bank holidays for the in-scope FY.
- Reads the 5 main trainer Google calendars (Gareth, Geoff, Mark, Andy F, Andy B) for each in-scope month (Apr 26..Mar 27).
- Downloads the master training spreadsheets (now **native Google Sheets** named `2026` / `2027` after the 2026-05-03 conversion; new file IDs in `MASTER_FILE_IDS` constant in script) from Sygma Hub / Course Records / Training Spreadsheets and parses every booked course row.
- Classifies each calendar event per the rules in the SOP (training / holiday / admin / skip).
- Computes per-trainer per-month metrics with the calendar classifier and master as **co-equal signals**: bookings = (calendar says training) ∪ (master has a course for this trainer/day).
- Tracks discrepancies (one signal says yes, the other no) for inclusion in the chat post.
- Computes Bookings / Available / Holidays inc Bank / Days Lost.
- Downloads the live `utilisation report.xlsx` from Drive (file ID `14NRq_A-IJCgqvEHgII6vmg9Gy6fhUYa6` -- Sygma Hub / Reports / Trainer Utilisation).
- Captures the existing per-trainer values from each monthly tab BEFORE writing -- this is "yesterday's snapshot" used for the chat diff.
- Rewrites rows 3-7 columns B-F on every monthly tab.
- Past or current month: Days Trained = Bookings. Future month: Days Trained left blank.
- Re-uploads the xlsx via PATCH to the same file ID on Drive.
- Posts a summary message to the **Management** Google Chat space (`spaces/AAQAfi47jHo`) with current + next month totals, per-trainer breakdown, day-over-day diff, discrepancies vs master, and a link to the live file.
- Emits a `===BEGIN DAILY NOTE BLOCK=== ... ===END DAILY NOTE BLOCK===` paragraph on stdout, with current + next month already substituted from today's date. This is what gets pasted into the daily note (see below).

Source of truth = live calendars + live UK bank holiday calendar + live master training spreadsheets. The pre-mutation utilisation xlsx values are used ONLY for the chat diff, never to seed today's metrics. Each run re-derives every metric from scratch.

The script reads master columns by HEADER NAME (`Date`, `Booking Company`, `Trainer`, `Course Title`, `CITB Levy Number`) so future column inserts don't break it. Master file IDs since 2026-05-03 are native Google Sheet IDs (not xlsx); the script downloads them via the Drive API export endpoint.

If the script errors out, do not retry silently. Capture the error, post a short failure note to the Management chat space ("Utilisation refresh failed at HH:MM -- error: ...") and append the error to today's daily note.

### Appending to the daily note

The script prints a ready-to-paste block at the end of its stdout, delimited by:

```
===BEGIN DAILY NOTE BLOCK===
...
===END DAILY NOTE BLOCK===
```

Append every line BETWEEN those two markers (exclusive of the markers themselves) to today's `Daily/YYYY-MM-DD.md`, preceded by a single blank line.

**Do not improvise, rewrite, or "tidy" the month labels.** The script computes the current month and next month from today's date and emits them already-substituted -- so whatever month the cron fires in (May, June, October, January), the right labels appear automatically. Any month name you see hard-coded in this SKILL.md or in past daily-note examples is illustrative only; trust the block.

If the BEGIN/END markers are missing from the log (e.g. the script crashed before reaching them), follow the failure path above and do NOT hand-compose a substitute block.

No automatic feedback loop. Pete tunes classification rules manually when he gives you feedback.

SOP: `Businesses/sygma-solutions/training/sops/daily-utilisation-tracker.md`