---
type: index
name: Skills Library
updated: 2026-06-25
tags: [skills, index, registry]
---

# Skills Library

Canonical home for every custom skill Pete uses. Each skill lives in its own folder under this directory with a `SKILL.md` (the skill definition), a `CHANGELOG.md` (full version history), and optionally a `references/` or `scripts/` folder.

> **SKILL.md convention (locked 2026-05-20)**: each skill's `SKILL.md` carries **current operational instructions only**. Version banners, inline `(vX.Y)` tags, `(NEW)` markers, and historical commentary belong in the sibling `CHANGELOG.md`. The skills-slim-down pass on 2026-05-20 stripped ~40KB of accumulated version history across all 13 skills. Pete's reasoning: long preambles eat context budget every invocation, slow execution, and let stale rules drift back into the operational sections. SKILL.md should read as if written for someone landing fresh, with the lesson `[[Library/lessons/2026-05-20-skill-md-current-state-only-history-in-changelog]]` documenting the policy.

Installing a skill elsewhere (for example via Claude Code plugin marketplaces or the Cowork skill installer) is fine. This folder is the **source of truth** that those installations are built from. **When a skill is updated here, run `package-skill.py <name>`** — it rebuilds the `.skill` archive in lockstep with the source AND delivers the current package to `~/Downloads/cc-skills-to-install/`, ready to install in Cowork. Never hand-zip a `.skill` (that drifted: Downloads held pre-repackage versions). See **Packaging & delivery** below.

> **Post-cutover state (24–25 Jun 2026)** — two system-wide changes postdate most dated entries below. (1) **Pete's tasks moved off Asana to the CC `public.tasks` table** (Asana is Jane's only now). (2) The triage verbs were renamed to plain words — **Reply / Task / Reply+Task / Hand to / File / Keep / Clear / Skip** — and the Gmail `Actions` label became **`Replies`**. So where a dated description below says "Asana task", "Delegate to", or "Actions label", read it as "CC `public.tasks`", "Hand to", "Replies". Each skill's `SKILL.md` is the current source of truth; the canonical workflow is [[email-workflow]].

## Active Skills

