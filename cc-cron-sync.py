#!/usr/bin/env python3
"""cc-cron-sync.py — push the canonical cron registry (crons-manifest.json) into CC Supabase public.crons,
enriched with LIVE status, and keep a meaningful change-timeline in public.cron_events.

This is the engine behind https://commandcentre.info/m/automations-log. The rule (see cron-registry.md):
  ▸ ANY time a cron is created / edited / paused / removed → edit crons-manifest.json, then run this. ◂
The page + the on-page chat read public.crons live, so they're correct the moment this finishes.

Live enrichment:
  • railway crons   → Railway GraphQL: latest deployment status; + a freshness PROBE (read the table the cron
                       writes, e.g. data_map.updated_at) for a TRUE last-run time, since Railway cron runs reuse
                       the deployment context and don't surface a fresh deployment row.
  • mac-launchd     → `launchctl print-disabled` (only meaningful when run ON the Mac): disabled ⇒ frozen.
  • cowork / claude-code → status carried from the manifest (frozen for the migration).

Timeline: logs cron_events only on real change — new cron (created), status flip, schedule change, enable/disable.
No generic per-run spam.

Credentials (env-first so it runs UNCHANGED on Railway; falls back to the CC secrets table locally):
  CC_SUPABASE_URL · CC_SUPABASE_SERVICE_KEY · RAILWAY_TOKEN
Usage: python3 cc-cron-sync.py [--dry]
"""

import sys as _sys
if __name__ == "__main__":
    _sys.exit("DEPRECATED → crons are managed by cc-cron.py (list/deploy/set-schedule/pause/resume/retire/status). "
              "See cron-registry.md. This script is retired — do not use it.")

import json, os, sys, subprocess, urllib.request, urllib.error, shutil, datetime, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "..", "crons-manifest.json")
DRY = "--dry" in sys.argv
NOW = datetime.datetime.now(datetime.timezone.utc)

RAILWAY_PROJECT = "b2d89898-cc67-43a7-b900-af2c2c8e4a66"
RAILWAY_ENV = "7b0fd4ed-0f4a-41a4-8eb0-86e713397380"
# crons that write to a CC table → read that table's freshness for a true last-run time
FRESHNESS_PROBE = {
    "data-map-cron": ("data_map", "updated_at"),
    "drive-changes-watch": ("drive_files", "indexed_at"),
}
# columns we never blank on re-sync if we have no live value
CONDITIONAL = {"last_run_at", "last_status", "last_output", "next_run_at", "host_ref"}

def secret(name):
    out = subprocess.run(["python3", os.path.join(HERE, "cc-sql.py"),
        f"SELECT value FROM secrets WHERE name='{name}'"], capture_output=True, text=True)
    try: return json.loads(out.stdout)[0]["value"]
    except Exception: return None

def creds():
    url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        blob = secret("command-centre-supabase-keys.json")
        if blob:
            k = json.loads(blob); url = url or k["url"]; key = key or k["service_role_key"]
    return url, key

SB_URL, SB_KEY = creds()
RW_TOKEN = os.environ.get("RAILWAY_TOKEN") or secret("railway-token")

def sb(method, path, body=None, prefer=None):
    """PostgREST call against CC Supabase."""
    url = f"{SB_URL}/rest/v1/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("apikey", SB_KEY); req.add_header("Authorization", f"Bearer {SB_KEY}")
    req.add_header("Content-Type", "application/json")
    if prefer: req.add_header("Prefer", prefer)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else []
    except urllib.error.HTTPError as e:
        print(f"  ✗ Supabase {method} {path}: {e.code} {e.read().decode()[:200]}"); return None

def railway(query):
    req = urllib.request.Request("https://backboard.railway.app/graphql/v2",
        data=json.dumps({"query": query}).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {RW_TOKEN}"); req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "cc-cron-sync/1.0")   # Railway edge 403s the default python-urllib UA
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read().decode())
    except Exception as e:
        print(f"  ⚠ Railway: {e}"); return {}

def probe_last_run(key):
    tbl_col = FRESHNESS_PROBE.get(key)
    if not tbl_col: return None
    tbl, col = tbl_col
    rows = sb("GET", f"{tbl}?select={col}&order={col}.desc&limit=1")
    if rows and rows[0].get(col): return rows[0][col]
    return None

PBS_RAW = "https://raw.githubusercontent.com/PortalPeteZero/pete-brain-scripts/main"
def _md5(b): return hashlib.md5(b).hexdigest()
def drift_for(cron):
    """For a railway cron with a script_file: is the DEPLOYED repo copy byte-identical to the canonical
    helper? Returns None if clean, else a message. THIS is what makes the data-map stub class of bug
    impossible to hide — any future divergence shows as red on the card."""
    sf = cron.get("script_file")
    if not sf or cron.get("host") != "railway": return None
    local = os.path.join(HERE, sf)
    if not os.path.exists(local): return f"canonical {sf} missing from Library/processes/scripts/"
    try:
        remote = urllib.request.urlopen(f"{PBS_RAW}/{sf}", timeout=15).read()
    except Exception:
        return None   # network hiccup — don't false-flag drift
    if _md5(remote) != _md5(open(local, "rb").read()):
        return f"⚠ Railway repo copy of {sf} DIFFERS from canonical — run: railway-sync-repo.py {sf}"
    return None

