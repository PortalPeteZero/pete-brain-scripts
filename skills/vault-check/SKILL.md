---
name: vault-check
description: >
  Comprehensive system audit of Pete's Command Centre. Audits every skill (SKILL.md
  ↔ .skill archive ↔ install location), every scheduled task / cron (canonical SKILL.md
  AND live cron registry), verifies Sygma Hub Drive integrity, checks every process /
  connection / API doc, verifies the full CLAUDE + MAP are semantically current, and
  sweeps recent daily logs for pending-task drift. Future-proof: walks directories and
  queries live registries rather than hard-coding lists, so new skills / crons /
  processes are picked up automatically. Behavioural contract: no shortcuts, no skim
  reading, no "leave for another session", no key-files-only. Trigger phrases include
  "system check", "audit the system", "full audit", "check the Command Centre",
  "vault check", "thorough audit".
---

<!-- drive-cloudstorage-allowed: this skill references the CloudStorage path for orientation when auditing Drive integrity. The actual Drive checks are delegated to drive-api.py. See [[external-service-routing]]. -->
<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Sheets / Docs / Xero / Odoo / GSC / GA4 / Vision / Geocoding / Sentry operation in this skill, see [[external-service-routing]]. Helper-first. -->


# System Check

> [!important] What this skill audits
> The Command Centre is a cloud system: tools in GitHub `pete-brain-scripts` (pulled to `/tmp/pbs`), secrets in the CC `secrets` table, knowledge in `vault_notes`, files in Google Drive (`drive_files` index), tasks in `public.tasks`, session logs in `daily_log`, crons on Railway. This audit checks all of it is coherent and points where it should:
> 1. **Skeleton integrity** — the tiny `CLAUDE.md` bootstrap + `~/.config/pete-cc/` (kernel, caches, hooks).
> 2. **Skills** — each SKILL.md is cloud-aligned (tool paths are `/tmp/pbs`, no inline secrets, no stale routing) and its `.skill` archive matches source.
> 3. **Scheduled tasks / crons** — the live Railway estate (`public.crons` / `/m/automations-log`) matches intent.
> 4. **Cloud-homes consistency** — `vault_notes` has 0 un-embedded notes; `drive_files` is fresh; secrets are complete in the CC table; the reconcile gate passes (`VAULT=/tmp/pbs python3 /tmp/pbs/vault-reconcile-gate.py`).
> 5. **Processes / connections / APIs, Sygma Hub Drive, CLAUDE + MAP semantics, daily-log drift.**
>
> Knowledge → `vault_notes` (`cc-knowledge-api.py`); files → `drive_files` (`cc-sql.py`); a `[[wikilink]]` links a note by its name in `vault_notes`.

Version history: [[CHANGELOG]].

> **This is the thorough audit skill.** Run when Pete asks for a system check, before installing any skill update, after a major change, or quarterly as preventive medicine.
>
> **Behavioural contract:**
>
> 1. Start from scratch. Do a full audit and create a report.
> 2. Audit every single skill and every scheduled task.
> 3. Ensure crons are properly saved (the canonical SKILL.md the cron runs matches intent, and the live cron is using the canonical, not an old version).
> 4. Ensure Sygma Hub Drive integrity is working and hasn't been undone.
> 5. Present a report on it all and a plan to fix any issues.
> 6. **No deferring to another session. No "this is minor leave it for another session". Fix everything in the same session.**
> 7. Set a task for each phase (TaskCreate -- one task per phase).
> 8. **No shortcuts. No skim reading. No just-the-key-files. Read everything.**
> 9. Check all processes, connections, APIs.
> 10. Ensure everything is in sync and everything points where it should.
> 11. Check the full CLAUDE + MAP.

## Usage

```
Pete: system check
Pete: audit the system
Pete: full audit
Pete: check the Command Centre
```

The skill runs phase-by-phase, sets a task per phase, fixes issues as it goes, and ingests a final report to `vault_notes` (`type: audit`).

## Key locations (fixed)

