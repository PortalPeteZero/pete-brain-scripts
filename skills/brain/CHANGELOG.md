---
type: changelog
skill: brain
updated: 2026-06-30
---

# Brain skill changelog

## 2026-06-30
- Compress Step 7c gains a **deterministic reconcile gate**: for every repo committed this session, run `worklog.py reconcile` and log any flagged commit. Diffs git vs `work_log.source_ref` so a forgotten ship can't slip — replaces sole reliance on recall. Scope broadened to **any product repo** (a separate app like LeakGuard), not just website/CC. Added after a full day of LeakGuard deploys reached no work_log row until Pete asked. See [[work-log]].

## 2026-06-29
- Compress Step 7c — log-on-ship to the Work Log (`worklog.py`), mirroring vault-writer so it runs whichever skill closes the session. See [[work-log]].

## 2026-06-29 (Plan/note lifecycle — verify-before-surface + context-switch close-out)
- **Resume Step 5 (plans)** no longer reports an in-progress/ready plan as live. It now VERIFIES each against the live system first; a plan whose work demonstrably shipped is surfaced as "Looks shipped — close this plan? {title}" for Pete to confirm (never auto-stamped at resume), not announced as in-progress. Fixes the recurring "a fresh session tells Pete a plan's still in progress when it actually shipped".
- **Resume Step 3 (tasks)** gains a shipped-evidence sanity-check before presenting an open task as a priority; looks-done tasks are surfaced as "Looks shipped — close? {task}" for confirmation (never auto-closed — tasks are Pete's).
- **New context-switch close-out** (Compress closing-nudge): on a mid-session pivot to a different project, stamp the left project's finished plans, fence its state notes, reconcile its tasks — inline, no nag. The prevention half, so abandoned plans don't survive to the next Resume even when no end-of-session Compress runs. (Pete-directed: stop having to manually remind Claude to close/stamp plans every session.)

## 2026-06-28 (Step 2a — PF journal reads from CC, not local Drive mount)
- **Resume Step 2a fixed**: PF journal lesson now read from CC `health_journal` table via `cc-sql.py`. Removed stale instruction to read from `~/Library/CloudStorage/.../My Drive/Passion Fit/journal/` via Desktop Commander. Journal migrated to CC on 2026-06-27; the skill hadn't been updated. Added hard prohibition: never read a local Drive mount for this step.

## 2026-06-27 (G6.3 — Resume surfaces manual notes/projects)
- **Resume Step 3c extended**: now also flags NOTES and PROJECTS added in the CC since the last session (same `<cutoff>` as Step 3a's manual-task detection), with a new `**New since last session**` briefing line. Pete edits the CC directly, so jotted notes / spun-up projects surface the same way manual tasks do. List-only, no auto-action.
- Context: also fixed `cc-skeleton-registry-sync.py` (was crashing + scanning dead `Library/skills`/`Library/processes/scripts` paths) so the `public.skills`/`public.helpers` registries self-heal; added `data_map` rows for skills + helpers. Skill source of truth = `skills/<name>/SKILL.md` in pete-brain-scripts, packaged via `package-skill.py`.

## 2026-06-06 (second edit today)
- **Resume Step 3b: Actions tray check** — live Gmail `label:Actions` query in every Resume; briefing gains an `Actions tray` line (count + >3d agers, oldest first, cap 5, walker hook "say actions"). Tray is reply-shaped only per the Action/Task split (locked 2026-06-06).
- Routing table + email-workflow verbs updated: `action this`, `actions`/`my actions` (walker), `de-tray this`; `task this` redefined Asana-only. Plan: Projects/PA-General/files/email-workflow-plan-2026-06-06-action-task-split.md.

Full version history. SKILL.md carries operational instructions only.

## v6.9 (2026-05-25 eve, Resume surfaces yesterday's PF journal lesson)

Resume Step 2 extended with Step 2a: read yesterday's PF journal entry, grep `## One lesson for tomorrow`, surface as `**Yesterday's lesson:** {line}` in the briefing. Skip silently if absent. Same source-of-truth + extraction pattern the morning daily-briefing now uses (canonical: [[pf-journal#Lesson-flow]]). Locked in tonight's first-journal refinement session — the lesson only earns its keep if it shapes the next day, and brain Resume is one of the three surfaces that carries it forward (alongside the 6pm reminder cron and the 07:30 morning briefing).

## v6.8 (2026-05-25, Garmin Resume handles twice-daily cron + PUSH FAILED tag)

Resume Step 2 updated to handle the now twice-daily `garmin-daily-pull` cron (07:00 + 17:00 Atlantic/Canary, extended 2026-05-25 for half-Ironman training prep — see [[Projects/PA-General/pete-health-dashboard]] v2 + [[garmin-api-configuration]] gotcha 9). The Garmin section in today's daily note may now carry **multiple lines** under `## Garmin daily pull (Automated)`; read the **most-recent line** for the freshest activity count. If that line carries a `| PUSH FAILED (…)` tag (added by the script after the 2026-05-25 rebase-before-push fix — see [[Library/lessons/2026-05-25-garmin-daily-pull-must-rebase-before-push]]), surface it as a warning. Cron preserves `signoff.confirmed` across runs, so a morning correction is never overwritten by the 17:00 pull.

## v6.7 (2026-05-24 eve, sign-off surfaced + correctable at Resume)

Resume Step 2 now also surfaces the Garmin **sign-off estimate** ("Last night you signed off ~HH:MM" — last Claude/Cowork session activity the night before, written by the `garmin-daily-pull` cron into the daily-note line) and invites a correction. On Pete's correction, run `garmin-daily-pull.py --set-signoff {today} HH:MM` (via Desktop Commander) to record the confirmed time — it wins over the estimate and updates the dashboard. Wording in SKILL.md (vault + plugin); repackaged + reinstalled. See [[Projects/PA-General/pete-health-dashboard]].

## v6.6 (2026-05-24 eve, Garmin recovery wording corrected to Garmin-native dating)

After the Garmin day-model was reverted to Garmin-native (no +1 recovery shift — see [[garmin-api-configuration]] gotcha 6), corrected Resume Step 2: the latest Garmin file is now **today's** (last night's sleep + today's readiness), not yesterday's. Resume surfaces it as `Last night (Garmin): ...`. Wording fixed in SKILL.md (vault + plugin); repackaged + reinstalled.

## v6.5 (2026-05-24, Garmin recovery surfaced at Resume)

Resume Step 2 extended: when reading recent daily notes, surface the `## Garmin daily pull (Automated)` section's headline (sleep score + qualifier + hours, HRV + status, training readiness, activity count) in the briefing as `Yesterday (Garmin): ...`. Full per-day file at `Personal/health/garmin/{yesterday}.md` available for deeper context. Reads from the daily-note line written by the 08:00 `garmin-daily-pull` cron — no SKILL.md-level "fetch Garmin" step needed, the existing daily-note read picks it up. Repackaged + reinstalled 2026-05-24. See [[garmin-api-configuration]].

## v6.4 (2026-05-20, SKILL.md slim-down)

Stripped v6.0-v6.3 version banner block from SKILL.md preamble (all four versions' operational rules were already in the body). Stripped inline `(NEW v6.0)`, `(NEW v6.1)`, `(v6.0)` tags scattered through the body. SKILL.md is now operational-only; CHANGELOG.md is the single home for version history. Same pass applied across all 13 vault skills today.

## v6.3 (2026-05-20, Compress mirrors vault-writer's session-end guarantees)

Compress now runs the same end-of-session guarantees as vault-writer, so they fire whichever skill closes the session:
- **Step 3 reinforced** with the structured-home sweep — for every topic touched, find its existing project/property/folder and update it with what changed + rationale; daily logs / CLAUDE.md / lessons are pointers only.
- **New Step 7a — Asana staleness sweep**: surface stale tasks (untouched >21d, long-overdue >14d, bloated undated clusters). Surface-only; never bulk-action without Pete's per-item confirmation.
- **New Step 7b — Asana ↔ vault project parity**: for every project touched this session, run the Full Sync Check Rules ([[asana-configuration]]) scoped to those projects; fix the vault side, surface Asana-side drift.

Mirrors vault-writer v5.4 / v5.5 / v5.6. Pete-directed 2026-05-20. See [[Library/lessons/2026-05-20-website-work-saved-to-structured-vault]].

## v6.2 (2026-05-17, Teaching Loop tightened — CLAUDE.md pointers for Pete-corrections only)

A lesson written from your own observation (methodology, code patterns, debugging insights, audit findings) goes into `Library/lessons/` without a pointer in CLAUDE.md. The lessons README index is sufficient discovery for non-correction lessons. Locks down ambiguity that let a code session drift on 17 May 2026. See [[Library/lessons/2026-05-17-claude-md-pointer-pete-corrections-only]].

## v6.1 (2026-05-13, Resume Pending line becomes cross-checked, not narrative)

**One change:** the Resume briefing's `Pending` line — historically derived by copying "carry-overs" / "Pending into next chunk" / "Pending Tasks" entries from yesterday's daily note + today's earlier session logs — now requires a per-gid live Asana check before any item is shown. Items that fail the check are silently dropped. The Δ block being live-queried does not exempt the Pending block from the same source-of-truth rule.

**Why:** 13 May 2026 evening Resume briefing listed "EU Skills wk5 £1,518.60 by Fri 15 May (Pravin stop)" as a live carry-over. Pete had already paid it; the Asana task (`1214591431289630`) was `completed: true` before the briefing was written. The Δ block at the top had been live-queried and correctly flagged wk4 as closed, but the carry-overs below it were inherited verbatim from yesterday's daily note. Pete called out the inconsistency: *"so why did you tell me they were open when we just started?"*

The first instinct was to add a new memory + a new CLAUDE.md bullet — Pete corrected that as CLAUDE.md bloat. The existing "Asana data must be live" rule already covers this; the gap is in the brain skill's Resume template, where the `Pending: items left over` line had no defined source and invited narrative inheritance.

**What changed in SKILL.md:**

1. **Step 2 (load daily notes)** now explicitly states daily notes are a SECOND source, used to spot drift against live Asana, never quoted forward into the briefing without a per-gid cross-check.
2. **Step 8 (briefing template)**:
   - `Pending: [[Project-Name]] -- [items left over]` → `Pending (cross-checked, not narrative): [items from daily-note pending blocks that survive a per-gid live Asana check]`
   - Added a "How to derive the Pending line" sub-block with the 6-step mechanic: collect candidates from daily notes → per-gid `GET /tasks/{gid}` → drop completed → refresh due-dates → silently drop items that don't survive → skip the line entirely if empty.
3. Top-of-file version block bumped to v6.1 with a one-paragraph rationale.

**Vault-writer comparison:** vault-writer's Step 3a already does the right thing at session end — it cross-reconciles daily-note `[ ]` items against live evidence (commit hashes, README updates, decision docs) and only strikes a line when evidence is positive; uncertain items get asked rather than auto-modified. v6.1 brings brain Resume into line with that pattern for the session-start direction.

**No new CLAUDE.md rule.** "Asana data must be live" (CLAUDE.md line 189) and "Live systems are the truth, not the daily log" (line 190) already cover this. The fix is in the skill, not the rulebook.

## v6.0 (2026-05-06, project consolidation + brain behaviour revisions)

Three bundled changes:

**1. Bare `/brain` reverts to Resume.** Pete clarified 2026-05-06: he uses `/brain` only at session start as a synonym for "resume" — he's never come back into a running session and re-typed `/brain`. The v5.1 "show routing table on bare invocation" rule was solving a problem that didn't exist for Pete's actual usage pattern. Reverted: bare `/brain` runs Resume; named verbs still route per the table.

**2. Resume picks up manually-added Asana tasks.** New step 3a: detect Asana tasks created since the last session by `created_by != Claude PAT` AND `assignee != Jane`, surface in the briefing under "Manual tasks since last session". These are tasks Pete adds directly in the Asana app (mobile, web) outside of a Claude session — Claude needs to absorb their context at session start so it doesn't blunder past them. Briefing template gets a new line.

**3. Compress closing nudge.** When Pete signals he's wrapping up ("ok thats it", "im done", "going to bed", etc.) and a meaningful body of work landed in the session, brain proactively nudges "want me to compress before you go?" rather than waiting for an explicit `/brain compress`. Honour his answer either way. Don't nudge mid-session, don't nudge after every quiet moment.

**Plus structural alignment with the project-consolidation restructure landing today:**
- Vault Structure block updated: 10 top-levels (was 12). Invoices/ folded into Projects/Team-Finances/. Delegated/ folded into Projects/Team-General/Delegated/. Accreditations/ now reference-only (tasks → Team-General/SY-General).
- SY-Clancy exception called out: vault content at Customers/SY-Clancy/, NOT Projects/SY-Clancy/, despite SY-Clancy having its own standalone Asana project. The only customer-as-Asana-project case.
- Vocab lock + parent/sub-project pattern documented. Default to General sub-project; don't sprawl new projects/sub-projects for 1-2 tasks. See [[Library/lessons/2026-05-06-ask-before-creating-projects]].

Decision file: [[Library/decisions/2026-05-06-project-consolidation]] for the full restructure rationale + mapping.

## v5.1 (2026-05-05, no-default-verb fix) — superseded by v6.0

- **Bare `/brain` no longer triggers Resume.** The Routing section now leads with a callout: if the user invokes `/brain` (or otherwise loads the skill) without naming a verb, show the routing table, ask what's wanted, and stop. Do not load Asana, do not read daily notes, do not pull Gmail, do not write to the daily file.
- **Why:** 2026-05-05 morning. Pete typed `/brain` and Claude immediately ran the full Resume workflow (MAP + vault-routing + project READMEs + 3 daily notes + Asana search + Gmail Cowork-Inbox query + daily-note write). Pete asked "why are you running resume when its a new session?" The skill's "If unclear, ask" rule existed but was buried as a closing line under the table; the v5.0 banner phrase "loaded at session start by Resume workflow" plus the description's "or runs /brain" trigger biased Claude into auto-Resume.
- **Fixes:**
  - SKILL.md Routing section gets a leading `> [!important]` callout that makes the no-default rule unmissable.
  - Routing table gets a final row "(nothing -- bare `/brain` invocation) -> Show this table and ask. Do not pick a default."
  - Description frontmatter now spells out the rule: "Bare `/brain` (no verb in the user's message) means SHOW THE ROUTING TABLE AND ASK -- never default to Resume or any other verb."
  - v5.0 banner reworded from "loaded at session start by Resume workflow" to "loaded by Resume workflow when invoked" to remove the session-start == Resume implication.
- No behaviour change to any verb workflow. Resume / Compress / Preserve / Daily Review / Task Management / Output Styles / Resources / Meeting Intelligence all unchanged. Only the bare-invocation default behaviour is fixed.

## v5.0 (2026-05-04 evening, iPhone -> Cowork bridge)

- **Resume workflow gains a new Step 6: Cowork-Inbox check.** At session start, brain queries `subject:"For Claude Cowork" in:inbox newer_than:30d` via the Gmail helper, surfaces matching threads in the briefing as "X incoming from your iPhone -- want to process now?". On confirmation, walks Pete through each: read body (standard shape: What / Where / Why / Done when / optional detail), propose a filing label based on content (existing labels -- not a dedicated Cowork-* lifecycle label per Pete's directive), execute the actual request (Asana task / vault write / audit / email / etc), then archive the thread under the chosen label. Steps 7-10 renumbered.
- **Why:** New iPhone <-> Cowork bridge. Pete's web/iPhone Claude can now hand work back to Cowork via email -- subject `For Claude Cowork: ...` triggers the pickup. Brain skill catches them at session start so iPhone-originated requests surface alongside Asana priorities. No dedicated Cowork-* labels (Pete's call) -- requests get filed under their content-appropriate existing label, like any other email.
- Resume briefing template gains a "From your iPhone (Cowork-Inbox)" line.

## v4.9 (2026-05-04 afternoon, same-day reconciliation pass)

- **Compress workflow gains a new Step 7: Same-day reconciliation pass.** Re-reads every prior `> [!todo] Pending Tasks` block in today's daily note plus any pending entries in same-day session plans. For each open `[ ]` task with positive evidence of having shipped (commit hash named in a later session log, README "recent commits" line, decision doc), closes the Asana task with a comment naming the evidence and replaces the `[ ]` line in-place with `[x]` + strikethrough + `**SHIPPED same-day as <evidence>**` marker. When uncertain, asks Pete instead of auto-modifying. Mirror logic also lives in vault-writer Step 3a (separate-but-parallel cleanup checklist guarantee).
- **Why:** Surfaced 2026-05-04. The morning 09:00-14:00 photo-sort session opened "Wire `x_studio_report_link` writeback" as TODO + created Asana 1214496261050040. The 12:30 detour bug-fix session shipped it as commit `ba02060`. End-of-session ran but never cross-referenced the morning's pending block against the 12:30 detour's commits; the Asana task sat open and the daily note still claimed `[ ] Wire X` next morning. Pete spotted three places of drift simultaneously. Lesson: [[Library/lessons/2026-05-04-same-day-reconciliation-gap]].
- Step 7 was previously a numbering gap (the existing flow went 1, 2, 3, 4, 5, 6, 8). Slot taken.
- CLAUDE.md gets a one-line pointer at the new entry under Rules.

## v4.8 (2026-05-03 night, post-Personal-section + brain-skill-review fold-in)
- Vault Structure diagram now correctly says 12 top-level sections (was 10/11). Personal/ + Screenshots/ added.
- Pre-flight Check `/setup` reference replaced with real fallback (the old branch was dead code).
- Five-system context loading replaced with pointer to vault-routing canonical 10-step protocol (1-6 auto, 7-10 on demand). Stops parallel maintenance.
- Task Management `projects: [array]` field name corrected to `project_gid: <gid>` singular (matches actual MCP signature).
- Compress Step 6 ("mirror property to Asana reference project") deleted -- reference projects archived since 2026-04-06.
- Steps 1-5 (Tier 1 factual fixes) from `[[Library/audits/2026-05-03-brain-skill-review]]`.
- C1-C6 fold-in (operational rules previously only in changelog now in body):
  - Sweep verb rule (single deliberate verb, manual only) -- General Guidelines + Routing + Skill Orchestration
  - Lessons folder usage convention (sessions can write lessons not just for corrections) -- General Guidelines
  - Scheduled tasks brain is aware of (new section listing 6 vault-touching crons + read-before-edit rule)
  - voice-principles routing on outbound text -- Routing + General Guidelines
  - finance-workflow routing on invoice/Soldo/Dext/Odoo/Xero/payroll/VAT -- Routing + General Guidelines
  - scripts-index awareness (don't reinvent helper scripts) -- General Guidelines
  - Bonus: connections.md awareness, Hub-content-index awareness, Email-workflow verbs row in Routing
- Skill Orchestration section added: brain explicitly hands off to inbox-triage, asana-gmail-sync, ahrefs-audit, audit-review, property-manager, simplify, vault-writer, frontend-design.
- Changelog (v4.0 through v4.7) extracted out of SKILL.md into this CHANGELOG.md to reduce per-session context bloat. SKILL.md header now just points here.

## v4.7 (2026-05-03 late evening, post-vault-rot-audit follow-ups)
- File access rule relaxed: removed the "never use Desktop Commander for vault operations" warning. DC is fine in Cowork and Claude Code; Read/Write/Edit are still preferred for vault files (faster, no path translation), but DC is no longer banned. When using DC for vault paths, always use the mounted folder path (`/Users/peterashcroft/Second Brain/...`), never the session-internal `/sessions/.../mnt/` path. Non-vault access (Shared Drives, My Drive) was already DC; that's unchanged.
- Auto-create rule extended to Properties. Properties are manually created today (no Asana-driven property creation), but if a session ever creates a property folder programmatically, it must complete README.md + data/ in the same operation, with `property_type:` in the frontmatter (`marketing-site|saas-app|internal-tool|external-data-source`). Same completeness model as projects/customers/suppliers.
- Teaching Loop rewritten. Old rule routed every correction inline into CLAUDE.md, which bloated it past 40KB before the 2026-05-03 audit. New rule routes by shape: one-liner sticky rules (no Why/How) append to CLAUDE.md; anything with structure (rule + Why + How) becomes `Library/lessons/{date}-{slug}.md` with a single-line pointer in CLAUDE.md. Default to lessons/ when in doubt. Mirror update in CLAUDE.md "Learn from corrections" rule and the General Guidelines bullet.

## v4.6 (2026-05-03, post-vault-rot-audit)
- Auto-Create Vault Folders rule expanded: now creates README.md + files/ (not just files/). Stub README template documented inline with required frontmatter (type, status, prefix, slug, asana_gid, asana_team_gid, created, updated, tags, category). The 2026-05-03 audit found 29 of 40 active project folders had folder + files/ but NO README; auto-create was silently incomplete.
- Same completeness rule for Customers / Suppliers folders (populate from `Library/templates/customer-readme-template.md`, never leave empty).
- Verification step added to auto-create: re-list every active Asana project after the run, surface any still-missing READMEs as "auto-create still pending" in the resume briefing.
- Compress / Save Session got Step 7 (onboarding-ritual completeness check). For any new project / customer / supplier / property folder created during the session, run the verification checklist in vault-routing.md before closing.
- Output style rule updated: no em dashes AND no double dashes (replace with full stops, commas, parens, colons). Pete's Preferences for Written Content section updated. Pointer to `[[voice-principles]]` added for outbound-text drafting.
- New `Library/lessons/` folder added 2026-05-03 (22 lessons extracted from old CLAUDE.md long sticky-rule entries). When a session produces a new behavioural rule with full Why/How structure, write it as `Library/lessons/{YYYY-MM-DD}-{slug}.md` and add a one-liner pointer to CLAUDE.md (don't expand CLAUDE.md inline). Index in MAP.md under `### Lessons`.
- New project frontmatter convention: `category: {seo|migration|build|marketing|ops|regulatory|general}`. Auto-create stub already includes it.
- New property frontmatter convention: `property_type: {marketing-site|saas-app|internal-tool|external-data-source}`. Resume Session briefing can use this to filter "active marketing sites" vs "active saas-apps" if Pete asks for either lens.
- New scheduled task `vault-drift-check` runs monthly (1st 07:00) and reports drift across READMEs, scheduled-task lockstep, orphan scripts, skill archive freshness. Don't shadow-run; let cron handle it.

## v4.5 (2026-04-25 late evening: Businesses/ tree + Gmail-as-truth awareness)
- Aware of the new Gmail `Businesses/{prefix}-{area}` tree (mirrors vault `Businesses/{name}/{area}/` -- Finance, Insurance, Vehicles per business). Same prefix colours as Customers/Suppliers/Projects.
- Aware of the new Gmail `General/{prefix}-General` tree (mirrors Asana general projects: SY/CD/AT/PA/EA-General).
- Gmail-as-truth principle: when reasoning about labels, filters, or modes, query Gmail live (`g.list_labels()`, `g.list_filters()`); apply categorisation rules from `gmail-label-scheme.md`. Never assume an enumerated list of labels exists in markdown.
- Sweep verb is `sweep` (single deliberate word, manual trigger only, no auto-offers from any skill -- accidental-trigger guard).
- EA-General Asana project added (gid `1214275930984986`, under Team El Atico).

## v4.4 (2026-04-24 -- vault-cleanup pass)
Routing logic extracted to `[[vault-routing]]` (single source of truth for routing). Removed: New Project/Property Workflow, New Customer/Supplier Workflow, Project/Property/Customer/Supplier Intelligence sections, business-mode routing table, Meeting Intelligence routing table (operational steps stay). Vault Structure diagram simplified to 10-section minimal + pointer. Added: top pointer to vault-routing, demand-driven project Gmail label rule, calendar defaults (Atlantic/Canary tz, Pete primary), five-system context-loading reference. Resume Session step 1 now loads vault-routing alongside CLAUDE.md + MAP.md + daily notes.

## v4.3 (2026-04-24 evening)
Five-system brain live (vault + Gmail + Asana + Calendar + Shared Drives). Vault expanded to **10 top-level sections**: added `Invoices/` (payables, moved from `Projects/`), `Accreditations/` (awarding bodies, moved from `Projects/`), `Delegated/` (follow-up tracking). Gmail got `Actions/P1-P4` traffic-light labels and top-level `Delegated` (purple `#8e63ce`). `03_Personal/*` migrated red → burgundy. Calendar joined the service-account DWD family -- all Calendar work via `Library/processes/scripts/calendar-api.py` (old Google Calendar MCP `9854eedd` superseded). New workflow verbs: `task this`, `delegate this`, `sync asana`, `triage`, `add to calendar`, `file all emails`. Full design: [[email-workflow-plan-2026-04-24]].

## v4.2 (2026-04-24)
Gmail API path documented -- `Library/processes/scripts/gmail-api.py` is now the canonical route for all Gmail work (search, read, send, draft, labels, attachments, filters, end-of-day sweep). Old built-in Gmail MCP and Zapier `gmail_send_email` superseded. Gmail vocabulary ("label as" / "file under" / "file all emails"), label-folder parity rule, and auto-filter "apply don't move" convention added. New Customer / New Supplier Workflow extended to include Gmail label creation and filter scaffold. Customer/supplier READMEs now carry `gmail_label` + `gmail_url` in frontmatter and an `## Email` section.

## v4.1 (2026-04-24)
`Customers/` and `Suppliers/` added as root-level folders for named customer and supplier relationships. Routing table expanded with eight new customer/supplier rows. New Customer/Supplier Intelligence sections added, parallel to Project/Property Intelligence. New Customer/Supplier workflow subsection added. Meeting Intelligence client-call row clarified -- notes stay in `Library/meetings/client-calls/` and are wikilinked from customer matters, not duplicated. Resume Session Step 1 extended to list current customer/supplier folders. DC positive rule added (non-vault paths via Desktop Commander).

## v4.0 (2026-04-22)
Vault restructure complete. `Departments/` + `Teams/` collapsed into `Businesses/`. `Intelligence/` + `Resources/` + `Assets/` + `Onboarding/` + `Skills/` consolidated into `Library/`. All routing tables, search paths, and end-of-session steps updated to reflect the new structure. Connector routing lives in `Library/processes/connections.md` -- single source of truth.
