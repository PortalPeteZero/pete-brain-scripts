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
> Files → **Google Drive** (find via the `drive_files` index: `/tmp/pbs/cc-sql.py`); knowledge / lessons / decisions / notes → **CC `vault_notes`** (`cc-knowledge-api.py`, surfaced in the CC Brain page). The vault content folders (`Properties/`, `Customers/`, `Suppliers/`, `Businesses/`, `Personal/`, `Accreditations/`, most of `Library/`) are **retired 24 Jun 2026 (now in Drive + vault_notes)** — route new content per the new-world matrix in [[vault-routing]]. `[[wikilinks]]` still resolve (by name, against `vault_notes`), so don't rewrite links. Full picture: `MAP.md`. State: [[Projects/PA-Command-Centre/files/business-os-master-plan-2026-06-20|master plan]].

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

The vault is retired. Save to the cloud homes (full matrix: [[vault-routing]]):
- **Knowledge / decisions / notes / research** → CC `vault_notes`: write a `.md` to `/tmp`, then `VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-ingest.py <file>` → null its embedding → `cc-knowledge-embed-backfill.py`.
- **Files / documents / data** → the entity's **Google Drive** folder (find it: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT drive,path FROM drive_files WHERE …"`).
- **Live work** → Asana / the CC `tasks` engine. **Session log** → CC `daily_log`.
- **Tools** pull from GitHub to `/tmp/pbs`; run `VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py`.

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
> List **every distinct topic, entity, project, property, or piece of work** touched this session. For each, find its home and update it with what changed **+ the rationale**. Find homes by querying the cloud, never a local tree: knowledge → `vault_notes` (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py`); files/entities → the `drive_files` index (`cc-sql.py`). State of play lives in the entity's note / Drive folder, not the daily log. Generalised from the website lesson (in `vault_notes`).

Then scan everything discussed this session and route each category to its cloud home (full matrix: [[vault-routing]]):

- **Properties / property data** (hosting, stack, IDs, SEO, Surfer scores, audits) → the property's record in the **CC Properties module** + data files to its **Drive** folder.
- **Customers / Suppliers** (new relationship, matter, contract, status) → the entity's **Drive** folder + a `vault_notes` record. For account-customers (e.g. Clancy) the CC `account_*` store is the live home.
- **People / Businesses / operational areas** (roles, KPIs, SOPs, finance/insurance/vehicles) → the relevant **Drive** home + `vault_notes`. `vault-enricher.py` (called by triage/sync) still auto-pulls attachments — keep calling it.
- **Personal / Family** → the **My Drive** / **Ashcroft Family** Drive home — write **direct to Drive** via Desktop Commander (no vault folder, and `vault-drive-sync` is retired). TitleCase folder names in the family Drive.
- **Sygma owner-private** (salaries, payroll, pay-sensitive) → **Drive `Pete & Mic / Sygma Solutions Private/`** direct (never a shared Sygma drive). [[shared-drives#sygma-solutions-private]]. Monthly payroll: [[file-wages-email]].
- **Decisions / competitive intel / lessons / processes / connectors** → CC `vault_notes` (ingest a `.md`). API-config docs are the surviving `Library/processes/` skeleton reference; new ones go there too + ingest.
- **Gmail labels** → Gmail is source of truth; only update `[[gmail-label-scheme]]` for a NEW category/colour/mode/rule.
- **CLAUDE.md** → only on an explicit Pete correction he asks to be saved; structured rules → `vault_notes`.
- **Vault-routing capture** → a new convention must be reflected in `[[vault-routing]]` (ingest the update) AND notify Pete.

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

**Close-on-ship (mechanical — the durable fix for "shipped it, never closed it"):** for every discrete thing this session actually shipped — a commit, a cron added to the registry, a file created, an email sent that names a task — run `VAULT=/tmp/pbs python3 /tmp/pbs/asana-reconcile.py --ship <gid|keyword>…`. It searches the **full open Asana list** (not just today's TODO block) for the matching task(s) and lists them; after eyeballing the match, re-run with `--apply-auto` to close them with an audit comment. This catches the common miss the same-day check is blind to: a task opened in an *earlier* session whose work lands today. Same discipline — lists first, you confirm, never closes on assumption.

**Make the close findable (so the safety nets work):** two cheap conventions feed the `asana-ship-gate` Stop hook (deterministic, harness-run every turn — it won't let you sign off if you shipped a task-referenced thing that's still open):
- **Code:** put the Asana gid in the commit message when a commit completes a task (e.g. `… (Asana 1215631946311467)`).
- **Non-code ships you can't close immediately** (a cron, an email, a file): drop a `SHIPPED: <gid> — <evidence>` line in today's daily note. The reconciler treats that explicit marker as auto-closeable, and the hook catches it at sign-off.
Neither replaces actually closing the task — they're what make a *missed* close get caught instead of rotting. Full design: [[Library/decisions/2026-06-14-asana-reconciliation-system]].

**Why:** before this step, each session log's pending-task block was treated as final. A morning session opens "Wire X" + creates Asana 1234; a 12:30 detour ships X as commit ABC; end-of-day vault-writer never re-read the morning's TODO block. Asana sat with a closed-but-still-open task; daily note still claimed `[ ] Wire X`. Pete spots it the next morning, vault loses credibility. Surfaced 2026-05-04 via the `x_studio_report_link` writeback (Asana 1214496261050040, shipped as `ba02060`). See [[Library/lessons/2026-05-04-same-day-reconciliation-gap]].

**How to apply:** Runs end-of-session, before Verification. Cheap because today's daily note is small. Touches only TODO lines that have positive evidence -- never strikes a line on assumption alone. Mirror logic also lives in brain Compress Step 7 (canonical orchestrator); vault-writer's copy is the cleanup-checklist guarantee that it actually runs.

#### Step 3b: Asana staleness sweep (NEW v5.5, 2026-05-20)

Pete's projects drift messy over time. At session end, scan the whole workspace for stale work and surface a short digest in Step 6. **Surface-only — never bulk-close, delete, reassign, or re-section without Pete's explicit per-item confirmation** (Asana teams + tasks are sacred; never bulk-delete without confirmation).

**Mechanic — run the evidence reconciler, don't hand-roll it:**
- `VAULT=/tmp/pbs python3 /tmp/pbs/asana-reconcile.py --overdue-only` (or no flag for the full open set). It pulls Pete's open tasks (direct PAT) and buckets each by **completion evidence**, not just age:
  - **AUTO** — unambiguous mechanical proof (the gid is in a merged commit; a "build cron X" task whose named cron now exists in the registry). The only bucket eligible for unprompted close, via `--apply-auto`.
  - **PROPOSE** — strict, suggestive evidence (a non-list daily-note line records it done next to the gid; or, for a reply/chase task, Pete sent the last message on the linked thread). **Surface for Pete's one-word confirm — never auto-close.**
  - **PAYMENT** — "Pay X" payables; can't be verified here, always Pete's call.
  - **OPEN** — no trustworthy signal; silent unless >30 days overdue.
- The reconciler is deliberately **high-precision** (it once flagged 47/59 on a loose heuristic — status-dump resume lines leak completion words across task IDs; the strict version drops that to a handful). If it surfaces a lot, suspect a regex regression, not a real backlog.

**Digest (in Step 6):** surface the AUTO + PROPOSE + PAYMENT buckets (the OPEN >30d list is a quieter footnote). One line per task with its evidence + recommended action. If all buckets are empty, say so in one line — don't manufacture noise.

**Weekly safety net:** the full evidence sweep also runs every Sunday via the `asana-reconcile` cron, which emails Pete the pre-evidenced digest — so the backlog gets a regular pass even in weeks where no session triggers this step. (Registry: [[scheduled-tasks]].)

**Never auto-act beyond AUTO.** The only unprompted Asana mutations are: closing tasks *this* session demonstrably shipped (Step 3a / `--ship`), the reconciler's AUTO bucket (`--apply-auto`, mechanical proof only), and auto-creating clear follow-ups (Step 3). PROPOSE / PAYMENT / stale-item cleanup is always Pete's per-item call (Asana teams + tasks are sacred).

**Why:** end-of-session is the natural moment to keep the work brain clean. Surfaced 2026-05-20 when SY-Website carried 80 open tasks — 42 stale Jane backlink tasks from 6 May (since emailed to move to her own project) + ~6 April-dated SEO monitoring tasks long since done. Mirror in brain Compress so it runs whichever skill closes the session.

#### Step 3c: Asana state parity

For every project **touched this session**, confirm Asana reflects what shipped — the task is closed / moved to the right section (overlaps Step 3a); conversely a task marked done in Asana has its artefact updated in the cloud (Drive / `vault_notes` / CC). **Don't sprawl**: never create a new project/sub-project for 1-2 tasks; default to the parent's `{prefix}-General`; ask Pete before creating either. (The old Asana↔vault-folder parity check is retired — there are no vault project folders.)

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

### Step 7: Daily log (CC)

- Append this session's entry to the CC `daily_log` (`date`=today, `cron_name`=`'session'`, `content`=summary) via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py` (dollar-quote the content). This is the cloud last-session that brain Resume reads — don't skip it.
- Include: what was worked on, what was saved, decisions made, what's pending
- Use `[[wikilinks]]` for project and person references
- Include any late additions from Pete's feedback in Step 6

### Step 8: Propagate to the cloud

MAP is now **auto-generated** (a CC `config` row, regenerated daily) — there is no manual MAP.md to maintain and no MAP-drift check. The hourly `vault-drive-sync` is **retired**. Knowledge reaches the cloud when you **ingest it to `vault_notes`** (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-ingest.py <file>` → null its embedding → `cc-knowledge-embed-backfill.py`); files reach the cloud by living in their **Drive** folder (captured automatically by the `drive-changes-watch` Railway cron). Confirm each thing you saved this session has landed in its cloud home before sign-off.

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
- Write to a retired vault folder (`Properties/`, `Customers/`, `Projects/`, `Library/lessons` …) — they're gone; route to Drive + `vault_notes`
- Put operational data (keyword trackers, Surfer scores) anywhere but the property's Drive folder + its CC record
- Auto-create tasks anywhere other than Asana
- Skip the daily note after meaningful work

## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill per [[Library/audits/2026-05-16-lesson-deployment-matrix]]:

- [[Library/lessons/2026-05-03-header-name-lookups-for-resilient-scripts]]
- [[Library/lessons/2026-05-04-skill-md-canonical-and-mirror-not-hardlinked]]
- [[Library/lessons/2026-05-05-sheet-migration-via-values-update-is-wrong]]
- [[Library/lessons/2026-05-06-vault-bookkeeping-with-artefacts]]

