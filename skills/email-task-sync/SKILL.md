---
name: email-task-sync
description: >
  Reconciliation engine for Pete's email workflow. Bidirectional sync between
  Gmail labels (`Actions`, `Delegated`) and the Command Centre task table
  (`public.tasks`). An `Actions`-labelled thread with no task is the EXPECTED
  state (the label IS the record) — the sync SURFACES those for awareness, it
  never auto-creates a task. Marks a CC task done when its `Actions`/`Delegated`
  label is removed in Gmail, and strips the Gmail label when a CC task is marked
  done. Detects auto-filter and demand-driven label opportunities and label↔home
  parity drift, surfacing them for Pete's confirmation. Closure exemptions:
  `[no-sync-close]` marker + the Team-Finances blanket; every closure leaves an
  audit note. Idempotent — safe to run repeatedly. Triggered by "sync" / "sync
  tasks" / any reconcile request, and offered (opt-in) at the end of every triage.
  (Asana belongs to Jane and her work only — this skill never touches it.)
---

<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Sheets / Docs operation in this skill, see [[external-service-routing]]. Helper-first. -->

# email-task-sync

Reconciliation engine for the email workflow: a bidirectional sync between Gmail labels (`Actions`, `Delegated`) and Command Centre task state in **`public.tasks`**. All task CRUD is a `cc-sql.py` INSERT / SELECT / UPDATE against `public.tasks` (`name`, `priority`, `due_on`, `entity_slug`, `project_slug`, `notes`, `status`). **Asana is Jane's, for her work only — this skill never connects to it.**

> **Operating manual**: `[[email-workflow]]` (full system — verbs, decision lines, sweep behaviour, delegation flow).
> **Routing rules**: `[[vault-routing]]`. Gmail-side rules: `[[gmail-label-scheme]]`. **Version history**: `[[CHANGELOG]]`.

## When to invoke

User says any of: "sync" · "sync tasks" · "reconcile my tasks" · "check my delegations" · "close completed tasks" · "clean up stale labels".

Also: **offered at the end of every `triage` session** (opt-in y/n after the Actions walker — triage never auto-chains; see inbox-triage Step 8b). This skill runs **on demand only** — there is no scheduled cron (an earlier 07:15 `daily-asana-gmail-sync` was specced but never deployed; the live registry has no email/task sync cron).

## Dependencies

- Gmail helper: `/tmp/pbs/gmail-api.py` · Calendar helper: `/tmp/pbs/calendar-api.py`
- CC task store: `public.tasks`, CRUD via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py`.
- State file: `Library/processes/email-workflow-state.md` (declined suggestions, sender→label observations).
- Entity homes for parity/filing: the entity's **Google Drive** folder (`drive_files` via `cc-sql.py`) + its `vault_notes` record. (The old local `Customers/`/`Suppliers/`/`Projects/` vault tree is retired — match against Drive + `vault_notes`.)

## Core principles

1. **Idempotent.** Safe to run repeatedly. No double-create, no double-close.
2. **Bidirectional.** Mark a CC task `status='done'` → its Gmail label leaves. Remove `Actions`/`Delegated` in Gmail → the CC task is set `status='done'`.
3. **Surface, never auto-create.** An `Actions`-labelled thread with no task is the expected state — the label IS the record (an Action just means "an email Pete owes a reply to"). The sync surfaces these for awareness; it NEVER creates a task from a label. A task is created only when Pete explicitly asks (a reply gated on real work — the overlap/de-tray case), and then it carries `[no-sync-close]`. New labels, folders, filters always need Pete's confirmation via the proposal pattern.
4. **`public.tasks` is the source of truth for task STATE** (priority, completion). Gmail labels are a view — they follow CC task state.
5. **Re-prioritisation lives in the CC only.** Pete edits the `priority` column directly; there's no Gmail sub-label to swap. The close-on-label-removed rule fires only when NEITHER `Actions` NOR `Delegated` is on the thread. **Changing priority does NOT recompute `due_on`** — a P3→P1 change keeps the existing due date unless Pete edits it.

## Execution: ALWAYS call the deterministic wrapper first

> [!important] The skill's first action MUST be to run `/tmp/pbs/email-task-sync.py`.
> The wrapper is the deterministic Python implementation of Steps 1, 3, 4, 5, 7, 8 — every run executes the same 8-step algorithm, no prose interpretation. Step 6 surfaces `Actions`-without-task threads for awareness only (no task creation). **Don't re-derive the steps in bash — always run the wrapper.**

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/email-task-sync.py            # run + apply changes
VAULT=/tmp/pbs python3 /tmp/pbs/email-task-sync.py --dry-run  # report only, no mutations
VAULT=/tmp/pbs python3 /tmp/pbs/email-task-sync.py --json     # raw JSON (for LLM chaining)
```

