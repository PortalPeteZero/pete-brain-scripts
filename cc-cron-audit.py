#!/usr/bin/env python3
"""Read-only deep audit of the cron fleet vs Railway + public.crons. Catches: schedule_local↔UTC
mismatches (mis-firing / DST-flip breakage), missing schedule_local, Railway↔registry orphans,
non-SUCCESS deploys, and stale runs. No mutations."""
import importlib.util, os, datetime
HERE = "/tmp/pbs"
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
verdict = "✅ CLEAN" if not (tz_bad or orphan or bad_deploy) else "⚠ ISSUES FOUND (see 🔴 above)"
print(f"\n=== VERDICT: {verdict} ===")
