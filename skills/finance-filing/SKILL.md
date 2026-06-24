---
name: finance-filing
description: >
  Intelligent, reconciling filing of finance content into the right ENTITY home —
  Sygma / Canary Detect / Personal — decided from what the content is ABOUT, never the
  sender. Triggered conversationally (usually mid-triage) by "add to personal finance",
  "this is Sygma finance", "file under CD finance", "finance this", or "add to Ashcroft
  Finance". Reads the current context FIRST, finds where the item fits, classifies the
  change (addition / edit / change / duplicate) and the entity (auto when clear, ASK when
  ambiguous, SPLIT when one email spans two), enforces the Sygma owner-private
  payroll/accounts carve-out, enriches with attachments + key facts, raises an Asana task
  if there's an action, and updates the entity's finance ledger. Never dumps-and-files;
  never guesses the entity.
---

# Finance filing — the reconciling finance verb

> [!important] POST-CUTOVER ROUTING — overrides any vault path below (vault retired 24 Jun 2026)
> Anywhere a step reads/writes `Businesses/{name}/finance/`, `Personal/`, `Personal/family/Finance/`, or `Daily/`, do the **cloud equivalent** — the entity's **Drive** finance home (Sygma op → `Sygma Hub`; Sygma owner-private → `Sygma Private`; CD → `CD Private/finance`; personal/family → `Ashcroft Family/Finance` + `My Drive/Finance`) + a `vault_notes` record; the Ashcroft ledger → `Ashcroft Family/Finance/finance-ledger.md` (Drive); session log → CC `daily_log`. Tools run from `/tmp/pbs`; `[[wikilinks]]` resolve against `vault_notes`.

