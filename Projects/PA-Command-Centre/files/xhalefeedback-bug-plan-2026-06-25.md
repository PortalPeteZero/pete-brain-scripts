---
type: plan
slug: xhalefeedback-bug-plan
status: ready
date: 2026-06-25
project: "[[Projects/PA-Command-Centre]]"
tags: [plan, bug, business-os, migration, reference-repoint, training-feedback, xhale, command-centre, health-dashboard]
---

# Xhale Feedback Bug — diagnosis + remediation plan

> [!summary] Plain-English summary (for Pete)
> While logging three training sessions to Xhale/Loren on 25 Jun, I started to revert to the **old, pre-migration method** — saving files to a local folder and pushing them into the website's code. The Business OS migration was supposed to kill that method (everything reads/writes the cloud now). The reason I reverted: **the guide I follow for every training-feedback session still has the old steps written into it.** I read the guide, followed it, and it walked me back to the old way. The clue that something was wrong was only luck — the local folder it wanted had been deleted in last night's migration crash, so the old method failed loudly instead of silently "working" and hiding the reversion.
>
> Nothing was lost. Your feedback is correctly saved in the cloud Brain. The bug is a **stale instruction problem**, and it would have recurred every single training session. This document records exactly what happened and what must change so it cannot recur — here or in any other guide carrying the same leftover.

---

## 1. What the system is supposed to do

The **Xhale training-feedback loop** ([[training-feedback-loop]]) is the daily ritual for Pete's structured PF training with Loren:

