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
  payroll/accounts carve-out, enriches with attachments + key facts, raises a CC task
  (`public.tasks`) if there's an action, and updates the entity's
  finance ledger. Never dumps-and-files;
  never guesses the entity.
---

# Finance filing — the reconciling finance verb

> [!important] Finance homes (route per [[vault-routing#finance-routing-by-entity--sygma--canary-detect--personal]])
> Each entity's finance home is a **Google Drive** folder + a `vault_notes` record: personal & family → **Ashcroft Family/Finance** (Pete-solo → **My Drive/Finance**); Sygma operational → **Sygma Hub**; Sygma owner-private (Accounts + Payroll, Pete + Michaela only) → **Sygma Private**; Canary Detect → **Entities Private / Canary Detect (Camello Blanco SL) / Finance** (the old CD Private was folded in here 4 Jul). The Ashcroft ledger → the CC **`public.finance_ledger`** table (converted from the old finance-ledger.md 2026-07-03). An action → `public.tasks`; session log → `daily_log`.

The way to get **any** finance content into the right place: separated by entity, captured into its entity home, reconciled against what's already there — Claude **understands each item before it files it**, never just dumps it somewhere.

> **Routing source of truth: [[vault-routing#finance-routing-by-entity--sygma--canary-detect--personal|Finance routing (by entity)]].** The three homes, the owner-private carve-out, and the Gmail labels live there — consult it, don't duplicate it here. This skill is the step-by-step *workflow*.

## The golden rule

**Read → reconcile → file. Never dump-and-file.** Before writing anything, read the current context, find where it fits, and decide what *kind* of change it is. Never spawn a second record that contradicts the first — search first then write, and live state is the truth (re-confirm before acting).

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
If the content is **salary / payroll / company accounts / P11D / P60 / pay-sensitive** → it is **Sygma owner-private** (Drive **Sygma Private**, Pete + Michaela only) — **never** the household home, **never** the shareable Sygma area. If a mixed doc carries a salary figure, **split the salary-bearing part to owner-private first**, then file the rest.

### 4. Read the current context for that home
Open the entity's home (its `finance_ledger` rows — `SELECT kind, entry FROM finance_ledger WHERE archived_at IS NULL ORDER BY entry_date DESC` via `cc-sql.py` — + recent files — see homes in [[vault-routing#finance-routing-by-entity--sygma--canary-detect--personal|the routing table]]). Understand what's already recorded **before** deciding the change type.

### 5. Classify the CHANGE
- **Addition** — genuinely new → create a new dated file in the right home.
- **Edit** — updates something that exists → update it **in place** (don't create a parallel file).
- **Change** — corrects/supersedes a prior fact → update **and note what changed and why** (keep the old value visible).
- **Duplicate** — already captured → **cross-link and stop.** Do not spawn a contradicting second file.

### 6. Act
- **Write/edit** the right file in the right home (Step 4's location), per Step 5's change type.
- **Enrich**: run `vault-enricher.py` on the thread to pull its attachments + key facts into the home (the same helper triage/sync use).
- **Label** the Gmail thread with the entity label *after reading* — business finance → `Businesses/{SY,CD,EA}-Finance`; household → `Personal/PA-Finance` (both already exist; never create a duplicate). Don't auto-create a holding filter (filters are persistent config → Pete-gated).
- **Task** (only if there's a real action — a deadline, a reply owed, a payment): raise a CC task (`public.tasks`). **A bill/payment = always a PD** (`priority='PD'`) with the **invoice due date** as `due_on` (set it without asking — the one case Claude dates autonomously), `base_priority='P3'`, routed to `Team-Finances`. A non-payment action with no hard deadline = an **undated** P-tier (leave `due_on` NULL — the date is the switch, so a date would force it to PD); only make it a PD if there's a genuine fixed date, and confirm that date with Pete first. Insert via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py` (`INSERT INTO tasks (id,name,priority,base_priority,due_on,entity_slug,project_slug,status,source,notes) VALUES (gen_random_uuid(),…,'todo','claude',…)`); set `project_slug`/`entity_slug` for the home entity (Sygma finance → `Team-Finances`/Sygma; CD → `Team-Finances`/Canary Detect; household → entity Personal). A reply-to-Pete-by-email shaped ask → the `Reply` verb (Replies tray) instead (per [[email-workflow]]). Pure filing with no action → **no task**.
- **Ledger**: INSERT a row into the CC `public.finance_ledger` table — `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "INSERT INTO finance_ledger (entity, cc_report_key, kind, entry, entry_date) VALUES ('personal','ashcroft-finance','deadline'|'decision'|'filing','YYYY-MM-DD — what happened — [[wikilink]]','YYYY-MM-DD')"` — then run `VAULT=/tmp/pbs python3 /tmp/pbs/finance-ledger-publish.py` so the entity's Command Centre surface refreshes **with no deploy** (the "Latest from the ledger" panel on `/m/ashcroft-finance`). Retire an outdated line by setting its `archived_at` (never DELETE — it's a ledger). The old finance-ledger.md file is gone (converted 2026-07-03). Static reference (advisers, the doc-map) stays in the home + the module.

### 7. Report
One plain-English line: **entity → home → change-type → (label / task / ledger)**. e.g. *"Personal — updated Ashcroft Family/Finance/cross-border-tax-restructure (edit: added Mike's NI verdict); labelled Personal/PA-Finance; ledger updated; no task."*

## Keep distinct from the business invoice flow
This verb is for **finance knowledge/context by entity**. The **business payables** flow (Soldo / Dext / Xero / the `Team-Finances` payables track + the `xero wages` / `file wages email` verbs) is owned by [[finance-workflow]] — don't reroute a payable through here, and don't reroute finance context through the payables track.

## Worked examples (the test set)
- **Addition · Personal** — a new pension statement → new file in `Ashcroft Family/Finance` (Pete-solo → `My Drive/Finance`), `Personal/PA-Finance` label, ledger line, no task.
- **Edit · Personal** — Mike confirms the NI opt-out verdict → update the existing `Ashcroft Family/Finance/cross-border-tax-restructure/` doc in place; ledger line.
- **Change · Sygma** — a corrected VAT figure supersedes last quarter's → update `Sygma Hub` (Sygma operational) + note old→new; P2 task if a refiling is due.
- **Duplicate · CD** — a re-sent CD invoice already filed → cross-link in `Entities Private / Canary Detect (Camello Blanco SL) / Finance`, stop, no second file.
- **Ambiguous** — "sort this finance email" with no entity cue → **ASK** "Sygma or personal?" before filing.
- **Both (split)** — a JWR thread with a personal NI line **and** a Sygma P11D chase → personal part to `Ashcroft Family/Finance`, Sygma part to `Sygma Hub` (or `Sygma Private` if salary-bearing), cross-linked.

Version history: [[CHANGELOG]].
