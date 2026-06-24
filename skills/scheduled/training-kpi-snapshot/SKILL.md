---
name: training-kpi-snapshot
description: Weekly refresh of Sygma training KPIs from the live 2026 master Sheet, written to Businesses/sygma-solutions/training/kpis.md.
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

Run the Sygma training KPI snapshot refresh.

1. Execute: `python3 /Users/peterashcroft/Second Brain/Library/processes/scripts/training-kpi-snapshot.py`
2. The script downloads the live `2026` master (now a **native Google Sheet** since 2026-05-03 conversion, file ID `1_kS3-typOQs42PHNjWDe_x7uWqZWPVNeUNcTPCOATiU` -- in Sygma Hub / Course Records / Training Spreadsheets) via the Drive API export endpoint + service-account DWD, parses each monthly sheet (reading columns by header name), counts real course rows (excluding Train With Us Monthly + Virtual EUS Cards notice rows), and writes the snapshot to `Businesses/sygma-solutions/training/kpis.md`.
3. Read back the updated kpis.md and confirm:
   - The "Snapshot taken" date is today.
   - The "Completed-month average" line and headline number are present.
   - The monthly breakdown table has all 12 months.
4. Append a short entry to today's `Daily/YYYY-MM-DD.md` under a `## Training KPI snapshot (Automated)` heading with the headline figure (completed-month average courses + delegates, year-to-date totals, top customer).
5. Do NOT create tasks or send any email -- this is a silent vault-only refresh.

If anything fails (Drive download, parse, write), include the error in the Daily entry under `## Training KPI snapshot (Automated)` so it's visible at the next session start.

Source of truth for the spreadsheet: https://docs.google.com/spreadsheets/d/1_kS3-typOQs42PHNjWDe_x7uWqZWPVNeUNcTPCOATiU/edit
Cap used: 8 delegates per course (Sygma's standing rule, enforced from 22 April 2026).
Column reads use header-name lookup (resilient to future column inserts).