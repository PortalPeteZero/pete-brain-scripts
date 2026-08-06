#!/usr/bin/env python3
# CRON-META
# what: finds readings hit by the ThingsLog x256 fault and corrects them (delta/256, onset row zeroed, counter untouched)
# why: Pete told Stephen Betts in writing on 6 Aug 2026 that we correct his data EVERY DAY while the fault runs, so his leak cover is unaffected. That promise needed a mechanism rather than someone remembering.
# reads: LeakGuard Supabase public.readings + devices
# writes: LeakGuard public.readings (delta_litres, corrected_at) + public.reading_corrections (the audit row, written first from the pre-change values)
# entity: canary-detect
# schedule: 40 */6 * * *
# timezone: Atlantic/Canary
# secrets: LEAKGUARD_SUPABASE_KEYS
# note: runs 15 minutes after thingslog-sync-6h (25 */6) so it always sees freshly-imported data.
# note: RETIRE THIS once ThingsLog fix the fault (ticket 4474). It is a mitigation, not a fix — it cleans up after a supplier defect and should not outlive it.
# CRON-META-END
"""lg-x256-sweep.py — find and correct readings hit by the x256 fault.

WHAT THE FAULT IS
  A ThingsLog logger starts reporting every reading multiplied by exactly 256. A genuine 5 litres
  arrives as 1,280. It is in ThingsLog's OWN data, not our ingest (their export and ours agreed
  65 of 65 on 6 Aug 2026, counter identical to 0 L). It is intermittent: six separate episodes on
  04159615 across 4, 5 and 6 Aug, so "it has stopped" is a claim that needs re-testing.

  Three of thirty loggers have had it: 04160611 (21-22 Jul, corrected 27 Jul), 04297713 (4 Aug),
  04159615 (4-6 Aug, still live at the time of writing). Chased with ThingsLog on ticket 4474.

WHY THIS EXISTS
  Pete told Stephen Betts in writing on 6 Aug 2026 that we correct his data EVERY DAY while the
  fault runs, so his leak cover is unaffected. That promise needs a mechanism, not a person
  remembering. This is the mechanism.

THE METHOD (established on 04160611, 27 Jul 2026; reused 6 Aug)
  - delta_litres is divided by 256
  - the ONSET row (22,529 L = 88*256 + 1, the counter leaving scale) is set to 0
  - counter_m3 is LEFT COMPLETELY ALONE, so our copy still agrees with ThingsLog. That agreement is
    the thing lg-crosscheck tests and it is the whole basis for trusting our data at all.
  - the reading_corrections audit rows are written FIRST, from the pre-change values
  - correcting sets corrected_at, which is what stops the 6-hourly sync re-importing the bad values
    (trg_readings_protect_corrections)

HOW A REAL FAULT IS TOLD FROM A COINCIDENCE (the device-DAY rule)
  Any step can land on a multiple of 256 by chance. Two devices in the 30-day sweep on 6 Aug had
  exactly one such step each (04299014 on 18 Jul, 04298116 on 2 Aug) and both were coincidence.

  The fault always arrives as a RUN. So the test is two-stage:
    1. a device-DAY qualifies only if it contains a run of MIN_RUN or more consecutive multiples
    2. on a qualifying device-day, EVERY inflated reading is corrected, including the isolated ones

  A pure run-only test was tried first and was too tight: replayed against the real 4-6 Aug fault
  data it caught 83 of 84 on 04159615 and only 20 of 25 on 04297713, because the fault fires in
  bursts as short as three readings with single stragglers around them. The device-day rule catches
  all of them while still leaving a lone 256 L step on an otherwise clean device completely alone.

Usage:
  lg-x256-sweep.py                 report only, exit 0 clean / 1 found something
  lg-x256-sweep.py --apply         correct what it finds
  lg-x256-sweep.py --days N        how far back to look (default 3)
"""
import json, os, subprocess, sys, uuid

VAULT = os.environ.get("VAULT", "/tmp/pbs")
LGSQL = os.path.join(VAULT, "lg-sql.py")
MIN_RUN = 3           # consecutive multiples of 256 before we believe it
ONSET_LITRES = 22529  # 88*256 + 1 — the counter leaving scale, seen on both devices 4 Aug 2026
SNAPSHOT_HOME = "Drive: Canary Detect / Projects/CD-LeakGuard"


def sql(q):
    r = subprocess.run([sys.executable, LGSQL, q], capture_output=True, text=True,
                       env=dict(os.environ, VAULT=VAULT), cwd=VAULT)
    if r.returncode:
        print(r.stdout + r.stderr, file=sys.stderr)
        sys.exit(2)
    out = r.stdout.strip()
    return json.loads(out) if out.startswith("[") else out


