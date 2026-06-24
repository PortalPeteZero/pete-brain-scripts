<!-- DECOMMISSIONED 2026-06-08 (Pete: "delete the fortnight auto thing, dont need it"). Disabled via update_scheduled_task; metadata-row deletion pending in the scheduled-tasks UI (no delete API). Was the main consumer burning the Surfer audit quota; sygma-health-report dropped Surfer the same day. Registry: Library/processes/scheduled-tasks.md -> Sygma SEO Fortnightly Re-check. Do NOT re-enable without re-reading. -->

---
name: sygma-seo-fortnightly-recheck
description: Fortnightly Sygma SEO position re-check (Ahrefs Rank Tracker + GSC + Surfer for Cat & Genny + EUSR cluster + URL transition state) emailed to Pete
---

## Execution -- READ THIS FIRST

Long-running orchestration. Use `mcp__Desktop_Commander__start_process` for any Python script that hits external APIs. Pattern:

```
cd "/Users/peterashcroft/Second Brain" && nohup python3 <script> > /tmp/sygma-seo-fortnightly.log 2>&1 &
```
Poll the log file every 30s until `REPORT_COMPLETE` or `REPORT_FAILED`.

## Goal

Fortnightly SEO position re-check for Sygma Solutions website -- focused on the three rescued pages (Cat & Genny, EUSR CAT1, EUSR Combined) plus URL-transition health (the 20 Apr CG rename + 22 Apr EUSR rename should reach steady state by ~mid-June). Email Pete a delta report.

## Sources to pull

1. **Ahrefs Rank Tracker** (project 9613452, GB desktop) for tags `CAT & Genny Training`, `EUSR CAT1`, `EUSR Combined`. Curl pattern documented at [[ahrefs-api-configuration]]. Endpoint: `https://api.ahrefs.com/v3/rank-tracker/overview?project_id=9613452&country=gb&device=desktop&date={today}&select=keyword,position,volume,url,tags,traffic&limit=500`. Bearer `lGssv7YX4gEWyDhKaBhDLcmLfs14q-yqlZTzsMQa`. Filter rows by tag.

2. **GSC top pages 28d** via `Library/processes/scripts/gsc-api.py top-pages sc-domain:sygma-solutions.com 28 25`. Look at:
   - Whether `/courses/genny-cat-training` (OLD) still beats `/courses/cat-and-genny-training` (NEW) on impressions
   - Whether `/courses/eus-cat1` (OLD) still beats `/courses/eusr-cat1` (NEW)
   - Same for `/courses/eus-cat1-cat2-combined` vs `/courses/eusr-cat1-cat2-combined`

3. **GSC URL Inspection** on the 3 new URLs via `python3 Library/processes/scripts/gsc-api.py inspect sc-domain:sygma-solutions.com https://sygma-solutions.com/courses/<slug>`. Capture `coverageState`, `googleCanonical`, `userCanonical`, `verdict`, `lastCrawlTime` for each.

4. **Surfer audits** (re-trigger via API). For each of the 3 pages:
   ```
   curl -X POST -H "API-KEY: vfv0b3tbStnuc_Utup9AXCsdI32sNT_8" \
     "https://app.surferseo.com/api/v1/audits" \
     -d '{"url":"...","keyword":"...","location":"United Kingdom"}'
   ```
   Wait ~3 min, then GET `/v1/audits/{id}` to fetch `audited_page.content_score` and `competitors_pages` scores. Compare to baseline from [[Projects/SY-Website/2026-05-06-master-recovery-plan]] (CG 82, CAT1 64, Combined 52).

5. **GA4 page traffic 28d** for the 3 pages via `Library/processes/scripts/ga4-api.py page 354127076 <path> 28`. Compare to prev 28d to see if traffic is migrating to new URLs yet.

## Output

Build HTML report (NAVY/TEAL/ORANGE brand colours, mirror cd-team-briefing.py styling). Sections:
- **TL;DR**: are headline keywords climbing? (cat and genny training 800/mo, eusr cat 1 training 60/mo, eusr cat 1 & 2 250/mo)
- **Position movements**: position now vs previous report for each tracked keyword in the 3 tags. Bold any > +5 or < -5 movement.
- **URL transition health**: GSC top-pages comparison old vs new slug for each cluster. Flag if old still > new after 35+ days post-rename.
- **Surfer scores**: current vs baseline. Flag any drop > 5 points.
- **GSC URL Inspection verdict**: PASS/NEUTRAL/FAIL for each, googleCanonical alignment, last crawl recency.
- **GA4 traffic delta**: page-level sessions/users 28d vs prev 28d.
- **Decision prompt**: based on the data, suggest one of: (a) continue waiting, (b) escalate (request indexing / 410 old URLs / deeper rewrite), (c) move to next page rescue.

Email to `pete.ashcroft@sygma-solutions.com` via `Library/processes/scripts/gmail-api.py send`. Subject: `Sygma SEO Fortnightly Re-check -- {date}`.

## Append to history file

`Properties/Sygma Solutions Website/data/sygma-seo-fortnightly-history.md` -- add a dated row with the headline-keyword positions + Surfer scores + URL-transition completion percentage. Builds a trend file across runs.

## Don'ts

- Don't dispatch agents -- single linear orchestration.
- Don't action findings -- analysis + recommend only.
- Don't run on weekends if cron lands one (1,15 will sometimes). Skip silently with SKIPPED_WEEKEND in the log.
- Don't fail loudly to chat -- failures go in the log + a fallback email with subject `Sygma SEO Re-check -- FAILED {date}`.

## Cross-references
- Master plan: [[Projects/SY-Website/2026-05-06-master-recovery-plan]]
- SEO plans: [[cat-and-genny-training-seo-plan]], [[eusr-cat1-seo-plan]], [[eusr-combined-seo-plan]]
- Targeting principles: [[seo-targeting-principles]]
- Ahrefs: [[ahrefs-api-configuration]]
- Surfer: [[surfer-api-configuration]]