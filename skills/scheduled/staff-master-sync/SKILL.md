---
name: staff-master-sync
description: Sygma Staff Master nightly sync — cache JSON, regen person.md, regen trainer roster, diff into daily note
---

# Staff Master nightly sync

Run the Hub Staff Master → vault sync. Background-execute the helper script and report briefly.

## Execution, READ THIS FIRST

Bash sandbox has a 45s cap. Use Desktop Commander to run the script in background:

```
mcp__Desktop_Commander__start_process
  command: cd "/Users/peterashcroft/Second Brain" && nohup python3 Library/processes/scripts/staff-master-sync.py > /tmp/staff-master-sync-$(date +%Y%m%d).log 2>&1 & echo "PID=$!"
  timeout_ms: 5000
```

Then sleep 20-30s and tail the log file via Desktop Commander to see the result.

## What the script does

1. Pull all 9 tabs of Hub Staff Master via Sheets API.
2. Cache to `Library/sy-hr/Staff Master.json` (single document, all tabs).
3. Regenerate `Businesses/sygma-solutions/people/{kebab-name}.md` frontmatter for every Directory row (preserve body).
4. Regenerate `Library/processes/sygma-trainer-roster.yaml` from rows where `sub_business == "Sygma Training"`.
5. ~~Emit JSON caches for the Vercel dashboard~~ **RETIRED 2026-06-19** — dead dashboard, no readers; the script no longer emits them.
6. Diff against yesterday's cache; new starters / leavers / calendar_id changes / vehicle_reg changes / home_address changes get surfaced in today's `Daily/YYYY-MM-DD.md` under `## Staff master sync (Automated)`.
7. On 2 consecutive failures: raise a P2 Asana task in `Team-General / SY-General` (SY-Staff project archived 2026-06-06; the staff system now lives in the Portal/Hub — see [[staff-data-routing]]).

The script writes the daily-note section automatically — do not add a duplicate.

## After the script finishes

Report one short line to confirm the run, like:

`staff-master-sync OK: Directory=15 rows, people.md touched=15, roster trainers=8, diffs=3`

If the log shows FAILED, surface the error message clearly. The script handles the 2-strikes-and-P2 logic itself — do not raise the Asana task manually.

## Source-of-truth references

- System ID card: `Properties/Sygma Staff System/README.md` (build project archived 2026-06-06)
- Spec (frozen): `Projects/_archive/SY-Staff/files/staff-master-reshape-spec-2026-06-01.md`
- Routing doc: `Library/processes/staff-data-routing.md`
- Project README (archived): `Projects/_archive/SY-Staff/README.md`
- Hub Sheet: https://docs.google.com/spreadsheets/d/1o04hBPhGzyyD3q2kHusLG5cHgAIOfsD0v2zajoEgtf8/edit
- Payroll Master companion (not touched by this sync — owner-only): https://docs.google.com/spreadsheets/d/1ic1J58k7PApPxnRg48QbaJP61LvdtNAPPOEPUoLv2os/edit