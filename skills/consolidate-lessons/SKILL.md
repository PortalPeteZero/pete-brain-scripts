---
name: consolidate-lessons
description: >
  RETIRED 24 Jun 2026 (Business OS cutover). Lessons now live in the CC `vault_notes` knowledge
  base, which has its own link graph + semantic search + dedup — there is no `Library/lessons/`
  folder or README index to consolidate. Do not invoke; use cc-knowledge-api.py for lesson work.
---

# Consolidate lessons — RETIRED

This skill curated `Library/lessons/` + its `README.md` index. Both are gone: lessons migrated to
the CC **`vault_notes`** store (1,900+ notes + a `note_links` graph + semantic search), so
duplicate-merging and index-rebuilding are now handled by the knowledge base itself, not a manual
monthly pass.

**If you need to work with lessons now:**
- Find / search → `VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py "<query>"` (full-text + semantic).
- Add one → write a `.md`, then `cc-knowledge-ingest.py <file>` → null its embedding → `cc-knowledge-embed-backfill.py`.
- The `note_links` graph replaces the README index; semantic search replaces manual dedup.

Kept as a stub so any reference resolves clearly. Safe to remove from the installed skill set.
