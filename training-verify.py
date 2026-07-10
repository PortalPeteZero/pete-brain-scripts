#!/usr/bin/env python3
"""training-verify.py — runnable gate for the training-stats DB. Prints PASS/FAIL per check.
Done = every line PASS and the final EXIT is 0.  VAULT=/tmp/pbs python3 training-verify.py
"""
import os, sys, json, subprocess

def sql(q, service=True):
    r = subprocess.run(["python3", os.path.join(os.environ.get("VAULT","/tmp/pbs"),"cc-sql.py"), q],
                       capture_output=True, text=True)
    return r.stdout.strip()

def j(q):
    out = sql(q)
    try: return json.loads(out)
    except Exception: return out

fails = 0
def check(name, ok, detail=""):
    global fails
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    if not ok: fails += 1

# (a) lap->rep gate: the 10 Jul 3x3 = 14 reps
r = j("SELECT count(*) AS n FROM training_rep tr JOIN training_session s ON s.id=tr.session_id WHERE s.garmin_activity_id=23549884707")
n = r[0]['n'] if isinstance(r,list) and r else 0
check("3x3 (23549884707) => 14 reps", int(n)==14, f"got {n}")

# (a2) 17 Jun auto-lap merge = 7 reps (if ingested)
r = j("SELECT count(*) AS n FROM training_rep tr JOIN training_session s ON s.id=tr.session_id WHERE s.garmin_activity_id=23285123169")
n = r[0]['n'] if isinstance(r,list) and r else 0
if int(n) > 0:
    check("17 Jun auto-lap (23285123169) => 7 reps", int(n)==7, f"got {n}")
else:
    print("[SKIP] 17 Jun not ingested (outside backfill or pending)")

# (b) orphan zone-slug count = 0 (trigger integrity holds)
z = j("""SELECT count(*) AS n FROM training_rep tr WHERE tr.zone_slug IS NOT NULL
         AND tr.zone_slug NOT IN (SELECT jsonb_array_elements(payload->'zones')->>'slug'
                                  FROM health_config WHERE key='training-zones')""")
n = z[0]['n'] if isinstance(z,list) and z else '?'
check("orphan zone_slug count = 0", str(n)=='0', f"got {n}")

# (c) recovery lows populated on the 3x3 chilled reps (engine sanity)
r = j("SELECT count(*) AS n FROM training_rep tr JOIN training_session s ON s.id=tr.session_id WHERE s.garmin_activity_id=23549884707 AND step_role='recovery' AND recovery_hr_low IS NOT NULL")
n = r[0]['n'] if isinstance(r,list) and r else 0
check("3x3 recovery reps carry recovery_hr_low", int(n)>=3, f"got {n} with a low")

# (d) RLS: base tables have RLS on
r = j("SELECT count(*) AS n FROM pg_class WHERE relname IN ('training_session','training_rep','training_session_code_map') AND relrowsecurity")
n = r[0]['n'] if isinstance(r,list) and r else 0
check("RLS enabled on all 3 base tables", int(n)==3, f"got {n}/3")

# (e) views have security_invoker=true
r = j("""SELECT count(*) AS n FROM pg_class c WHERE c.relname IN ('training_weekly_volume','training_weekly_totals')
         AND EXISTS (SELECT 1 FROM pg_options_to_table(c.reloptions) WHERE option_name='security_invoker' AND option_value='true')""")
n = r[0]['n'] if isinstance(r,list) and r else 0
check("weekly views security_invoker=true", int(n)==2, f"got {n}/2")

# (f) zone bands: numeric fields present on the ladder
r = j("SELECT count(*) AS n FROM health_config, jsonb_array_elements(payload->'zones') z WHERE key='training-zones' AND (z ? 'run_hr_min')")
n = r[0]['n'] if isinstance(r,list) and r else 0
check("zone ladder carries numeric bands (run_hr_min)", int(n)==8, f"got {n}/8")

# (g) weekly view computes (grid present)
r = j("SELECT count(*) AS n FROM training_weekly_volume")
n = r[0]['n'] if isinstance(r,list) and r else 0
check("training_weekly_volume queryable (week x sport grid)", int(n)>0, f"{n} rows")

print()
print(f"{'='*50}\n{'ALL PASS' if fails==0 else str(fails)+' CHECK(S) FAILED'}\n{'='*50}")
sys.exit(0 if fails==0 else 1)
