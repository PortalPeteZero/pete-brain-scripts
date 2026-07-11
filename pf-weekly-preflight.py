#!/usr/bin/env python3
"""pf-weekly-preflight.py — the MANDATORY data pull before any weekly feedback OR plan.

Nothing gets drafted until this has run and been read. Pulls, for the closing week, in ONE
block: the plan set for that week (feedback is assessed against IT), every daily journal +
"one lesson" of the week, the last 4 weekly entries (plan + reflection), the week's Garmin
recovery + training sessions actually done, and the planned sessions. See [[pf-weekly-loop]].

Usage:  VAULT=/tmp/pbs python3 pf-weekly-preflight.py [--week 2026-W28]   # default: current ISO week
"""
import os, sys, json, subprocess, datetime

def sql(q):
    r = subprocess.run(["python3", os.path.join(os.environ.get("VAULT","/tmp/pbs"),"cc-sql.py"), q],
                       capture_output=True, text=True)
    out = r.stdout.strip()
    try:
        return json.loads(out) if out and not out.startswith("ERROR") else []
    except Exception:
        return []

def iso_week_of(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"

def week_bounds(iso):
    y, w = iso.split("-W")
    monday = datetime.date.fromisocalendar(int(y), int(w), 1)
    sunday = datetime.date.fromisocalendar(int(y), int(w), 7)
    return monday, sunday

def prev_weeks(iso, n):
    y, w = iso.split("-W")
    monday = datetime.date.fromisocalendar(int(y), int(w), 1)
    out = []
    for i in range(1, n+1):
        d = monday - datetime.timedelta(weeks=i)
        out.append(iso_week_of(d))
    return out

def main():
    # closing week = the week we are reflecting on
    week = None
    if "--week" in sys.argv:
        week = sys.argv[sys.argv.index("--week")+1]
    if not week:
        # can't call datetime.date.today() reliably in sandbox restrictions? it's fine here.
        week = iso_week_of(datetime.date.today())
    mon, sun = week_bounds(week)
    lo, hi = mon.isoformat(), sun.isoformat()
    last4 = prev_weeks(week, 4)
    inlist4 = ",".join(f"'{w}'" for w in [week]+last4)

    print("="*70)
    print(f"PF WEEKLY PRE-FLIGHT — closing week {week}  ({lo} to {hi})")
    print("READ ALL OF THIS before drafting the feedback or the plan.")
    print("="*70)

    # 1. THE PLAN set for the closing week (feedback is assessed against this)
    plan = sql(f"SELECT body, frontmatter->>'status' AS status FROM health_weekly WHERE iso_week='{week}'")
    print("\n### 1. PLAN set for {} (assess the week against this):".format(week))
    print(plan[0]['body'] if plan else "  (no plan entry for this week — flag it)")

    # 2. Daily journals + one-lesson for the 7 days
    js = sql(f"SELECT date, body FROM health_journal WHERE date BETWEEN '{lo}' AND '{hi}' ORDER BY date")
    print(f"\n### 2. DAILY JOURNALS this week ({len(js)} of 7):")
    import re
    for j in js:
        body = j['body'] or ""
        m = re.search(r'##\s*One lesson for tomorrow\s*(.+?)(?:\n##|\Z)', body, re.S|re.I)
        lesson = (m.group(1).strip()[:200] if m else "(no explicit lesson line)")
        print(f"  {j['date']}: lesson -> {lesson}")
        print(f"       (journal {len(body)} chars — read in full if drafting from it)")
    if not js:
        print("  (no journals this week)")

    # 3. Last 4 weekly entries — plan + reflection
    print("\n### 3. LAST 4 WEEKLIES (plan + reflection, for continuity):")
    wl = sql(f"SELECT iso_week, frontmatter->>'status' AS status, body FROM health_weekly WHERE iso_week IN ({inlist4}) AND iso_week <> '{week}' ORDER BY iso_week DESC")
    for w in wl:
        b = w['body'] or ""
        refl = ""
        mr = re.search(r'##\s*Reflection on the week(.+)', b, re.S|re.I)
        if mr: refl = " | HAS reflection"
        print(f"  {w['iso_week']} ({w['status']}){refl} — {len(b)} chars")

    # 4. Garmin recovery this week
    rec = sql(f"SELECT date, hrv, sleep_hours, resting_hr, readiness FROM garmin_daily WHERE date BETWEEN '{lo}' AND '{hi}' ORDER BY date")
    print(f"\n### 4. GARMIN RECOVERY this week ({len(rec)} days):")
    for r in rec:
        print(f"  {r['date']}: HRV {r['hrv']}, sleep {r['sleep_hours']}h, RHR {r['resting_hr']}, readiness {r['readiness']}")
    # weekly recovery averages + the 4-week trend context
    trend = sql(f"SELECT iso_week, avg_hrv, avg_sleep_h, avg_rhr FROM garmin_weekly_recovery WHERE iso_week IN ({inlist4}) ORDER BY iso_week")
    print("  4-week recovery trend (avg HRV / sleep / RHR):")
    for t in trend:
        print(f"    {t['iso_week']}: HRV {t['avg_hrv']}, sleep {t['avg_sleep_h']}h, RHR {t['avg_rhr']}")

    # 5. Training done this week (training_session)
    ts = sql(f"SELECT date, sport, session_name, session_code, round(distance_m/1000.0,1) AS km, round(duration_s/60.0) AS min, avg_hr FROM training_session WHERE date BETWEEN '{lo}' AND '{hi}' ORDER BY date")
    print(f"\n### 5. TRAINING DONE this week ({len(ts)} sessions):")
    for t in ts:
        print(f"  {t['date']}: {t['sport']} — {t['session_name']} — {t['km']}km / {t['min']}min, HR {t['avg_hr']}, code={t['session_code']}")
    if not ts:
        print("  (no ingested sessions — some may be un-ingested; check garmin activities)")

    # 6. PLANNED sessions (health_planned_session) touching this week
    ps = sql(f"SELECT date, seq, source, spec->>'name' AS name, spec->>'code' AS code FROM health_planned_session WHERE date BETWEEN '{lo}' AND '{hi}' ORDER BY date, seq")
    print(f"\n### 6. PLANNED SESSIONS this week ({len(ps)}):")
    for p in ps:
        print(f"  {p['date']} seq{p['seq']} [{p['source']}]: {p['name']} (code {p['code']})")
    if not ps:
        print("  (no planned sessions recorded for this week)")

    print("\n" + "="*70)
    print("CONTRACT — do not skip: (a) feedback = the week above ASSESSED AGAINST plan #1;")
    print("(b) plan = the NEXT week only; (c) BOTH delivered as clean paste-ready Xhale copy")
    print("blocks (no markdown, no headers); (d) save each to health_weekly on Pete's go.")
    print("="*70)

if __name__ == '__main__':
    main()
