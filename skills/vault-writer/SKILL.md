---
name: vault-writer
description: >
  The standard way to persist what a session produced to the Command Centre — knowledge / lessons /
  decisions / notes → `vault_notes`, files → Google Drive, tasks → `public.tasks`, the session log →
  `daily_log`. Use whenever a session produces knowledge, research, decisions, or project updates that
  should outlast the conversation. Triggers include: "save this to the brain", "log this",
  end-of-session wrap-ups, or any time meaningful work has been done that future sessions would benefit
  from. Handles discovery (finding where things already live in the cloud), routing (the right cloud
  home), formatting, and verification (confirming what was saved).
---

<!-- drive-cloudstorage-allowed: this skill writes directly to the personal & family Drive homes via Desktop Commander. See [[external-service-routing]] for the marker convention. -->
<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Sheets / Docs / Xero / Odoo / GSC / GA4 / Vision / Geocoding / Sentry operation in this skill, see [[external-service-routing]]. Helper-first. -->


# Vault Writer

> [!important] Where things live (route per [[vault-routing]])
> Files → **Google Drive** (`drive_files` index via `/tmp/pbs/cc-sql.py`). Knowledge / lessons / decisions / notes → **CC `vault_notes`** (SAVE with **`cc-save.py`**; SEARCH/read with `cc-knowledge-api.py`; surfaced in the CC Brain page). Tasks → **`public.tasks`**. Session log → **`daily_log`**.

End-of-session cleanup checklist and vault writing standards. Routing rules live in [[vault-routing]] — single source of truth for where things go. Brain owns workflow orchestration; vault-writer ensures end-of-session capture follows those rules.

The golden rule: **search first, then write**.

> **Routing source of truth**: [[vault-routing]]. End-of-session check ensures structural changes propagate there before signing off.
>
> **Gmail-side rules**: [[gmail-label-scheme]] — patterns + categorisation rules + colour palette. Skills query Gmail live; this file should NEVER ask Pete to write a parallel label registry.
>
> **Version history**: [[CHANGELOG]].


> **This skill is fully self-contained for end-of-session. Do not skip steps assuming the brain skill's Compress function handled it.**

---

## Where things go (cloud)

Save to the cloud homes (full matrix: [[vault-routing]]):
- **Knowledge / decisions / notes / research** → CC `vault_notes`: write a `.md` to `/tmp`, then `VAULT=/tmp/pbs python3 /tmp/pbs/cc-save.py <file>`. **Use `cc-save.py`, not `cc-knowledge-ingest.py`, as the default single-file save** — `cc-save.py` always persists (including lifecycle notes such as session-plans, which the bulk ingest deliberately skips and would silently drop — F3). `cc-knowledge-ingest.py` remains the BULK/directory ingest for general knowledge. The hourly embedder re-indexes changed notes automatically (content-hash detection) — run `cc-embedder.py` to index it immediately.
- **Files / documents / data** → the entity's **Google Drive** folder (find it: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT drive,path FROM drive_files WHERE …"`).
- **Live work** → the CC `tasks` engine (`public.tasks`). **Session log** → CC `daily_log`.
- **Tools** pull from GitHub to `/tmp/pbs`; run `VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py`.

---

## Search Before You Write

Before creating any file or adding any section, search for existing content.

### How to search

Query `vault_notes` (`cc-knowledge-api.py` — full-text + semantic) and the `drive_files` index (`cc-sql.py`) first.

### Where to search?

See `[[vault-routing#master-routing-matrix]]`. That table is the canonical map of every content type to its destination. Always check there before writing -- the master matrix is owned by vault-routing.md and is not duplicated here.

---

## End-of-Session Checklist

Run this at the end of every working session — **and run the lifecycle steps (Step 1 plan-sweep + the Step 2 note-fencing rule) ALSO on a mid-session context-switch**, when Pete pivots to a different project/app. Do not skip steps assuming the brain skill's Compress ran -- it often doesn't during heavy sessions, and a context-switch may never reach an end-of-session at all.

### Step 1: Project tidy-up

