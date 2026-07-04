#!/usr/bin/env python3
"""
Vercel API utility for Sygma projects.
Replaces the unreliable Vercel MCP connector with direct API calls.

Usage:
  python3 Library/processes/scripts/vercel-api.py <command> [args]

Commands:
  deployments [project_id]        List recent deployments
  deployment <deployment_id>      Get deployment details
  logs <deployment_id>            Get build logs
  projects                        List all projects
  project <project_id>            Get project details
  status <deployment_id>          Quick status check (READY/ERROR/BUILDING)
  latest [project_id]             Get latest deployment status
  deploy-for-sha <sha> [proj_id]  Map a pushed commit SHA -> its deploy readyState
                                  (--json for machine output; exit 0=READY 2=not-ready 3=no-deploy)

Environment:
  Token fetched from the CC secrets table (name: vercel-token) via _cc_secret().
  Team: team_vIKK6s4RTIybcRa71woZLUlm
"""

import json
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

def _cc_secret(name):
    """Fetch a secret from the CC secrets table (cloud). Bootstrap CC key: env first, else ~/.config mirror."""
    import os, json, urllib.request
    url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        k = json.load(open(os.path.expanduser("~/.config/pete-secrets/command-centre-supabase-keys.json")))
        url, key = k["url"], k["service_role_key"]
    req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/secrets?select=value&name=eq.{name}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())[0]["value"]

TOKEN = _cc_secret("vercel-token")
TEAM_ID = "team_vIKK6s4RTIybcRa71woZLUlm"
BASE = "https://api.vercel.com"

# Known project IDs
PROJECTS = {
    "sygma-solutions-nextjs": "prj_nt0llwMCvapPlnRM5ebr3hf6si4T",
    "sygma-internal-hub": None,  # Add when known
}


def api(path, params=None):
    """Make a GET request to the Vercel API."""
    url = f"{BASE}{path}"
    if params is None:
        params = {}
    params["teamId"] = TEAM_ID
    qs = urllib.parse.urlencode(params)
    url = f"{url}?{qs}"

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"ERROR {e.code}: {body[:500]}", file=sys.stderr)
        sys.exit(1)


def ts_to_str(ts):
    """Convert millisecond timestamp to readable string."""
    if not ts:
        return "N/A"
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")


def cmd_deployments(project_id=None):
    """List recent deployments."""
    params = {"limit": "10"}
    if project_id:
        params["projectId"] = project_id
    data = api("/v6/deployments", params)

    for d in data.get("deployments", []):
        sha = d.get("meta", {}).get("githubCommitSha", "")[:7]
        msg = d.get("meta", {}).get("githubCommitMessage", "").split("\n")[0][:60]
        state = d.get("state", "UNKNOWN")
        created = ts_to_str(d.get("created"))
        marker = "+" if state == "READY" else "x" if state == "ERROR" else "~"
        print(f"[{marker}] {state:8s} {created} {sha} {msg}")
        print(f"    ID: {d['uid']}  URL: {d.get('url', 'N/A')}")


def cmd_deployment(deployment_id):
    """Get deployment details."""
    data = api(f"/v13/deployments/{deployment_id}")
    d = data
    print(f"ID:      {d.get('id', 'N/A')}")
    print(f"State:   {d.get('readyState', d.get('state', 'UNKNOWN'))}")
    print(f"URL:     {d.get('url', 'N/A')}")
    print(f"Created: {ts_to_str(d.get('createdAt', d.get('created')))}")
    print(f"Ready:   {ts_to_str(d.get('ready'))}")
    commit = d.get("meta", {})
    print(f"Commit:  {commit.get('githubCommitSha', 'N/A')[:7]}")
    print(f"Message: {commit.get('githubCommitMessage', 'N/A').split(chr(10))[0][:80]}")
    aliases = d.get("alias", [])
    if aliases:
        print(f"Aliases: {', '.join(aliases[:3])}")


def cmd_logs(deployment_id):
    """Get build logs."""
    data = api(f"/v2/deployments/{deployment_id}/events", {"limit": "100"})
    events = data if isinstance(data, list) else data.get("events", data.get("logs", []))

    for event in events:
        text = event.get("text", "")
        if not text.strip():
            continue
        level = event.get("level", "")
        etype = event.get("type", "")
        prefix = "ERR " if level == "error" or etype == "stderr" else "    "
        if level == "warning":
            prefix = "WARN"
        print(f"{prefix} {text}")


def cmd_projects():
    """List all projects."""
    data = api("/v9/projects", {"limit": "50"})
    for p in data.get("projects", []):
        framework = p.get("framework") or "unknown"
        updated = ts_to_str(p.get("updatedAt"))
        print(f"{p['name']:40s} {framework:15s} {updated}")
        print(f"    ID: {p['id']}")


