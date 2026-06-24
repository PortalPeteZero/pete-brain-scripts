---
name: consolidate-lessons
description: >
  Reflective pass over Pete's Library/lessons/ -- merge duplicates, retire stale dated incidents, promote narrow rules to broader deployment, rebuild the lessons README index, and surface deployment gaps (lessons cited from 0 skills). The lesson-side equivalent of consolidate-memory. Use when Pete says "consolidate lessons", "tidy lessons", "lesson audit", or as the monthly scheduled task.
---

<!-- external-service-routing pre-flight: this skill is filesystem-only; no external services needed. -->

# Consolidate lessons

A reflective pass over Pete's `Library/lessons/` -- the lesson-side equivalent of `consolidate-memory`. Pete's auto-memory has MEMORY.md as its single canonical index; the lessons system has `Library/lessons/README.md` as its parallel index.

Order of operations:

## Phase 1 -- Take stock

- List `Library/lessons/` (~50+ lessons by 2026-05).
- Read `Library/lessons/README.md` -- the semantic index. Should have one entry per lesson, grouped by topic.
- Cross-check on-disk count vs README entry count via `Library/processes/scripts/vault-drift-check.py` (check_lesson_index_parity).
- Skim each lesson file. Note which look stale, which overlap, which are thin or under-explained.

## Phase 2 -- Consolidate

**Separate durable from dated.** A lesson is durable if its rule applies the next time the trigger fires. Dated if the rule was about a one-off incident with no future relevance.

- **Durable** (default): keep. Sharpen the README one-liner if it doesn't match the lesson's substance.
- **Dated / fully-mitigated**: retire by moving to an `_archive` subfolder (preserves history without the live README needing to point at it). Update README to remove the entry.
- **Superseded by a newer lesson**: keep both but add a "Superseded by [[link]]" header at the top of the older lesson; README points at the newer one as canonical.
- **Duplicate** (two lessons covering the same rule from different incidents): merge into one canonical lesson with both incident anchors preserved; retire the lesser to `_archive`.

**Time references.** Convert "this morning", "yesterday", "this quarter" to absolute dates. Lessons should be readable years later.

**Promotion candidates.** If a lesson has been corrected ≥3 times across daily logs but lives only as a narrow lesson, it should be promoted to CLAUDE.md inline rule (one-line pointer to the lesson) and/or added to the [[Library/audits/2026-05-16-lesson-deployment-matrix]] for explicit skill citation.

## Phase 3 -- Tidy the README index

Update `Library/lessons/README.md`:

- Lesson count matches on-disk count
- Every entry has a one-liner under ~150 chars
- Grouped by topic (Source-of-truth / Working style / Email / Vault structure / Sygma Hub / Code quirks / Chrome MCP / SEO migrations / etc -- see existing groupings)
- Remove pointers to retired lessons; add pointers for newly-promoted ones
- Frontmatter: bump `updated:` date

## Phase 4 -- Verify via drift-check

Run `python3 Library/processes/scripts/vault-drift-check.py` via Desktop Commander. Verify:

- `lessons/README.md ↔ lesson files parity` -- 0 issues
- `Lesson deployment gaps (cited from 0 consumers)` -- count goes down (or stays at 0)

## Phase 5 -- Report

Output a short summary:

- How many lessons touched (kept / retired / merged / promoted)
- New canonical-lesson candidates surfaced
- Open deployment gaps left for Pete to action (specific (lesson, skill) pairs in the matrix that don't have citations yet)

## Pairing with consolidate-memory

Both skills run on the same monthly cadence. Order: consolidate-memory first (memory is fast-lookup), consolidate-lessons second (lessons are deeper).

## Skill plumbing

- Source: `Library/skills/consolidate-lessons/SKILL.md`
- Archive: `Library/skills/consolidate-lessons.skill`
- Triggers: "consolidate lessons", "tidy lessons", "lesson audit"
- Scheduled task: `consolidate-lessons-monthly` (cron `0 8 1 * *`, pending Pete approval)
- Deployment matrix: [[Library/audits/2026-05-16-lesson-deployment-matrix]]
- Drift-check sibling: `vault-drift-check.py` `check_lesson_index_parity` + `check_lessons_cited_from_skills`

## Related lessons

- [[Library/lessons/2026-05-31-save-corrections-to-process-doc-not-just-memory]] — meta-rule: Pete-corrections during a process walk go into the process doc + lessons, not personal memory only. Fires at every consolidation pass when deciding where a correction lives.
