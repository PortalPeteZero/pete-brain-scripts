# closeout — changelog

## 1.0.2 — 2026-07-04 (second hardening round — re-audit found 2 pre-existing defects)
The convergence re-audit confirmed all five 1.0.1 fixes closed cleanly, and a fresh-eyes
pass surfaced two defects that pre-dated the audit (not introduced by the fixes):
- **Honest runnable gate (major)** — a FAILED work_log write still counted toward `recorded`,
  so `UNLOGGED-OWNED REMAINING` and the `--apply` exit code reported 0/clean even though the
  commit never logged (the exact silent-record-failure the skill exists to prevent). Now a
  failed write keeps the commit as still-unlogged: `REMAINING > 0`, exit 2, and a loud warning.
- **Arg parsing (minor)** — `--since`/`--git-dir` as a bare trailing token crashed with an
  IndexError; now a clean one-line usage error.

## 1.0.1 — 2026-07-04 (same-day hardening after an independent adversarial audit)
A 4-auditor + verify workflow confirmed deliverables and records were complete and the
spine never grabs another session's work in the shipped environment, but flagged five
latent defects in the ownership code. All fixed, each verified with code-level evidence:
- **Structured session match** — `_sibling_subagent_files` claimed a subagent transcript on
  a loose `sid in head` substring, so a foreign file that merely MENTIONED our session id as
  data could be claimed. Now it claims only when a structured `sessionId`/`parentSessionId`
  field equals ours; unconfirmed files stay surfaced. (Was unreachable in the current
  transcript layout, but a real logic hole in the safety test.)
- **No silent drop** — a `gitOperation` line that won't JSON-parse (truncated/corrupt) is now
  counted and surfaced as "N unattributed", per the plan's no-silent-cap rule (was silently skipped).
- **Cross-repo prefix safety** — an owned short SHA that resolves in more than one checkout
  (a 7-char prefix collision) is now surfaced, never auto-placed/logged in any of them.
- **Empty-SHA guard** — `owns()` and `worklog_sha.is_present()` now reject an empty/None SHA
  instead of matching any token.
- **Honest deploy doc** — SKILL.md A3/A4 now states `deploy-for-sha` exit-3 has three causes
  (unverified-author BLOCK, build-not-started, or older than the 100-deploy scan window) and
  the scan window, instead of asserting a single cause.

## 1.0 — 2026-07-04
Initial build, from the plan audited to convergence over 4 rounds
(`Projects/PA-Command-Centre/files/plan-closeout-skill-2026-07-04.md`).

- **The attribution spine** (`session_attribution.py`): proves this session's own commits
  from `toolUseResult.gitOperation.commit.sha` in the transcript — never git stdout text.
  Build-time correction to the plan: `CLAUDE_CODE_CHILD_SESSION` is set even in the real
  claude-desktop main session, so the top-level-session guard keys on the transcript PATH
  (top-level `<sid>.jsonl` = main; under `subagents/` = sub-run), not that env var.
  Verified live: on a 54 MB / 5089-line transcript it returns exactly the session's 3 real
  commits, zero false positives.
- **The record gate** (`closeout-sweep.py`): discovers touched checkouts, maps each owned
  SHA to its repo, logs the mine-and-unlogged commits (idempotent), and surfaces
  not-mine unlogged commits without ever logging them. `--apply` records; default is a
  dry-run report. Proven live to attribute this session's Sygma commits and correctly
  NOT claim other sessions' `pete-brain-scripts` commits.
- **Shared SHA tokeniser** (`worklog_sha.py`): factored out of `worklog.py reconcile` so
  discovery and ownership use one matcher and can't drift.
- **B1 collision guard** (`closeout_ingest_guard.py`): pre-ingest SELECT on `vault_path`;
  classifies NEW / IDENTICAL / UPDATE (safe) vs COLLISION (a different note already there —
  stop, don't overwrite) and NOT_INGESTABLE (authored outside `/tmp/pbs`, would never reach
  the cloud).
- **`vercel-api.py deploy-for-sha`**: maps an arbitrary pushed SHA → its deploy readyState
  (exit 0 READY / 2 not-ready / 3 no-deploy — the non-verified-author BLOCK signature).
- **SSOT fix lands in the same change**: `/brain` Compress Step 7c now logs only commits
  the shared ownership helper proves are this session's own (was: "log every commit
  reconcile flags" — the today-bug root), so the two end-of-session reconcile-writers can't
  grab each other's work whichever runs first.
