---
name: helper-script-registry-refresh
description: Weekly Monday 07:00 — regenerate the helper-script registry table in Library/processes/external-service-routing.md from the current state of Library/processes/scripts/. Auto-picks up any new *-api.py helpers without manual edits.
---

Weekly refresh of the helper-script registry. Single short job — runs the registry regenerator.

## Execution -- READ THIS FIRST

This task uses Desktop Commander for the script run, not workspace bash. Workspace bash has a 45s sandbox cap; Desktop Commander runs natively from the host.

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/helper-script-registry.py" > /tmp/helper-script-registry.log 2>&1 & echo "PID=$!"
  timeout_ms: 5000
```

Then poll completion via `ps -p $PID` until process exits, then read `/tmp/helper-script-registry.log`.

## What the regen does

The script walks `Library/processes/scripts/` matching `*-api.py|*-api.sh`, parses each helper's top-of-file `# Scope:` docstring, and regenerates the table in `Library/processes/external-service-routing.md` between the `<!-- BEGIN HELPER-SCRIPT-REGISTRY AUTOGEN -->` / `<!-- END HELPER-SCRIPT-REGISTRY AUTOGEN -->` markers.

Idempotent. If nothing changed, the script prints "OK: external-service-routing.md table matches N helpers on disk." and exits 0. If drift exists, it regenerates and prints "OK: regenerated table in external-service-routing.md (N helpers)."

## Expected output

Log file ends with one of:
- `OK: external-service-routing.md table matches N helpers on disk.` (no changes, nothing to do)
- `OK: regenerated table in external-service-routing.md (N helpers).` (drift caught + fixed)

## On error

Email Pete (pete.ashcroft@sygma-solutions.com) via gmail-api.py with subject `helper-script-registry-refresh: failed` and the log contents. Otherwise no email — silent success.

## Why this exists

Helper-first discipline. Pete wires API helpers (`drive-api.py`, `gmail-api.py`, `sheets-api.py`, etc.) and Claude should reach for them before any MCP connector or Zapier. The routing doc + auto-registry make this mechanical: add a new `xyz-api.py` to `scripts/` with a top docstring, this weekly job picks it up, every skill + CLAUDE.md sees it via the routing-doc wikilink. No manual edits.

See [[Library/lessons/2026-05-16-helper-first-external-service-discipline]] for full context.
