---
name: vault-drift-check
description: Monthly vault-wide drift detection. Walks the vault and cron registry, flags anything that doesn't match its convention. Saves report to Library/audits/.
---

---
name: vault-drift-check
description: Monthly vault-wide drift detection
---

# Vault Drift Check

Walk the vault and cron registry, flag anything that doesn't match its convention. Catches the silent-rot pattern that built up across April-May 2026 (29 missing project READMEs, 12 missing customer/supplier READMEs, scheduled-task lockstep drift, orphan scripts, stale references to deleted shared drive). See [[Library/lessons/2026-05-03-vault-rot-audit-and-drift-prevention]] for context.

## Execution -- READ THIS FIRST

This task uses Desktop Commander for the script run, not workspace bash. Workspace bash has a 45s sandbox cap that may not finish a full vault walk. Desktop Commander runs natively.

Use this pattern:

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/vault-drift-check.py" > /tmp/vault-drift-check.log 2>&1 &
  timeout_ms: 5000
```

Then poll completion via `ps -p $PID` (PID printed by `echo $!` in the start command) until process exits.

Once finished, read `/tmp/vault-drift-check.log` to see the issue count + report path. The report is also written to `Library/audits/{today}-vault-drift-check.md`.

## What to do with the report

- If 0 issues: append a one-line confirmation to today's `Daily/YYYY-MM-DD.md` ("Vault drift check: 0 issues") and stop.
- If non-zero issues: 
  1. Append the issue count + the report path to today's `Daily/YYYY-MM-DD.md` under a `## Vault drift check (Automated)` heading.
  2. For each category in the report, summarise the count and the top 3 issue lines.
  3. If any category has more than 5 issues, flag for Pete to action in a focused session.
  4. Send a brief email to pete.ashcroft@sygma-solutions.com with the report inline (HTML, formatted as in the report file). Subject: "Vault drift check ({N} issues) -- {today}".

## What NOT to do

- NEVER attempt to fix issues automatically. The task surfaces drift; fixes happen in a deliberate Pete-driven session.
- NEVER delete files or folders flagged as orphan. They might be intentional.
- NEVER edit the canonical SKILL.md prompts at `~/Documents/Claude/Scheduled/`. The drift-check is read-only against that path.

## Cadence

Monthly cron `0 7 1 * *` (1st of each month, 07:00 Atlantic/Canary local). If a quarter goes by with consistent zero-issue runs, switch to quarterly (`0 7 1 */3 *`) to reduce noise.

## Cross-references

- Implementation: `Library/processes/scripts/vault-drift-check.py`
- Lessons: [[Library/lessons/2026-05-03-vault-rot-audit-and-drift-prevention]]
- First-pass findings: [[Library/audits/2026-05-03-vault-md-audit]]
