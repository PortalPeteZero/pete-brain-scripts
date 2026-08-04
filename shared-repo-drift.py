#!/usr/bin/env python3
"""shared-repo-drift.py -- do the guarded repos still have their guard rails?

Born 4 Aug 2026, the day two of the three shared repos turned out to have a type-check that was
never wired into the build, and a corrupted edit reached a live customer because of it. The gates
and SOPs added that day only stay valuable if their absence is LOUD. This is the on-demand checker
(Workstream E4 of plan-shared-dev-hardening): run it any time, exit 0 = all guard rails present.

Per shared repo it verifies, via the GitHub API (no clone):
  1. CLAUDE.md exists at the root          (the SOP an assistant fetches live)
  2. WORK-LOG.md exists at the root        (the running change record)
  3. package.json "build" runs a type-check before bundling (the gate)
     - accepted forms: "tsc -b" ..., or "typecheck && ", or an explicit "tsc " before vite/next
     - Next.js repos pass by framework (next build type-checks itself)

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/shared-repo-drift.py            # human report, exit 0/1
  VAULT=/tmp/pbs python3 /tmp/pbs/shared-repo-drift.py --json     # machine form

Deliberately NOT a cron (Pete's rule: ask before any background cron). Run it from closeout or by
hand when a shared repo was touched.
"""
import json, os, sys, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PAT = open(f"{VAULT}/Library/processes/secrets/github-pat").read().strip()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

# The repos that carry the 4 Aug guard rails (gate + SOP + work log). NOT all shared:
# locator-data and sygma-platform are joint Pete+Jim; leakguard-insight-hub is PETE'S ONLY
# (Canary Detect) -- it is here because it had the identical vulnerability and got the same
# fix, not because Jim works on it. Add a row when a repo gains the guard rails.
REPOS = [
    ("SygmaSol/locator-data", "vite"),          # joint
    ("SygmaSol/sygma-platform", "vite"),        # joint
    ("SygmaSol/leakguard-insight-hub", "vite"), # Pete's (Canary Detect)
]

def fetch(repo, path):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        headers={"Authorization": f"token {PAT}", "Accept": "application/vnd.github.raw",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        # never map a bare status code to a business meaning -- a WAF block is not a missing file
        raise SystemExit(f"ABORT: {repo}/{path} -> HTTP {e.code} (not a 404; refusing to verdict)")

def gate_ok(pkg_json, framework):
    try:
        scripts = json.loads(pkg_json).get("scripts", {})
    except Exception:
        return False, "package.json unparseable"
    build = scripts.get("build", "")
    if framework == "next" or build.startswith("next build"):
        return True, f'"{build}" (Next type-checks in build)'
    if "tsc -b" in build or "typecheck" in build or "tsc " in build.split("&&")[0]:
        return True, f'"{build}"'
    return False, f'"{build}" -- NO type-check before bundling'

def main():
    as_json = "--json" in sys.argv
    rows, bad = [], 0
    for repo, framework in REPOS:
        sop = fetch(repo, "CLAUDE.md") is not None
        wlog = fetch(repo, "WORK-LOG.md") is not None
        pkg = fetch(repo, "package.json")
        gated, detail = (False, "package.json missing") if pkg is None else gate_ok(pkg, framework)
        ok = sop and wlog and gated
        bad += 0 if ok else 1
        rows.append({"repo": repo, "sop": sop, "worklog": wlog, "gated": gated,
                     "build": detail, "ok": ok})
    if as_json:
        print(json.dumps({"ok": bad == 0, "repos": rows}, indent=1))
    else:
        for r in rows:
            mark = "OK " if r["ok"] else "DRIFT"
            print(f"[{mark}] {r['repo']}: SOP={'y' if r['sop'] else 'MISSING'} "
                  f"WORK-LOG={'y' if r['worklog'] else 'MISSING'} gate={r['build']}")
        print("all guard rails present" if bad == 0 else f"{bad} repo(s) have drifted")
    sys.exit(0 if bad == 0 else 1)

if __name__ == "__main__":
    main()
