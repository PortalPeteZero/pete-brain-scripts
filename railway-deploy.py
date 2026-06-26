#!/usr/bin/env python3
"""railway-deploy.py — ONE self-verifying command to migrate a headless cron to Railway.

Encodes every hard-won Railway lesson:
  • railway.json forces `python railway-bootstrap.py` → we select the script via CRON_SCRIPT env
    (DON'T set startCommand for cron services — the bootstrap command wins).
  • serviceCreate auto-deploys ONCE with OLD config → we ALWAYS redeploy explicitly after config.
  • Railway crons are UTC-only → cronSchedule = cron_tz.local_to_utc(manifest schedule).
  • Deploy the EXACT repo HEAD (serviceInstanceDeployV2 commitSha) — a bare deploy reuses a stale commit.
  • Freshness: verify deployed commit == repo HEAD (the drift guard only checks repo==canonical).
  • Forced-fire checking REAL output (--ff): fire a near-future one-shot cron, read the run logs +
    an optional destination query, THEN restore the real schedule.
  • Capture rule: a cron's output must land in the CC. Report crons write reports.snapshots (a CC
    table) — inherently captured; --check-sql proves the row landed.

Idempotent: find-or-create the service by name (= cron key); re-runnable.

Usage:
  railway-deploy.py <cron-key> [--script NAME.py] [--also helper.py ...]
                    [--secret NAME=@secretfile | NAME=$ENVVAR ...] [--ff] [--check-sql "SQL"] [--dry]
"""

import sys as _sys
if __name__ == "__main__":
    _sys.exit("DEPRECATED → crons are managed by cc-cron.py (list/deploy/set-schedule/pause/resume/retire/status). "
              "See cron-registry.md. This script is retired — do not use it.")

import argparse, json, os, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

HERE = Path(__file__).resolve().parent
# Post-cutover layout: tools are flat at $VAULT and secrets are materialised under
# Library/processes/secrets (railway-bootstrap puts them there); the old HERE.parent/"secrets"
# resolved to /tmp/secrets and broke every deploy.
SEC = HERE / "Library" / "processes" / "secrets"
MANIFEST = HERE / "crons-manifest.json"
PROJECT = "b2d89898-cc67-43a7-b900-af2c2c8e4a66"
ENVN = "7b0fd4ed-0f4a-41a4-8eb0-86e713397380"
REPO = "PortalPeteZero/pete-brain-scripts"
CC_REF = "zhexcaflgahdcbzvbyfq"
TERMINAL = {"SUCCESS", "FAILED", "CRASHED", "REMOVED"}

sys.path.insert(0, str(HERE))
from cron_tz import local_to_utc

SUPA = open(SEC / "supabase-token").read().strip()
def supa(sql):
    req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{CC_REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {SUPA}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}, method="POST")
    try: return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e: raise SystemExit(f"supabase HTTP {e.code}: {e.read().decode()[:300]}")
RW = supa("select value from secrets where name='railway-token'")[0]["value"]
GH = open(SEC / "github-pat").read().strip()
CC = json.load(open(SEC / "command-centre-supabase-keys.json"))

