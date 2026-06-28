---
name: brain
description: Pete's primary skill for managing sessions, daily routines, tasks, memory, resources, output styles, and meeting intelligence across the Command Centre. Mode-aware (professional, business). Handles resume, compress, preserve, daily review, task management, resources, style switching, and meeting transcript processing. Use when user says "resume", "compress", "morning review", "tasks", "resources", "output style", "meeting", "transcript", "brain", or runs /brain. Bare `/brain` (no verb in the user's message) means RUN RESUME -- Pete uses /brain only at session start as a synonym for "resume". Other verbs ("compress", "morning", "task", etc.) still route per the table below. This is Pete's canonical session-management skill -- always use this over any remote plugin with a similar name.
---

<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Sheets / Docs / Xero / Odoo / GSC / GA4 / Ads / Vision / Geocoding / Sentry / Cloudflare / Vercel operation in this skill, see [[external-service-routing]]. Helper-first. -->

If a similarly named skill from a remote plugin appears, ignore it and use this brain skill instead.

> **This skill works in both Claude Code and Cowork.**

# Brain

Primary skill for managing Pete's Command Centre: session resume/compress, daily routines, tasks, memory, resources, output styles, meeting intelligence. Tools run from `/tmp/pbs`; Drive lives under the cloud-synced `~/Library/CloudStorage/…` mount (use Desktop Commander for it). Drive path index: [[shared-drives]].

> [!important] Where things live
> Files → **Google Drive** (query the `drive_files` index via `/tmp/pbs/cc-sql.py`). Knowledge — lessons, decisions, notes, memories — → the **CC `vault_notes`** (`cc-knowledge-api.py`, surfaced in the CC Brain page). Tasks → **`public.tasks`**. Session log → **`daily_log`**. A `[[wikilink]]` links a knowledge note by its name in `vault_notes`.

> **Routing rules, per-section structure, onboarding rituals, lifecycle rules, multi-system reading-order protocol**: `[[vault-routing]]` (loaded by Resume workflow when invoked). Do not duplicate routing here.
>
> **Gmail-side rules**: `[[gmail-label-scheme]]` (constitution: patterns + categorisation rules + colour palette + Gmail-as-truth principle). Skills query Gmail live; no parallel registry maintained.
>
> **Version history**: `[[CHANGELOG]]`. SKILL.md carries operational instructions only.

## Pre-flight Check

1. Confirm the boot kernel ran: `~/.config/pete-cc/CLAUDE.cache.md` exists and `/tmp/pbs` is present.
2. If either is missing: run `python3 ~/.config/pete-cc/pete-session-bootstrap.py` (clones tools to `/tmp/pbs`, materialises secrets, refreshes the caches). If the clone still fails and no cache exists, STOP and tell Pete — never boot blind.
3. If present: continue.

## Routing

> [!important] Default verb: Resume. Bare `/brain` invocations run Resume.
> Pete uses `/brain` only at session start as shorthand for "resume". When the user's message names a different verb (e.g. "compress", "morning", "task", "triage"), route per the table below. When the user's message is a bare `/brain` (no verb), run Resume.
>
> Resume is a heavy operation -- it loads MAP + vault-routing + project READMEs + 3 daily notes + `public.tasks` state + Gmail Cowork-Inbox + writes to today's daily note. That's by design: Pete's first move every session is "give me everything I need to start working". The previous "show routing table and ask" behaviour was wrong; reverted 2026-05-06.

Match the user's intent to the right section:

