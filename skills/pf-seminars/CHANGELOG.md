# pf-seminars Changelog

> This file was created on 2026-07-30. The skill existed from at least 2026-07-27 at v1.2.0, but no
> CHANGELOG.md was ever kept (it was the only skill of 18 without one, despite SKILL.md linking to
> `[[CHANGELOG]]`). Entries before v1.3.0 are therefore not reconstructed — inventing them would be
> worse than the gap. The v1.2.0 skill content is in git history.

## v1.5.0 — 2026-08-05
- **Step 4a gets a runnable gate: `pf-quote-gate.py`.** v1.4.0 added an adversarial verify pass, and
  it worked — but it was a *reading* pass, and reading passes converge on summaries that merely read
  well. Nine summaries survived repeated adversarial rounds until two consecutive auditors returned
  zero, were declared clean and banked. A mechanical check then failed **71% of their quoted spans**,
  including one ("written on the wall in the old studio") that appears nowhere on the tape. By
  contrast, summaries fixed by agents required to mechanically verify their own output came in at
  7.5%, with seven at zero. The difference is the command, not the care.
- The gate extracts every quoted span from `<key>-summary.md`, finds `<key>-transcript.txt`, and
  proves each appears verbatim. Ellipsis joins are checked per side so a weld cannot hide behind one.
- Two parser traps are handled, because both manufacture failures indistinguishable from real ones:
  markdown hard-wraps quotations across physical lines (so blocks are reassembled before extraction),
  and straight quotes must be paired **positionally** — scanning for any `"…"` pair matches the prose
  *between* two quotes whenever a line carries more than one.
- Non-zero is a list to adjudicate, not automatically a defect list: stutter removal is accepted
  practice and fails the gate. In the 4-5 Aug sweep the residue ran ~4 genuine defects per acceptable
  cleanup. Zero is the only result needing no adjudication.

## v1.3.0 — 2026-07-30
- **Step 2 rewritten: the browser-JWT Plaud route is retired.** Plaud shipped official CLI access on
  30 Jul 2026, so transcripts now come from `plaud-api.py` — no Chrome, no headless caveat, and no
  26 Oct 2026 token cliff. The old `api-euc1.plaud.ai` route is kept only as an explicit
  "do not use" warning so a future session recognises the dead method instead of rediscovering it.
  Verified equivalent: the CLI stream and the 27 Jul Drive export of the 07-06 seminar match at
  17,346 words / 162 segments, word for word.
- **Boundary callout added** at the top: this skill is for Passion Fit seminars (a member-facing
  product that syncs to the portal); any other recorded meeting goes to the new `meeting-notes`
  skill. They share only the transcript step, and running the wrong one produces the wrong kind of
  document.
- **Step 4's "never use Plaud's summary" rule now carries its evidence** rather than just asserting
  it — 15 Action Items tables with a due date invented on every row (`2026-07-13` × 32, one running
  to `2026-12-31`), other members' example goals assigned to Pete, and "Lauren" and "Laura" listed
  as two separate people. The full failure-mode write-up lives in `meeting-notes`; this points there
  rather than duplicating it.
- Recorded Plaud's real search limit: recording NAMES only, 500 most recent — so finding a seminar
  by what was *said* means querying `vault_notes` / `drive_files`, not Plaud.
- Google Recorder half unchanged: still browser-driven.
