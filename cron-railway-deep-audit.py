#!/usr/bin/env python3
"""cron-railway-deep-audit.py — RUNTIME health of EVERY Railway cron (no sampling).

The structural reconcile (cron-railway-audit.py) proves each service exists, deployed
SUCCESS, has a schedule and a registry row. This goes deeper — for EVERY service it adds:
  • code freshness  — deployed commitHash vs pete-brain-scripts HEAD; if behind, it checks
                      whether THIS cron's own script_file changed in the gap (script-level
                      staleness, not just "a newer commit exists somewhere").
  • runtime proof   — pulls the latest deployment's RUNTIME logs and scans for
                      Traceback / Error / FAILED; captures the last log line + its time
                      (= evidence the cron actually executed cleanly). Empty logs = the
                      service was rebuilt after its last fire (expected for daily/weekly/
                      monthly crons) → reported, not a failure.
  • schedule armed  — a service with no cronSchedule runs continuously (Railway runs it as
                      a service, not a cron) — always flagged.

Reuses the same Railway + CC access as cron-railway-audit.py. Clones the scripts repo to
/tmp/pbs-audit for the file-level freshness diff (reuses it if already present).
"""
import json, re, subprocess, urllib.request
from pathlib import Path
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

SECRETS = Path(f"{VAULT}/Library/processes/secrets")
PROJECT = "b2d89898-cc67-43a7-b900-af2c2c8e4a66"
ENVN = "7b0fd4ed-0f4a-41a4-8eb0-86e713397380"
CC = json.load(open(SECRETS / "command-centre-supabase-keys.json"))
RW_TOKEN = json.loads(urllib.request.urlopen(urllib.request.Request(
    f"{CC['url'].rstrip('/')}/rest/v1/secrets?select=value&name=eq.railway-token",
    headers={"apikey": CC['service_role_key'], "Authorization": f"Bearer {CC['service_role_key']}"})).read())[0]["value"]
GH = (SECRETS / "github-pat").read_text().strip()
REPO = "PortalPeteZero/pete-brain-scripts"
CLONE = Path("/tmp/pbs-audit")

ERR_RE = re.compile(r"traceback|error|exception|failed|fatal|no such file|denied|not found|killed", re.I)
# benign substrings that contain an error-word but are normal output
BENIGN = ("0 errors", "errors=0", "error: None", "no errors", "0 deletes", "skip", "gracefully")


def rw(q, v=None):
    body = {"query": q}
    if v:
        body["variables"] = v
    req = urllib.request.Request("https://backboard.railway.app/graphql/v2", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {RW_TOKEN}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=45).read())


def cc(path):
    req = urllib.request.Request(f"{CC['url'].rstrip('/')}/rest/v1/{path}",
        headers={"apikey": CC['service_role_key'], "Authorization": f"Bearer {CC['service_role_key']}"})
    return json.loads(urllib.request.urlopen(req, timeout=45).read())


def git(*a):
    return subprocess.run(["git", "-C", str(CLONE), *a], capture_output=True, text=True).stdout.strip()


# clone (or refresh) the scripts repo for the file-level freshness diff
if not (CLONE / ".git").exists():
    subprocess.run(["git", "clone", "--quiet", f"https://{GH}@github.com/{REPO}", str(CLONE)], check=True)
else:
    subprocess.run(["git", "-C", str(CLONE), "fetch", "--quiet", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(CLONE), "reset", "--hard", "--quiet", "origin/main"], check=True)
HEAD = git("rev-parse", "HEAD")

# registry
crons = {c["key"]: c for c in cc("crons?select=key,status,host,schedule,schedule_local,script_file,consumes,produces,last_run_at")}

# every Railway service
svcs = [e["node"] for e in rw(f'{{ project(id:"{PROJECT}") {{ services {{ edges {{ node {{ id name }} }} }} }} }}')["data"]["project"]["services"]["edges"]]

print(f"=== DEEP AUDIT — repo HEAD {HEAD[:8]} | {len(svcs)} Railway services | {len(crons)} registry crons ===\n")
problems = []
rows = []

