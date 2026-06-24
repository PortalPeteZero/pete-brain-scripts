---
name: finance-filing-changelog
tags: [skill, changelog, finance]
---

# finance-filing — changelog

## v1.0 — 2026-06-17
Created (household-finance-system plan, Phase 3 — [[Projects/PA-Command-Centre/files/household-finance-system-plan-2026-06-17]]). The reconciling finance verb: **"add to personal finance" / "this is Sygma finance" / "file under CD finance" / "finance this"**.

- Routes by **entity decided from content** (Sygma / CD / Personal), never the sender.
- Classifies the change: **addition / edit / change / duplicate** — never spawns a contradicting second file ("search first, then write").
- **Asks** when the entity is ambiguous; **splits** when one item spans two entities.
- Enforces the **Sygma owner-private payroll/accounts carve-out** (Pete + Michaela only; never Personal, never shareable Sygma).
- Enriches via `vault-enricher.py`; applies the existing entity Gmail label (`Businesses/{SY,CD,EA}-Finance` / `Personal/PA-Finance` — no duplicates); raises an Asana task **only on a real action**; appends to the entity `finance-ledger.md`.
- Routing source of truth = [[vault-routing#finance-routing-by-entity--sygma--canary-detect--personal|Finance routing (by entity)]]. Kept distinct from the business payables flow ([[finance-workflow]]).
