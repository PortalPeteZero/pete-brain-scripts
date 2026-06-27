---
name: inbox-triage
description: >
  Interactive email triage walker. On the verb "triage", reads each thread in
  full (including history -- has Pete already replied?), classifies each row's
  action-need from a fixed vocabulary BEFORE choosing a verb, and walks Pete
  through the inbox in STAGED BATCHES of 5-10 rows grouped by category
  (auto-filter candidates → customers/suppliers/projects → internal → personal).
  Always opens with a Mode A vs Mode B reminder PLUS the verb
  reference (Reply = tray, reply-shaped only; Task = CC task only,
  no Replies label). After the stages, offers the Replies walker (one tray item
  at a time with a suggested response or defer) then `sync` opt-in (no
  auto-chain). Also triggers standalone on "replies" / "my replies" (legacy
  "actions" / "my actions" still work) — walker only. Sweep is on-command ONLY --
  triage NEVER calls or offers sweep.
---

<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Sheets / Docs / Xero / Odoo / GSC / GA4 / Ads / Vision / Geocoding / Sentry / Cloudflare / Vercel operation in this skill, see [[external-service-routing]]. Helper-first. -->

# inbox-triage

> [!important] Where triage files things
> **Tasks → the CC:** `Task`/`Hand to` create a row in **`public.tasks`** (via `cc-sql.py`). **`Reply` is label-only — no task** (a task only in the overlap case, carrying `[no-sync-close]`). **Entity content** (customer / supplier / project / personal) → the entity's **Google Drive** folder + a **`vault_notes`** record (account-customers → CC `account_*`). **Decisions / notes** → **`vault_notes`** (ingest a `.md`). New customer/supplier onboarding creates a **Drive** folder + CC record. `vault-enricher.py` runs on every filed/task-linked thread — keep calling it. Gmail labels are unchanged. Route per the matrix in [[vault-routing]]. Tools run from `/tmp/pbs`; a `[[wikilink]]` links a note by its name in `vault_notes`.

Interactive email triage walker. The verb `triage` runs this skill.

> **Operating manual**: see `[[email-workflow]]` for the full system overview (verbs, four decision lines, sweep behaviour, demand-driven label rule, delegation flow).
>
> **Routing rules**: `[[vault-routing]]`. Gmail-side rules: `[[gmail-label-scheme]]`.
>
> **Version history**: `[[CHANGELOG]]`.

## When to invoke

User says any of:
- "triage"
- "let's do inbox"
- "let's go through my emails"
- "triage my inbox"

**Walker-only invocation**: "replies", "my replies", "deal with my replies" (legacy "actions" / "my actions" still work) → skip the triage stages entirely and run ONLY Step 8a (the Replies walker) against the current tray.

## Dependencies

- Gmail API helper: `/tmp/pbs/gmail-api.py` (always available via service account DWD)
- **Triage action classifier helper: `/tmp/pbs/triage-action-classify.py`** (runs the History pre-pass, emits a draft `Ask` per thread)
- Calendar API helper: `/tmp/pbs/calendar-api.py`
- CC task store: `public.tasks`, CRUD via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py`.
- Read/Write/Edit tools for vault operations
- State file: `Library/processes/email-workflow-state.md`

## Mode A vs Mode B vs Mode C -- reminder block (printed at start of every triage)

> **Print this verbatim to the chat before any other work in every triage session.** Drift caused mid-round confusion; this is now a structural pre-step.

```
═════════════════════════════════════════════════════════════════════════════
  Filter mode reference (always shown at start of triage):

   Mode A:  apply label, KEEP in inbox
            → use for customer / supplier / project mail you still triage
            → action shape: addLabelIds=[X]

   Mode B:  apply label, AUTO-ARCHIVE on arrival
            → use for pure noise: receipts, newsletters, alerts, marketing
            → action shape: addLabelIds=[X], removeLabelIds=["INBOX"]

   Mode C:  no filter (manual labelling only)
            → one-offs not worth a filter

  These are filter modes (what happens to FUTURE arrivals).
  They are NOT the same as triage verbs (what we do to existing threads now).

  ───────────────────────────────────────────────────────────────────────────
  Verb reference — the triage verbs:

   THREE outcomes when something needs doing:
     Reply         → Replies label + filing label + archive. NO task.
                     The label IS the record (reply / RSVP / sign-and-return).
                     Oldest-first in the Replies walker — Reply carries no Pn.
     Reply + Task  → a Reply AND a prep Task: "do the work, then reply"
                     (e.g. build the quote, then reply). The task is tagged
                     `reply`, carries [no-sync-close] + the Mimestream link.
     Task Pn       → filing label + a CC task ([no-sync-close]). NO Replies
                     label. Bills, cert batches, work — done off-email.

   Hand to {who}   → Delegated label + a chase task.
   File / Keep / Clear / Skip → file away / keep in inbox / archive-as-noise / defer.

   One-sentence rule: Replies = waiting on Pete to respond by email; a task is
                      created only when work is required, never from the label.
