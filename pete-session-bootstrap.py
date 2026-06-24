#!/usr/bin/env python3
"""pete-session-bootstrap.py — the thin-client boot kernel (Business OS Part J step 3).

Turns a bare machine (just the CC key) into a working session with ZERO permanent local code or
secrets: it PULLS all code from GitHub to /tmp/pbs and MATERIALISES all secrets from the CC
`secrets` table into /tmp/pbs/Library/processes/secrets/ — so the canonical helper scripts run
UNCHANGED. This is the proven railway-bootstrap no-stub pattern, extended from Railway to local
sessions: there are no hand-written env-reading copies to drift, and nothing secret persists on disk
outside the one CC bootstrap key.

Irreducible local footprint after cutover:
  • ~/.config/pete-secrets/command-centre-supabase-keys.json   (the ONE bootstrap key; or CC_SUPABASE_* env)
  • the tiny CLAUDE.md bootstrap + this kernel (a copy at ~/.config/pete-cc/)
Everything else is pulled/materialised into /tmp on demand and discarded.

Usage:
  python3 pete-session-bootstrap.py                 # clone/pull + materialise secrets; print VAULT
  python3 pete-session-bootstrap.py cc-sql.py "SELECT 1"   # ...then exec a canonical tool
Manual run of any pulled tool afterwards:  VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py [args]
"""
import os, sys, json, base64, subprocess, urllib.request
from pathlib import Path

PBS = Path(os.environ.get("PBS_DIR", "/tmp/pbs"))
REPO = "PortalPeteZero/pete-brain-scripts"
CFG = Path.home() / ".config/pete-secrets/command-centre-supabase-keys.json"


def cc_creds():
    url, key = os.environ.get("CC_SUPABASE_URL"), os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if url and key:
        return url.rstrip("/"), key
    d = json.load(open(CFG))
    return d["url"].rstrip("/"), d["service_role_key"]


CC_URL, CC_KEY = cc_creds()


def cc_get(path):
    req = urllib.request.Request(f"{CC_URL}/rest/v1/{path}",
                                 headers={"apikey": CC_KEY, "Authorization": f"Bearer {CC_KEY}"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


def clone_or_pull():
    rows = cc_get("secrets?select=value&name=eq.github-pat")
    pat = rows[0]["value"].strip() if rows else None
    if (PBS / ".git").exists():
        subprocess.run(["git", "-C", str(PBS), "pull", "-q", "--ff-only"], check=False)
        return "pulled"
    url = f"https://{pat}@github.com/{REPO}.git" if pat else f"https://github.com/{REPO}.git"
    subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(PBS)], check=True)
    return "cloned"


def materialise_secrets():
    sec = PBS / "Library" / "processes" / "secrets"
    sec.mkdir(parents=True, exist_ok=True)
    rows = cc_get("secrets?select=name,value,encoding")
    n = 0
    for r in rows:
        name = r["name"]
        val = r.get("value") or ""
        enc = (r.get("encoding") or "text").lower()
        dest = sec / name                       # name may be a subpath (e.g. garminconnect-tokens/garmin_tokens.json)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if enc == "base64":
            dest.write_bytes(base64.b64decode(val))
        else:
            dest.write_text(val)
        n += 1
    return n


def main():
    how = clone_or_pull()
    n = materialise_secrets()
    os.environ["VAULT"] = str(PBS)
    print(f"bootstrap: /tmp/pbs ready ({how} {REPO}, {n} secrets materialised), VAULT={PBS}", flush=True)
    if len(sys.argv) > 1:
        tool = PBS / sys.argv[1]
        if not tool.exists():
            sys.exit(f"bootstrap: tool {sys.argv[1]} not found in {PBS}")
        os.execve(sys.executable, [sys.executable, str(tool)] + sys.argv[2:], os.environ)


if __name__ == "__main__":
    main()
