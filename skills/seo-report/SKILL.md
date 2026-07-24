---
name: seo-report
version: 1.0
description: |
  The one property-agnostic SEO skill. Answers "how is this site doing?", runs the six-step work loop
  (find, diagnose, choose lever, do, verify, measure) and reports from the CC store, never a live paid
  API. The commercial-intent filter is enforced in CODE, so a vanity term (bare "cat and genny") can
  never reach a report. GSC is the scoreboard for rank and traffic; Ahrefs is the map (competitors,
  backlinks, SERP, volumes) on demand only; Surfer is on-page content scoring while editing.

  Use whenever Pete says: "how is sygma doing", "seo report", "how is the CD site", "check the
  positions", "has it moved", "run the seo report", "what should I work on", "why is this page not
  ranking", "site health", or any per-site SEO health or opportunity question.

  Supersedes ahrefs-audit, audit-review and sygma-health-report. Read-only reporting plus on-demand
  diagnosis; on-site fixes go through property-manager, never auto-deploy.
trigger_phrases:
  - "how is sygma doing"
  - "seo report"
  - "run the seo report"
  - "check the positions"
  - "has it moved"
  - "what should I work on"
  - "why is this page not ranking"
  - "site health"
---

# seo-report -- the one property-agnostic SEO skill

> Full design, decisions and provenance: `vault_notes` **plan-seo-measurement-platform-2026-07-23**.
> Per-tool manuals: `[[gsc-how-to-use]]`, `[[ga4-how-to-use]]`, `[[ahrefs-api-configuration]]`,
> `[[surfer-api-configuration]]`. Per-property rules: `seo_property_config` + the property's
> `seo-targeting-principles`-style note.

## RULE ZERO — fix it NOW, in the tooling, before you carry on

**This skill must be better every time it runs.** The moment a run surfaces a defect or an obvious
improvement, you FIX IT AT THAT MOMENT — before you continue the analysis and before you report to Pete.
A retraction in chat is not a fix: the next session repeats it. Pete's standing instruction (23 Jul 2026):
*"whenever we come across an error or something that can improve, it's fixed at the time it happens."*

**What counts as a trigger** (any one of these, no judgement call needed):
- a tool returned something you MISREAD, or that is easy to misread (wrong field, missing field, a shape
  that invites a wrong assumption)
- you reported something to Pete that turned out to be wrong, or you had to retract or caveat it
- a call failed, refused, or returned an empty/ambiguous result whose CAUSE was not obvious from the output
- you needed a fact that was not in the manual, or the manual was stale/wrong
- you hand-rolled logic that the next session will also have to hand-roll
- Pete corrects you on anything

**What "fixed" means — all four, same session:**
1. **Structural, not documentary.** Put it in the CODE where it can't be skipped (`ahrefs-api.py`,
   `surfer-api.py`, `seo-report.py`) — a helper method, a guard, a loud error. Only if code truly cannot
   carry it does it go in this SKILL.md or the tool's manual note.
2. **Name the failure in the fix.** Docstring/comment states what was misread and what the wrong output
   was, so the trap is visibly closed and cannot be re-entered by someone reasoning the same way.
3. **Prove it.** Re-run the corrected path and show the real answer before reporting.
4. **Commit + push it**, and repackage this skill if you edited it. `/tmp/pbs` is re-cloned every session:
   an uncommitted fix is a fix that never happened.

**Then tell Pete what you fixed**, in one line, alongside the finding. Never silently absorb it.

> Worked example (the fault that created this rule): the Surfer terms endpoint carries TARGET ranges and
> no usage field. A `t.get("count",0)==0` parse read every term as "missing" and reported "185 terms
> missing" on a page scoring 72 — impossible, and fabricated. The fix was NOT "remember to check": it was
> `terms_vs_content()` in `surfer-api.py` (counts real occurrences vs target), a docstring naming the bad
> parse, the same warning in the Surfer manual, and this step naming the sanctioned call.

## RULE ONE -- every number names what it is FOR

