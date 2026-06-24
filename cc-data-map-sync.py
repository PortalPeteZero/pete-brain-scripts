#!/usr/bin/env python3
"""cc-data-map-sync.py — the canonical "where does each kind of data live" map, pushed into the
CC Supabase `public.data_map` so Claude can query it and the Command Centre can show it
(Pete 22 Jun: "Claude knowing where the data lives"; Business-OS decisions #2/#3/#12).

This script IS the editable source of the data-map (the routing rules are curated, not derivable);
running it regenerates the table. Keep it current when a data home changes.

Usage:  python3 cc-data-map-sync.py [--dry]
"""
import json, sys, os, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")  # Railway bootstrap sets VAULT=repo
KEYS = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))
URL, SVC = KEYS["url"], KEYS["service_role_key"]
DRY = "--dry" in sys.argv

# domain · owner_system · home · access · notes
MAP = [
    ("Files & documents", "Cross", "Google Drive — 12 indexed drives (~150k files)", "drive_files index (cc-sql) + the synced Drive mount", "Any document/sheet/PDF/image/report. Find via the index, not the vault."),
    ("Knowledge (lessons/decisions/notes/memory)", "Cross", "CC Supabase public.vault_notes", "cc-knowledge-api.py / CC Brain page", "1,909 notes + 6,730-edge link graph + semantic search."),
    ("Automations & crons", "Cross", "CC Supabase public.crons (+ cron_events timeline)", "cc-sql / CC /m/automations-log (cards + chat)", "47 crons: rich registry from crons-manifest.json via cc-cron-sync.py (+ drift guard). public.processes is the older thin snapshot. 3 live / 42 frozen until Part H / 2 retired."),
    ("Pete's tasks", "CC", "CC Supabase public.tasks", "cc-sql / CC Tasks page (Stage-2)", "Priority engine, replacing Asana for Pete. ⚠ a CD-leak `tasks` shadows it."),
    ("Live work (Jane + legacy)", "Sygma", "Asana", "asana-api.py / Asana MCP", "Jane stays on Asana; Pete migrates to CC tasks."),
    ("Courses (catalogue)", "Sygma", "Sygma Portal public.courses (+ web-Hub /hub/courses)", "Portal (CC surfaces, never owns)", "From _course-map.yaml. Courses → the Sygma Platform, not the CC (Pete 22 Jun)."),
    ("Training delivery / utilisation / KPIs", "Sygma", "Sygma Portal (hub schema)", "Portal API / CC reads it (/m/sygma-training/utilisation)", "Bookings master sheet → Portal. Utilisation → Sygma Platform, not the CC."),
    ("Staff (Sygma)", "Sygma", "Sygma Staff System (Hub) + owner-private payroll", "see [[staff-data-routing]]", "Operational in the Hub; payroll/salary docs owner-private only."),
    ("Secrets / API keys", "Cross", "CC Supabase public.secrets (+ local mirror ~/.config/pete-secrets)", "cc-sql / the mirror", "72, owner-gated; keys-in-Drive is fine (Pete)."),
    ("Property state (websites)", "CC", "CC Supabase property cards (property-state system)", "CC Properties page (/m/properties)", "Nightly property-live-state refresh across ~30 properties."),
    ("Calendar / schedule", "Personal", "Google Calendar", "calendar-api.py", "CC Schedule page = Stage-2 (CC-built on calendar-api.py)."),
    ("Email", "Cross", "Gmail (Google Workspace)", "gmail-api.py", "Triage / sync / sweep workflows; Gmail is source of truth."),
    ("Finance — Canary Detect", "Canary Detect", "Drive: CD Private/finance + Odoo", "odoo-api.py / Drive mount", "Camello Blanco S.L. entity; Stripe live."),
    ("Finance — family/personal", "Personal", "Drive: Ashcroft Family/Finance", "Drive mount", "Joint Pete + Michaela, family-private."),
    ("Health / Garmin", "Personal", "Drive: My Drive/Health/garmin", "garmin-daily-pull.py", "Sleep/HRV/readiness/activities; the one re-pointed cron."),
    ("Passion Fit (journal/training)", "Personal", "Drive: My Drive/Passion Fit", "Drive mount (Desktop Commander)", "Personal PF — stays in Personal, NOT One System."),
    ("Screenshots & captures", "Personal", "Drive: My Drive/Screenshots", "Drive mount (~/Library/CloudStorage/GoogleDrive-…/My Drive/Screenshots) + drive_files index", "macOS Cmd-Shift screenshot save location (`defaults read com.apple.screencapture location`). Where Pete drops booking/account/site captures for Claude to read. Newest first by filename `Screenshot YYYY-MM-DD at HH.MM.SS.png`."),
    ("Daily notes", "CC", "CC Supabase vault_notes (type=daily) + vault Daily/ skeleton", "CC Daily Notes page (/m/daily)", "Session logs; the most-read memory."),
    ("Plans", "CC", "CC Supabase vault_notes (plan-family types)", "CC Plans page (/m/plans)", "≈182 plans; a typed `plans` table is Stage-2."),
    ("Code / scripts", "Cross", "GitHub (PortalPeteZero / SygmaSol) + Library/processes/scripts skeleton", "git / the skeleton", "Version-controlled; helpers run on Railway at Part H."),
    ("CD Leak app data", "Canary Detect", "CD-Leak Supabase + Drive: Canary Detect/App Data", "the CRM app (hard-excluded from automated ops)", "Live customer CRM — verify ref + hard-exclude before any Drive op."),
    ("Sygma Portal CRM", "Sygma", "Sygma Portal Supabase (hub schema) + Drive: Sygma Hub/App Data", "the Portal app (CC reads/surfaces)", "The Sygma engine's operational store; CC monitors, never absorbs (#2)."),
]

import datetime
_NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()  # bump updated_at so freshness/last-run tracks
rows = [{"domain": d, "owner_system": o, "home": h, "access": a, "notes": n, "sort": i * 10, "updated_at": _NOW}
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
