---
name: closeout
version: 1.0.3
description: >
  Run at the END of any session that touched one of Pete's properties — an SEO site, a
  product app like LeakGuard, the Command Centre itself, or a brand-new build. One
  command that inspects everything the session touched, RECORDS the session's own
  finished work into its correct cloud home (commits → Work Log, knowledge → vault_notes,
  the diary → daily_log), VERIFIES the live state (deploys READY, pages indexable,
  analytics intact, nothing stranded on local disk), and hands Pete a single short menu of
  only the things that need a human decision. It is safe to run while other sessions are
  live: it records ONLY commits it can prove are this session's own (via the transcript's
  gitOperation field — session_attribution.py), never a parallel session's work.
  Success test (Pete's words): "say it once and know everything that needs to be done is
  done, and everywhere updated." Triggers: "/closeout", "close out", "wrap this up",
  "lock up", "are we done / everything saved?", end of any property-touching session.
---

# closeout — the end-of-session "is everything done and everywhere updated?" command

> [!important] What this is FOR
> Pete should be able to say `/closeout` once and trust that (a) everything this session
> finished is recorded in the CC in its correct home, (b) the live systems actually match
> what we think we shipped, (c) nothing important is stranded on the local machine, and
> (d) the only things left in front of him are genuine judgement calls — not a checklist he
> has to remember. It is the SSOT / nothing-local / plans-aren't-live-state discipline,
> automated and run on demand.

> [!warning] The one rule that makes recording safe
> Nothing in the system links a commit to a session. So "mine" is DERIVED from the one
> unambiguous source: **this session's own transcript**. A commit counts as this
> session's ONLY if it appears as `toolUseResult.gitOperation.commit.sha` in the
> transcript (the field the harness stamps on a commit a call actually made) — read via
> `session_attribution.py`, **never** from git stdout text. Everything auto-recorded is
> filtered by that test, so closeout can run beside other live sessions and never touch
> their work. Anything it cannot prove is its own, it only SURFACES.

## Pre-flight

1. Confirm the boot kernel ran (`/tmp/pbs` present, `~/.config/pete-cc/CLAUDE.cache.md` exists). If not, run `python3 ~/.config/pete-cc/pete-session-bootstrap.py` first.
2. All tools below run as `VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py`.

## The flow (six steps — no jargon, no visible loop)

```
0. Identify   assert top-level session; read my owned commits; count OTHER running sessions (courtesy)
1. Sweep      run every applicable check across the whole surface (read-only, always safe)
2. Record     auto-record ONLY work the ownership test proves is mine (commits, notes, diary)
3. Confirm    one quiet re-check that the records landed (internal; not a visible loop)
4. Report     plain-English summary + ONE scannable menu of "your call" items
5. Your call  answer the whole menu in one reply; only delete/send/bin force a separate yes
6. Optional   "Save a resume note too? (y)" — a LIGHT save that skips what closeout already did
```

### Step 0 — Identify (who am I, what did I make, who else is live)

- Run `VAULT=/tmp/pbs python3 /tmp/pbs/session_attribution.py`. It prints this session's owned commit SHAs and, crucially, **whether this is a top-level session**. If it reports a sub-run (`is_subagent: true`) or no main transcript, **STOP** — closeout is a main-session command; say so and do nothing else. (Note: `CLAUDE_CODE_CHILD_SESSION` is unreliable in claude-desktop — it is set even in the real main session — so identity comes from the transcript PATH, which `session_attribution.py` already handles. Don't second-guess it.)
- **Other sessions running** (courtesy only): call `mcp__ccd_session_mgmt__list_sessions` and count those with `isRunning` true, excluding self (normalise the `local_` id-prefix mismatch). Report as a one-line heads-up ("N other sessions running"). If it can't be read, say "couldn't check other sessions" — never guess. This NEVER gates a decision; safety is the ownership test, not this count.

### Step 1 + 2 — Sweep and Record (the deterministic core)