def find(days):
    """Every inflated reading on a device-DAY that contains a run of >= MIN_RUN, plus onset rows."""
    return sql(f"""
WITH s AS (
  SELECT r.id, r.device_id, d.device_number, r.reading_time,
         (r.reading_time AT TIME ZONE 'UTC')::date AS day, r.delta_litres,
         (r.delta_litres > 0 AND (r.delta_litres)::numeric % 256 = 0) AS x256,
         (r.delta_litres::int = {ONSET_LITRES}) AS onset
  FROM readings r JOIN devices d ON d.id = r.device_id
  WHERE r.reading_time >= now() - interval '{int(days)} days'
    AND r.corrected_at IS NULL
), g AS (
  SELECT *, row_number() OVER (PARTITION BY device_id ORDER BY reading_time)
          - row_number() OVER (PARTITION BY device_id, x256 ORDER BY reading_time) AS grp
  FROM s
), runs AS (
  SELECT device_id, day, max(run_len) AS longest FROM (
    SELECT device_id, day, grp, count(*) AS run_len FROM g WHERE x256 GROUP BY 1,2,3
  ) x GROUP BY 1,2
), bad_days AS (
  SELECT device_id, day FROM runs WHERE longest >= {MIN_RUN}
)
SELECT g.id, g.device_number, g.reading_time, g.delta_litres, g.onset
FROM g
WHERE (g.x256 AND EXISTS (SELECT 1 FROM bad_days b WHERE b.device_id=g.device_id AND b.day=g.day))
   OR g.onset
ORDER BY g.device_number, g.reading_time
""")


def main():
    apply_it = "--apply" in sys.argv
    days = 3
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    rows = find(days)
    if not rows:
        print(f"lg-x256-sweep: nothing to correct in the last {days} day(s). Clean.")
        return 0

    by_dev = {}
    for r in rows:
        by_dev.setdefault(r["device_number"], []).append(r)
    print(f"lg-x256-sweep: {len(rows)} reading(s) carrying the x256 signature, {len(by_dev)} device(s)")
    for dev, rs in sorted(by_dev.items()):
        booked = sum(float(x["delta_litres"]) for x in rs)
        print(f"  {dev}  {len(rs):>4} reading(s)  {booked:>12,.0f} L booked  "
              f"{rs[0]['reading_time'][:16]} -> {rs[-1]['reading_time'][:16]}")

    if not apply_it:
        print("\nreport only. re-run with --apply to correct.")
        return 1

    run_id = str(uuid.uuid4())
    ids = ",".join(f"'{r['id']}'" for r in rows)
    reason = (f"x256 fault: reading booked its true value multiplied by exactly 256. Delta divided "
              f"by 256; the onset row ({ONSET_LITRES} L, the counter leaving scale) set to 0; "
              f"counter_m3 left untouched so our copy still agrees with ThingsLog. Method of "
              f"27 Jul 2026. Corrected automatically by lg-x256-sweep, run {run_id}. "
              f"Underlying fault is in ThingsLog data, ticket 4474.")

    # audit rows FIRST, from the pre-change values
    ins = sql(f"""
INSERT INTO reading_corrections
  (run_id, corrected_at, actor, reason, method, snapshot_ref, device_id, device_number, reading_time,
   reading_id, old_counter_m3, old_delta_litres, old_dt_seconds, new_counter_m3, new_delta_litres,
   new_dt_seconds, operation)
SELECT '{run_id}', now(), 'lg-x256-sweep', $tag${reason}$tag$,
       'divide delta by 256; zero the onset row; counter left as-is',
       $tag${SNAPSHOT_HOME}$tag$,
       r.device_id, d.device_number, r.reading_time, r.id,
       r.counter_m3, r.delta_litres, r.dt_seconds, r.counter_m3,
       CASE WHEN r.delta_litres::int = {ONSET_LITRES} THEN 0 ELSE round(r.delta_litres/256) END,
       r.dt_seconds, 'update'
FROM readings r JOIN devices d ON d.id = r.device_id
WHERE r.id IN ({ids}) AND r.corrected_at IS NULL
RETURNING 1
""")
    upd = sql(f"""
UPDATE readings r
SET delta_litres = CASE WHEN r.delta_litres::int = {ONSET_LITRES} THEN 0
                        ELSE round(r.delta_litres/256) END,
    corrected_at = now(),
    correction_reason = $tag${reason}$tag$
WHERE r.id IN ({ids}) AND r.corrected_at IS NULL
RETURNING r.id
""")
    print(f"\naudit rows written : {len(ins) if isinstance(ins, list) else ins}")
    print(f"readings corrected : {len(upd) if isinstance(upd, list) else upd}")
    print(f"run_id             : {run_id}")

    left = find(days)
    print(f"remaining uncorrected in window: {len(left)}")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
