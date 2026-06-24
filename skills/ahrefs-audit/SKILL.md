---
name: ahrefs-audit
description: >-
  Combined Ahrefs + Surfer SEO page audit and optimisation plan. Pulls data from both APIs,
  cross-references keyword intelligence with NLP content analysis, and builds a balanced
  optimisation plan where neither tool's score is gospel. Handles both new pages (full setup)
  and re-runs on existing pages (skips what's already done, reports outstanding Asana tasks).
  Use this skill whenever Pete says "audit this page", "ahrefs audit", "research this keyword",
  "set up a new page for SEO", "run the ahrefs report", "what's the competition for [keyword]",
  "gap analysis", "analyse this page", "SEO audit", "why isn't this ranking", "optimise this
  page", "content audit", "compare to competitors", or any request to research and plan SEO
  work on a specific page. Also use when Pete mentions a page and keyword combination that
  doesn't yet have a vault plan file. One page, one keyword cluster, one property per run.
---

<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Asana / Sheets / Docs / Xero / Odoo / GSC / GA4 / Ads / Vision / Geocoding / Sentry / Cloudflare / Vercel operation in this skill, see [[external-service-routing]]. Helper-first. -->

> **This skill runs in Claude Code.** If triggered in Cowork, stop and tell Pete.

# AhrefAudit

Combined audit using Ahrefs (strategic intelligence), Surfer (content intelligence), and Google Search Console (impression/click truth) to produce a balanced optimisation plan. Neither tool's score is the target. Pete controls the editorial direction.

This skill applies to `property_type: marketing-site` properties primarily (vocabulary at [[vault-routing#property-type-vocabulary]]). The reusable per-page SEO workflow lives at [[page-seo-workflow]] (referenced from each property's README) — follow that for new pages so they fit the existing pattern.

Version history: [[CHANGELOG]].

## Philosophy

Ahrefs tells you what keywords matter, who's competing, and how they got there. Surfer tells you what NLP terms competitors use and how your content structure compares. Both are useful signals, but both have blind spots.

Surfer's NLP recommendations often lean towards keyword stuffing -- hitting a term count target without considering whether it reads naturally or adds value. Ahrefs doesn't understand content quality at all. The skill's job is to present both datasets, highlight where they agree and where they diverge, and let Pete make the call.

**The golden rule: write content for humans that happens to satisfy search engines, not the other way round.**

## Required Connectors

| Connector | How | Used for |
|-----------|-----|----------|
| Ahrefs API v3 | Direct API via bash curl. Config: [[ahrefs-api-configuration]] | Keywords, SERP, competitors, backlinks, positions, Rank Tracker writes |
| Surfer SEO API | Direct API via bash curl. Config: [[surfer-api-configuration]] | Content editors, NLP terms, content scoring, competitor audits |
| GSC API | Direct via service account JWT. Config: [[google-api-credentials]]. Key file: `Library/processes/secrets/google-seo-service-account.json` | searchAnalytics/query for impressions, clicks, CTR, position -- true user behaviour |
| Asana MCP | `mcp__asana__asana_*` (load via ToolSearch) | Section creation, standing tasks |
| Vault (file tools) | Read/Write/Edit | Plan files, property READMEs, MAP.md |

**Auth quick reference** (full details in config files):

```bash
# Ahrefs
curl -s -H "Authorization: Bearer lGssv7YX4gEWyDhKaBhDLcmLfs14q-yqlZTzsMQa" \
  "https://api.ahrefs.com/v3/[endpoint]"

# Surfer
curl -s -H "API-KEY: vfv0b3tbStnuc_Utup9AXCsdI32sNT_8" \
  "https://app.surferseo.com/api/v1/[endpoint]"
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

body = {
  "startDate": "2026-03-22", "endDate": "2026-04-22",
  "dimensions": ["query", "page"],
  "dimensionFilterGroups": [{"filters": [{"dimension": "page",
    "operator": "equals", "expression": "https://sygma-solutions.com/courses/cat-and-genny-training"}]}],
  "rowLimit": 100
}
r = requests.post(
  "https://searchconsole.googleapis.com/webmasters/v3/sites/sc-domain%3Asygma-solutions.com/searchAnalytics/query",
  headers={"Authorization": f"Bearer {creds.token}"}, json=body)
print(json.dumps(r.json(), indent=2))
```

Use for: real impressions, clicks, CTR, and average position at the query level -- the ground truth that Ahrefs and Surfer don't have. Pulls 28-day window by default.

---

## Phase 0 -- Gather Inputs

### 0a. Property Discovery

Ask Pete which property and page. Read the property README from `Properties/{Name}/README.md` and the parent project's `seo/` sub-project README from `Projects/{Parent}/seo/README.md` (post 2026-05-06 restructure -- SEO no longer has its own top-level project).

From the property README, get: domain, Ahrefs project ID, Surfer workspace ID, country/region.
From the SEO sub-project README, get: standing instructions, SEO Page Tracker, API Quick Reference. The SEO Page Tracker is in the parent property README's `data/` folder, not the project.

**Property-to-project mapping (post 2026-05-06 restructure):**

| Property | Parent Project | SEO sub-project path | Ahrefs Project | Surfer Workspace |
|----------|------------|----------|----------------|------------------|
| Sygma Solutions Website | SY-Website | `Projects/SY-Website/seo/` | 9613452 | 1312139 |
| Canary Detect Main Website | CD-Website | `Projects/CD-Website/seo/` | 9613451 | 1312141 |
| The Leaky Finders Website | CD-Other-Sites | `Projects/CD-Other-Sites/the-leaky-finders/` (sub-project covers all channels including SEO) | 9613450 | Yes |
| LeakGuard Lanzarote | CD-Other-Sites | `Projects/CD-Other-Sites/leakguard-lanzarote/` | 9613445 | 1330651 |
| Pipebusters Lanzarote | CD-Other-Sites | `Projects/CD-Other-Sites/pipebusters-lanzarote/` | 9613446 | Yes |
| Leakbusters Lanzarote Website | CD-Other-Sites | `Projects/CD-Other-Sites/leakbusters/` | 9613448 | 1312143 |

In Asana, SEO work for main sites lives under the parent project's `seo` section (created in the Asana restructure). For CD-Other-Sites, each secondary site has its own section, and SEO work for that site is filed in that section. New tasks: place via `asana_add_task_to_section`.

### 0b. Page and Keyword

Ask Pete which page URL and primary keyword. If he gives a keyword without a page, use Ahrefs `site-explorer/organic-keywords` with `search: [keyword]` at domain level to find which URL ranks. One page per run.

### 0c. Existing Work Check

Before doing any research:

1. **Vault plan file**: Look in `Projects/{Parent}/seo/files/` (e.g. `Projects/SY-Website/seo/files/`) for an existing `*-seo-plan.md` matching this page. For CD-Other-Sites, look in the per-site sub-project: `Projects/CD-Other-Sites/{site-slug}/files/`.
2. **SEO Page Tracker**: Check the property README for an existing row
3. **Asana section**: Use `asana_get_sections` on the SEO project to check for an existing section. If found, pull tasks and report outstanding ones.

If a plan file exists with Ahrefs research already done (from a previous run), ask Pete if he wants to skip to Phase 2 (Surfer) or re-run everything fresh.

### 0d. Confirm and Start

> "Running audit on **[page URL]** targeting **[keyword]** on **[property]** ([country]). [New / Picking up from existing plan]. I'll pull data from both Ahrefs and Surfer, cross-reference, and build the optimisation plan. Ready?"

---

## Phase 1 -- Ahrefs Strategic Intelligence

The 6-step research that was always the core of this skill. If the plan file already has this data from a previous run and Pete doesn't want it refreshed, skip to Phase 2.

### Step 1: Keywords Explorer

**Primary keyword overview:**
- `keywords-explorer/overview` with `keywords: [primary keyword]`, `country: [country code]`

**Matching terms (direct variants):**
- `keywords-explorer/matching-terms` with `keyword: [primary keyword]`, `country: [country code]`, `limit: 30`

**Related terms (broader topic):**
- `keywords-explorer/related-terms` with `keyword: [primary keyword]`, `country: [country code]`, `limit: 30`

**Search suggestions:**
- `keywords-explorer/search-suggestions` with `keyword: [primary keyword]`, `country: [country code]`, `limit: 20`

Compile into keyword cluster table with volume, KD, CPC. Calculate total addressable volume.

### Step 2: SERP Overview

- `serp-overview` with `keyword: [primary keyword]`, `country: [country code]`

Capture URL, domain, position, DR, backlinks, referring domains for each organic result. Note SERP features. SERP features often show as position 1 -- real organic starts at 2+.

### Step 3: Our Page Performance

- `site-explorer/organic-keywords` for page keyword profile (mode: exact)
- `site-explorer/metrics` and `site-explorer/domain-rating` for domain metrics
- Cannibalization check: `site-explorer/organic-keywords` at domain level with `search: [keyword]`
- `site-explorer/pages-by-internal-links` for internal link profile

### Step 4: Site Audit

- `site-audit/issues` for technical issues affecting the target page

### Step 5: Backlink Gap

- Our page: `site-explorer/backlinks-stats` and `site-explorer/referring-domains`
- Top 3-5 competitors: same endpoints per competitor URL
- Identify domains linking to them but not to us

### Step 6: Rank Tracker and GSC

- `rank-tracker/overview` and `management/project-keywords` to check tracking status
- **Primary GSC source:** call the GSC API directly via service account JWT (see Auth quick reference). Pull 28 days of `searchAnalytics/query` at `query` and `page` dimensions, filtered to the target URL. This is the ground truth -- real impressions, clicks, CTR, average position by query.
- Ahrefs `gsc/keywords` and `gsc/pages` remain as a convenient secondary check, but the direct GSC API is authoritative.

Capture for the plan file: top 10 queries by impressions, click-through rate on the target URL, position trend over the period, any queries with impressions but zero clicks (CTR rescue candidates).

---

## Phase 2 -- Surfer Content Intelligence

All calls use the Surfer API directly. Audits + editors via API. **Full audit signal breakdown requires Chrome MCP** -- the `/v1/audits/{id}` endpoint returns only content score + competitors. The per-signal breakdown (word count, H2-H6 count, exact/partial keyword distribution per element, strong/b counts, title/meta chars, terms-to-use) lives only in the Surfer UI. After kicking off the audit via API, also navigate to the audit's permalink in Chrome MCP and `get_page_text` to capture every signal. **Don't propose changes off the headline score alone** -- the signals beneath are what move it. See `[[Library/lessons/2026-05-16-surfer-audit-read-every-section-and-dual-keyword]]`.

**Dual-keyword pages** -- for any page targeting a "X training" / "X course" pair (the standard Sygma course-page shape per `seo-targeting-principles` section 1d, also applies to other paired commercial intents), run **TWO audits** in Step 2a, one per phrase. Reconcile the full signal sets side-by-side -- they often disagree on word-count trim, exact-keyword distribution in H1/title, body density. Surface conflicts explicitly before proposing edits.

### Step 2a. Create Surfer Audit (Competitor Benchmark)

Run a Surfer audit to get competitor content scores for this keyword:

```bash
curl -s -X POST -H "API-KEY: [key]" -H "Content-Type: application/json" \
  -d '{"keyword": "[keyword]", "url": "[page URL]", "location": "[country]"}' \
  "https://app.surferseo.com/api/v1/audits"
```

Poll `GET /v1/audits/{id}` until `state` is "completed". This returns:
- `audited_page`: your page's content score
- `competitors_pages`: array of competitor URLs with their content scores

**Cross-reference with Ahrefs SERP data from Phase 1 Step 2.** For each competitor that appears in both datasets, you now have: their SERP position (Ahrefs), their DR and backlinks (Ahrefs), AND their content score (Surfer). This shows whether top positions correlate with content quality or domain authority -- that distinction matters for the plan.

### Step 2b. Create Content Editor (NLP Terms)

Create a content editor for the primary keyword:

```bash
curl -s -X POST -H "API-KEY: [key]" -H "Content-Type: application/json" \
  -d '{"keywords": ["[keyword]"], "location": "[country]", "workspace_id": [workspace_id]}' \
  "https://app.surferseo.com/api/v1/content_editors"
```

Poll `GET /v1/content_editors/{id}` until `state` is "active".

Then fetch the NLP terms:

```bash
curl -s -H "API-KEY: [key]" \
  "https://app.surferseo.com/api/v1/content_editors/{id}/terms"
```

This returns 200+ terms with: `term`, `included` (boolean), `is_nlp` (boolean), `target_range` (min/max), `use_in_heading` (boolean).

### Step 2c. Push Current Content for Baseline Score

Fetch the live page content (via WebFetch or curl), then PATCH it into the editor:

```bash
curl -s -X PATCH -H "API-KEY: [key]" -H "Content-Type: application/json" \
  -d '{"content": "[HTML content]"}' \
  "https://app.surferseo.com/api/v1/content_editors/{id}"
```

Then get the baseline score:

```bash
curl -s -H "API-KEY: [key]" \
  "https://app.surferseo.com/api/v1/content_editors/{id}/content_score"
```

### Step 2d. Multi-Keyword (if applicable)

If Pete identified secondary keywords in Phase 0d, repeat Steps 2b-2c for each. Different keywords produce different NLP term lists -- the combined view shows which terms are important across the cluster.

### Step 2e. Compile Surfer Intelligence

For each editor, categorise the NLP terms:

| Category | Description | How to Use |
|----------|-------------|------------|
| **High-value NLP** | `is_nlp: true`, not included, target_range min > 0 | These are the terms Surfer thinks matter most. Cross-reference with Ahrefs keyword data before acting. |
| **Heading candidates** | `use_in_heading: true`, not included | Potential H2/H3 restructure opportunities. Only use if they make editorial sense. |
| **Already covered** | `included: true`, in target range | Leave alone. Don't over-optimise. |
| **Over-used** | `included: true`, above target_range max | Possible keyword stuffing already present. Consider reducing. |
| **Low-value** | `is_nlp: false`, low target range | Ignore these. They're statistical noise. |

---

## Phase 3 -- Cross-Reference and Balance

This is the most important phase. This is where both datasets meet and the plan takes shape.

### Step 3a. Combined Competitor View

Build a table combining Ahrefs and Surfer data for every competitor that appears in both:

| Competitor | SERP Position | DR | Backlinks | Ref Domains | Content Score | Word Count |
|-----------|---------------|-----|-----------|-------------|---------------|------------|
| Our page | X | X | X | X | X | X |
| competitor1.com | X | X | X | X | X | X |
| competitor2.com | X | X | X | X | X | X |

**Analysis questions:**
- Do high-DR sites rank despite low content scores? (authority-dominated SERP -- backlinks matter more than content)
- Do low-DR sites rank with high content scores? (content-quality SERP -- on-page work will move the needle)
- Where does our page sit in both dimensions?

### Step 3b. NLP Term Filtering

Go through the high-value NLP terms from Phase 2e and cross-reference each against:

1. **Ahrefs keyword data**: does this term appear as a keyword variant with actual search volume? If yes, it's a strong signal. If not, it might be NLP noise.
2. **Domain knowledge**: does this term make sense in the context of Pete's business? Some terms only work paired (e.g. "genny certificate" must be "cat and genny certificate"). Some are from adjacent but incorrect domains.
3. **Current content**: is the term missing because it genuinely should be there, or because the page intentionally covers a different angle?

Categorise each high-value term:

| Term | Surfer Target | Ahrefs Volume | In Content? | Verdict |
|------|--------------|---------------|-------------|---------|
| [term] | 3-8 uses | 200/mo | No | **Include** -- real keyword with volume, natural fit |
| [term] | 2-5 uses | 0 | No | **Skip** -- NLP noise, no search demand |
| [term] | 1-3 uses | 50/mo | No | **Consider** -- low volume but topically relevant |
| [term] | 5-10 uses | 800/mo | Yes (2 uses) | **Expand** -- strong keyword, currently underused |

### Step 3c. The Balanced View

Present Pete with a clear summary:

**Where both tools agree:** [terms/topics that Ahrefs shows have volume AND Surfer flags as missing NLP terms -- these are the strongest signals]

**Where only Ahrefs sees value:** [keywords with search volume that Surfer doesn't flag -- these might need new sections or pages rather than NLP tweaking]

**Where only Surfer sees value:** [NLP terms with no search volume -- treat with scepticism, only include if they genuinely improve the content]

**Content vs Authority gap:** [based on the competitor table -- is the main gap content quality, backlinks, or both?]

---

## Phase 4 -- Build the Optimisation Plan

### Step 4a. Connect to the Repo

Clone the GitHub repo fresh using the PAT from [[github-configuration]]. Read the target page file. Map the current structure: headings, sections, word count, existing terms.

### Step 4b. Draft Content Changes

Based on the Phase 3 cross-reference, draft specific changes organised by priority:

**Priority 1 -- Quick wins (both tools agree):**
Terms that have search volume AND are flagged by Surfer AND fit naturally. These go in first.

**Priority 2 -- Strategic additions (Ahrefs-led):**
New sections or expansions driven by keyword gaps found in Ahrefs. These might not be in Surfer's NLP list but address real search demand.

**Priority 3 -- NLP polish (Surfer-led, filtered):**
Terms from Surfer that passed the domain knowledge filter. Light touch -- weave naturally, don't stuff.

**Do NOT include:**
- Terms flagged as "Skip" in the filtering step
- Any change that reads like keyword stuffing
- Terms from unrelated domains that Surfer's NLP confused
- Heading changes that sacrifice readability for keyword placement

For each change, specify: what section, what to add/modify, which terms it addresses, estimated word count impact.

### Step 4c. URL, Title, Meta, Schema

Propose changes to:
- URL/slug (if needed -- include redirect plan)
- Title tag and H1
- Meta description
- Schema markup

Base these on the Ahrefs keyword cluster (what people search for) not just Surfer's recommendations.

### Step 4d. Internal Linking

From Phase 1 Step 3, identify:
- Pages that should link TO this page (with anchor text)
- Pages this page should link OUT to
- Any hub/spoke opportunities

### Step 4e. Backlink Targets

From Phase 1 Step 5, produce a prioritised outreach list:
- Target site, DR, type, angle, priority (High: DR 40+, Med: DR 20-40, Low: under 20)

### Step 4f. Present for Approval

Present the complete plan to Pete. Include the balanced view from Phase 3c so he can see the reasoning. He may:
- Approve as-is
- Reject specific changes
- Add domain knowledge Claude doesn't have
- Adjust the priority ordering
- Override Surfer recommendations with editorial judgment

**Do NOT proceed to implementation until Pete approves.**

---

## Phase 5 -- Asana Setup

Read `Library/processes/asana-configuration.md` for project GIDs.

### 5a. Create Section (if needed)

Check for existing section with `asana_get_sections`. If not found, create one matching the page name.

### 5b. Standing Tasks (if section is new)

1. **"Set up Ahrefs Rank Tracker tags -- [Page Name]"** -- P2, assigned to Pete
2. **"Surfer baseline audit -- [Page Name]"** -- mark as complete (we just did it via API)
3. Don't create the fortnightly review task yet -- that happens after implementation

---

## Phase 6 -- Save to Vault

### 6a. Write/Update Plan File

Save to `Projects/{Parent}/seo/files/{page-slug}-seo-plan.md` (e.g. `Projects/SY-Website/seo/files/...`), or for CD-Other-Sites use `Projects/CD-Other-Sites/{site-slug}/files/{page-slug}-seo-plan.md`. Structure:

```markdown
---
type: seo-plan
page: [URL path]
target-keyword: [primary keyword]
secondary-keywords: [list]
property: "[[Property Name]]"
status: plan-approved / research-complete
created: [date]
updated: [date]
surfer-editor-id: [editor ID from Phase 2b]
surfer-audit-id: [audit ID from Phase 2a]
---

## Overview
[One paragraph summary]

## Ahrefs Intelligence
[Compiled from Phase 1 -- keyword cluster, SERP analysis, current performance, backlink gap]

## Surfer Intelligence
[Compiled from Phase 2 -- competitor content scores, NLP terms, baseline score]

## Cross-Reference Analysis
[From Phase 3 -- balanced view, filtered terms, content vs authority assessment]

## Optimisation Plan
[From Phase 4 -- prioritised changes with rationale]

## Backlink Targets
[From Phase 4e]

## Post-Optimisation Checklist
- [ ] Implement content changes (Claude Code + property-manager)
- [ ] Re-crawl in Ahrefs
- [ ] Request indexing in GSC
- [ ] PATCH updated content to Surfer editor and check new score
- [ ] Update SEO Page Tracker
- [ ] Create fortnightly review task (due: implementation + 14 days)
```

### 6b. SEO Page Tracker

Update the property README's SEO Page Tracker with baseline data.

### 6c. Project README Working Files

Update the SEO project README's Working Files section.

### 6d. MAP.md

Add new files if created.

### 6e. Summary for Pete

> **Page**: [URL]
> **Keyword**: [keyword] ([volume]/mo)
> **Current position**: [X] (Ahrefs) | Content score: [X] (Surfer)
> **Competitor benchmark**: avg position [X], avg content score [X], avg DR [X]
> **Main gap**: [content / authority / both]
> **Plan**: [X] content changes across [X] priority levels. [X] backlink targets.
> **Next step**: Pete approves plan, then implementation in Claude Code.

---

## Rescan Mode

When a page has been optimised and needs checking, use the **audit-review** skill instead. That skill handles the fortnightly review cycle with position checks and score comparisons.

If Pete says "rescan" or "check how it's doing", trigger audit-review, not this skill.

---

## API Quick Reference

### Ahrefs (GET unless noted)

| Endpoint | Purpose | Key Params |
|----------|---------|------------|
| `keywords-explorer/overview` | Keyword metrics | `keywords`, `country` |
| `keywords-explorer/matching-terms` | Direct variants | `keyword`, `country`, `limit` |
| `keywords-explorer/related-terms` | Broader topic | `keyword`, `country`, `limit` |
| `keywords-explorer/search-suggestions` | Autocomplete | `keyword`, `country`, `limit` |
| `serp-overview` | Who's ranking | `keyword`, `country` |
| `site-explorer/organic-keywords` | Page/domain keywords | `target`, `mode`, `country`, `columns` |
| `site-explorer/metrics` | Domain traffic | `target` |
| `site-explorer/domain-rating` | DR | `target` |
| `site-explorer/pages-by-internal-links` | Internal links | `target`, `columns=url_to,links_to_target` |
| `site-audit/issues` | Technical issues | `target` |
| `site-explorer/backlinks-stats` | Backlink counts | `target`, `mode` |
| `site-explorer/referring-domains` | Who links to us | `target`, `mode`, `limit` |
| `rank-tracker/overview` | Tracked positions | `project_id` |
| `gsc/keywords` | GSC keyword data | `target`, `search` |
| `gsc/pages` | GSC page data | `target`, `search` |
| `management/project-keywords` (PUT) | Add keywords + tags | See [[ahrefs-api-configuration]] |

### Surfer

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/v1/audits` | Create audit (competitor benchmark) |
| GET | `/v1/audits/{id}` | Get audit results |
| POST | `/v1/content_editors` | Create editor |
| GET | `/v1/content_editors/{id}` | Editor detail (poll for state) |
| GET | `/v1/content_editors/{id}/terms` | NLP terms list |
| GET | `/v1/content_editors/{id}/content_score` | Current score |
| PATCH | `/v1/content_editors/{id}` | Push content for scoring |
| GET | `/v2/content_editors` | Rich list with score breakdown |

### Common Gotchas

- **Ahrefs**: Use `sum_traffic` not `traffic`. Use `url_to`/`links_to_target` for internal links. Add `www.` for competitors. SERP features show as position 1.
- **Surfer**: `import_content_url` on create is unreliable -- always PATCH content in manually. `keyword` is singular for audits, `keywords` is array for editors. Poll until state is "active"/"completed" before reading results.
- **Both**: Audit score (live page) differs from editor score (PATCHed content). Don't panic if they don't match.


## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill per [[Library/audits/2026-05-16-lesson-deployment-matrix]]:

- [[Library/lessons/2026-05-07-surfer-rewards-curriculum-detail]]
- [[Library/lessons/2026-05-16-surfer-audit-read-every-section-and-dual-keyword]]
- [[Library/lessons/2026-05-17-bulk-h1-audit-must-respect-do-not-touch-flags]] — bulk H1 trims must read per-page "DO NOT touch" flags before rewriting; respect approved-H1 status
- [[Library/lessons/2026-05-17-surfer-audit-score-is-competitor-mix-dependent]] — Surfer Content Score = competitor-pool dependent; before/after needs same-day parallel audits
- [[Library/lessons/2026-05-19-surfer-optimise-to-csv-not-audit-trim-recommendations]]

