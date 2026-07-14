---
name: sygma-health-report
description: |
  Generates the Sygma Solutions website health report — one combined report pulling four data sources live (Ahrefs, GSC, GA4, Google Ads) for sygma-solutions.com, with a site-level overview, a per-page scorecard, a 7-day day-by-day rank trajectory for the four "same-course" cluster pages (EUSR CAT1, Cat & Genny, Cable Avoidance, HSG47), and a GSC daily cross-check table (the judge for any Ahrefs movement).

  Use this skill whenever Pete says: "run the sygma report", "sygma health report", "sygma website report", "how's the sygma website looking", "how's sygma doing", "sygma seo report", "pull all the data for sygma", "is it moving", "sygma health check", or any variation thereof.

  Output: an immutable snapshot row in CC reports.snapshots (report_key sygma-health, auto-published by the generator) rendered at commandcentre.info/m/sygma-reports, plus an inline narrated summary. Read-only analysis — no code changes, no agents (website carve-out).
version: 1.2
trigger_phrases:
  - "run the sygma report"
  - "run sygma report"
  - "sygma health report"
  - "sygma health check"
  - "sygma website report"
  - "sygma seo report"
  - "how's the sygma website"
  - "how is the sygma website"
  - "how's sygma doing"
  - "how's sygma looking"
  - "pull all the data for sygma"
created: 2026-05-20
updated: 2026-07-14
---

<!-- external-service-routing pre-flight: before any Ahrefs / GSC / GA4 / Google Ads operation in this skill, see [[external-service-routing]]. Helper-first. -->

# Sygma Website Health Report

One command → a combined, multi-source read on **sygma-solutions.com**:

- **Ahrefs** — Domain Rating + Rank Tracker (project 9613452) position buckets + a **7-day day-by-day trajectory** per tracked term
- **GSC** — site top pages/queries + per-page query detail + **daily by-query positions for each head term** (`sc-domain:sygma-solutions.com`)
- **GA4** — sessions/users/conversions + traffic-source split + per-page views (property 354127076, 28d)
- **Google Ads** — ad-group + landing-page performance (advertiser 173-909-0181, 30d)

Default deep-dive pages (the "recently worked on" cluster): **EUSR CAT1, Cat & Genny, Cable Avoidance, HSG47**. To change them, edit the `PAGES` list at the top of `scripts/build_report.py`.

---

## Where things live (Business OS homes — verified 14 Jul 2026)

| Thing | Home | How to reach it |
|---|---|---|
| **This skill + the generator** | GitHub `pete-brain-scripts` → `skills/sygma-health-report/` | Pulled to `/tmp/pbs` by the boot kernel each session. Edit in the repo, push, then `package-skill.py` for the Cowork copy. |
| **Previous reports** | CC `reports.snapshots`, `report_key='sygma-health'` (one immutable row per run) | Page: **commandcentre.info/m/sygma-reports** → Health reports tab. SQL: `cc-sql.py "SELECT period_date FROM reports.snapshots WHERE report_key='sygma-health' ORDER BY period_date DESC"` |
| **State-of-play + SOP docs** | CC `vault_notes` (titles below) | `VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py "<title>"` |
| **Recent session history** | CC `daily_log` (`cron_name='session'`) | `cc-sql.py "SELECT date, content FROM daily_log WHERE cron_name='session' ORDER BY date DESC LIMIT 3"` |
| **Ships / commits record** | CC `work_log` | Page `/m/work-log`; written via `worklog.py` |
| **Ahrefs token** | CC `public.secrets` `'ahrefs-token'` | Materialised by the boot kernel to `$VAULT/Library/processes/secrets/ahrefs-token`; the script exits loudly if missing (no hardcoded fallback). |
| **GSC / GA4 / Ads auth** | The `*-api.py` helpers at `/tmp/pbs` | Helper-first per [[external-service-routing]]; they self-auth from materialised secrets. |

The old vault-tree paths (`Properties/…/*.md`, `Library/decisions/…`, `Daily/…`) are RETIRED (24 Jun 2026 cutover) — never read or write them; everything above is the live home.

---

## Guardrails (read before running)