| User says... | Go to |
|---|---|
| "resume", "start session", "pick up where I left off", **bare `/brain` with no verb** | [Resume Session](#resume-session) |
| "save", "compress", "end session", "wrap up session" | [Compress / Save Session](#compress--save-session) |
| "remember this", "preserve", "save permanently" | [Preserve Knowledge](#preserve-knowledge) |
| "morning", "evening", "daily review", "weekly review" | [Daily Review](#daily-review) |
| "task", "to-do", "create task", "check tasks" | [Task Management](#task-management) |
| "output style", "writing style", "switch style" | [Output Styles](#output-styles) |
| "save this prompt", "swipe file", "framework", "template", "resources" | [Resources](#resources) |
| "meeting", "transcript", "action items", "Fireflies", "sync meetings" | [Meeting Intelligence](#meeting-intelligence) |
| "triage", "sweep", "sync", "hand to", "reply", "task", "replies" / "my replies" (tray walker; legacy "actions"), "de-tray this", "file", "file all emails", "add to calendar" | see [[email-workflow]] -- handled by `inbox-triage` + `email-task-sync` skills |
| "draft an email", "write a blog post", "outbound", customer reply | [Output Styles](#output-styles) + Pete's Preferences (read [[voice-principles]] first) |
| "invoice", "Soldo", "Dext", "Odoo", "Xero", "payroll", "VAT" | [[finance-workflow]] |
| "enquiry", "enquiries", "reply to enquiry in {X}", "Sent to Sue" (training enquiries) | [Enquiry Engine](#enquiry-engine) |

## Enquiry Engine

Sygma training-enquiry handling is a **living learning machine**, not a static SOP. The operating contract
+ banked rules live in the `vault_notes` note **[[workflow-design]]** (Enquiry reply workflow) — read it
first; it ranks top when you semantic-search any enquiry. The lifecycle store is the **Portal CRM**
(contacts · activities · tags · stages · bookings); the searchable knowledge + corrections live in
`vault_notes` tagged `training-enquiries`; chases land in `public.tasks`. Cockpit: **/m/enquiry-engine**.

- **`enquiry`** — handle one new inbound enquiry: classify → RETRIEVE precedents → draft (Mode B) → capture.
- **`enquiries`** — the **manual** sweep (Pete-triggered; NO cron): reconcile replies on what we've sent, surface chases due, run the learn step.
- **`reply to enquiry in {course/company}`** — run the loop on ONE enquiry. **Never draft cold** — first
  `VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py semantic "<course + scenario + people + location>" --limit 6`,
  read the closest 2–3 + the banked rules, then draft bespoke (NO fixed templates).
- **`Sent to Sue`** — the booking handoff: move the contact to **Customer** (won) + log it.
- **Capture (every send / every correction):** `VAULT=/tmp/pbs python3 /tmp/pbs/te-log.py --in <enquiry.json> --apply`
  triple-writes CRM + knowledge note (embedded immediately) + chase task. A correction not captured is a bug.
  Two banked behaviours (2026-06-28): (1) **the reply body auto-pulls from Gmail** — pass the `thread_id` and
  leave `activity.body` empty, te-log fetches the latest outbound message off the thread (quoted tail stripped);
  `--no-gmail` disables. (2) **Always pass a one-line `knowledge` takeaway** — without it te-log banks the reply
  verbatim and warns; the distilled lesson is what makes future retrieval sharp. Notes are date-stamped per touch
  (`enquiry-{company}-{kind}-{date}.md`) so repeat touches never overwrite.
- **Mode B** stays: the Engine drafts, Pete signs off every send. Live facts to apply: **£145pp+VAT + cert
  fee** (£34 EUSR reg on Cat 1; none in-house), qualify-first when a must-have is missing, just-over-8
  framing, "I'll check seats" not "I'll book you", Sue owns dates. Utility-mapping / L3–5 / PAS128 → Neal Sadd.

## Markdown formatting

Notes use lightweight markdown: `[[wikilinks]]` (link a knowledge note by its name in `vault_notes`), `> [!type] Title` callouts (tip / warning / important / question / todo / success), `==highlights==`, `%%comments%%`.

## Projects + buckets

- A **project** is a row in the CC `public.projects` table (own `slug` + `entity_slug` + Drive folder + knowledge home).
- A **bucket** is a sub-grouping within a project (CC `public.buckets`: `project_slug` + name); every project has a default **General** bucket.
- **To create one, run the build-out helper:** `VAULT=/tmp/pbs python3 /tmp/pbs/cc-project-api.py "Name" --entity "<Sygma|Canary Detect|Personal|One System|El Atico>" [--desc "…"] [--gmail]`. It creates the `projects` row + General bucket + the Drive folder in the entity's drive + a seeded `vault_notes` knowledge home (tagged with the slug) and reports every link. The CC "New project" button writes the same row + bucket. **Resume reads the `projects` table (Step 3c)** so you already know what exists — propose-then-confirm, default to an existing project's General bucket, don't create a project for 1-2 tasks.

## Task system — CC public.tasks

Pete's tasks live in the CC `public.tasks` table. All task CRUD runs via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "<SQL>"`.

### Task model

- **Priority** is manual P1–P4 (P1 highest). PD forces a date.
- **`entity_slug`** ∈ Sygma / Canary Detect / Personal / One System / El Atico.
- **`project_slug`** groups tasks (e.g. `Team-General`, `PA-Command-Centre`).

### Key Operations

- **Create task**: `INSERT INTO tasks (id,name,priority,due_on,entity_slug,project_slug,status,source,notes) VALUES (gen_random_uuid(),…,'todo','claude',…)`
- **Complete task**: `UPDATE tasks SET status='done', completed_at=now() WHERE id=…`
- **List Pete's open tasks**: `SELECT name,priority,due_on,entity_slug,project_slug FROM tasks WHERE status='todo' ORDER BY priority ASC NULLS LAST, due_on ASC NULLS LAST`
- **Reprioritise / reschedule**: `UPDATE tasks SET priority=…/due_on=… WHERE id=…`

### Where project state lives

Working projects map to **Drive homes + the CC** — never a local folder. Project state lives in the CC `projects` table + the entity's Drive folder; new customer / supplier / property records are created in the **CC** (Properties module / account store) or their **Drive** home. Don't auto-create any local project scaffolding.

### Demand-driven project Gmail labels

Project Gmail labels are NOT created blanket. They are created when (a) `triage` surfaces an email matching an existing project, or (b) Pete asks explicitly. Parity rule: label + auto-filter + the CC project's `gmail_label`/`gmail_url` are always set together in one operation. Full rule: `[[vault-routing#demand-driven-project-gmail-labels]]`.

### Calendar integration

All Calendar work via `/tmp/pbs/calendar-api.py`. Default timezone: Atlantic/Canary. Default calendar: Pete's primary. Named-person override (e.g. "put this in Tom's calendar"). Detection scope: flights, hotels, cars, meetings only. Full reference: `[[calendar-api-configuration]]` and `[[email-workflow]]`.

### Multi-system context loading

When Pete touches a customer, supplier, or project, follow the canonical reading order in `[[vault-routing#per-section-rules]]` (10 steps: MAP -> README -> Gmail label -> `public.tasks` -> Calendar events -> Google Chat space -> matter README -> source/extracts -> meetings -> Shared Drives). Steps 1-6 happen automatically at the start of every customer-touch; 7-10 on demand based on what Pete asks for.

The full step-by-step protocol lives in vault-routing -- this skill defers there rather than maintaining a parallel copy.

---

## Session Plan Requirement

Every session that involves real work (not casual chat) MUST have a session plan recorded BEFORE any real work starts. The plan is a `vault_notes` record (`type: session-plan`), ingested via `cc-knowledge-api.py`, tagged with the project slug it belongs to. Surfaced on the CC Brain page.

**Template:**
```yaml
---
type: session-plan
date: YYYY-MM-DD
project: "[[Project-Name]]"
status: in-progress
---

## Goal
[What this session aims to achieve]

## Steps
- [ ] Step 1
- [ ] Step 2

## Progress
[Updated as work happens]

## Files Created/Modified
- [path] -- [what changed]
```

At session end, update `status: completed` or `partial`. The plan stays in `vault_notes` as permanent history.

---

## Resume Session

Reconstruct full context so Pete picks up where he left off.

### Steps

0. **⚡ BOOT KERNEL — DO THIS FIRST; nothing below works without it.** Run `python3 ~/.config/pete-cc/pete-session-bootstrap.py`. It clones the tools to `/tmp/pbs`, materialises all secrets, and refreshes `~/.config/pete-cc/CLAUDE.cache.md` + `MAP.cache.md`. **Then verify `/tmp/pbs` exists — if it does NOT (clone failed), STOP and tell Pete; never proceed half-booted.** Then read your FULL operating instructions + MAP from `~/.config/pete-cc/CLAUDE.cache.md` + `~/.config/pete-cc/MAP.cache.md` (the harness injects only the tiny bootstrap `CLAUDE.md`). Every tool below runs as `VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py`.
1. **Load core memory** -- the full `CLAUDE` + `MAP` came from the Step 0 caches (fallback: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT value FROM config WHERE key='claude-md'"` / `'map-md'`); pull routing from `vault_notes` (`/tmp/pbs/cc-knowledge-api.py "vault-routing"`). Project / entity / customer / property context lives in the live homes: query the **file-index** for "what exists / where is X" (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT drive,path FROM drive_files WHERE …"`) and the **knowledge DB** for notes / decisions / context (`/tmp/pbs/cc-knowledge-api.py`). Don't bulk-load individual project/customer records at resume — pull a specific one on demand when it's referenced in the conversation. **Also load the capability registry** -- the auto-generated `<!-- CAPABILITY-REGISTRY -->` block in `[[connections]]` (what API access exists, which keys are live, helpers available) -- so you start with general capability awareness and never ask Pete what access you have. Property-specific live state arrives via the `property-context-hook` on mention; this registry is the general baseline.
2. **Load recent daily notes** -- the last 3 entries from the CC `daily_log` (`cc-sql.py "SELECT date, content FROM daily_log WHERE cron_name='session' ORDER BY date DESC LIMIT 3"`; if it's empty, the log is fresh — skip, no error). Read Quick Reference sections first; dig deeper only if needed. **Daily notes are a SECOND source.** Use them to spot drift against `public.tasks` (something in narrative but missing from `public.tasks`, or something in `public.tasks` whose status conflicts with narrative). Never quote pending items forward into the briefing without a per-task live-state cross-check against `public.tasks` — see Step 8 for the mechanic. The daily note's `## Garmin daily pull (Automated)` section carries the Garmin recovery + training headline (last night's sleep score + hours, HRV + status, today's training readiness, activity count) — the cron now fires **twice daily (07:00 + 17:00)**, so multiple lines may exist under that section; read the **most-recent line** (last entry) for the freshest activity count. Surface as "Last night (Garmin): ..." if present, and append a `| PUSH FAILED (…)` warning if the most-recent line carries that tag. The full Garmin data lives in the CC `garmin_daily` table (populated twice-daily by the `garmin-daily-pull` Railway cron — the rich `training` block: status, ACWR, training-effect, HR zones); query it via `cc-sql.py`. ([[garmin-api-configuration]].) The Garmin line also carries a **sign-off estimate** (`signed off ~HH:MM (night before)`) — surface it as "Last night you signed off ~HH:MM" and invite a correction. If Pete corrects it (e.g. "actually 23:00"), run `python3 "/tmp/pbs/garmin-daily-pull.py" --set-signoff {today} 23:00` (via Desktop Commander) to record the confirmed time — it wins over the estimate and updates the dashboard. The cron preserves `confirmed` across re-runs, so the 17:00 pull will never overwrite a morning correction. The estimate is a proxy (last Claude/Cowork session activity), so the correction is what makes it true.
2a. **Surface yesterday's PF journal lesson** -- Read `My Drive/Passion Fit/journal/{yesterday-YYYY-MM-DD}.md` **via Desktop Commander** (the journal moved to Drive; the old vault `Personal/passion-fit/journal/` is deleted — mount root `~/Library/CloudStorage/GoogleDrive-pete.ashcroft@sygma-solutions.com/My Drive/Passion Fit/journal/`). Grep for the `## One lesson for tomorrow` heading; extract the line(s) that follow (cut at next `## ` heading or end-of-file). Surface in the Resume briefing as its own line: `**Yesterday's lesson:** {extracted text}`. If the file is missing or the heading is absent, skip silently — no nag from Resume; the 6pm `pf-journal-reminder` cron is the only nagger. Same source-of-truth + same extraction logic as the morning briefing's "Lesson from yesterday" section (canonical process: [[pf-journal#Lesson-flow]]).
3. **Pull task state from public.tasks** -- list Pete's open tasks: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT name,priority,due_on,entity_slug,project_slug FROM tasks WHERE status='todo' ORDER BY priority ASC NULLS LAST, due_on ASC NULLS LAST"`. (Project/entity context lives in the CC + Drive.)
3a. **Detect manually-added tasks** -- Surface tasks added to `public.tasks` since the last session, so Claude absorbs their context before settling on the day's focus.

**Mechanic:**
1. **Cutoff time**: use the most recent `daily_log` `date` (the last session) — its start-of-day in `Atlantic/Canary` is the cutoff. If that's today, fall back to yesterday. (If `daily_log` is empty, use yesterday.)
2. **Query public.tasks**: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT name,priority,due_on,entity_slug,project_slug,notes,created_at FROM tasks WHERE status='todo' AND created_at > <cutoff> AND source <> 'claude' ORDER BY created_at"`. (The `source <> 'claude'` filter excludes tasks Claude created itself — those aren't "added while I was away".)
3. **Surface in briefing** under `**Manual tasks since last session**`: one line per task: `{name} | {entity_slug / project_slug} | {priority} | {due_on or '–'}`. Tap-to-show notes excerpt on demand.
4. **Don't auto-action**. List only. Pete decides what (if anything) needs talking through.

When the most recent daily note covers today (rare edge case where Claude resumes mid-day after a previous Claude session ended already), use yesterday's start-of-day as the cutoff. The aim is "what showed up in public.tasks while I was away".

**Edge cases:**
- Priority unset → display as `–`.
- Task in an unfamiliar `project_slug` (rare) → surface but flag the project.
3b. **Replies tray check (added 2026-06-06)** -- Query Gmail LIVE: `g.search_threads("label:Actions OR label:Replies", max_results=50)` (transition-safe: the tray label was renamed Actions→Replies 2026-06-25; matches either name — trim to `label:Replies` once bedded in). For each thread, age = days since the LAST message on the thread. Surface in the briefing as its own line:
   - `**Replies tray**: {N} waiting ({M} aging >3d): {short-subject} {X}d · {short-subject} {Y}d — say "actions" to walk them with drafts ready.`
   - List every item older than **3 days**, oldest first; cap the inline list at 5 + `+K more aging`.
   - Tray empty → skip the line entirely.
   - The tray is reply-shaped only (**Replies = waiting on Pete to respond by email; a task only when work is required**). Bills/work items are CC tasks with `[no-sync-close]` — they never appear here. Source = live Gmail, never the daily note. Operating manual: [[email-workflow]].
3c. **Projects + Quick Notes index (B1/B7, added 2026-06-25)** -- so Claude always knows where work lives + what's been jotted, without Pete re-explaining ("stop making me repeat myself"):
   - **Projects registry**: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT slug, name, entity_slug, status, drive_folder_url FROM projects WHERE status='active' ORDER BY entity_slug, slug"` — the live CC `projects` table (backfilled from task `project_slug`s; the CC "New project" button + `cc-project-api.py` write here). When Pete says "the X project" / "new bucket" / "put this in Y", resolve against THIS list and its Drive homes — don't re-derive or ask. Buckets: `SELECT project_slug, name FROM buckets`.
   - **Quick Notes**: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT id, title, left(body,80) AS body FROM notes WHERE status='open' ORDER BY pinned DESC, updated_at DESC LIMIT 20"` — the Keep-style scratchpad (CC `notes`, distinct from `vault_notes`). Surface in the briefing as its own line `**Quick notes**: {N} open — {title} · {title} …` (like the Replies tray; skip the line if empty). The **"check notes"** verb pulls them on demand; "note: …" creates one (insert into `notes`); promote-to-task/project/knowledge happens in the CC UI.
   - **New since last session (mirrors 3a's manual-task detection)** — also flag NOTES and PROJECTS added while Claude was away, using the **same `<cutoff>` as Step 3a** (the most recent `daily_log` date's start-of-day in `Atlantic/Canary`, else yesterday). Pete edits the CC directly now, so a note he jots or a project he spins up between sessions must surface the same way a manual task does:
     - Notes: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT title, left(body,80) AS body, created_at FROM notes WHERE created_at > <cutoff> AND status='open' ORDER BY created_at"`
     - Projects: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT name, entity_slug, created_at FROM projects WHERE created_at > <cutoff> AND status='active' ORDER BY created_at"`
     Surface as its own briefing line `**New since last session**: {N} note(s) — {title} · …  ·  {M} project(s) — {name} ({entity}) · …` (skip the line if both are zero). **List only; don't auto-action** — same rule as Step 3a (Pete decides what needs talking through).
4. **Check goals/strategy** -- Pull business/department status from the knowledge DB (`cc-knowledge-api.py`) or the relevant Drive home.
5. **Check session plans** -- Look for incomplete session plans:
   - Query `vault_notes` for plan notes still open: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT title, frontmatter->>'status' FROM vault_notes WHERE type ILIKE '%plan%' AND frontmatter->>'status' IN ('in-progress','ready') ORDER BY source_updated DESC"` (note: there is no `public.plans` table — plans are notes in `vault_notes`).
   - If found, mention them so Pete can pick up where he left off.
6. **Cowork-Inbox check (iPhone -> Cowork bridge)** -- Pete can send requests from his iPhone Claude to Cowork by emailing himself with subject starting `For Claude Cowork`. Brain skill picks these up at session start.
   - Run `g.search_threads("subject:\"For Claude Cowork\" in:inbox newer_than:30d", max_results=20)` via the Gmail helper.
   - For each match, read the full body (`g.get_thread(tid)`).
   - Surface in the briefing: `"X incoming from your iPhone -- want to process now?"`. List one-line summaries.
   - On confirmation, walk through each one: read body (standard shape: What / Where / Why / Done when / optional detail), propose a filing label based on content (existing labels like `Customers/SY-Clancy`, `General/CD-General`, `Suppliers/CD-MVP-Lanzarote`, etc.), execute the actual request (create CC task, write vault file, run audit, send email -- whatever the body asks for), then archive the thread under the chosen label.
   - If the request needs more info, leave thread in inbox and reply asking Pete (don't process partial).
   - If the request is genuinely outside Cowork's scope (very rare -- Cowork has more access than iPhone), reply explaining + leave for Pete to handle.
7. **Ask what to work on** -- If Pete hasn't already said:
   > "What are we working on today? If it's a specific project or property, let me know and I'll load the context."
8. **Present briefing** -- Concise standup format:
   ```
   Welcome back, Pete.

   **Last session** (date): [Brief summary -- link [[projects]] mentioned]
   **Replies tray**: [N waiting (M aging >3d): item Xd · item Yd — say "actions" to walk them]
   **Task priorities**: [P1/P2 tasks from public.tasks]
   **Manual tasks since last session**: [N added to public.tasks -- list one-liners with entity/project + priority]
   **New since last session**: [N notes · M projects added in the CC while away -- one-liners; skip if both zero]
   **From your iPhone** (Cowork-Inbox): [N requests pending -- list one-liners]
   **In Progress**: [[Project-A]] -- [task], [[Project-B]] -- [task]
   **Pending** (cross-checked, not narrative): [items from daily-note pending blocks that survive a live public.tasks check]

   What would you like to focus on today?
   ```
   Skip any line whose count is zero (don't print "Manual tasks since last session: 0").

   **How to derive the Pending line** -- daily notes are a SECOND source. To build the Pending block:
   1. Collect candidate items from yesterday's daily note + today's earlier session logs: every `> [!todo] Pending Tasks` block, every "Live carry-overs" / "Pending into next chunk" / "Pending into next session" list.
   2. For each candidate that names a task id (UUID): `cc-sql.py "SELECT status,due_on FROM tasks WHERE id='<uuid>'"`. Drop the line if `status='done'`. Refresh the due-date if it differs from the candidate.
   3. For each candidate that names a £/€ amount but no id (e.g. "wk5 £1,518.60"): `cc-sql.py "SELECT 1 FROM tasks WHERE status='todo' AND name ILIKE '%<amount>%'"`. Drop the line if 0 rows.
   4. For each candidate that names a person + topic but no id (e.g. "Laura @ MVP — 15 missing Sygma invoices"): `cc-sql.py "SELECT 1 FROM tasks WHERE status='todo' AND name ILIKE '%<keyword>%'"`; drop if 0 rows.
   5. Items that fail the live check are silently dropped from the briefing — they do NOT appear with strikethrough or "carried over" labels. Items that pass appear with their current due-date.
   6. If the Pending block is empty after cross-check, skip the line entirely. Don't pad with stale narrative just to fill space.

   The Δ block being current does not exempt the Pending block. Same source-of-truth rule applies to both.
9. **File-index freshness** -- the `drive_files` index is kept current automatically by the **`drive-changes-watch`** capture cron (every 15 min) — anything Pete or staff add/move in Drive is already captured. No action needed at resume. Belt-and-braces: check its status in the CC Automations registry (`/m/automations-log`) or `public.crons`; a manual catch-up run is `VAULT=/tmp/pbs python3 /tmp/pbs/drive-changes-watch.py`.
10. **Update the daily log** -- append this session's summary to the CC `daily_log` (`date`=today, `cron_name`=`'session'`, `content`=a concise summary: decisions, what shipped, what's pending) via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py` (dollar-quote the content to avoid escaping). **This is what populates the cloud last-session that Resume Step 2 reads** — don't skip it.

### Guidelines
- Keep it short -- like a quick standup, not a data dump
- Prioritize: P1/P2 tasks (from public.tasks), deadlines this week, unfinished work
- If there's no prior history (fresh setup): "This is your first session. What would you like to work on?"

---

## Compress / Save Session

Save everything valuable from the current session.

> **Closing nudge**: when Pete signals he's wrapping up the day or pausing the session for a while -- phrases like "ok thats it", "im done for today", "lets stop here", "going to bed", "back tomorrow", "off out", or a long pause after a clearly-finished body of work -- proactively offer: *"Want me to compress before you go? It'll save the session log, update memory, create any follow-up CC tasks, and reconcile today's TODOs."* Don't wait for an explicit `/brain compress`. Honour his answer either way.
>
> Don't nudge mid-session, don't nudge after every quiet moment, and don't nudge if Pete just asked a question and is waiting for an answer. The signal is: a clear stop signal + a meaningful body of work landed in the session.

### Steps

1. **Save everything** -- Don't ask what to preserve. Automatically save all learnings, decisions, solutions, files modified, pending tasks, and errors.
2. **Create session log** -- INSERT a row into the CC `daily_log` table (the canonical home — the same table Resume Step 2/10 read and the CC **Daily** page (`/m/daily`) renders): `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "INSERT INTO daily_log (date, cron_name, content) VALUES ('<today>', 'session', \$\$<the log below>\$\$)"` (dollar-quote `content` to avoid escaping). One row per session; the CC Daily page groups them by day. Body:
   ```markdown
   ## Session Log: HH:MM -- [Topic Summary]

   ### Quick Reference
   **Topics:** [comma-separated -- use [[wikilinks]] for projects and people]
   **Projects:** [[Project-Name]], [[Project-Name]]
   **Outcome:** [what was accomplished]
   **Duration:** [approximate]

   > [!important] Decisions Made
   > - [[Project-Name]] -- [Decision -- reasoning]

   > [!tip] Key Learnings
   > - [Learning -- link [[related notes]] when applicable]

   > [!info] Solutions & Fixes
   > - [[Project-Name]] -- [Problem -> Solution]

   ### Files Modified
   - [file path -- what changed]

   > [!todo] Pending Tasks
   > - [ ] [[Project-Name]] -- [Task]

   ### Raw Session Summary
   [Condensed summary -- use [[wikilinks]] for every project, person, and vault note mentioned]
   ```
3. **Update memory files (structured-home sweep)** -- Route per `[[vault-routing#master-routing-matrix]]`. **For every distinct topic / entity / project / property / piece of work touched this session, find its home in the cloud — query `vault_notes` (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py`) for knowledge + the `drive_files` index (`cc-sql.py`) for the entity's Drive folder — and update it with what changed + the rationale.** Knowledge / decisions / lessons → ingest a `.md` to `vault_notes`; files → the Drive folder; the session log → CC `daily_log`. The daily log is a pointer only; nothing of substance ends its life there. Operator prefs → CLAUDE.md Rules **only on an explicit Pete correction he asks to be saved**; structured rules → `vault_notes`. See the website-work lesson (in `vault_notes`).
4. **Create CC tasks** -- For any follow-up actions identified during the session, `INSERT` into `public.tasks` with correct `entity_slug`, `project_slug`, and priority (`source='claude'`). Also mark any completed items done via `UPDATE tasks SET status='done', completed_at=now() WHERE id=…`.
5. **Check session plans** -- Look for session plan files created this session:
   - If all steps complete, update `status: completed`
   - If steps incomplete, note pending items in the daily note
   - If an execution plan exists and ALL phases complete, set the project README `status: completed`
6. **Onboarding-ritual completeness check** -- For any new project, customer, supplier, or property created this session, run the verification checklist from `[[vault-routing#new-project--new-property]]` (Step 5) or `[[vault-routing#new-customer--new-supplier]]` (Step 7). If any item is incomplete (e.g. CC record missing a required field, Drive home not created, knowledge note not ingested), complete it before closing the session.
7. **Same-day reconciliation pass** -- Before writing the new session log's `Pending Tasks` block, re-read **every prior `> [!todo] Pending Tasks` block in today's daily note** plus any pending entries in same-day session plans. For each open `[ ]` task:
   - If it has a `(CC: <task-id>)` reference, query `public.tasks` live (`cc-sql.py "SELECT status FROM tasks WHERE id='<uuid>'"`) and read the rest of today's daily note + any commits / READMEs / decision docs touched today for matching evidence (commit hash, README "recent commits" line, decision file, etc.). If shipped: **close the task** (`UPDATE tasks SET status='done', completed_at=now() WHERE id=…`), and **replace the `[ ]` line in-place** with `[x]` + ~~strikethrough~~ + a `**SHIPPED same-day as <evidence>**` marker.
   - If no task id, grep today's daily note for the task's keywords. If a later session log shows the work landed, do the same in-place strike-through with the evidence marker.
   - When uncertain, surface as a question to Pete (`"Looks like X may have shipped via commit Y -- close the task?"`) rather than auto-modifying.
   
   **Why:** before this step existed, each session-log's pending-task snapshot was treated as final. A morning session would open "Wire X" + create a task; a 12:30 detour shipped X as commit ABC; end-of-day Compress wrote the new session log but never re-read the morning's TODO block, so the closed task stayed open and the daily note still claimed `[ ] Wire X`. Pete spots the drift in the morning, vault loses credibility. Surfaced 2026-05-04 via the `x_studio_report_link` writeback (a task, shipped as `ba02060`). See [[2026-05-04-same-day-reconciliation-gap]].
   
   **How to apply:** Runs at end-of-session, before the final Report step. Cheap because today's daily note is small. Touches only TODO lines that have positive evidence (commit hash, decision doc, README log line) -- never strikes a line on assumption alone. Same logic must run in `vault-writer` (separate but parallel cleanup checklist).
7a. **Task staleness sweep** (mirrors vault-writer Step 3b) -- Scan `public.tasks` for stale work and surface a digest: open tasks untouched >21d, long-overdue >14d (`due_on` past), bloated undated clusters (a `project_slug` full of same-day-dumped tasks), completed-but-still-listed clutter. Group by `entity_slug` -> `project_slug` with a one-line call per cluster (close / archive / delegate / verify-then-close). **Surface-only -- never bulk-close, delete, or reassign without Pete's per-item confirmation** (Pete's tasks are sacred). The only unprompted closes are tasks this session demonstrably shipped (Step 7). If nothing crosses the thresholds, say so in one line -- don't manufacture noise.
7b. **Task <-> project-home parity** (mirrors vault-writer Step 3c) -- For every project touched this session, confirm the `public.tasks` `project_slug` grouping and the project's live home (the CC + its Drive folder, per the `drive_files` index) are in sync: the Drive home exists; work this session shipped is reflected in `public.tasks` (done tasks closed) and the corresponding Drive artefact updated; no orphan tasks pointing at a `project_slug` with no home. Surface drift to Pete. Don't sprawl -- default to the parent's `{prefix}-General` `project_slug`; ask before introducing a new `project_slug`. The exhaustive all-projects sweep stays the `vault-check` skill's job.
8. **Report** -- Tell Pete what was saved and where. "You're safe to close. I'll remember everything next time."

### Guidelines
- If the session was short/trivial, create a minimal log (Quick Reference only)
- Be thorough with the Raw Session Summary -- future sessions depend on it

---

## Preserve Knowledge

Save durable knowledge that persists indefinitely.

### Steps

1. **Save immediately** -- Don't ask what to remember. Just save it to the right file.
2. **Search before writing** -- query `vault_notes` (`cc-knowledge-api.py`) / the `drive_files` index to see if content on this topic already exists. Update the existing record rather than creating duplicates.
3. **Route to the right home** -- consult `[[vault-routing#master-routing-matrix]]` for the canonical routing. Knowledge → `vault_notes`; files → Drive; tasks → `public.tasks`.
4. **Map upkeep is automatic** -- `cc_map` (the `/m/map` page) is regenerated by the `cc-map` cron, and the `config.map-md` orientation doc is rendered twice daily by the `cc-orientation-map-sync` cron from the live tables; new knowledge/files are auto-discoverable via `vault_notes` + the `drive-changes-watch` index.
5. **Report** -- After saving, tell Pete what was saved and where.

### Teaching Loop

When Pete corrects you, save the correction. Don't ask. **Where you save it depends on shape:**

- **One-liner sticky rule** (no Why, no How, just a fact or preference) -> append to `CLAUDE.md` under the Rules section.
- **Anything with structure** (rule + Why + How to apply, or a recurring pattern with reasoning) -> write as a `vault_notes` lesson (`type: lesson`, ingested via `cc-knowledge-api.py`) using the lesson template, and add a single-line pointer in CLAUDE.md (`- **Short title.** One-line summary. See [[{slug}]].`).

**CLAUDE.md pointers are for Pete-corrections ONLY.** This is the structural rule. A lesson that emerged from your own observation (methodology, code patterns, debugging insights, audit findings, "things to remember") goes into `vault_notes` as a standalone lesson with no pointer in CLAUDE.md. Knowledge-DB search is sufficient discovery for non-correction lessons. Pete-correction lessons get the pointer because corrections are the rules that bind future behaviour; non-correction lessons are reference notes — keep CLAUDE.md a navigable index of corrections only.

Default to a `vault_notes` lesson when in doubt. Routing structured corrections inline into CLAUDE.md bloats it; lessons exist precisely to absorb them so CLAUDE.md stays a navigable index of corrections only.

Confirm what was saved and where after the fact.

---

## Daily Review

Morning check-in, evening reflection, and weekly review routines.

### Routing

1. Check time: before noon --> suggest morning; after 5pm --> suggest evening
2. If user explicitly requests a type, use that
3. If unclear, ask

### Templates

| Review | Template |
|--------|----------|
| Morning | `references/template-morning-business.md` |
| Evening | `references/template-evening.md` |
| Weekly | `references/template-weekly-business.md` |

Read the appropriate template before generating the review.

### Morning Routine

1. Read the most recent daily note — the latest `daily_log` row in the CC
2. Pull open tasks from `public.tasks`: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT name,priority,due_on,entity_slug,project_slug FROM tasks WHERE status='todo' ORDER BY priority ASC NULLS LAST, due_on ASC NULLS LAST"` -- note P1/P2 priorities and overdue items
3. Check `public.tasks` for approaching deadlines (filter `due_on`)
4. Business: pull business/department status from `vault_notes` (`cc-knowledge-api.py`) or the Drive home.
5. Ask mode-appropriate questions:
   - Main focus, key meetings, blockers
6. Record to the CC `daily_log` (append morning section) with frontmatter
7. Create 1-3 CC tasks (`INSERT` into `public.tasks`) based on energy and deadlines. Report what was created.

### Evening Routine

1. Read today's daily note (morning section if it exists)
2. Compare task progress vs morning intentions
3. Ask mode-appropriate questions:
   - Accomplishments, decisions made, top priority for tomorrow
4. Record to the CC `daily_log` (append evening section)
5. Mark completed tasks done in `public.tasks` (`UPDATE tasks SET status='done', completed_at=now() WHERE id=…`); route any new insights to the right file

### Weekly Review

1. Read this week's daily notes — the `daily_log` rows in the CC
2. Scan `public.tasks` for movement
3. Pull completed tasks for the week (`SELECT name FROM tasks WHERE status='done' AND completed_at >= <week-start>`) -- celebrate wins
4. Business: Check OKR progress, department health
5. Ask mode-appropriate questions:
   - Biggest win, OKR progress, blockers, focus for next week
6. Save to the CC `daily_log` (`INSERT … cron_name='weekly-review'` for today's date) — not a vault file.
7. Plan top 3 priorities for next week; create CC tasks (`INSERT` into `public.tasks`) automatically
8. Archive completed items if appropriate

---

## Task Management

Pete's tasks live in the CC `public.tasks` table. All CRUD runs via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "<SQL>"`.

### Schema notes

- **Priority** is manual P1–P4 (P1 highest). PD forces a date.
- **`entity_slug`** ∈ Sygma / Canary Detect / Personal / One System / El Atico.
- **`project_slug`** groups tasks (e.g. `Team-General`, `PA-Command-Centre`).
- **`source`** = `'claude'` for tasks Claude creates.

### Creating a Task

```sql
INSERT INTO tasks (id,name,priority,due_on,entity_slug,project_slug,status,source,notes)
VALUES (gen_random_uuid(), '<name>', '<P1–P4>', '<YYYY-MM-DD or NULL>',
        '<entity_slug>', '<project_slug>', 'todo', 'claude', '<notes>');
```

### Listing Tasks

Open tasks, highest priority / soonest due first:

```sql
SELECT name,priority,due_on,entity_slug,project_slug FROM tasks
WHERE status='todo' ORDER BY priority ASC NULLS LAST, due_on ASC NULLS LAST;
```

Filter by entity or project with `AND entity_slug='…'` / `AND project_slug='…'`.

### Updating a Task

- **Complete**: `UPDATE tasks SET status='done', completed_at=now() WHERE id=…`
- **Reprioritise**: `UPDATE tasks SET priority=… WHERE id=…`
- **Reschedule**: `UPDATE tasks SET due_on=… WHERE id=…`

### Guidelines
- Always set `entity_slug`, `project_slug`, and priority when creating tasks
- At session end, create CC tasks for follow-up actions
- Present task lists as clean markdown tables
- For bulk operations, confirm with Pete first

---

## Output Styles

Output styles define how the assistant communicates. Styles are bundled as reference files within this skill (`references/style-*.md`). Users can override or add custom styles in `.claude/output-styles/`.

### Available Styles

**All modes:**

| Style | Reference File | Use When |
|-------|---------------|----------|
| Conversation | `references/style-conversation.md` | Default -- chat, brainstorming, Q&A |
| YouTube Script | `references/style-youtube-script.md` | Video scripts |
| Blog Post | `references/style-blog-post.md` | Long-form articles |
| Quick Reply | `references/style-quick-reply.md` | DMs, short messages |
| Email | `references/style-email.md` | Professional emails |
| Meeting Summary | `references/style-meeting-summary.md` | Meeting transcripts |

**Business mode only:**

| Style | Reference File | Use When |
|-------|---------------|----------|
| SOP | `references/style-sop.md` | Standard operating procedures |
| Report | `references/style-report.md` | Business reports for stakeholders |

### Loading a Style

1. **Check vault first**: If `.claude/output-styles/{style-name}.md` exists, use that (user override)
2. **Fall back to reference**: Otherwise, read `references/style-{style-name}.md` from this skill's references
3. **Default**: Always use `conversation` style unless told otherwise

### Switching

- **Explicit**: "write a YouTube script" --> load `youtube-script` style
- **Context clues**: Working on meeting transcript --> auto-switch to `meeting-summary`
- **"Go back to normal"** --> revert to `conversation`

### Creating Custom Styles

1. Ask about content type, tone, format, and rules
2. Create at `.claude/output-styles/[style-name].md` with sections: Identity, Tone, Format, Rules, Examples
3. Test with a sample, iterate until happy

### Personalization

User voice from the primary context file is applied ON TOP of the active style.

---

## Resources

`Library/` is Pete's organisation library -- processes, market intel, competitors, decisions, ip-trademark, templates, reference material, archived projects.

### Saving Resources

When Pete shares reusable content, route per `[[vault-routing#master-routing-matrix]]`. Processes / SOPs / API-config docs stay in `Library/processes/` (the surviving skeleton); everything else — competitor intel, market research, templates, decisions, IP/trademark — is **knowledge → ingest a `.md` to `vault_notes`** (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-ingest.py <file>` → null embedding → `cc-knowledge-embed-backfill.py`). Add `tags:`; report what was saved and where.

### Finding Resources

1. **Knowledge** → `VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py "<query>"` (full-text + semantic over `vault_notes`).
2. **Files** → the `drive_files` index: `cc-sql.py "SELECT drive,path FROM drive_files WHERE name ILIKE '%X%'"`.
3. Read and present the matching resource.

---

## Web Content Extraction

When Pete shares a URL for context:

```bash
defuddle parse <url> --md
```

Defuddle strips clutter and returns clean markdown. If not installed, fall back to standard web fetch.

---

## Meeting Intelligence

Process meeting transcripts, extract decisions and action items, sync from Fireflies, and file meeting notes.

USE WHEN Pete:
- Pastes a transcript or drops a transcript file
- Asks to summarize a meeting or extract action items
- Asks about past meetings
- Mentions Fireflies, asks to sync or pull transcripts

### Step 1: Identify Meeting Type and Save Location

Meeting notes are **knowledge → `vault_notes`**: write the note (`type: meeting`, a `meeting_type` tag — standup / client-call / one-on-one / board-review / all-hands / cross-team / general — + entity tags) and ingest it (`cc-knowledge-ingest.py` → null embedding → `cc-knowledge-embed-backfill.py`). Client-call notes wikilink to the customer's CC record.

Filename: `YYYY-MM-DD Meeting Title.md`.

### Step 2: Load Output Style

Read `.claude/output-styles/meeting-summary.md`. If missing, use `references/template-meeting-note.md`.

### Step 3: Extract from Transcript

1. Key decisions
2. Action items -- who, what, when
3. Discussion summary
4. Open questions
5. Follow-up items

### Step 4: Create the Meeting Note

Frontmatter:

```yaml
---
type: meeting
subtype: team-standup | client-call | one-on-one | board-review | all-hands | cross-team | general
date: YYYY-MM-DD
time: HH:MM
participants: [[[Person A]], [[Person B]]]
duration: X minutes
source: manual | fireflies
status: processed
---
```

Body uses callouts:

```markdown
## Participants
- [[Person A]]
- [[Person B]]

## Summary
[2-3 sentence overview]

> [!important] Key Decisions
> - [Decision 1]

> [!todo] Action Items
> - [ ] [[Person A]] -- [Task] (by [date])

## Discussion Notes
### [Topic 1]
[Summary]

> [!question] Open Questions
> - [Unresolved item]

> [!info] Follow-up
> - Next meeting: [date/time]
```

Business mode additions:
- Board reviews: `> [!warning] Governance Items` callout
- All-hands: `> [!info] Company Announcements` callout
- Cross-team: `> [!todo] Department Dependencies` callout

### Step 5: Create CC Tasks from Action Items

`INSERT` a row into `public.tasks` for each action item assigned to Pete. Set `entity_slug`, `project_slug`, and priority (`source='claude'`). (Action items owned by Jane go to her own queue, not here.)

### Step 6: Link and Update

- Add `project:` and `department:` to frontmatter where applicable
- Use [[wikilinks]] for all project and person references
- The meeting note lives in `vault_notes` (ingested); the cloud map auto-regenerates

### Fireflies Sync

**MCP Server (Business Plan):** Check `.claude/settings.json` for fireflies config. Use `fireflies_list_transcripts` and `fireflies_get_transcript`.

**Manual Export (Free Plan):** Have Pete export from app.fireflies.ai, paste or drop file.

---

## Scheduled tasks brain is aware of

Crons modify vault files before sessions start — **always Read a daily note before appending**. Don't shadow-run any cron from a session.

**No cron list is embedded here — embedded copies drift (locked 2026-06-06; the old table here was stale on schedule AND contents).** The sources of truth:

- `[[scheduled-tasks]]` — narrative registry, entry per task with vault-touch lists. **Read its header rules before editing any entry** — it carries the dashboard 3-step.
- `Library/processes/automations-dashboard/automations.json` → live view at https://pete-automations.vercel.app
- `mcp__scheduled-tasks__list_scheduled_tasks` — live Cowork cron state

Any cron change (create / edit / pause / decommission, any runtime) must run the dashboard 3-step: update `automations.json` → re-embed `index.html` → `deploy.py`. See [[2026-06-06-cron-changes-update-dashboard-skills-point-at-registries]].

## General Guidelines

- **The cloud map is your index.** The `config.map-md` orientation doc + the generated `cc_map` (the `/m/map` page) tell you what exists and where; the `drive_files` + `vault_notes` indexes are the live "what's where" lookups. Search them before creating anything.
- **Memory protocol**: Load the cached `CLAUDE` + `MAP` at session start. Route new knowledge per `[[vault-routing]]`.
- **Sweep verb**: `sweep` is a single deliberate verb, manual trigger only. No skill should auto-offer it. Email-workflow operating manual: `[[email-workflow]]`.
- **Email-workflow verbs**: `triage`, `sync`, `hand to`, `reply` (tray: Replies label, **no task**), `task` (CC task: no Replies label, `[no-sync-close]`), `reply + task` (the combo — a Reply with a prep task), `replies` / `my replies` (tray walker; legacy `actions`), `de-tray this`, `file`, `file all emails`, `add to calendar`. One-sentence rule: **Replies = waiting on Pete to respond by email; a task only when work is required** (decoupled 2026-06-25). See `[[email-workflow]]` -- handled by `inbox-triage` and `email-task-sync` skills.
- **Wikilinks everywhere**: Every mention of a project, person, or knowledge note MUST be a `[[wikilink]]`.
- **Teaching loop**: When corrected, save the correction. One-liner sticky rules -> CLAUDE.md. Structured rules (rule + Why + How) -> a `vault_notes` lesson (`type: lesson`) + one-line pointer in CLAUDE.md. Don't ask. **The pointer is for Pete-corrections only -- see the full Teaching Loop section above.**
- **Lessons**: lessons live in `vault_notes` (`type: lesson`) — behavioural rules with Why/How structure. Sessions can also write a lesson when a non-correction insight emerges that future sessions should know — those carry NO pointer in CLAUDE.md; knowledge-DB search (`cc-knowledge-api.py`) is their discovery surface.
- **Outbound text drafting**: read `[[voice-principles]]` before drafting any outbound text on Pete's behalf (customer email, supplier email, internal email, blog, article, ad copy).
- **Finance / invoicing / Soldo / Dext / Odoo / Xero / payroll / VAT**: read `[[finance-workflow]]` first.
- **Helper scripts**: in GitHub `pete-brain-scripts`, pulled to `/tmp/pbs` by the boot kernel — run `VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py`. Don't reinvent; check the CC Helpers registry (`/m/process-library`).
- **Connectors and APIs**: registry at `[[connections]]` -- don't guess what's connected or how it auths.
- **Sygma Hub content**: lives in the **Sygma Hub** Google Drive folder (indexed in `drive_files`). When Pete asks about Sygma policies / training reference / company info / HR / sales pipeline, query the Drive index first. See `[[hub-content-index]]`.
- **Daily notes**: the CC `daily_log` table (one row per session, `cron_name='session'`) is the session diary + most-read memory — keep it current; the CC **Daily** page (`/m/daily`) renders it.
- **Working files live in Drive + the CC.** Project artefacts go in the entity's Drive home; knowledge goes in `vault_notes`; code repos clone into a temp directory (`/tmp/...`). Nothing permanent is written to local disk.
- **Search before writing.** Query the `drive_files` / `vault_notes` indexes (and grep `/tmp/pbs`) before creating anything.
- **Properties before property work.** Read the property's CC record before any SEO/ads/analytics work.
- **CC `public.tasks` is Pete's task system.** Create, update, and complete tasks via `cc-sql.py`.

## Skill orchestration

Brain owns workflow orchestration. Hand off to specialised skills when their verbs hit:

| Verb / phrase | Skill |
|---|---|
| `triage` | `inbox-triage` |
| `sync`, `sweep` (verb), orphan reconciliation | `email-task-sync` |
| `audit this page`, `ahrefs audit`, "research this keyword" | `ahrefs-audit` |
| `fortnightly review`, "check the positions", "has it moved" | `audit-review` |
| `connect to my site`, "look at my app", "set up a new project" | `property-manager` |
| `simplify`, "review my code" | `simplify` |
| End-of-session vault cleanup | `vault-writer` (defers to brain Compress for task sync) |
| Distinctive frontend design | `frontend-design` |

## Auto-Save Rule

**Never ask permission to save.** When meaningful info comes up, save it to the right home immediately (knowledge → `vault_notes`, files → Drive, tasks → `public.tasks`). Report what was saved and where.

## Anti-Patterns

Do NOT:
- Ask "should I save this?" -- just save it
- Write project names or people as plain text -- use `[[wikilinks]]`
- Use `[markdown](links)` for internal knowledge notes -- use `[[wikilinks]]`
- Put a `# Title` heading that duplicates the note title
- Create orphan notes
- Read entire files when scanning many -- use `grep`
- Record knowledge on casual chat
- Start real work without writing a session plan first
- Create tasks anywhere other than CC `public.tasks` (Pete's tasks)
- Write any permanent file to local disk -- it belongs in one of the four homes (Drive / `vault_notes` / `public.tasks` / the CC)
- Duplicate routing rules into this skill -- they live in [[vault-routing]] only

## Pete's Preferences for Written Content

- Human, natural tone (not corporate, not AI-sounding)
- British English spelling
- No unnecessary jargon
- **Outbound text** (email, article, blog, ad copy, customer reply, anything sent to a recipient): read `[[voice-principles]]` first. The dash rule, voice patterns, and AI-tells live there. Internal vault content (md files, plans, daily notes, audits) is not subject to those rules.

## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill per [[2026-05-16-lesson-deployment-matrix]]:

- [[2026-05-06-vault-bookkeeping-with-artefacts]]
- [[2026-05-24-mirror-source-system-dating-not-a-cleverer-model]] — for any data Pete also reads in a source app (Garmin, bank, console), match that app's own dating/labels; don't invent a smarter scheme that contradicts their screen.
- [[2026-05-24-gcal-updated-timestamp-race-after-create]] — calendar/sync race-condition pattern; Resume reads gcal-twice-daily-sync output.
- [[2026-05-25-calendar-sync-window-mismatch-births-past-event-dupes]] — Resume reads gcal cron line; understand window-mismatch failure mode.
- [[2026-05-25-garmin-daily-pull-must-rebase-before-push]] — Resume Step 2 reads Garmin pull line; PUSH FAILED warning is part of that line.
- [[2026-05-19-ip-takedown-attribution-vs-speed-trade-off]] — IP enforcement methodology; fires when brain handles IP-portfolio / takedown tasks.
- [[2026-05-26-enforcement-campaigns-surface-counter-attack-vectors-at-planning]] — surface counter-attack + personal-liability vectors at planning; fires on any IP / regulatory / public-callout campaign brain orchestrates.
- [[2026-05-27-pptx-image-pass-build-from-pristine-backup-and-match-logo-to-bg]] — deck/document image-placement work; build from pristine backup, match each logo to its slide background.

