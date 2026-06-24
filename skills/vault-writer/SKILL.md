---
name: vault-writer
description: >
  The standard way to save information to Pete's Second Brain (Obsidian vault). Use this skill
  whenever a session produces knowledge, research, decisions, project updates, or any information
  that should persist beyond the current conversation. Triggers include: "save this to the brain",
  "update the vault", "log this", "add this to Second Brain", end-of-session wrap-ups, or any
  time meaningful work has been done that future sessions would benefit from knowing about. Also
  use proactively at the END of any working session -- if useful information was produced, it
  belongs in the vault. This skill handles discovery (finding where things already live), routing
  (putting info in the right place), formatting (frontmatter, wikilinks, Obsidian markdown), and
  verification (confirming what was saved). It also maintains the Vault Map so every session can
  quickly understand what exists.
---

<!-- drive-cloudstorage-allowed: this skill documents the DC-required filesystem-shape sync pattern for the vault-drive-sync step. See [[external-service-routing]] for the marker convention. -->
<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Asana / Sheets / Docs / Xero / Odoo / GSC / GA4 / Vision / Geocoding / Sentry operation in this skill, see [[external-service-routing]]. Helper-first. -->


# Vault Writer

> [!important] Business OS migration — content lives in Drive + the knowledge DB, not the vault tree
> Files → **Google Drive** (find via the `drive_files` index: `Library/processes/scripts/cc-sql.py`); knowledge / lessons / decisions / notes → **CC `vault_notes`** (`cc-knowledge-api.py`, surfaced in the CC Brain page). The vault content folders (`Properties/`, `Customers/`, `Suppliers/`, `Businesses/`, `Personal/`, `Accreditations/`, most of `Library/`) are **legacy mirrors pending retirement at Part I** — route new content per the new-world matrix in [[vault-routing]]. `[[wikilinks]]` still resolve (by name, against `vault_notes`), so don't rewrite links. Full picture: `MAP.md`. State: [[Projects/PA-Command-Centre/files/business-os-master-plan-2026-06-20|master plan]].

End-of-session cleanup checklist and vault writing standards. Routing rules live in [[vault-routing]] — single source of truth for where things go. Brain owns workflow orchestration; vault-writer ensures end-of-session capture follows those rules.

The golden rule: **search first, then write**.

> **Routing source of truth**: [[vault-routing]]. End-of-session check ensures structural changes propagate there before signing off.
>
> **Gmail-side rules**: [[gmail-label-scheme]] — patterns + categorisation rules + colour palette. Skills query Gmail live; this file should NEVER ask Pete to write a parallel label registry.
>
> **Version history**: [[CHANGELOG]].


> **This skill is fully self-contained for end-of-session. Do not skip steps assuming the brain skill's Compress function handled it.**

---

## Vault Access

All paths are vault-relative. The vault is the working directory.

