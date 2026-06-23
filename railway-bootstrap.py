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

# 2b. the shared Google service-account key — passed as GOOGLE_SA_JSON (a CLEAN env-var name;
#     Railway rejects the dots/hyphens in the real filename so SECRETFILE__ can't carry it).
#     Unblocks every gmail-api.py / calendar-api.py cron (they resolve $VAULT/.../secrets/<this>).
_sa = os.environ.get("GOOGLE_SA_JSON")
if _sa:
    (SECRETS / "google-seo-service-account.json").write_text(_sa)
    print("bootstrap: materialised google-seo-service-account.json")

# 2d. the Garmin OAuth tokens — passed as GARMIN_TOKENS_JSON (the token lives in a SUBDIR that the
#     flat SECRETFILE__ name mechanism can't express). Unblocks garmin on Railway.
_gt = os.environ.get("GARMIN_TOKENS_JSON")
if _gt:
    (SECRETS / "garminconnect-tokens").mkdir(parents=True, exist_ok=True)
    (SECRETS / "garminconnect-tokens" / "garmin_tokens.json").write_text(_gt)
    print("bootstrap: materialised garminconnect-tokens/garmin_tokens.json")

# target script: argv[1], else the per-service env var CRON_SCRIPT (lets one railway.json serve every service)
target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CRON_SCRIPT")
if not target:
    print("bootstrap: no target script (argv or CRON_SCRIPT)"); sys.exit(2)
script_path = REPO / target
if not script_path.exists():
    print(f"bootstrap: target {target} not found in repo"); sys.exit(2)
print(f"bootstrap: VAULT={REPO} → running canonical {target}")
sys.argv = [target] + sys.argv[2:]          # the canonical sees a clean argv
try:
    runpy.run_path(str(script_path), run_name="__main__")
    rc = 0
except SystemExit as e:
    rc = e.code if isinstance(e.code, int) else (0 if not e.code else 1)
except BaseException:
    import traceback; traceback.print_exc(); rc = 1
# Force-terminate the container. The script's work (DB writes, email sends) is finished by here; the
# interpreter would otherwise HANG waiting on a lingering non-daemon thread / open API connection that
# the gmail / odoo / drive clients leave behind — which kept Railway cron containers "running"
# indefinitely after they'd actually completed. os._exit skips that wait. Flush first (it also skips that).
sys.stdout.flush(); sys.stderr.flush()
os._exit(rc)
