---
name: asana-gmail-sync
description: >
  Reconciliation engine for Pete's email-workflow system. Bidirectional sync
  between Gmail labels and Asana task state. Auto-creates Asana tasks for
  Actions-labelled orphans (no asking) using smart routing, defaulting priority
  to P2 when no signal. Closes Asana tasks when Actions or Delegated label is
  removed manually in Gmail. Strips Gmail Actions / Delegated labels when
  Asana tasks are completed. Detects demand-driven label opportunities,
  auto-filter patterns, and parity drift between Gmail labels and vault
  folders. Surfaces all suggestions for Pete's confirmation. Closure exemptions
  per the 2026-06-06 Action/Task split: [no-sync-close] marker + Team-Finances
  blanket; every sync closure gets an audit comment. Runs on command
  -- idempotent, safe to run repeatedly. Triggered by the phrase "sync asana"
  or any reconciliation request. Offered (opt-in) at the end of every triage
  session, and runs daily at 07:15 as the daily-asana-gmail-sync cron.
---

<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Asana / Sheets / Docs / Xero / Odoo / GSC / GA4 / Ads / Vision / Geocoding / Sentry / Cloudflare / Vercel operation in this skill, see [[external-service-routing]]. Helper-first. -->

# asana-gmail-sync

> [!important] POST-CUTOVER ROUTING — overrides any vault path below (vault retired 24 Jun 2026)
> **Pete is off Asana (24 Jun) — this skill now syncs Gmail ↔ the CC `tasks` table, NOT Asana.** Wherever the body says create / read / close an **Asana** task, do it in **`public.tasks`** instead (INSERT / SELECT / UPDATE via `cc-sql.py`: `name`, `priority`, `due_on`, `entity_slug`, `project_slug`, `notes`, `status`). **Asana = Jane's only — do NOT connect to it for Pete's work.** Parity / folder-match against `Customers/`/`Suppliers/`/`Projects/` vault folders is retired → match the entity's **Drive** folder (`drive_files` via `cc-sql.py`) + `vault_notes`. `vault-enricher.py` still runs. Tools run from `/tmp/pbs`; `[[wikilinks]]` resolve against `vault_notes`.

> [!important] Business OS migration — filing targets are Drive + the knowledge DB now
> Gmail-label ↔ Asana reconciliation is unchanged. Where this skill files thread context to a customer/supplier/project, the real home is the entity's **Google Drive** folder + the **CC `vault_notes`** record (the vault content folders are retired 24 Jun 2026 (now in Drive + vault_notes)). Route per the new-world matrix in [[vault-routing]]. **`vault-enricher.py`** (called on filed/task-linked threads) still targets the vault file — flagged for Drive/DB redesign in the [[Projects/PA-Command-Centre/files/part-d-reference-repoint-ledger-2026-06-22|Part D ledger]]; keep calling it for now. `[[wikilinks]]` resolve against `vault_notes`.

Reconciliation engine for the email workflow. Bidirectional sync between Gmail labels (`Actions`, `Delegated`) and Asana task state. The verb `sync asana` runs this skill.

> **Operating manual**: `[[email-workflow]]` (full system overview — verbs, decision lines, sweep behaviour, delegation flow).
> **Routing rules**: `[[vault-routing]]`. Gmail-side rules: `[[gmail-label-scheme]]`.
> **Version history**: `[[CHANGELOG]]`.

## When to invoke

User says any of:
- "sync asana"
- "reconcile my tasks"
- "check my delegations"
- "close completed tasks"
- "clean up stale labels"

Also: **offered at the end of every `triage` session** (opt-in y/n after the Actions walker — triage never auto-chains; see inbox-triage Step 8b). And **runs daily at 07:15 as the `daily-asana-gmail-sync` cron** (see Cron mode below).

## Dependencies