> [!important] Business OS migration — finance homes are Drive now
> The three entity finance homes are **Google Drive**: Personal/family → **Ashcroft Family/Finance** (+ Pete-solo → **My Drive/Finance**); Sygma operational → **Sygma Hub**; Sygma owner-private (Accounts + Payroll) → **Sygma Private**; Canary Detect → **CD Private/finance**. The old `Personal/finance/`, `Businesses/{name}/finance/`, `Personal/family/Finance/` vault paths are **retired 24 Jun 2026 (now in Drive + vault_notes)**. The entity-split rules + owner-private carve-out are unchanged — only the destination folders moved to Drive. `[[wikilinks]]` resolve against `vault_notes`. See [[vault-routing#finance-routing-by-entity--sygma--canary-detect--personal]].

The way to get **any** finance content into the right place: separated by entity, captured into the vault, reconciled against what's already there — Claude **understands each item before it files it**, never just dumps it somewhere.

> **Routing source of truth: [[vault-routing#finance-routing-by-entity--sygma--canary-detect--personal|Finance routing (by entity)]].** The three homes, the owner-private carve-out, and the Gmail labels live there — consult it, don't duplicate it here. This skill is the step-by-step *workflow*.

## The golden rule

**Read → reconcile → file. Never dump-and-file.** Before writing anything, read the current context, find where it fits, and decide what *kind* of change it is. Never spawn a second file that contradicts the first. This is the vault's "search first, then write" plus "live state is the truth, re-confirm before acting" ([[Library/lessons/2026-04-25-live-systems-are-truth-not-daily-log]]), turned into the verb's defining behaviour.

## When it fires

Pete says, about an email / doc / fact in front of you: **"add to personal finance"**, **"this is Sygma finance"**, **"file under CD finance"**, **"add to Ashcroft Finance"**, or just **"finance this"**. Usually mid-`triage`; can also be standalone.

## Workflow

### 1. Pull + read the item in full
Read the triggering thread/doc **completely**, including history (has it already been actioned? is it a reply in a longer chain?). For an email, use `gmail-api.py get-thread <id> full`. Don't act on the subject line alone.

### 2. Classify the ENTITY — from content, not sender
Decide **Sygma / Canary Detect / Personal** from what the item is *about*. The accountant (Mike Barton / JWR) handles Sygma **and** Pete's personal tax — the sender can't tell you which.
- **Auto** when it's unambiguous (a CD Odoo invoice → CD; a personal mortgage statement → Personal).
- **ASK** when ambiguous — never guess; a wrong guess leaks the separation. One plain question: *"Is this Sygma or personal?"*
- **SPLIT** when one item spans two entities — file each part in its own home (the cross-border restructure thread carried Pete's **personal** NI verdict *and* Mike chasing the **Sygma** P11D → both homes, cross-linked).

### 3. Enforce the owner-private carve-out (the part that must never leak)
If the content is **salary / payroll / company accounts / P11D / P60 / pay-sensitive** → it is **Sygma owner-private** (Drive `Pete & Mic / Sygma Solutions Private/`, Pete + Michaela only) — **never** `Personal/`, **never** the shareable Sygma area. If a mixed doc carries a salary figure, **split the salary-bearing part to owner-private first**, then file the rest. See [[Library/lessons/2026-06-05-staff-contracts-salary-never-in-hub]] + the high-private-folders rule.

### 4. Read the current context for that home
Open the entity's home (the relevant README + its `finance-ledger.md` + recent files — see homes in [[vault-routing#finance-routing-by-entity--sygma--canary-detect--personal|the routing table]]). Understand what's already recorded **before** deciding the change type.

### 5. Classify the CHANGE
- **Addition** — genuinely new → create a new dated file in the right home.
- **Edit** — updates something that exists → update it **in place** (don't create a parallel file).
- **Change** — corrects/supersedes a prior fact → update **and note what changed and why** (keep the old value visible).
- **Duplicate** — already captured → **cross-link and stop.** Do not spawn a contradicting second file.

### 6. Act
- **Write/edit** the right file in the right home (Step 4's location), per Step 5's change type.
- **Enrich**: run `vault-enricher.py` on the thread → attachments to the home's `source/`, body facts to `extracts/` (the same helper triage/sync use).
- **Label** the Gmail thread with the entity label *after reading* — business finance → `Businesses/{SY,CD,EA}-Finance`; household → `Personal/PA-Finance` (both already exist; never create a duplicate). Don't auto-create a holding filter (filters are persistent config → Pete-gated).
- **Task** (only if there's a real action — a deadline, a reply owed, a payment): raise an Asana task with the correct project/section/priority (P1=+2d / P2=+7d / P3=+30d)/due, Mimestream + Gmail links in notes. A reply-to-Pete-by-email shaped ask → the Actions tray verb instead (per [[email-workflow]]). Pure filing with no action → **no task**.
- **Ledger**: append a dated line to the entity's `finance-ledger.md` under the right load-bearing header (`## Deadlines` / `## Latest decision` / `## Recent filings`), then run `VAULT=/tmp/pbs python3 /tmp/pbs/finance-ledger-publish.py <path-to-ledger>` so the entity's Command Centre surface refreshes **with no deploy**. The Ashcroft Finance ledger is `Personal/family/Finance/finance-ledger.md` → the "Latest from the ledger" panel on `/m/ashcroft-finance`. Static reference (advisers, the doc-map) stays in the home's README + the module.

### 7. Report
One plain-English line: **entity → home → change-type → (label / task / ledger)**. e.g. *"Personal — updated Personal/family/Finance/cross-border-tax-restructure (edit: added Mike's NI verdict); labelled Personal/PA-Finance; ledger updated; no task."*

## Keep distinct from the business invoice flow
This verb is for **finance knowledge/context by entity**. The **business payables** flow (Soldo / Dext / Xero / the `Team-Finances` payables track + the `xero wages` / `file wages email` verbs) is owned by [[finance-workflow]] — don't reroute a payable through here, and don't reroute finance context through the payables track.

## Worked examples (the test set)
- **Addition · Personal** — a new pension statement → new file in `Personal/finance/`, `Personal/PA-Finance` label, ledger line, no task.
- **Edit · Personal** — Mike confirms the NI opt-out verdict → update the existing `Personal/family/Finance/cross-border-tax-restructure/` doc in place; ledger line.
- **Change · Sygma** — a corrected VAT figure supersedes last quarter's → update `Businesses/sygma-solutions/finance/` + note old→new; P2 task if a refiling is due.
- **Duplicate · CD** — a re-sent CD invoice already filed → cross-link in `Businesses/canary-detect/finance/`, stop, no second file.
- **Ambiguous** — "sort this finance email" with no entity cue → **ASK** "Sygma or personal?" before filing.
- **Both (split)** — a JWR thread with a personal NI line **and** a Sygma P11D chase → personal part to `Personal/family/Finance/`, Sygma part to `Businesses/sygma-solutions/finance/` (or owner-private if salary-bearing), cross-linked.

Version history: [[CHANGELOG]].