- Update the project's CC record (status, next steps, any new context learned this session)
- Consolidate working files in the project's Drive folder -- merge scratch notes or drafts that are now superseded
- **Plan lifecycle (keep plans honest — a shipped plan must not look live, a dead one must not look active):** plans carry an auto-stamped `<!-- PLAN-LIFECYCLE-BANNER -->` driven by frontmatter `status`. **Sweep every plan you touched OR that relates to work done this session — not only ones you think changed**, and verify each against the live system (does the thing it planned now exist? is its task done?) before leaving it `ready`/`in-progress`. Stamp the shipped ones and **re-save with `cc-save.py`** so the banner re-stamps (a session-plan would be SKIPPED by `cc-knowledge-ingest.py`, so re-ingest silently does nothing — F3) — even if an earlier session did the actual shipping (a plan left `in-progress` is exactly what the next Resume mis-reads as live):
  - **Executed / shipped** → `status: completed` (banner → ✅ historical). Never leave a shipped plan `ready`/`in-progress` — that's what makes a future grep treat it as live state.
  - **Scrapped / abandoned** → EITHER **hard-delete the plan note** (snapshot first; the default for pure noise) **OR** set `status: scrapped` (banner → ⛔ "do not use"). Delete if it should leave no trace; stamp if the *decision not to do it* is worth keeping.
- Don't delete old *knowledge/files* on a whim -- keep history; but a scrapped *plan* should be deleted or clearly stamped dead (above), never left looking active

### Step 2: Whole-session reflection