1. Pete trains. Garmin records the activity.
2. Claude pulls the splits, presents them in the locked block format, Pete writes his feedback, Claude lightly tidies it and produces a paste-ready block for **Xhale** (Loren's coaching platform).
3. The confirmed feedback is **logged** (a per-date record) and **rendered** on the Command Centre health dashboard (`commandcentre.info/m/health/training/{date}`), one feedback card per activity, paired by `garmin_activity_id`.

After the **Business OS cloud cutover (24 Jun 2026)** the homes are: knowledge/records → CC Supabase `vault_notes`; files → Drive; Garmin data → CC `public.garmin_daily`; the Mac is a thin client (no local vault tree). See [[business-os-cutover-complete-2026-06-24]].

So the **correct** post-migration shape is: feedback record → `vault_notes` (cloud); dashboard → the CC app reads it live from Supabase. **No local files. No git push.**

---

## 2. What went wrong (incident, 25 Jun 2026)

1. Pete asked to log three backlogged sessions (24 Jun OW swim + indoor turbo pyramid, 25 Jun structured run). Claude formatted, tidied, and produced the paste blocks correctly.
2. On "log all three", Claude followed [[training-feedback-loop]] **as written**. The doc's logging steps say: write a local `.md` + `.json` to `Personal/passion-fit/coaching/feedback/{date}.md` and `.../data/{date}.json`, then **run `dashboard-sync.py --feedback`**, which **copies the JSON into a local clone of the `command-centre` repo (`~/code/command-centre`) and git-pushes it** so Vercel redeploys.
3. Claude did do the cloud-correct part (upserted the feedback to `vault_notes`), **but also went down the doc's `dashboard-sync` path** — i.e. began reverting to the pre-migration mechanism.
4. The `~/code/command-centre` clone had been **deleted in the 24/25 Jun Phase-6 migration crash** (recorded in the 25 Jun daily log; nothing was lost, the repo was already pushed to GitHub). So the old method couldn't complete.
5. Claude surfaced this to Pete as *"want me to re-clone and push it?"* — proposing to **restore and use the retired method**. Pete caught it: *"why are you reverting back to old methods?"* and *"this will happen every time we do this if we dont [fix the cause]."*

**The tell:** had the clone still existed, the old push would have "worked", the dashboard would have updated, and the reversion would have gone **unnoticed** — quietly re-establishing a local-file dependency the migration was meant to remove. The crash is what made the bug visible.

---

## 3. What I found (evidence, all angles)

### 3a. The process doc still instructs the retired method
[[training-feedback-loop]] (`vault_notes` id `a67cebee-15e2-4c35-b6aa-e08b7dbfea8f`, key `Library/processes/training-feedback-loop.md`) still contains, verbatim:

- **Step 6 (workflow):** *"AUTO log the confirmed feedback to **the vault** (md + JSON pair) AND AUTO run `dashboard-sync.py --feedback` … the push in one motion."*
- **Logging section:** write `Personal/passion-fit/coaching/feedback/{date}.md` + `.../data/{date}.json`, then *"run `… /dashboard-sync.py --feedback` … mirrors the JSON to the dashboard **repo's** `data/coaching/{date}.json` and **git-pushes to GitHub → Vercel** auto-deploys."*
- **Backfill section:** `cd "/Users/peterashcroft/Second Brain/Library/processes/scripts"` — a path the cutover **deleted**.
- **Discovery section:** Obsidian **Dataview** queries + `grep`/`jq` against local `Personal/passion-fit/coaching/feedback/*.md|json` — all retired surfaces.

Following the doc faithfully = doing the old thing. This is the **proximate cause**.

### 3b. The sync tool is a local-clone + git-push script
`dashboard-sync.py` reads `VAULT/Personal/passion-fit/coaching/feedback/data/*.json`, copies to `DASHBOARD = ~/code/command-centre/data/coaching/*.json` (line 45; "repointed 2026-06-11, was `code/pete-health-dashboard`"), then `git fetch + rebase + commit + push`. It is structurally a **pre-migration mechanism** (local files → local clone → push). It still exists and is reachable; nothing marks it retired.

### 3c. The dashboard PAGE was only half-migrated
- `modules` row `health` ("Health Dashboard") records `reads = {garmin_daily}` — i.e. the page reads Garmin data **live from the cloud table** (good, cloud-native).
- There is **no `coaching` / `feedback` / `training` table** in the CC `public` schema.
- The **training-feedback cards** are therefore **not a DB read** — they are **file-fed** from `data/coaching/*.json` committed into the `command-centre` repo (a "file feed", flagged separately in the lineage work). So the page's **reader** for feedback was never moved to the cloud, even though the feedback **data** was.

**Net:** the migration moved the **data home** (feedback → `vault_notes`; the 21 + 22 Jun entries are already there) but left **two halves on the old model** — the doc's *write* mechanics and the page's *read* mechanics. The only bridge between them is the retired local-push.

### 3d. I made it worse in the same session
Earlier on 25 Jun I edited this very doc to add the new `format_version 4` table layout — and **walked straight past the stale logging steps without repointing them.** A "leave-it-better" miss: I had the doc open and fixed only my target section.

### 3e. Contributing factor — the crash
`~/code/command-centre` was deleted by the migration-remediation Phase 6 on 24/25 Jun (daily log). This both (a) made the old method fail loudly (surfacing the bug) and (b) is itself a reminder that local working copies are now ephemeral/expendable — anything depending on a fixed local clone is fragile by design post-cutover.

---

## 4. Root cause (layered)

| Layer | Cause |
|---|---|
| **Proximate** | The process doc instructs the retired local-file + `dashboard-sync` + git-push method; Claude follows the doc. |
| **Underlying — docs** | The doc's logging/dashboard/discovery sections were never repointed for the cutover (the tracked *Part-D reference-repoint* class of staleness). |
| **Underlying — code** | The CC Training page reads feedback from committed JSON files, not from `vault_notes` — so a file-push is still *structurally required* for the page to update. The data store was migrated; this reader was not. |
| **Underlying — tooling** | `dashboard-sync.py` + its `~/code/command-centre` clone dependency still exist and are reachable, unflagged as retired. |
| **Behavioural** | Claude edited the doc without repointing stale refs it passed, and followed doc mechanics without checking them against the cloud-native principle. |
| **Systemic** | The migration moved data homes but left a long tail of docs + app readers + helper scripts on the old model, with **no guard** that catches "you are reaching for a retired local method." |

**One-line root cause:** *the data was migrated to the cloud, but the instructions and the page's reader that surround it were not — so faithfully following the instructions reverts the migration.*

---

## 5. Impact

- **Data loss:** none. All three sessions are correctly in `vault_notes` (`format_version 4`, embedded, verified). The 21/22 Jun entries are intact.
- **User-facing:** the CC Training page will not show the 24/25 Jun feedback cards until the page-read is fixed (or a push happens). Secondary — Loren receives the feedback via the Xhale paste blocks directly, which is the actual coaching channel.
- **Process integrity:** the bug would recur on **every** training-feedback session while the doc is stale.
- **Trust / migration integrity:** the failure mode is silent reversion to a local dependency the cutover was meant to remove — the most important thing to prevent, because it erodes the "everything is cloud now" guarantee.

---

## 6. Already done this session (stop-gap, NOT the full fix)

- [x] **Override callout** added to the top of [[training-feedback-loop]] — explicitly: ignore the old local-file/`dashboard-sync`/push/Obsidian-Dataview steps; log to `vault_notes`; the page reads from the cloud. So the next read hits the correction first. (Verified present in the live note.)
- [x] **Feedback logged the cloud way** — 24 Jun (multi-session swim+bike) + 25 Jun (run) upserted to `vault_notes` as `training-feedback`, `format_version 4`, embedded. (Verified.)
- [x] **`format_version 4`** (aligned-column tables, all sports) saved to the doc + Tweak Log.
- [x] **Remediation task** created — `public.tasks` id `754dfa02-59bc-4baf-8624-1833062b8380` (P2, PA-Command-Centre).
- [x] **Systemic lesson** saved — [[2026-06-25-pre-migration-docs-cause-reversion]] (fires on any stale doc, not just this one).
- [x] **Dashboard JSON** for 24/25 Jun staged at `VAULT/Personal/passion-fit/coaching/feedback/data/` (so the page can render the moment a cloud read OR a one-off push exists — but the proper fix below removes the need).

The stop-gap stops the recurrence on **this** doc. It does **not** fix the page reader, retire the tool, or clean the other docs. That is the plan below.

---

## 7. What needs to change (remediation plan)

### Phase 1 — Make the CC Training page read feedback from the cloud (kills the push) ★ core fix
- [ ] In the `command-centre` app, change the Training page (`app/.../m/health/training/[date]`) + `FeedbackBox`/`getFeedback()` to read `training-feedback` from **Supabase `vault_notes`** (filter `type='training-feedback'`, by `date`/`frontmatter->>date`), instead of importing `data/coaching/{date}.json`.
- [ ] Map entries to ActivityCards by `garmin_activity_id`; support **multi-session** notes (`frontmatter->sessions[]`) and **unpaired** notes ("Notes to Loren" band).
- [ ] Render `format_version 4` blocks (and keep v1–3 backward-compatible for the 21/22 Jun + older entries).
- [ ] **Acceptance:** 24 + 25 Jun feedback renders on `/m/health/training` with **no** local file and **no** push; deleting `data/coaching/*.json` from the repo does not change the page.

### Phase 2 — Retire `dashboard-sync.py` + the `~/code/command-centre` dependency
- [ ] Migrate the **zones** path too (`training-zones.json`) to a cloud read, or confirm it already is, so nothing in `dashboard-sync.py` is still needed.
- [ ] Mark `dashboard-sync.py` **retired** in the helper registry / `external-service-routing`; remove its invocation from all docs.
- [ ] Remove any remaining assumption of a fixed local `command-centre` clone for PF data.
- [ ] **Acceptance:** no helper writes PF data into a local clone; the registry shows the script retired; `whereis` on "training feedback dashboard" returns the cloud read, not the script.

### Phase 3 — Full rewrite of [[training-feedback-loop]] (beyond the override)
- [ ] Rewrite **Step 6**, the **"Logging tidied feedback"** section, the **JSON/dashboard** section, and the **Discovery** section to the cloud method: log = upsert `training-feedback` to `vault_notes` (path kept only as stable slug) → `cc-knowledge-embed-backfill`; query = `cc-knowledge-api.py` / `cc-sql.py`, never Dataview; remove the `cd "…/Second Brain/…/scripts"` backfill path.
- [ ] Keep the **content** that is still valid (frontmatter shape, JSON entry shape as the note's frontmatter/JSON, voice rules, locked v4 format).
- [ ] Add a Tweak Log entry; **downgrade** the override callout to a one-line "migrated 25 Jun" note once the body is clean.
- [ ] **Acceptance:** a fresh read of the doc contains **zero** live retired-path/push instructions (only historical mentions inside Tweak Log / clearly-marked HISTORY).

### Phase 4 — Sweep every process doc + skill for the same class of bug (systemic)
- [ ] Grep `vault_notes` (processes/skills) + the skill files for retired-method signatures: `dashboard-sync`, `Second Brain/`, `~/code/`, `git push`/`git-push` to a clone, `Library/processes/scripts/`, `Obsidian`, `Dataview`, and live local vault content paths.
- [ ] For each hit, classify (data-write / page-read / discovery-query) and repoint to the cloud equivalent. Record in the **Part-D reference-repoint ledger** ([[part-d-reference-repoint-ledger-2026-06-22]]).
- [ ] **Acceptance:** the grep returns only intentional/historical references, no live instructions.

### Phase 5 — Guardrail so it cannot silently recur
- [ ] Add a **drift-check** (extend the existing weekly Phase-0 drift cron, or a new check) that greps process docs/skills for the retired-method signatures and flags any **new** occurrence.
- [ ] Pair it with the standing lesson [[2026-06-25-pre-migration-docs-cause-reversion]] (interim behavioural guard) + the override-callout pattern.
- [ ] **Acceptance:** a newly-introduced stale instruction is caught by the check, not by Pete.

---

## 8. Verification performed (25 Jun)

- `modules` → `health` reads `{garmin_daily}` only (no feedback DB read). ✓
- No `coaching`/`feedback`/`training` table in `public`. ✓
- `dashboard-sync.py` confirmed local-clone + git-push to `~/code/command-centre`; `~/code/command-centre` confirmed **missing**. ✓
- Feedback notes present in `vault_notes`: 21/22 Jun (`fmt 3`), 24/25 Jun (`fmt 4`, this session), all embedded. ✓
- Override callout present in the live doc. ✓
- Task `754dfa02…` + lesson `2026-06-25-pre-migration-docs-cause-reversion` created. ✓

---

## 9. Decisions for Pete

1. **Sequencing:** do Phase 1+2 now (proper fix, a contained `command-centre` code change), or fold into the **Command Centre v2 / Business-OS remediation** programme? (Recommendation: Phase 1+2 soon — they remove the recurring fragility; Phase 3+4 ride along with the Part-D sweep.)
2. **Design:** page reads `vault_notes` **directly**, or via a thin `coaching_feedback` view/table for cleaner querying? (Recommendation: read `vault_notes` directly — it is the home; add a view only if query shape demands it.)

---

## 10. References

- Process doc: [[training-feedback-loop]] (`vault_notes` `a67cebee-15e2-4c35-b6aa-e08b7dbfea8f`)
- Lesson: [[2026-06-25-pre-migration-docs-cause-reversion]]
- Task: `public.tasks` `754dfa02-59bc-4baf-8624-1833062b8380`
- Ledger: [[part-d-reference-repoint-ledger-2026-06-22]]
- Cutover record: [[business-os-cutover-complete-2026-06-24]]
- Tool: `pete-brain-scripts/dashboard-sync.py` · Module: `modules.health` · Tables: `public.vault_notes`, `public.garmin_daily`