**A position on its own is meaningless and Pete will not accept one.** (23 Jul 2026: *"Every time you give
me stats and a position, I need to know for what."*) Every ranking figure carries four things:

1. **The exact search term in quotes** -- `"cat and genny training"`. Not "cat and genny", not "the head
   term", not "the page". Page-level figure? Say so and name the URL.
2. **The measure** -- impression-WEIGHTED position, Google UK, GSC.
3. **The window** -- real dates/months, equal lengths when comparing.
4. **The source** -- GSC or Ahrefs, never blended.

**`avg(position)` on `seo_gsc_daily` is BANNED.** Rows are (date, query, PAGE), so a stray URL with 1
impression at position 88 outweighs nothing and distorts everything -- it read July as 22.0 when the truth
was 16.1 and invented two collapse weeks. Never hand-roll it in SQL:

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/seo-report.py <property> --term 'cat and genny training' [--weekly]
VAULT=/tmp/pbs python3 /tmp/pbs/seo-report.py <property> --page /courses/cat-and-genny-training
```

Both weight correctly and print what they measured. **Never change what you are measuring mid-conversation
without saying so** -- term, then page, then blend reads as flip-flopping even when each number is right.


## RULE TWO — report what is RULED OUT, never what is suspected

Pete, 24 Jul 2026, after seven explanations for one page in a day: *"you really are fucking useless."*
Every check was sound. Narrating each one as a finding is what made the day worthless.

- **One answer, or none.** Better "I don't know, here is what it isn't" than a fifth theory. Pete
  cannot act on a theory and does not want to referee them.
- **Test the obvious alternative FIRST.** A rival outranks us? READ THEIR PAGE before theorising about
  Google: `firecrawl-api.py compare <ours> <theirs>` (works on sites that block curl). That one check
  killed two of the seven.
- **Hunt the artefact BEFORE reporting, not after.** Inflated denominators, capped samples, unweighted
  averages, stray URLs. Sitelinks alone made site-wide CTR look catastrophic when the homepage
  converts brand at 32.5%. Run `seo-report.py <prop> --ctr`.
- **Does it explain the SPECIFIC anomaly?** A cause that would apply to any page is not a diagnosis.
- **Name what would disprove it.** No falsifier means it is a story.
- **Check the pipe before trusting the data.** `seo-pull-gsc`/`seo-pull-ga4` sat CRASHED for a day
  while reports answered from stale rows. `SELECT key,last_status FROM public.crons WHERE key LIKE 'seo-pull%'`.

**Never promote a measurement to a diagnosis.** "A difference I measured" and "the reason we rank
here" are different claims. Sliding between them is what read as flip-flopping all day.

## The five principles (never break these)

1. **GSC is the scoreboard, Ahrefs is the map.** Judge our own rank/traffic on GSC (Google's own data,
   free, in the store). Ahrefs is for what GSC cannot know: competitors, backlinks, SERP, volumes. A blank
   Ahrefs figure is a PULL FAILURE to report loudly, never a ranking loss.
2. **Read the store, not a paid API.** Reports run off `seo_gsc_daily` / `seo_ga4_daily` / `seo_backlinks`.
   Only `ahrefs-api.py` / `surfer-api.py` may spend, on demand, and they log every call to `seo_api_usage`
   and refuse at quota (management/* is free and always passes).
3. **Commercial intent only, enforced in code.** `seo-report.py` filters every query set through the
   property's `seo_property_config` (commercial patterns + explicit vanity list) BEFORE analysing. Clicks
   are the measure, not impressions. Never open with movement on a vanity term.
4. **Split organic from paid; never a blended total.** GA4 conversions are reported by channel.
5. **A standing verdict carries provenance or it is RE-TEST.** Verdicts live in `seo_verdicts` with
   claim/evidence/dates/tooling-state/status. A verdict whose tooling was failing (see Sygma's content-lever
   verdict) is `retest`, not settled -- never inherit it as fact.

## Quick "how is <site> doing?"

```bash
VAULT=/tmp/pbs python3 /tmp/pbs/seo-report.py <property_key> [--days 13]   # e.g. sygma-solutions-website
VAULT=/tmp/pbs python3 /tmp/pbs/seo-report.py --list                       # in-scope properties with config
```
The engine prints commercial-only clicks/impressions/terms/avg-position before-vs-after, the biggest
money-term moves (incl. decliners), and the GA4 organic-vs-paid conversion split. Narrate that; do not dump.
If `prev` is empty, the store lacks history for the older window -- backfill it (`seo-pull-gsc.py --property
<key> --days 30`, free) then re-run.

## The six-step work loop (for "what should I work on / why isn't it ranking")

1. **FIND** the opportunity -- commercial pages ranking 4-20 with real impressions and poor CTR (from the
   store / GSC). The config's intent rules filter the list; locked pages (`no_ranking_work`) are excluded.
2. **DIAGNOSE** why -- `ahrefs-api.py` for `serp_overview` (who is above us + their DR/backlinks: is it
   winnable?) and competitors; `surfer-api.py audit_page(url, keyword)` then **`terms_vs_content(editor_id)`** for content score +
   which terms are genuinely SHORT (the terms endpoint returns TARGET ranges only, with no usage field --
   never infer 'missing' from it; that misread invented a finding on 23 Jul)
   (import_content_from_url IS the content audit; always set location + device). Both on demand, both gated.
2b. **READ THE PAGE THAT IS BEATING US.** Not optional, and BEFORE theorising:
   ```bash
   VAULT=/tmp/pbs python3 /tmp/pbs/firecrawl-api.py compare <our-url> <their-url>
   ```
   Prints page SHAPE side by side (headings, question-headings, prices, book/date/venue counts) and
   works on sites that block curl and WebFetch behind Cloudflare. Word count hides what matters: the
   two cat-and-genny pages were both ~3,000 words; the difference was 15 headings vs 45 and 85
   mentions of "book" vs 6. Skipping this check cost a full day on 24 Jul 2026.
3. **CHOOSE THE LEVER** -- content / technical / internal links / **off-site**. If the diagnosis is "money
   pages have no links and competitors do", on-site work will not fix it -- say so. For Sygma's head terms
   that is the evidenced finding (88% of links hit the homepage), and off-site is Appear Online's remit.
4. **DO** -- via the `property-manager` skill (repo, build, deploy, verify). Never from this skill.
5. **VERIFY it shipped** -- live URL check, not a green build.
6. **MEASURE with provenance** -- wait 4-8 weeks (redirect/consolidation effects lag), equal 13+ day
   windows, commercial-only, GSC as judge. Record the verdict in `seo_verdicts` WITH its evidence, dates and
   whether the tooling was verified working.

## Cost discipline (paid tools)

- **Never scheduled.** A cron may never spend money. Ahrefs/Surfer run only when a human asks.
- **Ahrefs:** `ahrefs-api.py` logs each call's real unit cost and refuses metered calls at the floor;
  `management/*` + `subscription-info/*` are free and always callable (re-read the live project list rather
  than hand-maintaining it). Check remaining: `ahrefs-api.py units`.
- **Surfer:** `surfer-api.py` sends the mandatory `User-Agent` (no more Cloudflare-1010 misdiagnosis),
  counts Content Editor creates per calendar month, refuses past 20/month. Content Audit (`/audits`) is
  plan-gated and unconfirmed -- prefer `audit_page()` (a Content Editor create).

## Honest framing (state it, do not oversell)

Better measurement improves the SPEED and ACCURACY of knowing -- it does not by itself move rankings. Judge
this platform on "we stop being told wrong things and stop losing sessions to false alarms", never on
"rankings went up". For rankings, the lever is usually off-site.

## Onboarding a new site

Add its `property_declarations` row (gsc/ga4/ahrefs ids or an explicit "no Ahrefs"), verify GSC+GA4 access,
**run the GA4 property-config audit** (time zone + currency must match the entity -- the check that would
have caught Sygma's Etc/GMT + USD), seed its `seo_property_config` (intent rules REQUIRED -- no default),
mark it in-scope. Free pulls then cover it automatically. No new script, no new page template.
