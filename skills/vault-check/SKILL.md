---
name: vault-check
description: >
  Comprehensive vault audit. Reads every md file in the vault, audits every
  skill, audits every scheduled task (canonical SKILL.md AND vault recovery
  mirror AND live cron registry), verifies Sygma Hub linking integrity, checks
  every Library/processes/ doc, verifies CLAUDE.md + MAP.md semantic
  consistency, runs vault-drift-check, runs vault-drive-sync drift report.
  Future-proof: walks directories rather than hard-coding lists, so new files /
  skills / scheduled tasks / processes / personal areas are picked up
  automatically. Behavioural contract: no shortcuts, no skim reading, no
  "leave for another session", no key-files-only. Trigger phrases include
  "vault check", "audit the vault", "full vault audit", "check the vault",
  "audit my vault", "thorough audit", "vault audit".
---

<!-- drive-cloudstorage-allowed: this skill references the CloudStorage path for orientation when auditing Drive parity. The actual Drive parity check is delegated to drive-api.py via vault-drift-check.py. See [[external-service-routing]]. -->
<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Sheets / Docs / Xero / Odoo / GSC / GA4 / Vision / Geocoding / Sentry operation in this skill, see [[external-service-routing]]. Helper-first. -->


# Vault Check

> [!important] RESCOPED 24 Jun 2026 — read before running. The vault is **retired**; most phases below audited the now-gone vault tree.
> **DO NOT run any phase that walks the content folders, or runs `vault-drift-check.py` / `vault-drive-sync.py` — both are RETIRED/disabled and the content folders are gone.** What this skill now usefully audits (thin-client scope):
> 1. **Skeleton integrity** — the tiny `CLAUDE.md` bootstrap + `~/.config/pete-cc/` (kernel, caches, hooks) + `Library/processes` config docs + `Library/skills`.
> 2. **Skills** — each SKILL.md is cloud-aligned (no retired-folder writes; tool paths are `/tmp/pbs`; no inline secrets) and its `.skill` archive matches source.
> 3. **Scheduled tasks / crons** — the live Railway estate (`public.crons` / `/m/automations-log`) matches intent.
> 4. **Cloud-homes consistency** — `vault_notes` 0 un-embedded; `drive_files` fresh; secrets complete in the CC table; reconcile gate passes (`VAULT=/tmp/pbs python3 /tmp/pbs/vault-reconcile-gate.py`).
> The behavioural contract (no shortcuts / fix everything) applies to **this scoped audit**, NOT the retired vault tree. Ignore anything below that contradicts this. Knowledge → `vault_notes` (`cc-knowledge-api.py`); files → `drive_files` (`cc-sql.py`); `[[wikilinks]]` resolve against `vault_notes`.

Thorough vault audit. Reads every md file, audits every skill + scheduled task, verifies Sygma Hub linking, checks every `Library/processes/` doc, verifies CLAUDE.md + MAP.md semantic consistency, runs `vault-drift-check.py` + `vault-drive-sync` drift report.

Version history: [[CHANGELOG]].

