---
name: sygma-health-report
description: |
  Generates the Sygma Solutions website health report — one combined report pulling four data sources live (Ahrefs, GSC, GA4, Google Ads) for sygma-solutions.com, with a site-level overview, a per-page scorecard, and a 7-day day-by-day rank trajectory for the four "same-course" cluster pages (EUSR CAT1, Cat & Genny, Cable Avoidance, HSG47).

  Use this skill whenever Pete says: "run the sygma report", "sygma health report", "sygma website report", "how's the sygma website looking", "how's sygma doing", "sygma seo report", "pull all the data for sygma", "is it moving", "sygma health check", or any variation thereof.

  Output: a dated report ingested to the CC `vault_notes` plus an inline narrated summary. Read-only analysis — no code changes, no agents (website carve-out).
version: 1.1
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
updated: 2026-06-08
---

<!-- external-service-routing pre-flight: before any Ahrefs / GSC / GA4 / Google Ads operation in this skill, see [[external-service-routing]]. Helper-first. -->

# Sygma Website Health Report

One command → a combined, multi-source read on **sygma-solutions.com**:

- **Ahrefs** — Domain Rating + Rank Tracker (project 9613452) position buckets + a **7-day day-by-day trajectory** per tracked term
- **GSC** — site top pages/queries + per-page query detail (`sc-domain:sygma-solutions.com`, 28d)
- **GA4** — sessions/users/conversions + traffic-source split + per-page views (property 354127076, 28d)
- **Google Ads** — ad-group + landing-page performance + 7-day spend (advertiser 173-909-0181, 30d)

Default deep-dive pages (the "recently worked on" cluster): **EUSR CAT1, Cat & Genny, Cable Avoidance, HSG47**. To change them, edit the `PAGES` list at the top of `scripts/build_report.py`.

---

## Guardrails (read before running)

- **Website carve-out.** This is **read-only analysis**. Do NOT make code changes, do NOT spawn agents, do NOT touch the repo. If the report surfaces a fix, surface it to Pete and let him decide — sequential main-session only.
- **`/knowledge-hub/hsg47-explained` is a no-work page.** State its data factually if it appears (it often intercepts "hsg47 training"); never propose optimising it. Decision locked: [[2026-05-07-hsg47-explained-no-work]].
- **No backlink suggestions** for Sygma (Appear Online owns off-site). See `feedback_no_backlinks`.
- **GSC position/CTR are 28-day blended averages.** Always read them alongside the live Ahrefs head-term position before concluding a page is "stuck".

---

## Execution

**In Claude Code (default):** run the generator directly. It pulls four sources live and writes the report in ~10–15 seconds.

```bash
# The generator is pulled from GitHub by the boot kernel; run it from /tmp/pbs:
VAULT=/tmp/pbs python3 "/tmp/pbs/skills/sygma-health-report/scripts/build_report.py"
```

