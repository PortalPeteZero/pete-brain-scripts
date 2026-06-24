#!/usr/bin/env python3
"""cc-tasks-seed-from-asana.py — seed/refresh Pete's CC task store (public.tasks) from his open
Asana tasks. Decision #9: Pete migrates off Asana to the CC task engine (Jane stays on Asana).
NON-DESTRUCTIVE: Asana is untouched; this mirrors Pete's open assigned tasks into the CC so the
Tasks page has real data. Idempotent on the Asana gid (re-run to refresh). Priority is COMPUTED
live from the due date in the app (the ladder); here we store the Asana manual P as a reference.

Usage:  python3 cc-tasks-seed-from-asana.py [--dry]
"""
import json, sys, subprocess, urllib.request, urllib.error
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = VAULT
KEYS = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))
URL, SVC = KEYS["url"], KEYS["service_role_key"]
DRY = "--dry" in sys.argv

def entity_of(project: str) -> str:
    if not project: return "Personal"          # loose personal tasks (travel, admin)
    p = project.upper()
    if p.startswith(("SY-", "TEAM-")): return "Sygma"
    if p.startswith("CD-"): return "Canary Detect"
    if p.startswith("PA-"): return "Personal"
    if p.startswith("OS-"): return "One System"
    if p.startswith(("EA-", "AT-")): return "El Atico"
    return "Other"

raw = subprocess.run(["python3", f"{VAULT}/Library/processes/scripts/asana-api.py", "my-tasks"],
                     capture_output=True, text=True).stdout
tasks = json.loads(raw)
rows = []
for t in tasks:
    name = t.get("name", "").strip()
    if not name or name.startswith("Consider delegating"):  # Asana housekeeping, not real work
        continue
    pri = next((f.get("display_value") for f in t.get("custom_fields", []) if f.get("name") == "Priority"), None)
    projs = [p.get("name") for p in t.get("projects", []) if p.get("name")]
    project = projs[0] if projs else None
    rows.append({
        "name": name[:500],
        "priority": pri,                        # Asana manual P (reference; app computes the ladder)
        "due_on": t.get("due_on"),
        "entity_slug": entity_of(project),
        "project_slug": project,
        "notes": (t.get("notes") or "")[:1500] or None,
        "source": "asana",
        "track": "active",
        "status": "todo",
        "asana_gid": t["gid"],
    })

by_ent = {}
for r in rows: by_ent[r["entity_slug"]] = by_ent.get(r["entity_slug"], 0) + 1
print(f"{len(rows)} open tasks → seed · by entity: {dict(sorted(by_ent.items(), key=lambda x:-x[1]))}")
if DRY:
    print("--dry: not writing"); sys.exit(0)

def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, method=method,
        headers={"apikey": SVC, "Authorization": f"Bearer {SVC}", "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=representation"})
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:300]); sys.exit(1)

status, out = req("POST", "tasks?on_conflict=asana_gid", rows)
print(f"upserted {len(out)} tasks (HTTP {status})")