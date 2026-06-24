---
name: sygma-ads-fortnightly-report
description: Fortnightly Sygma Google Ads performance review (direct Ads API via ads-api.py + delta vs prev period + new-negative candidates) emailed to Pete
---

## Execution -- READ THIS FIRST

Long-running script orchestration. The 45-second workspace bash cap means Python scripts that hit external APIs (Google Ads, Gmail) MUST run via Desktop Commander, not via the Bash tool. Pattern:

```
mcp__Desktop_Commander__start_process with command:
  cd "/Users/peterashcroft/Second Brain" && nohup python3 -c "..." > /tmp/sygma-ads-fortnightly.log 2>&1 &

then poll the log file via mcp__Desktop_Commander__read_file every 30s until you see "REPORT_COMPLETE" or "REPORT_FAILED".
```

## Goal

Generate Sygma Google Ads fortnightly performance report (last 14d vs prev 14d), surface waste candidates and Quality Score regressions, email Pete a styled HTML digest. Single advertiser account 173-909-0181 ("Sygma Training -- All Courses") under MCC 220-653-9186.

## Steps

1. **Pull last 14d + prev 14d data via direct Ads API** -- `Library/processes/scripts/ads-api.py` (Basic Access dev token approved 2026-05-18; Windsor superseded -- see [[google-ads-api-configuration]]). Import the helper via importlib (filename has a hyphen):

   ```python
   import importlib.util
   spec = importlib.util.spec_from_file_location("ads_api",
       "/Users/peterashcroft/Second Brain/Library/processes/scripts/ads-api.py")
   mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
   ads = mod.GoogleAdsAPI()  # defaults to advertiser 173-909-0181, login_customer_id MCC 220-653-9186
   ```

   Run TWO GAQL queries per period (last 14d, prev 14d) -- four queries total. Use `segments.date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'` for explicit ranges. Spend is in `cost_micros` (divide by 1e6 for £). Conversions are floats.

   **Query A -- top-line + per-keyword + QS** (`FROM keyword_view`):
   ```sql
   SELECT campaign.id, campaign.name, ad_group.id, ad_group.name,
          ad_group_criterion.criterion_id, ad_group_criterion.keyword.text,
          ad_group_criterion.keyword.match_type,
          ad_group_criterion.quality_info.quality_score,
          ad_group_criterion.quality_info.creative_quality_score,
          metrics.cost_micros, metrics.clicks, metrics.impressions,
          metrics.conversions, metrics.ctr, metrics.average_cpc
   FROM keyword_view
   WHERE segments.date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'
     AND ad_group_criterion.status != 'REMOVED'
   ```

   **Query B -- search terms (waste-candidate surface)** (`FROM search_term_view`):
   ```sql
   SELECT search_term_view.search_term, ad_group.id, ad_group.name,
          metrics.cost_micros, metrics.clicks, metrics.conversions
   FROM search_term_view
   WHERE segments.date BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'
     AND metrics.clicks > 0
   ORDER BY metrics.cost_micros DESC
   ```

2. **Compute deltas** from the two periods' top-line aggregates. cost_micros sums divided by 1e6 give £.

3. **Compute and flag**:
   - Top-line spend / clicks / conversions / CPA delta vs prev 14d
   - **New waste candidates**: search terms with clicks > 0 AND no commercial-intent modifier (training/course/near me/cost/book/certificate/accreditation) AND not already in `Sygma Master Negatives` (read snapshot from `Properties/Sygma Solutions Website/data/2026-05-06-sygma-master-negatives-snapshot.csv` -- treat as authoritative until further consolidation runs change it). Cap at top 15 by spend.
   - **Quality Score regressions**: any keyword whose QS dropped by 2+ since prev period, or sits at QS 1-3 with > £10 spend. Cap at top 10 by spend.
   - **Conversion attribution health**: count form_submit conversions where source/medium = google/cpc vs everything else. Goal post-2026-05-07 fix: > 60% paid attribution. Lower than that = paid-attribution still leaking.

     Pull from GA4 via `Library/processes/scripts/ga4-api.py` (import via importlib, same hyphen pattern as ads-api.py). Sygma Solutions GA4 property = `354127076`. Use:
     ```python
     g = ga4_mod.GA4API()
     rows = g.run_report("354127076", ["sessionSourceMedium"], ["eventCount"],
         date_ranges=[{"startDate": last_start, "endDate": last_end}],
         dimension_filter={"filter": {"fieldName": "eventName",
             "stringFilter": {"value": "form_submit"}}}, limit=50)
     ```
     **`run_report` returns an ALREADY-FLATTENED list of dicts keyed by dimension/metric NAME** -- e.g. `{"sessionSourceMedium": "google / cpc", "eventCount": "12"}`. Do NOT treat the return as a raw RunReportResponse (`.get("rows")` / `row["dimensionValues"]` will KeyError -- this exact bug degraded the 2026-06-01 run). Read `row.get("sessionSourceMedium")` and `float(row.get("eventCount", 0))` directly. Paid = source/medium lower-cases to `google / cpc`; everything else = other. Wrap the GA4 pull in try/except so a GA4 failure degrades this one section gracefully rather than failing the whole report.

4. **Build HTML report** (mirror `cd-team-briefing.py` styling -- ORANGE #F5A623, TEAL #2BBFBF, NAVY #1B2340 brand colours). Sections:
   - Top-line summary (spend, clicks, conv, CPA, deltas)
   - New waste candidates (negatives to add)
   - Quality Score regressions
   - Attribution health
   - Source-link footer to [[Projects/SY-Website/2026-05-06-master-recovery-plan]]

5. **Send via** `Library/processes/scripts/gmail-api.py send` to `pete.ashcroft@sygma-solutions.com` with subject `Sygma Ads Fortnightly Report -- {date_range}`. Body = HTML.

6. **Append a row to** `Properties/Sygma Solutions Website/data/sygma-ads-fortnightly-history.md` (create if absent) with date, spend, clicks, conv, CPA. This builds a trend file Pete can scan in seconds.

7. **Log REPORT_COMPLETE** at the end so the polling loop exits cleanly.

## Don'ts

- Don't dispatch agents -- single linear orchestration in one Desktop Commander process.
- Don't act on findings (don't add negatives, don't change bid strategy). Report only. Pete decides + applies.
- Don't fail loudly to chat -- failures go in the log file plus a fallback email to Pete with subject `Sygma Ads Report -- FAILED {date}` and the error tail.
- Don't run on weekends if it falls on one (cron's 1,15 will sometimes land Saturday/Sunday). Skip silently and log SKIPPED_WEEKEND.

## Cross-references
- Master plan: [[Projects/SY-Website/2026-05-06-master-recovery-plan]]
- Ads config: [[google-ads-api-configuration]]
- Negatives snapshot: [[Properties/Sygma Solutions Website/data/2026-05-06-sygma-master-negatives-snapshot]]
- Source: `Library/processes/scripts/ads-api.py` (direct Ads API, since 2026-05-18). Windsor.ai was the pre-approval read path; superseded now -- see [[google-ads-api-configuration#supersedes--co-existence]].