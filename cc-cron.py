#!/usr/bin/env python3
"""cc-cron.py — THE one command for crons. Author the schedule in the script's `# CRON-META` header
(it lives in git WITH the code); `public.crons` is the live registry every dashboard reads; Railway runs
them. One tool binds the three so nothing drifts and no session ever hand-converts a timezone or reads
deploy internals again.

  cc-cron.py list                         — every cron, from public.crons
  cc-cron.py deploy   <key> [--script F] [--run]
                                          — create/update the Railway service from the script's CRON-META,
                                            arm its schedule (local→UTC), deploy HEAD, write public.crons
  cc-cron.py set-schedule <key> "<local cron>"
                                          — rewrite the script's CRON-META schedule + Railway + public.crons
  cc-cron.py pause    <key>               — stop it firing (never-fire schedule) + enabled=false
  cc-cron.py resume   <key>               — re-arm from the stored local schedule + enabled=true
  cc-cron.py retire   <key>               — delete the Railway service + mark binned in public.crons
  cc-cron.py status  [<key>]              — refresh live Railway status + freshness probe → public.crons (+ cron_events)

This SUPERSEDES crons-manifest.json + cc-cron-sync.py + railway-deploy.py + railway-sync-repo.py.
The schedule in CRON-META is LOCAL (Atlantic/Canary); cc-cron.py converts to the UTC Railway needs
(cron_tz, list/range-aware). Code deploys are automatic on `git push` (Railway GitHub trigger); `deploy`
is for creating a service / arming a schedule, not for shipping code.
"""
import sys, os, json, time, argparse, subprocess, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEC = HERE / "Library" / "processes" / "secrets"
PROJECT = "b2d89898-cc67-43a7-b900-af2c2c8e4a66"
ENVN = "7b0fd4ed-0f4a-41a4-8eb0-86e713397380"
REPO = "PortalPeteZero/pete-brain-scripts"
RW_GQL = "https://backboard.railway.app/graphql/v2"
NEVER_FIRE = "0 0 30 2 *"            # Feb 30 never exists → a valid cron that never runs (= paused)
TERMINAL = {"SUCCESS", "FAILED", "CRASHED", "REMOVED"}
# crons that write a CC table → read its freshness for a TRUE last-run (Railway cron runs reuse the
# deployment context and don't surface a fresh deployment row). Ported from cc-cron-sync.py.
FRESHNESS_PROBE = {"data-map-cron": ("data_map", "updated_at"), "drive-changes-watch": ("drive_files", "indexed_at"),
                   "cc-calendar-sync": ("calendar_events", "synced_at")}

sys.path.insert(0, str(HERE))
from cron_tz import local_to_utc

CC = json.load(open(SEC / "command-centre-supabase-keys.json"))
CC_URL, CC_KEY = CC["url"].rstrip("/"), CC["service_role_key"]

