#!/usr/bin/env python3
"""railway-bootstrap.py — runs INSIDE the Railway container, in front of a canonical helper.

THE point: the canonical helpers (Library/processes/scripts/*.py) read their secrets from local
files (Library/processes/secrets/*.json) and resolve paths under $VAULT. A cloud container has
neither. The WRONG fix (what bit us with the data-map stub) is to hand-rewrite an env-reading copy
of the script for Railway — that copy then drifts from canonical and silently corrupts data.

The RIGHT fix is this bootstrap: it materialises the secret FILES from Railway env vars and points
$VAULT at the repo, then execs the UNCHANGED canonical script. The repo therefore holds the real
canonical code (byte-for-byte, kept in lockstep by railway-sync-repo.py + the cc-cron-sync drift
guard) — there is nothing to drift.

Start command on each Railway service:  python railway-bootstrap.py <canonical-script.py> [args...]

Secrets are provided as env vars:
  • CC_SUPABASE_URL + CC_SUPABASE_SERVICE_KEY  → writes command-centre-supabase-keys.json
  • SECRETFILE__<name>  (e.g. SECRETFILE__odoo-api.json) → writes Library/processes/secrets/<name>
    (use __ in the env-var name for any dot in the filename)
"""
import os, sys, json, pathlib, runpy

REPO = pathlib.Path(__file__).parent.resolve()
SECRETS = REPO / "Library" / "processes" / "secrets"
SECRETS.mkdir(parents=True, exist_ok=True)
os.environ["VAULT"] = str(REPO)   # canonical helpers resolve {VAULT}/Library/processes/secrets/...

# 1. the CC Supabase keys file, reconstructed from the two env vars Railway already holds
url, key = os.environ.get("CC_SUPABASE_URL"), os.environ.get("CC_SUPABASE_SERVICE_KEY")
if url and key:
    (SECRETS / "command-centre-supabase-keys.json").write_text(
        json.dumps({"url": url, "service_role_key": key, "project_ref": url.split("//")[-1].split(".")[0]}))

# 2. any other secret file passed verbatim as SECRETFILE__<name>
for k, v in os.environ.items():
    if k.startswith("SECRETFILE__"):
        name = k[len("SECRETFILE__"):].replace("__", ".")
        (SECRETS / name).write_text(v)
        print(f"bootstrap: materialised secret {name}")

# target script: argv[1], else the per-service env var CRON_SCRIPT (lets one railway.json serve every service)
target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CRON_SCRIPT")
if not target:
    print("bootstrap: no target script (argv or CRON_SCRIPT)"); sys.exit(2)
script_path = REPO / target
if not script_path.exists():
    print(f"bootstrap: target {target} not found in repo"); sys.exit(2)
print(f"bootstrap: VAULT={REPO} → running canonical {target}")
sys.argv = [target] + sys.argv[2:]          # the canonical sees a clean argv
runpy.run_path(str(script_path), run_name="__main__")