═════════════════════════════════════════════════════════════════════════════
```

## The triage loop

### Step 1: Pull inbox

`gmail-api.py search "in:inbox" 100` -- the limit is a POSITIONAL argument (not `--max-results`); paginate further if needed.

Filter out:
- Threads already labelled with the `Replies` or `Delegated` label (already in-flight workflow, accessible via the Replies sidebar view; shown separately if Pete asks).
- Threads older than 30 days (avoid drowning in ancient backlog).

### Step 2: Group by category (NOT by sender clusters first)

In v1.8 the grouping is **by category for staging**, not by sender clusters. Categories drive the stage order in Step 5:

| Category | Examples | Stage |
|---|---|---|
| Auto-filter / Mode B candidates | Receipts, newsletters, alerts, marketing, cold sales | **Stage 1** |
| Customers / Suppliers / Projects / Accreditations | Relationship mail | **Stage 2** |
| Internal Sygma / CD | Forwards from Michaela, Paul, Jane, Dave, Jim, Sue | **Stage 3** |
| Personal + ambiguous one-offs | Lodge, charity, friend, can't-classify | **Stage 4** |

Within each category, thread-level grouping (same sender + same topic, transactional repeats) still applies for clean presentation.

### Step 3: Decide stage size (hard cap: 10)

| Threads in stage | Behaviour |
|---|---|
| ≤ 10 | One sub-batch |
| 11-20 | Two sub-batches of ~10 |
| > 20 | Multiple sub-batches of ~10 with progress indicator (`Stage 1 of 4, sub-batch 2 of 3, 12 of 28 done`) |

**Hard rule: never present more than 10 rows in a single chat-visible batch.** Pete called this out 1 May 2026 -- 25-row tables are useless. If the rule conflicts with logical grouping, split the group.

Pete can override at any time: "give me everything in one go" -- collapse to single round.

### Step 4: History pre-pass (NEW in v1.8) -- mandatory, structural

For every thread, BEFORE choosing an Action verb, run the history classifier:

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/triage-action-classify.py /tmp/inbox-bodies.json > /tmp/triage-ask.json
```

Or call the equivalent function library-side. The output is a JSON file with one entry per thread:

```json
{
  "thread_id": "19de322f2b2f3697",
  "msg_count": 3,
  "latest_direction": "external",          // "external" | "pete-sent" | "internal-forward"
  "pete_replied_since_last_external": false,
  "open_question_in_latest": true,
  "has_actions_label": false,
  "ask_classification": "review",          // see vocabulary below
  "ask_reason": "External party (Regan @ surveyequipment.uk) sent quote PDF and 'feel free to ask' -- review needed before any decision."
}
```

**`Ask` vocabulary (fixed -- 6 values, no others permitted):**

| Ask | Meaning | Permitted Action verbs |
|---|---|---|
| `none` | Nothing in the body asks Pete to do anything (already-handled / informational / auto-confirmed / Pete already replied) | File / Keep in inbox / Clear |
| `reply` | Thread asks or expects a reply from Pete | **must be Reply or Hand to** |
| `decision` | Requires Pete to approve / reject / pay / sign off | **Reply / Task / Hand to** — Reply if the decision is executed by replying to the email itself; Task if it's executed elsewhere (bank, portal, build); Reply + Task if a reply is gated on work first |
| `review` | Doc / quote / invoice / report needs Pete's eyeball before next step | **Reply / Task / Hand to** — same action-shape test as decision |
| `rsvp` | Meeting / event needs accept / decline | **must be Reply or Hand to** |
| `info-only` | Informational FYI -- file home but no action expected | File / Clear |

`info-only` and `none` are similar but distinct: `none` = the body had something but it's already handled; `info-only` = the body is purely FYI (newsletter, status report).

**Heuristic rules the classifier applies (codified in the helper script):**

