# Changelog — sygma-health-report

## v1.1 — 2026-06-08
- **Dropped Surfer entirely** (Pete's call — this is his most-used report and he doesn't need Surfer content scores on it). `POST /audits` had been returning HTTP 422 "Quota exceeded" for the whole billing period — Surfer's audit allowance is metered + monthly — so the four audits silently returned `None` and the scorecard showed "—" every run (broken "for ages"). Removed: the `pull_surfer()` pull, the `surfer()` helper + `SURFER_KEY`, the scorecard "Surfer (vs comp max)" column, the per-page Surfer score in the stdout headline, the "Surfer score ≠ ranking" note, and the Surfer source-line / guardrail / doc references. Report now pulls **four** sources (Ahrefs · GSC · GA4 · Google Ads) and runs in **~12s** (was 1–3 min). Root-cause investigation + fix options recorded in [[surfer-api-configuration]] § Quota & limits.

## 2026-05-20 (later — same day as v1.0)
- **Fixed sandbox-path gap**: `scripts/build_report.py` had a hardcoded `VAULT = "/Users/peterashcroft/Second Brain"` that worked on Pete's Mac but not in Cowork sandbox. Added `os.environ.get("VAULT_ROOT", ...)` so the script reads the env override when present. Same fix applied to the SKILL.md "Execution" code block. Mirrors the cd-cost-report fix made the same day.

## v1.0 — 2026-05-20
- Initial release. Combined multi-source health report for sygma-solutions.com.
- Pulls Ahrefs (DR + Rank Tracker + 7-day per-keyword trajectory), Surfer (content score vs competitors), GSC (site + per-page, 28d), GA4 (traffic/sources/conversions, 28d), Google Ads (ad-group + landing-page + 7-day spend, 30d).
- Deep-dives the four same-course cluster pages: EUSR CAT1, Cat & Genny, Cable Avoidance, HSG47 (editable via `PAGES` in `scripts/build_report.py`).
- Output: dated Markdown report to `Properties/Sygma Solutions Website/data/health-report-{date}.md` + stdout headline.
- Read-only (website carve-out): no code changes, no agents. Honours the hsg47-explained no-work rule and the no-backlinks rule.
- Born out of the 20 May 2026 manual multi-source review session.
