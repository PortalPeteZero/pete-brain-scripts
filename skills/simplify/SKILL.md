---
name: simplify
description: >
  Multi-agent code review and simplification for web projects. Spawns three parallel review agents
  that check your code for reuse opportunities, quality issues, and efficiency improvements, then
  reports findings and applies fixes. Use this skill whenever the user says "simplify", "review my code",
  "clean up this code", "check code quality", "refactor", "optimise my code", "code review",
  "check for issues", "tidy up the code", "improve this code", or any request to review, simplify,
  or improve code quality in a web project. Also trigger when the user pastes code and asks for
  improvements, or after a coding session when they want a final quality pass. Works with HTML, CSS,
  JavaScript, TypeScript, React, and other web technologies.
---

# Simplify — Multi-Agent Code Review & Fix

> [!important] Where things go
> Property repo / tech stack / account → the **CC Properties module** (`drive_files` for files). Review findings + session plan → **`vault_notes`** (ingest a `.md`). Session log → CC `daily_log`. Tools run from `/tmp/pbs`.

Three parallel review agents (reuse opportunities, quality issues, efficiency improvements) review code, aggregate findings, then apply fixes. Style rules for outbound communications live in [[voice-principles]] only — commit messages, PR descriptions, README writes, and audit reports are internal artefacts and not subject to those rules.

Version history: [[CHANGELOG]].

> **This skill runs in Claude Code.** If triggered in Cowork, stop and tell Pete.

You are running the Simplify skill. This skill reviews code using three parallel review agents, each focused on a different quality dimension. After all agents report back, you aggregate the findings into a clear report and then apply the fixes.

## How It Works

The review process has four phases:

1. **Connect** - Find the code, clone the repo if needed, understand the tech stack
2. **Parallel Review** - Three agents run simultaneously, each examining the code from a different angle
3. **Report** - Findings are aggregated into a single clear report, grouped by severity
4. **Fix & Clean Up** - Approved fixes are applied, findings are saved to the CC knowledge base (`vault_notes`)

## Phase 1: Connect to the Code

### If working on one of Pete's properties

Get the repo, tech stack, and account from the **CC Properties module** (`drive_files` for any files). The GitHub PAT: [[github-configuration]].

**Pre-step: Write session plan**

Before starting the review, write a session plan to `vault_notes` (ingest a `.md`) with goal, steps, and `status: in-progress`. Update it as you work through each phase.

Clone the repo:

```bash
git clone https://<PAT>@github.com/<Account>/<repo>.git /tmp/<repo-name>
cd /tmp/<repo-name>
git status
```

**The repo is the single source of truth.** Always clone fresh -- never work from cached files or memory.

**Platform classification**: Before any code work, read the property README and classify using the property-manager skill's decision tree (Step 0b2). This determines what can be edited directly and how to push.

> [!important] Do NOT generate a new PAT per session
> Pete has two permanent master PATs (classic, no expiry, repo scope) -- one per GitHub account. Use them directly.

Check the tech stack from the property README. If it's a Lovable site, remember:
- `src/` files are Lovable territory -- fixes here need Lovable prompts, not direct edits
- `index.html` and `public/*` are safe to edit directly
- See the property-manager skill's Lovable rules for details

### If the user points at specific files or pastes code

Use those directly. Save pasted code to a temp file for the agents to work with.

### If unclear

Ask which files or folder to review.

### Scope check

If the codebase is large (more than 10-15 files), ask the user to narrow the scope before spawning agents. Good scoping questions: "Want me to focus on the pages, the components, the API layer, or everything?" or "Any specific files you're worried about?"

Gather the list of file paths to review. Read each file so you understand the codebase before spawning agents.

## Phase 2: Spawn Three Review Agents

Launch all three agents in parallel using the Agent tool. Each agent gets the same list of files but a different review focus.

### Agent 1: Code Reuse