def mac_disabled_labels():
    if not shutil.which("launchctl"): return None   # not on the Mac (e.g. Railway) → unknown
    try:
        uid = os.getuid()
        out = subprocess.run(["launchctl", "print-disabled", f"gui/{uid}"], capture_output=True, text=True).stdout
        return out
    except Exception: return None

def main():
    if not (SB_URL and SB_KEY):
        print("✗ no CC Supabase credentials (env or secret)"); sys.exit(1)
    man = json.load(open(MANIFEST))
    crons = man["crons"]
    print(f"cc-cron-sync — {len(crons)} crons{' (dry)' if DRY else ''}")

    # existing state for change-detection
    existing = {r["key"]: r for r in (sb("GET", "crons?select=key,status,schedule,enabled") or [])}
    # live columns of public.crons — filter every row to these so an extra manifest field (e.g. a new
    # schedule_local before its column exists) can NEVER silently break a row's upsert again
    _probe = sb("GET", "crons?select=*&limit=1") or []
    COLS = set(_probe[0].keys()) if _probe else None
    disabled_blob = mac_disabled_labels()

    # railway services (for undocumented-drift check + per-service deploy status)
    rw = railway(f'{{ project(id:"{RAILWAY_PROJECT}") {{ services {{ edges {{ node {{ id name }} }} }} }} }}')
    rw_services = {}
    try:
        for e in rw["data"]["project"]["services"]["edges"]:
            rw_services[e["node"]["name"]] = e["node"]["id"]
    except Exception: pass

    payloads, events = [], []
    for c in crons:
        key = c["key"]; row = dict(c)
        row["updated_at"] = NOW.isoformat()
        # --- live enrichment ---
        if c["host"] == "railway":
            row["host_ref"] = rw_services.get(key) or rw_services.get(c.get("title",""))
            last = probe_last_run(key)
            if last:
                row["last_run_at"] = last; row["last_status"] = "SUCCESS"
                # fresh if written within ~2 daily intervals
                row["status"] = "ok"
            row["code_drift"] = drift_for(c)   # None clears any prior drift; a message turns the card red
        elif c["host"] == "mac-launchd" and disabled_blob is not None:
            label = f"com.peterashcroft.{key}"
            is_disabled = (f'"{label}" => disabled' in disabled_blob) or (f"{label} => disabled" in disabled_blob)
            if c["migration_status"] == "binned":
                row["status"] = "binned"
            elif c["migration_status"] != "retired":
                row["status"] = "frozen" if is_disabled else c.get("status", "unknown")
            last = probe_last_run(key)
            if last: row["last_run_at"] = last
        # freshness probe for any host
        if "last_run_at" not in row:
            last = probe_last_run(key)
            if last: row["last_run_at"] = last

        # strip conditional columns we have no value for (preserve existing on upsert)
        clean = {k: v for k, v in row.items() if (COLS is None or k in COLS) and not (k in CONDITIONAL and v in (None, "", []))}
        payloads.append(clean)

        # --- change timeline ---
        prev = existing.get(key)
        if prev is None:
            events.append({"cron_key": key, "kind": "created", "detail": f"registered ({c['host']}, {c.get('schedule_human','')})"})
        else:
            if prev.get("status") != clean.get("status") and clean.get("status"):
                events.append({"cron_key": key, "kind": "status-changed", "detail": f"{prev.get('status')} → {clean.get('status')}"})
            if prev.get("schedule") != clean.get("schedule") and clean.get("schedule"):
                events.append({"cron_key": key, "kind": "schedule-changed", "detail": f"{prev.get('schedule')} → {clean.get('schedule')}"})
            if prev.get("enabled") != clean.get("enabled"):
                events.append({"cron_key": key, "kind": "enabled" if clean.get("enabled") else "disabled", "detail": ""})

    # undocumented Railway services
    undoc = [n for n in rw_services if n not in {c["key"] for c in crons} and n not in {c.get("title") for c in crons}]
    if undoc: print(f"  ⚠ undocumented Railway services (add to manifest): {undoc}")

    if DRY:
        print(f"  [dry] would upsert {len(payloads)} crons, log {len(events)} events")
        for e in events[:20]: print(f"    · {e['kind']}: {e['cron_key']} {e['detail']}")
        return

    # per-row upsert — PostgREST requires uniform keys per request, and per-row preserves conditional cols on re-sync
    ok = 0
    for p in payloads:
        if sb("POST", "crons?on_conflict=key", [p], prefer="resolution=merge-duplicates,return=minimal") is not None:
            ok += 1
    print(f"  {'✓' if ok == len(payloads) else '✗'} upserted {ok}/{len(payloads)} crons")
    if events and ok:
        if sb("POST", "cron_events", events, prefer="return=minimal") is not None:
            print(f"  ✓ logged {len(events)} timeline events")
            for e in events[:12]: print(f"    · {e['kind']}: {e['cron_key']} {e['detail']}")
    # stamp a registry-level heartbeat into data_map-style? no — just report
    n = sb("GET", "crons?select=status")
    if n:
        from collections import Counter
        print("  status:", dict(Counter(x["status"] for x in n)))

if __name__ == "__main__": main()