- Gmail API helper: `/tmp/pbs/gmail-api.py` (always available)
- Calendar API helper: `/tmp/pbs/calendar-api.py` (always available)
- Asana MCP: `mcp__asana__*` tools. Load via `ToolSearch({ query: "asana", max_results: 60 })` if deferred.
- State file: `Library/processes/email-workflow-state.md` (declined suggestions, sender→label observations, routing observations).
- Vault folder access: Read/Write/Edit/Glob for parity checks against Customers/, Suppliers/, Projects/.

## Core principles

1. **Idempotent**. Safe to run repeatedly. No double-create, no double-close.
2. **Bidirectional**. Close in Asana → label leaves Gmail. Close in Gmail (remove Actions/Delegated label) → Asana task closes.
3. **Auto-create, never auto-structure**. Asana tasks for Actions/* orphans are created without asking. New labels, folders, filters always require Pete's confirmation via the proposal pattern.
4. **Asana is source of truth for task STATE** (priority, completion). Gmail labels are a view -- they follow Asana state.
5. **Re-prioritisation lives in Asana only**. There's no Gmail sub-label to swap. Pete edits the Asana priority custom field directly when he wants to re-prioritise. The "close-on-label-removed" rule fires only when NEITHER `Actions` NOR `Delegated` is on the thread. **When priority changes in Asana, the due date does NOT auto-recalculate** -- if Pete changes a task from P3 to P1, the existing due date stays unless Pete edits it. Re-pri shouldn't accidentally pull a deadline 28 days closer.

## Execution: ALWAYS call the deterministic wrapper first

> [!important] The skill's first action MUST be to run `/tmp/pbs/sync-asana.py`.
> The wrapper is the deterministic implementation of Steps 1, 3, 4, 5, 7, 8 in Python. It does not depend on prose interpretation — every run executes exactly the same 8-step algorithm. Steps that need LLM judgement (Step 6 orphan routing + task naming) are surfaced as candidates in the wrapper's output for the LLM to action.
>
> Procedural rule for this skill: **don't manually re-derive the steps in bash.** Always run the wrapper. It exists precisely so the 8-step algorithm can't drift between runs.

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/sync-asana.py            # run + apply changes
VAULT=/tmp/pbs python3 /tmp/pbs/sync-asana.py --dry-run  # report only, no mutations
VAULT=/tmp/pbs python3 /tmp/pbs/sync-asana.py --json     # raw JSON (for LLM chaining)
```

**Exit codes:**
- `0` = sync complete, no LLM decisions needed
- `1` = sync complete, Step 6 orphan candidates need LLM routing (script output lists them)
- `2` = fatal error (auth, API, file system)

**After running the wrapper:**
1. Read its output (closures, label strips, parity, orphan candidates).
2. For each Step 6 orphan candidate, decide routing per the fallback chain below and call the task-creation path (see "Orphan task creation" section). Apply the locked Mimestream + Gmail + Finder link policy, and call `vault-enricher.py` on the source thread → target vault folder.
3. Report the consolidated outcome to Pete in the format shown at the bottom of this file.

**Source-of-truth lesson on why this enforcement exists:** [[Library/lessons/2026-05-20-sync-must-call-wrapper-not-re-derive-steps]].

## The full sync algorithm (reference — implemented by the wrapper)

The wrapper executes these in order. Each step is idempotent and reports what changed.

### Step 1: Pull linked tasks (BOTH open AND recently-completed)

Query Asana **twice** — once for open tasks, once for tasks completed in the last 30 days — and merge into the linked set. Both have linked Gmail threads that the algorithm needs to act on.

```bash
# Open tasks (drives Step 4: close on Gmail-side label removal)
GET /workspaces/{ws}/tasks/search?assignee.any={pete}&completed=false&opt_fields=name,notes,due_on,projects.name,memberships.section.name

# Recently-closed tasks (drives Step 3: strip Gmail label after Asana-side closure)
GET /workspaces/{ws}/tasks/search?assignee.any={pete}&completed=true&completed_at.after={today-30d}&opt_fields=name,notes,completed_at
```

For each task, extract Gmail thread IDs from notes (matching both `mail.google.com/mail/u/0/#[a-z]+/{thread_id}` and `links.mimestream.com/g/{email}/t/{thread_id}` URL forms). Carry forward: `gid`, `name`, `completed`, `completed_at`, `due_on`, `projects`, the thread IDs, and the priority custom field value.

**Both-sides query is non-negotiable.** If Step 1 only pulls open tasks, Step 3 has no closed tasks to act on — the Gmail label persists after Asana-side closure, and Step 6 then auto-creates a duplicate ("revives" the task). The wrapper enforces both queries on every run. See [[Library/lessons/2026-05-20-sync-must-query-both-open-and-closed-tasks]].

### Step 2: Priority reconciliation — no-op

No Gmail sub-labels for priority; Asana is the sole authority via the custom field. Step 2 reports nothing. Step number preserved for ordinal stability.

### Step 3: Completion reconciliation (Asana → Gmail)

For each Asana task that is `completed: true` in the linked set, strip the `Actions` label or the `Delegated` label from the Gmail thread (whichever is present). Use `gmail-api.py modify_thread` with `remove=[label_id]`.

Behaviour when completion strips the last workflow label:
- If thread still has a filing label (`Customers/*`, `Suppliers/*`, `Projects/*`, `Invoices/*` (legacy — re-route to Team-Finances at sync time), `Accreditations/*` (legacy — re-route to Team-General/SY-General at sync time), or any Mode-A top-level): no further action -- thread stays archived under its home (next sweep handles inbox if it somehow returned).
- If thread has NO filing label: skip silent orphaning. Add to Step 8 report: "{thread subject} has no filing home -- file under X, archive, or bin?". Pete decides.

### Step 4: Bidirectional close (Gmail → Asana)

For each linked task that is still `completed: false`, check the linked Gmail thread's current labels:

- If thread has the `Actions` label OR the `Delegated` label → leave the task alone (it's still active).
- If thread has NEITHER `Actions` NOR `Delegated` → **mark the Asana task complete**. Pete handled this action in Gmail (or via another route) and removed the workflow label. Sync follows.

**Exemptions (Action/Task split, locked 2026-06-06 — the wrapper enforces both):**

1. **`[no-sync-close]` marker in task notes** → NEVER close on label state. Two uses: (a) Pete-sent watch tasks (chase-if-no-reply; thread never had Actions — added 2026-05-24); (b) **Asana-only tasks** — bills, cert batches, work items created by the `Task this` verb or de-trayed by Pete. Their work happens outside email, so Gmail label state must never close them.
2. **Team-Finances blanket** — any task in Team-Finances (gid `1214565508668959`) is exempt regardless of marker. A bill is never a reply.

**Closure audit comment** — every task Step 4 closes gets an Asana story: *"Closed by sync — Actions/Delegated label removed in Gmail, {date}. If this strip was a tray clear-out rather than completion, reopen and ask Claude to mark it [no-sync-close]."* Closures are also listed in the running session's / cron's daily-note block. This is the safety net for the strip-to-clear-out gesture.

**The one-sentence rule behind the split: Actions = waiting on Pete to respond by email. Everything else = Asana only.** Design + migration record: [[Projects/PA-General/files/email-workflow-plan-2026-06-06-action-task-split]] and [[Library/decisions/2026-06-06-actions-label-reply-only]].

**Multi-thread tasks**: a task can be linked to multiple Gmail threads (parsed from notes). Close the task only when ALL linked threads have lost BOTH `Actions` AND `Delegated`. If any one thread still has either label, the task stays open.

Edge cases:
- Thread was trashed (POST /threads/trash) → all labels stripped → task closes per the rule. Correct: if trashed, the action is done by definition.
- Thread was deleted (DELETE /threads) → no thread to check. If the Gmail thread URL returns 404, close the task with a note: "Source thread deleted from Gmail."
- Thread was archived but still has `Actions` label → no closure (archive removes INBOX, not the workflow label). Common case: triage archived the thread when applying Actions; the Actions label persists until task closes.

Report: "Closed X via Gmail-side completion: {task name list}".

### Step 5: Delegation reply check

For each open task in the `Team-General` Asana project (GID `1214564987703466`) placed in the `Delegated` section (section GID `1214564987864352`):

(The standalone `Delegated` project at GID `1214255292794724` was archived in the 2026-05-06 restructure. Pull tasks via `asana_get_tasks` on Team-General + filter by section, OR pull all sections via `asana_get_sections` and walk the Delegated section.)

1. Extract the `delegatee` email from the task notes (recorded at delegation time).
2. Extract the `forwarded_at` datetime from the task notes (or fall back to `created_at`).
3. Pull the Gmail thread's messages via `get_thread`. Look at messages where:
   - `internalDate` > `forwarded_at`
   - `from:` header matches the delegatee email (not Pete, not auto-reply bots)
4. Filter out auto-replies by subject pattern: `^(Out of Office|Auto.*Reply|Automatic Reply|Vacation).*` (case-insensitive).
5. If a genuine reply found → mark Asana task `completed: true` + remove `Delegated` Gmail label + post the same audit-comment pattern as Step 4 ("Closed by sync — {delegatee} replied {date}"). Report: "✓ Jane replied to the Clancy Q2 delegation -- closed the task."
6. If no reply AND follow-up date passed → flag for chase (do not auto-send; draft only). Draft chaser to Gmail Drafts using `gmail-api.py draft` with a polite template. Report: "⚠ 3 delegations overdue -- drafted chasers to Drafts for your review."

### Step 6: Orphan handling -- auto-create with smart routing (no asking)

Find Gmail threads labelled `Actions` or `Delegated` that have NO matching Asana task.

**Orphans are tray items by definition** (Pete put the Actions label on, usually from his phone — that's the manual-dump pickup he relies on). The auto-created task is therefore an `Action this`-class task: normal label↔task coupling, **no `[no-sync-close]` marker**. If the dump was really a bill/work item, Pete de-trays it later with one ask.

For each orphan, **auto-create the task with smart routing** (no asking):

- **Routing discipline**: the fallback chain below implements the task-routing decision tree at `[[vault-routing#task-routing-decision-tree]]` — related project/bucket first, else `{prefix}-General`; bucket/project escalation only ever by proposal to Pete, never auto-created.
- **Priority**: no priority signal in Gmail (single-Actions-label model). Default to **P2** (sensible middle priority). Pete can edit in Asana. For `Delegated`-only orphans, no priority set (Delegated is its own track).
- **Project routing fallback chain**:
  1. Thread has a `Projects/*` label → task goes in the matching Asana project (read project name from label, look up GID via vault `Projects/{name}/README.md` `asana_gid` frontmatter). For sub-project labels (e.g. `Projects/SY-Website-Articles` if such a label exists demand-driven), the README frontmatter at the parent points to the parent project + section. **Always use `asana_add_task_to_section`** when the README specifies a section.
  2. Thread has a `Customers/*` or `Suppliers/*` label only → task goes in `Team-General` (gid `1214564987703466`) placed in the matching `{prefix}-General` section:
     - SY → Team-General / `SY-General` section (gid `1214564987855498`)
     - CD → Team-General / `CD-General` section (gid `1214564987862794`)
     - EA → Team-General / `EA-General` section (gid `1214565283959281`)
     - AT → **AT-General** (gid `1214132593458752`, Ashcroft Family team — kept as a standalone project, not folded into Team-General) -- AT- supplier vault home is `Personal/family/`
     - **SY-Clancy exception**: threads labelled `Customers/SY-Clancy` route to the standalone SY-Clancy Asana project (gid `1214277900941306`), NOT Team-General/SY-General.
  3. Thread labelled `Invoices/*` (legacy label, kept for backwards compatibility) → task goes in `Team-Finances` (gid `1214565508668959`) placed in the matching invoices section:
     - `Invoices/CD-Invoices` → Team-Finances / `CD-Invoices` section. Default routing for new payables when section status not yet decided: `CD-To Pay` (gid `1214565862019985`). Other CD sections: `CD-Overdue` (gid `1214565640727847`), `CD-Awaiting` (gid `1214565508808207`). Auto-paid items still route to `Receipts`, not Invoices — see CLAUDE.md rule.
     - `Invoices/SY-Invoices` → Team-Finances / `SY-Invoices` section. Default: `SY-To Pay` (gid `1214565670136545`). Other: `SY-Overdue` (gid `1214565862262856`), `SY-Awaiting` (gid `1214565640753174`).
     - `Invoices/EA-Payments` → Team-Finances / `EA-Payments` section (gid `1214565801009636`). Historical EA payment log lives at `EA-Transfers-Log` (gid `1214565640822760`).
  4. Thread labelled `Accreditations/*` → task goes in `Team-General` / `SY-General` section. The old `SY-ProQual` and `SY-EUSR` standalone projects were archived 2026-05-06; vault folders at `Accreditations/SY-ProQual/` and `Accreditations/SY-EUSR/` remain reference-only.
  5. **Personal/PA-{area} label** (single project, sectioned) → task goes in **PA-General** (gid `1214124274861717`, Personal team) **placed in the matching section**:
     - `Personal/PA-Scouts` → PA-General / `Scouts` section (gid `1214469790785001`)
     - `Personal/PA-Los-Claveles` → PA-General / `Los Claveles` section (gid `1214469770988762`)
     - `Personal/PA-Freemasonry` → PA-General / `Freemasonry` section (gid `1214469771032895`)
     - `Personal/PA-Finance` → PA-General / `Finance` section (gid `1214469945006073`)
     - `Personal/PA-PassionFit` → PA-General / `PassionFit Migration` section (gid `1214565597755125`, added 2026-05-06)
     - **Use `asana_add_task_to_section`** after creating the task so it lands in the right section, not unsectioned.
  6. **Demand-driven label suggestion**: if no filing label but sender domain (or subject keyword) matches an existing vault folder (`Projects/{prefix}-{slug}/`, `Customers/{prefix}-{slug}/`, `Suppliers/{prefix}-{slug}/`, or `Personal/{area}/`) for which no Gmail label exists → surface in Step 8 report as a "create label?" suggestion. Check `email-workflow-state.md` declined list first.

     **For project labels especially: ASK before creating new project labels** if the sustained volume isn't clear. The 2026-05-06 rule was "default to General; don't sprawl projects/sub-projects for 1-2 tasks" — the same applies to demand-driven Gmail labels. Surface as a suggestion, but err on the side of routing through `{prefix}-General` until Pete confirms.

     Personal-area sender hints to recognise (from 2026-05-03 email scan):
     - BSO Southern Europe, scouts.org.uk, "Florida Jamborette", scout association → suggest `Personal/PA-Scouts`
     - Bryn Hart, Provincial Grand Master, "Tyldesley Lodge", "Imperial George 78", "Sincerely & Fraternally" body → suggest `Personal/PA-Freemasonry`
     - Los Claveles utility invoices (LPLIR/LPLR codes), Carlos / committee → suggest `Personal/PA-Los-Claveles`
     - HSBC, pension provider, HMRC personal Self Assessment → suggest `Personal/PA-Finance`
  7. Thread has no filing label and no folder match → default to `PA-General` (gid `1214124274861717`). Pete cleans up periodically -- the trade for zero friction.
- **Task name**: action verb + WHO + WHAT (e.g. "Reply to Wayne (Clancy) about UKPN DSR meeting time", "Pay Suministros Pantera invoice 26P15531"). Don't dump the raw email subject — derive an action-oriented name from the content. Notes should include source URL + sender + 3-5 line summary + action expected + routing trail.
- **Task notes**: include all three of these (Mimestream first):
  1. **Mimestream link**: `https://links.mimestream.com/g/pete.ashcroft@sygma-solutions.com/t/{thread_id}` — opens the thread directly in Pete's Mimestream desktop client.
  2. **Gmail web link**: `https://mail.google.com/mail/u/0/#all/{thread_id}` — fallback when not on the Mac.
  3. **Finder link to the matching vault folder** (when the task is in a project/customer/supplier): generated via `/tmp/pbs/vault-finder-link.py {project-name} [section-name]`. Returns a `file:///Users/peterashcroft/Second%20Brain/...` URL that opens the folder in macOS Finder. Omit if no matching vault folder exists (don't emit a broken link).

  Then: brief summary + how it was routed (which fallback step matched) + "priority defaulted to P2 — edit in Asana if needed".

  Reason: Mimestream opens the source thread, Finder link opens the working folder, both with one click. See [[Library/lessons/2026-05-20-asana-tasks-include-mimestream-link]].

- **MUST run vault-enricher on the source thread → routed vault folder.** The call is part of the orphan auto-create operation, not an after-thought:

  ```bash
  VAULT=/tmp/pbs python3 /tmp/pbs/vault-enricher.py {thread_id} "{routed-vault-folder}"
  ```

  The routed-vault-folder is whatever the fallback chain resolved (Customers/SY-X, Suppliers/CD-Y, Projects/CD-Website, Team-General/{prefix}-General, etc.) — converted to the vault folder path. Skip rules in the enricher handle PA-General and operational labels automatically.

  After the call, report attachments pulled + extract path + contacts added in Step 8's consolidated report so Pete sees what landed. See [[Library/lessons/2026-05-20-must-call-vault-enricher-not-just-reference]].
- **Due date** -- derived from the **default priority P2** (Atlantic/Canary tz): today + 7 days. Auto-due-date schedule still applies for any priority Pete later sets:
  - P1 → today + 2 days (48h)
  - P2 → today + 7 days (1 week)
  - P3 → today + 30 days (1 month)
  - P4 → no due date
  - `Delegated`-only orphans → no due date here (Step 5 handles delegation timing)
  - Pete can edit dates in Asana directly after creation. The default schedule is a starting point, not a constraint.
- **Assignee**: always Pete (GID `1213947679900718`).

Report: "Auto-created N tasks (default P2): {list with project routing per task}."

### Step 7: Pattern detection

#### 7a: Auto-filter suggestions

**Compute observations fresh from Gmail each run** (do NOT store in state file). For each label of interest (Customers/*, Suppliers/*, Projects/*, etc.):
- `gmail-api.py search "label:{label-name} newer_than:90d"` → group by sender domain, count.
- `gmail-api.py list_filters()` → get the set of senders already covered by existing filters.

For each sender domain → label pair where count >= 3 AND no existing filter covers it AND no decline recorded:

- Check `email-workflow-state.md > Declined auto-filter suggestions` first -- never re-suggest declined senders.
- Surface in Step 8 report:

```
Auto-filter suggestion:
  4 emails from *@newsupplier.co.uk routed to Suppliers/CD-NewSupplier (last 30d), no filter exists.
  Create filter: from:*@newsupplier.co.uk OR to:*@newsupplier.co.uk → apply Suppliers/CD-NewSupplier
                 (apply only, never remove INBOX)
  Create? (y / n)
```

- y → create filter via Gmail API, set `auto_filter: true` in state
- n → record decline in `email-workflow-state.md`, never re-suggest

**NEVER auto-create filters.** Always confirmed.

#### 7b: Strategic routing patterns

Append to `[[vault-routing#observed-patterns]]` when sync sees a routing decision being made repeatedly. After 5 occurrences confirmed without override → propose promotion into Master routing matrix at end of report. Pete decides.

#### 7c: Filter broadening detection

When a thread is labelled with the right label but the sender doesn't match any existing filter (e.g. subdomain not covered) → surface broadening suggestion in Step 8 report. Same confirm-before-create rule.

### Step 8: Parity check + consolidated report

Cross-system parity scan:

- **Customers/Suppliers labels ↔ vault folders**: every `Customers/{slug}` / `Suppliers/{slug}` Gmail label has a matching vault folder, and vice versa. Surface drift.
- **Project label ↔ vault folder ↔ Asana project**: every demand-driven `Projects/{slug}` Gmail label has matching vault folder + active Asana project. Surface drift.
- **Vault folder without label that should have one**: customer/supplier folders (parity violation -- surface immediately). Project folders with 3+ matching emails in last 30d but no label (demand-driven trigger -- surface as suggestion).

Output format:

```
sync asana complete

PRIORITY moves: -- (no-op: priority lives in Asana only)

CLOSED from Asana completion:
- {task name} -- closed, Gmail Actions label stripped, thread filed under {Customers/SY-Clancy}
- 2 more closures

CLOSED from Gmail-side completion:
- {task name} -- Actions label removed in Gmail, marking Asana task complete

DELEGATED:
- ✓ Jane replied to {thread} -- closed (2 days ago)
- ⚠ 3 overdue chasers drafted to Drafts:
    - {subject} to {delegatee} -- 9 days overdue
    - ...

ORPHANS auto-created (no asking, default P2):
- {subject} → Team-General/SY-General section, P2 (sender matched Customers/SY-Smith → fell to {prefix}-General)
- {subject} → SY-Clancy P2 (Customers/SY-Clancy exception — standalone project, not Team-General)
- {subject} → Team-Finances/CD-Invoices section P2 (Invoices/CD-Invoices label)
- {subject} → PA-General/Scouts section P2 (Personal/PA-Scouts label)
- {subject} → PA-General P2 (no filing label, no folder match -- default fallback)

DEMAND-DRIVEN LABEL suggestions:
- 3 emails from *@partnerco.co.uk match Projects/CD-Partnership-Programme folder (no label exists)
  Create Projects/CD-Partnership-Programme + auto-filter? (y / n)

AUTO-FILTER suggestions:
- 4 emails *@newsupplier.co.uk → Suppliers/CD-NewSupplier (no filter)
  Create filter? (y / n)

PARITY CHECK:
  ✓ All Customers/* and Suppliers/* labels match vault folders (3 customers, 5 suppliers)
  ⚠ 1 vault project folder without Gmail label: Projects/CD-Pool-Jobs (1 email matched in last 30d, below 3-threshold)
  ⚠ 0 Gmail labels without vault folders

CALENDAR revisit:
- 1 closed thread had a flight mention not added to calendar:
  ✈ {flight summary} -- propose adding? Default tz Atlantic/Canary, calendar Pete primary. (y / n)

STRATEGIC PATTERNS (vault-routing > observed):
- "Insurance docs from AXA" routed to Team-General/CD-General × 4 -- awaiting confirmation. Promote to Master routing matrix? (y / n)

Any decisions needed? (list any pending y/n questions)
```

If nothing changed: short report `sync asana complete -- no changes (X linked tasks checked, all in sync, parity clean)`.

## Cron mode (daily 07:15 Atlantic/Canary — `daily-asana-gmail-sync`)

The scheduled run executes the same wrapper + Step 6 orphan routing, with these differences (a cron can't ask questions):

- **Orphan auto-create proceeds** exactly as in interactive mode (it never asks anyway) — but **best-match routing only**: never create labels, buckets, sections, or projects. Ambiguous orphans → PA-General + flagged for re-route.
- **All suggestions** (auto-filter, demand-driven label, parity drift, broadening, homeless threads) are NOT asked — they're written to today's daily note under `## Asana sync (Automated)` along with closures/strips/orphans counts. The next interactive session surfaces them.
- **Chaser drafts** still go to Gmail Drafts only (never sent).
- **Read the daily note before appending** (other crons write to it).
- **Failure escalation**: 2 consecutive failed runs → P2 Asana task in Team-General/SY-General (mirrors staff-master-sync).
- Registry + schedule: [[scheduled-tasks]] + automations dashboard. Any change to this cron runs the dashboard 3-step.

## Rules

1. **Always dry-run if unsure**. If the user says `sync asana --dry-run` or it's the first run of the session, show the plan without executing.
2. **Never permanently delete**. If a thread should be deleted, always confirm. `DELETE /threads/{id}` is irreversible.
3. **Never send email without confirmation**. Chaser drafts go to Drafts -- Pete reviews and sends manually, even on the follow-up sweep.
4. **Always confirm filing when a thread is homeless**. Never silently leave a thread without a filing label once its Actions/Delegated label is stripped.
5. **Preserve label IDs**. Never delete-and-recreate a Gmail label during sync; use renames (`patch_label`) to preserve associations.
6. **Asana is source of truth for priority and completion**. Gmail labels are a view on top.
7. **Calendar revisit on completion sweep**. If a closed thread has a flight/hotel/car/meeting mention that wasn't added to calendar, flag it in the report (don't auto-add -- Pete's decision). When proposing the missed event, default timezone Atlantic/Canary, default calendar Pete's primary. When Pete responds with "put this in {name}'s calendar", use `list_calendars()` to resolve by display name. Cross-reference `[[calendar-api-configuration]]`.
8. **Auto-create tasks (Actions/* orphans only) -- no asking**. The smart routing fallback is fast and predictable. PA-General is the explicit dump for unmatched orphans -- accepted trade for zero-friction triage.
9. **Filters, labels, folders, Asana projects -- ALWAYS confirmed**. Pattern detection surfaces; Pete confirms; sync executes.
10. **Respect declined-suggestion list**. Before surfacing any auto-filter or demand-driven label suggestion, check `Library/processes/email-workflow-state.md` for declined entries matching the sender pattern. If declined, do not re-surface.
11. **Re-prioritisation is not closure**. The "close on label-removed" rule fires only if NEITHER `Actions` NOR `Delegated` remains on the thread. Priority changes happen in Asana directly (no Gmail sub-label to swap).
12. **Trashed thread = action complete**. If a linked thread is in Trash, treat as completion (close task with note).

## Typical run

User: "sync asana" (or accepted the end-of-triage offer, or the 07:15 cron)

Claude:
1. Loads Asana tools (ToolSearch query "asana") if not already loaded.
2. Reads `email-workflow-state.md`.
3. Runs the 8-step algorithm.
4. Produces the consolidated report.
5. Lists any y/n decisions Pete needs to make.

If Pete responds y/n to suggestions, those execute and a short follow-up confirms.

## Related skills

- `inbox-triage` -- the interactive walker that handles new inbox items. Auto-fires `sync asana` at end of session. Sync cleans up what triage started + catches manual Gmail-side changes between triage runs.
- `brain` -- for routing decisions that cross the five systems. Loaded at session start.

## Design references

- Operating manual: `[[email-workflow]]`
- Routing rules: `[[vault-routing]]`
- Gmail API: `[[gmail-api-configuration]]`
- Calendar API: `[[calendar-api-configuration]]`
- Asana config: `[[asana-configuration]]`
- Label scheme: `[[gmail-label-scheme]]`
- State file: `Library/processes/email-workflow-state.md`
- Build history (archived): `[[email-workflow-plan-2026-04-24]]`

## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill per [[Library/audits/2026-05-16-lesson-deployment-matrix]]:

- [[Library/lessons/2026-05-06-asana-direct-api-when-mcp-fails]] — fallback when MCP errors.
- [[Library/lessons/2026-04-25-email-mutation-pre-action-checklist]] — every Gmail mutation pre-flight.
- [[Library/lessons/2026-04-28-actions-label-proposed-not-auto-applied]] — orphan auto-create proposal step.
- [[Library/lessons/2026-05-20-sync-must-call-wrapper-not-re-derive-steps]] — always run the deterministic wrapper, never re-derive in bash.
- [[Library/lessons/2026-05-20-sync-must-query-both-open-and-closed-tasks]] — Step 1 must pull BOTH; closed-tasks query drives Step 3 label-strip.
- [[Library/lessons/2026-05-25-sync-asana-step-1-cap-100-no-pagination]] — Asana typeahead `tasks/search` caps at 100 with no pagination. Step 1 must use targeted `text:` filters (`mail.google.com`, `mimestream.com`). Step 3 must respect multi-task ownership before stripping labels — passes `open_tasks` set in to skip strips on threads any open task still owns.
