---
name: remittance-to-xero
description: Auto-forward remittance advice emails to Xero with a PDF attachment. Every 30 mins.
---

# Remittance to Xero auto-forwarder

Run the remittance-to-xero script. It scans Gmail for any remittance advice email (subject contains "remittance" OR sender contains "remittance"), and forwards each one to Xero's email-in inbox (`xero.inbox.!2!zhs.b16ycmse1tlm8n1h@xerofiles.com`) with a PDF attachment.

If the email has a PDF attachment, that PDF is forwarded as-is.
If the email is body-only (e.g. Severn Trent BACS notifications), the body is rendered as a PDF first and that PDF is attached.

After each successful forward the script applies a `Xero-Forwarded` Gmail label to the thread, so the same remittance never gets forwarded twice.

## Execution, READ THIS FIRST

Bash sandbox has a 45-second cap and remittance forwarding can run longer if there are several in the batch. Run via Desktop Commander instead:

```
mcp__Desktop_Commander__start_process
  command: 'nohup python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/remittance-to-xero.py" > /tmp/remittance-to-xero.log 2>&1 &'
  timeout_ms: 5000
```

Then poll the log file every 10s with `mcp__Desktop_Commander__read_file /tmp/remittance-to-xero.log` until you see `Done. processed=... skipped=... failed=...`.

## Reporting

When complete, post a single-line summary to this session's chat:

```
remittance-to-xero: processed=X skipped=Y failed=Z
```

If `processed > 0`, append a one-line bullet per forwarded remittance: `- {sender short name} | {subject snippet} | sent msg {id}`.

If `failed > 0`, surface the error from the log so a human can investigate.

No daily note appending, no tasks, no vault writes. Silent unless there's something Pete needs to know.

## Helper paths

- Script: `Library/processes/scripts/remittance-to-xero.py`
- Process doc: `Library/processes/email-workflow.md` (Xero remittance auto-forward section)
- Xero inbox: `xero.inbox.!2!zhs.b16ycmse1tlm8n1h@xerofiles.com`
- Dedup label: `Xero-Forwarded` (Gmail; created on first run if missing)