---
name: cd-monthly-finance-email
description: CD monthly finance turnover report. 10th of every month at 18:00 Atlantic/Canary. Sends to Pete (Sygma), Dave, Nicola.
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

You are running the CD monthly finance email scheduled task.

This task fires on the 10th of every month at 18:00 Atlantic/Canary local time. Each run pulls LIVE data from Odoo (camello-blanco-sl.odoo.com) for the LAST COMPLETED MONTH (e.g. on 10 May the report covers April), saves the markdown to the vault, and emails an HTML report to the team.

**Why the 10th**: Gives a full week after month-end for late invoicing to be filed. By 10th, the month's numbers are stable.

**Recipients (live)**: Pete (pete.ashcroft@sygma-solutions.com), Dave (dave.poxon@canary-detect.com), Nicola (nicola.brown@canary-detect.com).

**Strict rule**: NO cached / stored data. Every run queries Odoo fresh.

## Steps

Run the email script with default flags (no --preview, no --dry-run, no --month — defaults to last completed month and live recipients):

```bash
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/cd-monthly-finance-email.py"
```

The script will:
1. Determine last completed month
2. Pull all data from Odoo for that month + 2 prior months for comparison
3. Pull outstanding invoices (all vintages)
4. Save markdown to `Businesses/canary-detect/finance/monthly-turnover-reports/turnover-{YYYY}-{MM}.md`
5. Render an HTML email with the locked-in 8-section layout (headline, revenue split, by category, by product as grouped table, top customers, payment state, reconciliation badge, outstanding)
6. Send via Gmail API helper to the three live recipients
7. Print sent message id + recipient list

## Capture results

The script prints sent message id, recipient list, and the markdown file path. Capture for the daily-note append.

If the script fails (Odoo auth, Gmail API down), continue gracefully — log the error in the daily note and stop. Do not retry automatically.

## Daily note append

Append to today's `/Users/peterashcroft/Second Brain/Daily/{today YYYY-MM-DD}.md` (re-read first):

```
## CD Monthly Finance Report (Automated)
- Sent: {message id} to Pete + Dave + Nicola
- Month: {month label} — gross €{X}, {N} invoices
- Top category: {category} (€{Y})
- Top customer: {name} (€{Z})
- Outstanding total: €{W} across {C} invoices
- Reconciliation: {PASS/FAIL}
- Markdown archived to Businesses/canary-detect/finance/monthly-turnover-reports/
```

If the script failed, log the error message in place of the success block.

## Notes

- The script is read-only against Odoo. Nothing is written to Odoo.
- The script overwrites the markdown file for the target month (idempotent — running again later for the same month produces the same/updated file).
- Source: `cd-monthly-finance-email.py` in `Library/processes/scripts/`.
- Layout decisions locked-in 2026-04-30 with Pete: monthly retains Top Customers + Payment State sections (which the weekly drops); same grouped By Product table; same reconciliation safety net.
- Companion task: `cd-weekly-finance-email` (Tue 18:00).