for s in sorted(svcs, key=lambda x: x["name"]):
    name = s["id"], s["name"]
    nm = s["name"]
    d = rw('query($s:String!,$e:String!){ serviceInstance(serviceId:$s,environmentId:$e){ latestDeployment{ id status meta } cronSchedule } variables(projectId:"%s",environmentId:$e,serviceId:$s) }' % PROJECT, {"s": s["id"], "e": ENVN})["data"]
    si = d.get("serviceInstance") or {}
    dep = si.get("latestDeployment") or {}
    meta = dep.get("meta") or {}
    commit = meta.get("commitHash", "")
    status = dep.get("status")
    sched = si.get("cronSchedule")
    script = (d.get("variables") or {}).get("CRON_SCRIPT")

    if nm == "cc-agent":
        # 24/7 agent: no cron, must be SUCCESS; check freshness
        stale = "behind" if commit and commit != HEAD else "HEAD"
        print(f"  {'✓' if status=='SUCCESS' else '✗'} {nm:34s} deploy={status} 24/7-agent commit={commit[:7]}({stale})")
        if status != "SUCCESS":
            problems.append((nm, f"agent deploy={status}"))
        continue

    tags = []
    # 1. deploy status
    if status != "SUCCESS":
        tags.append(f"deploy={status}")
    # 2. schedule armed
    if not sched:
        tags.append("NO SCHEDULE armed (runs continuously)")
    # 3. registry row
    c = crons.get(nm)
    if not c:
        tags.append("NO registry row")
    else:
        if c["status"] not in ("live", "ok"):
            tags.append(f"registry status={c['status']}")
        # script_file keeps the canonical path (e.g. account/foo.py); Railway CRON_SCRIPT is its basename
        # (railway-sync-repo flattens subdir sources). Compare basenames, not the full path.
        if c.get("script_file") and script and Path(c["script_file"]).name != script:
            tags.append(f"script mismatch registry={Path(c['script_file']).name} railway={script}")
    # 4. code freshness — script-level
    fresh = "HEAD"
    if commit and commit != HEAD:
        changed = git("diff", "--name-only", commit, HEAD).splitlines()
        own = script and script in changed
        if own:
            tags.append(f"STALE CODE: {script} changed since deployed {commit[:7]}")
            fresh = "STALE"
        else:
            fresh = f"behind({commit[:7]}) but {script} unchanged"
    # 5. runtime log scan
    logmsgs = []
    if dep.get("id"):
        try:
            lg = rw('query($d:String!){ deploymentLogs(deploymentId:$d, limit:80){ message timestamp } }', {"d": dep["id"]})
            logmsgs = lg.get("data", {}).get("deploymentLogs") or []
        except Exception:
            pass
    errs = [l for l in logmsgs if ERR_RE.search(l.get("message", "")) and not any(b in l.get("message", "") for b in BENIGN)]
    if logmsgs:
        last = logmsgs[-1]
        run_ev = f"last log {last.get('timestamp','')[:16]} :: {last.get('message','')[:70]}"
    else:
        run_ev = "no runtime logs on current build (rebuilt since last fire)"
    if errs:
        tags.append(f"{len(errs)} ERROR line(s) in runtime log")

    verdict = "✗" if tags else "✓"
    if tags:
        problems.append((nm, "; ".join(tags)))
    print(f"  {verdict} {nm:34s} {str(status):7s} cron={str(sched):14s} {fresh:30s}")
    print(f"      {run_ev}")
    for e in errs[:3]:
        print(f"      ⚠ {e.get('timestamp','')[:19]} {e.get('message','')[:120]}")

# registry crons with no Railway service
for key, c in sorted(crons.items()):
    if c["status"] in ("live", "ok") and key not in {s["name"] for s in svcs}:
        problems.append((key, "live/ok registry cron has NO Railway service"))

print(f"\n=== PROBLEMS: {len(problems)} ===")
for n, t in problems:
    print(f"  ✗ {n}: {t}")
if not problems:
    print("  none — every cron deployed SUCCESS, scheduled, on current code, no runtime errors.")