A 120000 ms timeout is plenty (it's ~10–15s now). The script:
1. Pulls four sources live (helper-first: GSC/GA4/Ads via the `*-api.py` helpers; Ahrefs via direct API).
2. Writes the full Markdown report to `/tmp/health-report-{today}.md` (then published to the CC — step 5).
3. Prints a HEADLINE block to stdout (DR, top-10 count, and per-page head-term position + 7-day delta + ranking URL).

**In Cowork:** it now runs in ~10–15s — under the workspace bash ~45s cap — so a direct run is usually fine. If a run ever approaches the cap, fall back to Desktop Commander `start_process` with `nohup` + a log file and poll the log. Read the saved `.md` when it completes.

---

## BEFORE narrating — mandatory state-of-play reads

Pete maintains a small set of state-of-play docs that record what's already been investigated, fixed, or locked. **Every finding I'm about to surface must be cross-checked against these. If a finding sits inside any of them, it has already been handled — do NOT flag it as if it were new.** Skipping this step has burned the same trap (HSG47 explainer paid-spend residue, sitelink already killed 19 May) five times. The build_report.py output now includes a `--- MANDATORY READS ---` block in the stdout headline; the saved `.md` has matching "Recent ad-account changes" and "Locked no-work pages" sections at the top. Use them.

0. **The property FRONT DOOR + state of play** — `Properties/Sygma Solutions Website/README.md` (the single home: story, standing decisions, read-order) and **[[sygma-seo-state-of-play]]** (current truth: shipped work, verdicts, baselines, live monitors — updated in place). Read BOTH first; every finding must be consistent with the standing decisions there (e.g. content lever exhausted on head pages, /locations/* is the only geo system, judge on GSC never Ahrefs traffic). If this report constitutes a major event, the close-out adds one story line + a state-of-play update.
1. **`google-ads-account`** (Sygma ads state + `## Recent changes ledger`) — query `vault_notes` (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py "google-ads-account"`) or the Sygma property record in the CC. Anything in the ledger from the last 30 days is fresh; don't propose undoing it. Surfaced as "Recent ad-account changes".
2. **`seo-non-issues`** (six "looks-like-a-bug-but-isn't" traps) — query `vault_notes`. Read before flagging a matching pattern (lovable.app referral, old-WP-URL clicks in GSC, tail-keyword rank, hreflang on a single-language site, the 24 May cluster drop, hsg47-explained CTR).
3. **The hsg47-explained no-work decision** — query `vault_notes` (`"hsg47-explained no work"`): no CTR / Surfer / title work on `/knowledge-hub/hsg47-explained`. Surfaced as "Locked no-work pages".
4. **The last few `daily_log` entries** in the CC — what was actually done recently (the HSG47 ad-group restructure + sitelink kill, the P1/P2/P3 onsite plan).

If a finding I'm about to flag matches any of (1)-(4), I say so explicitly: *"x already actioned on {date}; the y you're seeing is decaying residue / locked / non-issue per ledger entry"*. The aim is the report cannot lead me into restating an already-fixed issue as if it were live.

## After running — narrate, don't dump

Read the generated `.md` and present it to Pete with interpretation, not just the raw tables:

1. **Site overview** — DR, organic vs paid split, conversion volume. One line on overall health.
2. **Per-page scorecard** — the four cluster pages at a glance.
3. **Movement** — for each page, is it moving? Call out the Δ7d direction and any **step-changes** (a date where several terms jump together usually = a Google re-crawl rewarding recent work). Note the **ranking URL** per head term — if a term ranks via a *different* Sygma page than its intended one, that's **cannibalisation** (the four are the same physical course, so this is common and structural).
4. **What's working / what's at risk** — tie movement back to recent commits where known (read the property README's HEAD history). For ad-spend findings, cross-check the Recent ad-account changes section before calling anything wasteful; a row marked "decaying residue" in the Landing pages table is pre-fix history ageing out of the rolling 30-day window, not live waste.
5. **Publish the snapshot to the Command Centre** (since 11 Jun 2026 — the Sygma Reports page's "Health reports" tab):
   ```bash
   python3 - <<'PY'
   import sys, datetime, pathlib, html as H
   sys.path.insert(0, "/tmp/pbs")
   import cc_publish
   md_path = sorted(pathlib.Path("/tmp").glob("*health-report*.md"))[-1]  # the file this run just saved
   body = "<pre style='font:13px/1.55 ui-monospace,Menlo,monospace;white-space:pre-wrap;padding:18px'>" + H.escape(md_path.read_text()) + "</pre>"
   period = datetime.date.today().isoformat()
   ok = cc_publish.publish("sygma-health", period, {"subject": f"Sygma health report — {period}", "html": body})
   print("published" if ok else "PUBLISH FAILED")
   PY
   ```
   (Adjust `md_path` to the exact file this run wrote if the glob picks the wrong one.) Verify at **commandcentre.info/m/sygma-reports** → Health reports tab → new period chip.
6. **Offer next steps** — save is automatic (the dated `.md`). Offer to create CC follow-up tasks in `public.tasks` (ask first — don't auto-create), or to deep-dive a specific page. Don't propose work on no-work pages.

The report is a **snapshot** — each run is a new dated file, so trajectory across runs is preserved in `data/`.

---

## Files this skill owns

- `skills/sygma-health-report/SKILL.md` — this file
- `skills/sygma-health-report/scripts/build_report.py` — the generator (four pulls + markdown build)
- Output: a dated report in `vault_notes` (one per run)

## Related

- Property: the CC **Properties** module — Sygma Solutions Website card (IDs, latest production HEAD)
- Config: [[ahrefs-api-configuration]] · [[google-api-credentials]] · [[google-ads-api-configuration]]
- Sister skills: `ahrefs-audit` (single-page keyword/Surfer optimisation), `audit-review` (fortnightly position check)