```
You are a code reuse reviewer. Your job is to find opportunities to reduce duplication and improve reuse.

Review the following files: [LIST FILES]

Look for:
- Duplicated logic across files or components (even if not identical, look for similar patterns)
- Utility functions that could be extracted and shared
- Repeated styling patterns that could become shared classes or styled components
- Constants or config values hardcoded in multiple places
- Components that are nearly identical and could be merged with props
- Copy-pasted API calls that could use a shared service layer
- Repeated error handling patterns that could be centralised

For each finding, report:
- SEVERITY: low / medium / high
- FILE: which file(s)
- LINE(S): approximate line numbers
- ISSUE: what the duplication or reuse opportunity is
- SUGGESTION: how to fix it, with a brief code example if helpful

Be practical. Only flag things where the reuse would genuinely reduce maintenance burden or improve clarity. Do not flag things that happen to look similar but serve different purposes.
```

### Agent 2: Code Quality

```
You are a code quality reviewer. Your job is to find bugs, bad practices, and maintainability issues.

Review the following files: [LIST FILES]

Look for:
- Unused imports, variables, or dead code
- Missing error handling (uncaught promises, missing try/catch, no error boundaries)
- Type safety issues (implicit any, missing null checks, unsafe type assertions)
- Accessibility problems (missing aria labels, no alt text, poor semantic HTML)
- Security concerns (XSS vectors, unsanitised inputs, exposed secrets)
- Overly complex conditionals or deeply nested logic
- Missing or misleading comments
- Inconsistent naming conventions
- React-specific: missing keys in lists, missing dependency arrays in hooks, prop drilling that should use context
- State management issues (unnecessary re-renders, derived state stored as state)

For each finding, report:
- SEVERITY: low / medium / high (high = bugs or security issues)
- FILE: which file(s)
- LINE(S): approximate line numbers
- ISSUE: what the problem is
- SUGGESTION: how to fix it, with a brief code example if helpful

Focus on things that matter. Stylistic nitpicks are low priority unless they hurt readability.
```

### Agent 3: Efficiency

```
You are a performance and efficiency reviewer. Your job is to find opportunities to make the code faster, leaner, and more efficient.

Review the following files: [LIST FILES]

Look for:
- Unnecessary re-renders in React components (missing memo, useMemo, useCallback where beneficial)
- Large bundle impact (importing entire libraries when only one function is needed)
- Expensive operations in render paths or hot loops
- Missing loading states or lazy loading opportunities
- Unoptimised images or assets referenced in code
- API calls that could be batched, cached, or debounced
- Redundant data transformations (mapping/filtering the same array multiple times)
- CSS that could be simplified (over-specific selectors, unused styles, layout thrashing)
- Opportunities to use more efficient data structures
- Network waterfalls that could be parallelised

For each finding, report:
- SEVERITY: low / medium / high
- FILE: which file(s)
- LINE(S): approximate line numbers
- ISSUE: what the inefficiency is
- SUGGESTION: how to fix it, with a brief code example if helpful

Be realistic about impact. Micro-optimisations that save nanoseconds are not worth flagging. Focus on things that would make a noticeable difference to load time, responsiveness, or maintainability.
```

## Phase 3: Aggregate and Report

Once all three agents have returned their findings, combine them into a single report. Structure it like this:

### Report Format

Group findings by severity (high first), then by file. Present it conversationally, not as a wall of bullet points. Something like:

**Start with a quick summary sentence**, e.g. "I found 12 issues across 5 files. Three are high severity and worth fixing straight away."

Then walk through the high-severity findings first, explaining each one clearly. Then medium, then low. For each finding, include which agent found it (Reuse / Quality / Efficiency) so the user knows what lens it came from.

If multiple agents flagged related issues in the same area of code, merge them into one finding rather than repeating.

### Present the report to the user

Show the full report and ask: "Want me to go ahead and apply these fixes?"

The user may want all fixes, or only some. Wait for their response before proceeding.

## Phase 4: Apply Fixes and Clean Up

### 4a. Apply fixes

Once the user confirms, apply the fixes. Work through them file by file, highest severity first.