1. **Latest message FROM Pete (sent direction)** → default `Ask = none` (ball is on the other side; Pete's already moved). Override only if Pete's reply set up an explicit follow-up he owes ("I'll send the doc tomorrow").
2. **Pete already replied AFTER the latest external message** → `Ask = none`.
3. **Auto-confirm subjects** -- `payment received`, `your receipt`, `welcome`, `your statement is ready`, `is up`, `deployed`, `confirmation` etc → `Ask = info-only` regardless of body.
4. **Cold sales / marketing patterns** -- domains never seen before, generic outreach signal phrases ("we help companies like yours", "growth specialist", unsolicited demos) → `Ask = info-only` (clear default).
5. **Calendar invites or RSVP requests** -- `.ics`, "menu choice", "please confirm attendance", "you've been invited" → `Ask = rsvp`.
6. **Direct question to Pete** ending in "?" + thread is external → `Ask = reply`.
7. **Forwards from internal staff** with phrases like "needs paying", "needs reviewing", "needs your eye" → `Ask = decision` or `review` per content.
8. **Document shares** (Google Doc invites, attached quotes/invoices/reports) where Pete hasn't acknowledged → `Ask = review`.
9. **Recovery / status alerts** (UptimeRobot UP, Vercel deploy succeeded, GitHub workflow passed, Sentry releases) → `Ask = info-only`. **Do NOT classify as `reply`/`review`/`verify`** -- a system saying "I'm fine now" is not an ask. Pete corrected this 1 May 2026 (#22 Vercel, #26 UptimeRobot).
10. **Empty-body forwards from internal staff** → `Ask = reply` (Pete owes a "what did you mean?" to the forwarder).

**The classifier emits a DRAFT.** Claude reviews each `ask_classification` against the body content and corrects in-line before building the ops table. The draft is a starting point, not a final answer.

**Without `/tmp/triage-ask.json` (or equivalent in-memory equivalent) populated for every thread, the round cannot be built.** Step 5 refuses to render. Step 6.0 validates that every row carries a non-empty `Ask` cell with a value from the vocabulary.

### Step 4.6: Enquiry-reply recognition (NEW — hand tracked enquiries to the Engine)

A reply to a training enquiry we already sent is NOT a generic inbox row — it's the next turn of a tracked enquiry, and it belongs to the **[[workflow-design|Enquiry Engine]]** (lifecycle in the Portal CRM, learning in `vault_notes`). Before Action-verb selection, flag each thread that is an enquiry reply. A thread is a tracked enquiry reply if ANY of:

1. **Thread id match** — the Gmail `thread_id` appears in an enquiry note's frontmatter:
   `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "select title from vault_notes where type='enquiry' and frontmatter->>'thread_id' = '<thread_id>'"`
2. **Sender is a CRM contact** — the from-address matches a Portal CRM contact (esp. one at stage New/Quoted). Quick check via the Portal REST (`contacts?email=eq.<addr>`), or it carries the `Projects/SY-Training-Enquiries` Gmail label.
3. **Subject/content** is clearly a reply on a course enquiry we're handling (cross-check the corpus).

When matched, **don't classify it as a generic `reply`/`task`** — route it to the Engine:
- Run the loop (`reply to enquiry in {company}`): RETRIEVE precedents (`cc-knowledge-api.py semantic …`), read this contact's timeline, draft the next reply **Mode B** (Pete signs off).
- Log the inbound reply + advance the contact + (re)set/close the chase in ONE write:
  `VAULT=/tmp/pbs python3 /tmp/pbs/te-log.py --in <touch.json> --apply` (activity kind `reply`, stage move if it progressed; te-log closes the prior chase and sets the next).
- In the ops table mark these `Ask = reply` but note **"→ Enquiry Engine"** so they're handled by the loop, not a generic Replies-tray label. (A booking forward — "Sent to Sue" — moves the contact to **Customer/won**.)

This is the cross-skill hook the cockpit relies on: triage recognises the reply, the Engine owns the lifecycle + learning. Detection mirrors the daily `enquiries` sweep.

### Step 5: Per-row Action verb selection (constrained by `Ask`)

Once `Ask` is set, the Action verb is constrained:

| Ask | Permitted verbs | Default proposal |
|---|---|---|
| `none` | File / Keep in inbox / Clear | File home label |
| `info-only` | File / Clear | File home label (or Clear for pure marketing) |
| `reply` | **Reply or Hand to {person}** | Reply (tray) in Team-General/{prefix}-General section (or AT-General/PA-General if explicitly Family/Personal) |
| `decision` | **Reply / Task / Hand to** | Pay/process/build → Task P2 (CC only); executed-by-replying → Reply (tray). P1 if deadline tight |
| `review` | **Reply / Task / Hand to** | Portal/code/finance review → Task P3 (CC only); review-then-reply-to-sender → Reply (tray). P2 if recipient waiting |
| `rsvp` | **Reply** + calendar event proposal | Reply (tray) in PA-General + calendar |

**The combo (Reply + Task):** when a `reply`/`decision`/`review` needs work done *before* Pete can reply (e.g. build the quote, check with the accountant, then reply), propose **Reply + Task** — a `Reply` with a Task cell holding the prep work. The `Replies` label tracks the reply; the `reply`-tagged task tracks the work; they're independent. This is the third outcome — don't force it into "either reply or task."

**The triage verbs:**

| Verb | Maps to | When to use |
|---|---|---|
| `File X` | add label X + remove INBOX | Default for triage when `Ask=none/info-only`. Thread is filed under its home and out of inbox. |
| `Keep X` | add label X, leave INBOX | Exceptional. Thread should stay visible until Pete actions it (e.g. an alert he's actively chasing). Always include the reason. |
| `Clear` | remove INBOX, no label | Transient noise -- auto-acks, expired promos, one-off junk. |
| `Skip` (or `-`) | no Gmail call | Defer to next round / Pete handles manually. |
| `Reply in {project}` | add filing label + add `Replies` + remove INBOX (one atomic call). **NO task** — the Replies label IS the record. | **Tray items only**: Pete owes a response via the email (reply / RSVP / sign-and-return). No `Pn` — the Replies walker is oldest-first. |
| `Reply in {project}` **+ a Task cell** (the combo) | the `Reply` primitive above **AND** a CC task for the prep work: `INSERT INTO tasks (…, tags, notes)` into `public.tasks` with `tags` containing `'reply'`, the **Mimestream + Gmail link** in notes (mandatory), and `[no-sync-close]` | **The overlap** — a reply gated on doing work first (e.g. *build the quote, then reply*). The `Replies` label tracks the reply; the `reply`-tagged task tracks the work. They're independent: closing the task does NOT clear the reply, and clearing the reply does NOT close the task. |
| `Task Pn in {project}` | add filing label + remove INBOX (NO Replies label), plus a CC task (`INSERT INTO tasks (…, project_slug, …)` into `public.tasks`) whose notes carry `[no-sync-close]` | **CC-only items**: bills, cert batches, work items — the doing happens outside the email. Sync never couples these to label state. |
| `Hand to {person}` | add `Delegated`, plus a CC task — `INSERT INTO tasks (…, project_slug, …)` into `public.tasks` with `project_slug='Team-General'` (Delegated track) + draft chaser | Separate flow. Tasks carry the `project_slug` NAME (e.g. `'Team-General'`), never a GID. |

Bare `Label: X` is **forbidden** in proposals.

**Transition guard (bed-in period):** if Pete types `task` on a row whose Ask is clearly reply-shaped (reply/rsvp), honour the verb but flag once: "looks reply-shaped — Action instead?". Never silently substitute one verb for the other.

**Task routing (which project/section):** follow the task-routing decision tree at `[[vault-routing#task-routing-decision-tree]]` — related project/bucket first, else `{prefix}-General`, bucket/project escalation only by proposal.

Label-routing logic (which X to pick once verb is chosen):
- Sender domain matches a known customer/supplier/project label → propose that label
- Sender domain matches an existing `Projects/{prefix}-{slug}/` vault folder BUT no Gmail label exists → demand-driven label rule
- Sender domain matches a `Personal/{area}/` vault folder (scouts, los-claveles, passion-fit, freemasonry, finance, family) BUT no Gmail label exists → demand-driven label rule applies same way (propose `Personal/PA-{area}` or `Personal/AT-{family-area}` Gmail label creation in same operation as filing)
- Sender is a utility (Stripe, GitHub, Supabase, Vercel) → propose appropriate filing label
- Sender is unknown → propose `Clear` or surface for discovery

**Personal area sender-pattern hints** (from 2026-05-03 email scan; use to nudge routing):
- BSO Southern Europe / scout association / Florida Jamborette / scouts.org.uk → `Personal/PA-Scouts` label, vault enrichment to `Personal/scouts/`
- Bryn Hart / Provincial Grand Master / Tyldesley Lodge / Imperial George 78 / `*@masonic*` / "Sincerely & Fraternally" body → `Personal/PA-Freemasonry` label, vault enrichment to `Personal/freemasonry/`
- Los Claveles utility invoices (LPLIR/LPLR codes), Carlos / committee correspondence → `Personal/PA-Los-Claveles` label, vault enrichment to `Personal/los-claveles/`
- HSBC, pension provider, HMRC personal Self Assessment → `Personal/PA-Finance` label, vault enrichment to `Personal/finance/`
- Passion Fit coaching / befabulous.me → `Personal/PA-PassionFit` label (demand-driven), vault enrichment to `Personal/passion-fit/`
- Family / school / household / Spanish admin / car insurance personal → AT- supplier or family matter, vault enrichment to `Personal/family/{Sub Area}/` (**TitleCase With Spaces** -- Family Members/, HMRC Personal/, Spanish Admin/, Vehicles/, Legal/, Property/, Travel/, Health/). Naming locked 2026-05-03 night to keep the 2-way sync from creating duplicates. **AT- prefix routes to `Personal/family/` (NOT the retired `Businesses/ashcroft-family/`).**

**Default due date** by task priority (Atlantic/Canary tz):
- P1 → today + 2 days
- P2 → today + 7 days
- P3 → today + 30 days
- P4 → no due date

**Durable layer** -- pull content into the entity's home (see Rule 13). `vault-enricher.py` does this on every filed/task-linked thread:
- Attachments worth pulling (quotes, contracts, reports, invoices, certs, photos, specs, datasheets, signed forms) → the entity's **Google Drive** folder under a dated sub-folder. Skip signature cruft.
- Substantive body content → a **`vault_notes`** record for the entity.
- Decisions of strategic note → **`vault_notes`** (`type: decision`, ingested).
- **Personal-area attachments**: scout training certs / lodge summons + menus / Los Claveles utility bills / Passion Fit coaching docs / personal finance docs → the matching **Personal** area folder in Google Drive.
- **Family attachments**: travel certs, vehicle docs, Spanish admin, school reports, medical → **Ashcroft Family/** in Google Drive under the matching sub-area (**TitleCase With Spaces** -- e.g. `Travel/`, `Vehicles/`, `Spanish Admin/`, `Health/`).

**Calendar** -- detect flights/hotels/cars/meetings; propose with default tz Atlantic/Canary, default calendar Pete's primary.

### Step 5.5: Present the round in STAGES (NEW in v1.8)

The round is no longer a single 20+ row table. It's a sequence of stages, presented in fixed order, each stage capped at 10 rows per sub-batch.

**Stage 0: print the Mode A/B reminder block** (always, every triage).

**Stage 1: Auto-filter / Mode B candidates** -- recurring noise.

```
─── Stage 1 of 4 -- Mode B / auto-filter candidates (8 threads) ───

Proposed filters first (these go in BEFORE we touch threads):

  Filter A:  from:*@stripe.com  →  Receipts          (Mode B, auto-archive)
  Filter B:  from:*@medium.com  →  Newsletters       (Mode B, auto-archive)
  ... (up to 5 filters per stage; more = next stage continuation)

Confirm filters? (y / except B / n)

Then per-thread:

| #  | Ask        | From / Subject (≤60ch)                  | Action                    | Task | Vault | Calendar |
|----|------------|-----------------------------------------|---------------------------|------|-------|----------|
| 1  | info-only  | Stripe -- April receipt                 | File Receipts       | -    | -     | -        |
| 2  | info-only  | Medium digest                           | File Newsletters    | -    | -     | -        |
| ... up to 10 rows per sub-batch

Reply: go / except #N: <new verb> / cancel
```

**Stage 2: Customers / Suppliers / Projects / Accreditations** -- relationship mail (capped at 10 per sub-batch).

**Stage 3: Internal Sygma / CD content** -- forwards and internal threads.

**Stage 4: Personal + ambiguous one-offs**.

**Each stage executes before the next is presented.** This is structural -- if Pete bails after Stage 1, Stages 2-4 never appear. He can resume by re-running `triage`.

Required ops-table columns: `#`, `Ask`, `From / Subject`, `Action`, `Task`, `Vault`, `Calendar`. Use `-` for empty cells.

**Rendering rule (bed-in period):** task-bearing verbs always render with their destination tag — `Reply (tray)` / `Task P3 (CC only)` — so the tray/no-tray difference is loud in every table.

**Hard rule: Ask cell must be present and from the vocabulary.** Empty `Ask` = malformed row.

**Hard rule: Task cell placement.** A Task entry may appear only on a `Reply` (combo) or `Task` verb (Task entry on any other verb = malformed). A `Task` verb REQUIRES a Task entry. A `Reply` verb does NOT — it's label-only by default; a Task entry on it is allowed but optional (the combo). `Hand to` requires a Delegate entry.

**Hard rule: Ask ⇔ verb match.** `reply` / `rsvp` require `Reply` or `Hand to`. `decision` / `review` require `Reply`, `Task`, or `Hand to`. Validator refuses to execute on mismatch.

### Step 6.0: Validate the ops table BEFORE presenting any stage's diff

Run every stage's ops table through the validator. The validator is in `/tmp/pbs/triage-validator.py`; or inline the same checks. v1.8 adds the `Ask` checks:

```python
def validate_ops(ops):
    errors = []
    valid_asks = {"none", "info-only", "reply", "decision", "review", "rsvp"}
    valid_verb_starts = ('File ', 'Keep ', 'Clear', 'Skip', '-',
                        'Reply', 'Task ', 'Hand to ')  # 'Reply' has no trailing space (matches bare "Reply")

    for op in ops:
        verb = op.get('action', '').strip()
        ask = op.get('ask', '').strip()
        task = op.get('task') or ''
        delegate = op.get('delegate') or ''

        # 1. Seven-verb rule
        if not any(verb == s.strip() or verb.startswith(s) for s in valid_verb_starts):
            errors.append(f"Row {op['row']}: Action '{verb}' is not one of the allowed verbs")

        # 2. Ask vocabulary
        if not ask:
            errors.append(f"Row {op['row']}: Ask cell is empty -- history pre-pass not run?")
        elif ask not in valid_asks:
            errors.append(f"Row {op['row']}: Ask '{ask}' not in vocabulary {valid_asks}")

        # 3. Ask ⇔ verb match
        is_action_ask = ask in {"reply", "decision", "review", "rsvp"}
        task_bearing = verb.startswith(("Reply", "Task ", "Hand to "))
        if is_action_ask and not task_bearing:
            errors.append(f"Row {op['row']}: Ask='{ask}' implies action but verb is '{verb}' -- malformed")
        if ask in {"reply", "rsvp"} and verb.startswith("Task "):
            errors.append(f"Row {op['row']}: Ask='{ask}' is reply-shaped — verb must be 'Reply' or 'Hand to' (transition guard: honour Pete's explicit override after flagging once)")
        if task_bearing and ask not in {"reply", "decision", "review", "rsvp"}:
            errors.append(f"Row {op['row']}: verb is '{verb}' but Ask='{ask}' doesn't imply action -- malformed")

        # 4. Task cell placement (2026-06-25 decoupling)
        #    A task cell is PERMITTED on Reply / Task; REQUIRED only on Task.
        #    Reply is label-only by default (a task is optional — the overlap case).
        has_task_cell = bool(task and task != '-')
        is_task_permitted = verb.startswith(('Reply', 'Task '))
        is_task_required = verb.startswith('Task ')
        if has_task_cell and not is_task_permitted:
            errors.append(f"Row {op['row']}: Task cell present but Action is '{verb}' -- malformed")
        if is_task_required and not has_task_cell:
            errors.append(f"Row {op['row']}: Action is '{verb[:12]}...' but Task cell is empty -- malformed")

        # 5. Delegate cell ⇔ Delegate verb (same shape)

    if errors:
        raise ValueError("Ops table malformed:\n" + "\n".join(errors))
```

If you cannot import the validator, inline the same checks. **Do not skip them.**

### Step 6.1: Pre-execution diff -- chat-visible, not console-only

After validation passes, render the verb→primitive table TO THE CHAT (so Pete sees it) for the current stage only:

- Row number + thread id (truncated)
- Resolved Ask + Action verb
- Exact Gmail call about to fire
- CC task call (if any)
- Vault writes (if any)
- Calendar events (if any)

Then ask: `go` (proceed), `cancel` (stop), or `change row N` (adjust). One confirmation per stage.

### Step 6.2: Execute -- iterate the ops table 1:1

Single-shape batch loops are forbidden. Iterate row-by-row with the verb→primitive map (unchanged from v1.7):

| Action verb in row              | Gmail primitive                                       | Other side-effects                                     |
|---------------------------------|-------------------------------------------------------|--------------------------------------------------------|
| `File X`                  | `modify_thread(id, add=[X], remove=["INBOX"])`        | **MUST run vault-enricher** (see below)                |
| `Keep X`       | `modify_thread(id, add=[X])`                          | **MUST run vault-enricher** if X is a filing label     |
| `Clear`                | `modify_thread(id, remove=["INBOX"])`                 | -                                                      |
| `Skip` / `-`                    | (no Gmail call)                                       | -                                                      |
| `Reply in {project}`   | `modify_thread(id, add=[X, Replies_label], remove=["INBOX"])` (atomic) | **NO task** — the Replies label IS the record. **MUST run vault-enricher** on the thread → matching vault folder. *Combo only* (the Reply+Task prep task): `INSERT INTO tasks (…, tags, notes)` with `tags` containing `'reply'`, the **Mimestream + Gmail + Finder links (Mimestream mandatory)**, and `[no-sync-close]` (so the label and task stay independent). |
| `Task Pn in {project}`     | `modify_thread(id, add=[X], remove=["INBOX"])` — **NO Replies label** | `INSERT INTO tasks (…, project_slug, …)` into `public.tasks` with Mimestream + Gmail + Finder links + **`[no-sync-close]` marker line appended to notes** + **MUST run vault-enricher** on the thread → matching vault folder |
| `Hand to {person}`          | `modify_thread(id, add=[Delegated_label])`            | `INSERT INTO tasks (…, project_slug, …)` into `public.tasks` with `project_slug='Team-General'` (Delegated track) + Mimestream + Gmail + Finder links in notes + draft chaser + **MUST run vault-enricher** on the thread → matching vault folder |

### CC task creation

`Task` and `Hand to` create a row in **`public.tasks`** (CRUD via `cc-sql.py`). **`Reply` does NOT create a task** — it's label-only (the Replies label is the record). It creates a task only in the **Reply + Task** combo (a reply gated on work first); that task is **tagged `reply`** (so it shows in the CC *Replies* filter — "what work is gating a reply"), carries the **Mimestream + Gmail link** (mandatory — it's how the to-do gets you back to the thread to reply, and keeps the sync's task↔thread tie), and `[no-sync-close]` (so the label and task stay independent). The `project_slug` column carries the project NAME (e.g. `'Team-General'`, `'SY-General'`), never a GID.

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "INSERT INTO tasks (name, priority, due_on, entity_slug, project_slug, notes) VALUES ('Reply to Wayne (Clancy) about UKPN DSR meeting time', 'P2', '2026-07-01', 'SY-Clancy', 'Team-General', '<Mimestream link>\n<Gmail link>\n<Finder link>\nsummary…')"
```

- `priority` = the Pn from the verb (`'P1'`/`'P2'`/`'P3'`/`'P4'`).
- `due_on` = derived from priority per the default-due-date schedule below.
- `entity_slug` = the customer/supplier/project slug the thread filed under (e.g. `'SY-Clancy'`); `project_slug` = the routing project NAME.
- For `Task`, append the `[no-sync-close]` marker line to `notes`.
- For a **Reply + Task** combo, the task ALSO sets `tags` to include `'reply'` (additive — don't clobber existing tags) **and** carries `[no-sync-close]`; the Mimestream + Gmail link in `notes` is **mandatory** (it ties the to-do to the email you owe a reply to).

### Vault enrichment is non-negotiable

**Every triage row that applies a filing label (Customers/, Suppliers/, Projects/, General/, Personal/{area}/, Accreditations/, Businesses/) MUST call `vault-enricher.py` on the source thread immediately after the Gmail label is applied.** No exceptions other than the documented skip rules (PA-General, operational labels, signature-only auto-reply threads).

The helper exists and is documented — what was missing in practice was the actual call during runs. The enricher call is a verb side-effect so context and files are always pulled into the durable layer when triage files a thread.

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/vault-enricher.py {thread_id} "{target-entity}"
```

- **target-entity** = the filing label converted to its entity slug (e.g. `SY-AppearOnline`, `SY-General`); the enricher resolves it to the entity's Drive home + `vault_notes` record
- **For supplier/customer**: enricher auto-pulls substantive attachments to the entity's Drive folder, body extracts to its `vault_notes` record, contacts to the CC record's Key contacts
- **Result is idempotent**: re-running on the same thread is safe (skips files that already exist)
- **Skip rules baked in**: PA-General, operational labels, signature cruft, auto-reply threads

After the enricher returns, report the count of attachments pulled + extract path written + contacts added in the triage row's outcome line.

### Links required in task notes

Every CC task created by triage must include both (and for a **Reply + Task** combo the Mimestream link is **non-negotiable** — it's what links the to-do back to the email Pete owes a reply to, and keeps the sync's task↔thread tie intact):

1. **Linked Gmail thread** — Mimestream link AND Gmail web URL (Mimestream first):
   - `https://links.mimestream.com/g/pete.ashcroft@sygma-solutions.com/t/{thread_id}`
   - `https://mail.google.com/mail/u/0/#all/{thread_id}`
   - Same `{thread_id}` in both forms.

2. **Drive link to the entity's home folder** if the task is tied to a project, customer, or supplier:
   - Generated via `/tmp/pbs/vault-finder-link.py {entity-slug} [section-name]`
   - Returns the Google Drive folder URL (or the cloud-synced mount path) for the entity's home
   - If no matching home exists, omit the link rather than emit a broken one

Reason: Mimestream opens the source thread in one click; the Drive link opens the working folder in one click. Both eliminate the "find the right place" friction at the moment of action.

The filing and Calendar columns drive their own side-effects (see Step 5).

### Step 7: Move to next stage (or finish)

After Stage N executes, present Stage N+1's reminder + filter proposals + ops table. Repeat until all stages done.

### Step 8a: Replies walker — offer to deal with the tray (added 2026-06-06)

After the last stage executes, BEFORE the sync offer, ask:

```
Shall we look at your Replies? (N in the tray, oldest is {X}d)  (y/n)
```

On **y**, walk the tray **one item at a time**:

- **Group by thread (or by TASK in the overlap case)** — most tray items are now label-only (no task); a multi-thread overlap task (e.g. a DocuSign chased across 3 threads) is ONE walker item listing all its threads.
- **Order: oldest last-message first** (most overdue at the top).
- For each item present: the thread(s) summary, the linked task **if one exists** (priority + due) — most items are label-only — and a **suggested response** — a ready-to-iterate draft in Pete's voice. **Read [[voice-principles]] BEFORE drafting the first suggestion, and run the em-dash / en-dash / double-dash grep on every draft before presenting it.**
- Outcomes per item (Pete picks):
  - **send** — iterate the draft if needed, send via gmail-api, then immediately strip the Replies label (+ close the linked task if one exists + audit comment "closed via Replies walker — reply sent {date}"). No waiting for next sync.
  - **defer** — untouched, move to next item.
  - **already done** — strip Replies label (+ close the linked task if one exists + audit comment).
  - **de-tray** (this is really work, not a reply) — if a task is already linked, append `[no-sync-close]` to its notes then strip the Replies label; if it's label-only, make it a `Task` FIRST, then strip the Replies label. CC-only from here.
- End with a one-line tray summary: `Tray: started N, sent X, closed Y, de-trayed Z, deferred W.`

The walker is also invocable standalone at any time via the verb "replies" (or legacy "actions") (see When to invoke).

### Step 8b: End of triage -- OFFER sync, do NOT auto-chain

After the walker (or its decline), present the summary (Step 9) and OFFER:

```
Triage complete.
{summary}

Run `sync` now to reconcile Gmail/CC task state? (y/n)
```

- y → invoke email-task-sync skill
- n → end here

**Triage NEVER calls `sweep`. Triage NEVER auto-chains anything that calls `sweep`.** The sync itself doesn't sweep, but the chain is opt-in to keep the principle clean.

### Step 9: Final summary

```
Triage complete.

Stages run: 4 of 4
- Stage 1 (Mode B / noise):     8 filed (5 new filters created)
- Stage 2 (relationships):       6 filed, 2 actioned (tray), 1 tasked (CC only)
- Stage 3 (internal):            4 filed, 2 tasked (CC only)
- Stage 4 (personal + one-off):  2 filed, 1 calendar event

Total: 22 threads → 0 inbox.
Replies added: 2 (label only — no task; tray now reflects them).
CC tasks created: 3 (all CC-only via Task — 1 P1, 2 P2).
Calendar events: 1.
New supplier folders: 1 (SY-NewSupplier with vault README + Mode A filter).
Vault pulls: 3 attachments.
Replies walker: ran — sent 2, deferred 1, de-trayed 0. Tray now 4.

Run `sync`? (y/n)
```

## Discovery flow (inline pattern detection)

During the round, watch for patterns and surface inline AT STAGE BOUNDARIES (not mid-stage):

### A. New customer/supplier discovery

- 3+ emails from an unknown sender domain in rolling 30 days → suggest creating a customer/supplier folder + label + filter (full structure proposal -- see Label creation pattern)
- Check `email-workflow-state.md` for declined suggestions BEFORE surfacing
- If yes → onboarding ritual (canonical: `[[vault-routing#onboarding-rituals]]`)
- If no → record decline

### B. Demand-driven project label

When an email is unlabelled with no Customers/Suppliers/Projects label AND the sender domain (or subject keyword) matches an existing `Projects/{prefix}-{slug}/` vault folder, propose creating the matching Gmail label inline.

Cross-link: `[[vault-routing#demand-driven-project-gmail-labels]]`.

### C. Auto-filter pattern (per-stage proposal)

Surfaced at the TOP of Stage 1 (Mode B candidates) plus optionally at the top of Stages 2-3 if Mode A patterns emerge.

Compute observations fresh from Gmail each run:
- `gmail-api.py search "label:{label-name} newer_than:90d"` per relevant label
- group by sender domain, count
- cross-check `gmail-api.py list_filters()` for existing coverage

When 3+ observations on same label AND no filter AND not in declined list → surface inline:

```
Pattern detected: 4 emails from *@newsupplier.co.uk → Suppliers/CD-NewSupplier (last 30d), no filter exists.

Mode? (A = label, leave in inbox / B = label + auto-archive / n = no filter)
```

Mode B for noise; Mode A for things Pete needs to triage. **Filters are NEVER created without explicit confirmation.**

### D. Filter broadening

When an email is labelled but the actual sender is a subdomain or variant the existing filter doesn't cover:

```
Existing filter from:*@newsupplier.co.uk → Suppliers/CD-NewSupplier
This email is from payroll.newsupplier.co.uk -- subdomain not covered.

Broaden filter to from:*@*.newsupplier.co.uk OR keep narrow? (broaden / keep / n)
```

### E. Strategic routing patterns

Routing decisions repeated 3+ times → append to `[[vault-routing#observed-patterns]]` "awaiting confirmation". 5+ confirmed without override → propose promotion.

## New-customer / new-supplier onboarding ritual

> Canonical: `[[vault-routing#onboarding-rituals]]`. Operational version below mirrors that.

When Pete says yes to a discovery suggestion:

1. Confirm name, prefix (SY/CD), relationship type, known contacts.
2. Ensure Gmail parent label exists (`Customers` or `Suppliers` top-level must exist).
3. Create Gmail label `{Category}/{prefix}-{slug}` with business colour (SY blue, CD yellow, etc).
4. Create the entity's **Google Drive** home folder + a **CC record** (account-customer → CC `account_*`; otherwise a `vault_notes` record), key fields pre-filled.
5. Create auto-filter (Mode A by default for customers/suppliers): `from:*@{domain} OR to:*@{domain}` → apply label, leave in inbox.
6. Backfill: scan Gmail for messages from/to sender in last 6-12 months, apply label retroactively.
7. **Matter analysis**: 5+ thread backfill → cluster by base subject, identify substantial matters, propose Drive sub-folders.
8. **Pull attachments per matter**: download non-cruft attachments to the entity's Drive folder under a dated sub-folder.
9. Report.

## Label creation -- full structure proposal pattern

Whenever the skill suggests creating a new label, ALWAYS propose the full chain:

```
Suggested new structure for {entity}:

  GMAIL
    Label:        {parent}/{slug}
    Parent:       {parent} (exists | CREATE)
    Colour:       {colour name + hex}
    Auto-filter:  {filter spec}  (Mode A | Mode B -- always confirmed)

  DRIVE + CC
    Drive home:   {Drive folder}  (CREATE | EXISTS | NOT NEEDED -- workflow only)
    CC record:    {vault_notes | account_* -- CREATE | EXISTS}

  CC TASKS (public.tasks)
    project_slug: {existing NAME | NEW NAME | NONE -- tasks go in Team-General with entity_slug={prefix}-General}

  Create all of the above? (y / edit / n)
```

Home decision per entity type (default proposal, Pete can override):

| Entity type | Gmail label | Drive home + CC record | CC project (`project_slug`) |
|---|---|---|---|
| Customer | YES (parity hard) | YES (parity hard) | NO (route via Team-General + `entity_slug`) |
| Supplier | YES (parity hard) | YES (parity hard) | NO (route via Team-General + `entity_slug`) |
| Project | demand-driven | YES | YES (own `project_slug` NAME) |
| Invoice batch | YES | YES | YES |
| Accreditation body | YES | YES | YES |
| Workflow tag (Replies, Delegated) | YES | NO (navigational only) | YES if applicable |
| Personal hobby/cluster | optional | optional | NO |
| One-off cluster (e.g. Travel) | maybe | NO | NO |

## Rules

1. **SWEEP IS SACRED -- on-command ONLY.** Triage MUST NOT call `sweep`, MUST NOT offer `sweep`, MUST NOT chain to anything that calls `sweep`. The point of `Keep X` is the thread STAYS in inbox until Pete acts. An auto-sweep defeats the verb. Sweep happens when Pete types `sweep` -- never otherwise. End-of-triage offers `sync` (which doesn't sweep), opt-in only.
2. **Mandatory History pre-pass + Ask classification.** Step 4 must run for every thread before Step 5 builds the table. Without `Ask` populated from the fixed vocabulary, the row is malformed and Step 6.0 refuses.
3. **Mode A/B reminder block at the top of every triage.** Print verbatim before any other work. Drift caused mid-round confusion.
4. **Staged batches, 10-row hard cap.** Stages run in fixed order (1 noise → 2 relationships → 3 internal → 4 personal). Each stage gets its own go/cancel. No more 25-row tables.
5. **Never auto-execute structural changes without confirmation.** Filters, labels, folders, new `project_slug` values -- all require Pete's y/edit before creation.
6. **Apply existing labels to existing threads -- no confirmation needed.** The triage decision is the confirmation; the labelling itself is the action.
7. **Respect "no" decisions.** Record declined suggestions in `Library/processes/email-workflow-state.md`. Never re-suggest.
8. **Pattern suggestions surface at stage boundaries, not mid-stage.** Keep stages clean; pattern proposals are the bridge between stages.
9. **Handle sub-requests gracefully.** "Hold this stage, let's onboard Clancy first" → pause, run onboarding, then resume.
10. **Calendar proposals never auto-create.** Always show event details (incl. tz + calendar) for confirmation.
11. **Inbox zero is the goal.** End-of-triage: any remaining untriaged items have explicit "skipped" or "I couldn't decide -- here" notes.
12. **Pattern learning is two-tier**: tactical (auto-filter, per-stage proposal) and strategic (routing rule promotion, recorded in `[[vault-routing#observed-patterns]]`). Both confirm-before-create.
13. **Pull attachments + body extracts into vault.** Filing isn't done when the Gmail label is applied. See Step 5 Vault. Skip signature cruft.
14. **Matter granularity.** Substantial topics get matter folders; one-offs go in `general/`. Threshold: 3+ messages OR ongoing topic OR significant attachments OR live commercial outcome.
15. **Ops table is the source of truth for execution.** Step 6 iterates the table row-by-row, never a generic batch loop. Bare-label verbs (`Label: X`) forbidden. Pre-execution diff (Step 6.1) mandatory.
16. **Read full thread, not just the latest message.** History pre-pass is structural. "Pete already replied" alone is sufficient to default `Ask = none`.

## Vocabulary (single-verb actions outside the triage loop)

Pete can invoke specific actions without full triage:

- "label {thread} as X" -- just label, keep in inbox
- "file {thread} under X" -- label + archive
- "reply in {project}" -- apply `Replies` Gmail label + filing label + archive thread (atomic). **NO task** — the Replies label is the record; Pn is urgency only. Tray items only: Pete owes a response via the email. *Overlap only* (reply gated on work first): add a `Task` alongside (that task carries `[no-sync-close]`).
- "task Pn in {project}" -- create a CC task (`INSERT INTO tasks (…, project_slug, …)` into `public.tasks`) at priority Pn (`[no-sync-close]` in notes) + filing label + archive thread. **No Replies label** — CC-only work item. Same due-date defaults/overrides. (`project_slug` carries the NAME, e.g. `'Team-General'`; SY-General / CD-General / EA-General are routing names, not GIDs.)
- "replies" / "my replies" (or legacy "actions") -- run the Step 8a walker standalone against the current tray.
- "de-tray this" -- convert a reply-item into CC-only work: if a task is linked, append `[no-sync-close]` to it then strip the Replies label; if it's label-only, make it a `Task` first, then strip the Replies label. Reverse ("tray this") = remove marker (or the task) + re-apply Replies.
- "hand to {person}" -- `Delegated` Gmail label + CC task (`INSERT INTO tasks (…, project_slug, …)` into `public.tasks`, `project_slug='Team-General'`) + draft chaser
- "add to calendar" -- detect from email + create event on confirmation (Atlantic/Canary tz default)
- "pull the attachments into {customer}" -- download + wikilink into matter README
- **"sweep"** (single deliberate verb -- accidental-trigger guard) -- archive every inbox thread with at least one user-applied label. No protect list. **NEVER auto-invoked by any skill, including triage.**
- "create a label for X" -- triggers full structure proposal

## Design references

- Operating manual: `[[email-workflow]]`
- Routing rules: `[[vault-routing]]`
- Gmail API: `[[gmail-api-configuration]]`
- Calendar API: `[[calendar-api-configuration]]`
- Label scheme: `[[gmail-label-scheme]]`
- Sister skill: `email-task-sync` (reconciliation engine; END-OF-TRIAGE OFFERS this, does not auto-chain)
- State file: `Library/processes/email-workflow-state.md`
- History classifier helper: `/tmp/pbs/triage-action-classify.py`
- Validator helper: `/tmp/pbs/triage-validator.py`
- Build history (archived): `[[email-workflow-plan-2026-04-24]]`