- Tools: GitHub `pete-brain-scripts` → pulled to `/tmp/pbs` by the boot kernel; run `VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py`.
- Skills: `pete-brain-scripts/skills/{name}/SKILL.md` + packaged `.skill` archives.
- Secrets: CC `secrets` table (materialised to `/tmp/pbs`); the one permanent local key is `~/.config/pete-secrets/command-centre-supabase-keys.json`.
- Crons: Railway estate, registered in `public.crons` / surfaced at `/m/automations-log`.
- Pete & Mic Drive + Sygma Hub Drive: under the cloud-synced `~/Library/CloudStorage/…` mount (query the `drive_files` index, don't walk the mount by hand).

## Execution, READ THIS FIRST

**Use Desktop Commander (`mcp__Desktop_Commander__*`), not workspace bash, for any walk that might exceed ~30s.** The workspace bash sandbox has a 45-second cap. The cron-vs-SKILL trap is documented at `[[2026-05-02-scheduled-task-skill-md-uses-dc]]`.

Concretely:
- Inventory walks → write a Python script to `/tmp/`, run via `mcp__Desktop_Commander__start_process`, capture results to a temp JSON / txt for parsing.
- Skill / cron / process audits → file tools (Read/Grep) for individual files in `/tmp/pbs`.
- Cloud-homes health → `VAULT=/tmp/pbs python3 /tmp/pbs/vault-reconcile-gate.py` + confirm `vault_notes` / `drive_files` / `crons` are current.

Workspace bash is fine for genuinely-fast one-shots (`wc -l`, `ls`, etc).

## Phases

Phases run in order. Each phase gets its own task via TaskCreate so progress is visible. Fix-on-find is the default within each phase. Defer NOTHING.

### Phase 0 -- Set up

1. **Create the master task list**. Use TaskCreate to make one task per phase below. This is the audit's task list.
2. **Confirm the boot kernel ran** — `/tmp/pbs` exists and the caches are present. If not, run `python3 ~/.config/pete-cc/pete-session-bootstrap.py` first.
3. **Note start time**. The report records duration so future runs know what to expect.

### Phase 1 -- Cloud-homes health check

Confirm the four cloud homes are coherent:

1. **Knowledge** — `vault_notes` has 0 un-embedded notes: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT count(*) FROM vault_notes WHERE embedding IS NULL"`. If non-zero, run `cc-knowledge-embed-backfill.py` and re-check.
2. **Files** — `drive_files` is fresh (the `drive-changes-watch` cron is current in `/m/automations-log`); spot-check a recent change is indexed.
3. **Secrets** — every secret a skill/process references exists in the CC `secrets` table (materialised under `/tmp/pbs`). Flag any referenced-but-missing key.
4. **Reconcile gate** — `VAULT=/tmp/pbs python3 /tmp/pbs/vault-reconcile-gate.py` passes. Fix any failure in-session.

### Phase 2 -- Skill audit (SKILL.md ↔ .skill archive ↔ install location)

For each directory under `skills/` (excluding `scheduled/` and `_previous/`):

1. **SKILL.md must be valid** -- has frontmatter (`name:`, `description:`), parseable YAML, non-empty body.
2. **`.skill` archive must exist as a sibling** -- `skills/{name}.skill` is a zip with `{name}/SKILL.md` inside.
3. **Lockstep check** -- byte count + content of source `SKILL.md` matches what's inside the `.skill` archive (`unzip -p {name}.skill {name}/SKILL.md`). Repackage if drifted.
4. **References folder** -- if `skills/{name}/references/` exists, every reference file should be in the archive too.
5. **Description quality** -- the description triggers the skill correctly (meaningful trigger phrases, not just "this skill does X").
6. **Cloud-aligned content** -- tool paths are `/tmp/pbs`; no inline secrets; no writes to retired local-folder paths; routing matches the live homes.
7. **Cross-skill references** -- skill mentions another skill name? Confirm that other skill exists.

Future-proof: this is a directory walk. Add a new skill -- it gets audited automatically.

For each issue, fix in same session: rebuild archive, fix references, repackage. Update the Skills Library index (`skills/README`) if a version shifted unrecorded.

### Phase 3 -- Scheduled-task audit (the cron-vs-SKILL trap)

This is the trap from 2026-04-27 (a cron ran a stale path for 6 days because the SKILL.md was updated in the WRONG location). Two layers must align:

1. **Live cron registry** -- query `public.crons` (`cc-sql.py`) / `/m/automations-log` / `mcp__scheduled-tasks__list_scheduled_tasks` to get the current crons.
2. **Canonical source** -- the script + SKILL behaviour each cron actually runs (Railway service from a script's `# CRON-META` header, or the canonical scheduled-task SKILL).

Per cron:

- Verify the canonical source exists. If missing, the cron is broken silently -- fix immediately.
- Confirm it references the right scripts / paths / behaviours.
- If the logic was updated recently, confirm the live cron actually got the update (inspect the running prompt/script -- that's THE thing that runs). Push the canonical immediately if it's stale.
- Confirm the cron registry/notes describe the task with up-to-date schedule + "what it does".

Future-proof: walks the cron registry, doesn't enumerate task names.

### Phase 4 -- Sygma Hub Drive integrity

Pete built the Sygma Hub Drive structure + sync. Audit it hasn't been undone.

1. **`hub-content-index`** (knowledge note) -- read it. It lists the Hub top-levels (synced + live-only) and the Hub→home mappings.
2. **The Hub Drive folder still exists at root** -- `drive-api.py drives` shows `Sygma Hub` (Drive ID `0APzpyHHfvUyIUk9PVA`).
3. **Each mapped Hub area is reachable** in the `drive_files` index; flag any that have gone missing or stale.
4. **Run `VAULT=/tmp/pbs python3 /tmp/pbs/hub-sync.py status`** -- every mapping should show [EXISTS] with a recent timestamp. Flag [MISSING] or stale (>30 days).
5. **Spot-check a sample** -- pick 3 mappings, list the Drive folder contents at top level, confirm reasonable overlap with the index.

Fix-on-find: missing or stale mapping -- run `VAULT=/tmp/pbs python3 /tmp/pbs/hub-sync.py pull <mapping>` immediately.

### Phase 5 -- Processes / connections / APIs

Process/SOP/API references live as knowledge notes (`vault_notes`) and config docs pulled to `/tmp/pbs`. Future-proof by querying the knowledge DB + walking the pulled config, not by listing names.

For each process / connection / API reference:

1. Read it (no skim).
2. If it documents an API or connector, verify: the credentials exist in the CC `secrets` table (materialised under `/tmp/pbs`) if the doc says so; the helper script (`/tmp/pbs/{name}.py`) exists if referenced; the MCP connector ID matches the canonical `[[connections]]` registry.
3. If it documents a workflow with steps, confirm the referenced scripts / API calls / task-ids (CC `public.tasks`) are still valid.
4. **Watch for drift**: a doc that says "we do X via Y" when we no longer do X, or use Z not Y. Cross-check against current behaviour where verifiable.

Particular attention:

- `[[connections]]` -- against actual connected MCP servers + APIs
- `[[gmail-label-scheme]]` -- sample a few labels; do they exist in Gmail?
- the cron registry -- matches Phase 3's live list
- `[[voice-principles]]`, `[[finance-workflow]]`, `[[vault-routing]]` -- read end-to-end

### Phase 6 -- CLAUDE + MAP semantic check

Don't just verify paths exist. Read the files end-to-end and check:

1. **CLAUDE** (local = the tiny bootstrap; full = CC `config` row `claude-md`):
   - The tiny local bootstrap points at the kernel + the CC; the full CLAUDE in `config` is current (describes the cloud homes).
   - Every Rule has a clear "what to do / not do"; sweep for stale references to retired conventions.
   - Every wikilink resolves (against `vault_notes`).
2. **MAP** (CC `config` row `map-md` + `/m/map`):
   - Describes the cloud homes (Drive / `vault_notes` / GitHub / CC / Railway).
   - No entries pointing at deleted locations; `updated:` recent.

### Phase 7 -- Daily-log pending-tasks drift sweep

Scan the **most recent 14 `daily_log` entries** in the CC. For each:

1. Find every `> [!todo] Pending Tasks` block (a day may have several -- one per session log).
2. For each open `[ ]` line:
   - **If `(CC: <task-id>)` is referenced**: query the task store live (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT status FROM tasks WHERE id='<task-id>'"`). If the task is `status='done'` but the line still says `[ ]`, flag **closed-task / open-line drift**.
   - **Regardless**: grep the same-day log for matching evidence (commit hashes, 7-char SHA refs, "shipped as", "landed", "closed by"). If a later session log on the same date shows the work shipped, flag **same-day shipped / line-not-struck drift**.
3. **Report-only.** This skill reports drift; it does NOT auto-strike or auto-close. (Auto-strike + auto-close belong in brain Compress Step 7 + vault-writer Step 3a, which run every session as prevention.)

Output: one row per drift finding, with the `daily_log` date, task summary, evidence type, and proposed fix. If prevention is working this list should usually be empty -- non-empty means prevention silently failed and Pete should investigate why.

**Why this phase exists:** prevention can fail silently (a session that didn't run vault-writer, a step that errored, an updated SKILL.md that wasn't installed). This is the periodic catch -- surfaced 2026-05-04, lesson [[2026-05-04-same-day-reconciliation-gap]].

### Phase 8 -- Compile report + fix plan + execute remaining fixes

By this point, most issues should already be fixed (fix-on-find is the rule). This phase is the rollup.

1. Write the audit report and ingest it to the CC `vault_notes` (`type: audit`, via `cc-knowledge-ingest.py`) with:
   - Summary at top: total items audited, time taken, total issues found, total fixed in-flight, total open at report time.
   - Per-phase findings + fixes made.
   - Open-but-not-yet-fixed issues, each with proposed fix.
2. **Execute every remaining fix in this same session.** Do NOT close the session with open issues.
3. The map is auto-generated: `cc_map` (the `/m/map` page) by the `cc-map` cron, and the `config.map-md` orientation doc by the `cc-orientation-map-sync` cron (twice daily, from the live tables); nothing to hand-edit.
4. Append a session log entry to the CC `daily_log` (`INSERT … cron_name='session'` for today) with the headline numbers + a wikilink to the report.

Skill output to Pete: a tight summary with item count audited, issue count, all fixed (zero deferred), and a wikilink to the full report.

## Output style

Concise, factual, no preamble. Numbers, paths, GIDs. The report is a working document, not narrative prose. If you find yourself writing "we then proceeded to" -- delete it.

## Anti-patterns

- Skim-reading any file. Read every byte.
- Picking "the important files" and ignoring the rest. Walk every directory / query every registry.
- Hard-coding skill / cron / process names. Walk directories or query live registries.
- Saying "this is minor, leave for another session". Fix in the same session.
- Treating the report as the goal. The goal is a coherent system. The report is a side effect.
- Missing the cron-vs-SKILL trap (Phase 3). The IP portfolio bug ran for 6 days because someone updated SKILL.md in the wrong place.

## Frequency

- On Pete's verbal request ("system check", "audit the system")
- Before installing any major skill update
- After any major change to the system
- Quarterly as preventive medicine (suggest cron-ifying as a "system-check-quarterly" task once stable)

## Pointers

- Reconcile gate: `/tmp/pbs/vault-reconcile-gate.py`
- Hub sync helper: `/tmp/pbs/hub-sync.py` + [[hub-content-index]]
- Routing rules: [[vault-routing]]
- Connections registry: [[connections]]
- The IP portfolio cron-vs-SKILL trap that motivated Phase 3: [[2026-05-02-scheduled-task-skill-md-uses-dc]]

## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill per [[2026-05-16-lesson-deployment-matrix]]:

- [[2026-05-03-header-name-lookups-for-resilient-scripts]]
- [[2026-05-04-skill-md-canonical-and-mirror-not-hardlinked]]
- [[2026-05-05-sheet-migration-via-values-update-is-wrong]]
