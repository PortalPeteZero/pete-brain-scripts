---
name: daily-briefing
description: Daily morning briefing email at 07:30 Atlantic/Canary. Order (2026-06-06): yesterday's PF lesson, ACTIONS TRAY (lead operational section, fresh off the 07:15 sync cron, itemised oldest-first with >3d aging flags + "say actions" walker hook), due-today tasks + overdue counts, calendar, Garmin recovery headline, GA4 snapshot. Moved 06:01 → 07:30 on 2026-05-25 so Garmin's 07:00 cron lands first.
---

## Execution -- READ THIS FIRST

This task runs script invocations via Desktop Commander, NOT workspace bash. Workspace bash has a 45s sandbox cap that silently truncates longer runs; Desktop Commander runs natively from the host with no cap.

For each `python3 ...` call below, use this pattern:

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "<absolute_path>" [args] > /tmp/<taskid>.log 2>&1 & echo "PID=$!"
  timeout_ms: 5000
```

Then poll `ps -p $PID` until exit, then read the log for output. Reference: [[Library/lessons/2026-05-02-scheduled-task-skill-md-uses-dc]].

---

# Daily Morning Briefing

Automated daily briefing sent to Pete at 6:01 AM Atlantic/Canary time. Combines vault context, Calendar, Gmail, CC tasks (`public.tasks` — Pete is off Asana), GA4, recent activity, and active projects into a styled HTML email sent via the Gmail API helper.

## VAULT ACCESS

The vault is mounted as the working folder. All paths are vault-relative. Use Read/Write/Edit/Glob tools directly.

Key sources:
- `Daily/` (yesterday's note for recap)
- CC task store `public.tasks` (Pete's tasks — read via `cc-sql.py`; Asana is retired for Pete)
- `Projects/**/README.md` (project status; Glob with `path: Projects/`, pattern `**/README.md`)
- `Properties/**/README.md` (property reference data including GA4 IDs)

## CONNECTOR ROUTING (canonical)

| Action | Tool |
|--------|------|
| Send email | Gmail API helper: `python3 Library/processes/scripts/gmail-api.py send "to" "subject" "body" --html` |
| Search Gmail | `python3 Library/processes/scripts/gmail-api.py search "QUERY"` returns thread JSON |
| Calendar events | `python3 Library/processes/scripts/calendar-api.py events primary YYYY-MM-DD YYYY-MM-DD` |
| Tasks | CC task store `public.tasks` via `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py` (Pete is off Asana — the `daily-briefing.py` cron already reads `public.tasks`) |
| GA4 metrics | Direct GA4 Data API via service account at `Library/processes/secrets/google-seo-service-account.json` (config in `Library/processes/google-api-credentials.md`) |

Migrated 2026-04-24 from Zapier Gmail + Gmail MCP + Calendar MCP to direct helpers. Both Gmail and Calendar use the same service account DWD path; always available, no on-demand enabling.

## BRIEFING SECTIONS (build in this order)

### 1. Header
"Good morning Pete" + today's date in Atlantic/Canary.

### 2. Yesterday's PF Lesson (TOP OF BRIEFING — framing for the day)

Read `Personal/passion-fit/journal/{yesterday-YYYY-MM-DD}.md`. Grep for the `## One lesson for tomorrow` heading. Extract the lesson text on the lines that follow (cut at the next `## ` or end-of-file).

Render under a section heading **"Lesson from yesterday"** with the extracted text as a blockquote. The lesson is the day's framing — present it before any operational content.

If the file is missing OR the heading is absent OR the extracted text is empty: **skip this section silently**. No "no lesson today" placeholder, no nag. The 6pm `pf-journal-reminder` cron is the only nagger for missing journal entries.

