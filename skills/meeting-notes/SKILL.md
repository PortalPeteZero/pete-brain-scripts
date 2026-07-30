---
name: meeting-notes
version: 1.0.0
description: >
  Turn a recorded meeting into a properly detailed written record, then TALK THROUGH what
  happens next rather than silently creating things. Plaud transcribes; Claude writes
  everything else. Run in-session with Pete, never on a schedule. Triggers: "meeting",
  "do the meeting", "write up the meeting", "here's the meeting", "meeting notes",
  "log the meeting", "transcript", "write up this call", "do yesterday's meeting",
  a bare Plaud recording id, or any message pairing a recording with a meeting.
  NOT for Passion Fit seminars — those go to `pf-seminars`.
---

# meeting-notes — the meeting becomes a record, then a conversation

> [!important] The division of labour (Pete, 30 Jul 2026)
> **Plaud transcribes. Claude does the rest.** Plaud's own summary is navigation only — never a
> source of fact and never a source of actions. See "Why we don't use Plaud's summary" below; this
> is measured, not a preference.

> [!important] This is a CONVERSATION, not a pipeline
> Pete runs this in-session because the next actions usually need discussing. The write-up is
> produced without asking. **What happens next is decided together** — never auto-created.

## Step 1 — Get the recording

```
VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py recent --days 14
VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py today
```
Pete usually names it ("this morning's call", "the Clancy one"). Match on date + duration, and
**read the id back to him with its date and length before pulling** if there is any ambiguity —
two meetings the same day is the normal case, not the edge case.

Then pull the verbatim transcript:
```
VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py transcript <file_id> -o /tmp/meeting.txt
```
`--block transaction` (verbatim) is the default and is what you want. Use `pull … --out DIR` if you
also want Plaud's outline for navigating a long one.

**If `file <id>` reports `transcript: unavailable`**, Plaud has not finished (or the audio has not
uploaded from the device). Say so plainly and stop — do not substitute the AI summary.

Full config, limits and the token rule: `[[plaud-api-configuration]]`.

## Step 2 — Establish who was actually in the room

The transcript says "Speaker 1..9". A record naming the wrong person is worse than one naming none.

- **Any name you do not recognise → `VAULT=/tmp/pbs python3 /tmp/pbs/people.py find "<name>"`.**
  It asks all four people-stores at once. Checking one store and reporting "no such person" is the
  exact failure that helper exists to stop.
- **Transcription mangles names.** The same person routinely appears under several spellings, and
  Plaud will happily list them as separate attendees. Resolve to one person before writing.
- **The same speaker number is not one person, and one person is not one speaker number.** Verified
  30 Jul 2026 on a real recording: Plaud listed Speaker 8 and Speaker 9, then noted itself they were
  both Tom.
- Where a voice genuinely cannot be resolved, write "one attendee" — **never guess a name**.

For a customer meeting, read the customer's record first (the canonical reading order in
`[[vault-routing#per-section-rules]]`) so you know the history you are writing into.

## Step 3 — Write the record. Detailed is the point.

**Pete's instruction, 30 Jul 2026: "it's key the summary you produce is super detailed."** The
transcript is the raw material, not the deliverable — but the write-up is where the meeting is
*kept*, so err long. Compression loses the thing that mattered six months later.

**Length:** roughly 1,500–3,000 words for a 45-minute meeting, 3,000–5,000 for 90 minutes. If you
find yourself under that, you are summarising when you should be recording.

**Structure** (adapt the order to the meeting; never pad with empty headings):

- **Standfirst** — date, duration, who was there, what it was for, in a few lines.
- **In one paragraph** — what actually happened, including the bit that mattered most.
- **The substance, in the order discussed** — sectioned, with real headings that name the topic.
  Carry the *specifics*: figures, dates said out loud, site names, job numbers, quantities, prices,
  named people. A number said in a meeting is the whole reason to have a record.
- **Decisions** — what was actually settled, by whom, and the reasoning given.
- **Disagreements and open questions** — record these properly. What was left unresolved is usually
  more useful later than what was agreed.
- **Direct quotes where the wording matters** — a commitment, a concession, a complaint, a price.
  Attribute them.
- **Anything raised but not addressed** — the things that fell off the end.

**Rules that are not negotiable:**
- **Never invent a date, figure, or deadline.** If someone said "next week", write "next week" —
  do not resolve it to a date. This is the precise failure mode of the machine summary.
- **Where a speaker hedges a name, source or figure: verify it, or drop the detail.** Never carry
  the hedge into the record, never manufacture certainty. Flag what you dropped so Pete can ask.
