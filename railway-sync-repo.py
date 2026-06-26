#!/usr/bin/env python3
"""railway-sync-repo.py — keep the Railway code repo (pete-brain-scripts) == the CANONICAL helpers.

THE way to deploy or update a cron's code on Railway. It copies the named canonical helpers
byte-for-byte into the repo (plus railway-bootstrap.py + a generic railway.json), pushes, and
VERIFIES byte-equality (md5). This is the cure for the data-map stub bug: there are no hand-written
Railway copies to drift — the repo IS the canonical code, and the cc-cron-sync drift guard flags any
file that ever diverges.

Usage: python3 railway-sync-repo.py cc-data-map-sync.py [more-canonical.py ...]
Env:   GITHUB_PAT  (or reads Library/processes/secrets/github-pat)

Each Railway service then runs:  python railway-bootstrap.py   with env CRON_SCRIPT=<its script>.
"""

import sys as _sys
if __name__ == "__main__":
    _sys.exit("DEPRECATED → crons are managed by cc-cron.py (list/deploy/set-schedule/pause/resume/retire/status). "
              "See cron-registry.md. This script is retired — do not use it.")

import os, sys, shutil, hashlib, subprocess, pathlib, json, urllib.request

HERE = pathlib.Path(__file__).parent.resolve()       # Library/processes/scripts (canonical helpers)
REPO_SLUG = "PortalPeteZero/pete-brain-scripts"
WORK = pathlib.Path("/tmp/pbs-sync")
ALWAYS = ["railway-bootstrap.py"]                     # shipped on every sync

def pat():
    p = os.environ.get("GITHUB_PAT")
    if p: return p.strip()
    f = HERE.parent / "secrets" / "github-pat"
    if f.exists(): return f.read_text().strip()
    sys.exit("✗ no GITHUB_PAT (env or Library/processes/secrets/github-pat)")

def md5(path): return hashlib.md5(pathlib.Path(path).read_bytes()).hexdigest()
def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True); return r.returncode, r.stdout + r.stderr

def main():
    targets = [a for a in sys.argv[1:] if a.endswith(".py")]
    if not targets: sys.exit("usage: railway-sync-repo.py <canonical-script.py> ...")
    ship = ALWAYS + targets
    for fn in ship:
        if not (HERE / fn).exists(): sys.exit(f"✗ canonical {fn} not found in {HERE}")

    P = pat()
    import time as _t
    for _attempt in range(5):
        if WORK.exists(): shutil.rmtree(WORK)
        code, out = run(["git", "clone", "-q", f"https://{P}@github.com/{REPO_SLUG}.git", str(WORK)])
        if code == 0: break
        _t.sleep(3 * (_attempt + 1))   # transient GitHub SSL_ERROR_SYSCALL on repeated clones → back off + retry
    if code: sys.exit(f"✗ clone failed after retries: {out[:200]}")

    # copy canonical → repo, byte-for-byte. Subdir sources (e.g. account/account_store.py) FLATTEN to
    # their basename in the repo so the Railway container (flat /app) can import them as siblings.
    for fn in ship: shutil.copy2(HERE / fn, WORK / pathlib.Path(fn).name)
    # generic launcher: every service runs bootstrap; the script is chosen per-service via CRON_SCRIPT env
    (WORK / "railway.json").write_text(json.dumps(
        {"$schema": "https://railway.app/railway.schema.json",
         "deploy": {"startCommand": "python railway-bootstrap.py"}}, indent=2))
    (WORK / "README.md").write_text(
        "# pete-brain-scripts\nRailway cron code. **Do not hand-edit the .py files** — they are CANONICAL "
        "copies from the vault's Library/processes/scripts/, synced by `railway-sync-repo.py` and drift-"
        "checked by `cc-cron-sync.py`. Each service runs `python railway-bootstrap.py` with env "
        "`CRON_SCRIPT=<script>.py`. See vault [[cron-registry]].\n")

    # VERIFY byte-equality before committing (the guarantee)
    bad = [fn for fn in ship if md5(HERE / fn) != md5(WORK / pathlib.Path(fn).name)]
    if bad: sys.exit(f"✗ copy mismatch (refusing to push): {bad}")
    print(f"✓ byte-equal: {', '.join(ship)}")

    run(["git", "config", "user.email", "pete.ashcroft@sygma-solutions.com"], WORK)
    run(["git", "config", "user.name", "PortalPeteZero"], WORK)
    run(["git", "add", "-A"], WORK)
    code, out = run(["git", "commit", "-q", "-m", f"sync canonical: {', '.join(targets)} (railway-sync-repo)"], WORK)
    if "nothing to commit" in out:
        print("→ repo already == canonical, nothing to push")
    else:
        for _ in range(5):
            code, out = run(["git", "push", "origin", "main"], WORK)
            if code == 0: break
        if code: sys.exit(f"✗ push failed: {out[:200]}")
        code, sha = run(["git", "rev-parse", "--short", "HEAD"], WORK)
        print(f"→ pushed {sha.strip()}")

    # post-push live check (raw CDN can lag a few min — informational)
    print("live check (raw.githubusercontent — may lag CDN):")
    for fn in ship:
        bn = pathlib.Path(fn).name
        try:
            remote = urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO_SLUG}/main/{bn}", timeout=20).read()
            rm = hashlib.md5(remote).hexdigest()
        except Exception as e: rm = f"ERR {e}"
        print(f"  {'✓' if rm == md5(HERE/fn) else '…'} {bn}  canonical={md5(HERE/fn)[:8]} live={str(rm)[:8]}")
    print("\nnext: set each Railway service's CRON_SCRIPT env + redeploy.")

if __name__ == "__main__": main()
