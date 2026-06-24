---
name: audit-review
description: >-
  Fortnightly SEO review for any page on any property. Pulls live Ahrefs Rank Tracker
  positions via API, runs Surfer re-audits automatically via API (audit endpoint for
  competitor benchmark, PATCH + score for content score), compares to baseline, builds
  a before/after comparison, updates the vault SEO Page Tracker, and decides next steps
  (close out, content top-up, shift to off-site). Can review a single page or scan all
  tracked pages on a property to find what's due. Use this skill whenever Pete says
  "fortnightly review", "check the positions", "how's the page doing", "run the review",
  "what's due for review", "check the SEO", "has it moved", "position check", "rescan",
  or any request to check progress on a page that's already been optimised. Also triggers
  from Asana "Fortnightly review" tasks. One property at a time, but can cover multiple
  pages in a single session.
---

<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Asana / Sheets / Docs / Xero / Odoo / GSC / GA4 / Ads / Vision / Geocoding / Sentry / Cloudflare / Vercel operation in this skill, see [[external-service-routing]]. Helper-first. -->

# AuditReview

Fortnightly check that closes the loop on page SEO work. Compares where we are now against where we started, decides what to do next.

This skill covers the **Fortnightly Review** section and **Phase 9** (Surfer Re-Audit) of the [[page-seo-workflow]]. Applies primarily to `property_type: marketing-site` properties (vocabulary at [[vault-routing#property-type-vocabulary]]). Read the property README before the run to confirm type + pull any rank-tracker keyword tags / Ahrefs project ID.

Version history: [[CHANGELOG]].

## Required Connectors

| Connector | How | Used for |
|-----------|-----|----------|
| Ahrefs API v3 | Direct API via bash curl. Config: [[ahrefs-api-configuration]] | Rank Tracker positions, keyword data, traffic changes |
| Surfer SEO API | Direct API via bash curl. Config: [[surfer-api-configuration]] | Re-audit scores, NLP term comparison, content scoring |
| GSC API | Direct via service account JWT. Config: [[google-api-credentials]]. Key file: `Library/processes/secrets/google-seo-service-account.json` | Primary source for impression/click/CTR movement and true average position per query |
| Asana MCP | `mcp__asana__asana_*` (load via ToolSearch) | Complete review tasks, create follow-up tasks |
| Vault (file tools) | Read/Write/Edit | SEO Page Tracker, plan files, daily log |

**Auth quick reference** (full details in config files):

```bash
# Tokens live in the CC secrets table — NEVER inline them. Fetch at runtime:
AHREFS=$(VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT value FROM secrets WHERE name='ahrefs-token'" | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['value'])")
curl -s -H "Authorization: Bearer $AHREFS" "https://api.ahrefs.com/v3/[endpoint]"

SURFER=$(VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT value FROM secrets WHERE name='surfer-token'" | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['value'])")
curl -s -H "API-KEY: $SURFER" "https://app.surferseo.com/api/v1/[endpoint]"
```

**GSC API quick reference** (service account JWT -- see [[google-api-credentials]] for full setup):

```python
# python3 with google-auth + requests
from google.oauth2 import service_account
import google.auth.transport.requests, requests, json

creds = service_account.Credentials.from_service_account_file(
    "Library/processes/secrets/google-seo-service-account.json",
    scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
creds.refresh(google.auth.transport.requests.Request())

# Pull last 28 days, filtered to a specific page, grouped by query
body = {
  "startDate": "2026-03-22", "endDate": "2026-04-22",
  "dimensions": ["query"],
  "dimensionFilterGroups": [{"filters": [{"dimension": "page",
    "operator": "equals", "expression": "<PAGE URL>"}]}],
  "rowLimit": 100
}
r = requests.post(
  "https://searchconsole.googleapis.com/webmasters/v3/sites/sc-domain%3A<DOMAIN>/searchAnalytics/query",
  headers={"Authorization": f"Bearer {creds.token}"}, json=body)
print(json.dumps(r.json(), indent=2))
```

Compare current 28-day window against the previous 28-day window on the same query set to get a true movement reading. Ahrefs GSC endpoints remain as a fallback only.

---

## Phase 0 -- Scope the Review

### 0a. Identify the Property

Ask Pete which property, or infer from context. Read the property README from `Properties/{Name}/README.md` and the parent project's `seo/` sub-project README from `Projects/{Parent}/seo/README.md` (post 2026-05-06 restructure).

**Property-to-project mapping (post 2026-05-06 restructure):**

| Property | Parent Project | SEO sub-project path | Ahrefs Project | Surfer Workspace |
|----------|------------|----------|----------------|------------------|
| Sygma Solutions Website | SY-Website | `Projects/SY-Website/seo/` | 9613452 | 1312139 |
| Canary Detect Main Website | CD-Website | `Projects/CD-Website/seo/` | 9613451 | 1312141 |
| The Leaky Finders Website | CD-Other-Sites | `Projects/CD-Other-Sites/the-leaky-finders/` | 9613450 | Yes |
| LeakGuard Lanzarote | CD-Other-Sites | `Projects/CD-Other-Sites/leakguard-lanzarote/` | 9613445 | 1330651 |
| Pipebusters Lanzarote | CD-Other-Sites | `Projects/CD-Other-Sites/pipebusters-lanzarote/` | 9613446 | Yes |
| Leakbusters Lanzarote Website | CD-Other-Sites | `Projects/CD-Other-Sites/leakbusters/` | 9613448 | 1312143 |

In Asana: SEO work for main sites lives in the parent project's `seo` section. For CD-Other-Sites, each secondary site has its own section. Use `asana_get_sections` on the parent project, then `asana_add_task_to_section` to file the review tasks in the right place.

### 0b. Find What's Due for Review

Read the **SEO Page Tracker** table in the property README. For each row:

1. Check the **Next Review** date -- is it today or past due?
2. Check the **Status** -- is it "Plan written, awaiting implementation", "Awaiting review", or something else?
3. Check the **Surfer Score** column -- does it have baseline scores recorded?

Build a list of pages that are due or overdue for review. Present to Pete:

> "I can see X pages tracked on [property]:
>
> 1. **[page]** -- targeting [keyword]. Status: [status]. Next review: [date]. [Due / Overdue by X days / Not yet due]
> 2. ...
>
> Which page(s) do you want to review today?"

If Pete came in asking about a specific page, skip the scan and go straight to that page.

### 0c. Check Asana for Review Tasks

Use `asana_get_sections` on the SEO project to find the page's section. Then `asana_get_tasks` to find:

- The "Fortnightly review" task -- is it due? Overdue?
- Any other outstanding tasks in the section that might affect the review

Report any outstanding tasks to Pete.

### 0d. Load the Existing Plan

Read the page's plan file from `Projects/{Parent}/seo/files/{page-slug}-seo-plan.md` (e.g. `Projects/SY-Website/seo/files/...`), or for CD-Other-Sites use `Projects/CD-Other-Sites/{site-slug}/files/{page-slug}-seo-plan.md`. The plan has:

- Baseline positions from Ahrefs research
- Target keywords and their volumes
- Surfer baseline scores (if audits were completed)
- NLP terms that were missing
- What implementation work was done (or is still pending)
- Success criteria

If the plan shows implementation hasn't happened yet, flag this: "The plan shows implementation hasn't been done yet. A position check is still useful to track organic movement, but the Surfer re-audit won't show content improvements until after implementation. Want to proceed with just the Ahrefs check?"

---

## Phase 1 -- Ahrefs Position Check

Pull live data from the Ahrefs API for every keyword tracked for this page.

### 1a. Rank Tracker Overview

- `rank-tracker-overview` with the property's Ahrefs project ID

Look for the page's tag (e.g. "CAT & Genny Training Page"). Note current positions for all tagged keywords.

### 1b. Page Keyword Profile

- `site-explorer-organic-keywords` with `target: [page URL]`, `mode: exact`, `country: [country code]`, `columns: keyword,volume,best_position,traffic`, `limit: 50`

This shows all keywords the page currently ranks for, not just the ones we're tracking. New keyword appearances are a good sign.

### 1c. GSC Data (primary source)

**Primary:** direct GSC API via service account JWT (see Auth quick reference above). Pull last 28 days filtered to the target page URL, grouped by query. Then pull the previous 28 days with the same filters for side-by-side comparison.

- Endpoint: `POST https://searchconsole.googleapis.com/webmasters/v3/sites/sc-domain%3A{DOMAIN}/searchAnalytics/query`
- Dimensions: `["query"]` (primary); also `["query", "page"]` for cannibalisation checks
- Filter: `page equals <PAGE URL>`

GSC gives the ground truth on clicks, impressions, CTR, and average position -- more accurate than third-party rank trackers and more granular than Ahrefs GSC endpoints. Capture: top 10 queries by impressions this window, queries that moved (abs change >= 3 positions), queries with impressions but zero clicks (CTR rescue candidates).

**Fallback:** Ahrefs `gsc-keywords` and `gsc-pages` remain as a secondary check only if the direct API is unavailable.

### 1d. Build the Position Comparison

For each tracked keyword, compare:

| Keyword | Volume | Baseline Position | Previous Review | Current Position | Change | Trend |
|---------|--------|-------------------|-----------------|------------------|--------|-------|

**Flags:**

- **Drop > 5 positions**: flag immediately, investigate cause
- **Entered top 10**: opportunity to push harder (content refresh or backlink push)
- **Positions 8-15** (striking distance): strong candidates for the next content push
- **New keywords appearing**: the page is gaining topical authority
- **Keywords lost**: were they cannibalised by another page?

If a drop > 5 positions is detected, run a quick investigation:

1. Check `site-explorer-organic-keywords` at domain level with `search: [keyword]` to see if another page is cannibalising
2. Check `site-explorer-backlinks-stats` to see if backlinks were lost
3. Check the SERP overview to see if a new competitor entered

---

## Phase 2 -- Surfer Re-Audit (if due)

All Surfer checks run via the API automatically. No manual handoff needed.

### 2a. Check if Surfer Re-Audit is Due

A Surfer re-audit is due when:

- Implementation was completed 14+ days ago
- The plan file has baseline Surfer scores recorded
- No re-audit has been done since implementation

If implementation hasn't happened yet, skip this phase entirely.

### 2b. Run Surfer Audit via API

Create a new audit to get the current competitor benchmark:

```bash
curl -s -X POST -H "API-KEY: [key]" -H "Content-Type: application/json" \
  -d '{"keyword": "[keyword]", "url": "[page URL]", "location": "[country]"}' \
  "https://app.surferseo.com/api/v1/audits"
```

Poll `GET /v1/audits/{id}` until `state` is "completed". This returns:
- `audited_page`: `{url, content_score}` -- your page's current score (scored against the live page)
- `competitors_pages`: array of `{url, content_score}` -- competitor scores

This gives you the "audit score" which reads the live page directly.

### 2c. Update Content Editor Score (if editor exists)

If the plan file has a `surfer-editor-id` from the original gap analysis:

1. Fetch the live page content (curl or WebFetch)
2. PATCH it into the existing editor:

```bash
curl -s -X PATCH -H "API-KEY: [key]" -H "Content-Type: application/json" \
  -d '{"content": "[HTML content]"}' \
  "https://app.surferseo.com/api/v1/content_editors/{id}"
```

3. Get the updated content score:

```bash
curl -s -H "API-KEY: [key]" \
  "https://app.surferseo.com/api/v1/content_editors/{id}/content_score"
```

4. Pull the NLP terms to check which gaps have closed:

```bash
curl -s -H "API-KEY: [key]" \
  "https://app.surferseo.com/api/v1/content_editors/{id}/terms"
```

If no editor ID exists in the plan, skip the editor check and rely on the audit score from 2b.

Note: the audit score (live page via Surfer's crawler) and the editor score (PATCHed HTML) often differ slightly. Both are useful -- the audit is what Surfer sees on the live site, the editor is what it scores from your content directly.

### 2d. Compare Scores

Build the comparison using baseline data from the plan file:

| Metric | Baseline | Current | Change | Status |
|--------|----------|---------|--------|--------|
| Audit score (live page) | X | Y | +/-Z | improved/stalled/dropped |
| Editor score (primary keyword) | X | Y | +/-Z | improved/stalled/dropped |
| Editor score (secondary keyword) | X | Y | +/-Z | improved/stalled/dropped |

### 2e. NLP Term Gap Check

If you pulled terms in 2c, compare against the missing terms from the original plan:

| Term | Original Status | Current Status | Verdict |
|------|----------------|----------------|---------|
| [term] | Missing (target: 3-8) | Now included (4 uses) | Gap closed |
| [term] | Missing (target: 2-5) | Still missing | Still a gap |
| [term] | Included (2 uses) | Now over-used (12 uses) | Over-optimised -- flag |

Focus on the terms that were categorised as "Include" or "Expand" in the original gap analysis. Skip terms that were categorised as "Skip" (NLP noise).

---

## Phase 3 -- Verdict and Next Steps

Based on the Ahrefs and Surfer data, make a recommendation using this decision framework:

### Scenario 1: Positions improved, Surfer score improved

Everything's working. Options:

- **Close out** if targets are met (primary keyword in target range, Surfer 80+)
- **Schedule another review** in 14 days if improving but not at target yet
- **Shift to off-site** (backlinks) if on-page is maxed but positions need more push

### Scenario 2: Positions improved, Surfer score flat/dropped

Content is ranking but Surfer disagrees. Positions matter more than Surfer scores. Possible causes:

- Competitor content improved (Surfer is relative)
- Surfer's NLP model updated

Recommendation: focus on positions and traffic, use Surfer as a secondary signal.

### Scenario 3: Positions flat, Surfer score improved

Content quality improved but Google hasn't rewarded it yet. Normal if < 4 weeks since implementation.

Recommendation: wait another 2 weeks. If still flat after 6 weeks total, investigate technical issues or shift to backlinks.

### Scenario 4: Positions dropped, any Surfer score

Investigate immediately:

1. Cannibalization (another page stealing the keyword)
2. Technical issue (page deindexed, redirect broken, noindex tag)
3. Competitor surge (new strong competitor entered)
4. Algorithm update (check SEO news)
5. Content overwritten (compare current page to implementation commit)

Create urgent Asana tasks for the root cause.

### Scenario 5: Implementation not done yet

Position check only. Movement here is purely organic/seasonal. Note it as a pre-implementation baseline and remind Pete about the outstanding implementation work.

---

## Phase 4 -- Update Everything

### 4a. SEO Page Tracker

Update the row in the property README's SEO Page Tracker table:

- **Current Position**: latest from Ahrefs
- **Surfer Score**: new scores if re-audit was done (format: "71 > 82 training / 55 > 68 course")
- **Next Review**: implementation date + 28 days (or "--" if closing out)
- **Status**: update based on verdict ("Review 1 complete", "Closing out", "Needs content top-up", etc.)

### 4b. Plan File

Add a review section to the existing plan file:

```markdown
## Review 1 -- [date]

### Ahrefs Positions
[Position comparison table]

### GSC Data
[Clicks, impressions, CTR, avg position]

### Surfer Re-Audit
[Score comparison if done, or "Skipped -- implementation pending"]

### Verdict
[Which scenario from the decision framework, and the recommendation]

### Actions
- [ ] [Any follow-up actions identified]
```

For subsequent reviews, add "Review 2", "Review 3", etc. This builds a longitudinal record in the plan file.

### 4c. Asana Tasks

- **Complete** the "Fortnightly review" task if it exists and is due
- **Create** a new "Fortnightly review -- [Page Name] (due [date])" task if ongoing monitoring is needed (due date = today + 14 days)
- **Create** any follow-up tasks identified in the verdict (content top-up, backlink push, technical investigation)

Use `Library/processes/asana-configuration.md` for GIDs.

### 4d. Daily Log

Note what was reviewed and the outcome in the daily note.

---

## Multi-Page Review Mode

When reviewing all due pages on a property in one session:

1. Run Phase 0b to scan the SEO Page Tracker and list all due/overdue pages
2. For each page, run Phases 1-4 in sequence
3. At the end, present a summary table:

| Page | Keyword | Position Change | Surfer Change | Verdict |
|------|---------|----------------|---------------|---------|
| [page 1] | [kw] | 28 > 15 | 71 > 82 | Improving, review in 14d |
| [page 2] | [kw] | 5 > 4 | 85 > 87 | Close out |

This gives Pete a single-glance view of how all tracked pages are performing.

---

## Handling Edge Cases

**Page was never implemented**: Skip Surfer re-audit. Do Ahrefs check only. Note pre-implementation baseline.

**Surfer audits were never done**: Skip Surfer comparison. Do Ahrefs check only. Suggest running baseline Surfer audits as a next step.

**Page URL changed since last review**: Check the plan file for redirect history. Use the new URL for all checks. Verify the redirect is working (301/308 from old to new).

**Property has no pages tracked yet**: Tell Pete there's nothing to review. Suggest running the ahrefs-audit skill to set up the first page.

**Ahrefs Rank Tracker tags not set up**: The skill can still pull data from `site-explorer-organic-keywords`, but note that proper tracking isn't in place. Create an Asana task to set up tags.

**No Surfer editor ID in plan file**: The page was set up before Surfer API integration. Run the audit (2b) for a live score, but skip the editor check (2c). Suggest running a fresh ahrefs-audit to create the editor for future reviews.

**Surfer API errors**: If the Surfer API returns errors (rate limit, timeout, etc.), continue with the Ahrefs-only review. Note the Surfer failure and retry next review. Don't block the entire review on a Surfer issue.

---

## API Endpoint Quick Reference

### Ahrefs (GET unless noted)

All calls: `curl -s -H "Authorization: Bearer [token]" "https://api.ahrefs.com/v3/[endpoint]?[params]"`

| What | Endpoint | Key Params |
|------|----------|------------|
| Rank Tracker positions | `rank-tracker/overview` | `project_id` |
| List tracked keywords + tags | `management/project-keywords` | `project_id` |
| Page keyword profile | `site-explorer/organic-keywords` | `target`, `mode=exact`, `country`, `columns` |
| Domain cannibalization check | `site-explorer/organic-keywords` | `target=[domain]`, `mode=domain`, `search=[keyword]` |
| Backlink check | `site-explorer/backlinks-stats` | `target`, `mode=exact` |
| SERP overview | `serp-overview` | `keyword`, `country` |
| GSC keywords | `gsc/keywords` | `target`, `search` |
| GSC pages | `gsc/pages` | `target`, `search` |

### Surfer

All calls: `curl -s -H "API-KEY: [key]" "https://app.surferseo.com/api/v1/[endpoint]"`

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/v1/audits` | Create audit (live page score + competitor benchmark) |
| GET | `/v1/audits/{id}` | Get audit results (poll until state: "completed") |
| PATCH | `/v1/content_editors/{id}` | Push updated content for scoring |
| GET | `/v1/content_editors/{id}/content_score` | Get current content score |
| GET | `/v1/content_editors/{id}/terms` | NLP terms with inclusion status |

### Common Gotchas

- **Ahrefs**: Use `sum_traffic` not `traffic` in columns. SERP features appear as position 1. GSC data lags 2-3 days.
- **Surfer**: `keyword` is singular for audits (not `keywords` array). Audit score reads live page; editor score reads PATCHed content -- expect slight differences. Poll until state is "completed"/"active" before reading results.
- **Both**: Config files have full endpoint docs: [[ahrefs-api-configuration]] and [[surfer-api-configuration]].


## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill per [[Library/audits/2026-05-16-lesson-deployment-matrix]]:

- [[Library/lessons/2026-05-07-surfer-rewards-curriculum-detail]]
- [[Library/lessons/2026-05-16-surfer-audit-read-every-section-and-dual-keyword]]
- [[Library/lessons/2026-05-17-bulk-h1-audit-must-respect-do-not-touch-flags]] — fortnightly audits that touch H1 must respect per-page "DO NOT touch" flags
- [[Library/lessons/2026-05-17-surfer-audit-score-is-competitor-mix-dependent]] — re-audit content scores can swing 75↔83 with no content change; trust direction, not absolute score
- [[Library/lessons/2026-05-19-surfer-optimise-to-csv-not-audit-trim-recommendations]]