Run the record gate:

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/closeout-sweep.py --apply --human
```

This does the whole A1/A2 record pass deterministically: it finds every checkout this
session touched, proves which commits are this session's own, logs the unlogged-but-mine
ones to the Work Log (idempotent — re-running never double-logs), and SURFACES any
unlogged commit that is **not** mine (never logs it). `UNLOGGED-OWNED REMAINING: 0` at the
end means the record pass is clean. If a commit is mine but its repo isn't checked out on
this machine, it says so — surface it, don't assume it's logged.

Then walk the rest of the manifest below. Every check no-ops when it doesn't apply, so a
pure-Command-Centre session skips all the SEO ones and a title-tweak session skips the
new-build ones — nothing to configure.

### Step 3 — Confirm

Re-read what you just recorded (a `count(*)` / `SELECT` on the rows you wrote, or a second
`closeout-sweep.py` dry-run showing `REMAINING: 0`). If anything did NOT land, report
"couldn't record X" loudly in Step 4. No "gate/convergence/round" language to Pete.

### Step 4 + 5 — Report and Your call

Use the output shape at the bottom. One plain-English headline, a "Recorded for you" line
(what closeout did silently), then a SINGLE numbered menu of judgement calls Pete answers
in one reply. Distinguish **"I'll do X on your OK"** (closeout executes it) from **"you'll
need to…"** (hands-on). Destructive items (delete / send / bin a note or plan) are asked
**separately, one explicit yes each** — never bundled into "all recommended".

> [!important] Never put a future-dated PD on the menu.
> A PD already auto-surfaces when it comes due, so it is NOT a "your call" item. **Exclude any
> PD whose `due_on > today`** from the menu, the "Recorded for you" line, and any "open item"
> mention. Surface a PD ONLY when it is due or overdue (`due_on <= today`). If excluding it
> leaves the menu empty, say "nothing needs a call" — do not pad with a scheduled PD. (Pete,
> 11 Jul 2026: "I don't want to hear about PD tasks again until they are due or overdue.")
> This binds H1/H2/H3 too — a not-yet-due PD is never a surface item.

### Step 6 — Optional light resume note

Offer once: "Save a resume note too? (y)". If yes, write a light `daily_log` resume note
that SKIPS everything closeout already wrote (don't re-run `/brain` Compress — see
Single-writer below).

---

## The check manifest

`class`: **auto** = record it (only if the ownership test proves it's mine) · **verify** =
check a live fact, surface on fail · **surface** = never touch, list with a recommendation.

### Records (the cloud homes)
| id | check | class | how |
|----|-------|-------|-----|
| A1 | this session's OWN commits are in the Work Log | auto | `closeout-sweep.py --apply` |
| A2 | unlogged commits that are NOT mine | surface | `closeout-sweep.py` lists them; never log |
| B1 | knowledge `.md` authored under `/tmp/pbs` is persisted to its CORRECT home, no cross-session collision | auto | for each note: `closeout_ingest_guard.py <file> --json` → safe(NEW/IDENTICAL/UPDATE) then **`cc-save.py <file>`** (always persists, incl. lifecycle notes like session-plans that `cc-knowledge-ingest.py` silently drops — F3); COLLISION → surface, don't overwrite |
| B2 | a durable lesson belongs in `vault_notes` | surface | brutal bar (will it be used + could it mislead) |
| B3 | a behavioural correction/preference belongs in AUTO-MEMORY (MEMORY.md), distinct from a vault lesson | surface | |
| K1 | knowledge produced this session but stranded LOCALLY (scratchpad / local dir) | surface | move under `/tmp/pbs/<home>` + ingest on Pete's OK |
| K2 | every file / data row / doc produced has a cloud home (Drive / right Supabase table / GitHub) | verify+surface | anything only-local is flagged |
| J1 | `daily_log` session entry written by closeout (the ONE diary writer this run) | auto | |
| J2 | closeout record saved: attributed work + "N other sessions live; items X may be theirs" | auto | |

### Live & deploy
| id | check | class | how |
|----|-------|-------|-----|
| A3 | every pushed SHA maps to a Vercel deploy that is READY + live curl 200 | verify | `vercel-api.py deploy-for-sha <sha> --json` per owned pushed SHA (exit 0 READY / 2 not-ready / 3 no-deploy). Scope caveat: it scans the last 100 deploys, so exit 3 = "no deploy in that window" |
| A4 | a deploy EXISTS for the pushed SHA (a missing deploy is OFTEN the non-verified-author BLOCK) | verify | exit 3 has THREE causes — unverified-author BLOCK, build-not-started-yet, or SHA older than the 100-deploy scan window. READ the tool's note before concluding BLOCK. (On a same-session close the SHA is brand-new, so "older than 100" ≈ impossible and "not started" self-resolves on retry — a persistent exit 3 there really is the BLOCK.) |
| A5 | no uncommitted/unpushed changes left in a clone this session owns | surface | `git status` in the owned checkout only |
| A7 | env vars a change depends on are present on the host (Vercel prod) + a runtime smoke | verify | |
| A9 | package.json + lockfile in sync and both committed | verify | |
| A10 | on any deployed change, Sentry still full-coverage (source maps this release, boundaries, prod DSN) | verify | |

### SEO / discoverability (existing sites, not just new builds)
| id | check | class |
|----|-------|-------|
| M1 | analytics intact on changed/new pages: GTM + GA4 AND server-side Measurement-Protocol (dual-path) | verify |
| M2 | changed prod pages indexable as intended — no stray noindex, robots.txt not `Disallow: /` | verify |
| A6 | redirects changed resolve to a live 200, no loop, ≤2 hops (benign Vercel www→apex 2-hop excepted) | verify |
| M6 | multilingual property: changed pages have correct reciprocal hreflang (+x-default, self-ref) | verify |
| M8 | changed/new pages self-reference a canonical at the live preferred URL | verify |
| M3 | images referenced by changed pages resolve 200 (Cloudinary = Sygma-only); orphan uploads surfaced | verify+surface |
| M9 | slug renames left no broken internal links; new pages not orphaned | surface |
| M10 | changed/new pages have valid JSON-LD + present OG/Twitter title+image | verify |
| M4 | content published/changed → sitemap resubmitted in GSC + Request-Indexing queued (closeout does it on your OK) | surface |
| M5 | a newly-optimised page registered in the SEO Page Tracker + Ahrefs Rank Tracker + given a fortnightly-review task | surface (one task) |
| M7 | DNS records changed resolve to the intended target and the endpoint answers | verify |

### Housekeeping & outbound
| id | check | class |
|----|-------|-------|
| A8 | no stale worktrees / orphaned branches left by this session | surface |
| C1 | a session-plan is still open | surface; MY finished plan → strong "stamp done?" default |
| C2a | STAMP a demonstrably-shipped (mine) plan `completed` + **re-save via `cc-save.py`** (re-embeds the banner; `cc-knowledge-ingest.py` would SKIP a session-plan, leaving the plan OPEN forever while closeout reports done — F3) | auto (non-destructive) |
| C2b | BIN a done plan (snapshot+delete) | surface (destructive — separate yes) |
| D1 | deliverable files in scratchpad that belong in Drive | surface |
| D2 | files created in Drive are indexed in `drive_files` | verify |
| E1 | a NEW permanent local file created outside scratchpad | surface |
| E2 | something deleted locally this session | surface (confirm snapshot) |
| E3 | any snapshot this session claims to have taken actually EXISTS before any bin proceeds | verify (block delete if missing) |
| F1 | a new API/secret/cron/integration/project/bucket/Gmail label recorded in its home | surface; auto only if provably mine AND capability write + re-ingest run as one confirmed action → route to `connection-updater` |
| F2 | a background cron deployed without being flagged first | surface |
| F3 | a new SKILL or HELPER/tool built this session is REGISTERED + discoverable: in `public.skills` / `public.helpers` (run `cc-skeleton-registry-sync.py`), listed in `skills/README.md` with a version, and — for a skill — reachable from routing (a `brain` routing row or the skill's own triggers). A built-but-unregistered skill is invisible to the CC. (This check exists because closeout's OWN first build shipped unregistered — 2026-07-04.) | verify + surface |
| G1 | LIVE probe: domain up + pushed SHA READY, reconciled against the stale `property_state` cache | verify | `property-live-state.py` + `deploy-for-sha` |
| G2 | the property's front-door state-of-play / README got its update | surface |
| H1 | a task is now shipped (mine) and should be closed | surface (Pete confirms) |
| H2 | a new must-do discovered | surface (suggest ONE task) |
| H3 | an inferred PD date needs confirming (except bills) | surface |
| I1 | every message/email sent this session had To verified + render tested | verify |
| I2 | a draft meant to go was left unsent | surface |
| I3 | an enquiry (EE) reply sent this session: `te-log --apply` triple-write landed, `draft_text` captured (I3a), AND the EE sign-off is clean — no source-bearing edit left with `source_fixed IS NOT TRUE` (I3b). Only `kind IN ('reply','quote')` carry a draft — handoff/chase/note/correction are exempt. Gate: `VAULT=/tmp/pbs python3 /tmp/pbs/ee-signoff.py --since <session start>` exits 0 | verify → "run te-log --apply / ee-signoff?" |
| I4 | the session touched triage (any decision, override, sync action, or auto path): the Triage Engine sign-off is clean — ledger complete (no stuck applying/sending rows), every override banked with BOTH its `override_reason` AND a `triage-routing-test` regression case, tray reconciled. Gate: `VAULT=/tmp/pbs python3 /tmp/pbs/triage-signoff.py` exits 0 (Pete's gate is the printed PASS/BLOCK lines) | verify → "run triage-signoff?" |
| I5 | the session had a SUBSTANTIVE touch (reply/decision/review/rsvp, or a Reply/Task/Hand-to verb, or an EE reply/quote) with a customer/supplier/project that has a CC knowledge home: that home's knowledge note was updated with the durable new facts (decisions, prices, venues, dates, state changes) this session — NOT just a `vault-enricher` Drive email-extract. Gate: `VAULT=/tmp/pbs python3 /tmp/pbs/entity-enrich-signoff.py --since today` exits 0 (every `✗` = a customer touched but not enriched — update its `vault_notes` home + `cc-embedder.py`, re-run). Standing rule, Pete 17 Jul 2026 | verify → "enrich {entity} knowledge, then re-run entity-enrich-signoff?" |
| I6 (CC Locator parity — advisory, fail-open) | (C2) the session shipped to a website/app repo → assert that repo/domain has a `property_declarations` row (the card mandate exists in property-manager §0d but is prose-only + intake-path-only; a repo shipped without that intake is otherwise never caught). (C3) the session onboarded/first-filed a new customer/supplier → assert a `vault_notes` type=customer|supplier record exists (per `[[vault-routing#onboarding-rituals]]`). These are advisory CHECKS, not gates — surface a missing record for Pete; never block. The daily `cc-locator-audit.py` drift check is the report-only backstop for anything missed | verify → "declare the property card / create the customer record?" |
| I7 (CC Locator drift — advisory) | this session ADDED anything the locator covers — **a skill, helper, project, table, storage bucket, property (website/app), entity or connector** — re-run it so the session's own additions are caught before you close, rather than waiting for tomorrow's 06:30 cron. Gate: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-locator-audit.py --json` → read `gaps` (an INT) and `gap_types`. **`gaps: 0` means nothing unfiled WITHIN ITS SCOPE — it does NOT cover CC pages, Railway crons, or the other databases (hub / CD-Leak / Odoo), and a customer or supplier is covered only insofar as it is an `entities` row. Read the `info[]` scope line before treating 0 as all-clear.** An `aborted: true` result means the check did not run at all — never read that as clean. Any `couldnt-check` finding means the check did NOT run — never read that as clean. Per Pete's standing decision (18 Jul 2026): **file the obvious ones and TELL him; ASK when the home is genuinely his call; ALWAYS ask before deleting anything.** Advisory — surface, never block. Note `--json` deliberately does NOT write a daily_log row, so running it here is free | verify → "file the {N} unfiled thing(s)?" |

### New-build mode (only when a brand-new property is detected — kept OUT of the everyday report)
project row + General bucket · Drive folder + seeded knowledge home · Sentry first-wire ·
sitemap + robots · redirect map · DNS + deploy live · `property_state` seeded · front-door
README · analytics wired (GTM + GA4 + Measurement-Protocol). Walk the
`[[vault-routing#new-project--new-property]]` checklist; surface any gap as a menu item.

## Concurrency safety

- **Reads never interfere** — the full sweep is always safe.
- **The only silent writes are ownership-gated:** A1 (commits proven mine), B1 (my
  `/tmp/pbs` notes, collision-checked), C2a (my finished plan), J1/J2 (my own diary +
  record). Each is filtered by `session_attribution.py`, so none can touch another
  session's work. Everything destructive or not-provably-mine is surface-only.
- **Owned clones only:** A4/A5 inspect only a checkout this session owns; else mark
  "couldn't check". Flag the footgun that `property-manager` clones to a fixed
  `/tmp/<repo>` path shared across sessions.

## Single-writer / SSOT (why closeout does NOT chain `/brain` Compress)

`/brain` Compress Step 7c and closeout are BOTH end-of-session reconcile-writers. Step 7c
used to "log every commit reconcile flags" with no ownership filter — that is the
30-Jun/04-Jul bug where it grabbed other sessions' commits. Both now import the SAME
ownership helper (`session_attribution.owned_commit_shas`), so whichever runs first, in any
order, only ever logs its own commits. closeout is THE end-of-session writer for property
work; its optional Step-6 save is a light resume note only. Do **not** chain `/brain`
Compress from closeout (it would re-run the old path). (`vault-writer` Step 3a is a
recall-scoped, `source_ref`-idempotent per-ship logger — not a reconcile writer — so it
needs no ownership gate.)

## Explicit non-goals (never without Pete)

Delete, send, close a task or plan, set a PD date, publish, register infra it can't prove it
built, or record any work it can't prove is its own. Mechanical record-keeping is silent;
every judgement call is Pete's.

## Output shape (plain, one screen)

```
CLOSEOUT — 4 Jul
Touched: Sygma Solutions (1 repo) + Command Centre.   1 other session running (heads-up).

Everything recorded and live — nothing slipped.        [details ▸]

Recorded for you: logged 1 commit · ingested 1 note · stamped 1 plan done · wrote the diary.

Your call (one reply — "all recommended", or e.g. "1,3 yes, 2 no"):
  1. Task "resubmit sitemap" looks shipped — close it?                    (recommend: close)
  2. Sygma state-of-play missing today's schema work — I'll add it        (recommend: yes)
  3. I'll resubmit the sitemap + request indexing in Search Console       (recommend: do)

Couldn't check: none.
```
Clean exit: **"Locked up — nothing slipped. Save a resume note? (y)"**

## Tools this skill drives

- `session_attribution.py` — the ownership test (owned commit SHAs; top-level-session guard). Shared with `/brain` Step 7c.
- `closeout-sweep.py` — the deterministic record gate (A1/A2): discover checkouts → prove mine → log mine-and-unlogged.
- `worklog_sha.py` — the shared SHA tokeniser (discovery ↔ ownership can't drift). Also used by `worklog.py reconcile`.
- `closeout_ingest_guard.py` — the B1 pre-ingest collision check.
- `vercel-api.py deploy-for-sha <sha>` — map a pushed SHA → its live deploy readyState (A3/A4/G1).
- Reuses: `worklog.py` (discover + append), `cc-knowledge-ingest.py`, `property-live-state.py`, GSC/GA4/Ahrefs helpers, `te-log.py` (EE capture), `connection-updater` (new connections), `mcp__ccd_session_mgmt__list_sessions` (count only).

## Related

- `[[vault-routing]]` — knowledge homing + onboarding checklists · `[[work-log]]` — the cross-property ship index · `[[connections]]` — capability registry · `brain` Compress — the general session save (closeout is the property-work-specific complement).
