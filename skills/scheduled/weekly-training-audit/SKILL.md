---
name: weekly-training-audit
description: Weekly Sygma training audit -- master Sheet vs 11 trainer diaries vs booking forms, T-7..T+7 rolling window. Posts to Diary Management chat.
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

Run the weekly Sygma training audit.

Execute:

```
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/training-audit.py"
```

This script (no arguments = default rolling window T-7..T+7) does the full job:
- Downloads the live master `2026` (a **native Google Sheet** since 2026-05-03 conversion, file ID `1_kS3-typOQs42PHNjWDe_x7uWqZWPVNeUNcTPCOATiU`) via Drive API export endpoint.
- Reads all 11 trainer Google calendars via Pete's subscribed view.
- Reads the Completed Booking Forms 2026 Drive folder.
- Cross-references all three; produces a markdown report.
- Writes the canonical copy to `Businesses/sygma-solutions/training/audits/YYYY-MM-DD-weekly-audit.md`.
- Uploads a duplicate to Sygma Hub / Reports / Daily Audits 2026 (Drive folder `18-sO2NfiTEVImpov6e_YBomCeQPN9cWG`).
- Posts a summary message to the Diary Management Google Chat space (`spaces/AAQAFmmWBnI`).

Source of truth = the live master Sheet, the live trainer diaries, the live booking-forms folder. **Do NOT read or rely on any previous audit report from the vault.** Each weekly run is independent. The audit is stateless on purpose. Column reads use header-name lookup (resilient to column inserts).

After the script completes, append a one-line entry to today's `Daily/YYYY-MM-DD.md` note:

```
## Weekly Training Audit (Automated)

- Window: {window_start} .. {window_end}
- Master rows: N | Clean dates: N | Issues: N | Orphans: N | LOUD: 0/Yes
- Vault report: [[Businesses/sygma-solutions/training/audits/YYYY-MM-DD-weekly-audit]]
```

If the script errors out, do not retry silently. Capture the error, post a short failure note to the Diary Management space ("Weekly training audit failed at HH:MM -- error: ..."), and append the error to today's daily note.

Rule-refinement happens later in the week via the `audit feedback` flow when Pete triggers it -- not here. The audit posts; Sue replies in chat; Pete picks up the feedback at his discretion.

## Related lessons

- [[Library/lessons/2026-05-31-sy-feedback-reporting-invariant-system]] — invariant + cron-safety meta-lesson. Sister Sygma cron (sy-feedback-reporting) uses an invariant system so its Monday cron can't re-make a prior session's mistakes. Same discipline applies to this weekly-training-audit cron when it surfaces rule refinements.

SOP: `Businesses/sygma-solutions/training/sops/weekly-training-audit.md`