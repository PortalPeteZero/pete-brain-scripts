---
name: cd-weekly-finance-email
description: CD weekly finance turnover report. Tuesday 18:00 Atlantic/Canary. Sends to Pete (Sygma), Dave, Nicola.
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

You are running the CD weekly finance email scheduled task.

This task fires every Tuesday at 18:00 Atlantic/Canary local time. Each run pulls LIVE data from Odoo (camello-blanco-sl.odoo.com), generates two weekly turnover reports (last completed week + week before that, refreshed), saves them to the vault, and emails an HTML report to the team.

**Why both weeks**: Mon/Tue typically catches up on late invoicing for the previous week. Re-running the prior week ensures any late-filed invoices are reflected in the older week's archived report.

**Recipients (live)**: Pete (pete.ashcroft@sygma-solutions.com), Dave (dave.poxon@canary-detect.com), Nicola (nicola.brown@canary-detect.com).

**Strict rule**: NO cached / stored data. Every run queries Odoo fresh.

## Steps

Run the email script with default flags (no --preview, no --dry-run, no --week — defaults to last completed week and live recipients):

```bash
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/cd-weekly-finance-email.py"
```

The script will:
1. Pull this-week's data from Odoo (last completed Mon-Sun)
2. Pull week-before's data (refresh)
3. Pull all outstanding invoices (all vintages)
4. Save BOTH markdown reports to `Businesses/canary-detect/finance/weekly-turnover-reports/turnover-week-{YYYY-MM-DD}.md`
5. Render an HTML email with the locked-in 6-section layout (headline, revenue split, by category, by product as grouped table, outstanding, reconciliation badge)
6. Send via Gmail API helper to the three live recipients
7. Print sent message id + recipient list

## Capture results

The script prints sent message id and the file paths it saved. Capture this output for the daily-note append.

If the script fails (e.g. Odoo auth, Gmail API down), continue gracefully -- log the error in the daily note and stop. Do not retry automatically. Pete will investigate manually.

## Daily note append

Append to today's `/Users/peterashcroft/Second Brain/Daily/{today YYYY-MM-DD}.md` (re-read first):

```
## CD Weekly Finance Report (Automated)
- Sent: {message id} to Pete + Dave + Nicola
- This week: w/c {Monday date} -- gross €{X}, {N} invoices
- Refreshed: w/c {prior Monday date}
- Outstanding total: €{Y} across {Z} invoices
- Reconciliation: {PASS/FAIL}
- Markdown archived to Businesses/canary-detect/finance/weekly-turnover-reports/
```

If the script failed, log the error message in place of the success block.

## Notes

- The script is read-only against Odoo. Nothing is written to Odoo.
- The script overwrites the markdown files for the two target weeks each run -- this is by design (the week-before refresh updates the prior week's archive with the latest invoice picture).
- Source: cd-weekly-finance-email.py (defined in `Library/processes/scripts/`).
- Layout decisions locked-in 2026-04-30 with Pete: weekly drops Top Customers + Payment State sections (kept on monthly only); By Product is single grouped table with category section rows + product rows nested; outstanding capped at top 15 in email body.