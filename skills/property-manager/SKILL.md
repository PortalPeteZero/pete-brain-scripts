---
name: property-manager
description: "Use this skill whenever Pete wants to work on any website, app, or digital property -- connecting to repos, analysing code, making changes, running audits, or setting up new properties. Triggers include: 'connect to my site', 'look at my app', 'analyse my site', 'check the SEO', 'review my code', 'make changes', 'edit the repo', 'set up a new project', 'I just created a new site', or any mention of a specific property or project name. Replaces the old lovable-site-manager and lovable-no-prerender skills with a single unified workflow that adapts to any tech stack."
---

<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Asana / Sheets / Docs / Xero / Odoo / GSC / GA4 / Ads / Vision / Geocoding / Sentry / Cloudflare / Vercel operation in this skill, see [[external-service-routing]]. Helper-first. -->

# Property Manager — Universal Workflow Skill

> [!important] Business OS migration — content lives in Drive + the knowledge DB, not the vault tree
> A property's working files (`Projects/{name}/files/`) and reference data (`Properties/{Name}/data/`) are migrating to **Google Drive** (find via `drive_files`: `Library/processes/scripts/cc-sql.py`) and the property cards into the **CC Properties module** (Part E). The vault `Properties/` + `Projects/` content folders are **legacy mirrors pending retirement at Part I**. Route new content per the new-world matrix in [[vault-routing]]; `[[wikilinks]]` resolve against `vault_notes` (no rewriting). Code repos still clone to a temp dir (never the vault). Full picture: `MAP.md`.

Single workflow for connecting to any of Pete's digital properties, understanding architecture, making changes safely, and keeping the vault up to date.

