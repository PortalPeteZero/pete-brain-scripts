# ahrefs-audit -- CHANGELOG

Append-only log of meaningful changes to this skill. Each entry: date + one-line summary. Add a new line above the existing entries when the SKILL.md is edited; bump the version stamp inside SKILL.md to match.

## 2026-06-30
- Same as audit-review — read the live card instead of the frozen property→project→ID table; removed all CD-Other-Sites / `Projects/.../seo/` refs; STOP-on-null contract.

## 2026-05-20
- SKILL.md slim-down: stripped v2.1-v2.4 version banners + trailing "Skill version 2.0" footer. Lifted v2.2's property_type + page-seo-workflow guidance into the body where it belongs operationally. Stripped inline "(v2.1)" tag on the GSC API note. SKILL.md now reads as current state without historical commentary.

## 2026-05-17
- Initial CHANGELOG.md backfill during the comprehensive vault audit (Phase 6 of the 2026-05-16 audit plan).
- Added external-service-routing pre-flight to SKILL.md (skills should consult [[external-service-routing]] before reaching for any external-service tool).

## 2026-05-06 (v2.4)
- Property → project mapping rewritten for project-consolidation restructure. SEO is now a sub-project (Asana section) under the website parent, not its own standalone project. Mapping format `{Parent project} / seo`. Old standalone SEO projects (CD-Canary-Detect-Website-SEO, Sygma-Solutions-Website-SEO, CD-Other-Sites-SEO) renamed/folded into CD-Website / SY-Website / CD-Other-Sites parents.

## 2026-05-03 (v2.3)
- Property mapping consolidation: The Leaky Finders, LeakGuard Lanzarote, Pipebusters, Leakbusters all moved under CD-Other-Sites-SEO. Pipebusters and Leaky Finders added to the mapping table.

## 2026-05-03 (v2.2)
- Aware of new property frontmatter convention: properties carry `property_type:` (vocabulary at [[vault-routing#property-type-vocabulary]]). This skill primarily applies to `property_type: marketing-site` properties. Reusable per-page SEO workflow lives at [[page-seo-workflow]] (referenced from each property README).

## 2026-04-22 (v2.1)
- Direct GSC API added as a primary data source alongside Ahrefs. Vault path migration: `Intelligence/processes/*` → `Library/processes/*`.

## 2026-04 (v2.0)
- Added Surfer Content Intelligence via direct API. Introduced cross-reference analysis and balanced planning philosophy. Surfer scores are signals, not targets. Absorbed old `seo-gap-analysis` skill functionality.

## Pre-CHANGELOG history
This skill predates the CHANGELOG convention. Version history before 2026-05-17 lives in:
- The skill's `SKILL.md` version header (if it has one)
- Daily/ session logs (Daily/YYYY-MM-DD.md) for major change days
- Library/decisions/ entries for design pivots
