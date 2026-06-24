---
name: marcos-kevin-calendar-provision-retry
description: One-time Mon 8 Jun 08:00: re-run the Marcos + Kevin calendar provision watchers (idempotent) so day-one logins get their calendar shares applied. Self-disables after firing.
---

# One-time: Marcos + Kevin calendar provision retry (Mon 8 Jun 08:00)

Two new Google Workspace accounts were created 6 Jun (marcos.knight@canary-detect.com — starts today Mon 8 Jun; kevin.morley@sygma-solutions.com — starts 29 Jun). Their calendar shares are applied by idempotent watcher scripts that poll until Google provisions Calendar on first login. The 6 Jun watchers timed out (6h window) before either user logged in. This one-shot re-runs both for Marcos's first day.

## Execution (Desktop Commander, NOT workspace bash)

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "/Users/peterashcroft/Second Brain/Projects/Team-General/CD-General/files/scripts/marcos-calendar-provision.py" >/dev/null 2>&1 & nohup python3 "/Users/peterashcroft/Second Brain/Projects/Team-General/CD-General/files/scripts/kevin-calendar-provision.py" >/dev/null 2>&1 & echo launched
  timeout_ms: 10000
```

Both scripts are idempotent (skip ACLs that already exist) and log to `.log` files alongside themselves. They poll every 2 min for up to 6h and apply: Marcos — CD-team writer ACLs ×5 + Pete/Nicola calendarList inserts + Atlantic/Canary tz; Kevin — trainer-pattern owner ACLs ×7 + CD-domain freeBusy + Europe/London tz + calendarList ×7.

## After launching

1. Check each log's tail after ~5 min (`tail -5 {script-dir}/{name}-calendar-provision.log`). If a log already shows `=== done ===` from an earlier run, that account is complete — don't relaunch it.
2. Append one line to today's daily note (READ it first — other crons write to it) under `## Calendar provision retry (Automated)`: launched/already-done status per account.
3. If Marcos's calendar is still unprovisioned by the time the watcher window closes (~14:00), note in the daily-note line that a further manual re-run is needed once he has logged in — his work email login is part of his day-one setup at the CD office.

No questions to ask; no email to send.