**Exit codes:** `0` = complete, no decisions needed · `1` = complete, Step 6 surfaced `Actions`-without-task threads for awareness (informational — no action required) · `2` = fatal error (auth, API, filesystem).

**After running the wrapper:**
1. Read its output (closures, label strips, parity, surfaced Actions-without-task threads).
2. The Step 6 surfaced threads are the EXPECTED state — the Actions label is the record. Do NOT create tasks for them. Only if Pete explicitly asks to track one as work (the overlap/de-tray case) create a `Task this`-style task with `[no-sync-close]` (route it like inbox-triage's `Task this`).
3. Report the consolidated outcome to Pete in the format at the bottom of this file.

Why the wrapper is mandatory: [[Library/lessons/2026-05-20-sync-must-call-wrapper-not-re-derive-steps]].

## The full sync algorithm (reference — implemented by the wrapper)

Each step is idempotent and reports what changed.

### Step 1: Pull linked tasks (BOTH open AND recently-done) from `public.tasks`

Query `public.tasks` **twice** — open tasks, and tasks marked done in the last 30 days — and merge into the linked set. Both have linked Gmail threads the algorithm acts on.

```bash
# Open tasks (drives Step 4: close on Gmail-side label removal)
VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT id, name, priority, due_on, entity_slug, project_slug, notes, status FROM tasks WHERE status != 'done'"
# Recently-done tasks (drives Step 3: strip Gmail label after CC-side closure)
VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT id, name, notes, status, updated_at FROM tasks WHERE status = 'done' AND updated_at > now() - interval '30 days'"
```

For each task, extract Gmail thread IDs from `notes` (both `mail.google.com/mail/u/0/#[a-z]+/{thread_id}` and `links.mimestream.com/g/{email}/t/{thread_id}` forms). Carry forward `id`, `name`, `status`, `due_on`, `entity_slug`, `project_slug`, the thread IDs, and `priority`.

**Both-sides query is non-negotiable.** If Step 1 only pulls open tasks, Step 3 has no done tasks to act on — the Gmail label persists after CC-side closure, so the thread wrongly lingers in the Actions tray after its task is done. See [[Library/lessons/2026-05-20-sync-must-query-both-open-and-closed-tasks]].

### Step 2: Priority reconciliation — no-op

No Gmail sub-labels for priority; the `priority` column is the sole authority. Step number preserved for ordinal stability.

### Step 3: Completion reconciliation (CC → Gmail)

For each linked task whose `status = 'done'`, strip the `Actions` or `Delegated` label from the Gmail thread (whichever is present) via `gmail-api.py modify_thread` with `remove=[label_id]`.

When completion strips the last workflow label:
- Thread still has a filing label (`Customers/*`, `Suppliers/*`, `Projects/*`, `Invoices/*` (legacy → re-route to Team-Finances at sync time), `Accreditations/*` (legacy → re-route to Team-General/SY-General), or any Mode-A top-level): no further action — it stays archived under its home.
- Thread has NO filing label: don't silently orphan. Add to the Step 8 report: "{subject} has no filing home — file under X, archive, or bin?". Pete decides.

### Step 4: Bidirectional close (Gmail → CC)

For each linked task still open (`status != 'done'`), check the linked thread's current labels:
- Has `Actions` OR `Delegated` → leave it (still active).
- Has NEITHER → **set the task `status='done'`** (`UPDATE tasks SET status='done' WHERE id=...`). Pete handled it in Gmail and removed the workflow label; sync follows.

**Exemptions (Action/Task split, locked 2026-06-06 — the wrapper enforces both):**
1. **`[no-sync-close]` marker in task notes** → NEVER close on label state. Two uses: (a) Pete-sent watch tasks (chase-if-no-reply; thread never had Actions); (b) CC-only tasks — bills, cert batches, work items created by `Task this` or de-trayed by Pete. Their work happens outside email, so label state must never close them.
2. **Team-Finances blanket** — any task with `project_slug='Team-Finances'` is exempt regardless of marker. A bill is never a reply.

**Closure audit note** — every task Step 4 closes gets a note appended: *"Closed by sync — Actions/Delegated label removed in Gmail, {date}. If this was a tray clear-out rather than completion, reopen (`status='open'`) and ask Claude to mark it [no-sync-close]."* Closures are also listed in the run's daily-note block.

**The rule behind the split: Actions = waiting on Pete to respond by email. Everything else = a CC task only.** Record: [[Library/decisions/2026-06-06-actions-label-reply-only]].

**Multi-thread tasks**: a task can link multiple threads (parsed from notes). Close only when ALL linked threads have lost BOTH `Actions` AND `Delegated`.

Edge cases:
- Thread trashed → all labels stripped → task closes (action is done by definition).
- Thread deleted (Gmail URL 404) → close the task with note "Source thread deleted from Gmail."
- Thread archived but still has `Actions` → no closure (archive removes INBOX, not the workflow label).

Report: "Closed X via Gmail-side completion: {task name list}".

### Step 5: Delegation reply check

For each open task on the Delegated track — `project_slug='Team-General'` with a `[delegated]` marker in notes:

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT id, name, notes FROM tasks WHERE status != 'done' AND project_slug = 'Team-General' AND notes LIKE '%[delegated]%'"
```

1. Extract `delegatee` email + `forwarded_at` (or fall back to `created_at`) from notes.
2. Pull the thread's messages (`get_thread`); look for messages where `internalDate` > `forwarded_at` and `from:` matches the delegatee (not Pete, not bots).
3. Filter auto-replies by subject `^(Out of Office|Auto.*Reply|Automatic Reply|Vacation).*` (case-insensitive).
4. Genuine reply → set task `status='done'` + remove `Delegated` label + append the audit note ("Closed by sync — {delegatee} replied {date}"). Report e.g. "✓ Jane replied to the Clancy Q2 delegation — closed the task." (Jane here = a delegatee Pete forwarded to.)
5. No reply AND follow-up date passed → flag for chase (draft only, never auto-send): draft a polite chaser to Gmail Drafts via `gmail-api.py draft`. Report "⚠ 3 delegations overdue — drafted chasers to Drafts."

### Step 6: Surface `Actions`/`Delegated` threads with no task (awareness only — NO auto-create)

Find Gmail threads labelled `Actions` or `Delegated` with NO matching task in `public.tasks` (no row whose `notes` link to the thread id).

**This is the expected, correct state — not an orphan to fix.** An `Actions` thread with no task just means "an email Pete owes a reply to" (he labelled it, usually from his phone). The Actions label IS the record — the morning brain-flag and the triage Actions walker surface the tray. **Do NOT create a task.** List these threads in the Step 8 report for awareness only (subject + age + which label).

**Only when Pete EXPLICITLY asks** to track one as work — a reply gated on doing something first (the overlap / de-tray case) — create a `Task this`-class task carrying the **`[no-sync-close]`** marker (so the label and task stay independent), routed exactly like inbox-triage's `Task this`. The smart-routing chain below is reference for THAT case only; it is **never run automatically**.

```bash
# ONLY when Pete asks to task an Actions thread (overlap / de-tray). Note the [no-sync-close] marker.
VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "INSERT INTO tasks (name, priority, due_on, entity_slug, project_slug, notes, status) VALUES ('<action verb + WHO + WHAT>', 'P2', '<today+7d>', '<entity_slug>', '<project_slug NAME>', '<Mimestream link>\n<Gmail link>\n<Finder link>\nsummary + routing trail\n[no-sync-close]', 'open')"
```

- **Routing discipline**: the chain below implements the decision tree at `[[vault-routing#task-routing-decision-tree]]` — related project first, else `{prefix}-General`; project escalation only ever by proposal to Pete, never auto-created.
- **Priority**: no Gmail priority signal → default **P2**. Pete can edit the row. `Delegated`-only orphans → leave `priority` unset.
- **Smart-routing fallback chain (Gmail label → `entity_slug` + `project_slug`):**
  1. `Projects/*` label → `project_slug` = the project NAME (e.g. `'CD-Website'`); `entity_slug` = the same. (Demand-driven sub-project labels → the parent project NAME.)
  2. `Customers/*` or `Suppliers/*` only → `entity_slug` = the customer/supplier slug; `project_slug` = the matching `{prefix}-General`:
     - SY / CD / EA → `project_slug='Team-General'`
     - AT (Ashcroft Family) → `project_slug='AT-General'`
     - **SY-Clancy exception**: `Customers/SY-Clancy` → `project_slug='SY-Clancy'`, `entity_slug='SY-Clancy'` (its own project NAME, not Team-General).
  3. `Invoices/*` (legacy label) → `project_slug='Team-Finances'`; `entity_slug` = the invoice owner (`CD-Invoices` / `SY-Invoices` / `EA-Payments`). Record To-Pay/Overdue/Awaiting in `notes`; default "To Pay". (Auto-paid items route to `Receipts`, not Invoices — see CLAUDE.md.)
  4. `Accreditations/*` → `project_slug='Team-General'`, `entity_slug` = the accreditation slug (e.g. `'SY-ProQual'`, `'SY-EUSR'`).
  5. `Personal/PA-{area}` → `project_slug='PA-General'`; `entity_slug` = the area slug (`PA-Scouts`, `PA-Los-Claveles`, `PA-Freemasonry`, `PA-Finance`, `PA-PassionFit`); record the area in `notes`.
  6. **Demand-driven label suggestion**: no filing label but sender domain / subject keyword matches an existing entity home (a Drive folder or `vault_notes` entity) with no Gmail label → surface in Step 8 as a "create label?" suggestion (check the declined list first). **For project labels especially, ASK before creating** unless sustained volume is clear — default to routing through `{prefix}-General`. Sender hints: BSO/scouts.org.uk/"Jamborette" → `PA-Scouts`; Provincial Grand Master/"Tyldesley Lodge"/"Sincerely & Fraternally" → `PA-Freemasonry`; Los Claveles utility codes (LPLIR/LPLR)/committee → `PA-Los-Claveles`; HSBC/pension/HMRC Self Assessment → `PA-Finance`.
  7. No filing label and no match → default `project_slug='PA-General'` (no `entity_slug`). Pete cleans up periodically — the trade for zero friction.
- **Task name**: action verb + WHO + WHAT (e.g. "Reply to Wayne (Clancy) about UKPN DSR meeting time", "Pay Suministros Pantera invoice 26P15531"). Don't dump the raw subject.
- **Task notes** (Mimestream first):
  1. Mimestream: `https://links.mimestream.com/g/pete.ashcroft@sygma-solutions.com/t/{thread_id}`
  2. Gmail web: `https://mail.google.com/mail/u/0/#all/{thread_id}`
  3. Finder link to the entity's working folder (when tied to a project/customer/supplier) via `/tmp/pbs/vault-finder-link.py {entity} [section]`; omit if there's no matching folder. Then a brief summary + which fallback step matched + "priority defaulted to P2 — edit the `priority` column if needed". Reason: one-click to the source thread + the working folder. See [[Library/lessons/2026-05-20-asana-tasks-include-mimestream-link]].
- **MUST run vault-enricher on the source thread** as part of creating the task (when Pete asks), not an afterthought:
  ```bash
  VAULT=/tmp/pbs python3 /tmp/pbs/vault-enricher.py {thread_id} "{routed-entity-home}"
  ```
  Report attachments pulled + extract path + contacts added in Step 8. See [[Library/lessons/2026-05-20-must-call-vault-enricher-not-just-reference]].
- **Due date** (`due_on`), Atlantic/Canary: P1 → +2d · P2 → +7d (default) · P3 → +30d · P4 → none · `Delegated`-only → none (Step 5 handles timing). Pete can edit `due_on` after creation.
- **Owner**: always Pete — the table is Pete's.

Report: "N `Actions` threads have no task (expected — surfaced for awareness, no task created): {subject + age list}." Only if Pete then asks to task one: "Created 1 `Task this` (with `[no-sync-close]`): {name}."

### Step 7: Pattern detection

**7a — Auto-filter suggestions** (compute fresh from Gmail each run; do NOT store in state). For each label of interest: `gmail-api.py search "label:{label} newer_than:90d"` grouped by sender domain; `gmail-api.py list_filters()` for senders already covered. For each domain→label pair with count ≥ 3, no existing filter, no recorded decline (check `email-workflow-state.md > Declined auto-filter suggestions`): surface in Step 8 with a proposed filter (`from:*@domain OR to:*@domain → apply {label}`, apply only, never remove INBOX). y → create + set `auto_filter: true`; n → record decline. **NEVER auto-create filters.**

**7b — Strategic routing patterns**: when sync sees a routing decision repeated, after 5 confirmed-without-override occurrences propose promotion into the Master routing matrix. Pete decides.

**7c — Filter broadening**: thread has the right label but the sender matches no existing filter (e.g. uncovered subdomain) → surface a broadening suggestion. Same confirm-before-create rule.

### Step 8: Parity check + consolidated report

Cross-system parity scan:
- **Customers/Suppliers labels ↔ entity homes**: every `Customers/{slug}` / `Suppliers/{slug}` label has a matching Drive folder / `vault_notes` entity, and vice versa. Surface drift.
- **Project label ↔ home ↔ CC project**: every demand-driven `Projects/{slug}` label has a matching home + a `project_slug` in use on `public.tasks`. Surface drift.
- **Entity home without a label that should have one**: customer/supplier homes (parity violation — surface immediately); project homes with 3+ matching emails in 30d but no label (demand-driven — surface as suggestion).

Output format:

```
sync tasks complete

PRIORITY moves: — (no-op: priority lives in the CC `priority` column only)

CLOSED from CC completion (status='done'):
- {task name} — closed, Gmail Actions label stripped, thread filed under {Customers/SY-Clancy}

CLOSED from Gmail-side completion:
- {task name} — Actions label removed in Gmail, set CC task status='done'

DELEGATED:
- ✓ Jane replied to {thread} — closed (2 days ago)
- ⚠ 3 overdue chasers drafted to Drafts: {subject} to {delegatee} — 9 days overdue …

ACTIONS WITH NO TASK (expected — surfaced for awareness, NO task created):
- {subject} — 2d in the tray (Actions)
- {subject} — 5d in the tray (Actions)
  (the Actions label is the record; reply via the triage Actions walker. Say "task this" on any of them only if a reply needs work done first.)

DEMAND-DRIVEN LABEL suggestions:
- 3 emails from *@partnerco.co.uk match the CD-Partnership-Programme home (no label exists)
  Create Projects/CD-Partnership-Programme + auto-filter? (y / n)

AUTO-FILTER suggestions:
- 4 emails *@newsupplier.co.uk → Suppliers/CD-NewSupplier (no filter)  Create filter? (y / n)

PARITY CHECK:
  ✓ All Customers/* and Suppliers/* labels match entity homes (3 customers, 5 suppliers)
  ⚠ 1 project home without a Gmail label: CD-Pool-Jobs (1 email in 30d, below 3-threshold)

CALENDAR revisit:
- 1 closed thread had a flight mention not on the calendar: ✈ {summary} — propose adding? (y / n)

STRATEGIC PATTERNS (vault-routing > observed):
- "Insurance docs from AXA" → Team-General (entity_slug=CD-General) × 4 — promote to Master matrix? (y / n)

Any decisions needed? (list pending y/n questions)
```

If nothing changed: `sync tasks complete — no changes (X linked tasks checked, all in sync, parity clean)`.

## Rules

1. **Dry-run if unsure.** `sync --dry-run`, or on the first run of a session, shows the plan without executing.
2. **Never permanently delete.** `DELETE /threads/{id}` is irreversible — always confirm.
3. **Never send email without confirmation.** Chaser drafts go to Drafts; Pete sends manually.
4. **Always confirm filing when a thread is homeless.** Never silently leave a thread without a filing label once its workflow label is stripped.
5. **Preserve label IDs.** Use `patch_label` renames, never delete-and-recreate, to keep associations.
6. **`public.tasks` is the source of truth for priority and completion.** Gmail labels are a view on top.
7. **Calendar revisit on completion sweep.** A closed thread with a flight/hotel/car/meeting mention not on the calendar → flag it (don't auto-add). Default tz Atlantic/Canary, default calendar Pete's primary; "put this in {name}'s calendar" → resolve via `list_calendars()`. See [[calendar-api-configuration]].
8. **Surface `Actions`/* threads with no task — NEVER auto-create.** The Actions label IS the record; a task is made only when Pete explicitly asks (overlap/de-tray), and then it carries `[no-sync-close]`.
9. **Filters, labels, folders, new `project_slug` values — ALWAYS confirmed.** Pattern detection surfaces; Pete confirms; sync executes.
10. **Respect the declined-suggestion list** in `email-workflow-state.md` before surfacing any suggestion.
11. **Re-prioritisation is not closure.** Close-on-label-removed fires only when NEITHER `Actions` NOR `Delegated` remains.
12. **Trashed thread = action complete.** Close the task with a note.

## Typical run

User: "sync" (or accepted the end-of-triage offer). Claude: runs `email-task-sync.py` → reads `email-workflow-state.md` → executes the 8 steps → produces the consolidated report (incl. any `Actions`-without-task threads surfaced for awareness) → lists any y/n decisions. If Pete answers y/n, those execute and a short follow-up confirms.

## Related skills

- `inbox-triage` — the interactive walker for new inbox items; offers this sync at end of session. Sync cleans up what triage started + catches manual Gmail-side changes between runs.
- `brain` — routing decisions across the homes; loaded at session start.

## Design references

- Operating manual: `[[email-workflow]]` · Routing: `[[vault-routing]]` · Gmail API: `[[gmail-api-configuration]]` · Calendar API: `[[calendar-api-configuration]]` · Label scheme: `[[gmail-label-scheme]]` · State file: `Library/processes/email-workflow-state.md`

## Related lessons

- [[Library/lessons/2026-04-25-email-mutation-pre-action-checklist]] — every Gmail mutation pre-flight.
- [[Library/lessons/2026-04-28-actions-label-proposed-not-auto-applied]] — the Actions label is surfaced, never auto-tasked.
- [[Library/lessons/2026-05-20-sync-must-call-wrapper-not-re-derive-steps]] — always run the deterministic wrapper.
- [[Library/lessons/2026-05-20-sync-must-query-both-open-and-closed-tasks]] — Step 1 must pull BOTH open and recently-done.