Example paths:
- `Properties/Sygma Solutions Website/data/keyword-tracker-v5.md`
- `Projects/CD-Website/seo/files/session-plan-2026-05-06.md` (sub-project files: parent/sub-project/files/)
- `Projects/CD-Website/files/main-site-overview.md` (parent's own files)
- `Daily/2026-05-06.md`

---

## Search Before You Write

Before creating any file or adding any section, search for existing content.

### How to search

Use the Grep tool to scan file contents. Use the Read tool to check specific files. Check MAP.md first.

### Where to search?

See `[[vault-routing#master-routing-matrix]]`. That table is the canonical map of every content type to its destination. Always check there before writing -- the master matrix is owned by vault-routing.md and is not duplicated here.

---

## End-of-Session Checklist

Run this at the end of every working session. Do not skip steps assuming the brain skill's Compress ran -- it often doesn't during heavy sessions.

### Step 1: Project tidy-up

- Update the project README (status, next steps, any new context learned this session)
- Consolidate working files in `files/` -- merge scratch notes or drafts that are now superseded
- Mark completed session plans as `status: completed`
- Don't delete old files -- keep history, just mark things as done

### Step 2: Vault-wide reflection

> [!important] First — the structured-home sweep (whole session, not just website work)
> Before the category checklist below, list **every distinct topic, entity, project, property, or piece of work** touched this session. For **each one**, actively ask: **is there already a project on this? a property? a customer / supplier / business area? any folder or sub-folder relating to this topic?** Search MAP.md + the relevant top-level sections (Projects/, Properties/, Customers/, Suppliers/, Businesses/, Personal/, Library/) to find its home. If a home exists, **update it** with what changed and **the rationale / reasons** — that is where state of play lives for the next session. If a home *should* exist but doesn't, create it per the onboarding rituals (or ask Pete). The daily note + session log are **pointers only**; nothing of substance should end its life in a daily log, a lesson, or CLAUDE.md when it has a real home in the indexed vault. Generalised from [[Library/lessons/2026-05-20-website-work-saved-to-structured-vault]] (written for websites, applies to everything).

Then scan everything discussed this session and check each category. This is the critical step that prevents information loss:

- **Properties**: Hosting change? New domain? Tech stack update? New tracking IDs? Supabase change? -> Update `Properties/{Name}/README.md`
- **Property data**: SEO work done? Keyword research? Surfer scores? Audit results? -> Save to `Properties/{Name}/data/`
- **Customers**: New customer, new matter, contract update, complaint, relationship status change? -> Update `Customers/{prefix}-{slug}/` -- customer-level for profile/contract changes, matter folder for matter-specific thinking, `source/` for external artefacts.
- **Suppliers**: New supplier, contract change, matter update, pricing change, relationship status? -> Update `Suppliers/{prefix}-{slug}/` following the same routing as customers.
- **People**: Person info, new role, working style, contact detail? -> Update `Businesses/{name}/people/{person}.md` (or sub-business `people/{person}.md`)
- **Businesses**: KPI changes, charter updates, SOP changes? -> Update `Businesses/{name}/`
- **Businesses operational areas**: finance / insurance / vehicles / asset content for any business (CD, SY, EA)? -> Update `Businesses/{name}/finance/`, `/insurance/`, `/assets/{type}/` -- or rely on `vault-enricher.py` (called by triage / sync) which auto-pulls attachments to `source/` and body extracts to `extracts/` for these areas
- **Vendor capacity changes**: a supplier acting in a new capacity (e.g. MVP-Lanzarote also functioning as CD finance arm)? -> Update `Suppliers/{prefix}-{slug}/README.md` notes section AND ensure threads are routed by capacity (per Issue 17 in [[vault-routing#observed-patterns]])
- **Personal areas**: scouts event captured, los-claveles community decision, passion-fit content drafted, freemasonry summons received, personal finance update? -> Update `Personal/{area}/` -- READMEs at the area root, supporting content in sub-folders (events/, source/, etc.)
- **Family**: joint file added (vehicle, property, Spanish admin, travel cert, etc.)? -> Update `Personal/family/{Sub Area}/` (**TitleCase With Spaces**: Family Members/, HMRC Personal/, Spanish Admin/, Vehicles/, Legal/, Property/, Travel/, Health/). The hourly `vault-drive-sync` will push to Drive Pete & Mic / Ashcroft Family/. Don't create lowercase / kebab-case folders inside Personal/family/ -- they'll create case-duplicate conflicts in Drive.
- **Sygma owner-private**: private accounts, salaries, payroll, pay-sensitive personnel? -> **Write DIRECTLY to the Drive folder** `Pete & Mic / Sygma Solutions Private/` (via bash / Desktop Commander at the CloudStorage path). This is **Drive-direct, NOT mirrored** (changed 2026-06-03) — the vault has only a pointer at `Businesses/sygma-solutions/owner-private/README.md`; do NOT write content into that vault folder (it won't sync anywhere and just re-bloats a dead mirror). **Do NOT route to Sygma Hub or other shared Sygma drives.** Path index + structure: [[shared-drives#sygma-solutions-private]]. Monthly payroll: [[file-wages-email]].
- **Personal/inbox/ check**: anything dropped on My Drive landed in Personal/inbox/ via the pull-only sync? -> Triage now or flag for next session.
- **Gmail labels**: did session create / rename / remove a Gmail label? -> If new label fits an existing pattern (`Customers/{prefix}-{slug}`, etc.), NO doc update needed (Gmail is source of truth). Only update `[[gmail-label-scheme]]` if a NEW CATEGORY, COLOUR, MODE, or RULE was introduced. Never enumerate labels in markdown.
- **Vendors/tools**: New tools, vendors, frameworks, costs, templates? -> Update `Library/processes/vendors-and-tools.md` or create new library file
- **Processes**: Process changed or new one emerged? -> Update `Library/processes/`
- **Connectors/APIs**: New account-level token, connector added/swapped? -> Update `Library/processes/connections.md` and the relevant `Library/processes/{service}-api-configuration.md`
- **Decisions**: Significant decision made with reasoning? -> Create `Library/decisions/YYYY-MM-DD-{title}.md`
- **Competitive intel**: Competitor discussed? -> Update `Library/competitors/{name}.md`
- **CLAUDE.md**: Was Claude corrected? New rule needed? -> Add to Rules section
- **Vault-routing capture**: Did this session introduce a new convention, top-level section, file-naming rule, or routing decision? If yes, the change must already be reflected in `[[vault-routing]]`. Confirm before signing off. If not yet in vault-routing, append to its `## Learned decisions` section AND notify Pete.

### Step 3: Asana sync (auto-create, no asking)

This step defers to the brain skill's Compress Step 4, which is the canonical authority on session-end Asana sync. Brain owns workflow orchestration; vault-writer follows the same auto-create model.

- Pull current Asana state for relevant projects -- verify vault reflects reality
- Identify follow-up actions from the session (what's pending, what surfaced, what needs watching)
- **Auto-create those tasks in Asana** with correct project, assignee, priority (custom field), due date (P1+2d / P2+7d / P3+30d / P4 none), and section (To Do / Backlog as appropriate). No "shall I send these?" gate.
- Mark any completed tasks as done via `asana_update_task`
- Check if any new Asana projects need vault folders -- create them if missing
- Report what was created in Step 6 with task GIDs

If a follow-up is genuinely judgement-call (e.g. "should we even do this?"), surface it as a *question* in Step 6 instead of creating a task. Default for clear actions: just create the task.

#### Step 3a: Same-day reconciliation (NEW v5.1, 2026-05-04)

Before sign-off, re-read **every prior `> [!todo] Pending Tasks` block in today's daily note** plus any pending entries in same-day session plans. Cross-reconcile open `[ ]` items against later session logs / commits / READMEs / decision docs from today. For each open task with positive evidence of having shipped:

- If it has an `(Asana: <gid>)` reference, query Asana live (`asana_get_task`). If still open and the work has demonstrably shipped (commit hash matches a same-day session log, README "recent commits" line names it, decision doc records the rollout, etc.): **close the Asana task** with a comment naming the evidence, and **replace the daily-note `[ ]` line in-place** with `[x]` + ~~strikethrough~~ + a `**SHIPPED same-day as <evidence>**` marker.
- If no Asana GID, grep today's daily note for the task's keywords. Same in-place strikethrough + evidence marker if matched.
- When uncertain, ask Pete (`"Looks like X may have shipped via commit Y -- close the task?"`) instead of auto-modifying.

**Close-on-ship (mechanical — the durable fix for "shipped it, never closed it"):** for every discrete thing this session actually shipped — a commit, a cron added to the registry, a file created, an email sent that names a task — run `python3 Library/processes/scripts/asana-reconcile.py --ship <gid|keyword>…`. It searches the **full open Asana list** (not just today's TODO block) for the matching task(s) and lists them; after eyeballing the match, re-run with `--apply-auto` to close them with an audit comment. This catches the common miss the same-day check is blind to: a task opened in an *earlier* session whose work lands today. Same discipline — lists first, you confirm, never closes on assumption.

**Make the close findable (so the safety nets work):** two cheap conventions feed the `asana-ship-gate` Stop hook (deterministic, harness-run every turn — it won't let you sign off if you shipped a task-referenced thing that's still open):
- **Code:** put the Asana gid in the commit message when a commit completes a task (e.g. `… (Asana 1215631946311467)`).
- **Non-code ships you can't close immediately** (a cron, an email, a file): drop a `SHIPPED: <gid> — <evidence>` line in today's daily note. The reconciler treats that explicit marker as auto-closeable, and the hook catches it at sign-off.
Neither replaces actually closing the task — they're what make a *missed* close get caught instead of rotting. Full design: [[Library/decisions/2026-06-14-asana-reconciliation-system]].

**Why:** before this step, each session log's pending-task block was treated as final. A morning session opens "Wire X" + creates Asana 1234; a 12:30 detour ships X as commit ABC; end-of-day vault-writer never re-read the morning's TODO block. Asana sat with a closed-but-still-open task; daily note still claimed `[ ] Wire X`. Pete spots it the next morning, vault loses credibility. Surfaced 2026-05-04 via the `x_studio_report_link` writeback (Asana 1214496261050040, shipped as `ba02060`). See [[Library/lessons/2026-05-04-same-day-reconciliation-gap]].

**How to apply:** Runs end-of-session, before Verification. Cheap because today's daily note is small. Touches only TODO lines that have positive evidence -- never strikes a line on assumption alone. Mirror logic also lives in brain Compress Step 7 (canonical orchestrator); vault-writer's copy is the cleanup-checklist guarantee that it actually runs.

#### Step 3b: Asana staleness sweep (NEW v5.5, 2026-05-20)

Pete's projects drift messy over time. At session end, scan the whole workspace for stale work and surface a short digest in Step 6. **Surface-only — never bulk-close, delete, reassign, or re-section without Pete's explicit per-item confirmation** (Asana teams + tasks are sacred; never bulk-delete without confirmation).

**Mechanic — run the evidence reconciler, don't hand-roll it:**
- `python3 Library/processes/scripts/asana-reconcile.py --overdue-only` (or no flag for the full open set). It pulls Pete's open tasks (direct PAT) and buckets each by **completion evidence**, not just age:
  - **AUTO** — unambiguous mechanical proof (the gid is in a merged commit; a "build cron X" task whose named cron now exists in the registry). The only bucket eligible for unprompted close, via `--apply-auto`.
  - **PROPOSE** — strict, suggestive evidence (a non-list daily-note line records it done next to the gid; or, for a reply/chase task, Pete sent the last message on the linked thread). **Surface for Pete's one-word confirm — never auto-close.**
  - **PAYMENT** — "Pay X" payables; can't be verified here, always Pete's call.
  - **OPEN** — no trustworthy signal; silent unless >30 days overdue.
- The reconciler is deliberately **high-precision** (it once flagged 47/59 on a loose heuristic — status-dump resume lines leak completion words across task IDs; the strict version drops that to a handful). If it surfaces a lot, suspect a regex regression, not a real backlog.

**Digest (in Step 6):** surface the AUTO + PROPOSE + PAYMENT buckets (the OPEN >30d list is a quieter footnote). One line per task with its evidence + recommended action. If all buckets are empty, say so in one line — don't manufacture noise.

**Weekly safety net:** the full evidence sweep also runs every Sunday via the `asana-reconcile` cron, which emails Pete the pre-evidenced digest — so the backlog gets a regular pass even in weeks where no session triggers this step. (Registry: [[scheduled-tasks]].)

**Never auto-act beyond AUTO.** The only unprompted Asana mutations are: closing tasks *this* session demonstrably shipped (Step 3a / `--ship`), the reconciler's AUTO bucket (`--apply-auto`, mechanical proof only), and auto-creating clear follow-ups (Step 3). PROPOSE / PAYMENT / stale-item cleanup is always Pete's per-item call (Asana teams + tasks are sacred).

**Why:** end-of-session is the natural moment to keep the work brain clean. Surfaced 2026-05-20 when SY-Website carried 80 open tasks — 42 stale Jane backlink tasks from 6 May (since emailed to move to her own project) + ~6 April-dated SEO monitoring tasks long since done. Mirror in brain Compress so it runs whichever skill closes the session.

#### Step 3c: Asana ↔ vault project parity (NEW v5.6, 2026-05-20)

For every project **touched this session**, confirm the Asana project and its vault folder are in sync — run the **Full Sync Check Rules** in [[asana-configuration]] scoped to those projects:

- **Folder + README + files/**: the vault project folder exists with `README.md` + `files/`; each Asana **section** (sub-project) has a matching vault sub-folder **direct under the parent**, each with its own `README.md` + `files/`. Create any missing vault-side piece now (per the onboarding rituals).
- **Names match**: vault folder / sub-folder names match the Asana project / section names (hyphen convention; sub-project slugs are kebab-case of the section).
- **No orphans either way**: a vault sub-folder with no matching Asana section, or an Asana section with no vault sub-folder, is drift — resolve the vault side if obvious, surface Asana-side to Pete.
- **State parity**: work this session shipped (recorded in the vault) is reflected in Asana — the task is closed / moved to the right section (overlaps Step 3a); conversely, a task marked done in Asana should have its vault artefact updated.
- **SY-Clancy exception**: vault content lives at `Customers/SY-Clancy/`, never `Projects/SY-Clancy/` — never propose moving it.
- **Don't sprawl**: never create a new project / sub-project (or vault sub-folder) for 1-2 tasks; default to the parent's `{prefix}-General` sub-project; ask Pete before creating either.

**Scope**: projects touched this session only. The exhaustive all-projects parity sweep is the `vault-check` skill's job — don't shadow-run it here.

### Step 4: Housekeeping

- Wikilinks used for all references in files written this session
- Frontmatter on all new files (minimum: type, date)
- No orphan notes -- every new file linked from at least one existing file

### Step 5: Verification

- Re-read every file that was created or modified during the session
- Confirm content actually landed (don't just claim it's done)
- If anything is wrong or missing, fix it before moving on

### Step 6: Report to Pete

- Present a summary: what was saved, where, what was updated, what Asana tasks were created
- Flag anything that needs Pete's attention next session
- Pete may request changes, additions, or notes at this point -- handle them before continuing

### Step 7: Daily note

- Create or append session entry to `Daily/YYYY-MM-DD.md`
- Include: what was worked on, what was saved, decisions made, what's pending
- Use `[[wikilinks]]` for all project and person references
- Include any late additions from Pete's feedback in Step 6

### Step 8: Update MAP.md

- Add entries for any new files created (including anything from Steps 6-7)
- Remove entries for any files deleted
- Update descriptions for files whose purpose changed
- **Run MAP-drift check via Desktop Commander** to catch anything that bypassed Claude during the session. Same routing pattern as Step 9 -- both end-of-session scripts go through DC for a single consistent invocation pattern. The script itself is sandbox-safe via `_detect_vault()` as a fallback, but DC is the canonical path here.

  ```python
  mcp__Desktop_Commander__start_process(
      command='python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/vault-drift-check.py" --map-only 2>&1 | tail -40',
      timeout_ms=45000,
  )
  ```

  If it flags drift, fix the missing entries in MAP.md right now (Pete might have added a file in Obsidian or Michaela might have uploaded via Drive while the session was running). Future-proof: the check walks by directory so new top-level patterns are caught without code changes.

### Step 9: Run vault-drive-sync (failsafe, always last)

Best-effort run of the sync helper as a session-end failsafe in case the hourly LaunchAgent has failed or the host was asleep. This guarantees that anything edited or added in vault during the session reaches Drive (and anything dumped on My Drive lands in `Personal/inbox/`).

> [!important] Run via Desktop Commander, NOT workspace bash
> The vault-drive-sync helper references Drive paths under `/Users/peterashcroft/Library/CloudStorage/...` which exist only on the host filesystem -- the Cowork sandbox can't see them. Always invoke via `mcp__Desktop_Commander__start_process` so the script runs host-side. Workspace bash will fail with FileNotFoundError on the Drive paths.

```python
mcp__Desktop_Commander__start_process(
    command='python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/vault-drive-sync.py" 2>&1 | tail -40',
    timeout_ms=60000,
)
```

- Run quietly. If it succeeds, note "vault-drive-sync ran (two-way + pull-only, X files changed)" in the Step 6 report.
- If it errors (Drive Desktop offline, paths missing, etc.), log the error in the daily note and flag for Pete's attention but **do NOT block session close** -- the sync can be retried next session.
- This step runs AFTER MAP.md update so the sync picks up the latest MAP changes too.

Why this is a step here, not just a cron: the hourly LaunchAgent is the primary path. This step is the failsafe -- if launchctl unloaded the job, if the Mac was asleep across the hourly fire, if the cron crashed silently. Running on every session close means edits never wait more than one session to propagate.

Step 8's MAP-drift check (`vault-drift-check.py --map-only`) was made path-agnostic 2026-05-04 so it runs cleanly from sandbox bash too -- no DC required there.

---

## Formatting Standards

### YAML Frontmatter

```yaml
---
type: project | meeting | decision | sop | market-intel-report | ip-portfolio-report | ...
date: YYYY-MM-DD
department: sygma-solutions | sygma-training | sygma-gpr | canary-detect | one-system | el-atico
project: Project-Name
status: active | planning | on-hold | completed
tags: [relevant, tags]
---
```

### Wikilinks

Every mention of a project, person, or vault note MUST be a `[[wikilink]]`. Weave them into sentences naturally.

Wrong: `The Google Ads account was fixed. Related: [[SY-Website/ads]]`
Right: `The [[SY-Website/ads]] account issues from 12 March have been resolved.`

### Obsidian-Flavored Markdown

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

Several scheduled tasks modify the vault. Always re-read a vault file before editing if time has passed since your last read — automated runs may have written to it.

**Do not embed cron lists in this skill — they drift.** The single sources of truth (locked 2026-06-06 after embedded copies went stale):

- `[[scheduled-tasks]]` — narrative registry, entry per task with vault-touch lists. **Its header carries the dashboard 3-step routing rule.**
- `Library/processes/automations-dashboard/automations.json` → live view at https://pete-automations.vercel.app
- `mcp__scheduled-tasks__list_scheduled_tasks` — live Cowork cron state

Any cron change this skill's session touches (create / edit / pause / decommission, any runtime) must run the dashboard 3-step: update `automations.json` → re-embed `index.html` → `deploy.py`. See [[Library/lessons/2026-06-06-cron-changes-update-dashboard-skills-point-at-registries]].

The scheduled-task SKILL.md prompts themselves live OUTSIDE the vault at `~/Documents/Claude/Scheduled/{taskId}/SKILL.md` (Cowork's canonical path). DO NOT delete that folder. Vault sidecar at `Library/skills/scheduled/` is a recovery-only mirror.

---

## Web Mode (No File Access)

When running on claude.ai web without file access, produce vault-ready markdown blocks Pete can paste directly:

```markdown
%% Save to: Projects/CD-Website/seo/files/keyword-research.md %%
---
type: research
date: 2026-05-06
project: CD-Website
sub_project: seo
department: canary-detect
tags: [seo, research]
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
- Put project working files in Properties/ -- they go in `Projects/{name}/files/`
- Put operational data (keyword trackers, surfer scores) in Projects/ -- goes in `Properties/{Name}/data/`
- Auto-create tasks anywhere other than Asana
- Skip the daily note after meaningful work

## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill per [[Library/audits/2026-05-16-lesson-deployment-matrix]]:

- [[Library/lessons/2026-05-03-header-name-lookups-for-resilient-scripts]]
- [[Library/lessons/2026-05-04-skill-md-canonical-and-mirror-not-hardlinked]]
- [[Library/lessons/2026-05-05-sheet-migration-via-values-update-is-wrong]]
- [[Library/lessons/2026-05-06-vault-bookkeeping-with-artefacts]]

