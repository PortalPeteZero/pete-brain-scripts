---
name: pf-seminars
version: 1.4.0
description: >
  Add a Passion Fit seminar to the archive, end to end, from nothing but a link or a
  "here's Monday's seminar". Pulls the verbatim transcript out of Plaud or Google
  Recorder, decides whether it even IS a seminar, files it to Drive, writes the fresh
  summary to the house spec, and updates the manifest and corpus note. Pete should never
  have to explain the rules, name the folder, or check afterwards that it landed.
  Triggers: "add seminar", "add this seminar", "new seminar", "here's the seminar",
  "here's Monday's seminar", "log the seminar", a bare recorder.google.com link, a bare
  web.plaud.ai link, or any message pairing a recording with the word seminar.
---

# pf-seminars — add a seminar without being told how

> [!important] The success test (Pete, 27 Jul 2026)
> *"I need to ensure when I do add a seminar I don't have to explain everything and check
> everything."* So: he says "add this seminar" plus a link, and everything below happens
> without a single question back, unless something genuinely needs his judgement.

> [!warning] Boundary — this skill or `meeting-notes`?
> **`pf-seminars` (here)** = a Passion Fit seminar. The output is a **member-facing product** that
> syncs to the portal, written to the teaching-notes spec, banked with `pf-seminar-ingest.py`.
> **`meeting-notes`** = any other recorded meeting — customer, supplier, internal, board. The output
> is an internal record, followed by a **discussion** of next actions with Pete.
> They share only the transcript step. Running the wrong one produces the wrong kind of document,
> so decide this before Step 1. If a recording is genuinely borderline, ask Pete.

