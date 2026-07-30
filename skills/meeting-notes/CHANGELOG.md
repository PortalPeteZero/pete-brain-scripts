# meeting-notes Changelog

## v1.0.0 — 2026-07-30
- Initial version. Built the day Plaud shipped official CLI access.
- Division of labour set by Pete: **Plaud transcribes, Claude writes everything else.** Plaud's own
  summary is navigation only.
- The "never use Plaud's summary" rule is evidence-backed, not stylistic — measured against Plaud's
  summary of a real 90-minute recording the same day: 15 Action Items tables, every row carrying an
  invented due date (`2026-07-13` appeared 32 times), other people's examples assigned to Pete
  (one running to `2026-12-31`), and one person split into several in its own attendee list.
- Detail is the explicit brief ("it's key the summary you produce is super detailed"), so the spec
  sets length floors rather than a summarising target: ~1,500–3,000 words for 45 minutes,
  ~3,000–5,000 for 90.
- Step 5 is a **conversation, not a pipeline** — next actions are talked through with Pete, covering
  other people as well as him, and may become a task, a project, a backlog item, an email or
  nothing. Nothing is created until he decides.
- Deliberately **not automated**. Pete runs it in-session because the discussion is the point.
- Attendee resolution routed through `people.py` (all four people-stores) rather than guessing from
  the transcript.
