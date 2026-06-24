# vault-writer -- CHANGELOG

Append-only log of meaningful changes to this skill. Each entry: date + one-line summary. Add a new line above the existing entries when the SKILL.md is edited; bump the version stamp inside SKILL.md to match.

## 2026-06-14 — evidence-driven Asana reconciliation (Pete-directed fix for "ships it, never closes it")
- Step 3a gains a **close-on-ship** reflex: for anything this session shipped (commit / cron / file / email naming a task), run `asana-reconcile.py --ship <gid|kw>` to match the **full open Asana list** and close with `--apply-auto` after eyeballing. Catches multi-day work whose task was opened in an earlier session (the gap the same-day-TODO check is blind to).
- Step 3b's mechanic now runs `asana-reconcile.py` (evidence buckets AUTO/PROPOSE/PAYMENT/OPEN) instead of an age-only hand-rolled scan; high-precision (strict completion-record matching to avoid the status-dump false-positive). Notes the weekly `asana-reconcile` Sunday cron as the safety net. Posture unchanged: never auto-act beyond the AUTO bucket. New helper: `Library/processes/scripts/asana-reconcile.py`.
- Step 3a documents the two write-side conventions that feed the new `asana-ship-gate` Stop hook (gid-in-commit-message for code; `SHIPPED: <gid>` daily-note marker for non-code ships). The hook is harness-run every turn and blocks silent sign-off when a shipped, task-referenced item is still open — the deterministic answer to "are completions written reliably". Reconciler reads the `SHIPPED:` marker as auto-closeable.

## 2026-05-20 — SKILL.md slim-down pass
- Stripped v4.0-v5.6 version banner block (~85 lines) from preamble. SKILL.md now reads as current operational state; full history below.

## 2026-05-20
- v5.6: New Step 3c — for every project touched this session, verify the Asana project ↔ vault folder are in sync (Full Sync Check Rules in [[asana-configuration]], scoped to touched projects): folder/README/files exist, section↔sub-folder parity, names match, no orphans either way, state parity. Fix the vault side; surface Asana-side drift. Pete-directed.
- v5.5: New Step 3b — session-end scan of the whole Asana workspace for stale work (untouched >21d, long-overdue >14d, bloated undated clusters, completed-but-listed) surfaced as a digest. Surface-only; never bulk-close/delete/reassign without Pete's per-item confirmation. Mirror in brain Compress.
- v5.4: Step 2 now opens with a structured-home discovery sweep applied to the whole session — find the existing project/property/folder for everything touched and update it with what changed + rationale; daily logs / CLAUDE.md / lessons are pointers only. Pete-directed. See [[Library/lessons/2026-05-20-website-work-saved-to-structured-vault]].

## 2026-05-17
- Initial CHANGELOG.md backfill during the comprehensive vault audit (Phase 6 of the 2026-05-16 audit plan).
- Added external-service-routing pre-flight to SKILL.md.

## 2026-05-06 (v5.3)
- Project-consolidation alignment. End-of-session reflection no longer searches Invoices/ or Delegated/ as top-levels (folded into Projects/Team-*). Aware of parent + sub-project pattern. SY-Clancy exception preserved (vault content at `Customers/SY-Clancy/`). Default to General sub-project for new tasks.

## 2026-05-04 evening (v5.2)
- Step 8 (vault-drift-check.py --map-only) and Step 9 (vault-drive-sync.py) both routed via Desktop Commander. Single consistent end-of-session script invocation pattern.

## 2026-05-04 afternoon (v5.1)
- New Step 3a — same-day reconciliation pass: re-read today's `> [!todo] Pending Tasks` blocks, cross-reconcile against later session-log commits / READMEs / decision docs. For tasks with positive evidence of having shipped: close Asana + strike daily-note `[ ]` line in-place + SHIPPED-as marker. Mirror in brain Compress Step 7. Lesson: [[Library/lessons/2026-05-04-same-day-reconciliation-gap]].

## 2026-05-03 night (v5.0)
- TitleCase lock-in for 2-way sync surfaces. Family / owner-private folder names use TitleCase With Spaces matching Drive. vault-drive-sync.py guard against case-different duplicates.

## 2026-05-03 night earlier (v4.9)
- Step 8 (Update MAP.md) extended: also runs `vault-drift-check.py --map-only` to catch out-of-session adds. Parallel pre-flight at brain Resume Step 8.

## 2026-05-03 night (v4.8)
- New Step 9: vault-drive-sync.py as session-close failsafe (hourly LaunchAgent is primary). Aware of "no autonomous moves out of private folders" rule.

## 2026-05-03 night (v4.7)
- Aware of new `Personal/` top-level (12th section). Step 2 reflection extended with personal-area entries. 3 sync paths via vault-drive-sync.

## 2026-05-03 late evening (v4.6)
- Step 3 (Asana sync) reconciled with brain Compress Step 4. Defers to auto-create model; no "wait for Pete to pick" gate.

## 2026-05-03 late evening (v4.5)
- Post-vault-rot-audit changes: every active project README carries `category:` frontmatter; every property README carries `property_type:`; `Library/decisions/*.md` `status:` uses canonical 4-value vocab; new onboarding-ritual verification at session close; new top-level reference files; new `Library/lessons/` folder; new `vault-drift-check` cron; dash rule scoped to outbound only.

## 2026-04-25 late evening (v4.4)
- Step 2 extended for `Businesses/{name}/{area}/` operational area structure. Gmail-as-truth principle baked in. Aware of vault-enricher.py auto-pull (don't duplicate).

## 2026-04-24 (v4.3)
- Routing rules consolidated to [[vault-routing]] (vault-writer points there, doesn't duplicate). End-of-session vault-routing-capture check.

## 2026-04-24 evening (v4.2)
- Vault extended (Invoices/, Accreditations/, Delegated/ at root — later folded into Projects/Team-* in v5.3).

## 2026-04-24 (v4.1)
- `Customers/` and `Suppliers/` added as root-level folders.

## 2026-04-22 (v4.0)
- Vault restructure: Departments/ + Teams/ → Businesses/; Intelligence/ + Resources/ + Assets/ → Library/.