Same extraction logic the `pf-journal-reminder` cron already uses (see `~/Documents/Claude/Scheduled/pf-journal-reminder/SKILL.md` or the canonical script in [[Library/processes/pf-journal#cron-execution-script--dc-instructions]]). Source of truth = the journal entry's heading; do not maintain a parallel store.

### 3. ACTIONS TRAY (lead operational section — added 2026-06-06, Pete: "focus a bit more on my actions")

The 07:15 `daily-asana-gmail-sync` cron reconciles the tray BEFORE this briefing, so the data is fresh. Query Gmail LIVE via the helper: `python3 Library/processes/scripts/gmail-api.py search "label:Actions"` (via DC). The tray is reply-shaped only (Actions = waiting on Pete to respond by email; bills/work items are Asana-only and never appear here).

Render as the FIRST operational section, headed **"ACTIONS TRAY (N — M aging)"**:

- One line per item, **oldest last-message first**: `{n}. {who} — {what, from the subject/linked task name}` with age flag ` ⚠ {X}d` for anything whose last message is older than 3 days.
- Item count cap 10; overflow line `+{K} more in tray`.
- Multi-thread tasks (same CC task linked to several threads) = ONE line.
- Tray empty → render the section as the single line "Tray clear." (Pete should still see it's clear.)
- Close the section with: *Say "actions" in Cowork to walk these with drafts ready.*

### 4. Priority Tasks (CC `public.tasks`)
Query the CC task store (Pete is off Asana): `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT name, priority, project_slug, due_on FROM tasks WHERE status='todo' ORDER BY due_on"`. Render in two parts:
- **DUE TODAY** — itemised: title, priority, project (all priorities, not just P1/P2).
- **Overdue counts** — one line: `Overdue: {n} P1 / {n} P2 / {n} P3` (no itemisation; the tray + due-today carry the focus).
If nothing due today: "No tasks due today." then the overdue line.

### 5. Calendar Today
Run `python3 Library/processes/scripts/calendar-api.py events primary {today} {today}` (replace {today} with today's date YYYY-MM-DD). Parse the JSON for events. Show time, title, location. If no events: "Clear calendar today."

### 6. Garmin Recovery Headline

The 07:00 `garmin-daily-pull` cron writes `Personal/health/garmin/{today-YYYY-MM-DD}.md` BEFORE this 07:30 briefing fires. Read that file's frontmatter (or the headline-text block under `## Recovery`) and render:

- **Sleep**: `{sleep_score} {qualifier}` ({sleep_total_min as Hh Mm})
- **HRV**: last-night {hrv_last_night} ({hrv_status})
- **Training readiness**: {training_readiness_score} ({training_readiness_level})
- **Body battery**: +{body_battery_charged} / -{body_battery_drained}
- **Sign-off last night**: ~HH:MM (estimate) — pulled from `signoff.detected_iso` or `signoff.confirmed` if set
- **Today's activities so far**: read from the `activities` list in the file (typically empty at 07:30, will populate during the day)

If the file is missing or stale (date doesn't match today), include a one-liner "Garmin recovery unavailable this run (pulled at 07:00)" and continue. Do not skip the rest of the briefing.

The PF lesson (Section 2) is the day's BEHAVIOURAL framing; this is the PHYSICAL one.

### 7. GA4 Yesterday Snapshot
Pull yesterday's metrics via direct GA4 Data API (service account at `Library/processes/secrets/google-seo-service-account.json`). Pull for **BOTH** tracked GA4 properties (don't iterate — name them explicitly):

1. **Sygma Solutions** — property `354127076`, measurement ID `G-QVFF0DPG6X`. Config: [[Properties/Sygma Solutions Website#tracking--integrations]].
2. **Canary Detect** — property `537126447`, measurement ID `G-L31BXZTDXX`. Config: [[Properties/Canary Detect Main Website]]. **Provisioned 2026-05-11**; data starts flowing 11 May evening post-cutover. If returned data is empty (e.g. before midnight UTC of the first full day), include the property line anyway with `(no data yet)` so Pete sees we're tracking.

For each property, render: sessions, users, top 3 pages by sessions, conversion events. Keep to 8-10 lines total (4-5 per property). Lead with Sygma (the longer-tail history), then CD.

If GA4 fails for one property only, render the other normally and include "Canary Detect: snapshot unavailable this run" (or vice versa). If both fail, render a one-line skip note ("GA4 snapshot unavailable this run.") and continue.

> [!info] Sections 7-9 removed 2026-05-25 evening; Actions Tray added + sections reordered 2026-06-06
> Three sections were dropped from the briefing email on Pete's instruction: **Unread Emails**, **Yesterday's Wins**, **Active Projects**. They cluttered the email without adding durable value. Briefing ends at the GA4 section. 2026-06-06: **Actions Tray** became the lead operational section (Pete: "it should focus a bit more on my actions") and the order is now PF lesson → Tray → Due-today tasks → Calendar → Garmin → GA4.

## EMAIL STYLING

- White background, dark text
- Section headers in blue (`#2563eb`)
- Priorities colour-coded (P1 red, P2 amber)
- Compact, mobile-friendly tables, no complex CSS grid
- No em dashes. No double dashes. Replace with full stops, commas, parentheses, or colons. Both are 100% associated with AI-written text and must be eliminated from output.

## SEND

`python3 Library/processes/scripts/gmail-api.py send pete.ashcroft@sygma-solutions.com "Morning Briefing, {Day} {Date}" "{html_body}" --html`

## DAILY NOTE UPDATE

After send:
1. Read `Daily/{today-YYYY-MM-DD}.md`
2. If missing, Write it with frontmatter:
```
---
type: daily
date: YYYY-MM-DD
tags: [daily]
---
```
3. Edit-append:
```
## Daily Briefing (Automated)
- Sent at {time}
- PF lesson: {present|absent}
- Actions tray: {N} ({M} aging >3d)
- Garmin: {sleep-score} sleep / {readiness-level} readiness / signed off ~HH:MM
- Calendar events: {count}
- Priority tasks: {due-today count} due today, {overdue counts}
- GA4 snapshot: {summary}
```

IMPORTANT: Read daily note BEFORE appending. Other scheduled tasks may have written to it.

4. Command Centre publish (added 2026-06-11, P5): after the email is sent, publish the SAME briefing as a snapshot so it lives at commandcentre.info/m/morning-brief (day-by-day history). Run via Desktop Commander:
```
python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/cc_publish.py" morning-brief {TODAY-YYYY-MM-DD} '{"subject": "<the email subject>", "html": "<the full email HTML, JSON-escaped>"}'
```
Easier pattern: write the HTML to /tmp/briefing.html first, then:
```
python3 -c "import json,sys,importlib.util; spec=importlib.util.spec_from_file_location('cc','/Users/peterashcroft/Second Brain/Library/processes/scripts/cc_publish.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); m.publish('morning-brief', '{TODAY}', {'subject': sys.argv[1], 'html': open('/tmp/briefing.html').read()})" "<subject>"
```
Non-fatal: if the publish fails, note "CC publish failed" in the daily-note line and continue. The email is unchanged and remains the notification channel.

## ERROR HANDLING

- If a section fails, include "Section unavailable, {reason}" inline; continue.
- Always send the email even with empty sections.
- Always update the daily note with what happened.

## ANTI-PATTERNS

- NEVER use Windsor.ai for GA4 (Google Ads only)
- NEVER fail the whole briefing because one section is unavailable
- NEVER send to anyone other than pete.ashcroft@sygma-solutions.com
- NEVER use em dashes. NEVER use double dashes. Replace with full stops, commas, parentheses, or colons. Both are 100% associated with AI-written text.
- NEVER skip the daily note update
- NEVER reference old MCP IDs `099d2726` (Gmail) or `9854eedd` (Calendar). Superseded by helpers.
- NEVER reference Zapier `gmail_send_email` for delivery. Use the gmail-api.py helper.