**For files safe to edit directly** (all files on non-Lovable sites, or `index.html`/`public/*` on Lovable sites): edit in the cloned repo using the Edit tool.

**For `src/` files on Lovable sites**: write Lovable prompts for Pete to paste in. Format:

```
LOVABLE PROMPT -- [Component/file]:
[Plain English: what to change, where, why]
```

After applying all direct fixes:
1. Read each modified file back to verify the edits look correct
2. **For UI changes**: run the dev server and use Preview to visually verify
3. If the project has a linter or build command available, run it to check nothing is broken
4. Give the user a short summary of what was changed

### 4b. Commit and push (if working from a cloned repo)

```bash
git config user.email "pete.ashcroft@sygma-solutions.com"
git config user.name "PortalPeteZero"
git add path/to/specific/file   # always specific files, never git add .
git commit -m "Simplify: [brief description of fixes applied]"

# Set remote URL with PAT before pushing
git remote set-url origin https://<PAT>@github.com/<Account>/<repo>.git
git push origin main
```

Always confirm with Pete before pushing. Show a summary of changes first.

**Verification**: Follow property-manager Steps 5-6 for mandatory pre-push (git diff, grep, build) and post-push (fresh-clone, grep, build, Preview for UI) verification.

Update the session plan after pushing with commit hash and verification results.

### 4c. Save findings

Route the review results to the right places:

| What | Where |
|------|-------|
| Full review report | `vault_notes` (ingest a `.md`) |
| Session progress | CC `daily_log` (`cron_name='session'`) |
| Commit details | Note on the property's CC card if significant changes were made |

If the review uncovered something that should be tracked as a task (e.g. "needs a bigger refactor later"), create a task in the CC task store (`public.tasks`). Insert via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py` (the date is the switch — leave `due_on` NULL for P1–P4 and set `base_priority` to the same tier; a date auto-makes it a PD): `INSERT INTO tasks (id, name, priority, base_priority, due_on, entity_slug, project_slug, status, source, notes) VALUES (gen_random_uuid(), '<name>', '<P1|P2|P3|P4 — undated>', '<same P-tier>', NULL, '<entity>', '<project_slug NAME>', 'todo', 'claude', '<notes>');`

### 4d. Suggest vault-writer

At the end, offer: "Want me to run the vault-writer skill to make sure everything from this review is properly captured?"

### 4e. Create follow-up CC tasks

If the review identified issues that require follow-up work (e.g. major refactor, performance optimization, dependency updates), create tasks in the CC task store (`public.tasks`). Insert via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py`:

```sql
-- The date is the switch (2026-07): leave due_on NULL for P1–P4 (undated). A date auto-makes a PD.
INSERT INTO tasks (id, name, priority, base_priority, due_on, entity_slug, project_slug, status, source, notes)
VALUES (gen_random_uuid(), '<name>', '<P1|P2|P3|P4 — undated>', '<same P-tier>', NULL,
        '<entity: Sygma | Canary Detect | One System>', '<project_slug NAME, not a GID>',
        'todo', 'claude', '<notes>');
```

Use the project_slug NAME (e.g. `SY-Website`, `PA-Command-Centre`); entity follows the prefix (`SY-`/`Team-` → Sygma, `CD-` → Canary Detect, `OS-` → One System, `PA-` → Personal).

## Important Notes

- Never apply fixes without showing the report first and getting confirmation
- If you cannot spawn parallel agents (e.g. environment limitation), run the three reviews sequentially -- the skill still works, just slower
- Keep agent prompts focused. Do not add extra instructions beyond what is specified above
- The agents should read the actual files, not work from summaries
- On Lovable sites, never directly edit `src/` files -- write Lovable prompts instead

## Pete's Preferences

- Human, natural tone (not corporate, not AI-sounding)
- British English spelling
- No unnecessary jargon
- For outbound text drafted during a run (rare here, since simplify produces commits/PRs not customer comms), see `[[voice-principles]]`

---