def cmd_project(project_id):
    """Get project details."""
    data = api(f"/v9/projects/{project_id}")
    print(f"Name:      {data.get('name', 'N/A')}")
    print(f"ID:        {data.get('id', 'N/A')}")
    print(f"Framework: {data.get('framework', 'N/A')}")
    print(f"Updated:   {ts_to_str(data.get('updatedAt'))}")
    print(f"Node:      {data.get('nodeVersion', 'N/A')}")
    domains = data.get("alias", data.get("targets", {}).get("production", {}).get("alias", []))
    if isinstance(domains, list):
        print(f"Domains:   {', '.join(d if isinstance(d, str) else d.get('domain', '') for d in domains[:5])}")


def cmd_status(deployment_id):
    """Quick status check."""
    data = api(f"/v13/deployments/{deployment_id}")
    state = data.get("readyState", data.get("state", "UNKNOWN"))
    print(state)


def cmd_latest(project_id=None):
    """Get latest deployment status."""
    params = {"limit": "1", "target": "production"}
    if project_id:
        params["projectId"] = project_id
    data = api("/v6/deployments", params)
    deps = data.get("deployments", [])
    if not deps:
        print("No deployments found")
        return

    d = deps[0]
    state = d.get("state", "UNKNOWN")
    sha = d.get("meta", {}).get("githubCommitSha", "")[:7]
    msg = d.get("meta", {}).get("githubCommitMessage", "").split("\n")[0][:60]
    created = ts_to_str(d.get("created"))

    print(f"State:   {state}")
    print(f"Commit:  {sha}")
    print(f"Message: {msg}")
    print(f"Created: {created}")
    print(f"ID:      {d['uid']}")

    if state == "ERROR":
        print("\n--- Build errors ---")
        cmd_logs(d["uid"])


def cmd_deploy_for_sha(sha, project_id=None, as_json=False):
    """Map an arbitrary pushed commit SHA -> its deployment readyState.

    Scans recent deployments and matches meta.githubCommitSha by prefix (a pushed SHA
    is often short). This is what lets closeout verify EVERY pushed SHA reached a live
    READY deploy -- not just the latest one.

    Exit codes (so a close routine can branch): 0 = found + READY,
    2 = found but not READY (BUILDING/QUEUED/ERROR), 3 = NO deploy for this SHA at all
    (the LeakGuard non-verified-commit-author BLOCK signature -- a push that silently
    never deployed)."""
    sha = (sha or "").lower()
    params = {"limit": "100"}
    if project_id:
        params["projectId"] = project_id
    if len(sha) >= 40:            # Vercel's `sha` filter needs the full hash; prefix still scans below
        params["sha"] = sha
    data = api("/v6/deployments", params)
    match = None
    for d in data.get("deployments", []):
        dsha = (d.get("meta", {}).get("githubCommitSha") or "").lower()
        if dsha and (dsha.startswith(sha) or sha.startswith(dsha)):
            match = d
            break
    if not match:
        note = ("no deployment found for this SHA in the last 100 -- if it was just pushed, either the "
                "build hasn't started, the SHA is older than 100 deploys, or the commit author is "
                "unverified (Vercel silently blocks deploys from non-allowed authors).")
        if as_json:
            print(json.dumps({"sha": sha, "found": False, "readyState": None, "note": note}))
        else:
            print(f"No deployment for {sha[:9]}.\n{note}")
        sys.exit(3)
    state = match.get("readyState", match.get("state", "UNKNOWN"))
    url = match.get("url", "")
    if as_json:
        print(json.dumps({"sha": sha, "found": True, "readyState": state, "url": url,
                          "uid": match.get("uid"), "created": ts_to_str(match.get("created"))}))
    else:
        print(f"SHA:     {sha[:9]}")
        print(f"State:   {state}")
        print(f"URL:     {url}")
        print(f"Created: {ts_to_str(match.get('created'))}")
        print(f"ID:      {match.get('uid')}")
    sys.exit(0 if state == "READY" else 2)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]
    as_json = "--json" in args
    pos = [a for a in args if a != "--json"]

    commands = {
        "deployments": lambda: cmd_deployments(pos[0] if pos else None),
        "deployment": lambda: cmd_deployment(pos[0]),
        "logs": lambda: cmd_logs(pos[0]),
        "projects": cmd_projects,
        "project": lambda: cmd_project(pos[0]),
        "status": lambda: cmd_status(pos[0]),
        "latest": lambda: cmd_latest(pos[0] if pos else None),
        "deploy-for-sha": lambda: cmd_deploy_for_sha(pos[0], pos[1] if len(pos) > 1 else None, as_json),
    }

    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(commands.keys())}")
        sys.exit(1)

    commands[cmd]()


if __name__ == "__main__":
    main()
