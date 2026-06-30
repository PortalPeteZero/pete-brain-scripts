# cd-cost-report -- CHANGELOG

Append-only log of meaningful changes to this skill. Each entry: date + one-line summary. Add a new line above the existing entries when the SKILL.md is edited; bump the version stamp inside SKILL.md to match.

## 2026-06-30
- `Library/processes/odoo-api-configuration.md` (deleted) → the CC `odoo-api-configuration` note.

## 2026-05-20
- **Fixed stale sandbox-path gap**: `render_html.py` had a hardcoded `/sessions/wizardly-blissful-cannon/...` path from a previous Cowork session (would have failed next run). Replaced with `VAULT_ROOT` env-var pattern (default `/Users/peterashcroft/Second Brain`; override via env in sandbox). Same fix applied to SKILL.md "How to run" code blocks. Found during skills audit pass.

## 2026-05-17
- Initial CHANGELOG.md backfill during the comprehensive vault audit (Phase 6 of the 2026-05-16 audit plan).
- Added external-service-routing pre-flight to SKILL.md.

## 2026-05-11 (v1.0)
- Initial release. Generates CD cost-base monthly + weekly burn analysis with baseline (averaged-from-history) vs per-period actuals from Odoo + Sygma intercompany extras from Xero + manual cash items. Outputs tabbed HTML report locally + Vercel mirror at https://cd-cost-base.vercel.app. Anti-patterns locked: no casual-worker names in public output; no tax-sensitive descriptors; Sygma extras distributed into proper buckets not lumped.

## Pre-CHANGELOG history
This skill predates the CHANGELOG convention. Version history before 2026-05-17 lives in:
- The skill's `SKILL.md` version header (if it has one)
- Daily/ session logs (Daily/YYYY-MM-DD.md) for major change days
- Library/decisions/ entries for design pivots
