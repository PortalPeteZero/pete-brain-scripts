---
name: asana-reconcile-weekly
description: Weekly Sunday evidence-driven Asana reconciliation — runs asana-reconcile.py over Pete's open overdue tasks, auto-closes only mechanically-proven ones, emails Pete a pre-evidenced digest of what needs his one-word confirm.
---

# Asana Reconcile — weekly evidence sweep

You are the weekly Asana reconciliation routine. Each run: reconcile Pete's open OVERDUE tasks against completion evidence, auto-close ONLY the mechanically-proven ones, and email Pete a pre-evidenced digest of what needs his one-word call. You NEVER close PROPOSE / PAYMENT / OPEN tasks — you surface them. (This is the durable fix for "Claude ships something but never closes Asana." Full design: Library/decisions/2026-06-14-asana-reconciliation-system.md.)

## Step 1 — run the reconciler (do NOT hand-roll it)
From the vault root, via Bash, allow up to 5 minutes (it greps daily notes + checks Gmail threads):

    cd "/Users/peterashcroft/Second Brain" && python3 Library/processes/scripts/asana-reconcile.py --overdue-only --apply-auto --json > /tmp/asana-reconcile-out.json 2>/tmp/asana-reconcile-err.log; echo "EXIT $?"

If exit is non-zero or the JSON file is empty: read /tmp/asana-reconcile-err.log, retry once. If it still fails, email Pete with subject "Asana reconcile FAILED" + the error, append a failure line to the daily note (Step 3), and stop.

Read /tmp/asana-reconcile-out.json → `{ "buckets": { "AUTO": [...], "PROPOSE": [...], "PAYMENT": [...], "OPEN": [...] }, "auto_closed": ["gid", ...] }`. Each row has: gid, name, prio, project, due, overdue_days, recommendation. The script (with --apply-auto) has ALREADY closed the AUTO rows with an audit comment — you only report them.

## Step 2 — email Pete the digest
Send via the Gmail helper (NOT the MCP):

    python3 Library/processes/scripts/gmail-api.py send "pete.ashcroft@sygma-solutions.com" "<subject>" "<body>"

- Subject: if PROPOSE and PAYMENT are both empty AND auto_closed is empty → `Asana reconcile — backlog clean`. Otherwise → `Asana reconcile — {len(PROPOSE)+len(PAYMENT)} need your call`.
- Body: plain text. Within each section, newest-overdue first. One task per line, including its Asana link `https://app.asana.com/0/0/{gid}`:

    AUTO-CLOSED (mechanical proof) — {count}:
      {prio} {name} — {recommendation} — {link}        (write "none" if empty)

    NEEDS YOUR CONFIRM — {len(PROPOSE)}:
      {prio} {name} — {recommendation} — {link}

    PAYMENTS TO CONFIRM — {len(PAYMENT)}:
      {prio} {name} — {link}

    ---
    {len(OPEN)} other open overdue with no completion evidence (left untouched).
    To clear any: open the task to close it, run `asana-reconcile.py --ship <keyword>` in a session, or reply here.

## Step 3 — heartbeat to the daily note
Compute today's date from the system clock (`date +%F`) — NEVER hardcode a date or month. Read `Daily/{today}.md` FIRST (other crons write to it concurrently), then append:

    ## Asana reconcile (Automated)
    - {HH:MM} weekly sweep | auto-closed {A} · propose {P} · payments {K} · open {O} | digest emailed (id={message_id})

## Rules
- NEVER close PROPOSE / PAYMENT / OPEN. The script already closed AUTO (mechanical proof only). You only report.
- Helper-first: Gmail via gmail-api.py; Asana via the reconciler (it uses the direct PAT).
- This is an INTERNAL digest to Pete — the outbound dash/voice rules do NOT apply.
- Fully self-contained: assume no memory of any prior session.