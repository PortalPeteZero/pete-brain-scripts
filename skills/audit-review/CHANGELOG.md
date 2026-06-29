# audit-review -- CHANGELOG

Append-only log of meaningful changes to this skill. Each entry: date + one-line summary. Add a new line above the existing entries when the SKILL.md is edited; bump the version stamp inside SKILL.md to match.

## 2026-06-29
- Phase 1e — close the loop: write the position-movement verdict back to the Work Log row's outcome (unknown/too-early → worked / no-change / regressed). See [[work-log]].

## 2026-05-20
- SKILL.md slim-down: stripped v2.1-v2.4 version banners + trailing v2.0 footer. Lifted v2.2's `property_type` + property-README pre-read guidance into the body intro where it belongs operationally.

## 2026-05-17
- Initial CHANGELOG.md backfill during the comprehensive vault audit (Phase 6 of the 2026-05-16 audit plan).
- Added external-service-routing pre-flight to SKILL.md.

## 2026-05-06 (v2.4)
- Property → project mapping rewritten for project-consolidation restructure. SEO is a sub-project (Asana section) under the website parent, mapping format `{Parent project} / seo`. Old standalone SEO projects folded into CD-Website / SY-Website / CD-Other-Sites parents.

## 2026-05-03 (v2.3)
- Property mapping consolidation: The Leaky Finders, LeakGuard Lanzarote, Pipebusters, Leakbusters all under CD-Other-Sites-SEO.

## 2026-05-03 (v2.2)
- Aware of `property_type:` frontmatter convention. Reviews apply primarily to `property_type: marketing-site` properties.

## 2026-04-22 (v2.1)
- Direct GSC API added as primary data source (impressions, clicks, CTR, true position by query). Vault path migration: `Intelligence/processes/*` → `Library/processes/*`.

## 2026-04 (v2.0)
- Surfer re-audits now run via API (audit endpoint + editor PATCH/score). NLP term gap tracking automated. LeakGuard Lanzarote added to property mapping.
