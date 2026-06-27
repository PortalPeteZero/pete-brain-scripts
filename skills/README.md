---
type: index
name: Skills Library
updated: 2026-06-25
tags: [skills, index, registry]
---

# Skills Library

Canonical home for every custom skill Pete uses. Each skill lives in its own folder under this directory with a `SKILL.md` (the skill definition), a `CHANGELOG.md` (full version history), and optionally a `references/` or `scripts/` folder.

> **SKILL.md convention**: each skill's `SKILL.md` carries **current operational instructions only**. Version banners, inline `(vX.Y)` tags, `(NEW)` markers, and historical commentary belong in the sibling `CHANGELOG.md`. Long preambles eat context budget every invocation, slow execution, and let stale rules drift back into the operational sections. SKILL.md should read as if written for someone landing fresh.

Installing a skill elsewhere (for example via Claude Code plugin marketplaces or the Cowork skill installer) is fine. This folder is the **source of truth** that those installations are built from. **When a skill is updated here, run `package-skill.py <name>`** — it rebuilds the `.skill` archive in lockstep with the source AND delivers the current package to `~/Downloads/cc-skills-to-install/`, ready to install in Cowork. Never hand-zip a `.skill`. See **Packaging & delivery** below.

## Active Skills

| Skill | Version | Runs in | Purpose |
|---|---|---|---|
| `brain/` | v6.3 | Cowork + Claude Code | Command Centre session layer (sessions, reviews, tasks, memory, meetings, output styles). Multi-system context loading. Full history: [[CHANGELOG]]. |
| `vault-writer/` | v5.6 | Cowork + Claude Code | End-of-session cleanup checklist (structured-home sweep, task staleness, task↔project parity). Full history: [[CHANGELOG]]. |
| `inbox-triage/` | v1.11 | Cowork + Claude Code | Interactive inbox walker. Verb `triage`. `Hand to {person}` creates a CC task (`public.tasks`) under `project_slug='Team-General'`, tagged `delegated`. Full history: [[CHANGELOG]]. |
| `email-task-sync/` | 2026-06-25 | Cowork + Claude Code | Reconciliation engine. Verb `sync`. Reconciles Gmail `Replies`/`Delegated` labels ↔ CC `public.tasks`. Full history: [[CHANGELOG]]. |
| `ahrefs-audit/` | v2.4 | Claude Code | Combined Ahrefs + Surfer + GSC page audit and optimisation plan. Full history: [[CHANGELOG]]. |
| `audit-review/` | v2.4 | Claude Code | Fortnightly SEO review. SEO Page Tracker on the property's CC card; review tasks under the property's project. Full history: [[CHANGELOG]]. |
| `property-manager/` | v2.9 | Claude Code | Universal workflow for working on any website/app. Full history: [[CHANGELOG]]. |
| `simplify/` | v3.2 | Claude Code | Multi-agent code review (three parallel agents check for reuse, quality, efficiency). Reports findings and applies fixes. Full history: [[CHANGELOG]]. |
| `frontend-design/` | v1.0 | Cowork + Claude Code | Distinctive, production-grade frontend interface design. |
| `vault-check/` | v1.5 | Cowork + Claude Code | Thorough system audit (skills, crons, processes/APIs, cloud-homes health, CLAUDE + MAP, daily-log drift). Full history: [[CHANGELOG]]. |
| `cd-cost-report/` | v1.0 | Cowork + Claude Code | CD cost-base report — monthly + weekly cost burn combining Odoo baseline + per-period actuals + Xero Sygma extras + manual casual labour. Triggers: "run CD cost report", "what's our burn rate", "CD cost-base for [period]". Outputs tabbed HTML to Canary Detect's finance Drive folder + public mirror at **https://cd-cost-base.vercel.app** (Vercel project `sygma1/cd-cost-base`). Hard rule: no staff names + no tax-sensitive language in public output. Scripts: `skills/cd-cost-report/scripts/`. |
| `sygma-health-report/` | v1.0 | Cowork + Claude Code | Combined multi-source website health report for sygma-solutions.com — Ahrefs + Surfer + GSC + GA4 + Google Ads. Deep-dives the 4 same-course cluster pages (EUSR CAT1, Cat & Genny, Cable Avoidance, HSG47). Triggers: "run the sygma report", "how's the sygma website". Output: dated Markdown to the Sygma Solutions Website Drive folder. Read-only (website carve-out); honours hsg47-explained no-work + no-backlinks rules. Script: `skills/sygma-health-report/scripts/build_report.py`. |
| `finance-filing/` | v1.0 | Cowork + Claude Code | The reconciling finance verb — "add to personal finance" / "this is Sygma finance" / "file under CD finance" / "finance this". Routes by **entity decided from content** (Sygma/CD/Personal); classifies addition/edit/change/duplicate; **asks** when ambiguous, **splits** when one item spans two; enforces the Sygma owner-private payroll/accounts carve-out; enriches + tasks-only-on-action + appends the entity finance-ledger. Routing source of truth = [[vault-routing#finance-routing-by-entity--sygma--canary-detect--personal]]. |

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
3. Update [[connections]] if connector requirements change.
4. Update this README when version bumps land.

## How skills relate to connections

Skills use MCP connectors and direct APIs. The routing rules and account-level credentials for those connectors live in [[connections]]. Skill files should reference connections for how to authenticate, and the property's **CC card** for property-specific IDs (Ahrefs project ID, Surfer workspace ID, Supabase project ref, Vercel project name, etc.).

## History

- 2026-04-22 -- README created as the canonical skills index.