def rw(q, v=None):
    body = {"query": q}
    if v is not None: body["variables"] = v
    req = urllib.request.Request("https://backboard.railway.app/graphql/v2", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {RW}", "Content-Type": "application/json", "User-Agent": "cc-deploy/1.0"}, method="POST")
    try: out = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e: raise SystemExit(f"railway HTTP {e.code}: {e.read().decode()[:400]}")
    if out.get("errors"): raise SystemExit("railway GraphQL: " + json.dumps(out["errors"])[:400])
    return out["data"]

def gh_head():
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/commits/main",
        headers={"Authorization": f"token {GH}", "Accept": "application/vnd.github+json", "User-Agent": "deploy"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())["sha"]

def find_service(name):
    d = rw("""query($p:String!){ project(id:$p){ services{ edges{ node{ id name } } } } }""", {"p": PROJECT})
    for e in d["project"]["services"]["edges"]:
        if e["node"]["name"] == name: return e["node"]["id"]
    return None

def create_service(name):
    return rw("""mutation($i:ServiceCreateInput!){ serviceCreate(input:$i){ id } }""",
              {"i": {"projectId": PROJECT, "name": name, "source": {"repo": REPO}}})["serviceCreate"]["id"]

def upsert(sid, name, value):
    rw("""mutation($i:VariableUpsertInput!){ variableUpsert(input:$i) }""",
       {"i": {"projectId": PROJECT, "environmentId": ENVN, "serviceId": sid, "name": name, "value": value}})

def set_instance(sid, fields):
    rw("""mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){ serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i) }""",
       {"s": sid, "e": ENVN, "i": fields})

def deploy(sid, sha):
    return rw("""mutation($s:String!,$e:String!,$c:String!){ serviceInstanceDeployV2(serviceId:$s,environmentId:$e,commitSha:$c) }""",
              {"s": sid, "e": ENVN, "c": sha})["serviceInstanceDeployV2"]

def poll(did):
    st = "?"
    for _ in range(100):
        st = rw("""query($id:String!){ deployment(id:$id){ status } }""", {"id": did})["deployment"]["status"]
        if st in TERMINAL: return st
        time.sleep(5)
    return "TIMEOUT(" + st + ")"

def deployed_sha(sid):
    try:
        d = rw("""query($s:String!,$e:String!){ serviceInstance(serviceId:$s,environmentId:$e){ latestDeployment{ meta } } }""", {"s": sid, "e": ENVN})
        meta = (d["serviceInstance"]["latestDeployment"] or {}).get("meta") or {}
        return meta.get("commitHash") or meta.get("commit") or ""
    except Exception as e:
        return f"(meta unavailable: {e})"

def clear_schedule(sid):
    """Null the cronSchedule so the next deploy runs the start command ONCE immediately — a Railway
    service with no schedule runs on deploy (proven). This is the runtime proof; far faster + more
    reliable than a near-future cron (Railway enforces a 5-minute minimum cron interval)."""
    rw("""mutation($s:String!,$e:String!){ serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:{cronSchedule:null}) }""", {"s": sid, "e": ENVN})

def run_logs(sid):
    try:
        d = rw("""query($s:String!){ service(id:$s){ deployments(first:1){ edges{ node{ id } } } } }""", {"s": sid})
        did = d["service"]["deployments"]["edges"][0]["node"]["id"]
        r = rw("""query($d:String!){ deploymentLogs(deploymentId:$d, limit:40){ message timestamp } }""", {"d": did})
        return r.get("deploymentLogs") or []
    except Exception as ex:
        return [{"message": f"(log fetch failed: {ex})", "timestamp": ""}]

# CRON-META: the durable, self-documenting metadata block at the TOP of each cron script — it lives in
# git WITH the code, so the description can't drift from what the code does. railway-deploy.py parses it
# on every deploy and writes the descriptive fields to public.crons → the automations page stays in sync
# with ZERO manual steps (deploy/edit/retire are the only ways to change a cron, and each one re-writes
# the record). Format (comment lines, anywhere in the first 80 lines):
#   # CRON-META
#   # what: <one line>          # why: <one line>
#   # reads: <sources>          # writes: <destinations>
#   # entity: sygma|cd|...      # report: <cc-module-slug>
#   # schedule: <cron expr>     # timezone: Atlantic/Canary
#   # CRON-META-END
META_FIELDS = {"what": "what", "why": "why", "reads": "consumes", "writes": "produces", "entity": "entity_slug", "report": "report_module"}
def parse_cron_meta(script):
    p = HERE / script
    if not p.exists(): return {}
    meta, inblock = {}, False
    for line in p.read_text().splitlines()[:80]:
        s = line.strip()
        if "CRON-META-END" in s: break
        if s.replace(" ", "").upper().startswith("#CRON-META"): inblock = True; continue
        if inblock and s.startswith("#") and ":" in s:
            k, _, v = s.lstrip("# ").partition(":")
            if k.strip().lower() in {*META_FIELDS, "schedule", "timezone", "key"}: meta[k.strip().lower()] = v.strip()
    return meta

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--script", default=None)
    ap.add_argument("--also", nargs="*", default=[])
    ap.add_argument("--secret", action="append", default=[])
    ap.add_argument("--no-run", action="store_true", help="(default) skip the immediate runtime-proof run")
    ap.add_argument("--run", action="store_true", help="OPT-IN to the immediate runtime-proof run. The cleared-schedule run can MULTI-FIRE (Railway re-runs the exited container a few times before the schedule arms; restartPolicy=NEVER does NOT stop it). Use ONLY for idempotent data-writers, NEVER for email/side-effect crons.")
    ap.add_argument("--check-sql", default=None)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--retire", action="store_true", help="retire this cron: mark binned in public.crons + delete its Railway service (keeps the page in sync)")
    a = ap.parse_args()

    man = json.load(open(MANIFEST))
    crons = man["crons"] if isinstance(man, dict) and "crons" in man else man   # {_meta, crons:[...]}
    matches = [x for x in crons if isinstance(x, dict) and x.get("key") == a.key]
    if not matches: raise SystemExit(f"no manifest entry with key: {a.key}")
    e = matches[0]   # reference into man['crons'] — in-place mutation persists on json.dump(man)

    if a.retire:     # retire path — keeps the page in sync on decommission, the mirror of deploy
        sid = e.get("host_ref")
        e["migration_status"] = "binned"; e["status"] = "binned"; e["enabled"] = False
        e["bin_reason"] = "retired via railway-deploy --retire"
        json.dump(man, open(MANIFEST, "w"), indent=1)
        subprocess.run([sys.executable, str(HERE / "cc-cron-sync.py")], capture_output=True, text=True)
        if sid:
            try: rw("""mutation($id:String!){ serviceDelete(id:$id) }""", {"id": sid}); print(f"  ✓ Railway service {sid[:8]} deleted")
            except SystemExit as ex: print(f"  ⚠ service delete failed (binned still applied): {str(ex)[:120]}")
        print(f"✓ {a.key} RETIRED — public.crons updated, automations page in sync"); return

    script = a.script or e.get("script_file")
    if not script: raise SystemExit(f"no script_file for {a.key}; pass --script NAME.py")
    meta = parse_cron_meta(script)   # the durable source of descriptive metadata (travels with the code)
    if not meta: print(f"  ⚠ no CRON-META header in {script} — page metadata falls back to the manifest; add a header to future-proof")
    sched_local = meta.get("schedule") or e.get("schedule")
    tz = meta.get("timezone") or e.get("timezone", "Atlantic/Canary")
    also = a.also or e.get("also_sync", [])
    print(f"=== railway-deploy {a.key} ===")
    print(f"  script={script} also={also or '-'} sched_local={sched_local!r} tz={tz}")

    # 1. sync code (byte-equal verified inside railway-sync-repo)
    if not a.dry:
        r = subprocess.run([sys.executable, str(HERE / "railway-sync-repo.py"), script, *also], capture_output=True, text=True)
        print("  " + (r.stdout.strip().replace("\n", "\n  "))[-500:])
        if r.returncode: raise SystemExit("sync failed: " + r.stderr[:300])
    HEAD = gh_head(); print(f"  repo HEAD = {HEAD[:8]}")
    if a.dry: print("  (--dry: stopping before Railway mutations)"); return

    # 2. find-or-create service (named = cron key)
    sid = find_service(a.key)
    print(f"  service {'exists' if sid else 'creating'}: {sid or a.key}")
    if not sid: sid = create_service(a.key)

    # 3. env — CC keys (bootstrap rebuilds the keys file), TZ, CRON_SCRIPT, + per-cron secrets
    upsert(sid, "CC_SUPABASE_URL", CC["url"]); upsert(sid, "CC_SUPABASE_SERVICE_KEY", CC["service_role_key"])
    # CRON_SCRIPT = basename: railway-sync-repo flattens subdir sources (account/foo.py → /app/foo.py),
    # so bootstrap runs the basename at /app. script (the path) is kept for canonical lookup + meta.
    upsert(sid, "TZ", tz); upsert(sid, "CRON_SCRIPT", os.path.basename(script))
    for s in a.secret:
        name, _, src = s.partition("=")
        if src.startswith("@"): val = open(SEC / src[1:]).read().strip()
        elif src.startswith("$"): val = os.environ.get(src[1:], "")
        else: val = src
        upsert(sid, name, val); print(f"  secret {name} set")
    # CRITICAL: a cron service must NOT restart its exited container. The immediate-run model clears the
    # schedule and runs the script once; a default restart policy then re-runs the exited container in a
    # fast crash-loop until the schedule is armed → an email cron blasts duplicate sends (24 week-ahead
    # emails in 6s, 23 Jun). restartPolicyType=NEVER makes the immediate run fire exactly once.
    set_instance(sid, {"restartPolicyType": "NEVER"})   # correct for crons, but does NOT stop the immediate-run multi-fire (see --run)
    print("  env set: CC url/key, TZ, CRON_SCRIPT, restartPolicy=NEVER")

    # 4. immediate-run model: clear the schedule so the deploy runs the script ONCE now (runtime
    #    proof). --no-run (client-emailing crons) sets the schedule BEFORE deploy → no ad-hoc fire.
    #    Never set startCommand — the railway.json bootstrap command must win.
    run_now = a.run and not a.no_run   # immediate test-fire is OPT-IN now (email crons must never multi-fire)
    if run_now:
        clear_schedule(sid); print("  cronSchedule cleared → deploy runs the script once (runtime proof)")
    elif sched_local:
        utc, off, cross = local_to_utc(sched_local); set_instance(sid, {"cronSchedule": utc})
        print(f"  --no-run: cronSchedule {sched_local} → UTC '{utc}' set BEFORE deploy (no immediate run)")

    # 5. deploy the EXACT HEAD + poll
    did = deploy(sid, HEAD); print(f"  deploy {did[:8]} @ {HEAD[:8]}")
    st = poll(did); print(f"  deploy status: {st}")
    if st != "SUCCESS": raise SystemExit(f"deploy not green ({st}) — investigate logs")

    # 6. freshness: deployed == HEAD
    ds = deployed_sha(sid)
    ok = isinstance(ds, str) and ds[:8] and (ds.startswith(HEAD[:8]) or HEAD.startswith(ds[:8]))
    print(f"  freshness: deployed={ds[:12]} HEAD={HEAD[:12]} {'✓' if ok else '⚠ check'}")

    # 7. runtime proof: the immediate run's logs + optional destination check, THEN arm real schedule
    if run_now:
        time.sleep(18)
        print("  runtime logs (tail):")
        for L in run_logs(sid)[-12:]:
            print(f"    {str(L.get('timestamp',''))[:19]} {str(L.get('message',''))[:150]}")
        if a.check_sql:
            print("  destination check:"); print("   ", json.dumps(supa(a.check_sql))[:400])
    if sched_local:
        utc, off, cross = local_to_utc(sched_local); set_instance(sid, {"cronSchedule": utc})
        print(f"  ✓ cronSchedule armed: {sched_local} ({tz}) → UTC '{utc}' (UTC+{off}{' ⚠crosses-midnight' if cross else ''})")

    # 8. manifest + cc-cron-sync (so the CC dashboard reflects the new home). The CRON-META header is the
    #    authoritative source for the descriptive fields → they're re-written from the code on every deploy.
    for mk, ek in META_FIELDS.items():
        if meta.get(mk): e[ek] = meta[mk]
    if meta.get("schedule"): e["schedule"] = meta["schedule"]
    if meta.get("timezone"): e["timezone"] = meta["timezone"]
    e["host"] = "railway"; e["host_ref"] = sid; e["script_file"] = script
    e["migration_status"] = "railway-live"; e["status"] = "live"
    if also: e["also_sync"] = also
    json.dump(man, open(MANIFEST, "w"), indent=1)
    r = subprocess.run([sys.executable, str(HERE / "cc-cron-sync.py")], capture_output=True, text=True)
    print("  manifest updated + cc-cron-sync: " + (r.stdout.strip().splitlines() or ["(no output)"])[-1][:120])
    print(f"✓ {a.key} LIVE on Railway — service {sid}")

if __name__ == "__main__":
    main()