- **Website carve-out.** This is **read-only analysis**. Do NOT make code changes, do NOT spawn agents, do NOT touch the repo. If the report surfaces a fix, surface it to Pete and let him decide — sequential main-session only.
- **`/knowledge-hub/hsg47-explained` — no ranking/CTR pitches** (non-intent page; its traffic doesn't convert). Normal technical hygiene (title length, broken links, audit errors) IS fine — blanket ban relaxed 7 Jul 2026. State its data factually. See vault_notes "No Active Work on /knowledge-hub/hsg47-explained" + `feedback_hsg47_explained_no_ranking_pitches`.
- **No backlink suggestions** for Sygma (Appear Online owns off-site). See `feedback_no_backlinks`.
- **GSC position/CTR are 28-day blended averages.** Always read them alongside the live Ahrefs head-term position before concluding a page is "stuck".
- **Judge movement on GSC, never Ahrefs alone (hard rule, learned 14 Jul 2026).** Ahrefs trajectory rows repeat between its actual crawls — a flat run of identical values is carried-forward samples, so a Δ7d "drop" is two single-location snapshots, not a trend. The report includes a **GSC daily cross-check** table (Google's own daily position per head term) — every Ahrefs step-change MUST be read against it before narrating. GSC lags 2–3 days; if the Ahrefs step post-dates the latest GSC day, the move is **unconfirmed** — say exactly that, never present the Ahrefs number as a fait accompli. (Origin: 12 Jul 2026 Ahrefs "crash" for cat-and-genny that GSC showed as normal page-2 volatility.)

---

## Execution

**In Claude Code (default):** run the generator directly. It pulls four sources live, writes the report, and **auto-publishes the snapshot to the CC** in ~10–20 seconds.

```bash
# The generator is pulled from GitHub by the boot kernel; run it from /tmp/pbs:
VAULT=/tmp/pbs python3 "/tmp/pbs/skills/sygma-health-report/scripts/build_report.py"
```

A 120000 ms timeout is plenty. The script:
1. Pulls four sources live (helper-first: GSC/GA4/Ads via the `*-api.py` helpers; Ahrefs via direct API).
2. Writes the full Markdown report to `/tmp/health-report-{today}.md` (ephemeral working copy for narration).
3. **Auto-publishes** the snapshot to `reports.snapshots` (`report_key='sygma-health'`) and prints the publish result — if it prints FAILED, publish manually via `cc_publish.publish("sygma-health", …)` before closing.
4. Prints a HEADLINE block to stdout: DR, top-10 count, per-page Ahrefs head-term position + Δ7d + ranking URL **with the GSC daily read beside each**, the latest-GSC-day banner, and the mandatory-reads list.

**In Cowork:** it runs well under the workspace bash ~45s cap, so a direct run is usually fine. If a run ever approaches the cap, fall back to Desktop Commander `start_process` with `nohup` + a log file and poll the log. Read the saved `.md` when it completes.

---

## BEFORE narrating — mandatory state-of-play reads

Pete maintains a small set of state docs that record what's already been investigated, fixed, or locked. **Every finding I'm about to surface must be cross-checked against these. If a finding sits inside any of them, it has already been handled — do NOT flag it as if it were new.** Skipping this step has burned the same trap five times. The stdout headline prints this list; the saved `.md` has matching "Recent ad-account changes" and "Locked no-work pages" sections. All live in CC `vault_notes` — fetch with `VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py "<title>"`.

0. **The property front door + state of play** — vault_notes **"Sygma Solutions Website — THE front door"** (story, standing decisions, read-order) and **"Sygma SEO -- State of Play (single source of truth; update IN PLACE)"** (current truth: shipped work, standing verdicts, baselines, live monitors/watch items). Read BOTH first; every finding must be consistent with the standing verdicts there (content lever exhausted on head pages, /locations/* is the only geo system, judge on GSC never Ahrefs, DR-targeting verdict, trial-then-fall signature). The watch items often *predict* what the report shows — check them before calling anything new.
1. **Ads state + ledger** — vault_notes **"Sygma Google Ads -- Account State"** (`## Recent changes ledger`). Anything in the ledger from the last 30 days is fresh; don't propose undoing it. Auto-surfaced in the report as "Recent ad-account changes" when entries are <30d old (an absent section = no fresh entries, verified working 14 Jul 2026).
2. **Non-issues** — vault_notes **"Sygma Solutions -- SEO non-issues and pre-work checks"** (9 "looks-like-a-bug-but-isn't" traps). Read before flagging a matching pattern (lovable.app referral, old-WP-URL clicks in GSC, tail-keyword rank, hreflang, the 24 May cluster drop, hsg47-explained CTR, …).
3. **The hsg47-explained decision** — vault_notes **"No Active Work on /knowledge-hub/hsg47-explained"** (relaxed 7 Jul 2026: technical hygiene fine, ranking/CTR pitches banned). Surfaced in the report as "Locked no-work pages".
4. **Recent sessions** — the last 3 `daily_log` session rows (what was actually done recently; ranking moves often trace to shipped work).

If a finding I'm about to flag matches any of (1)–(4), I say so explicitly: *"x already actioned on {date}; the y you're seeing is decaying residue / locked / non-issue per ledger entry"*. The aim is the report cannot lead me into restating an already-fixed issue as if it were live.

## After running — narrate, don't dump

Read the generated `.md` and present it to Pete with interpretation, not just the raw tables:

1. **Site overview** — DR, organic vs paid split, conversion volume. One line on overall health.
2. **Per-page scorecard** — the four cluster pages at a glance.
3. **Movement** — for each page, is it moving? Call out the Δ7d direction and any **step-changes** (a date where several terms jump together usually = a Google re-crawl digesting recent work). Note the **ranking URL** per head term — if a term ranks via a *different* Sygma page than its intended one, that's **cannibalisation** (the four are the same physical course, so this is common and structural). **Before narrating ANY Ahrefs movement, verdict it against the "GSC daily cross-check" table in the report**: (a) GSC confirms → narrate as real; (b) GSC shows the term was already bouncing in that band → normal volatility, not a step; (c) the Ahrefs step post-dates the latest GSC day → unconfirmed, say so and offer a re-check in 2–3 days.
4. **What's working / what's at risk** — tie movement back to recent ships (work_log + the last daily_log rows). For ad-spend findings, cross-check the Recent ad-account changes section before calling anything wasteful; a row marked "decaying residue" in the Landing pages table is pre-fix history ageing out of the rolling 30-day window, not live waste.

## What to update when it runs (the close-out checklist)

- **CC snapshot** — auto-published by the generator. Verify the stdout line said OK (chip at **commandcentre.info/m/sygma-reports** → Health reports tab). If FAILED, publish manually before closing.
- **State of play** — only if the run surfaced a **major event** (a confirmed step-change, a watch-item firing or resolving, a new standing verdict): update **"Sygma SEO -- State of Play"** IN PLACE (volatile-fact rule: replace the stale value, never append a duplicate line) and add one story line to the front-door note. A routine "nothing moved" run updates nothing.
- **Watch items** — if the report confirms or refutes a state-of-play watch item, record that verdict in the note (that's what the watch list is for).
- **Follow-up tasks** — PROPOSE in the narration, create in `public.tasks` only if Pete says so (never auto-create). Don't propose work on restricted pages.
- **Session log** — the session's Compress/closeout writes the `daily_log` row as usual; nothing report-specific to do here.
- **No vault_notes ingest of the report itself** — history lives in `reports.snapshots` (one row per run; the page renders the newest row per period). The `/tmp` md is ephemeral by design.

---

## Files this skill owns

- `skills/sygma-health-report/SKILL.md` — this file
- `skills/sygma-health-report/scripts/build_report.py` — the generator (four pulls + markdown build + CC auto-publish)
- Output: one `reports.snapshots` row per run (`report_key='sygma-health'`) → /m/sygma-reports Health reports tab

## Related

- Property: the CC **Properties** module (`/m/properties`) — Sygma Solutions Website card (IDs, latest production HEAD)
- Config: [[ahrefs-api-configuration]] · [[google-api-credentials]] · [[google-ads-api-configuration]]
- Sister skills: `ahrefs-audit` (single-page keyword/Surfer optimisation), `audit-review` (fortnightly position check)