## Before anything — load the two records
1. **[[Passion Fit seminars — the corpus definition (what is and isn't a seminar)]]** — what
   counts, the traps in this data, how to get transcripts out.
2. **[[Plan — PF Seminars section in the Passion Fit portal]]** — the summary spec and every
   decision Pete has already made.

Do not ask Pete anything these two answer.

## Step 1 — Is it a seminar?
**Pete's rule: seminars are Monday evenings and run about an hour or more.** But the metadata
lies, so never decide on title, date or duration alone:

| Trap | Verified 27 Jul 2026 |
|---|---|
| **Titles are AI-generated and can be in the wrong language** | A real seminar was titled in **Welsh** (`Cyfarfod Cymunedol Wythnosol`) |
| **`AUDIO-`-prefixed files carry the UPLOAD time, not the session** | One seminar's timestamp was a Tuesday lunchtime |
| **Durations are wrong** | Several files report exactly `300m`; their transcripts are ~40 minutes |

**Decide on the transcript.** A seminar has multiple named participants, someone running the
session, and framework vocabulary — commitment continuum, ipsative, controllables,
high-functioning, coachability, transactional state, intuition scale, latent ability, green line.
A one-to-one coaching call is NOT a seminar. Neither is a Sygma/Clancy meeting, a family
recording, or anything Pete did not run.

If it is genuinely borderline, say so in one line with the evidence and let Pete call it.

## Step 2 — Get the verbatim transcript

**Plaud — use the helper. No browser, no headless caveat** (changed 30 Jul 2026, the day Plaud
shipped CLI access):
```
VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py recent --days 30       # find it
VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py transcript <file_id> -o verbatim.txt
VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py pull <file_id> --out DIR   # all streams at once
```
- `transcript` returns the **verbatim** stream (`transaction`) by default, timestamped and
  speaker-labelled. Verified 30 Jul 2026 against the 27 Jul Drive export of the 07-06 seminar:
  17,346 words / 162 segments in both, word stream identical.
- Ignore the AI summary — we write summaries fresh (see Step 4).
- If it is in the trash, restore it and tag it into the **PF** folder (see Step 5).
- Full config, limits and the token-rotation rule: `[[plaud-api-configuration]]`.

> [!warning] The old browser route is RETIRED — do not use it
> This step used to say "run from Pete's logged-in Chrome, never headless — the auth is a bearer JWT
> in `localStorage` and **cannot be extracted**". That is no longer true, and the hand-scraped
> `api-euc1.plaud.ai` route (with its 26 Oct 2026 token cliff) is dead. Do not drive `web.plaud.ai`
> through `claude-in-chrome` for transcripts. Plaud's *own* search is names-only over the 500 most
> recent, so to find a seminar by what was **said**, query `vault_notes` / `drive_files`, not Plaud.

**Google Recorder** (a `recorder.google.com/<uuid>` share link) — still browser-driven, and awkward,
so follow exactly. Run from Pete's logged-in Chrome (`claude-in-chrome`), never headless:
- The page is deep shadow DOM. `get_page_text` returns nothing.
- Walk shadow roots for `RECORDER-TRANSCRIPT-UTTERANCE` (the whole transcript is present, not
  virtualised) using a recursive walker that descends into each node's `shadowRoot`.
- **Downloads, `fetch` to localhost and form POST are ALL blocked** by the page's security policy.
  Do not retry them. The route that works: `console.log` the transcript in ~2,000-character
  numbered chunks, call `read_console_messages`, and rebuild from the file the tool spills to disk
  when the result overflows.
- Recorder gives **no speaker labels** and no summary.

## Step 3 — File it to Drive
`My Drive/Passion Fit/Passion Fit Concepts/Seminars and Transcripts/Plaud Exports (raw)/2026-07-27 full export/`
One folder per seminar, named `YYYY-MM-DD - <title>`. **If the date is not established, name it
`UNDATED - …` and put a header in the transcript explaining why. Never invent a date.**

## Step 4 — Write the summary
**Fresh from the transcript, never Plaud's** — this is a standing rule, not a preference. Plaud's
read like minutes ("Speaker 1 initiated the conversation by asking participants…"); a member
opening this wants teaching notes. **Members never see the transcript**, so the summary IS the
product. Target ~2,000–2,500 words from a 90-minute seminar.

The rule is evidence-backed, and the evidence is worth knowing before you are tempted: measured
30 Jul 2026 against Plaud's summary of the 07-06 seminar, it produced 15 Action Items tables with a
**due date invented on every row** (`2026-07-13` appeared 32 times, and one ran to `2026-12-31`),
assigned other members' example goals to Pete, and listed "Lauren" and "Laura" as two separate
people. Full write-up of the failure modes lives in the `meeting-notes` skill — do not re-derive it.

Structure: standfirst · sectioned teaching notes in the order taught · pull-quotes where the
session turns · tables where the content is genuinely a list · quote bank with attributions ·
story blocks for the anecdotes · what to do with this · anything carried to the next seminar.

Cut the housekeeping (dogs, microphones, tech problems). **Where a speaker hedges a name, source
or figure: verify it, or drop the detail — never carry the hedge to the member, never invent
certainty.** Flag anything dropped so Pete can ask.

**Names (transcription traps — always normalise to these spellings, confirmed by Pete):**
coaches are **Tom**, **Loren** (transcripts mis-render her as "Lauren", "Laura", "Laurel",
"Lolly" — all the same person) and **Lydia** (mis-rendered "Liz", "Lids", "Lyds", "Lindsay",
"Linds"). Never introduce "Laura", "Lauren" or "Liz" as people in a summary. Members: use first
names where the transcript is clear; where garbled, write "one member". Verbatim transcript
files keep whatever the transcriber produced — never edit those.

## Step 4a — VERIFY the summary against the transcript before banking (added 4 Aug 2026)

Unlabelled, garbled transcripts (Google Recorder especially) invite exactly one class of error, and
it happened at scale: the 4 Aug Lydia batch was audited adversarially and **33 findings were
confirmed across 8 summaries** — welded quotes (two transcript moments merged into one), invented
specifics conjured from garble (a year, a race placing, a name), and misattributed speakers.

So: after writing the summary and BEFORE `pf-seminar-ingest.py`, run an adversarial verify pass —
a second read (or a subagent per seminar) whose job is to REFUTE the summary, with the rule that
**a finding must quote the transcript line that proves it**. Check, at minimum:

- **Every direct quote** exists in the transcript as one continuous passage. Joining two moments
  into one quotation is forbidden — mark joins with an ellipsis only within the same speech.
- **Every attribution**: with no speaker labels, "who said it" must be earned from the transcript's
  own evidence (being addressed by name, self-reference, third-person references excluding them).
  When it cannot be earned, write "one member" / "one of the coaches" — never guess a name.
- **Every specific** (year, placing, count, location, age): if it comes from garble, drop it or
  state the uncertainty. A garbled fragment is never a fact.
- **No cross-seminar imports** stated as this evening's content: a fact from another transcript
  (even a verified one) must be labelled as such or left out.
- **Causal links and pairings**: "X was the answer to Y" only if the transcript ties them — the
  27 Jul Pete-course-example weld is the canonical failure.

Fix what the pass finds, then bank. If a claim matters and cannot be verified, the summary states
the ambiguity honestly — same principle as the date rule: never invent certainty.

## Step 5 — Tidy Plaud itself
If the recording was in the trash or untagged, restore it and move it into the **PF** folder so
Plaud's library matches the archive.

**Driving that trash list** (it fights you): setting `scrollTop` on the `vue-recycle-scroller`
does nothing. Real mouse-wheel scroll events work, ~13 rows per call. Get the row index from the
API first (`is_trash=1&is_desc=true`), then scroll to it. **The first click on a row only reveals
its checkbox; a second click ticks it.**

## Step 6 — Bank it (one command, do not hand-roll this)
```
VAULT=/tmp/pbs python3 /tmp/pbs/pf-seminar-ingest.py <summary.md> \
    --date YYYY-MM-DD --duration "1h 29m" --transcript-chars N [--source-url ...] [--date-unconfirmed]
VAULT=/tmp/pbs python3 /tmp/pbs/pf-seminar-ingest.py --index
```
That single call does all of it: mints the record as `type: seminar-summary`, tags it into the
PassionFit corpus, **auto-detects the concepts and injects `[[slug|Display]]` links on first
mention** so the summary joins the concept graph, sets `audience: shared` so `pf-portal-sync.py`
carries it to Frank, and writes the concepts into frontmatter so the by-concept index works.
`--index` then regenerates [[pf-seminar-index]] from the live records — by date and by concept.

**Do NOT ingest a summary by hand with `cc-knowledge-ingest.py`.** You will get an untagged,
unlinked note that no search and no concept page can find.

Then run `VAULT=/tmp/pbs python3 /tmp/pbs/cc-embedder.py` so semantic search picks it up
immediately rather than waiting for the hourly run.

## Step 7 — Push it to the portal (both mirrors, both gates)
The portal is live (phases 2–6 shipped 27 Jul 2026). A banked summary reaches members and Frank
ONLY via the sync — nothing on the portal side is hand-authored:
```
VAULT=/tmp/pbs python3 /tmp/pbs/pf-portal-sync.py --seminars --apply   # the /seminars library
VAULT=/tmp/pbs python3 /tmp/pbs/pf-portal-sync.py --apply              # Frank's grounding mirror
```
Both end in a mandatory gate — every line must PASS. If the seminars gate reports a CONFLICT, a
summary was edited portal-side: reconcile with Pete before touching it (`--force-cc` overwrites;
never use it silently).

Also update `SEMINAR-MANIFEST.md` in the export folder and the corpus note counts.

## Step 8 — Concept figures (if a diagram was taught on screen)
The summaries carry the concept-gallery diagrams inline, anchored to the section where each model
was taught (`seminar_images` on the portal DB, shipped 28 Jul 2026). For the NEW seminar, decide
from the transcript: was a concept diagram actually shown/taught on screen? Most discussion
evenings teach none — **no placement is the correct default; never decorate.** If one was:
insert a row per figure with the service key:
```
POST {project_url}/rest/v1/seminar_images?on_conflict=seminar_id,image_id
  {seminar_id, image_id (from cms_concept_images by title), anchor, display_order}
```
`anchor` = the slugified h2 id (lowercase, strip non-alphanumerics, spaces→dashes, 80 cap — the
renderer's exact rule). **Validate the anchor exists in the stored `summary_md` before inserting**;
a wrong anchor silently drops the figure to the header strip. Photos, screenshares of posts, and
member slides are NOT concept figures — only the gallery diagrams qualify.

## The gate — done means all of these
Do not report finished until every line is true:
1. Verbatim transcript on Drive, character count stated.
2. Seminar/not-seminar decided **on transcript evidence**, not metadata.
3. Date confirmed, or explicitly left blank — never guessed.
4. Fresh summary written to the spec above.
5. Plaud tidy: restored if binned, tagged PF.
6. Manifest and corpus note updated and re-ingested.
7. Counts reconciled: seminars, recordings, hours.
8. `pf-seminar-ingest.py` run (NOT a hand ingest), index regenerated, embedder run — verify with
   `SELECT slug,(embedding IS NOT NULL) FROM vault_notes WHERE type='seminar-summary'`.
9. Both portal syncs run (`--seminars --apply` and `--apply`), both gates ALL PASS — the new
   seminar is in the /seminars library AND in Frank's mirror.
10. Concept-figures decision made from the transcript (Step 8) — placed with a validated anchor,
    or explicitly none.
11. The property records moved: a story line in `Properties/Passion Fit/README.md` is NOT needed
    per seminar, but [[passion-fit-state-of-play]] library counts and the manifest must match.

## Never do these
- Never use `pf-ingest.py plaud` — it is wired for the Plaud **Summary** export, the exact
  material Pete rejected. If it gets in the way, fix that helper rather than working around it.
- Never show a member a transcript.
- Never number seminars by week — label by date. 28 seminars sit across 70 Mondays and
  sequential numbering makes every quiet week read as a missing file.
- Never judge a recording by its title.
