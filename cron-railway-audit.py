#!/usr/bin/env python3
"""cron-railway-audit.py — EXHAUSTIVE Railway ↔ registry audit.

Checks EVERY Railway service against public.crons (no sampling): latest deploy status, the armed
cron schedule, the CRON_SCRIPT it runs, and the registry's reads/writes (consumes/produces). Flags
every mismatch: a Railway service with no registry row, a live/ok cron with no Railway service, a
deploy that isn't SUCCESS, a missing schedule, a missing consumes/produces.
"""
import json, urllib.request
from pathlib import Path
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

SECRETS = Path(f"{VAULT}/Library/processes/secrets")
PROJECT = "b2d89898-cc67-43a7-b900-af2c2c8e4a66"
ENVN = "7b0fd4ed-0f4a-41a4-8eb0-86e713397380"
CC = json.load(open(SECRETS / "command-centre-supabase-keys.json"))
RW_TOKEN = json.loads(urllib.request.urlopen(urllib.request.Request(
    f"{CC['url'].rstrip('/')}/rest/v1/secrets?select=value&name=eq.railway-token",
    headers={"apikey": CC['service_role_key'], "Authorization": f"Bearer {CC['service_role_key']}"})).read())[0]["value"]


def rw(q, v=None):
    body = {"query": q}
    if v:
        body["variables"] = v
    req = urllib.request.Request("https://backboard.railway.app/graphql/v2", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {RW_TOKEN}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=45).read())["data"]


def cc(path):
    req = urllib.request.Request(f"{CC['url'].rstrip('/')}/rest/v1/{path}",
        headers={"apikey": CC['service_role_key'], "Authorization": f"Bearer {CC['service_role_key']}"})
    return json.loads(urllib.request.urlopen(req, timeout=45).read())


# 1. every Railway service
svcs = [e["node"] for e in rw(f'{{ project(id:"{PROJECT}") {{ services {{ edges {{ node {{ id name }} }} }} }} }}')["project"]["services"]["edges"]]
# 2. per-service: latest deploy status + armed cron schedule + CRON_SCRIPT var
detail = {}
for s in svcs:
    d = rw('query($s:String!,$e:String!){ serviceInstance(serviceId:$s,environmentId:$e){ latestDeployment{ status } cronSchedule } variables(projectId:"%s",environmentId:$e,serviceId:$s) }' % PROJECT, {"s": s["id"], "e": ENVN})
    si = d.get("serviceInstance") or {}
    detail[s["name"]] = {"id": s["id"][:8], "deploy": (si.get("latestDeployment") or {}).get("status"),
                         "cron": si.get("cronSchedule"), "script": (d.get("variables") or {}).get("CRON_SCRIPT")}
# 3. registry
crons = {c["key"]: c for c in cc("crons?select=key,status,host,host_ref,schedule,schedule_local,script_file,consumes,produces,enabled")}

print(f"=== RAILWAY SERVICES: {len(svcs)} | public.crons: {len(crons)} ===\n")
problems = []

print(">>> EVERY RAILWAY SERVICE (vs registry):")
for name in sorted(detail):
    d = detail[name]
    c = crons.get(name)
    tags = []
    if name == "cc-agent":
        print(f"  ✓ {name:34s} deploy={d['deploy']} (24/7 agent, no cron — not in registry, expected)")
        continue
    if not c:
        tags.append("⚠ NO REGISTRY ROW")
    else:
        if c["status"] not in ("live", "ok"):
            tags.append(f"⚠ registry status={c['status']} (not live/ok)")
        if c["host"] != "railway":
            tags.append(f"⚠ registry host={c['host']} (not railway)")
        if not d["cron"]:
            tags.append("⚠ NO CRON SCHEDULE armed")
        if not c.get("consumes"):
            tags.append("⚠ registry: consumes empty")
        if not c.get("produces"):
            tags.append("⚠ registry: produces empty")
    if d["deploy"] != "SUCCESS":
        tags.append(f"⚠ deploy={d['deploy']}")
    flag = "  ".join(tags) if tags else "OK"
    if tags:
        problems.append((name, tags))
    print(f"  {'✗' if tags else '✓'} {name:34s} deploy={str(d['deploy']):8s} cron={str(d['cron']):14s} script={d['script']}  {flag}")

print("\n>>> EVERY live/ok REGISTRY CRON (has a Railway service?):")
for key in sorted(crons):
    c = crons[key]
    if c["status"] not in ("live", "ok"):
        continue
    if key not in detail:
        problems.append((key, ["⚠ NO RAILWAY SERVICE for a live/ok cron"]))
        print(f"  ✗ {key:34s} status={c['status']} — NO RAILWAY SERVICE")
    else:
        print(f"  ✓ {key:34s} status={c['status']:5s} reads={(c.get('consumes') or '—')[:40]:40s} writes={(c.get('produces') or '—')[:45]}")

print(f"\n=== PROBLEMS: {len(problems)} ===")
for n, t in problems:
    print(f"  {n}: {'; '.join(t)}")
print("\n=== status counts ===")
from collections import Counter
print(dict(Counter(c["status"] for c in crons.values())))