Properties carry a `property_type:` frontmatter field on their README (vocabulary at [[vault-routing#property-type-vocabulary]]: `marketing-site`, `saas-app`, `internal-tool`, `external-data-source`, `microsite`). Read the type when opening a property and adapt the workflow lens accordingly. Style rules for outbound communications live in [[voice-principles]] only — PRs, commit messages, README writes, audit reports, and code comments are internal artefacts and not subject to those rules.

> [!important] Live state is machine-maintained — don't re-derive it by hand
> Each property card carries a `<!-- LIVE-STATE -->` block (host, deployed commit vs repo head, DNS, Supabase, GSC/GA4/Ahrefs/GTM) refreshed every night by the [[property-state-and-capability-system-plan|property-state system]] (`property-live-state.py`). **Read that block for current state; don't manually curl/check what's already verified there.** The §E service-declaration frontmatter (`domains`, `hosting`, `github`, `vercel_project`, `gsc_property`, `ga4_property`, …) drives it — keep it filled on every card. The whole estate is on the dashboard at `properties-dashboard-xi.vercel.app`; in Claude Code, mentioning a property auto-injects its verified state via the `property-context-hook`.

Version history: [[CHANGELOG]].

> **This skill runs in Claude Code.** If triggered in Cowork, stop and tell Pete.

## Overview

Pete owns ~30 digital properties -- websites, apps, dashboards, tools -- built with various tech stacks (Lovable, React+Vite, Manus, static HTML, etc.). This skill is the single workflow for connecting to any of them, understanding the architecture, making changes safely, and keeping the vault up to date.

**Read this skill in full before taking any action.**

---

## CRITICAL -- Non-Technical User Protocol

> [!warning] Pete cannot review code.
> Pete is a non-technical user. He will never read a diff, a PR, a grep output, or understand what commit hashes mean. The assistant owns all code-level verification. The assistant never asks Pete to "review" or "approve" code. The assistant reports outcomes in plain English with concrete evidence, and Pete approves or redirects based on those outcomes.
>
> "Can you check this PR?" / "Does this diff look right?" / "Please review my changes" -- **never ask these questions of Pete.** Self-verify, then report in plain English.

### Plain-English Evidence Requirement

Every "done" claim must include concrete evidence Pete can trust, expressed without jargon:

- **What was changed, in plain language** (not "refactored the AuthContext useEffect" -- say "fixed the login page so it waits for the user's profile before redirecting")
- **How it was verified** ("the live site now returns 200 instead of 404", "the old URL still redirects to the new one", "the Vercel deploy finished and the page shows the new title")
- **What Pete can do to see it working** ("reload leakguard-manager.com/auth and log in without a hard refresh")
- **Commit hash and short message** (as a footer reference, not as the proof)

Never assert "done" without evidence. "It pushed successfully" is NOT evidence. "It built locally" is NOT evidence. Evidence is: the live site behaves correctly, a fresh clone builds clean, the deployed behaviour matches the intended behaviour.

### Per-Step Stop-and-Check

Every code step ends with a STOP. The assistant:

1. Completes the step end-to-end (edit → verify locally → push → merge → verify live)
2. Reports in plain English with evidence
3. Waits for Pete to say "next" (or "go", "continue", etc.) before starting the next step

**Never batch multiple code steps into one push.** One logical change per PR. If three things need to change, that is three stop-and-check cycles. Auto mode does NOT override this -- auto mode means execute the current step without mid-step permission prompts, it does NOT mean skip the stop between steps.

---

## Step 0 -- Identify the Property

Before doing anything, find out what you're working with. The vault already knows most of this.

### 0a. Find the property README

Read `Properties/{property-name}/README.md`. This contains:

- Domain and live URL
- Tech stack (Lovable, React+Vite, Manus, static, etc.)
- GitHub repo and account (PortalPeteZero or SygmaSol)
- Supabase project ref (if any)
- Vercel project (if any)
- LovableHTML pre-rendering (yes/no)
- Lovable app URL (if applicable)
- Hosting method
- Tracking IDs (GA, GTM, Ahrefs, etc.)
- Department

If you're not sure which property Pete means, search: `ls Properties/` or `grep -rl "domain" Properties/`. Property folder names use spaces (e.g. "Sygma Solutions Website"), project folder names use hyphens (e.g. "SY-Website" parent + sub-projects like "seo" / "articles" / "ads"; the old standalone "Sygma-Solutions-Website-SEO" project was folded into SY-Website/seo on 2026-05-06).

### 0b. Read the GitHub configuration

Read `Library/processes/github-configuration.md` to get:

- The PAT for the correct GitHub account
- The clone/push URL format
- The pre-project checklist

### 0b2. Classify the property

Before any code work, explicitly identify the property's platform characteristics. Read from the property README -- never guess.

1. **Platform**: Lovable / React+Vite / Manus / Static / Other
2. If Lovable: **Pre-rendered via LovableHTML?** (yes/no)
3. If Lovable: **Database hosting?**
   - Lovable Cloud Supabase = no direct DB access, changes via Lovable prompts only
   - Own Supabase = direct access via Supabase Management API + CLI. Config: [[supabase-access-token]]
4. **Hosting**: Lovable Cloud / Vercel / Manus / Other
5. If Vercel: **Auto-deploys from GitHub push?** (yes/no)
6. **Edit method**: git-only / mixed (git + Lovable prompts)

Classification determines what can be edited directly, how to push, how to access the database. If the README doesn't have this info, ask Pete and update the README immediately.

### 0c. Ask what Pete wants to do

If Pete hasn't already said:

1. **What do you want to do today?** (SEO audit, code change, feature addition, general review, new setup, etc.)

Only ask for details the brain doesn't already have. **Never ask for information that's in the property README.**

### 0d. If the property doesn't exist in the vault yet

This is a new property. Follow the intake workflow:

1. **Agree the project name** -- ask Pete. This becomes `{project-name}` everywhere.
2. **Gather what Pete knows** -- domain (or "none yet"), tech stack, GitHub repo (or "not yet"), which GitHub account, hosting, department, Supabase (or "none"), description. Don't push for fields that don't apply -- not everything has a repo or database and that's fine.
3. **Create the vault structure**:
   - `Properties/{project-name}/README.md` (property card with whatever details we have)
   - `Projects/{project-name}/` for active project work (flat mirror of Asana)
4. **Update MAP.md** with all new entries.
5. **Remind Pete** to update [[GitHub-Repo-Property-Master.xlsx]] if appropriate.
6. **Log in daily note**.

Then continue with the rest of this workflow.

### 0e. Write session plan (dual plan mode)

FIRST action after understanding what Pete wants:

1. **Vault session plan**: Write to `Projects/{project-name}/files/session-plan-YYYY-MM-DD.md` with goal, steps, and status: in-progress. Update this plan as work progresses. This is the permanent record.
2. **Claude Code built-in plan**: Also use Claude Code's built-in plan mode for live session tracking. This is ephemeral (lives in the UI, not saved) but gives Pete a live progress view.

---

## Step 1 -- Connect to the Repo

### 1a. Look up the PAT

The PAT is stored in `Library/processes/github-configuration.md`. Pete has two master PATs (classic, no expiry, repo scope) -- one per GitHub account. Use them directly.

> [!important] Do NOT generate a new PAT per session
> Never generate a new token unless Pete tells you the stored one has been revoked.

If the property has no GitHub repo, skip to Step 2.

### 1b. Clone the repository

```bash
git clone https://<PAT>@github.com/<Account>/<repo>.git /tmp/<repo-name>
cd /tmp/<repo-name>
git status
```

The repo clone lives in `/tmp/`, not in the vault. This is a fresh working copy every session.

**The repo is the single source of truth for code.** Always clone and read live. Never work from memory of what the code "should" look like.

### 1c. Check which branch to work on

Default is `main`. If Pete wants to work on a different branch, switch to it:

```bash
git checkout <branch-name>
```

If unsure, ask Pete. Always confirm which branch before making changes.

### 1d. Know the push method

**Standard method: Normal Git CLI** (clone, edit, commit, push). This is the only method for all projects because `git diff` provides a natural pre-push checkpoint.

```bash
git clone https://<PAT>@github.com/<Account>/<repo>.git /tmp/<repo-name>
cd /tmp/<repo-name>
# ... make edits ...
git diff          # SEE what changed (Step 5)
git add <files>   # stage specific files only
git commit -m "message"
git push origin main
```

> [!warning] Never use sub-agents for pushing
> Sub-agents rewrite files from scratch instead of using the provided content. This has broken production builds multiple times.

---

## Step 2 -- Read Existing Context

### 2a. Read property context

Read the property folder to understand current state:

- `ls Properties/{property-name}/` -- see what subdirs exist (data/ for operational data)
- Read `Properties/{property-name}/README.md` for domain, tech stack, tracking IDs

### 2b. Read any active projects

Check `Projects/` for active project work related to this property (post 2026-05-06 restructure). Project structure is **parent + sub-projects direct under parent**. For website properties, look at the parent (e.g. `Projects/SY-Website/`, `Projects/CD-Website/`) AND walk the sub-projects. Actual SY-Website sub-folders: `seo/ articles/ improvements/ youtube/ ads/ backlinks/`. CD-Website: `seo/ articles/ migration/`. CD-LeakGuard: `crm/ tiered/ communities/`. CD-Other-Sites: `the-leaky-finders/ leakguard-lanzarote/ pipebusters-lanzarote/ leakbusters/`. The old standalone projects (CD-Canary-Detect-Website-SEO, SY-Solutions-Website-SEO, SY-Google-Ads, SY-Articles-and-Blogs, SY-Main-Site-Improvements, SY-YouTube, SY-Backlink-SEO, CD-Articles-and-Blogs, CD-Canary-Detect-Main-Site-Migration, CD-LeakGuard-CRM/Tiered/Communities, CD-Leakbusters-Migration) were folded into this pattern 2026-05-06 -- if you see those names referenced anywhere old, they map to a sub-project under a parent now.

---

## Step 3 -- Understand the Tech Stack Rules

Based on what the property README says, apply the right rules. Load the relevant reference file from this skill's `references/` folder for detailed guidance.

### Lovable sites

**Reference:** `references/lovable-rules.md`

Lovable produces a React SPA (React + TypeScript + Vite + Tailwind CSS + shadcn/ui + react-helmet-async + react-router-dom). Lovable commits every change directly to the GitHub repo.

**The critical rule:** Any direct edits to `src/` files will be overwritten the next time Pete makes a change in Lovable.

| Files | Safe to edit directly? | Notes |
|---|---|---|
| `index.html` | Yes | Global HTML shell, meta tags, favicons, schema |
| `public/*` (sitemap, robots.txt, manifest) | Yes | Static assets |
| `src/*` (components, pages, hooks, data) | NO | Write Lovable prompts instead |
| Config files (package.json, vite.config.ts) | NO | Goes through Lovable |

When changes are needed in `src/`, write clear Lovable prompts for Pete to paste in. One prompt per logical change. Format:

```
LOVABLE PROMPT -- [Component/file]:
[Plain English: what to change, where, why]
```

### LovableHTML pre-rendered sites

**Reference:** `references/seo-prerendered.md`

LovableHTML (lovablehtml.com) is a separate product that sits as a pre-rendering proxy in front of the site. Human visitors get the normal React SPA. Search engine and AI crawlers get fully pre-rendered static HTML.

This means: React Helmet meta tags, canonical URLs, structured data, hreflang -- all visible to crawlers. Do NOT recommend SSR migration, Next.js, or noscript fallbacks on these sites. They don't need it.

### Non-pre-rendered Lovable sites

**Reference:** `references/seo-no-prerender.md`

Same Lovable SPA, but crawlers see the empty HTML shell. SEO concerns are real on these sites. `index.html` is the most important file because it's all non-JS crawlers see. Options: add LovableHTML, improve index.html meta/schema/noscript, or accept the limitation if SEO doesn't matter for this property.

Always ask Pete whether SEO matters for this particular site before diving into SEO recommendations.

### Next.js apps (e.g. Sygma Solutions on Vercel)

All files directly editable. App Router (`src/app/`), API routes, server components, custom loaders (e.g. Cloudinary image loader). Normal git workflow. Auto-deploys on push to `main` via Vercel. For image work, check the Cloudinary naming convention in the property README.

### Non-Lovable React apps (e.g. React + Vite on Vercel)

All files are directly editable. Normal development workflow -- edit, commit, push. No Lovable prompt dance needed. Still confirm changes with Pete before committing.

### Other tech stacks

If the property README shows a stack you don't have specific rules for, apply common sense: read the code, understand the build system, make targeted edits, test where possible, confirm with Pete before committing. The clone/analyse/edit/commit/cleanup workflow still applies.

---

## Step 4 -- Do the Work

This step depends on the session goal. Some common patterns:

### SEO audit (technical only)
This covers technical SEO checks on the codebase: `index.html`, `public/robots.txt`, `public/sitemap.xml`, schema markup, meta tags, redirect chains. Load the relevant SEO reference file based on whether the site has pre-rendering or not. Route findings to `Properties/{property-name}/data/`.

For **page-level keyword and content SEO** (keyword research, Surfer NLP analysis, Ahrefs position tracking, content optimisation plans), use the **ahrefs-audit** skill instead -- not this one. Property-manager handles the technical infrastructure; ahrefs-audit handles the strategic content work.

### Code changes (direct)
For files safe to edit directly: read the file first, make targeted edits, validate (check JSON-LD is valid, tags are complete, nothing accidentally removed).

**For UI changes**: run the dev server and use Claude Code's Preview to visually verify changes during development, before committing.

### Code changes (via Lovable)
For `src/` on Lovable sites: write prompts, give them to Pete, then after Pete applies them, pull the latest and verify.

### Feature work
Discuss the approach with Pete, break into steps, work through them. If it touches `src/` on a Lovable site, write prompts. If it touches Supabase, use the Supabase Management API or CLI directly with the account-level access token from [[supabase-access-token]] -- not the MCP connector.

---

## Step 5 -- Pre-Push Verification (MANDATORY)

> [!warning] Every sub-step below is mandatory. Do not skip any. Do not say "done" until all are complete.

This step sits between making edits and pushing code. Its purpose is to catch silent failures (string replacements that didn't match, files that weren't saved, edits that landed in the wrong place) BEFORE anything reaches the remote repo.

### 5a. Run `git diff` and READ the output

```bash
cd /path/to/repo
git diff
```

For every file you edited, confirm:
- The change you intended is visible in the diff
- No unintended changes snuck in
- If a file you edited does NOT appear in the diff, the edit failed silently -- fix it before continuing

### 5b. Grep for expected strings

For every bug fix, feature, or change, grep the working copy for the specific string you expect to find:

```bash
grep -n 'expected_string' src/path/to/file.tsx
```

If the grep returns nothing, the edit did not apply. Do not proceed.

### 5c. Check for untracked files

```bash
git status
```

Confirm any new files (hooks, utilities, components) show as untracked and will be included. Confirm no files were accidentally deleted or renamed.

### 5d. Build

```bash
npm run build   # or npx vite build, or whatever the project uses
```

Build must pass with zero errors. Warnings are acceptable. Errors are not.

### 5e. Review the file list

List every file that will be committed:

```bash
git diff --stat
```

Compare this against your intended changes. Every file you meant to change should be there. No files you didn't touch should be there. If `package-lock.json` changed from `npm install` and you didn't intend to update dependencies, exclude it.

### 5f. Update session plan

Update the session plan file to record that pre-push verification passed. Include the list of files changed and what was grepped.

> [!important] Only after all six sub-steps pass do you move to Step 6.

---

## Step 6 -- Commit, Push, and Verify (MANDATORY, ONE STEP AT A TIME)

> [!important] Each logical change = one branch, one merge-to-main, one verify, one report, one STOP.
> Do not batch multiple steps. Do not skip verification. **Default flow is direct branch-to-main via ref update (no PR).** PRs are only for the rare case where Pete explicitly asks for one, or the change is so large that a written PR description adds real audit value that `git log` alone doesn't.

### 6a. Commit locally on a feature branch

If you are not already on a feature branch, create one named after the step:

```bash
git checkout -b feat/<short-description-of-step>
```

Stage specific files and commit:

```bash
git config user.email "pete.ashcroft@sygma-solutions.com"
git config user.name "<Account>"   # PortalPeteZero or SygmaSol
git add path/to/specific/file   # always specific files, never git add .
git commit -m "<clear description>

<body: what was verified, what was the root cause, what this does and doesn't do>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

**Never use `git add .` or `git add -A`.** Always stage specific files.

### 6b. Push the feature branch

```bash
git remote set-url origin https://<PAT>@github.com/<Account>/<repo>.git
git push -u origin feat/<short-description-of-step>
```

Never `git push origin main` directly -- sandbox will (correctly) block it. Push the branch first, then promote it to main via 6c.

### 6c. Promote branch to main via ref update (DEFAULT)

Fast-forward main's ref to the branch's HEAD using the GitHub Git Refs API. This is the no-PR equivalent of merging -- one clean commit lands on main, Vercel deploys, no PR chatter, no Vercel preview email.

```bash
# Get the branch's current SHA
NEW_SHA=$(curl -s -H "Authorization: token <PAT>" \
  https://api.github.com/repos/<Account>/<repo>/git/refs/heads/feat/<branch-name> \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['object']['sha'])")

# Update main's ref to point at that SHA (fast-forward only)
curl -s -X PATCH \
  -H "Authorization: token <PAT>" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/<Account>/<repo>/git/refs/heads/main \
  -d "{\"sha\": \"$NEW_SHA\", \"force\": false}" \
  | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'main now at: {d.get(\"object\",{}).get(\"sha\",\"\")[:7]}') if 'object' in d else print(f'ERROR: {d}')"

# Delete the feature branch (it's now redundant)
curl -s -o /dev/null -w "branch delete HTTP %{http_code}\n" -X DELETE \
  -H "Authorization: token <PAT>" \
  https://api.github.com/repos/<Account>/<repo>/git/refs/heads/feat/<branch-name>

# Sync local main
git checkout main && git pull origin main
git log --oneline -3
```

If the ref update returns "Update is not a fast-forward", main has diverged -- rebase the feature branch on current main, re-push, then retry 6c.

### 6d. PR route (only when explicitly requested or warranted)

Use this route ONLY when:
- Pete explicitly says "do this via a PR"
- The change is large/risky enough that the PR description becomes a useful historical record beyond what's in the commit message
- External collaboration requires a PR-based review

In those cases:

```bash
# Open PR
curl -s -X POST \
  -H "Authorization: token <PAT>" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/<Account>/<repo>/pulls \
  -d '{"title": "<one-line>", "head": "feat/<branch>", "base": "main",
       "body": "## Summary\n...\n## Why safe\n...\n## Verification\n- [x] ...\n## Test plan post-merge\n- [ ] ..."}' \
  | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'PR #{d[\"number\"]}: {d[\"html_url\"]}')"

# Merge (rebase method)
curl -s -X PUT \
  -H "Authorization: token <PAT>" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/<Account>/<repo>/pulls/<PR_NUMBER>/merge \
  -d '{"merge_method": "rebase"}' \
  | python3 -c "import sys, json; d=json.load(sys.stdin); print(f'merged={d.get(\"merged\")} sha={d.get(\"sha\",\"\")[:7]}')"
```

Note: the PR route triggers a Vercel preview build and a notification email to Pete. That's extra noise. Only use this route when the audit value is worth the noise.

### 6e. Wait for Vercel (or Lovable Cloud) to deploy

- Vercel: 1-3 minutes typically. Detect a new deploy by checking the live URL's `etag` header has changed from pre-merge value.
- Lovable Cloud: 1-2 minutes.
- LovableHTML pre-rendered sites: cached HTML may take longer to refresh. If URLs or meta changed, consider invalidating cache via `lovablehtml-cache-invalidation` process.

### 6f. Post-Merge Verification (MANDATORY)

> [!warning] This is where mistakes have been caught in the past. Never skip this.

**Fresh clone of main into a separate directory:**

```bash
cd /tmp && rm -rf verify-clone
git clone https://<PAT>@github.com/<Account>/<repo>.git verify-clone && cd verify-clone
git log --oneline -2   # confirm expected commit is HEAD
```

**Grep every changed string in the fresh clone:**

```bash
grep -n 'expected_string_1' src/path/to/file1.tsx
# ... one grep per change
```

If ANY grep returns nothing, the change did not land. Fix it with a follow-up commit. Do not tell Pete "done" with missing changes.

**Build the fresh clone:**

```bash
npm install --silent && npm run build
```

Build must pass with zero errors. If it fails, the push broke something -- fix immediately via a follow-up commit.

**Curl the live site** to confirm deployed behaviour:

```bash
curl -sI https://<domain>/<path>           # expect the right status code (200, 308, etc.)
curl -sL https://<domain>/<path> | grep -E "<expected-live-content>"   # confirm new content is live
```

**For UI changes**: run the dev server on the fresh clone and use Claude Code's Preview to visually confirm the change looks correct.

**Scripted live verification (`browser-api.py`)** — headless, repeatable proof against the deployed URL. Stronger than `curl | grep` because the page is fully rendered, and the screenshots are the visual evidence to hand Pete:

```bash
# status + title + console errors + desktop/mobile/dark screenshots + JSON (exit 1 if HTTP>=400 or a page error fired)
python3 Library/processes/scripts/browser-api.py audit https://<domain>/<path> --out /tmp/verify
# prove the changed copy is actually live (exit 1 if any string missing / any "absent" string still present)
python3 Library/processes/scripts/browser-api.py check https://<domain>/<path> --expect "<new copy>" --absent "<old copy>"
```

Complements curl + Preview, does not replace them. Config + all verbs: [[browser-api-configuration]].

### 6g. Stop-and-Report

After 6f passes, produce a plain-English report following the evidence template from the Non-Technical User Protocol:

- What was changed, in plain language
- How it was verified (fresh clone ✓, build ✓, curl live ✓, etc.)
- What Pete can do to see it working
- Commit hash and PR number as references

**Then STOP.** Wait for Pete to say "next" (or "go", or similar) before starting the next code step.

### 6h. Deployment side-effects

Remind Pete (only if relevant) to:

1. Submit in Google Search Console -- if sitemap or URLs changed
2. Test with Facebook Sharing Debugger -- if Open Graph tags changed
3. Test with Google Rich Results Test -- if schema markup changed
4. Invalidate LovableHTML cache -- if on a pre-rendered site and the content changed

---

## Step 7 -- Clean Up and Persist

### 7a. Update property README and daily note

- Update `Properties/{property-name}/README.md` with any new information discovered during the session (new tracking IDs, tech stack changes, status updates)
- Log session work in `Daily/YYYY-MM-DD.md`

### 7b. Route findings back to the vault

Follow vault-writer routing rules:

| What you found | Where it goes |
|---|---|
| SEO crawl data, audit results | `Properties/{property-name}/data/` |
| SEO page optimisation (which pages, keywords, scores, rescans) | Update the SEO Page Tracker table in `Properties/{property-name}/README.md` (see ahrefs-audit skill for format) |
| Google Ads data | `Properties/{property-name}/data/` |
| Analytics / traffic data | `Properties/{property-name}/data/` |
| Tech stack changes, new domain, new tracking ID | Update `Properties/{property-name}/README.md` |
| New GitHub repo added to property | Update property README Git Connection section + `Library/processes/github-configuration.md` |
| New Supabase project added | Update property README + `Library/processes/supabase-access-token.md` if it's a new project ref |
| Project status, decisions, specs | `Projects/{project-name}/files/` |
| Session progress | `Daily/YYYY-MM-DD.md` |

### 7c. Capture new infrastructure

If during the session Pete mentions adding something new to the property -- a repo, a database, a domain, a Vercel deployment, tracking IDs -- update the property README immediately. Don't ask permission, just save it and confirm what was recorded.

But never nag about missing fields. Some properties deliberately don't have repos, databases, or domains. Accept what's there.

### 7d. Create follow-up Asana tasks

If the session produced actionable property updates (e.g., SEO fixes, content refreshes, design changes, new repos to set up), create Asana tasks for them using `asana_create_task` with the correct project GID, assignee, and priority. Read `Library/processes/asana-configuration.md` for all IDs.

---

## Quick Reference -- Dos and Don'ts

| Do | Don't |
|---|---|
| Read the property README before starting | Ask Pete for info the vault already has |
| Clone the repo fresh every session | Work from memory of the code |
| Check tech stack rules before editing | Edit `src/` directly on a Lovable site |
| Write Lovable prompts for `src/` changes | Commit `src/` changes that will be overwritten |
| Set remote URL with PAT before every push | Push without the PAT in the URL |
| Use normal Git CLI for all pushes | Use sub-agents for pushing (they rewrite files) |
| Use `vercel-api.py` for deployment checks | Rely on Vercel MCP connector |
| Classify the platform before any code work | Guess the tech stack or edit method |
| Use Preview for UI changes before committing | Rely only on grep for visual correctness |
| Update the session plan after every step | Batch plan updates or "update later" |
| Stage specific files only | Use `git add .` or `git add -A` |
| Confirm with Pete before committing | Auto-commit without showing what's changing |
| Update property README and daily note at session end | Leave the session with no record |
| Route findings to the right vault location | Dump everything in the project README |
| Capture new repos/databases when mentioned | Nag about missing repos/databases |
| Run `git diff` and READ it before committing | Trust that edits applied without checking the diff |
| Grep for expected strings before AND after push | Assume str.replace/sed worked because it didn't error |
| Fresh-clone and build after every push | Tell Pete "all done" without verifying the remote |
| Accept the property's limitations | Push for LovableHTML/SSR if Pete hasn't asked |

---

## Pete's Preferences

- Human, natural tone (not corporate, not AI-sounding)
- British English spelling
- No unnecessary jargon
- Always ask clarifying questions rather than guessing
- For outbound text drafted during a run (rare in this skill, but e.g. customer-facing copy on a marketing site), see `[[voice-principles]]`

---

## Verification Claims Checklist (hard gate before saying "done")

Before writing any message to Pete that claims a step is complete, the assistant MUST be able to answer YES to every item below. If any answer is NO, the step is not done. Keep working.

**Source state**
- [ ] Have I confirmed the expected commit is the HEAD of `main` on GitHub (via fresh clone, not local reflog)?
- [ ] Does `git status` in the fresh clone show a clean working tree on main?

**Code state**
- [ ] Have I grepped the fresh clone for every string I intended to add or remove, and got the expected matches (not from local working copy)?
- [ ] Did `npm run build` (or the project's build command) complete with zero errors in the fresh clone?

**Live state (for deployed changes)**
- [ ] Have I curled the live URL(s) and confirmed the response matches expected behaviour (status code, redirect target, content substring, schema)?
- [ ] For UI changes, have I used Preview, `browser-api.py audit`, or screenshots to visually confirm the change renders correctly?
- [ ] Has the Vercel/Lovable deploy actually finished (etag changed, age header low, or API status = READY)?

**Plan + memory state**
- [ ] Have I ticked the checkboxes in the plan file corresponding to this step, with commit hash and verification evidence recorded?
- [ ] Have I updated the daily note if the session is meaningful enough?
- [ ] If any NEW lesson came out of this step, have I saved it as a memory (feedback/project/reference) and indexed it in MEMORY.md?

**Report state**
- [ ] Is my report written in plain English that Pete can understand without reading code?
- [ ] Does my report include concrete evidence Pete can verify himself (e.g. "reload this URL and you'll see X")?
- [ ] Have I clearly stated STOP and that I'm waiting for Pete's next instruction?

Only after every box is ticked is the step done. "I believe it worked" is not acceptable. "The grep returned the expected match and the curl returned 200" is.

---

## Safeguards Based on Past Failures

These are specific lessons from incidents where this skill was followed incompletely or where a check was skipped. Each bullet addresses a real past failure. Do not skip any.

- **Always `git status` and `git log --oneline -5` FIRST.** Edits from previous sessions can be sitting uncommitted on disk. This caught us when `CustomerLogin.tsx` had been edited in a previous session and never committed -- every test run afterwards used the broken version.
- **Read the console before guessing at root cause.** If a user reports a login or network error, ask for a DevTools console screenshot before writing any fix. The console shows the actual bundle being served, the actual URLs being called, and the actual errors. All other debugging without this is guesswork. For a live/public page you can capture it headlessly yourself: `python3 Library/processes/scripts/browser-api.py console <url>` (console + page errors + failed requests). See [[browser-api-configuration]].
- **Pre-cutover audits must be technical, not just structural.** Before declaring any site migration "ready for cutover", run a full Ahrefs / Lighthouse / schema validator pass on the staging environment. Validating only redirect coverage and sitemap is not enough. Schema.org validation, OG tag completeness, meta description length, alt text, internal-link redirect chains, and structured data eligibility for rich results must all be checked BEFORE going live.
- **Never use `replace_all` on redirect destinations.** `replace_all` can corrupt entries where the old destination is also a source URL, creating self-referential redirect loops. Grep first, inspect each match individually, edit one at a time.
- **Stale service workers survive domain migrations.** When a domain moves hosts, any SW registered by the old host stays registered in visitors' browsers indefinitely. Deploy a poison-pill `public/sw.js` that self-destructs on install to clear them. This is the only reliable way.
- **Update the plan after every step, immediately.** Tick checkboxes in the same tool-call batch as the underlying work where possible. Never batch plan updates. Never "I'll update later". The original LeakGuard migration plan ran to ~70% completion with zero ticked checkboxes -- a reconciliation plan had to be written from scratch because nobody could tell what was done.
- **Connect to the repo instead of guessing.** Before writing any migration plan, audit, or analysis of a web property, clone the GitHub repo fresh and read the actual code. The repo is the source of truth -- not memory, not the property README, not a quick look at the live site. Fetching the live site with curl is a secondary check -- it is not a substitute for reading the source.
- **Just fix it, don't task it.** When a fix is small, obvious, and within the current session's scope, do it directly. Don't ceremoniously create an Asana task for "Fix X" and move on -- that's just paperwork. Tasks are for work that has to wait.
- **Never skip steps of this skill.** If a step feels tedious, that is a signal to do it, not to skip it. Every step here was added because a failure happened when it was skipped.

---

*Skill version: 2.6 -- 20 April 2026 (later). v2.6: Step 6 default flow changed from PR-via-API to **branch-push-then-ref-update-to-main** (no PR, no Vercel preview, no email noise). PRs are now the exception, reserved for large changes or explicit Pete request. Fixes v2.5's over-bureaucratic default -- for a non-technical user where the assistant owns verification, the PR adds ceremony without review value.*

*Skill version: 2.5 -- 20 April 2026. v2.5: Added Non-Technical User Protocol (Pete cannot review code, assistant owns verification, plain-English evidence required). Added Per-Step Stop-and-Check protocol (one change, one merge, one verify, one stop -- no batching). Step 6 originally set PR-via-API as default -- superseded by v2.6. Added Verification Claims Checklist as a hard gate before claiming done. Added Safeguards section capturing this week's incidents (git status first, read console first, technical pre-cutover audits, no replace_all on redirects, stale service workers, plan updates per step, source-of-truth is the repo, just fix it, never skip steps).*

*v2.4 -- 18 April 2026: Supabase MCP replaced with direct API + CLI. SEO audit clarified as technical-only, defers to ahrefs-audit for content SEO. Next.js stack section added. Path conventions fixed. analytics/ subfolder removed (use data/). Git config user.name reads from github-configuration.*

## Related lessons (auto-surfaced by deployment matrix)

Lessons in scope for this skill per [[Library/audits/2026-05-16-lesson-deployment-matrix]]:

- [[Library/lessons/2026-05-13-migration-redirect-prefix-exclusion-bug]]
- [[Library/lessons/2026-05-14-nextjs-instrumentation-must-be-in-src]]
- [[Library/lessons/2026-05-16-supabase-concurrent-component-row-creation]]
- [[Library/lessons/2026-05-06-google-ads-pre-filled-mccid-trap]]
- [[Library/lessons/2026-05-07-server-side-mp-paid-attribution-gap]]
- [[Library/lessons/2026-05-21-mergin-server-file-whitelist-and-input-app]] — Mergin server file-type whitelist, `.mergin-ignore` timing, Mergin Maps Input is the right tablet app (not QField)
- [[Library/lessons/2026-05-21-monitoring-alerts-anchor-on-real-device-timing]] — Anchor delta checks on chronological reading order (`dtSeconds > 0`); silent/offline thresholds on the device's actual configurable transmit interval, not a flat constant
- [[Library/lessons/2026-05-22-dont-swallow-conflict-then-insert]] — when applying a code change, if the surrounding context has drifted, fix the conflict before insert; don't silently swallow it.
- [[Library/lessons/2026-05-22-new-pages-generic-images-crop-in-fixed-slots]] — generic stock-style images crop predictably in fixed slots; brief-specific images must be hand-cropped before commit.
- [[Library/lessons/2026-06-01-cloudflare-worker-audit-bare-path-not-cache-buster]] — audit Worker output against the bare canonical URL, not a `?_cb=` cache-buster (CF can serve the variant without invoking the Worker → false negatives).
- [[Library/lessons/2026-06-05-no-chip-done-without-chrome-side-by-side]] — no "done" on any Pete-site chip/page without a Chrome screenshot pass (desktop 1280×800 + mobile 390×844) vs the live source; grep+build+200 are necessary, not sufficient.
- [[Library/lessons/2026-06-07-vercel-retired-apex-ip]] — Vercel decommissioned legacy apex IP `76.76.21.21` (`216.198.79.1` also dead); pull current apex IPs from the live Vercel API, never from notes.
- [[Library/lessons/2026-05-22-sygma-new-toplevel-page-catchall-redirect-allowlist]] — new top-level Sygma pages must be added to the catch-all redirect allowlist or the catch-all blackholes them.
- [[Library/lessons/2026-05-24-built-page-must-be-linked-not-orphaned]] — building a page is half the job; if no nav/footer/internal link points at it, it's invisible to users + search engines.
- [[Library/lessons/2026-05-29-supabase-management-api-write-access]] — when writing to a Supabase project: service_role + PostgREST, NOT the Management `/database/query` endpoint. Also: Cloudflare WAF blocks Python-urllib UA on `api.supabase.com`, set a browser UA.
- [[Library/lessons/2026-05-29-wp-xmlrpc-writes-yoast-meta-bypass-rest]] — when WordPress REST API rejects Yoast meta-key writes, XML-RPC `wp.editPost` writes the same meta-keys directly without the REST restriction.

