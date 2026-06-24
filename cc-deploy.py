#!/usr/bin/env python3
"""
cc-deploy.py — deploy the Command Centre to production AND regenerate the live map.

The Vercel git webhook for PortalPeteZero/command-centre is unreliable, so CC deploys are triggered
via the API and the deployed sha is verified (see [[command-centre]] Operate). This wraps that and
then runs cc-map.py, so `Properties/Pete Command Centre/cc-map.md` can never lag a code deploy
(household-finance-system plan, Phase 1 — wiring 1 of 3: regenerate-on-deploy).

Usage: push to main first, then `python3 cc-deploy.py [--expect <sha-prefix>]`.
Exit 0 = READY + map regenerated · 2 = deploy didn't reach READY · 1 = map regen failed.
"""
import json, os, sys, time, subprocess, urllib.request, urllib.error
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
VT = open(os.path.join(VAULT, "Library/processes/secrets/vercel-token")).read().strip()
TEAM = "team_vIKK6s4RTIybcRa71woZLUlm"
REPO_ID = 1266062248
PROJECT = "command-centre"
POLL_TIMEOUT = 420  # seconds


def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"https://api.vercel.com{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {VT}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    expect = None
    if "--expect" in sys.argv:
        try:
            expect = sys.argv[sys.argv.index("--expect") + 1]
        except IndexError:
            pass

    print("cc-deploy: triggering production deploy…")
    try:
        d = api("POST", f"/v13/deployments?teamId={TEAM}&forceNew=1", {
            "name": PROJECT, "project": PROJECT, "target": "production",
            "gitSource": {"type": "github", "repoId": REPO_ID, "ref": "main"}})
    except urllib.error.HTTPError as e:
        print(f"cc-deploy: trigger failed — {e} {e.read().decode()[:300]}", file=sys.stderr)
        return 2
    dep_id = d.get("id")
    sha = (d.get("meta") or {}).get("githubCommitSha")
    print(f"  deployment {dep_id} · sha {sha} · {d.get('readyState')}")
    if expect and sha and not sha.startswith(expect):
        print(f"  ⚠ deployed sha {sha} != expected {expect}", file=sys.stderr)

    deadline = time.time() + POLL_TIMEOUT
    state = d.get("readyState")
    while state not in ("READY", "ERROR", "CANCELED") and time.time() < deadline:
        time.sleep(8)
        try:
            state = api("GET", f"/v13/deployments/{dep_id}?teamId={TEAM}").get("readyState")
        except Exception as e:
            print(f"  poll error: {e}")
        print(f"  {state}")
    if state != "READY":
        print(f"cc-deploy: deploy did NOT reach READY (state={state}) — map NOT regenerated", file=sys.stderr)
        return 2

    print(f"cc-deploy: READY (sha {sha}) — regenerating cc-map…")
    rc = subprocess.call([sys.executable, os.path.join(VAULT, "Library/processes/scripts/cc-map.py")])
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())