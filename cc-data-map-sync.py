#!/usr/bin/env python3
"""cc-data-map-sync.py — the canonical "where does each kind of data live" map, pushed into the
CC Supabase `public.data_map` so Claude can query it and the Command Centre can show it
(Pete 22 Jun: "Claude knowing where the data lives"; Business-OS decisions #2/#3/#12).

This script IS the editable source of the data-map (the routing rules are curated, not derivable);
running it regenerates the table. Keep it current when a data home changes.

Usage:  python3 cc-data-map-sync.py [--dry]
"""
# CRON-META
# what: Refreshes the data-map (the 21 'where does X live' rows) in CC Supabase so Claude and the Ask page always know where every kind of Pete's data lives.
# why: Keeps the data-map (where every kind of Pete's data lives) current in the CC so Claude and the Ask page always know the homes. First cron proven on Railway (22 Jun).
# reads: the data-home definitions
# writes: CC Supabase data_map (the 21 data-homes)
# entity: command-centre
# schedule: 0 5 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import json, sys, os, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")  # Railway bootstrap sets VAULT=repo
KEYS = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))
URL, SVC = KEYS["url"], KEYS["service_role_key"]
DRY = "--dry" in sys.argv

# LIVE COUNTS — the map must ALWAYS reflect the live DB, never a hard-coded number (Pete, 27 Jun 2026).
# Every count below is queried fresh each run via PostgREST; if a count can't be fetched we ABORT rather
# than write a stale/partial map. (Previously hard-coded as 1,909 notes / 84 process / 16 SOP / 6,730 edges /
# 72 secrets — all drifted; this removes the whole class of bug.)
def _cnt(table, filt=""):
    url = f"{URL}/rest/v1/{table}?select=count" + (f"&{filt}" if filt else "")
    r = urllib.request.Request(url, headers={"apikey": SVC, "Authorization": f"Bearer {SVC}"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode())[0]["count"]
    except Exception as e:
        print(f"data-map: LIVE COUNT failed for {table} ({e}) — aborting, refusing to write a stale map", file=sys.stderr)
        sys.exit(2)

N_NOTES   = _cnt("vault_notes")
N_PROCESS = _cnt("vault_notes", "type=eq.process")
N_SOP     = _cnt("vault_notes", "type=eq.sop")
N_EDGES   = _cnt("note_links")
N_SECRETS = _cnt("secrets")
N_FILES   = _cnt("drive_files")

# domain · owner_system · home · access · notes
MAP = [
    ("Files & documents", "Cross", f"Google Drive — 12 indexed drives ({N_FILES:,} indexed files)", "drive_files index (cc-sql) + the synced Drive mount", "Any document/sheet/PDF/image/report. Find via the index, not the vault."),
    ("Knowledge (lessons/decisions/notes/memory)", "Cross", "CC Supabase public.vault_notes", "cc-knowledge-api.py / CC Brain page", f"{N_NOTES:,} notes + {N_EDGES:,}-edge link graph + semantic search."),
    ("Processes / SOPs / workflows", "Cross", "CC vault_notes (type = process | sop | workflow)", "whereis.py / cc-knowledge-api.py / CC Process Library · the ONE write-path = cc-knowledge-ingest.py", f"Every how-to procedure, SOP + workflow ({N_PROCESS} process + {N_SOP} SOP notes). Write/change one by ingesting its note via cc-knowledge-ingest.py → vault_notes — NEVER a free-floating doc. Surfaced in the CC Process Library + semantic search."),
    ("Connections (APIs / MCP / integrations)", "Cross", "CC vault_notes — the [[connections]] Connections Registry + per-connection config notes (keys live separately in public.secrets)", "whereis.py / cc-knowledge-api.py · CC Process Library → Connectors tab · add via cc-knowledge-ingest.py", "Every API / MCP / service connection. To add or change one: write its config note + register it in [[connections]] (cc-knowledge-ingest.py). The secret/key itself lives in public.secrets, not here."),
    ("Automations & crons", "Cross", "CC Supabase public.crons (+ cron_events timeline)", "cc-cron.py (the ONE tool) · CC /m/automations-log · whereis.py", "All crons live on Railway. ONE tool = cc-cron.py (deploy / set-schedule / pause / resume / retire / status): author the schedule in each script's # CRON-META (Lanzarote-local), cc-cron.py converts to UTC + writes public.crons; the dashboard reads it live. crons-manifest.json, cc-cron-sync.py, railway-deploy.py + railway-sync-repo.py are RETIRED (hard-exit). The old public.processes snapshot has been dropped."),
    ("Pete's tasks", "CC", "CC Supabase public.tasks", "cc-sql / CC Tasks page (Stage-2)", "Priority engine, replacing Asana for Pete. ⚠ a CD-leak `tasks` shadows it."),
    ("Live work (Jane + legacy)", "Sygma", "CC Supabase public.tasks (Delegated track)", "cc-sql / CC Tasks page", "Asana FULLY RETIRED 2026-07 (Pete confirmed 17 Jul). Delegated/Jane work now lives in public.tasks (Delegated track); no live Asana connector or secret."),
    ("Courses (catalogue)", "Sygma", "Sygma Portal public.courses (+ web-Hub /hub/courses)", "Portal (CC surfaces, never owns)", "Portal admin IS the source (YAML retired 3 Jul; allocator lives in course-code-register). Courses → the Sygma Platform, not the CC (Pete 22 Jun)."),
    ("Training delivery / utilisation / KPIs", "Sygma", "Sygma Portal (hub schema)", "Portal API / CC reads it (/m/sygma-training/utilisation)", "Bookings master sheet → Portal. Utilisation → Sygma Platform, not the CC."),
    ("Staff (Sygma)", "Sygma", "Sygma Staff System (Hub) + owner-private payroll", "see [[staff-data-routing]]", "Operational in the Hub; payroll/salary docs owner-private only."),
    ("Secrets / API keys", "Cross", "CC Supabase public.secrets (+ local mirror ~/.config/pete-secrets)", "cc-sql / the mirror", f"{N_SECRETS}, owner-gated; keys-in-Drive is fine (Pete)."),
    ("Property state (websites)", "CC", "CC Supabase property cards (property-state system)", "CC Properties page (/m/properties)", "Nightly property-live-state refresh across ~30 properties."),
    ("Business structure / entities (the group)", "CC", "CC Supabase public.entities (+ bank_accounts link)", "CC Business Structure page (/m/entities) · cc-sql", "Every company/entity Pete owns or controls: ownership %, registry numbers, compliance dates, VAT, accountant, accounting system, banking, websites, the story + open questions. Companies House / Xero facts cached with source+as_of; owned-here facts edited in the page. THE single master — the business-structure group + Phase-2 extraction notes are narrative/history only. Refresh UK facts via companies-house-api.py ([[companies-house-api-configuration]])."),
    ("Calendar / schedule", "Personal", "Google Calendar (source of truth) + CC mirror public.calendar_events", "calendar-api.py · cc-sql for the CC mirror", "Google Calendar is source; the CC keeps a synced mirror in public.calendar_events (252 rows) for on-CC reads. CC Schedule page = Stage-2."),
    ("Email", "Cross", "Gmail (Google Workspace)", "gmail-api.py", "Triage / sync / sweep workflows; Gmail is source of truth."),
    ("Finance — Canary Detect", "Canary Detect", "Drive: Entities Private / Canary Detect (Camello Blanco SL) / Finance + Odoo", "odoo-api.py / Drive mount", "Camello Blanco S.L. entity; Stripe live. (CD Private folded into Entities Private, 4 Jul.)"),
    ("Finance — family/personal", "Personal", "Drive: Ashcroft Family/Finance", "Drive mount", "Joint Pete + Michaela, family-private."),
    ("Health / Garmin", "Personal", "CC: public.garmin_daily (metrics only)", "garmin-daily-cc.py (Railway cron — ONE JOB: pull Garmin)", "Sleep/HRV/readiness/activities. Cron is Garmin-only (no Drive, no journal). Manual refresh: cc-cron.py deploy garmin-daily-pull --run. Garmin lib = garmin-pull-lib.py (pure, cloud-native); sign-off = garmin-signoff.py."),
    ("Passion Fit (journal/training/zones/goals)", "Personal", "CC tables: health_journal / health_feedback / health_weekly / health_planned_session / health_config (zones+goals)", "Authored + edited IN THE CC app (commandcentre.info/m/health) — owner-gated editors", "CC-native (27 Jun 2026). Journal/feedback/weekly/zones/goals all live in the CC; Drive is OUT of the health dashboard. Lessons = derived from health_journal. Drive `My Drive/Passion Fit/*` source files retired (pending hard-delete)."),
    ("Training stats (per-session + per-rep + weekly volume)", "Personal", "CC tables: public.training_session / training_rep / training_session_code_map + views training_weekly_volume / training_weekly_totals", "training-ingest.py (prescription-driven Garmin ingest; reads the Garmin workout store) — wired into the training-feedback-loop pull step", "SSOT for structured session stats + weekly volume (10 Jul 2026). Zone slugs validated against health_config training-zones by a trigger; zone bands numeric since schema_version 2. Verify: training-verify.py. Dashboard: /m/health/progress + the per-session rep table. Plan: Projects/PA-Health/plan-training-stats-db-2026-07-10."),
    ("Screenshots & captures", "Personal", "Drive: My Drive/Screenshots", "Drive mount (~/Library/CloudStorage/GoogleDrive-…/My Drive/Screenshots) + drive_files index", "macOS Cmd-Shift screenshot save location (`defaults read com.apple.screencapture location`). Where Pete drops booking/account/site captures for Claude to read. Newest first by filename `Screenshot YYYY-MM-DD at HH.MM.SS.png`."),
    ("Daily notes", "CC", "CC Supabase public.daily_log (one row per session, cron_name='session')", "CC Daily Notes page (/m/daily) · cc-sql", "Session logs; the most-read memory. Corrected 17 Jul 2026: the log lives in public.daily_log (363 rows), NOT vault_notes type=daily (0 rows)."),
    ("Plans", "CC", "CC Supabase vault_notes (plan-family types)", "CC Plans page (/m/plans)", "≈182 plans; a typed `plans` table is Stage-2."),
    ("Code / scripts", "Cross", "GitHub (PortalPeteZero / SygmaSol) + Library/processes/scripts skeleton", "git / the skeleton", "Version-controlled; helpers run on Railway at Part H."),
    ("CD Leak app data", "Canary Detect", "CD-Leak Supabase + Drive: Canary Detect/App Data", "the CRM app (hard-excluded from automated ops)", "Live customer CRM — verify ref + hard-exclude before any Drive op."),
    ("Sygma Portal CRM", "Sygma", "Sygma Portal Supabase (hub schema) + Drive: Sygma Hub/App Data", "the Portal app (CC reads/surfaces)", "The Sygma engine's operational store; CC monitors, never absorbs (#2)."),
    # --- subsystems homed 17 Jul 2026 (CC Locator build; were unhomed) ---
    ("System config (the REAL CLAUDE-md + the map)", "CC", "CC Supabase public.config", "cc-sql — UPDATE config SET value=… WHERE key='claude-md' (or 'map-md')", "THE most important SSOT. key 'claude-md' (~43k chars) IS the real operating rules (the boot kernel calls it the REAL CLAUDE); 'map-md' is the orientation-map source; 'protected-slugs', 'triage-auto-mode', 'triage-sync-mode' are live config. To change a rule, edit the config row — nothing generates it."),
    ("Connector registry (APIs / MCP)", "CC", "CC Supabase public.connectors (machine registry; the [[connections]] vault note is the human index)", "cc-sql · CC Process Library → Connectors · connection-parity.py (drift)", "40 rows: 39 direct-api + 1 MCP. Ground-truth for direct-api access. Token-less MCP connectors (session-only) stay a manual connection-updater ritual. Keys live in public.secrets."),
    ("Banking / statements & reconciliation", "Cross", "CC Supabase public.bank_accounts (45) + bank_statement_lines (1000) + bank_account_history", "cc-sql · CC Business Structure page", "Structured bank records per entity (sort_code/account/iban) + imported statement lines. Distinct from the Drive finance folders."),
    ("Projects (registry)", "CC", "CC Supabase public.projects (+ buckets = per-project groupings)", "cc-sql · cc-project-api.py (create) · CC Projects", "Every working project: slug + entity_slug + Drive home + knowledge home. Buckets are sub-groupings within a project."),
    ("Shipped work log", "CC", "CC Supabase public.work_log (~1,758 rows)", "worklog.py (write) · cc-sql · CC /m/work-log", "The cross-property 'what did we ship / did it work' SSOT (MEMORY.md canonical). The TABLE — distinct from the worklog.py helper."),
    ("Triage engine (email routing + decisions)", "CC", "CC Supabase public.triage_routing_facts (261 = sender→label/entity/auto-file rules) + triage_decisions (172 = audit trail) + triage_digests (16 = output)", "cc-sql · the inbox-triage skill · /m/… triage pages", "Where the email auto-file/routing rules + every triage decision live. triage-auto-mode/triage-sync-mode config in public.config."),
    ("Key Account Management (account_*)", "Sygma", "CC Supabase public.account_people/deliverables/documents/kpi/meetings/obligations/risks/state/config", "cc-sql · the KAM pages", "The Key-Account-Management subsystem (NOT a customer master — customers live in Drive Customers/Suppliers + Odoo + Portal CRM). Per-account people, deliverables, KPIs, risks, obligations."),
    ("Enquiry Engine (training enquiries)", "Sygma", "CC Supabase public.enquiry_touches (82) + ee_rates/ee_customer_rates/ee_catalogue/ee_phrases/ee_rules/ee_edits", "ee-facts.py / te-log.py / ee-learn.py · /m/enquiry-engine", "The EE knowledge model: prices in ee_rates/ee_customer_rates, catalogue in ee_catalogue, phrasing in ee_phrases, rules in ee_rules, learning trail in ee_edits + enquiry_touches. Lifecycle = Portal CRM."),
    # --- APP SCHEMAS in this same database (added 19 Jul 2026). Every qualified schema.table name
    # MUST appear here: cc-locator-audit.py builds "{schema}.{table}" per object and requires an
    # exact match in the joined data_map text. Schema-level summaries alone leave them unhomed.
    ("El Atico — accounts", "El Atico", "CC Supabase schema ea: ea.accounts + ea.categories + ea.category_memory + ea.committee_reports + ea.month_periods + ea.transactions", "the El Atico web app · cc-sql", "El Atico community accounting: transactions (Cash / Bank Transfer / Pete Paid), categories with a learned memory, month periods and the committee reports built from them."),
    ("Community water — Casas del Sol (cds)", "Personal", "CC Supabase schema cds: cds.periods + cds.period_summaries + cds.villa_readings + cds.sub_meter_readings", "the Casas del Sol water app · cc-sql", "Per-villa and sub-meter readings by period, plus the computed period summaries (consumption + loss)."),
    ("Community water — Los Claveles (lc)", "Personal", "CC Supabase schema lc: lc.unit_readings + lc.monthly_reports + lc.footnote_zeros + lc.footnote_outliers", "the Los Claveles water app · cc-sql", "Per-unit readings, the monthly report rows built from them, and the footnote flags for zero and outlier readings."),
    ("Community water — Parcela 25 (p25)", "Personal", "CC Supabase schema p25: p25.street_data + p25.direct_connections + p25.monthly_reports + p25.footnote_zeros + p25.footnote_anomalies + p25.tasks + p25.users", "the Parcela 25 water app · cc-sql", "Street-level consumption and loss %, direct connections, monthly reports, footnote flags, plus that app's own tasks and users. NOTE p25.tasks and p25.users shadow the CC public.tasks — different tables, do not confuse them."),
    ("Payroll — UK (Sygma)", "Sygma", "CC Supabase schema payroll: payroll.staff + payroll.payroll_month + payroll.payroll_fy + payroll.disciplinary + payroll.edit_audit", "the payroll web app · cc-sql — OWNER-PRIVATE", "UK payroll: staff records, monthly runs, financial-year rollups, disciplinary records and the edit audit trail. Owner-private (see the Staff row). No cron — the monthly xero-wages process is manual by Pete's decision."),
    ("Payroll — Spain (nóminas)", "Canary Detect", "CC Supabase schema payroll_es: payroll_es.employee + payroll_es.nomina + payroll_es.year_summary + payroll_es.edit_audit", "the Spanish payroll web app · cc-sql — OWNER-PRIVATE", "Spanish payroll: employees, nóminas, the year_summary view and the edit audit trail. Owner-private."),
    ("Backlink outreach (bl)", "Cross", "CC Supabase schema bl: bl.refdomains + bl.work_items", "backlinks-weekly-report.py · bl-sheet-sync.py · cc-sql", "Referring domains with ratings, and the outreach work items against them. Written by the backlinks-weekly-report and bl-sheet-sync crons."),
    ("Saved report snapshots (reports)", "CC", "CC Supabase schema reports: reports.snapshots", "daily-briefing.py · oconnors-seo-report.py · sygma-ads-fortnightly-report.py · cc-sql", "Point-in-time report snapshots (e.g. sygma-google-daily) kept so a report can be re-read as it was on the day."),
    # --- A2: a trainers' working folder, recorded so "where do course site plans live" has an answer.
    ("Course site plans (trainers)", "Sygma", "Drive: Sygma Trainers/Plans", "Drive mount · drive_files index", "Utility record drawings and site plans for course venues (BT, Electricity North West, gas, water, sewer, Virgin, CAD packs, utility searches). A LIVE WORKING FOLDER owned by the trainers, who add to it — do not move or reorganise it."),
    ("Clancy damage reviews", "Sygma", "CC Supabase public.clancy_damages + public.clancy_reports + public.clancy_actions + public.clancy_training_courses + public.damage_review_rules", "cc-sql · the damage-review engine · /m/clancy-* pages", "Clancy damage findings + report content (sectioned) + the partnership action board + the rollout schedule + the wording rules the review engine lints against. RE-AUTHORED 19 Jul 2026: a hand-edit naming all five was reverted by this cron on 19 Jul 04:01 because it was never written here — every table must be named in the SCRIPT, not the table."),
    ("Finance ledger (CC)", "Cross", "CC Supabase public.finance_ledger", "cc-sql", "The CC-side finance ledger table. Entity finance homes remain the Drive folders + Odoo (see the Finance rows)."),
    ("Ads (advertising)", "Cross", "CC Supabase public.ads", "cc-sql / ads-api.py", "Advertising records (Google Ads etc.)."),
    ("Family ID / admin", "Personal", "CC Supabase public.family_id", "cc-sql", "Family identity/admin records (Ashcroft family). Family documents live in the Ashcroft Family Drive."),
    ("Quick notes (scratchpad)", "CC", "CC Supabase public.notes (Keep-style, distinct from vault_notes)", "cc-sql · CC Quick Notes · 'note: …' / 'check notes'", "Pete's Keep-style scratchpad. Promote-to-task/project/knowledge in the CC UI. NOT the knowledge base (that's vault_notes)."),
    ("CC pages / modules", "CC", "CC Supabase public.modules (73 = the page registry; module_content = body)", "cc-sql · the command-centre repo app/m/[slug] · /m/map", "The registry of every commandcentre.info page. The CC is a single dynamic router app/m/[slug]; the page list is public.modules, rendered on /m/map."),
    ("Storage buckets (files/media)", "CC", "CC Supabase storage buckets: cc-modules, cc-report-media, leak-reports", "the Supabase storage API", "Binary/media storage buckets (distinct from public.buckets, which is project-groupings)."),
]

# S0.3 — backing_ref per data_map row (the structured backing SSOT the drift check validates).
# Only the rows this script owns; hand-added table rows get theirs during the Stage-3 backing_ref curation pass.
BACKING = {
    "Files & documents": "table:public.drive_files",
    "Knowledge (lessons/decisions/notes/memory)": "table:public.vault_notes",
    "Processes / SOPs / workflows": "table:public.vault_notes",
    "Connections (APIs / MCP / integrations)": "table:public.connectors",
    "Automations & crons": "table:public.crons",
    "Pete's tasks": "table:public.tasks",
    "Live work (Jane + legacy)": "table:public.tasks",
    "Secrets / API keys": "table:public.secrets",
    "Business structure / entities (the group)": "table:public.entities",
    "Calendar / schedule": "table:public.calendar_events",
    "Health / Garmin": "table:public.garmin_daily",
    "Passion Fit (journal/training/zones/goals)": "table:public.health_journal",
    "Training stats (per-session + per-rep + weekly volume)": "table:public.training_session",
    "Daily notes": "table:public.daily_log",
    "Plans": "table:public.vault_notes",
    "System config (the REAL CLAUDE-md + the map)": "table:public.config",
    "Connector registry (APIs / MCP)": "table:public.connectors",
    "Banking / statements & reconciliation": "table:public.bank_accounts",
    "Projects (registry)": "table:public.projects",
    "Shipped work log": "table:public.work_log",
    "Triage engine (email routing + decisions)": "table:public.triage_routing_facts",
    "Key Account Management (account_*)": "table:public.account_people",
    "Enquiry Engine (training enquiries)": "table:public.enquiry_touches",
    "El Atico — accounts": "table:ea.transactions",
    "Community water — Casas del Sol (cds)": "table:cds.villa_readings",
    "Community water — Los Claveles (lc)": "table:lc.unit_readings",
    "Community water — Parcela 25 (p25)": "table:p25.street_data",
    "Payroll — UK (Sygma)": "table:payroll.staff",
    "Payroll — Spain (nóminas)": "table:payroll_es.employee",
    "Backlink outreach (bl)": "table:bl.refdomains",
    "Saved report snapshots (reports)": "table:reports.snapshots",
    "Course site plans (trainers)": "drive:Sygma Trainers/Plans",
    "Clancy damage reviews": "table:public.clancy_damages",
    "Finance ledger (CC)": "table:public.finance_ledger",
    "Ads (advertising)": "table:public.ads",
    "Family ID / admin": "table:public.family_id",
    "Quick notes (scratchpad)": "table:public.notes",
    "CC pages / modules": "table:public.modules",
    "Storage buckets (files/media)": "storage:buckets",
    "Courses (catalogue)": "external:sygma-portal.courses",
    "Training delivery / utilisation / KPIs": "external:sygma-portal.hub",
    "Staff (Sygma)": "external:sygma-hub.staff_directory",
    "Staff directory (CC bot mirror)": "table:public.staff_directory",
    "Email": "external:gmail",
    "Finance — Canary Detect": "drive:Entities Private/Canary Detect (Camello Blanco SL)/Finance",
    "Finance — family/personal": "drive:Ashcroft Family/Finance",
    "Screenshots & captures": "drive:My Drive/Screenshots",
    "Code / scripts": "external:github",
    "CD Leak app data": "external:cd-leak-supabase",
    "Sygma Portal CRM": "external:sygma-portal.hub",
}

import datetime
_NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()  # bump updated_at so freshness/last-run tracks
rows = [{"domain": d, "owner_system": o, "home": h, "access": a, "notes": n, "sort": i * 10, "updated_at": _NOW, "backing_ref": BACKING.get(d, "")}
        for i, (d, o, h, a, n) in enumerate(MAP)]

def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, method=method,
        headers={"apikey": SVC, "Authorization": f"Bearer {SVC}", "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=representation"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:300]); sys.exit(1)

print(f"{len(rows)} data-home rows · systems:", sorted({r['owner_system'] for r in rows}))
if DRY:
    print("--dry: not writing"); sys.exit(0)
status, out = req("POST", "data_map?on_conflict=domain", rows)
print(f"upserted {len(out)} rows (HTTP {status})")
