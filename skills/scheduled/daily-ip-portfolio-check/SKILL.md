---
name: daily-ip-portfolio-check
description: DECOMMISSIONED 2026-06-06 — CD-IP-Trademark-Portfolio project archived (Hamilton campaign full-folded 26 May). IP register now lives at Library/ip-trademark/ip-portfolio-register.md. Do not re-enable unless a new filing needs monitoring; delete this row in the Cowork UI when convenient.
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

# IP Portfolio Daily Check

> DECOMMISSIONED 2026-06-06 — CD-IP-Trademark-Portfolio archived; Hamilton campaign full-folded 26 May 2026. Cron disabled. IP register: Library/ip-trademark/ip-portfolio-register.md. If this fires anyway, do nothing and exit.

Daily live check of OEPM (Spanish patent office) and EUIPO (EU IP office) trademark filings for Pete's portfolio + competitor Hamilton's portfolio. Scans Gmail for IP-related emails. Compares against previous report. Saves .md report. Creates Asana tasks for urgent items. Emails summary to Pete.

## VAULT ACCESS

Vault is mounted as working folder. Read/Write/Edit/Glob tools, vault-relative paths.

Key paths:
- `Library/ip-trademark/reports/` (reports)
- `Library/processes/asana-configuration.md` (Asana IDs)
- `Projects/CD-IP-Trademark-Portfolio/README.md` (project state)
- `Library/processes/scripts/oepm-bopi-check.py` (OEPM BOPI checker script)

## CONNECTOR ROUTING (canonical)

| Action | Tool |
|--------|------|
| Send email | `python3 Library/processes/scripts/gmail-api.py send "to" "subject" "body" --html` |
| Draft email | `python3 Library/processes/scripts/gmail-api.py draft "to" "subject" "body"` |
| Search Gmail | `python3 Library/processes/scripts/gmail-api.py search "QUERY"` returns thread JSON |
| Asana | Local MCP (`mcp__asana__asana_*`), personal API token, never expires |
| EUIPO lookups | `WebFetch` against EUIPO REST API (`https://euipo.europa.eu/copla/trademark/data/{applicationNumber}`) |
| OEPM lookups | `python3 Library/processes/scripts/oepm-bopi-check.py` via bash. BOPI buscadorAnotaciones is a server-rendered Struts app, works with curl + cookies. |

Migrated 2026-04-24 from Zapier Gmail + Gmail MCP to direct helper. Helper uses service account DWD, always available.
Migrated 2026-04-27 from WebSearch (unreliable, OEPM is SPA) to BOPI buscadorAnotaciones (server-rendered, reliable). Deployed to canonical 2026-05-03.

## APPLICANT ENTITIES

- LEAKBUSTERS EUIPO 019307377: applicant is Peter Ashcroft (personal filing).
- All other marks: applicant is Camello Blanco SL.

## PETE'S EUIPO PORTFOLIO (4 marks)

| Mark | App No. | Applicant | Classes | Status / Key date |
|------|---------|-----------|---------|-------------------|
| LEAKBUSTERS | 019307377 | Peter Ashcroft | 37 | Opposition closes 28 Apr 2026 |
| THE LEAKY FINDERS | 019307990 | Camello Blanco | 37, 42 | Opposition closes 29 Apr 2026 |
| CANARY DETECT | 019307987 | Camello Blanco | 37, 42 | Opposition closes 03 May 2026 |
| LEAKGUARD | 019334657 | Camello Blanco | 37, 42 | Under examination |

## PETE'S OEPM PORTFOLIO (6 marks)

| Mark | Type | Number |
|------|------|--------|
| LEAKBUSTERS | Trade Name | N 0495623 |
| LEAKBUSTERS | Trade Mark | M 4360295 |
| CANARY DETECT | Trade Name | N 0495644 |
| CANARY DETECT | Trade Mark | M 4359094 |
| THE LEAKY FINDERS | Trade Mark | M 4359099 |
| LEAKGUARD | Trade Mark | M 4370471 |

## HAMILTON'S OEPM PORTFOLIO (3 marks, competitor monitoring)

| Mark | Type | Number |
|------|------|--------|
| Canary Leakbusters | Trade Mark | M 4359523 |
| Lanzarote Leakbusters | Trade Mark | M 4359528 |
| Pipebusters | Trade Mark | M 4359531 |

Also run one EUIPO search per run for any Hamilton LEAKBUSTERS filing. CRITICAL: Do NOT cache Hamilton's EUIPO numbers beyond the OEPM three above. Always search fresh.

## CHECKING PROCEDURE

### Step 1: Load Previous Report

Glob `path: Library/ip-trademark/reports/` pattern `*ip-portfolio.md`. Read most recent. Extract statuses + dates for comparison. If none: "First run, no previous comparison."

### Step 2: Check EUIPO via REST API

`WebFetch GET https://euipo.europa.eu/copla/trademark/data/{applicationNumber}` for each EUIPO mark. Extract: status, oppositionStartDate, oppositionEndDate, applicants, niceclasses, markVerbalElement, recent events. Compare to previous report, flag genuine changes only. If WebFetch fails, note and continue.

For Hamilton on EUIPO: WebFetch / WebSearch for "LEAKBUSTERS" filed by Hamilton or any variant. If matched application found, record its number live this run + pull data via REST API.

### Step 3: Check OEPM via BOPI (MANDATORY, never skip or carry forward)

Run the OEPM BOPI checker script via bash:

```bash
cd "Library/processes/scripts" && python3 oepm-bopi-check.py --json
```