> **This is the thorough audit skill.** Born from Pete's 2026-05-03 instruction list. Run when Pete asks for a vault check, before installing any skill update, after a major restructure, or quarterly as preventive medicine.
>
> **Behavioural contract** (Pete's verbatim 2026-05-03 instructions baked in):
>
> 1. Start from scratch. Do a full audit. Read every md file and create a report.
> 2. Audit every single skill and every scheduled task.
> 3. Ensure crons are properly saved (canonical at `~/Documents/Claude/Scheduled/{taskId}/SKILL.md` matches vault mirror at `Library/skills/scheduled/{taskId}/SKILL.md`, and the live cron is using the canonical not an old version).
> 4. Ensure Sygma Hub linking is working and hasn't been undone.
> 5. Present a report on it all and a plan to fix any issues.
> 6. **No deferring to another session. No "this is minor leave it for another session". Fix everything in the same session.**
> 7. Set a task for each step (TodoWrite / TaskCreate -- one task per phase).
> 8. **No shortcuts. No skim reading. No just-the-key-files. Read everything.**
> 9. Check all processes, connections, APIs.
> 10. Ensure everything is in sync and everything points where it should.
> 11. Check CLAUDE.md and MAP.md.

## Usage

```
Pete: vault check
Pete: audit the vault
Pete: full vault audit
Pete: check my vault
```

The skill runs phase-by-phase, sets a task per phase, fixes issues as it goes, and produces a final report at `Library/audits/{date}-vault-check.md`.

## Vault paths (fixed)

- Vault: `/Users/peterashcroft/Second Brain/`
- Scheduled-task canonical: `~/Documents/Claude/Scheduled/{taskId}/SKILL.md` (Pete's Mac, NOT vault)
- Scheduled-task vault mirror: `Library/skills/scheduled/{taskId}/SKILL.md` (recovery copy)
- Pete & Mic Drive: `/Users/peterashcroft/Library/CloudStorage/.../Shared drives/Pete & Mic/`
- Sygma Hub Drive: same parent, `Sygma Hub/`
- My Drive: `/Users/peterashcroft/My Drive (pete.ashcroft@sygma-solutions.com)/`

## Execution, READ THIS FIRST

**Use Desktop Commander (`mcp__Desktop_Commander__*`), not workspace bash.** The workspace bash sandbox has a 45-second cap, which kills any vault-wide find / grep / read walk. The cron-vs-SKILL trap is documented at `[[Library/lessons/2026-05-02-scheduled-task-skill-md-uses-dc]]`; vault-check has the same problem.

Concretely, for every long-running step in this skill:
- Inventory walks (Phase 1) → write a Python script to `/tmp/`, run via `mcp__Desktop_Commander__start_process`, capture results to a temp JSON / txt for parsing.
- ~~Drift check (Phase 2)~~ + ~~Sync coherence (Phase 7)~~ → **RETIRED — skip** (`vault-drift-check` / `vault-drive-sync` are gone; see those phases).
- Skill / cron / Hub audits (Phases 3-5) → file tools (Read/Grep) for individual files; tools pulled to `/tmp/pbs`.
- Cloud-homes health (replaces Phase 2/7) → `VAULT=/tmp/pbs python3 /tmp/pbs/vault-reconcile-gate.py` + confirm `vault_notes` / `drive_files` / `crons` are current.

Workspace bash is fine for genuinely-fast one-shots (`wc -l`, `ls`, etc) but if a command might take >30s, use DC.

## Phases

Phases run in order. Each phase has its own task created via TaskCreate so progress is visible. Fix-on-find is the default within each phase. Defer NOTHING.

### Phase 0 -- Set up

1. **Create the master task list**. Use TaskCreate to make one task per phase below (Phases 1-9). This is the audit's TodoList.
2. **Confirm vault is mounted as the working folder**. If not, stop and tell Pete to mount it.
3. **Note start time**. The report will record duration so future runs know what to expect.

### Phase 1 -- Vault root + every md file inventory

Behaviour: walk the vault top-down, list every directory and every `.md` file. Then read every `.md` file (no skim). Build an inventory in the audit report.

**Implementation:** write a Python inventory walker to `/tmp/vault-md-inventory.py` and run via `mcp__Desktop_Commander__start_process` (not workspace bash). The walker captures path, size, frontmatter type/status/updated, and flags. Results to `/tmp/vault-md-inventory.json` + `.txt` for the audit report. See "Execution, READ THIS FIRST" above.

```bash
# Quick smoke-check only (workspace bash OK for this size)
find "/Users/peterashcroft/Second Brain" -name "*.md" -type f | wc -l
```

Expectation: typically 800-1500 md files. Read them in batches by directory. For each, capture: path, frontmatter type / status / updated date, file size. Flag any:

- File >0 bytes with empty frontmatter (no `---...---` block)
- File with frontmatter that doesn't match the section's convention (e.g. `Customers/{slug}/README.md` missing `gmail_label:` -- per `[[vault-routing]]`)
- File >50 KB (probably needs splitting or has accumulated cruft)
- Stale files: `updated:` frontmatter > 90 days old AND status: active / in-progress

(No dash / em-dash checks. Outbound-style rules apply only to content Pete sends out, not to vault md files. See [[voice-principles]].)

Read every file. Don't sample, don't skim. The skill description's contract demands it.

### Phase 2 -- RETIRED (skip)

`vault-drift-check.py` is **retired** (the self-maintaining `drive_files` capture cron + the derived MAP replaced it) and there are no content folders to drift-check. **Skip this phase.** The equivalent cloud health check (do this instead): `vault_notes` has 0 un-embedded notes, `drive_files` is fresh, all local secrets are in the CC table, and the reconcile gate passes — `VAULT=/tmp/pbs python3 /tmp/pbs/vault-reconcile-gate.py`. The scheduled-task lockstep + skill-archive freshness checks move to Phase 3.

### Phase 3 -- Skill audit (vault SKILL.md ↔ .skill archive ↔ install location)

For each directory under `Library/skills/` (excluding `scheduled/` and `_previous/`):

1. **SKILL.md must be valid** -- has frontmatter (`name:`, `description:`), parseable YAML, non-empty body.
2. **`.skill` archive must exist as a sibling** -- `Library/skills/{name}.skill` is a zip with `{name}/SKILL.md` inside.
3. **Lockstep check** -- byte count + content of vault `SKILL.md` matches what's inside the `.skill` archive (`unzip -p {name}.skill {name}/SKILL.md`). Repackage if drifted.
4. **References folder** -- if `Library/skills/{name}/references/` exists, every reference file should be in the archive too.
5. **Description quality** -- description in frontmatter triggers the skill correctly. Check it includes meaningful trigger phrases (not just "this skill does X").
6. **Cross-skill references** -- skill mentions another skill name? Confirm that other skill exists.

Future-proof: this is a directory walk. Add a new skill -- it gets audited automatically. No code change needed.

For each issue, fix in same session: rebuild archive, fix references, repackage. Update `Library/skills/README.md` if a version has shifted unrecorded.

### Phase 4 -- Scheduled-task audit (the cron-vs-SKILL trap)

This is the trap from 2026-04-27 (Pete's IP portfolio cron ran a stale path for 6 days because the SKILL.md was updated in the WRONG location). Three layers must align:

1. **Live cron registry** -- run `mcp__scheduled-tasks__list_scheduled_tasks` to get the current list of cron jobs. Each has a `taskId`.
2. **Canonical SKILL.md** at `~/Documents/Claude/Scheduled/{taskId}/SKILL.md` -- THE source the cron actually runs.
3. **Vault recovery mirror** at `Library/skills/scheduled/{taskId}/SKILL.md` -- read-only mirror for vault search + recovery.

Per cron job:

- Verify the canonical exists. If missing, the cron is broken silently -- fix immediately.
- Read the canonical. Confirm it references the right scripts / paths / SKILL behaviours. (Compare against any documented design in `Library/processes/scheduled-tasks.md`).
- Verify the vault mirror exists and matches the canonical byte-for-byte (or close enough -- per drift-check spec the first 200 chars must match). Repair drift immediately by copying canonical → mirror.
- Confirm `Library/processes/scheduled-tasks.md` registry mentions the task with up-to-date "Vault files touched" + "What it does" + schedule.
- If the SKILL.md's logic was updated recently, check the cron actually got the update. Use `mcp__scheduled-tasks__list_scheduled_tasks` and inspect each task's current prompt content -- this is THE thing that runs.

Fix-on-find: if a cron is using a stale prompt, push the canonical via `mcp__scheduled-tasks__update_scheduled_task` immediately.

Future-proof: walks the cron registry, doesn't enumerate task names. New cron added -- audited automatically.

### Phase 5 -- Sygma Hub linking integrity

Pete spent 2026-04-29 to 2026-05-01 building the Sygma Hub Drive structure + sync. Audit it hasn't been undone.

1. **`Library/processes/hub-sync-registry.md`** -- read it. It should list 11+ Hub→vault mappings with Drive folder IDs and last-pulled timestamps.
2. **For each registered mapping, the vault folder exists**. e.g. `Library/sy-policies/`, `Library/sy-templates/`, `Library/sy-equipment-manuals/`, `Library/sy-internal-tools/`, `Library/sy-hr/`, `Library/sy-sales-and-pipeline/`, `Library/sy-company-information/`, `Library/sy-brand-assets/`, `Library/sy-health-and-safety-posters/`, `Library/sy-topic-reference-material/`, `Library/sy-jim-google-api-setup-2026-05-01/`. Future-proof via the registry -- whatever it says, walk that.
3. **Hub Drive folder still exists at root** -- via `drive-api.py drives` should show `Sygma Hub` (Drive ID `0APzpyHHfvUyIUk9PVA`).
4. **`Library/processes/hub-content-index.md`** exists, lists all 12 Hub top-levels (synced + live-only).
5. **Run `VAULT=/tmp/pbs python3 /tmp/pbs/hub-sync.py status`** -- every mapping should show [EXISTS] with a recent last-pulled timestamp. If any [MISSING] or stale (>30 days), flag.
6. **Spot-check a sample** -- pick 3 mappings, list the vault sy-folder contents and the Drive folder contents at top level, confirm reasonable overlap. The local mirror is read-only (per CLAUDE.md rule); should match Drive within last sync window.

Fix-on-find: missing sy-folder or stale state -- run `VAULT=/tmp/pbs python3 /tmp/pbs/hub-sync.py pull <mapping>` immediately.

### Phase 6 -- Processes / connections / APIs

Every file in `Library/processes/` directly under that folder is a reference / SOP / API config. Future-proof by walking the directory, not by listing names.

For each `Library/processes/*.md`:

1. Read the file (no skim).
2. Confirm frontmatter has `type: process` (or another conventional type) and `status:`.
3. If it documents an API or connector, verify:
   - The credentials exist in `/tmp/pbs/Library/processes/secrets/` if the doc says so.
   - The helper script (`/tmp/pbs/{name}.py`) exists if referenced.
   - The MCP connector ID matches `Library/processes/connections.md` (the canonical registry).
4. If it documents a workflow with steps, confirm the referenced scripts / API calls / task-ids (CC `public.tasks`) are still valid.
5. **Watch for drift**: a process doc that says "we do X via Y" when actually we no longer do X, or use Z not Y. Cross-check against current behaviour where verifiable.

Particular attention:

- `connections.md` -- against actual connected MCP servers + APIs
- `gmail-label-scheme.md` -- sample a few labels; do they exist in Gmail?
- `scheduled-tasks.md` -- list matches Phase 4's live cron registry
- `vault-drive-sync.md` -- LaunchAgent loaded? `launchctl list | grep vault-drive` returns it?
- `hub-sync-registry.md` + `hub-content-index.md` -- already covered in Phase 5; cross-check passes here too
- `voice-principles.md`, `finance-workflow.md`, `scripts-index.md`, `vault-routing.md` -- read end-to-end

### Phase 7 -- RETIRED (skip)

`vault-drive-sync` is **disabled for good** — there is no vault↔Drive sync to audit (files live in Drive directly; the vault is retired). **Skip this phase.** Drive freshness is now the `drive-changes-watch` Railway cron's job (verify it in `/m/automations-log` if needed).

### Phase 8 -- CLAUDE.md + MAP.md semantic check

Don't just verify paths exist (drift-check does that). Read the FILES end-to-end and check:

1. **CLAUDE.md** (local = the tiny bootstrap; full = CC `config` row `claude-md`):
   - The tiny local bootstrap points at the kernel + the CC; the full CLAUDE in `config` is current (describes the cloud homes, not the vault tree).
   - Every Rule has a clear "what to do / not do"; sweep for stale references to retired vault folders / conventions.
   - Every wikilink resolves (against `vault_notes`).
2. **MAP** (CC `config` row `map-md` + `/m/map`):
   - Describes the cloud homes (Drive / `vault_notes` / GitHub / CC / Railway), not a vault tree.
   - No entries pointing to deleted vault folders; `updated:` recent.

### Phase 9 -- Daily-note pending-tasks drift sweep

Scan the **most recent 14 `daily_log` entries** in the CC. For each:

1. Find every `> [!todo] Pending Tasks` block (a daily note may have several -- one per session log).
2. For each open `[ ]` line:
   - **If `(CC: <task-id>)` is referenced**: query the CC task store live (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT status FROM tasks WHERE id='<task-id>'"`). If the CC task is `status='done'` but the daily-note line still says `[ ]`, flag this as **closed-task / open-line drift**.
   - **Regardless**: grep the rest of the same-day daily note for matching evidence (commit hashes, "ba02060"-style 7-char SHA refs, README "recent commits" lines, decision-doc creation, "shipped as", "landed", "closed by"). If a later session log on the same date shows the task's underlying work shipped, flag this as **same-day shipped / line-not-struck drift**.
3. **Report-only.** vault-check reports drift; it does NOT auto-strike or auto-close. (Auto-strike + auto-close belong in brain Compress Step 7 + vault-writer Step 3a, which run every session as prevention.)

Output for Phase 9 in the audit report: one row per drift finding, with daily-note path, line number, task summary, evidence type (CC task-state mismatch / same-day shipped), and proposed fix. If brain Compress + vault-writer Step 3a are doing their job, this list should be empty most of the time -- non-empty means prevention silently failed and Pete should investigate why.

**Why this phase exists:** prevention can fail silently (a session that didn't run vault-writer at the end, a vault-writer step that errored, an updated SKILL.md that wasn't installed). Phase 9 is the periodic catch -- surfaced 2026-05-04, lesson [[Library/lessons/2026-05-04-same-day-reconciliation-gap]].

### Phase 10 -- Compile report + fix plan + execute remaining fixes

By this point, most issues should already be fixed (fix-on-find is the rule). Phase 10 is the rollup.

1. Write the audit report to `Library/audits/{YYYY-MM-DD}-vault-check.md` with:
   - Summary at top: total files audited, time taken, total issues found, total fixed in-flight, total open at report time.
   - Per-phase findings + fixes made.
   - Open-but-not-yet-fixed issues, each with proposed fix.
2. **Execute every remaining fix in this same session.** Do NOT close the session with open issues.
3. Ingest the audit report to the CC `vault_notes` (`cc-knowledge-ingest.py`) — the map is the cloud `cc_map` (regenerated by the `cc-map` cron) + the `config.map-md` orientation doc; there is no local `MAP.md` to hand-edit.
4. Append a session log entry to the CC `daily_log` (`INSERT … cron_name='session'` for today) with the headline numbers + a wikilink to the report — not a vault file.

Skill output to Pete: a tight summary with file count audited, issue count, all fixed (zero deferred), and a wikilink to the full report.

## Output style

Concise, factual, no preamble. Numbers, paths, GIDs. The report is a working document, not narrative prose. If you find yourself writing "we then proceeded to" or "in the next phase we will" -- delete those sentences.

## Anti-patterns

- Skim-reading any md file. Read every byte.
- Picking "the important files" and ignoring the rest. Walk every directory.
- Hard-coding skill / cron / process names. Walk directories or query live registries.
- Saying "this is minor, leave for another session". Fix in the same session.
- Treating the report as the goal. The goal is a coherent vault. The report is a side effect.
- Running drift-check and stopping. Drift-check is Phase 2 of 9.
- Missing the cron-vs-SKILL trap (Phase 4). The IP portfolio bug ran for 6 days because someone updated SKILL.md in the wrong place.

## Frequency

- On Pete's verbal request ("vault check", "audit the vault")
- Before installing any major skill update
- After any major vault restructure (e.g. adding a new top-level section)
- Quarterly as preventive medicine (suggest cron-ifying as a "vault-check-quarterly" task once the skill is stable)

## Pointers

- Drift-check helper: `/tmp/pbs/vault-drift-check.py`
- Sync helper: [[vault-drive-sync]]
- Hub sync helper: `/tmp/pbs/hub-sync.py` + [[hub-sync-registry]]
- Vault routing rules: [[vault-routing]]
- Pre-skill-install audit example: [[Library/audits/2026-05-03-pre-skill-install-audit]]
- Full vault audit example: [[Library/audits/2026-05-03-full-vault-audit]]
- The IP portfolio cron-vs-SKILL trap that motivated Phase 4: [[Library/lessons/2026-05-02-scheduled-task-skill-md-uses-dc]]

## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill per [[Library/audits/2026-05-16-lesson-deployment-matrix]]:

- [[Library/lessons/2026-05-03-header-name-lookups-for-resilient-scripts]]
- [[Library/lessons/2026-05-04-skill-md-canonical-and-mirror-not-hardlinked]]
- [[Library/lessons/2026-05-05-sheet-migration-via-values-update-is-wrong]]
- [[Library/lessons/2026-05-06-vault-bookkeeping-with-artefacts]]

