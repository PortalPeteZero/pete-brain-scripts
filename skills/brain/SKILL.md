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
| "close out", "/closeout", "are we done / everything saved?", wrapping up a session that **touched a property** (SEO site, LeakGuard, the CC, a new build) | the **`closeout`** skill — the property-work end-of-session command (records only this session's own commits via the shared `session_attribution.py` gate, verifies live state, hands over one menu). Distinct from Compress (the general session save); closeout does NOT chain Compress. |
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

Sygma training-enquiry handling is a **living learning machine**. The full operating contract — process steps, pricing, te-log capture, routing (e.g. Neal Sadd for L3–5/mapping), and all banked rules — lives in the `vault_notes` note **[[workflow-design]]**. **On ANY enquiry verb, load that note first and follow it** (it ranks top on an enquiry semantic-search); never work an enquiry from this summary alone. Lifecycle = Portal CRM; knowledge = `vault_notes` `training-enquiries`; chases = `public.tasks`; cockpit **/m/enquiry-engine**.

Non-negotiable gates (detail in the note): **read the ENTIRE thread before drafting** (what we've sent / they asked / whose court the ball is in); **retrieve precedents before drafting** (never cold); **Mode B — Pete signs off every send, never auto-send**; chase only when ≥3 days *and* the ball is with them.

**⛔ Capture-on-send is a hard gate.** A send is NOT done until `te-log --apply` has run for it (CRM activity + stage, knowledge note, chase task) — this holds for EVERY exit path, including just sending a queued draft or a batch of signed-off drafts, not only the `enquiry` loop. **Never de-tray an enquiry by hand** — `te-log --apply` does de-tray + archive + CRM + knowledge + chase in one; manual label-stripping silently skips the capture (failure 30 Jun 2026). After capture, close any duplicate chase the triage routing already made.

Verbs: **`enquiry`** (handle one inbound) · **`enquiries`** (manual sweep, Pete-triggered, NO cron) · **`reply to enquiry in {X}`** (the loop on one) · **`Sent to Sue`** (booking handoff → Customer/won). All follow [[workflow-design]].

## Markdown formatting

Notes use lightweight markdown: `[[wikilinks]]` (link a knowledge note by its name in `vault_notes`), `> [!type] Title` callouts (tip / warning / important / question / todo / success), `==highlights==`, `%%comments%%`.

## Projects + buckets

- A **project** is a row in the CC `public.projects` table (own `slug` + `entity_slug` + Drive folder + knowledge home).
- A **bucket** is a sub-grouping within a project (CC `public.buckets`: `project_slug` + name); every project has a default **General** bucket.
- **To create one, run the build-out helper:** `VAULT=/tmp/pbs python3 /tmp/pbs/cc-project-api.py "Name" --entity "<Sygma|Canary Detect|Personal|One System|El Atico>" [--desc "…"] [--gmail]`. It creates the `projects` row + General bucket + the Drive folder in the entity's drive + a seeded `vault_notes` knowledge home (tagged with the slug) and reports every link. The CC "New project" button writes the same row + bucket. **Resume reads the `projects` table (Step 3c)** so you already know what exists — propose-then-confirm, default to an existing project's General bucket, don't create a project for 1-2 tasks.

## Task system — CC public.tasks

Pete's tasks live in the CC `public.tasks` table. All task CRUD runs via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "<SQL>"`.

### Task model — the date is the switch (2026-07)

- **Priority**: **PD** = a dated commitment (the ONLY shape that carries a `due_on`; can be overdue; auto-rolls to the next realistic day if missed; syncs to Google Tasks + shows on the calendar). **P1–P3** = undated importance ranking (do-next → eventually; **never dated**). **P4** = someday / backlog.
- **The date IS the switch**: give a task a `due_on` and it becomes a **PD** automatically (a DB trigger, `tasks_date_is_the_switch`, enforces this — you cannot store a dated P1/P2/P3). Clear the date and it reverts to its stored **`base_priority`** (the undated tier). So never write a dated P1/P2/P3; a date always means PD.
- **Claude never auto-sets an inferred PD date** — flag it and confirm the date with Pete first. **Exception: bills** — set the invoice due date without asking, always PD, routed to `Team-Finances`.
- **Undated P4 = the project backlog** — park via `cc-park.py`; the board shows one "Work through {Project} backlog" pointer.
- **`entity_slug`** ∈ Sygma / Canary Detect / Personal / One System / El Atico.
- **`project_slug`** groups tasks (e.g. `General`, `PA-Command-Centre`).

### Key Operations

- **Create task**: `INSERT INTO tasks (id,name,priority,due_on,entity_slug,project_slug,status,source,notes) VALUES (gen_random_uuid(),…,'todo','claude',…)` — leave `due_on` NULL for P1–P4; a date ⇒ PD (the trigger sets `priority='PD'`). For a PD, also set `base_priority` to the tier it reverts to when the date is cleared.
- **Complete task**: `UPDATE tasks SET status='done', completed_at=now() WHERE id=…`
- **List Pete's open tasks (PD-aware order — PDs by date first, then the undated tiers)**: `SELECT name,priority,due_on,entity_slug,project_slug FROM tasks WHERE status='todo' ORDER BY CASE priority WHEN 'PD' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 5 END, due_on ASC NULLS LAST`
- **Reprioritise / reschedule**: `UPDATE tasks SET priority=…/due_on=… WHERE id=…` (setting `due_on` auto-flips it to PD; when clearing it, also set `priority=base_priority`).

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

> [!important] Plan lifecycle — a finished plan must not look live; a dead plan must not look active
> Plans carry an auto-stamped `<!-- PLAN-LIFECYCLE-BANNER -->` (open = "verify live" · done = "historical" · scrapped = "⛔ dead"), driven by the frontmatter `status`. So whenever a plan's state changes, **change its status** (and re-ingest / fix the banner so it re-stamps):
> - **Executed / shipped** → set `status: completed`. NEVER leave a shipped plan as `ready`/`in-progress` — that is exactly what makes a future grep mistake it for live state.
> - **Scrapped / abandoned** → EITHER **hard-delete the plan note** (snapshot first — the default when it is pure noise with no lasting value) **OR** set `status: scrapped` (the banner becomes ⛔ "do not use"). Choose: delete if it should leave no trace; stamp if the *decision not to do it* is worth keeping. Never leave a dead plan looking active.
> - To re-stamp after a status change: re-ingest the plan (`cc-knowledge-ingest.py` → re-embed), or if editing the `vault_notes` row directly, update the banner at the top of the body too.

---

## Resume Session

Reconstruct full context so Pete picks up where he left off.

### Steps

0. **⚡ BOOT KERNEL — DO THIS FIRST; nothing below works without it.** Run `python3 ~/.config/pete-cc/pete-session-bootstrap.py`. It clones the tools to `/tmp/pbs`, materialises all secrets, and refreshes `~/.config/pete-cc/CLAUDE.cache.md` + `MAP.cache.md`. **Then verify `/tmp/pbs` exists — if it does NOT (clone failed), STOP and tell Pete; never proceed half-booted.** Then read your FULL operating instructions + MAP from `~/.config/pete-cc/CLAUDE.cache.md` + `~/.config/pete-cc/MAP.cache.md` (the harness injects only the tiny bootstrap `CLAUDE.md`). Every tool below runs as `VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py`.
1. **Load core memory** -- the full `CLAUDE` + `MAP` came from the Step 0 caches (fallback: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT value FROM config WHERE key='claude-md'"` / `'map-md'`); pull routing from `vault_notes` (`/tmp/pbs/cc-knowledge-api.py "vault-routing"`). Project / entity / customer / property context lives in the live homes: query the **file-index** for "what exists / where is X" (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT drive,path FROM drive_files WHERE …"`) and the **knowledge DB** for notes / decisions / context (`/tmp/pbs/cc-knowledge-api.py`). Don't bulk-load individual project/customer records at resume — pull a specific one on demand when it's referenced in the conversation. **Also load the capability registry** -- the auto-generated `<!-- CAPABILITY-REGISTRY -->` block in `[[connections]]` (what API access exists, which keys are live, helpers available) -- so you start with general capability awareness and never ask Pete what access you have. Property-specific live state arrives via the `property-context-hook` on mention; this registry is the general baseline.
2. **Load recent daily notes** -- the last 3 entries from the CC `daily_log` (`cc-sql.py "SELECT date, content FROM daily_log WHERE cron_name='session' ORDER BY date DESC LIMIT 3"`; if it's empty, the log is fresh — skip, no error). Read Quick Reference sections first; dig deeper only if needed. **Daily notes are a SECOND source.** Use them to spot drift against `public.tasks` (something in narrative but missing from `public.tasks`, or something in `public.tasks` whose status conflicts with narrative). Never quote pending items forward into the briefing without a per-task live-state cross-check against `public.tasks` — see Step 8 for the mechanic. The daily note's `## Garmin daily pull (Automated)` section carries the Garmin recovery + training headline (last night's sleep score + hours, HRV + status, today's training readiness, activity count) — the cron now fires **twice daily (07:00 + 17:00)**, so multiple lines may exist under that section; read the **most-recent line** (last entry) for the freshest activity count. Surface as "Last night (Garmin): ..." if present, and append a `| PUSH FAILED (…)` warning if the most-recent line carries that tag. The full Garmin data lives in the CC `garmin_daily` table (populated twice-daily by the `garmin-daily-pull` Railway cron — the rich `training` block: status, ACWR, training-effect, HR zones); query it via `cc-sql.py`. ([[garmin-api-configuration]].) The Garmin line also carries a **sign-off estimate** (`signed off ~HH:MM (night before)`) — surface it as "Last night you signed off ~HH:MM" and invite a correction. If Pete corrects it (e.g. "actually 23:00"), run `python3 "/tmp/pbs/garmin-daily-pull.py" --set-signoff {today} 23:00` (via Desktop Commander) to record the confirmed time — it wins over the estimate and updates the dashboard. The cron preserves `confirmed` across re-runs, so the 17:00 pull will never overwrite a morning correction. The estimate is a proxy (last Claude/Cowork session activity), so the correction is what makes it true.
2a. **Surface yesterday's PF journal lesson** -- Query the CC `health_journal` table (the canonical home since 2026-06-27 migration; Drive/local paths are retired): `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT body FROM health_journal WHERE date = '{yesterday}'"`. Grep the returned `body` for the `## One lesson for tomorrow` heading; extract the line(s) that follow (cut at next `## ` heading or end-of-body). Surface in the Resume briefing as its own line: `**Yesterday's lesson:** {extracted text}`. If the row is missing or the heading is absent, skip silently — no nag from Resume; the 6pm `pf-journal-reminder` cron is the only nagger. Same source-of-truth + same extraction logic as the morning briefing's "Lesson from yesterday" section (canonical process: [[pf-journal#Lesson-flow]]). **Never read a local Drive mount or use Desktop Commander for this.**
3. **Pull task state from public.tasks** -- list Pete's open tasks: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT name,priority,due_on,entity_slug,project_slug FROM tasks WHERE status='todo' ORDER BY CASE priority WHEN 'PD' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 5 END, due_on ASC NULLS LAST"`. (Project/entity context lives in the CC + Drive.) **Before presenting an open task as a live priority, sanity-check it for shipped-evidence** (a commit, a README line, the artefact already existing, a `daily_log` entry) — the same source-of-truth discipline as the Pending line (Step 8). If a task looks already done, do NOT list it as a to-do: surface it as **"Looks shipped — close? {task}"** for Pete to confirm. Never auto-close a task at resume — tasks are Pete's.
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
   - **Verify each one live before you surface it** — a `ready`/`in-progress` label is a CLAIM, not state (it routinely outlives the work; see "A PLAN IS NEVER THE LIVE STATE" in General Guidelines). For each plan, cross-check what it planned against the live system: the orientation map / live tables / `public.tasks` / today + recent `daily_log` for shipping evidence. If the work has demonstrably shipped, do **NOT** announce it as in-progress — surface it as **"Looks shipped — close this plan? {title}"** for Pete to confirm (never stamp it done at resume without his OK). Only present a plan as live after the check finds real remaining work, and name the outstanding item ("Plan {title} reads in-progress — I confirmed {X} is still outstanding"). Never forward a plan's status label unverified — this is the exact miss behind "a fresh session tells Pete a plan's still in progress when it actually shipped".
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
>
> **Context-switch close-out (distinct from the day-end nudge above):** when Pete pivots mid-session from one project/app to a clearly different one *after a meaningful body of work landed on the first*, run a lightweight close-out on the project being **left** — stamp the plans you finished (`completed`/`scrapped` + re-ingest so the banner re-stamps), fence/stamp any state-bearing notes you updated (the `[[vault-writer]]` note-fencing rule), and reconcile its tasks (close-on-ship the ones you demonstrably shipped; surface ambiguous ones as "close? {task}"). Do it **inline in one line** — don't block Pete, don't make him ask. This is the prevention half: it stops the left project re-surfacing as "live" next session, even if no end-of-session Compress ever runs.

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
4. **Follow-up tasks -- PROPOSE, never auto-create.** Surface follow-up actions from the session as a short *suggested* list in the Report step. **Create a CC task ONLY when Pete explicitly asks** ("make a task for that" / "add those"). Auto-creating follow-ups is forbidden -- they pile up as clutter (Pete, 28 Jun 2026). Marking *completed* items done (`UPDATE tasks SET status='done', completed_at=now() WHERE id=…`) when work demonstrably shipped is still fine -- it is *creating* new tasks unprompted that is banned.
4a. **Connection check** -- if this session added, changed, expanded, rotated, or retired any external access (an API key/token, MCP connector, OAuth app, service account), run the **`connection-updater`** skill for each before closing (it stores the secret pointer-only, registers the connection, and its `connection-parity.py` gate must print `0 gaps`). The weekly `drift-check` backstop catches anything missed, but same-session is cleaner.
5. **Check session plans** -- Look for session plan files created this session:
   - If all steps complete, update `status: completed`
   - If steps incomplete, note pending items in the daily note
   - If an execution plan exists and ALL phases complete, set the project README `status: completed`
6. **Onboarding-ritual completeness check** -- For any new project, customer, supplier, or property created this session, run the verification checklist from `[[vault-routing#new-project--new-property]]` (Step 5) or `[[vault-routing#new-customer--new-supplier]]` (Step 7). If any item is incomplete (e.g. CC record missing a required field, Drive home not created, knowledge note not ingested), complete it before closing the session.
7. **Same-day reconciliation pass** -- Before writing the new session log's `Pending Tasks` block, re-read **every prior `> [!todo] Pending Tasks` block in today's daily note** plus any pending entries in same-day session plans. For each open `[ ]` task:
   - If it has a `(CC: <task-id>)` reference, query `public.tasks` live (`cc-sql.py "SELECT status FROM tasks WHERE id='<uuid>'"`) and read the rest of today's daily note + any commits / READMEs / decision docs touched today for matching evidence (commit hash, README "recent commits" line, decision file, etc.). If shipped: **close the task** (`UPDATE tasks SET status='done', completed_at=now() WHERE id=…`), and **replace the `[ ]` line in-place** with `[x]` + ~~strikethrough~~ + a `**SHIPPED same-day as <evidence>**` marker.
   - If no task id, grep today's daily note for the task's keywords. If a later session log shows the work landed, do the same in-place strike-through with the evidence marker.
   - When uncertain, surface as a question to Pete (`"Looks like X may have shipped via commit Y -- close the task?"`) rather than auto-modifying.
   
 **Why:** before this step existed, each session-log's pending-task snapshot was treated as final. A morning session would open "Wire X" + create a task; a 12:30 detour shipped X as commit ABC; end-of-day Compress wrote the new session log but never re-read the morning's TODO block, so the closed task stayed open and the daily note still claimed `[ ] Wire X`. Pete spots the drift in the morning, vault loses credibility. Surfaced 2026-05-04 via the `x_studio_report_link` writeback (a task, shipped as `ba02060`).
   
   **How to apply:** Runs at end-of-session, before the final Report step. Cheap because today's daily note is small. Touches only TODO lines that have positive evidence (commit hash, decision doc, README log line) -- never strikes a line on assumption alone. Same logic must run in `vault-writer` (separate but parallel cleanup checklist).
7a. **Task staleness sweep** (mirrors vault-writer Step 3b) -- Scan `public.tasks` for stale work and surface a digest: open tasks untouched >21d, long-overdue >14d (`due_on` past), bloated undated clusters (a `project_slug` full of same-day-dumped tasks), completed-but-still-listed clutter. Group by `entity_slug` -> `project_slug` with a one-line call per cluster (close / archive / delegate / verify-then-close). **Surface-only -- never bulk-close, delete, or reassign without Pete's per-item confirmation** (Pete's tasks are sacred). The only unprompted closes are tasks this session demonstrably shipped (Step 7). If nothing crosses the thresholds, say so in one line -- don't manufacture noise.
7b. **Task <-> project-home parity** (mirrors vault-writer Step 3c) -- For every project touched this session, confirm the `public.tasks` `project_slug` grouping and the project's live home (the CC + its Drive folder, per the `drive_files` index) are in sync: the Drive home exists; work this session shipped is reflected in `public.tasks` (done tasks closed) and the corresponding Drive artefact updated; no orphan tasks pointing at a `project_slug` with no home. Surface drift to Pete. Don't sprawl -- default to the single `General` `project_slug`; ask before introducing a new `project_slug`. The exhaustive all-projects sweep stays the `vault-check` skill's job.
7c. **Log-on-ship to the Work Log** (mirrors `vault-writer` Step 3a's log-on-ship; runs whichever skill closes the session) -- in the same pass over what this session shipped, write a [[work-log]] row for each discrete ship that touched a website property, the CC platform, **or any product repo we ship** (a separate app such as LeakGuard counts): `VAULT=/tmp/pbs python3 /tmp/pbs/worklog.py --property "<name>" --area <seo|content|dev|design|ops|...> --title "<what shipped>" --evidence "<before->after>" --outcome <worked|too-early|unknown> --link <commit/PR/doc> --source-ref "git:<owner>/<repo>@<sha>"`. One row per ship, idempotent on `--source-ref`, evidence + outcome required for seo/dev/ads (the helper refuses otherwise -- the same rule as the DB). property-manager Step 6f² already logs code commits *as they land*; this Compress sweep is the safety net for ships outside that path (a non-property script, a config change, a published report). **Deterministic, ownership-gated gate -- don't trust recall, and never grab another session's commits:** run `VAULT=/tmp/pbs python3 /tmp/pbs/closeout-sweep.py --apply` (the shared record gate). It reconciles every checkout you touched against `work_log.source_ref` AND filters to the commits THIS session actually made -- proven from the transcript's `gitOperation.commit.sha` via `session_attribution.py` -- so it logs only your own unlogged ships (idempotent on `source_ref`) and merely SURFACES unlogged commits that belong to other live sessions. This closes the today-bug: before the ownership filter this step ran a bare `worklog.py reconcile` per repo and "logged every commit it flagged", which on a multi-session day grabbed OTHER sessions' commits (30-Jun / 04-Jul). `session_attribution.py` is the SAME helper the `closeout` skill uses, so whichever end-of-session writer runs first only ever logs its own work. (Bare `worklog.py reconcile --repo <owner>/<repo> --git-dir <checkout>` is still the read-only discovery underneath, and raw main-session dev/deploy work no per-ship hook covers still can't slip through. This gate was added 2026-06-30 after a full day of LeakGuard deploys reached no work_log row until Pete asked; the ownership filter was added 2026-07-04.) Skip pure-knowledge / triage / health sessions with no shippable artefact. Surfaced at **/m/work-log** -- the cross-property "what did we do / did it work" index.
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

**Separate track — a new/changed CONNECTION is not a lesson.** If the "correction" is actually Pete handing over external access (an API key, a connector, an OAuth login) or telling you to expand/rotate/retire one, that goes through the **`connection-updater`** skill (secret → `public.secrets`, registry row, config note, parity gate), not the lesson/CLAUDE.md track.

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
2. Pull open tasks from `public.tasks`: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT name,priority,due_on,entity_slug,project_slug FROM tasks WHERE status='todo' ORDER BY CASE priority WHEN 'PD' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 5 END, due_on ASC NULLS LAST"` -- note PDs due/overdue (overdue is PD-only now) + the top undated tiers (P1/P2)
3. Check `public.tasks` for approaching deadlines (filter `due_on`)
4. Business: pull business/department status from `vault_notes` (`cc-knowledge-api.py`) or the Drive home.
5. Ask mode-appropriate questions:
   - Main focus, key meetings, blockers
6. Record to the CC `daily_log` (append morning section) with frontmatter
7. **Suggest** 1-3 focus actions based on energy and deadlines; create CC tasks only if Pete asks.

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
7. Plan + **propose** top 3 priorities for next week; create CC tasks only if Pete asks
8. Archive completed items if appropriate

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

When Pete shares reusable content, route per `[[vault-routing#master-routing-matrix]]`. Processes / SOPs / API-config docs stay in `Library/processes/` (the surviving skeleton); everything else — competitor intel, market research, templates, decisions, IP/trademark — is **knowledge → ingest a `.md` to `vault_notes`** (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-ingest.py <file>`; the hourly embedder re-indexes it automatically, or run `cc-embedder.py` to index it now). Add `tags:`; report what was saved and where.

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

Meeting notes are **knowledge → `vault_notes`**: write the note (`type: meeting`, a `meeting_type` tag — standup / client-call / one-on-one / board-review / all-hands / cross-team / general — + entity tags) and ingest it (`cc-knowledge-ingest.py`; the hourly embedder re-indexes it automatically, or run `cc-embedder.py` to index it now). Client-call notes wikilink to the customer's CC record.

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

### Step 5: Propose CC Tasks from Action Items (create only if Pete asks)

**Propose** a task for each action item assigned to Pete (name + suggested priority/project) in your summary; **create them in `public.tasks` only if Pete asks**. (Action items owned by Jane go to her own queue, not here.)

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

Any cron change (create / edit / pause / decommission, any runtime) must run the dashboard 3-step: update `automations.json` → re-embed `index.html` → `deploy.py`.

## General Guidelines

- **The cloud map is your index.** The `config.map-md` orientation doc + the generated `cc_map` (the `/m/map` page) tell you what exists and where; the `drive_files` + `vault_notes` indexes are the live "what's where" lookups. Search them before creating anything.
- **A PLAN IS NEVER THE LIVE STATE — never trust a grepped plan for what's built.** Plans (`type=plan` in `vault_notes`) are *intent / history*; every one carries a `<!-- PLAN-LIFECYCLE-BANNER -->` saying so. NEVER answer "is X built / what exists / where does it live / what's the current state" from a plan you found by grep — its status label and prose are point-in-time and routinely stale (a plan reads "ready" weeks after it shipped, or describes a design later changed). The live state lives ONLY in the live system: the **orientation map** (`config.map-md`, rendered live twice daily) + the **live tables** (`cc-sql` over `modules` / `crons` / `vault_notes` / `data_map` / …) + the **`/m/map`** page. If a plan asserts a state, **verify it live before trusting or reporting it.** (Pete, 28 Jun 2026 — recurring failure: greps a plan, assumes its live state.)
- **Memory protocol**: Load the cached `CLAUDE` + `MAP` at session start. Route new knowledge per `[[vault-routing]]`.
- **Sweep verb**: `sweep` is a single deliberate verb, manual trigger only. No skill should auto-offer it. Email-workflow operating manual: `[[email-workflow]]`.
- **Email-workflow verbs**: `triage`, `sync`, `hand to`, `reply` (tray: Replies label, **no task**), `task` (CC task: no Replies label, `[no-sync-close]`), `reply + task` (the combo — a Reply with a prep task), `replies` / `my replies` (tray walker; legacy `actions`), `de-tray this`, `file`, `file all emails`, `add to calendar`. One-sentence rule: **Replies = waiting on Pete to respond by email; a task only when work is required** (decoupled 2026-06-25). See `[[email-workflow]]` -- handled by `inbox-triage` and `email-task-sync` skills.
- **Wikilinks everywhere**: Every mention of a project, person, or knowledge note MUST be a `[[wikilink]]`.
- **Teaching loop**: When corrected, save the correction. One-liner sticky rules -> CLAUDE.md. Structured rules (rule + Why + How) -> a `vault_notes` lesson (`type: lesson`) + one-line pointer in CLAUDE.md. Don't ask. **The pointer is for Pete-corrections only -- see the full Teaching Loop section above.**
- **Lessons**: lessons live in `vault_notes` (`type: lesson`) — behavioural rules with Why/How structure. Sessions can also write a lesson when a non-correction insight emerges that future sessions should know — those carry NO pointer in CLAUDE.md; knowledge-DB search (`cc-knowledge-api.py`) is their discovery surface.
- **Outbound text drafting**: read `[[voice-principles]]` before drafting any outbound text on Pete's behalf (customer email, supplier email, internal email, blog, article, ad copy).
- **Finance / invoicing / Soldo / Dext / Odoo / Xero / payroll / VAT**: read `[[finance-workflow]]` first.
- **Helper scripts**: in GitHub `pete-brain-scripts`, pulled to `/tmp/pbs` by the boot kernel — run `VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py`. Don't reinvent; check the CC Helpers registry (`/m/process-library`).
- **Connectors and APIs**: registry at `[[connections]]` -- don't guess what's connected or how it auths. **Any new/changed/expanded/rotated/retired external access (API key, MCP connector, OAuth, service account) → run the `connection-updater` skill** (secret → `public.secrets` pointer-only, registry row, config note, helper gate, `connection-parity.py` gate). Never store a key ad hoc.
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
| `here's the API key/token`, "I've connected X", "store/update this connection", key rotation, scope expansion, a new MCP connector, retiring/migrating a service | `connection-updater` |

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
- **Append a changed fact as a new line while leaving the old value in the note.** State a volatile fact (number / date / price / status) ONCE: replace it in place and remove the superseded line. FTS + the 24/7 bot rank on the whole note and aren't position-aware, so any dead value left in the body can be surfaced as current (the stale-cabin-"TBC" bug). See the volatile-fact rule in [[vault-writer]].

## Pete's Preferences for Written Content

- Human, natural tone (not corporate, not AI-sounding)
- British English spelling
- No unnecessary jargon
- **Outbound text** (email, article, blog, ad copy, customer reply, anything sent to a recipient): read `[[voice-principles]]` first. The dash rule, voice patterns, and AI-tells live there. Internal vault content (md files, plans, daily notes, audits) is not subject to those rules.

## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill:

- [[2026-05-24-mirror-source-system-dating-not-a-cleverer-model]] — for any data Pete also reads in a source app (Garmin, bank, console), match that app's own dating/labels; don't invent a smarter scheme that contradicts their screen.
- [[2026-05-24-gcal-updated-timestamp-race-after-create]] — calendar/sync race-condition pattern; Resume reads gcal-twice-daily-sync output.
- [[2026-05-25-calendar-sync-window-mismatch-births-past-event-dupes]] — Resume reads gcal cron line; understand window-mismatch failure mode.
- Resume Step 2 reads Garmin pull line; PUSH FAILED warning is part of that line.
- IP enforcement methodology; fires when brain handles IP-portfolio / takedown tasks.
- [[2026-05-26-enforcement-campaigns-surface-counter-attack-vectors-at-planning]] — surface counter-attack + personal-liability vectors at planning; fires on any IP / regulatory / public-callout campaign brain orchestrates.
- deck/document image-placement work; build from pristine backup, match each logo to its slide background.


## Tasks ↔ project backlog (operating model, 28 Jun 2026)
Canonical rule: [[ways-of-working-tasks-vs-backlog]]; gate lives at the top of [[vault-routing#task-routing-decision-tree]].
- **SUGGEST, never auto-create.** No explicit verb → propose "task (P+date) or park to {project} backlog?" and wait.
- Verbs literal: word "backlog" → backlog; word "task" → task.
- **Park to {project}** = `VAULT=/tmp/pbs python3 /tmp/pbs/cc-park.py park --task <id> --project <slug> --section "<S>"` (appends to the project's `{slug}-backlog` note, deletes the task, keeps ONE P4 pointer `Work through {Project} backlog`). Complete = `cc-park.py done`; promote back = `cc-park.py promote`.
- **General** is now ONE entity-agnostic project (the per-entity Team/PA/CD/SY/AT-General were consolidated). Tasks keep their own `entity_slug`. The Delegated track lives under `General`.
