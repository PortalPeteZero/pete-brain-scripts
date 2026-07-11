#!/usr/bin/env python3
"""railway-watchpaths-guard.py — weekly guardian for scoped Railway watch paths.

The real failure mode watch paths can hit: a service's script gains a new dependency (or someone
hand-provisions a service) but its Railway `watchPatterns` don't include that file — so a change to
the dependency SKIPS the service and it runs stale code. This cron keeps the invariant true.

Each week it recomputes every cron service's IDEAL watchPatterns (own script + every repo file the
script names textually + build inputs — byte-identical to what cc-cron.py sets on deploy) and
compares to what is live on Railway. For any service whose live patterns DRIFTED, it (a) re-applies
the correct patterns and (b) redeploys THAT service to latest main HEAD, clearing any staleness from
the window it was mis-scoped. A normal week = 0 drift = 0 redeploys = silent. Long-running services
(no cron schedule) are left at [] (always-rebuild) by design and skipped.

Reads railway-token + github-pat from the CC secrets table via the CC service key (both env-provided
by cc-cron.py). Set GUARD_DRY=1 to report drift without changing anything.
SOP: vault_notes 'Scoped Railway watch paths (monorepo deploy)'.
"""
# CRON-META
# key: railway-watchpaths-guard
# title: Railway watch-paths guard (scoping safety net)
# what: weekly, re-apply correct watchPatterns to any drifted cron service + redeploy just those
# why: a script whose deps drift from its watch pattern would run stale code; this keeps it correct
# schedule: 30 2 * * 0
# timezone: Atlantic/Canary
# entity: Personal
# CRON-META-END
import json, os, time, urllib.request
from pathlib import Path

VAULT = os.environ.get("VAULT", "/tmp/pbs")
HERE = Path(VAULT)
SEC = HERE / "Library" / "processes" / "secrets"
CC = json.load(open(SEC / "command-centre-supabase-keys.json"))
PROJECT = "b2d89898-cc67-43a7-b900-af2c2c8e4a66"
ENVN = "7b0fd4ed-0f4a-41a4-8eb0-86e713397380"
REPO = "PortalPeteZero/pete-brain-scripts"
RW_GQL = "https://backboard.railway.app/graphql/v2"
DRY = os.environ.get("GUARD_DRY") == "1"
# long-running services stay [] (always-rebuild) by design
ALWAYS_REBUILD = {"cc-agent", "telegram-bridge"}
WATCH_BUILD_INPUTS = ["railway-bootstrap.py", "requirements.txt", "runtime.txt", "railway.json"]


def cc_secret(name):
    req = urllib.request.Request(
        f"{CC['url'].rstrip('/')}/rest/v1/secrets?select=value&name=eq.{name}",
        headers={"apikey": CC["service_role_key"], "Authorization": f"Bearer {CC['service_role_key']}"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())[0]["value"]


RW = cc_secret("railway-token")
GH = cc_secret("github-pat")


def rw(q, v=None):
    body = {"query": q}
    if v:
        body["variables"] = v
    req = urllib.request.Request(RW_GQL, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {RW}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "cc-wp-guard/1.0")
    out = json.loads(urllib.request.urlopen(req, timeout=60).read())
    if out.get("errors"):
        raise SystemExit("railway GraphQL: " + json.dumps(out["errors"])[:300])
    return out["data"]


def gh_head():
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/commits/main",
        headers={"Authorization": f"token {GH}", "Accept": "application/vnd.github+json", "User-Agent": "cc-wp-guard"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["sha"]


def ideal_patterns(script):
    """Byte-identical to cc-cron.py compute_watch_patterns — keep the two in step."""
    base = os.path.basename(script)
    pats = {base}
    try:
        txt = (HERE / base).read_text(errors="ignore")
        repo_py = {p.name for p in HERE.glob("*.py")}
        pats |= {f for f in repo_py if f in txt}
    except OSError:
        pass
    for b in WATCH_BUILD_INPUTS:
        if (HERE / b).exists():
            pats.add(b)
    return sorted(pats)


def main():
    HEAD = gh_head()
    print(f"main HEAD = {HEAD[:8]}{' (DRY)' if DRY else ''}")
    svcs = [e["node"] for e in rw(f'{{ project(id:"{PROJECT}"){{ services{{ edges{{ node{{ id name }} }} }} }} }}')["project"]["services"]["edges"]]
    drifted, fixed, failed = [], [], []
    for s in svcs:
        sid, name = s["id"], s["name"]
        if name in ALWAYS_REBUILD:
            continue
        d = rw('query($s:String!,$e:String!){ serviceInstance(serviceId:$s,environmentId:$e){ watchPatterns } variables(projectId:"%s",environmentId:$e,serviceId:$s) }' % PROJECT,
               {"s": sid, "e": ENVN})
        si = d.get("serviceInstance") or {}
        script = (d.get("variables") or {}).get("CRON_SCRIPT")
        if not script:
            continue
        live = sorted(si.get("watchPatterns") or [])
        want = ideal_patterns(script)
        if live == want:
            continue
        drifted.append((name, sorted(set(want) - set(live)), sorted(set(live) - set(want))))
        if DRY:
            continue
        try:
            rw('mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){ serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i) }',
               {"s": sid, "e": ENVN, "i": {"watchPatterns": want}})
            rw('mutation($s:String!,$e:String!,$c:String!){ serviceInstanceDeployV2(serviceId:$s,environmentId:$e,commitSha:$c) }',
               {"s": sid, "e": ENVN, "c": HEAD})
            fixed.append(name)
            time.sleep(0.6)
        except Exception as ex:
            failed.append((name, str(ex)[:80]))
    if drifted:
        print(f"DRIFT on {len(drifted)} service(s):")
        for n, add, rem in drifted:
            print(f"  {n}: +{add} -{rem}")
    print(f"railway-watchpaths-guard: {len(drifted)} drifted, {len(fixed)} fixed+redeployed, {len(failed)} failed"
          + (f" | FAILED {failed}" if failed else ""))


if __name__ == "__main__":
    main()
