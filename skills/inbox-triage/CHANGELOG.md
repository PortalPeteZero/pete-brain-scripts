# inbox-triage -- CHANGELOG

Append-only log of meaningful changes to this skill. Each entry: date + one-line summary. Add a new line above the existing entries when the SKILL.md is edited; bump the version stamp inside SKILL.md to match.

## 2026-06-30
- De-staled: `email-workflow-state.md` → the CC `email-workflow-state` note; `Projects/{…}/`·`Personal/{area}/` folder matching → live CC projects/areas; archived `*-General` + `SY-AppearOnline` examples → live ones.

## 2026-06-06
- **Action/Task verb split** (plan: Projects/PA-General/files/email-workflow-plan-2026-06-06-action-task-split.md). `Action this Pn` = tray (Actions label, reply-shaped only); `Task this Pn` = Asana-only (no Actions, `[no-sync-close]` marker). Reminder block extended with verb reference. Ask⇔verb matrix + validator updated. `(tray)` / `(Asana only)` rendering rule. Transition guard. Routing decision tree pointer → vault-routing.
- **Step 8a Actions walker**: end-of-triage offer + standalone verbs ("actions" / "my actions"). Grouped by task, oldest first, suggested responses (voice-principles + dash grep mandatory), outcomes send / defer / already-done / de-tray. Old Step 8 → 8b.
- New single verbs: `action this`, `de-tray this` / `tray this`.

## 2026-05-20
- SKILL.md slim-down: stripped v1.0-v1.11 banner block (~60 lines) from preamble. Stripped inline `(v1.8)`, `(NEW in v1.8)`, `(locked 2026-05-20)`, `(v1.8 hard cap: 10)` tags throughout. SKILL.md now reads as current operational state; full history is below.
- Earlier same day: added Mimestream + Gmail + Finder link requirement to Task/Delegate verbs; enforced vault-enricher call (non-negotiable section); recorded triage atomic-Actions rule (Task must apply Actions + filing label atomically).

## 2026-05-17
- Initial CHANGELOG.md backfill during the comprehensive vault audit (Phase 6 of the 2026-05-16 audit plan).
- Added external-service-routing pre-flight to SKILL.md.

## 2026-05-06 (v1.11)
- Project-consolidation alignment: standalone Delegated project archived. Delegate-to-person tasks now go to Team-General/Delegated section. Task-creation routing follows asana-gmail-sync v1.7's chain. Vault structure shrinks to 10 top-levels.

## 2026-05-03 night (v1.10)
- TitleCase lock-in for family + owner-private vault enrichment paths. `Personal/family/{Sub Area}/` uses TitleCase With Spaces; lowercase / kebab-case forbidden inside 2-way sync surfaces.

## 2026-05-03 evening (v1.9)
- Aware of new vault `Personal/` top-level section. Family/personal emails enrich under `Personal/family/`, not retired `Businesses/ashcroft-family/`. AT-General Asana mapping unchanged.

## 2026-05-01 (v1.8) — five structural fixes shipped together
1. Mandatory `Ask` classification column (structural, not advisory).
2. Thread-history awareness (`History` pre-pass, mandatory) via `triage-action-classify.py`.
3. Mode A vs Mode B reminder block at top of every triage session.
4. Staged batched presentation: 5-10 rows per stage, fixed order (noise → relationships → internal → personal).
5. Sweep is sacred — on-command ONLY. Triage MUST NOT call/offer/chain-to sweep.

## 2026-04-27 (v1.7)
- Atomic Task-row execution. Pre-execution diff is Pete-visible. Hard validation gate added (Step 6.0).

## 2026-04-26 (v1.6)
- Ambiguous-verb fix. Six explicit verbs only. Step 5 ops table is the source of truth.

## 2026-04-25 audit (v1.5)
- Drift cleanup. Sweep vocabulary corrected to inverted-no-protect-list design.

## 2026-04-25 late evening (v1.4)
- Categorisation-aware. Smart task generation. Vault enrichment introduced.

## 2026-04-25 (v1.3)
- Read-content rule, action-need check, propose-when-ambiguous, vault enrichment, matter granularity (all as prose; v1.8 made #1 and #2 structural).

## 2026-04-25 (v1.2)
- Single Actions label. Priority in Asana only. Atomic triage operation.

## 2026-04-24 (v1.1)
- Adaptive batch sizing (superseded by v1.8 staged-batch rule). Pattern learning.

## 2026-04-24 (v1.0)
- Initial release.
