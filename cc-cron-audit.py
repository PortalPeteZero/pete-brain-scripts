#!/usr/bin/env python3
"""Read-only deep audit of the cron fleet vs Railway + public.crons. Catches: schedule_local↔UTC
mismatches (mis-firing / DST-flip breakage), missing schedule_local, Railway↔registry orphans,
non-SUCCESS deploys, and stale runs. No mutations."""
import importlib.util, os, datetime

# --json emits ONLY machine output: this script prints as it goes, so capture and discard
# the human text until the JSON block, otherwise the result is unparseable.
import json
import sys as _sys, io as _io
_JSON_BUF = _io.StringIO()


def _json_abort(exc):
    """A crash in --json mode must still emit VALID JSON with a non-zero gap count — otherwise a
    caller gets an empty, unparseable result at exactly the moment the fleet could not be checked."""
    import json as _j
    _sys.stdout = _sys.__stdout__
    msg = f"cc-cron-audit aborted: {type(exc).__name__}: {exc}"
    print(_j.dumps({"gaps": 1, "gap_types": ["aborted"],
                    "findings": [{"rule": "aborted", "subject": "cc-cron-audit",
                                  "detail": msg, "severity": "high"}],
                    "info": [], "aborted": True}, indent=1))
    _sys.exit(99)

if "--json" in _sys.argv:
    _sys.stdout = _JSON_BUF
    _ORIG_EXCEPTHOOK = _sys.excepthook
    _sys.excepthook = lambda t, v, tb: _json_abort(v)

# 18 Jul 2026: this hardcoded /tmp/pbs, so it ran only in a local session and FileNotFound-ed on
# Railway (where VAULT is the container repo dir). Matches sibling cron-railway-audit.py now.
HERE = os.environ.get("VAULT", "/tmp/pbs")
spec = importlib.util.spec_from_file_location("cc", f"{HERE}/cc-cron.py"); cc = importlib.util.module_from_spec(spec)
os.environ["VAULT"] = HERE; spec.loader.exec_module(cc)

now = datetime.datetime.now(datetime.timezone.utc)
crons = cc.sb("GET", "crons?select=*&order=key")
d = cc.rw('query($p:String!){ project(id:$p){ services{ edges{ node{ id name } } } } }', {"p": cc.PROJECT})
svc = {e["node"]["name"]: e["node"]["id"] for e in d["project"]["services"]["edges"]}
svc_ids = set(svc.values())

tz_bad, no_local, orphan, bad_deploy, stale, services = [], [], [], [], [], []
for c in crons:
    key, sched, local = c["key"], c.get("schedule"), c.get("schedule_local")
    host_ref, enabled = c.get("host_ref"), c.get("enabled")
    is_service = not sched and not local
    if is_service:
        services.append(key); continue
    # 1. schedule_local ↔ UTC consistency
    if local:
        try:
            utc, off, _ = cc.local_to_utc(local, c.get("timezone") or "Atlantic/Canary")
            if utc != sched:
                tz_bad.append((key, local, sched, utc))
        except Exception as e:
            tz_bad.append((key, local, sched, f"ERR {e}"))
    elif enabled:
        no_local.append(key)
    # 2. Railway orphan
    sid = host_ref or svc.get(key)
    if not sid or sid not in svc_ids:
        orphan.append((key, f"host_ref={host_ref}"))
        continue
    # 3. deploy status
    st = cc.deploy_status(sid)
    if st and st != "SUCCESS":
        bad_deploy.append((key, st))
    # 4. stale run
    eih = c.get("expected_interval_hours"); lra = c.get("last_run_at")
    if enabled and eih and lra:
        try:
            t = datetime.datetime.fromisoformat(lra.replace("Z", "+00:00"))
            age_h = (now - t).total_seconds() / 3600
            if age_h > 2.2 * float(eih):
                stale.append((key, round(age_h, 1), eih))
        except Exception:
            pass

def show(title, rows, fmt):
    print(f"\n{'🔴' if rows and title.startswith(('TZ','ORPHAN','DEPLOY')) else '🟡' if rows else '🟢'} {title}: {len(rows)}")
    for r in rows[:30]: print("   " + fmt(r))

print(f"=== CRON FLEET AUDIT — {len(crons)} registry rows · {len(svc)} Railway services · {len(services)} services(no-cron) ===")
show("TZ MISMATCH (schedule_local→UTC ≠ live schedule — mis-fire / DST risk)", tz_bad, lambda r: f"{r[0]:32} local={r[1]:16} live={r[2]:16} should-be={r[3]}")
show("ORPHAN (no Railway service)", orphan, lambda r: f"{r[0]:32} {r[1]}")
show("DEPLOY not SUCCESS", bad_deploy, lambda r: f"{r[0]:32} {r[1]}")
show("MISSING schedule_local (enabled cron — won't DST-self-heal)", no_local, lambda r: r)
show("STALE last run (>2.2× interval)", stale, lambda r: f"{r[0]:32} age={r[1]}h interval={r[2]}h")
print(f"\n   services (no schedule, correctly): {services}")
# no_local and stale were MISSING from the machine output, so an automated caller saw
# a clean result while a real problem stood (demo-analytics-digest has no local
# schedule and will not self-correct at the clock change).
issues = list(tz_bad) + list(orphan) + list(bad_deploy) + list(no_local) + list(stale)
if "--json" in _sys.argv:
    _sys.stdout = _sys.__stdout__
    print(json.dumps({
        "gaps": len(issues),
        "gap_types": ([k for k, v in (("timezone", tz_bad), ("orphan", orphan), ("deploy", bad_deploy),
                                      ("no-schedule-local", no_local), ("stale-run", stale)) if v]),
        "findings": [{"rule": "cron-fleet", "subject": str(i)[:80], "detail": "see cc-cron-audit output",
                      "severity": "high"} for i in issues],
        "info": [{"subject": "coverage", "detail": "registry vs Railway: timezone, orphans, deploy status"}],
    }, indent=1))
    _sys.exit(0)
verdict = "✅ CLEAN" if not issues else "⚠ ISSUES FOUND (see 🔴 above)"
print(f"\n=== VERDICT: {verdict} ===")
_sys.exit(0 if not issues else 1)   # exit non-zero on issues so a caller can gate on it