# ---------- CC Supabase (PostgREST) ----------
def sb(method, path, body=None, prefer=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{CC_URL}/rest/v1/{path}", data=data, method=method)
    req.add_header("apikey", CC_KEY); req.add_header("Authorization", f"Bearer {CC_KEY}")
    req.add_header("Content-Type", "application/json")
    if prefer: req.add_header("Prefer", prefer)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode(); return json.loads(txt) if txt else []
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Supabase {method} {path}: {e.code} {e.read().decode()[:200]}")

RW_TOKEN = (sb("GET", "secrets?select=value&name=eq.railway-token") or [{}])[0].get("value")
GH = (SEC / "github-pat").read_text().strip() if (SEC / "github-pat").exists() else None

# ---------- Railway (GraphQL) ----------
def rw(q, v=None):
    body = {"query": q}
    if v is not None: body["variables"] = v
    req = urllib.request.Request(RW_GQL, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {RW_TOKEN}"); req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "cc-cron/1.0")          # Railway edge 403s the default urllib UA
    try:
        out = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"railway HTTP {e.code}: {e.read().decode()[:400]}")
    if out.get("errors"): raise SystemExit("railway GraphQL: " + json.dumps(out["errors"])[:400])
    return out["data"]

def find_service(name):
    d = rw('query($p:String!){ project(id:$p){ services{ edges{ node{ id name } } } } }', {"p": PROJECT})
    for e in d["project"]["services"]["edges"]:
        if e["node"]["name"] == name: return e["node"]["id"]
    return None

def create_service(name):
    return rw('mutation($i:ServiceCreateInput!){ serviceCreate(input:$i){ id } }',
              {"i": {"projectId": PROJECT, "name": name, "source": {"repo": REPO}}})["serviceCreate"]["id"]

def var_upsert(sid, name, value):
    rw('mutation($i:VariableUpsertInput!){ variableUpsert(input:$i) }',
       {"i": {"projectId": PROJECT, "environmentId": ENVN, "serviceId": sid, "name": name, "value": value}})

def set_instance(sid, fields):
    rw('mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){ serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i) }',
       {"s": sid, "e": ENVN, "i": fields})

def ensure_trigger(sid):
    """Best-effort: make sure a main-branch GitHub auto-deploy trigger exists (so future pushes redeploy).
    serviceCreate(source:repo) usually creates one; this is belt-and-braces and never fatal."""
    try:
        rw('mutation($i:DeploymentTriggerCreateInput!){ deploymentTriggerCreate(input:$i){ id } }',
           {"i": {"projectId": PROJECT, "environmentId": ENVN, "serviceId": sid,
                  "branch": "main", "provider": "github", "repository": REPO}})
        return "created"
    except SystemExit as ex:
        return f"skipped ({str(ex)[:80]})"

def deploy_head(sid, sha):
    return rw('mutation($s:String!,$e:String!,$c:String!){ serviceInstanceDeployV2(serviceId:$s,environmentId:$e,commitSha:$c) }',
              {"s": sid, "e": ENVN, "c": sha})["serviceInstanceDeployV2"]

def poll(did):
    st = "?"
    for _ in range(100):
        st = rw('query($id:String!){ deployment(id:$id){ status } }', {"id": did})["deployment"]["status"]
        if st in TERMINAL: return st
        time.sleep(5)
    return "TIMEOUT(" + st + ")"

def run_logs(sid):
    try:
        d = rw('query($s:String!){ service(id:$s){ deployments(first:1){ edges{ node{ id } } } } }', {"s": sid})
        did = d["service"]["deployments"]["edges"][0]["node"]["id"]
        return rw('query($d:String!){ deploymentLogs(deploymentId:$d, limit:40){ message timestamp } }', {"d": did}).get("deploymentLogs") or []
    except Exception as ex:
        return [{"message": f"(log fetch failed: {ex})", "timestamp": ""}]

def service_delete(sid):
    rw('mutation($id:String!){ serviceDelete(id:$id) }', {"id": sid})

def deploy_status(sid):
    try:
        d = rw('query($s:String!,$e:String!){ serviceInstance(serviceId:$s,environmentId:$e){ latestDeployment{ status } } }', {"s": sid, "e": ENVN})
        return ((d["serviceInstance"] or {}).get("latestDeployment") or {}).get("status")
    except Exception:
        return None

def gh_head():
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/commits/main",
        headers={"Authorization": f"token {GH}", "Accept": "application/vnd.github+json", "User-Agent": "cc-cron"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())["sha"]

# ---------- CRON-META (the authored source — lives with the code) ----------
META_FIELDS = {"what": "what", "why": "why", "reads": "consumes", "writes": "produces",
               "entity": "entity_slug", "report": "report_module"}
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
            if k.strip().lower() in {*META_FIELDS, "schedule", "timezone", "key", "secrets", "title", "host"}:
                meta[k.strip().lower()] = v.strip()
    return meta

def resolve_secret_env(name):
    """Map a CRON-META `# secrets:` token to the env value railway-bootstrap expects (it materialises the
    file from the env var). Known clean-name secrets + the generic SECRETFILE__<name> convention."""
    known = {"GOOGLE_SA_JSON": SEC / "google-seo-service-account.json",
             "GARMIN_TOKENS_JSON": SEC / "garminconnect-tokens" / "garmin_tokens.json"}
    if name in known:
        f = known[name]
        if not f.exists(): raise SystemExit(f"secret source missing: {f}")
        return f.read_text()
    if name.startswith("SECRETFILE__"):
        fn = name[len("SECRETFILE__"):].replace("__", ".")
        f = SEC / fn
        if not f.exists(): raise SystemExit(f"secret source missing: {f}")
        return f.read_text()
    raise SystemExit(f"unknown secret token in CRON-META: {name} (expected GOOGLE_SA_JSON / GARMIN_TOKENS_JSON / SECRETFILE__<file>)")

# ---------- public.crons registry ----------
def get_cron(key):
    rows = sb("GET", f"crons?key=eq.{key}&select=*")
    return rows[0] if rows else None

def write_cron(row):
    sb("POST", "crons?on_conflict=key", [row], prefer="resolution=merge-duplicates,return=minimal")

def log_event(cron_key, kind, detail=""):
    try: sb("POST", "cron_events", [{"cron_key": cron_key, "kind": kind, "detail": detail}], prefer="return=minimal")
    except SystemExit: pass

def now_iso():
    # avoid Date.now-style banned calls in scripts isn't a concern here (plain python), but keep it explicit
    import datetime; return datetime.datetime.now(datetime.timezone.utc).isoformat()

# ---------- verbs ----------
def cmd_list(_a):
    rows = sb("GET", "crons?select=key,host,schedule,schedule_local,status,enabled,script_file&order=key")
    print(f"{'KEY':30} {'HOST':12} {'SCHED(UTC)':16} {'STATUS':9} EN  SCRIPT")
    for r in rows:
        print(f"{(r['key'] or '')[:30]:30} {(r.get('host') or '')[:12]:12} {(r.get('schedule') or '-')[:16]:16} "
              f"{(r.get('status') or '?')[:9]:9} {'Y' if r.get('enabled') else 'n'}   {r.get('script_file') or ''}")
    print(f"\n{len(rows)} crons  ·  registry = public.crons  ·  dashboard = /m/automations-log")

def cmd_deploy(a):
    key = a.key
    existing = get_cron(key)
    script = a.script or (existing or {}).get("script_file")
    if not script: raise SystemExit(f"no script_file for {key} — pass --script NAME.py for a brand-new cron")
    meta = parse_cron_meta(script)
    if not meta: raise SystemExit(f"{script} has no # CRON-META block — add one (cc-cron.py is CRON-META-driven). "
                                  "Required: what/why/schedule/timezone (+ secrets: if it needs Google/Garmin/etc).")
    sched_local = meta.get("schedule")
    tz = meta.get("timezone", "Atlantic/Canary")
    is_service = not sched_local                          # no schedule → long-running service (e.g. telegram-bridge)
    secrets = [s.strip() for s in (meta.get("secrets") or "").split(",") if s.strip()]
    print(f"=== cc-cron deploy {key} ===")
    print(f"  script={script}  schedule_local={sched_local or '(service — none)'}  tz={tz}  secrets={secrets or '-'}")

    HEAD = gh_head(); print(f"  repo HEAD = {HEAD[:8]}")
    sid = find_service(key)
    print(f"  service {'exists ' + sid[:8] if sid else 'creating'}")
    if not sid: sid = create_service(key)
    trig = ensure_trigger(sid); print(f"  auto-deploy trigger: {trig}")

    # env: CC keys always, TZ + CRON_SCRIPT (basename), restartPolicy=NEVER for crons, + declared secrets
    var_upsert(sid, "CC_SUPABASE_URL", CC["url"]); var_upsert(sid, "CC_SUPABASE_SERVICE_KEY", CC["service_role_key"])
    var_upsert(sid, "TZ", tz); var_upsert(sid, "CRON_SCRIPT", os.path.basename(script))
    for s in secrets:
        var_upsert(sid, s, resolve_secret_env(s)); print(f"  secret {s} set")
    if not is_service:
        set_instance(sid, {"restartPolicyType": "NEVER"})
    print("  env set: CC url/key, TZ, CRON_SCRIPT" + ("" if is_service else ", restartPolicy=NEVER"))

    utc = None
    if is_service:
        print("  (service — no cronSchedule)")
    elif a.run:
        set_instance(sid, {"cronSchedule": None}); print("  cronSchedule cleared → runs once on deploy (runtime proof)")
    else:
        utc, off, cross = local_to_utc(sched_local, tz); set_instance(sid, {"cronSchedule": utc})
        print(f"  cronSchedule armed BEFORE deploy: {sched_local} → UTC '{utc}' (UTC+{off}{' ⚠crosses-midnight' if cross else ''})")

    did = deploy_head(sid, HEAD); print(f"  deploy {did[:8]} @ {HEAD[:8]}")
    st = poll(did); print(f"  deploy status: {st}")
    if st != "SUCCESS": raise SystemExit(f"deploy not green ({st}) — check logs")

    if a.run and not is_service:
        time.sleep(18); print("  runtime logs (tail):")
        for L in run_logs(sid)[-12:]:
            print(f"    {str(L.get('timestamp',''))[:19]} {str(L.get('message',''))[:160]}")
        utc, off, cross = local_to_utc(sched_local, tz); set_instance(sid, {"cronSchedule": utc})
        print(f"  ✓ cronSchedule armed: {sched_local} → UTC '{utc}'")

    # write the registry row from CRON-META (the manifest/sync replacement)
    row = {"key": key, "host": "railway", "host_ref": sid, "script_file": script,
           "schedule_local": sched_local, "schedule": utc, "timezone": tz,
           "enabled": True, "status": "live", "migration_status": "railway-live", "updated_at": now_iso()}
    for mk, ek in META_FIELDS.items():
        if meta.get(mk): row[ek] = meta[mk]
    row["title"] = meta.get("title") or key            # title is NOT NULL in public.crons
    write_cron(row)
    log_event(key, "created" if not existing else "deployed",
              f"railway {sched_local or 'service'}{' → '+utc if utc else ''}")
    print(f"✓ {key} LIVE on Railway — service {sid} · written to public.crons")

def cmd_set_schedule(a):
    key, local = a.key, a.schedule
    row = get_cron(key) or {}
    script = row.get("script_file")
    utc, off, cross = local_to_utc(local, "Atlantic/Canary")
    sid = row.get("host_ref") or find_service(key)
    if not sid: raise SystemExit(f"no Railway service for {key}")
    set_instance(sid, {"cronSchedule": utc})
    # rewrite the CRON-META schedule line in the script so a redeploy can't revert it
    if script and (HERE / script).exists():
        p = HERE / script; lines = p.read_text().splitlines()
        for i, ln in enumerate(lines):
            if ln.strip().lower().lstrip("# ").startswith("schedule:") and "CRON-META" not in ln:
                lines[i] = f"# schedule: {local}"; break
        p.write_text("\n".join(lines) + "\n")
        print(f"  CRON-META schedule line rewritten in {script} — commit+push so it persists")
    write_cron({"key": key, "schedule": utc, "schedule_local": local, "updated_at": now_iso()})
    log_event(key, "schedule-changed", f"{row.get('schedule_local') or '?'} → {local} (UTC {utc})")
    print(f"✓ {key}: {local} (Atlantic/Canary) → Railway UTC '{utc}' + public.crons updated")

def cmd_pause(a):
    row = get_cron(a.key) or {}
    sid = row.get("host_ref") or find_service(a.key)
    if not sid: raise SystemExit(f"no Railway service for {a.key}")
    set_instance(sid, {"cronSchedule": NEVER_FIRE})
    write_cron({"key": a.key, "enabled": False, "status": "frozen", "updated_at": now_iso()})
    log_event(a.key, "disabled", "paused (never-fire schedule)")
    print(f"✓ {a.key} PAUSED — never-fire schedule set, enabled=false (resume re-arms from schedule_local)")

def cmd_resume(a):
    row = get_cron(a.key)
    if not row: raise SystemExit(f"{a.key} not in public.crons")
    local = row.get("schedule_local")
    if not local: raise SystemExit(f"{a.key} has no schedule_local to resume from")
    sid = row.get("host_ref") or find_service(a.key)
    utc, off, cross = local_to_utc(local, row.get("timezone") or "Atlantic/Canary")
    set_instance(sid, {"cronSchedule": utc})
    write_cron({"key": a.key, "schedule": utc, "enabled": True, "status": "live", "updated_at": now_iso()})
    log_event(a.key, "enabled", f"resumed → {local} (UTC {utc})")
    print(f"✓ {a.key} RESUMED — {local} → UTC '{utc}', enabled=true")

def cmd_retire(a):
    row = get_cron(a.key) or {}
    sid = row.get("host_ref") or find_service(a.key)
    write_cron({"key": a.key, "status": "binned", "enabled": False, "migration_status": "binned", "updated_at": now_iso()})
    if sid:
        try: service_delete(sid); print(f"  ✓ Railway service {sid[:8]} deleted")
        except SystemExit as ex: print(f"  ⚠ service delete failed (binned still applied): {str(ex)[:120]}")
    log_event(a.key, "retired", "service deleted + binned")
    print(f"✓ {a.key} RETIRED — binned in public.crons, automations page in sync")

def cmd_status(a):
    keys = [a.key] if a.key else [r["key"] for r in sb("GET", "crons?select=key&host=eq.railway&order=key")]
    services = {}
    d = rw('query($p:String!){ project(id:$p){ services{ edges{ node{ id name } } } } }', {"p": PROJECT})
    for e in d["project"]["services"]["edges"]: services[e["node"]["name"]] = e["node"]["id"]
    for key in keys:
        row = get_cron(key) or {}
        sid = row.get("host_ref") or services.get(key)
        patch = {"key": key, "updated_at": now_iso()}
        if sid:
            st = deploy_status(sid)
            if st: patch["last_status"] = "SUCCESS" if st == "SUCCESS" else st
        probe = FRESHNESS_PROBE.get(key)
        if probe:
            tbl, col = probe
            rows = sb("GET", f"{tbl}?select={col}&order={col}.desc&limit=1")
            if rows and rows[0].get(col):
                patch["last_run_at"] = rows[0][col]; patch.setdefault("last_status", "SUCCESS"); patch["status"] = "ok"
        write_cron(patch)
        print(f"  {key:30} deploy={deploy_status(sid) if sid else '-':10} last_run={patch.get('last_run_at','-')}")
    print(f"✓ status refreshed for {len(keys)} cron(s)")

def main():
    ap = argparse.ArgumentParser(prog="cc-cron.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    d = sub.add_parser("deploy"); d.add_argument("key"); d.add_argument("--script"); d.add_argument("--run", action="store_true")
    s = sub.add_parser("set-schedule"); s.add_argument("key"); s.add_argument("schedule")
    for v in ("pause", "resume", "retire"):
        sv = sub.add_parser(v); sv.add_argument("key")
    st = sub.add_parser("status"); st.add_argument("key", nargs="?")
    a = ap.parse_args()
    {"list": cmd_list, "deploy": cmd_deploy, "set-schedule": cmd_set_schedule,
     "pause": cmd_pause, "resume": cmd_resume, "retire": cmd_retire, "status": cmd_status}[a.cmd](a)

if __name__ == "__main__":
    main()