- **Cut the housekeeping** — microphones, "can you hear me", dogs, who is late.
- **Never edit the verbatim transcript.** It keeps whatever the transcriber produced.

## Step 4 — File it

Route by what the meeting was *about*, per `[[vault-routing]]`:

| Meeting | Home |
|---|---|
| Customer / supplier | `Customers/{SLUG}/source/{YYYY-MM-DD}-{topic}/` in `vault_notes` (`type: customer`), plus the entity's Drive folder |
| Internal / team | `vault_notes` (`type: meeting`), tagged with the project slug |
| Passion Fit seminar | **stop — use `pf-seminars` instead** |

Ingest with `cc-knowledge-ingest.py`. Wikilink every project, person and customer.

Keep the verbatim transcript too — file it alongside the write-up so the record can always be
checked back to source.

## Step 5 — THE CONVERSATION. This is the point of running it in-session.

**Do not create anything yet.** Present what you found and talk it through. Pete's words,
30 Jul 2026: *"it's best we discuss next actions for everyone as we might need to raise tasks and
projects, keeping it more flexible."*

Bring him a short, plain list of **everything that looks like it needs to happen, and who it lands
on** — including other people, not just Pete. For each one, say what you think it is:

- **A task for Pete** — with the tier you'd suggest, and a date ONLY if one was actually agreed
  in the meeting. **Never infer a date** (bills are the only standing exception, and a meeting is
  not a bill).
- **Something for someone else** — Jane, a trainer, a supplier, a customer. Say who, and whether it
  needs an email from Pete or is already their ball.
- **Bigger than a task** — if a strand needs its own project or a backlog, say so rather than
  slicing it into six tasks. Don't propose a project for one or two jobs.
- **Nothing to do** — say that too. Not every discussion produces work, and a clean "nothing here"
  is a useful answer.

Then **wait.** Pete decides what becomes a task, what becomes a project, what he'll handle in an
email, and what gets dropped. Ask about anything genuinely ambiguous **one thing at a time**.

## Step 6 — Execute only what he agreed

- **Tasks** → `public.tasks` via `cc-sql.py`. The **date is the switch**: a `due_on` makes it a PD
  automatically, so never write a dated P1/P2/P3. Set `entity_slug` and `project_slug`.
- **Projects** → `VAULT=/tmp/pbs python3 /tmp/pbs/cc-project-api.py "Name" --entity "<...>"` — it
  creates the record, the bucket, the Drive folder and the knowledge home in one call.
- **Backlog** → `cc-park.py park` rather than a pile of P4s.
- **Customer work delivered or promised** → log it against the account (`account_deliverables`) in
  the same session, especially anything goodwill or free of charge.
- **An email Pete needs to send** → offer to draft it, reading `[[voice-principles]]` first.

Report back what you created, in one line each.

## Why we don't use Plaud's summary — measured, 30 Jul 2026

Checked directly against Plaud's own summary of a real 90-minute recording:

- It produced **15 "Action Items" tables, every row carrying a due date it invented.** The date
  `2026-07-13` appears **32 times**. Nobody said it. Its template has a Due Date column, so it
  filled one in.
- It **assigned other people's examples to Pete as tasks**, including one running to `2026-12-31`.
- It **split one person into several** in its own attendee list, and listed two speaker numbers for
  the same man.

So: if Plaud's action items were ever taken at face value, they would pour phantom tasks with
fabricated deadlines into Pete's list. **Use Plaud for the words and the timestamped outline. Take
no fact, name, date or action from its summary.**

## The gate — done means all of these

1. Right recording confirmed (id + date + duration), verbatim pulled, character count stated.
2. Every named attendee resolved — via `people.py` for anyone unrecognised — or written as
   "one attendee". No guessed names.
3. Write-up meets the length guide, carries the specifics, and states anything deliberately dropped.
4. No invented date, figure or deadline anywhere in it.
5. Filed to the correct home per `[[vault-routing]]`, verbatim transcript kept alongside.
6. Next actions **discussed with Pete**, covering other people as well as him.
7. Only what he agreed was created — and reported back.

## Never do these

- Never take an action item, due date, attendee or fact from Plaud's AI summary.
- Never auto-create tasks or projects off the back of a meeting.
- Never put a date on a task because the meeting "implied" one.
- Never run this on a schedule. Pete runs it in-session, deliberately.
- Never use this for a Passion Fit seminar — that is `pf-seminars`, which has its own spec.