| Skill | Version | Runs in | Purpose |
|---|---|---|---|
| `brain/` | v6.3 (2026-05-20) | Cowork + Claude Code | Vault awareness layer (sessions, reviews, tasks, memory, meetings, output styles). Multi-system context loading. **2026-05-20 (v6.3)**: Compress mirrors vault-writer's session-end guarantees so they run whichever skill closes the session — Step 3 structured-home sweep, new Step 7a Asana staleness sweep, new Step 7b Asana ↔ vault project parity. **2026-05-17 (v6.2)**: Teaching Loop tightened — CLAUDE.md pointers are for Pete-corrections ONLY. Non-correction lessons (methodology, code patterns, audit findings, observations) stay in `Library/lessons/` without a pointer in CLAUDE.md; lessons README is the discovery surface for those. Closes ambiguity that let a code session drift. See [[Library/lessons/2026-05-17-claude-md-pointer-pete-corrections-only]]. **(v6.1)** Resume Pending line is a cross-checked output, not a copy-paste source. **(v6.0)** bare `/brain` reverts to Resume; Resume Step 3a manual-task pickup; Compress closing nudge; 10 top-levels; SY-Clancy exception; parent/sub-project pattern. **(v5.0)** Resume Step 6 iPhone -> Cowork bridge. **(v4.9)** Compress Step 7 same-day reconciliation pass. |
| `vault-writer/` | v5.6 (2026-05-20) | Cowork + Claude Code | End-of-session cleanup checklist. **2026-05-20 (v5.6)**: new Step 3c — per-session Asana ↔ vault project parity check (Full Sync Check Rules scoped to touched projects: folder/README/files + section↔sub-folder parity + name match + no orphans + state parity); fix vault side, surface Asana drift. **2026-05-20 (v5.5)**: new Step 3b — session-end scan of the whole Asana workspace for stale work (untouched >21d, long-overdue >14d, bloated undated clusters) surfaced as a digest; surface-only, never bulk-action without confirmation; mirror in brain Compress. **2026-05-20 (v5.4)**: Step 2 opens with a structured-home discovery sweep for the WHOLE session — for everything touched, find the existing project/property/folder and update it with what changed + rationale; daily logs/CLAUDE.md/lessons are pointers only. Generalises [[Library/lessons/2026-05-20-website-work-saved-to-structured-vault]]. **2026-05-06 (v5.3)**: aware of project consolidation -- Invoices/+Delegated/ folded into Projects/Team-* parents; Accreditations/ reference-only; SY-Clancy exception; parent + sub-project vault pattern (sub-project subfolders direct under parent, not inside parent/files/); default to General sub-project for new tasks. **(v5.2)** Step 8 + Step 9 unified via Desktop Commander. **(v5.1)** Step 3a same-day reconciliation. **(v5.0)** TitleCase lock-in for 2-way sync. |
| `inbox-triage/` | v1.11 (2026-05-06) | Cowork + Claude Code | Interactive inbox walker. Verb `triage`. **Verb `Hand to {person}`** (renamed from `Delegate to` on 2026-06-25) creates a CC task (`public.tasks`) under `project_slug='Team-General'`, tagged `delegated`. Routing chain follows `email-task-sync`. **(v1.10)** TitleCase enrichment paths. |
| `email-task-sync/` | 2026-06-25 | Cowork + Claude Code | Reconciliation engine (formerly `asana-gmail-sync`). Verb `sync`. Reconciles Gmail `Replies`/`Delegated` labels ↔ CC `public.tasks` (Pete off Asana 24 Jun 2026). **Earlier Asana-era history (v1.7, 2026-05-06)**: routing chain rewritten for project consolidation. Step 5 Delegated now points at Team-General/Delegated section. Step 6 orphan handling: Customers/Suppliers → Team-General/`{prefix}-General` section (SY-Clancy exception → standalone SY-Clancy project). Invoices/* → Team-Finances/`{prefix}-Invoices` section. Accreditations/* → Team-General/SY-General. Articles labels → website parents' `articles` sections. Default to General sub-project; ask before sprawling new sub-projects. **(v1.6)** PA- consolidation. |
| `ahrefs-audit/` | v2.4 (2026-05-06) | Claude Code | Combined Ahrefs + Surfer + GSC page audit and optimisation plan. **2026-05-06 (v2.4)**: property → project mapping updated for restructure -- SEO is a sub-project (Asana section) under the website parent, not its own standalone project. New mapping uses `{Parent} / seo` paths (Projects/SY-Website/seo/, Projects/CD-Website/seo/, Projects/CD-Other-Sites/{site-slug}/). **(v2.3)** Other-Sites consolidation. |
| `audit-review/` | v2.4 (2026-05-06) | Claude Code | Fortnightly SEO review. **2026-05-06 (v2.4)**: same restructure alignment as ahrefs-audit -- property mapping updated to parent + sub-project pattern. SEO Page Tracker still in property README's `data/`; review tasks file in parent project's `seo` section. **(v2.3)** Other-Sites consolidation. |
| `property-manager/` | v2.9 (2026-05-06) | Claude Code | Universal workflow for working on any website/app. **2026-05-06 (v2.9)**: aware of parent + sub-project structure. When opening a property, walk parent project AND active sub-project READMEs (e.g. SY-Website/seo, SY-Website/articles). Don't propose new projects when work fits existing parent + sub-project home; default to filing under existing structure. **(v2.8)** dash-rule strip. |
| `simplify/` | v3.2 (2026-05-03) | Claude Code | Multi-agent code review (three parallel agents check for reuse, quality, efficiency). Reports findings and applies fixes. **2026-05-03**: dash rule statement updated. |
| `frontend-design/` | v1.0 (2026-04-25) | Cowork + Claude Code | Distinctive, production-grade frontend interface design. |
| `vault-check/` | v1.5 (2026-05-06) | Cowork + Claude Code | Thorough vault audit. **2026-05-06 (v1.5)**: Phase 1 expected_top_levels = 10 (Projects, Properties, Customers, Suppliers, Accreditations, Businesses, Personal, Library, Daily, Screenshots). Project completeness check understands parent + sub-project pattern. SY-Clancy exception preserved. Phase 4 cron audit checks for refs to archived project gids. **(v1.4)** Phase 9 daily-note TODO drift sweep. **(v1.3)** dash-flag strip + DC execution rule. |
| `cd-cost-report/` | v1.0 (2026-05-11) | Cowork + Claude Code | CD cost-base report — monthly + weekly cost burn analysis combining Odoo baseline (averaged-from-history) + per-period actuals + Xero Sygma extras + manual casual labour. Trigger: "run CD cost report", "regenerate cost report", "what's our burn rate", "CD cost-base for [period]". Outputs tabbed HTML at `Businesses/canary-detect/finance/cost-base-reports/2026-cost-base-YTD.html` + public mirror at **https://cd-cost-base.vercel.app** (Vercel project `sygma1/cd-cost-base`). Hard rule: no staff names + no tax-sensitive language in public output. Scripts: `Library/skills/cd-cost-report/scripts/{build_data,render_html}.py`. |
| `sygma-health-report/` | v1.0 (2026-05-20) | Cowork + Claude Code | Combined multi-source website health report for sygma-solutions.com. Pulls Ahrefs (DR + Rank Tracker + 7-day per-keyword trajectory), Surfer (content score vs competitors), GSC (site + per-page, 28d), GA4 (traffic/sources/conversions, 28d), Google Ads (ad-group + landing-page + 7-day spend, 30d). Deep-dives the 4 same-course cluster pages (EUSR CAT1, Cat & Genny, Cable Avoidance, HSG47; editable via `PAGES` in the script). Trigger: "run the sygma report", "sygma health report", "how's the sygma website", "is it moving". Output: dated Markdown at `Properties/Sygma Solutions Website/data/health-report-{date}.md`. Read-only (website carve-out); honours hsg47-explained no-work + no-backlinks rules. Script: `Library/skills/sygma-health-report/scripts/build_report.py`. |
| `finance-filing/` | v1.0 (2026-06-17) | Cowork + Claude Code | The reconciling finance verb — "add to personal finance" / "this is Sygma finance" / "file under CD finance" / "finance this". Routes by **entity decided from content** (Sygma/CD/Personal); classifies addition/edit/change/duplicate; **asks** when ambiguous, **splits** when one item spans two; enforces the Sygma owner-private payroll/accounts carve-out; enriches + tasks-only-on-action + appends the entity finance-ledger. Routing source of truth = [[Library/processes/vault-routing#finance-routing-by-entity--sygma--canary-detect--personal]]. Built household-finance-system plan Phase 3. |

## Retired

| Skill | Retired | Notes |
|---|---|---|
| `seo-gap-analysis` | 2026-04-18 | Absorbed into `ahrefs-audit` v2.0. No replacement needed. |

## Packaging & delivery

The single packager `package-skill.py` (in `pete-brain-scripts`, run with `VAULT=/tmp/pbs`) keeps the source folder and the `.skill` archive in lockstep AND hands the installable package to Pete. It zips the folder's **contents** (so `SKILL.md` sits at the archive root, never nested), and copies the result to `~/Downloads/cc-skills-to-install/` with an `_INSTALL-ME.md` manifest.

```
package-skill.py <name> [<name> ...]   # repackage + deliver the named skills
package-skill.py --changed             # only skills whose source ≠ its .skill (content-compare)
package-skill.py --all                 # every skill
package-skill.py --no-deliver ...      # rebuild the archive(s) only, skip local delivery
```

- **Change detection is content-based** (ignores zip timestamps), so `--changed` rebuilds only genuinely-edited skills and surfaces any pre-existing drift (it caught 2 stale + 2 malformed archives on 2026-06-25).
- **Delivery is automatic when `~/Downloads` exists** (a local Mac session); on a cloud/Railway run it skips delivery but still rebuilds the archive, so source and package never drift.
- After editing a skill: `package-skill.py <name>` → install the delivered package in Cowork → empty the folder.

## Installing

1. Run the packager (above) — the current `.skill` lands in `~/Downloads/cc-skills-to-install/`.
2. Install each `.skill` via the Cowork or Claude Code skill installer (replaces the installed version).
3. Update `Library/processes/connections.md` if connector requirements change.
4. Update this README when version bumps land.

## How skills relate to connections

Skills use MCP connectors and direct APIs. The routing rules and account-level credentials for those connectors live in `Library/processes/connections.md`. Skill files should reference connections.md for how to authenticate, and `Properties/{Name}/README.md` for property-specific IDs (Ahrefs project ID, Surfer workspace ID, Supabase project ref, Vercel project name, etc.).

## History

- 2026-04-22 -- README created as part of vault restructure Phase 7. Replaces the skills section of the now-deleted `skill-registry.md`.
