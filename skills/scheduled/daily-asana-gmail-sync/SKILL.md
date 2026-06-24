---
name: daily-asana-gmail-sync
description: Daily 07:15 Gmail↔Asana reconciliation: runs sync-asana.py wrapper, auto-creates tray-orphan tasks (best-match routing), strips labels for completed tasks, writes report + suggestions to the daily note. No questions, no sends.
---

# Daily Asana–Gmail sync (07:15)

> [!note] Verify this cron's purpose post-cutover (Phase 5). Pete is off Asana — his tasks are now the CC task store (`public.tasks`). This cron reconciles Gmail labels against *Asana* task state; that premise may be moot now. Do not delete it without confirming; the orphan-creation / escalation task ops below have been converted to `public.tasks`.

Reconciles Gmail workflow labels (Actions / Delegated) with Asana task state so Pete's tray and task list agree before the 07:30 briefing reads them. Operating manual: Library/processes/email-workflow.md. Skill reference: Library/skills/asana-gmail-sync/SKILL.md (Cron-mode section). One-sentence rule: Actions = waiting on Pete to respond by email; everything else = Asana only.

## Execution — READ THIS FIRST

Run script invocations via Desktop Commander, NOT workspace bash (45s sandbox cap silently truncates). Pattern:

```
mcp__Desktop_Commander__start_process
  command: cd "/Users/peterashcroft/Second Brain" && nohup python3 Library/processes/scripts/sync-asana.py > /tmp/daily-asana-gmail-sync.log 2>&1 & echo "PID=$!"
  timeout_ms: 8000
```

Then poll: `sleep 35; tail -40 /tmp/daily-asana-gmail-sync.log` (repeat until the `═══ sync-asana run ═══` report block is complete). Reference: Library/lessons/2026-05-02-scheduled-task-skill-md-uses-dc.md.

## Steps

1. **Run the wrapper** (above). It executes Steps 1/3/4/5/7/8 deterministically: strips Gmail labels from Asana-closed tasks, closes tasks whose threads lost their labels (with audit comments; [no-sync-close] marker + Team-Finances tasks exempt), checks delegations, finds orphan candidates, runs parity.
2. **Step 6 orphans** (threads labelled Actions/Delegated with no task): auto-create per the asana-gmail-sync skill Step 6 — these are tray-class tasks (NO [no-sync-close] marker), default P2 + due today+7 (Atlantic/Canary), name = action verb + WHO + WHAT, notes = Mimestream link (https://links.mimestream.com/g/pete.ashcroft@sygma-solutions.com/t/{thread_id}) then Gmail link (https://mail.google.com/mail/u/0/#all/{thread_id}) + summary + routing trail. Pete is off Asana — create these in the CC task store (`public.tasks`) via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py` (`INSERT INTO tasks (id,name,priority,due_on,entity_slug,project_slug,status,source,notes) VALUES (gen_random_uuid(),…,'P2',(current_date+7),…,'todo','claude',…)`). **Cron-mode routing → set `project_slug` (the NAME) by best-match via the existing-label fallback chain (Projects label → that project_slug; Customers/Suppliers → `Team-General`, SY-Clancy → `SY-Clancy`; Invoices → `Team-Finances`; Personal/PA-* → `PA-General`). Entity follows the prefix (`SY-`/`Team-` → Sygma, `CD-` → Canary Detect, `PA-` → Personal). NEVER create labels, buckets, or projects. Ambiguous/no-label → `project_slug='PA-General'` (entity Personal) and flag in the daily-note block for interactive re-route.** Run vault-enricher per orphan: `python3 Library/processes/scripts/vault-enricher.py {thread_id} "{routed-vault-folder}"` (via DC).
3. **Suggestions** (auto-filter / demand-driven label / parity drift / homeless threads from the report): do NOT act, do NOT ask. Collect them for the daily-note block.
4. **Daily note**: READ Daily/{today-YYYY-MM-DD}.md FIRST (other crons write to it; create with standard daily frontmatter if missing), then append:

```
## Asana sync (Automated)
- 07:15 run | tasks closed: {n} ({names}) | labels stripped: {n} | exempt-skips: {n} | orphans created: {n} ({names+routing}) | delegations open: {n} | parity: {ok|drift detail} | suggestions: {list or none}
```

If the run FAILED, the block reads `- 07:15 run | FAILED: {one-line reason}` instead.
5. **Failure escalation**: before writing the block, check yesterday's daily note for a `FAILED` marker in its `## Asana sync (Automated)` block. If yesterday failed AND today failed → create a P2 task "Investigate daily-asana-gmail-sync failures (2 consecutive)" in the CC task store (`public.tasks`, Pete is off Asana): `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "INSERT INTO tasks (id,name,priority,due_on,entity_slug,project_slug,status,source,notes) VALUES (gen_random_uuid(),'Investigate daily-asana-gmail-sync failures (2 consecutive)','P2',(current_date+7),'Sygma','Team-General','todo','claude','Auto-raised after 2 consecutive sync failures')"` (project_slug NAME `Team-General`, due today+7).

## Hard rules

- NEVER sweep. NEVER offer sweep.
- NEVER create Gmail filters or labels (suggestions go to the daily note).
- NEVER send email. Delegation chasers go to Drafts only.
- NEVER close a task whose notes contain [no-sync-close] or that lives in Team-Finances (the wrapper enforces this — do not re-derive Step 4 manually; if the wrapper fails, report the failure rather than hand-rolling the algorithm).
- NEVER use em dashes or double dashes in anything written to email drafts.