This queries the BOPI buscadorAnotaciones at `sede.oepm.gob.es/bopiweb` (server-rendered Struts app) for all 9 marks (6 Pete + 3 Hamilton). Returns JSON with BOPI publication entries (date, expedition number, annotation type) for each mark.

Interpretation guide:
- "Solicitudes de Marcas" / "Solicitud Nombre Comercial": initial publication (opposition period starts ~2 months after this date)
- "Suspenso Solicitud de Marcas" / "Suspenso Solicitud Nc": suspension notice issued (response required)
- "Concesion de Marcas": mark granted/registered
- "Denegacion": mark refused
- No entries: not yet published in BOPI (still being examined)

Compare to previous report: check if any NEW BOPI entries have appeared since the last report date. New entries = genuine status change. Same entries = no change (but now verified live, not carried).

NEVER say "statuses carried" or "OEPM unverified this run". Every run must produce live BOPI data. If the script fails, note the error and try the individual mark endpoint: `python3 oepm-bopi-check.py M 4359094`.

Technical notes:
- Discovered 2026-04-27: OEPM CEO and LocalizadorWeb are Angular SPAs behind reCAPTCHA. The BOPI buscadorAnotaciones is the one server-rendered endpoint that works with curl.
- The script uses curl with session cookies (one fresh session per mark).
- The BOPI only shows publication events, not the full expedition timeline. For deeper status (e.g. "response filed"), cross-reference with Gmail correspondence.

### Step 4: Gmail IP Scan

`python3 Library/processes/scripts/gmail-api.py search "from:oepm.es OR from:euipo.europa.eu OR subject:trademark OR subject:marca OR subject:opposition"`. Parse JSON response. Look for: official correspondence, deadline notifications, fee reminders, third-party actions.

## REPORT OUTPUT

### Full Report (changes found)

Write to `Library/ip-trademark/reports/YYYY-MM-DD-ip-portfolio.md`:

```yaml
---
type: ip-report
date: YYYY-MM-DD
business: canary-detect
tags: [ip-portfolio, trademark, automated]
status: completed
---
```

Include: summary of changes, table of all marks (current vs previous), Gmail correspondence, Hamilton activity section, urgent action items.

OEPM table header must say "verified live via BOPI", never "statuses carried from previous report".

### Short Report (no changes)

```markdown
---
type: ip-report
date: YYYY-MM-DD
business: canary-detect
tags: [ip-portfolio, trademark, automated]
status: completed
---

## IP Portfolio Check, {date}
No status changes since last report ({previous date}).
All EUIPO marks confirmed live via REST API. All OEPM marks verified live via BOPI.
Hamilton: no changes detected.
```

## URGENT TASK CREATION

For urgent items (opposition deadlines within 14 days, imminent BOPI dates, Hamilton activity, IP office emails needing response, fee payments within 30 days):

`asana_create_task` with:
- `name`: "[IP] Description"
- `projects`: [CD-IP-Trademark-Portfolio GID, read from asana-configuration.md]
- `assignee`: Pete's GID
- `custom_fields`: Priority P1 (urgent) or P2 (high) enum GID
- `due_on`: deadline date
- `notes`: description + recommended action

## DAILY NOTE UPDATE

Read `Daily/YYYY-MM-DD.md` first (Write if missing). Edit-append:

```markdown
## IP Portfolio Check
- Pete's EUIPO marks: {summary}
- Pete's OEPM marks: {summary, must say "verified live via BOPI"}
- Hamilton marks: {summary}
- Gmail: {count} IP-related emails found
- Tasks created: {count or "none"}
- Urgent items: {any deadlines within 14 days}
- Report saved: [[{report filename}]]
```

IMPORTANT: Read daily note BEFORE appending. Other tasks may have already written.

## EMAIL SUMMARY

`python3 Library/processes/scripts/gmail-api.py send pete.ashcroft@sygma-solutions.com "IP Portfolio Check, {date}" "{html_body}" --html`

Keep short unless changes found. Urgent items in bold/red at top if any.

If send fails, fall back to draft via `python3 Library/processes/scripts/gmail-api.py draft ...` and note in daily note.

## ERROR HANDLING

- EUIPO REST API fails for a mark: note + continue
- OEPM BOPI script fails: try individual marks via `python3 oepm-bopi-check.py M {number}`. If all fail, note error + continue. NEVER fall back to "status carried", report the failure explicitly.
- Gmail search fails: note + continue (helper rarely fails)
- Always send email + update daily note even if partial

## ANTI-PATTERNS

- NEVER cache Hamilton's EUIPO application numbers (search fresh each run; OEPM numbers are cached above)
- NEVER say "status carried from previous report" for OEPM marks. Always run the BOPI check.
- NEVER use WebSearch as primary for OEPM (unreliable, SPA pages return no data)
- NEVER use Chrome MCP as primary (not reliable in scheduled runs)
- NEVER use bash curl for EUIPO (sandbox may not have outbound; use WebFetch). DO use bash for OEPM BOPI script.
- NEVER use em dashes. NEVER use double dashes. Replace with full stops, commas, parentheses, or colons. Both are 100% associated with AI-written text and must be eliminated from output.
- NEVER skip previous report read
- NEVER overwrite files without reading first
- NEVER create tasks for unchanged statuses
- NEVER include API keys / PATs in reports or emails
- NEVER reference Zapier `gmail_send_email` or Gmail MCP `099d2726`. Superseded by helper.
- NEVER send to any email other than pete.ashcroft@sygma-solutions.com