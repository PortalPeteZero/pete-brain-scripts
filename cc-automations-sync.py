#!/usr/bin/env python3
"""cc-automations-sync.py — push the canonical automations registry into the CC Supabase
`public.processes` table, so the Command Centre can monitor every cron/automation and Claude
can query "what runs, what it feeds, where its data lives" (Business-OS decisions #2 + #12;
Pete 22 Jun: the engine's automations must be visible in the CC + known to Claude).

DERIVED, regenerable — source of truth stays `automations-dashboard/automations.json`; this
script regenerates the table rows from it (never hand-maintained). Run after editing the registry.

Usage:  python3 cc-automations-sync.py [--dry]
"""
import json, sys, urllib.request, urllib.error
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
SRC = f"{VAULT}/Library/processes/automations-dashboard/automations.json"
KEYS = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))
URL, SVC = KEYS["url"], KEYS["service_role_key"]
DRY = "--dry" in sys.argv

# infer the business/system each automation serves (the runner-category is kept in steps)
def system_of(idd: str, desc: str) -> str:
    t = f"{idd} {desc}".lower()
    if any(k in t for k in ("cd-", "canary", "leak", "camello", "pipebust", "eco")): return "Canary Detect"
    if any(k in t for k in ("garmin", "passion", "pf-", "journal", "xhale", "scout")): return "Personal"
    if any(k in t for k in ("account", "clancy")): return "Customers"
    if any(k in t for k in ("finance", "invoice", "soldo", "xero", "dext", "odoo", "payroll")): return "Finance"
    if any(k in t for k in ("lanza", "locator", "o'connor", "oconnor", "one-system")): return "One System"
    if any(k in t for k in ("sygma", "staff", "training", "eusr", "proqual", "ads", "hub", "trainer")): return "Sygma"
    return "Ops / System"

def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, method=method,
        headers={"apikey": SVC, "Authorization": f"Bearer {SVC}", "Content-Type": "application/json",
                 "Prefer": "return=representation"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:300]); sys.exit(1)

reg = json.load(open(SRC))
rows = []
for cat in reg["categories"]:
    for t in cat["tasks"]:
        rows.append({
            "name": t["id"],
            "type": "cron",
            "entity_slug": system_of(t["id"], t.get("desc", "")),
            "description": t.get("desc", ""),
            "trigger": t.get("freq", ""),
            "active": t.get("status") == "active",
            "steps": {"runner": cat["key"], "runner_label": cat["label"],
                      "script": t.get("script"), "source": "automations.json",
                      "registry_generated": reg.get("generated"),
                      "migration_state": "frozen until Part H (Railway) — see cron-freeze-ledger"},
        })

print(f"{len(rows)} automations from {reg.get('generated')} · by system:",
      {s: sum(1 for r in rows if r['entity_slug'] == s) for s in sorted({r['entity_slug'] for r in rows})})
if DRY:
    print("--dry: not writing"); sys.exit(0)

# derived registry → clear the cron rows and re-insert (regenerate from truth, #12)
req("DELETE", "processes?type=eq.cron")
status, out = req("POST", "processes", rows)
print(f"wrote {len(out)} rows (HTTP {status})")