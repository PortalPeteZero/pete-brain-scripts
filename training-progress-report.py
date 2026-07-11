#!/usr/bin/env python3
"""training-progress-report.py — pull the raw material for a health/training progress report.

Dumps, for the last N weeks, everything the narrative needs from the training-stats SSOT:
weekly totals + WoW, volume by discipline, time-in-zone (intensity distribution), weekly
recovery (HRV / sleep / RHR), and per-session_code trends. Also computes simple slopes so
the narrative only states trends that are actually in the data (VERIFIED-FACTS-ONLY).

Usage:  VAULT=/tmp/pbs python3 training-progress-report.py [--weeks 8]
Process: [[health-progress-report]].
"""
import os, sys, json, subprocess

def sql(q):
    r = subprocess.run(["python3", os.path.join(os.environ.get("VAULT","/tmp/pbs"),"cc-sql.py"), q],
                       capture_output=True, text=True)
    out = r.stdout.strip()
    try:
        return json.loads(out) if out and not out.startswith("ERROR") else []
    except Exception:
        return []

def slope(vals):
    """+1 rising / -1 falling / 0 flat over the ordered numeric list (ignores None)."""
    v = [x for x in vals if x is not None]
    if len(v) < 2: return 0, None
    delta = v[-1] - v[0]
    trend = 'rising' if delta > 0 else ('falling' if delta < 0 else 'flat')
    return delta, trend

def main():
    weeks = 8
    if '--weeks' in sys.argv:
        weeks = int(sys.argv[sys.argv.index('--weeks')+1])

    # weeks that actually have training data, newest first, capped to N
    wk = sql("SELECT iso_week, week_start FROM training_weekly_totals WHERE sessions>0 ORDER BY week_start DESC")
    wk = wk[:weeks]
    wk = list(reversed(wk))  # chronological
    iso_weeks = [w['iso_week'] for w in wk]
    if not iso_weeks:
        print(json.dumps({"error": "no weeks with training data"})); return
    lo, hi = wk[0]['iso_week'], wk[-1]['iso_week']
    inlist = ",".join(f"'{w}'" for w in iso_weeks)

    totals = sql(f"SELECT iso_week, week_start, sessions, total_time_s, total_distance_m, total_load, time_wow_pct FROM training_weekly_totals WHERE iso_week IN ({inlist}) ORDER BY week_start")
    volume = sql(f"SELECT iso_week, sport, sessions, total_time_s, total_distance_m, total_load, time_by_zone, time_wow_pct, distance_wow_pct FROM training_weekly_volume WHERE iso_week IN ({inlist}) AND sessions>0 ORDER BY week_start, sport")
    recovery = sql(f"SELECT iso_week, avg_hrv, avg_sleep_h, avg_rhr FROM garmin_weekly_recovery WHERE iso_week IN ({inlist}) ORDER BY iso_week")
    # per-session_code trends (recurring sessions in the window)
    trends = sql(f"""SELECT session_code, date, session_name, sport, avg_hr, max_hr,
                     avg_pace_s_per_km, avg_speed_kmh, efficiency_index, training_load
                     FROM training_session
                     WHERE session_code IS NOT NULL
                       AND to_char(date,'IYYY-\"W\"IW') IN ({inlist})
                     ORDER BY session_code, date""")

    # computed slopes (so the narrative can cite real trends only)
    rec_by = {r['iso_week']: r for r in recovery}
    hrv_series = [float(rec_by[w]['avg_hrv']) if rec_by.get(w) and rec_by[w]['avg_hrv'] is not None else None for w in iso_weeks]
    sleep_series = [float(rec_by[w]['avg_sleep_h']) if rec_by.get(w) and rec_by[w]['avg_sleep_h'] is not None else None for w in iso_weeks]
    rhr_series = [float(rec_by[w]['avg_rhr']) if rec_by.get(w) and rec_by[w]['avg_rhr'] is not None else None for w in iso_weeks]
    tot_by = {r['iso_week']: r for r in totals}
    time_series = [float(tot_by[w]['total_time_s'])/60 if tot_by.get(w) else None for w in iso_weeks]

    computed = {
        'hrv':   dict(zip(('delta','trend'), slope(hrv_series))),
        'sleep': dict(zip(('delta','trend'), slope(sleep_series))),
        'rhr':   dict(zip(('delta','trend'), slope(rhr_series))),
        'weekly_time_min': dict(zip(('delta','trend'), slope(time_series))),
        'series': {'iso_weeks': iso_weeks, 'hrv': hrv_series, 'sleep_h': sleep_series,
                   'rhr': rhr_series, 'time_min': [round(t) if t else None for t in time_series]},
    }

    print(json.dumps({
        'window': {'weeks': len(iso_weeks), 'from': lo, 'to': hi},
        'weekly_totals': totals,
        'volume_by_discipline': volume,
        'recovery': recovery,
        'session_trends': trends,
        'computed_trends': computed,
    }, indent=2, default=str))

if __name__ == '__main__':
    main()
