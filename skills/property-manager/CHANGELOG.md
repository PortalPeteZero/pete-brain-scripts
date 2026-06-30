# property-manager -- CHANGELOG

Append-only log of meaningful changes to this skill. Each entry: date + one-line summary. Add a new line above the existing entries when the SKILL.md is edited; bump the version stamp inside SKILL.md to match.

## 2026-06-30
- Step 2a guard: the Work Log is HISTORY, not current state — always read current state from the live source first (repo / live page / CC card / Ahrefs); use the Work Log only to orient on what we last did + whether it worked. See [[work-log]].

## 2026-06-29
- Step 6f² — log every shipped change to the Work Log via `worklog.py` the moment 6f verification passes; Verification-checklist gate (no code step "done" without its row) + §7b routing-table & Dos/Don'ts rows. The EUSR-class fix. See [[work-log]].

## 2026-06-08
- §6f Post-Merge Verification gains scripted live-verification via the new `browser-api.py` Playwright helper (`audit` / `check` against the deployed URL) alongside curl + Preview. "Read the console" safeguard + the UI-change checklist line now point at it too. See [[browser-api-configuration]] + [[Library/decisions/2026-06-08-playwright-direct-browser-helper]].

## 2026-05-20
- SKILL.md slim-down: stripped v2.7-v2.9 version banners from preamble. Lifted v2.8's "voice-principles only for outbound; internal artefacts exempt" + v2.9's parent + sub-project routing guidance into the intro paragraph where they belong operationally.

## 2026-05-17
- Initial CHANGELOG.md backfill during the comprehensive vault audit (Phase 6 of the 2026-05-16 audit plan).
- Added external-service-routing pre-flight to SKILL.md.

## 2026-05-06 (v2.9)
- Project-consolidation alignment. Project folder is the parent (e.g. `Projects/SY-Website/`); channel-specific files live in sub-project subfolders (e.g. `Projects/SY-Website/seo/files/`). Opening a property reads the property README + parent project README + active sub-project READMEs.

## 2026-05-03 (v2.8)
- Stripped vault-wide dash-rule duplication. Outbound communication style rules consolidated in [[voice-principles]]. Internal artefacts (PRs, commits, README writes, audit reports, code comments) exempt from dash enforcement.

## 2026-04-22 (v2.7)
- Vault restructure: all `Intelligence/processes/*` references updated to `Library/processes/*`. Account-level tokens live in `Library/processes/` only; property READMEs hold property-specific IDs only.
