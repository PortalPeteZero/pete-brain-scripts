# vault-check -- CHANGELOG

Append-only log of meaningful changes to this skill. Each entry: date + one-line summary. Add a new line above the existing entries when the SKILL.md is edited; bump the version stamp inside SKILL.md to match.

## 2026-05-20
- SKILL.md slim-down: stripped v1.1-v1.5 version banner block (~25 lines). Stripped inline "(NEW v1.4)" tag from Phase 9 heading. SKILL.md reads as current operational state; banners moved here.

## 2026-05-17
- Initial CHANGELOG.md backfill during the comprehensive vault audit (Phase 6 of the 2026-05-16 audit plan).
- Added external-service-routing pre-flight to SKILL.md.

## 2026-05-06 (v1.5)
- Project-consolidation alignment. Phase 1 expected_top_levels = 10 (Projects, Properties, Customers, Suppliers, Accreditations, Businesses, Personal, Library, Daily, Screenshots). Project completeness check understands parent + sub-project pattern. SY-Clancy exception preserved (vault content at `Customers/SY-Clancy/`). Phase 4 cron audit checks for refs to archived project gids. Phase 6 SOP audit treats Accreditations/SY-EUSR + SY-ProQual as reference-only.

## 2026-05-04 afternoon (v1.4)
- New Phase 9 — daily-note pending-tasks drift sweep. Scans last 14 daily notes for `> [!todo] Pending Tasks` blocks; cross-references against live Asana state + same-day commits/READMEs. Existing Phase 9 (Compile report + fix plan) renumbered to Phase 10. Lesson: [[Library/lessons/2026-05-04-same-day-reconciliation-gap]].

## 2026-05-03 night (v1.3)
- Phase 1 em-dash flag stripped (rule lives in [[voice-principles]] only; internal vault md exempt). New top-of-skill "Execution, READ THIS FIRST" block: every long-running step uses Desktop Commander, not workspace bash (45-second cap).

## 2026-05-03 night (v1.2)
- TitleCase lock-in for `Personal/family/` + `Businesses/sygma-solutions/owner-private/` (matches Drive). Phase 2 case-collision detection added.

## 2026-05-03 night (v1.1)
- Phase 2 drift-check has 3 CLI modes (--map-only, --quick, full). Vault↔Drive parity uses file counts + cumulative size, not path-level diff.