> [!important] First — the structured-home sweep (whole session, not just website work)
> List **every distinct topic, entity, project, property, or piece of work** touched this session. For each, find its home and update it with what changed **+ the rationale**. Find homes by querying the cloud, never a local tree: knowledge → `vault_notes` (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py`); files/entities → the `drive_files` index (`cc-sql.py`). State of play lives in the entity's note / Drive folder, not the daily log. Generalised from the website lesson (in `vault_notes`).
>
> **Fence state-bearing notes like plans.** Any note that records *current state* (what's built / broken / pending, balances, config, "as of now") gets the note-equivalent of the plan banner: set `status: snapshot` + `as_of: YYYY-MM-DD` in frontmatter, AND open the state block with `> [!warning] Point-in-time snapshot as of {date} — VERIFY live before treating as current.` A state note with no fence is read by a future session as live truth — the same failure as an unstamped plan. If it's now obsolete, hard-delete (snapshot first) or set `status: superseded`; never leave a stale state note looking current.
>
> **Volatile-fact rule — state a changing fact ONCE.** When you update a note with a changed fact (a number, date, price, cabin, status, address), **REPLACE the old value in place and remove any earlier line that still states the superseded value** — never append a second line that contradicts an existing one. Both the brain's full-text search and the 24/7 bot rank on the whole note body and are *not* position-aware, so a dead value left anywhere in the note can be surfaced as current (this is exactly the bug that had the bot quoting a stale cabin "TBC" while the confirmed number sat lower down). If in doubt, grep the note for the old value before saving and confirm it's gone.

Then scan everything discussed this session and route each category to its cloud home (full matrix: [[vault-routing]]):

- **Properties / property data** (hosting, stack, IDs, SEO, Surfer scores, audits) → the property's record in the **CC Properties module** + data files to its **Drive** folder.
- **Customers / Suppliers** (new relationship, matter, contract, status) → the entity's **Drive** folder + a `vault_notes` record. For account-customers (e.g. Clancy) the CC `account_*` store is the live home.
- **People / Businesses / operational areas** (roles, KPIs, SOPs, finance/insurance/vehicles) → the relevant **Drive** home + `vault_notes`. `vault-enricher.py` (called by triage/sync) still auto-pulls attachments — keep calling it.
- **Personal / Family** → the **My Drive** / **Ashcroft Family** Drive home — write **direct to Drive** via Desktop Commander. TitleCase folder names in the family Drive.
- **Sygma owner-private** (salaries, payroll, pay-sensitive) → **Drive `Sygma Private/`** direct (never a shared/operational Sygma drive). [[shared-drives#sygma-solutions-private]]. Monthly payroll: [[file-wages-email]].
- **Decisions / competitive intel / lessons / processes / connectors** → CC `vault_notes` (ingest a `.md`); a new connection → the connections registry ([[connections]]).
- **Gmail labels** → Gmail is source of truth; only update `[[gmail-label-scheme]]` for a NEW category/colour/mode/rule.
- **CLAUDE.md** → only on an explicit Pete correction he asks to be saved; structured rules → `vault_notes`.
- **Vault-routing capture** → a new convention must be reflected in `[[vault-routing]]` (ingest the update) AND notify Pete.

### Step 3: CC task sync (PROPOSE follow-ups; create only if Pete asks)

This step defers to the brain skill's Compress Step 4, the canonical authority on session-end task sync. Brain owns workflow orchestration; vault-writer follows the same model. **His tasks live in the CC `public.tasks` table**.

- Pull current `public.tasks` state for the relevant projects -- verify the CC reflects reality
- Identify follow-up actions from the session (what's pending, what surfaced, what needs watching)
- **PROPOSE the follow-ups -- do NOT auto-create them.** List them in the Step 6 report as a short *suggested* set (name + suggested priority/project). **Create in `public.tasks` ONLY when Pete explicitly asks** -- then `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "INSERT INTO tasks (id,name,priority,base_priority,due_on,entity_slug,project_slug,status,source) VALUES (gen_random_uuid(),'<name>','<P1|P2|P3|P4 — undated>','<same P-tier>',NULL,'<entity>','<project_slug>','todo','claude')"` (the date is the switch — leave `due_on` NULL; a date auto-makes a PD, confirm any date with Pete). Never auto-insert -- auto-created follow-ups pile up as clutter (Pete, 28 Jun 2026).
- Mark any completed tasks as done: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "UPDATE tasks SET status='done', completed_at=now() WHERE id='<task-id>'"`
- Report what was created in Step 6

If a follow-up is genuinely judgement-call (e.g. "should we even do this?"), surface it as a *question* in Step 6 instead of creating a task. Default for clear actions: just create the task.

#### Step 3a: Same-day reconciliation (NEW v5.1, 2026-05-04)

Before sign-off, re-read **every prior `> [!todo] Pending Tasks` block in today's daily note** plus any pending entries in same-day session plans. Cross-reconcile open `[ ]` items against later session logs / commits / READMEs / decision docs from today. For each open task with positive evidence of having shipped:

- If it has a `(CC: <task-id>)` reference, query the CC task store live (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT status FROM tasks WHERE id='<task-id>'"`). If still `todo` and the work has demonstrably shipped (commit hash matches a same-day session log, README "recent commits" line names it, decision doc records the rollout, etc.): **close the CC task** (`UPDATE tasks SET status='done', completed_at=now() WHERE id='<task-id>'`) and record the evidence in the task notes, and **replace the daily-note `[ ]` line in-place** with `[x]` + ~~strikethrough~~ + a `**SHIPPED same-day as <evidence>**` marker.
- If no CC task-id, grep today's daily note for the task's keywords. Same in-place strikethrough + evidence marker if matched.
- When uncertain, ask Pete (`"Looks like X may have shipped via commit Y -- close the task?"`) instead of auto-modifying.

**Close-on-ship (mechanical — the durable fix for "shipped it, never closed it"):** for every discrete thing this session actually shipped — a commit, a cron added to the registry, a file created, an email sent that names a task — run `VAULT=/tmp/pbs python3 /tmp/pbs/email-task-reconcile.py --ship <task-id|keyword>…`. It searches the **full open CC task list** (`public.tasks`, not just today's TODO block) for the matching task(s) and lists them; after eyeballing the match, re-run with `--apply` to close them (`status='done'`) with an audit note. This catches the common miss the same-day check is blind to: a task opened in an *earlier* session whose work lands today. Same discipline — lists first, you confirm, never closes on assumption.

**Log-on-ship (the [[work-log]] peer of close-on-ship):** in the *same* pass over what shipped this session, write a Work Log row for each discrete ship that touched a website property or the CC platform -- `VAULT=/tmp/pbs python3 /tmp/pbs/worklog.py --property "<name>" --area <seo|content|dev|design|ops|...> --title "<what shipped>" --evidence "<before->after>" --outcome <worked|too-early|unknown> --link <commit/PR/doc> --source-ref "git:<owner>/<repo>@<sha>"`. One row per shipped thing, idempotent on `--source-ref`, evidence+outcome required for seo/dev/ads. property-manager's Step 6f² already logs code commits *as they land*; this end-of-session sweep is the safety net for anything that shipped outside that path (a non-property script, a config change, a published report). Skip pure-knowledge / triage / health sessions with no shippable artefact. The log is the cross-property "what did we do / did it work" index surfaced at /m/work-log.

> [!important] The recall-based row above is the human layer; it does NOT catch a commit you FORGOT. For any session that committed code, ALSO run the deterministic, ownership-gated gate -- `VAULT=/tmp/pbs python3 /tmp/pbs/closeout-sweep.py --apply` -- the SAME gate brain Compress Step 7c and the `closeout` skill use. It reconciles every checkout you touched against `work_log.source_ref` and logs the unlogged commits it can prove THIS session made (from the transcript's `gitOperation.commit.sha` via `session_attribution.py`), while surfacing (never logging) commits that belong to other live sessions. This closes the "shipped a raw commit, never logged it" gap that pure recall leaves open, and keeps all three end-of-session skills consistent. For a property-touching session, the `closeout` skill is the fuller end-of-session command (adds live-deploy + SEO + registration checks); vault-writer is the general knowledge/file/task capture. Whichever runs, run `closeout-sweep.py --apply` so no commit slips through. **Runtime note:** the gate proves ownership from the Claude Code interactive transcript (the `gitOperation` stamps), so it only works in a Claude Code session. In a runtime without that transcript (e.g. Cowork) it **exits 3 and prints "OWNERSHIP UNVERIFIABLE" rather than a false all-clear** — there, fall back to the recall-based row above and log each commit by hand. Never read `REMAINING: 0` as clean without checking the exit code was 0 (2 = a commit still unlogged, 3 = could not verify).

**Make the close findable (so the safety nets work):** two cheap conventions help a missed close get caught at sign-off:
- **Code:** put the CC task id in the commit message when a commit completes a task (e.g. `… (task 727634d8…)`).
- **Non-code ships you can't close immediately** (a cron, an email, a file): drop a `SHIPPED: <task-id> — <evidence>` line in today's daily note. The reconciler treats that explicit marker as closeable.
Neither replaces actually closing the task — they're what make a *missed* close get caught instead of rotting.

**Why:** before this step, each session log's pending-task block was treated as final. A morning session opens "Wire X" + creates a task; a 12:30 detour ships X as commit ABC; end-of-day vault-writer never re-read the morning's TODO block. the task sat closed-but-still-open; daily note still claimed `[ ] Wire X`. Pete spots it the next morning, vault loses credibility. Surfaced 2026-05-04 via the `x_studio_report_link` writeback (a task, shipped as `ba02060`).

**How to apply:** Runs end-of-session, before Verification. Cheap because today's daily note is small. Touches only TODO lines that have positive evidence -- never strikes a line on assumption alone. Mirror logic also lives in brain Compress Step 7 (canonical orchestrator); vault-writer's copy is the cleanup-checklist guarantee that it actually runs.

#### Step 3b: Stale-task review (simplified — old evidence engine retired)

Pete's task list drifts messy over time. At session end, surface a short digest of stale work in Step 6. **Surface-only — never bulk-close, delete, reassign, or re-section without Pete's explicit per-item confirmation** (CC tasks are Pete's).

Stale-task review is a light query against `public.tasks`, surfaced for Pete's per-item call:

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT id, name, priority, project_slug, due_on FROM tasks WHERE status!='done' AND priority='PD' AND due_on < current_date - interval '30 days' ORDER BY due_on LIMIT 30"
```

**Digest (in Step 6):** one line per stale task (name + priority + how overdue). If the list is empty, say so in one line — don't manufacture noise. Pete confirms any close per item; never auto-close. (A richer cloud evidence engine — `daily_log` + the GitHub API for commits + `public.crons` — can be rebuilt later if this manual pass proves too coarse.)

**Why:** end-of-session is the natural moment to keep the work brain clean. Surfaced 2026-05-20 when SY-Website carried 80 open tasks — 42 stale Jane backlink tasks from 6 May (since emailed to move to her own project) + ~6 April-dated SEO monitoring tasks long since done. Mirror in brain Compress so it runs whichever skill closes the session.

#### Step 3c: CC task-state parity

For every project **touched this session**, confirm `public.tasks` reflects what shipped — the task is closed (overlaps Step 3a); conversely a task marked done in the CC has its artefact updated in the cloud (Drive / `vault_notes` / CC). **Don't sprawl**: never create a new project/sub-project for 1-2 tasks; default to the single `General`; ask Pete before creating either. (The old task↔vault-folder parity check is retired — there are no vault project folders.)

#### Step 3d: Connection parity

If this session touched any external access — a new/rotated/expanded/retired API key, MCP connector, OAuth app, or service account — run the **`connection-updater`** skill for each (it stores the secret pointer-only in `public.secrets`, registers the connection, and its gate `VAULT=/tmp/pbs python3 /tmp/pbs/connection-parity.py` must print `0 gaps`). If unsure whether the session touched a connection, run the bare parity gate anyway — it's read-only and fast. The weekly `drift-check` cron is the backstop, but same-session is cleaner.

#### Step 3e: Enquiry-Engine capture + sign-off (mirrors `closeout` check I3)

If this session sent any training-enquiry replies, they MUST be captured and reconciled before close — the same gate the `closeout` skill runs, so whichever end-of-session skill fires, the EE never closes half-done:
- **Every reply captured** via `te-log --apply` (CRM + knowledge + chase + de-tray). A reply Pete sent by hand from Gmail still needs logging — capture it with its `thread_id`.
- **EE sign-off clean:** `VAULT=/tmp/pbs python3 /tmp/pbs/ee-signoff.py --since today` must exit 0 — no source-bearing edit left with `source_fixed IS NOT TRUE`, no `reply`/`quote` with a dropped `draft_text`, no duplicate chases. `handoff`/`chase`/`note`/`correction` are draftless and exempt. If it exits non-zero, close each named source and re-run to zero. Full contract: [[workflow-design]] §6.10.

### Step 4: Housekeeping

- Wikilinks used for all references in files written this session
- Frontmatter on all new files (minimum: type, date)
- No orphan notes -- every new file linked from at least one existing file

### Step 5: Verification

- Re-read every file that was created or modified during the session
- Confirm content actually landed (don't just claim it's done)
- If anything is wrong or missing, fix it before moving on

### Step 6: Report to Pete

- Present a summary: what was saved, where, what was updated, what CC tasks were created
- Flag anything that needs Pete's attention next session
- Pete may request changes, additions, or notes at this point -- handle them before continuing

### Step 7: Daily log (CC)

- Append this session's entry to the CC `daily_log` (`date`=today, `cron_name`=`'session'`, `content`=summary) via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py` (dollar-quote the content). This is the cloud last-session that brain Resume reads — don't skip it.
- Include: what was worked on, what was saved, decisions made, what's pending
- Use `[[wikilinks]]` for project and person references
- Include any late additions from Pete's feedback in Step 6

### Step 8: Propagate to the cloud

The map is **auto-generated** — `cc_map` (the `/m/map` page) from the `modules` table, and the `config.map-md` orientation doc rendered twice daily by `cc-orientation-map-sync.py` from the live tables (counts + the `data_map` routing) — nothing to maintain by hand. Knowledge reaches the cloud when you **save it to `vault_notes`** (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-save.py <file>` — the default single-file save that never drops a lifecycle note; `cc-knowledge-ingest.py` is the bulk/directory ingest for general knowledge; the hourly embedder re-indexes it automatically, or run `cc-embedder.py` to index it now); files reach the cloud by living in their **Drive** folder (captured automatically by the `drive-changes-watch` Railway cron). Confirm each thing you saved this session has landed in its cloud home before sign-off.

---

## Formatting Standards

### YAML Frontmatter

```yaml
---
type: project | meeting | decision | sop | market-intel-report | ip-portfolio-report | ...
date: YYYY-MM-DD
department: sygma-solutions | sygma-training | sygma-gpr | canary-detect | one-system | el-atico
project: Project-Name
status: active | planning | on-hold | in-progress | ready | completed | scrapped | snapshot | superseded
as_of: YYYY-MM-DD          # REQUIRED when status: snapshot — the date the state was true
tags: [relevant, tags]
---
```

### Wikilinks

Every mention of a project, person, or vault note MUST be a `[[wikilink]]`. Weave them into sentences naturally.

Wrong: `The Google Ads account was fixed. Related: [[SY-Website/ads]]`
Right: `The [[SY-Website/ads]] account issues from 12 March have been resolved.`

### Markdown formatting

- **Callouts**: `> [!type] Title` -- use `important` for decisions, `todo` for action items, `tip` for wins, `warning` for blockers
- **Highlights**: `==critical text==` (sparingly)
- **Comments**: `%%internal note%%` (hidden in preview)
- **No H1 heading** that duplicates the filename

### File Naming

- kebab-case: `sygma-google-ads.md`
- Always `.md`, never `.txt`
- Date-prefixed: `YYYY-MM-DD-{name}.md`
- Match existing patterns in the directory

---

## Automated Task Awareness

Several scheduled crons write to the CC (`daily_log`, `garmin_daily`, registries). Re-read the relevant CC table before editing if time has passed — an automated run may have written to it.

**Do not embed cron lists in this skill — they drift.** The single sources of truth (locked 2026-06-06 after embedded copies went stale):

- `[[cron-registry]]` — the canonical narrative registry (per cron: what / why / schedule / dependencies).
- CC `public.crons` → live view at **commandcentre.info/m/automations-log** (reads it LIVE).
- `cc-cron.py list` — live Railway cron state; each script's `# CRON-META` header is the schedule source.

Any cron change this skill's session touches (create / edit / pause / decommission, any runtime) is made with **`cc-cron.py`** — it writes Railway + `public.crons` and the dashboard updates instantly. There is NO manual sync: the old `automations.json → index.html → deploy.py` 3-step and `pete-automations.vercel.app` are RETIRED.

All crons run on **Railway**. The source of truth is the live `crons` table in the CC + the `# CRON-META` blocks inside each `.py` in `pete-brain-scripts`; manage them with `cc-cron.py`.

---

## Web Mode (No File Access)

When running on claude.ai web without file access, produce vault-ready markdown blocks Pete can paste directly:

```markdown
%% Route: ingest to CC vault_notes, tagged CD-Website (knowledge) — or the property's Google Drive folder if it's a file %%
---
type: research
date: 2026-05-06
project: CD-Website
bucket: SEO
department: canary-detect
tags: [seo, research, CD-Website]
---

Content here with [[wikilinks]] to projects and people.
```

Always include: the `%% Save to: path %%` comment, complete frontmatter, wikilinks. Provide a summary of all files to create/update at the end.

---

## Should I Save This?

**Save it if:**
- Decision with reasoning
- New info about a project, department, team, or competitor
- Research that took effort
- Correction to existing vault content
- Process or SOP
- Contact info, tool config, vendor details
- Another session would benefit from knowing this

**Skip if:**
- Casual chat with no actionable content
- One-off factual question
- Duplicates existing content with nothing new
- Draft content Pete said to discard

When in doubt, save it.

---

## What NOT to Do

- Create `.txt` files -- everything is `.md` with frontmatter
- Skip the search -- always check if content exists before creating
- Append blindly to end of file -- find the right section
- Create orphan notes -- link from at least one existing file
- Write to a local file tree — knowledge → `vault_notes`, files → Drive
- Put operational data (keyword trackers, Surfer scores) anywhere but the property's Drive folder + its CC record
- Create tasks unprompted -- propose them, create only when Pete asks; and never anywhere other than `public.tasks`
- Skip the daily note after meaningful work

## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill:

- [[2026-05-03-header-name-lookups-for-resilient-scripts]]
- [[2026-05-05-sheet-migration-via-values-update-is-wrong]]


## Tasks ↔ project backlog (operating model, 28 Jun 2026)
Canonical rule: [[ways-of-working-tasks-vs-backlog]]; gate lives at the top of [[vault-routing#task-routing-decision-tree]].
- **SUGGEST, never auto-create.** No explicit verb → propose "task (P+date) or park to {project} backlog?" and wait.
- Verbs literal: word "backlog" → backlog; word "task" → task.
- **Park to {project}** = `VAULT=/tmp/pbs python3 /tmp/pbs/cc-park.py park --task <id> --project <slug> --section "<S>"` (appends to the project's `{slug}-backlog` note, deletes the task, keeps ONE P4 pointer `Work through {Project} backlog`). Complete = `cc-park.py done`; promote back = `cc-park.py promote`.
- **General** is now ONE entity-agnostic project (the per-entity Team/PA/CD/SY/AT-General were consolidated). Tasks keep their own `entity_slug`. The Delegated track lives under `General`.
