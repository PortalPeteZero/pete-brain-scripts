# asana-gmail-sync -- CHANGELOG

Append-only log of meaningful changes to this skill. Each entry: date + one-line summary. Add a new line above the existing entries when the SKILL.md is edited; bump the version stamp inside SKILL.md to match.

## 2026-06-06
- **Action/Task split** (plan: Projects/PA-General/files/email-workflow-plan-2026-06-06-action-task-split.md): Step 4 documents both exemptions ([no-sync-close] marker — now also the Asana-only class; Team-Finances blanket) + closure audit comments (wrapper implements). Step 5 delegation closures get the same comment. Step 6: orphans are tray-class by definition (no marker) + routing chain now points at vault-routing#task-routing-decision-tree. New Cron-mode section (daily 07:15 run: no questions, suggestions → daily note, best-match routing only, 2-strike failure escalation).

## 2026-05-20
- SKILL.md slim-down: stripped all v1.0-v1.7 version banners from the preamble; stripped inline (v1.x), (locked DATE), (NEW), (rewritten DATE) tags throughout. Body now reads as current state. History captured below.
- Lifted multi-thread closure rule into Step 4 body ("close task only when ALL linked threads have lost BOTH Actions AND Delegated"). Previously this was only in the deleted v1.4 banner.
- Lifted smart task-name rule into Step 6 body ("action verb + WHO + WHAT, not raw subject"). Previously only in the deleted v1.4 banner.
- Earlier same day: added Step 0 wrapper enforcement (`sync-asana.py`); both-sides query in Step 1; Mimestream + Gmail + Finder links in Step 6 task notes; MUST-call enricher in Step 6.

## 2026-05-17
- Initial CHANGELOG.md backfill during the comprehensive vault audit (Phase 6 of the 2026-05-16 audit plan).
- Added external-service-routing pre-flight to SKILL.md.

## 2026-05-06 (v1.7)
- Project consolidation restructure: ~30 active projects → 16 + 3 templates. All routing chains rewritten:
  - Step 5 Delegated: standalone Delegated project (gid `1214255292794724`) archived; delegations now in Team-General/Delegated section.
  - Step 6: Customers/Suppliers SY-/CD-/EA- → Team-General/{prefix}-General section. AT- stays standalone (Ashcroft Family team). Invoices → Team-Finances/{prefix}-Invoices. Accreditations → Team-General/SY-General. Articles labels → website parents' articles sections.
  - SY-Clancy exception preserved: Customers/SY-Clancy threads route to the standalone SY-Clancy project (gid `1214277900941306`).
  - Vocab lock: project = top-level Asana project + vault folder + README; sub-project = section under parent + vault subfolder direct under parent + own README.

## 2026-05-03 night (v1.6)
- PA- consolidation: 4 standalone PA- projects archived. Personal-team work consolidated into PA-General with sections (Scouts, Los Claveles, Freemasonry, Finance).

## 2026-05-03 evening (v1.5)
- Step 5 demand-driven label suggestions: AT- labels now map to vault `Personal/family/`. Personal/PA-* labels stay demand-driven.

## 2026-04-25 late evening (v1.4)
- Step 6 routing simplified to 3 checks. Businesses/{prefix}-{area} labels understood. Smart task generation rule. Multi-thread closure rule (Step 4). Vault enrichment via `vault-enricher.py`. Step 8 Gmail-as-truth + categorisation-aware report. NO sweep auto-offer.

## 2026-04-25 evening (v1.3)
- Read-content-before-route rule. Matter-granularity rule. Vault enrichment introduced. (Superseded by v1.4 routing.)

## 2026-04-25 (v1.2)
- Single Actions label model: collapsed Actions/P1-P4 sub-labels into one top-level Actions. Priority moves to Asana custom field. Step 2 became no-op.

## 2026-04-24 (v1.1)
- Auto-create Asana tasks for orphans (no asking). Auto-due-date schedule by priority. Bidirectional close.

## 2026-04-24 (v1.0)
- Initial release as part of the email-workflow build.
