#!/usr/bin/env python3
"""training-ingest.py — ingest a Garmin activity into training_session + training_rep.

Rep structure comes from the AUTHORITATIVE Garmin workout store (get_workout_by_id on
the activity's associatedWorkoutId) — present for every structured session incl.
backfilled ones the CC health_planned_session table doesn't have. Laps are assigned to
the fully-expanded prescription steps by cumulative duration. Falls back to wktStepIndex
contiguity, then a 1km-sum heuristic, for unstructured/unpaired runs.

Idempotent on garmin_activity_id. Re-ingest preserves human-entered columns
(rep_rpe/rep_feel/manual zone, session kit/rpe/feel/tags) and always re-derives
session_code via the persistent cascade.  See plan: Projects/PA-Health/plan-training-stats-db-2026-07-10.

CLI:  VAULT=/tmp/pbs python3 training-ingest.py <activity_id>            # ingest one
      VAULT=/tmp/pbs python3 training-ingest.py --backfill 2026-01-01   # backfill from date
      VAULT=/tmp/pbs python3 training-ingest.py --dry <activity_id>     # print, write nothing
"""
import sys, os, re, json, subprocess, importlib.util
_spec = importlib.util.spec_from_file_location("garmin_api", os.path.join(os.environ.get("VAULT","/tmp/pbs"),"garmin-api.py"))
_gm = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_gm)
GarminAPI = _gm.GarminAPI

ZONE_SLUGS = ['competent','chilled','comfortable','controlled','challenging','candy','critical','crazy']

def sql(q):
    r = subprocess.run(["python3", os.path.join(os.environ.get("VAULT","/tmp/pbs"),"cc-sql.py"), q],
                       capture_output=True, text=True)
    if r.returncode != 0 or (r.stdout.strip().startswith("ERROR")):
        raise RuntimeError(f"SQL failed: {r.stdout.strip()[:300]} {r.stderr.strip()[:300]}\nQ: {q[:200]}")
    out = r.stdout.strip()
    try:
        return json.loads(out) if out else []
    except Exception:
        if out.startswith("ERROR"):
            raise RuntimeError(out[:400])
        return []

def q_lit(v):
    if v is None: return "NULL"
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v,(int,float)): return str(v)
    if isinstance(v, list):
        inner = ",".join(q_lit(x) for x in v)
        return f"ARRAY[{inner}]::text[]" if v else "ARRAY[]::text[]"
    s = str(v).replace("'","''")
    return f"'{s}'"

# ---------- zone-label parsing ----------
def parse_zone(label):
    """-> (zone_slug, zone_slug_end, zone_modifier, zone_label) from a step description."""
    if not label: return (None,None,None,label)
    t = label.lower()
    mod = None
    for m in ('super','low','high','mid'):
        if re.search(r'\b'+m+r'\b', t): mod = m; break
    found = [z for z in ZONE_SLUGS if re.search(r'\b'+z+r'\b', t)]
    # order them by position in the string
    found = sorted(set(found), key=lambda z: t.find(z))
    is_ramp = ('build' in t or ' to ' in t or re.search(r'\b\w+\s*-\s*\w+\b', t)) and len(found) >= 2
    if not found: return (None, None, mod, label)
    if is_ramp and len(found) >= 2:
        return (found[0], found[1], mod, label)
    return (found[0], None, mod, label)

def role_from(intensity, label):
    it = (intensity or '').upper()
    if it == 'WARMUP': return 'warmup'
    if it == 'COOLDOWN': return 'cooldown'
    if it == 'REST': return 'rest'
    if it == 'RECOVERY': return 'recovery'
    ll = (label or '').lower()
    if 'warm' in ll: return 'warmup'
    if 'cool' in ll: return 'cooldown'
    if 'chilled' in ll or 'recover' in ll or 'rest' in ll: return 'recovery'
    return 'work'

def expand_workout(w):
    """Fully expand a Garmin workout into an ordered list of step dicts (repeats unrolled)."""
    steps = []
    def walk(node, set_idx=None, rep_in_set=None):
        for st in node.get('workoutSteps', []) or []:
            if st.get('type') == 'RepeatGroupDTO':
                n = st.get('numberOfIterations', 1) or 1
                for i in range(1, n+1):
                    walk(st, set_idx=i, rep_in_set=None)
            else:
                dur = None; dist = None
                ec = (st.get('endCondition') or {}).get('conditionTypeKey')
                ev = st.get('endConditionValue')
                if ec == 'time': dur = float(ev) if ev else None
                elif ec == 'distance': dist = float(ev) if ev else None
                steps.append({
                    'label': st.get('description') or '',
                    'intensity': (st.get('stepType') or {}).get('stepTypeKey','').upper()
                                 or st.get('intensityType',''),
                    'dur': dur, 'dist': dist,
                    'set_index': set_idx, 'rep_in_set': rep_in_set,
                })
    for seg in w.get('workoutSegments', []) or []:
        walk(seg)
    # fill rep_in_set sequentially within a set
    return steps

# ---------- lap -> step assignment ----------
def merge_laps(laps):
    tot_dur = sum((l.get('duration') or 0) for l in laps)
    tot_dist = sum((l.get('distance') or 0) for l in laps)
    hrs = [(l.get('averageHR'), l.get('duration') or 0) for l in laps if l.get('averageHR')]
    avg_hr = round(sum(h*d for h,d in hrs)/sum(d for _,d in hrs)) if hrs and sum(d for _,d in hrs) else None
    max_hr = max((l.get('maxHR') for l in laps if l.get('maxHR')), default=None)
    pw = [(l.get('averagePower'), l.get('duration') or 0) for l in laps if l.get('averagePower')]
    avg_pw = round(sum(p*d for p,d in pw)/sum(d for _,d in pw)) if pw and sum(d for _,d in pw) else None
    max_pw = max((l.get('maxPower') for l in laps if l.get('maxPower')), default=None)
    cad = [l.get('averageRunningCadenceInStepsPerMinute') or l.get('averageBikingCadenceInRevPerMinute') for l in laps]
    cad = [c for c in cad if c]
    return {
        'dur': tot_dur, 'dist': tot_dist, 'avg_hr': avg_hr, 'max_hr': max_hr,
        'avg_power_w': avg_pw, 'max_power_w': max_pw,
        'avg_cadence': round(sum(cad)/len(cad)) if cad else None,
    }

def assign_laps_to_steps(laps, steps):
    """Assign contiguous laps to expanded prescription steps by cumulative duration."""
    laps = [l for l in laps if (l.get('distance') or 0) >= 10 or (l.get('duration') or 0) >= 10]
    groups = [[] for _ in steps]
    li = 0
    for si, st in enumerate(steps):
        target = st.get('dur') or (st.get('dist') and None) or 0
        acc = 0.0
        if not target:  # distance-based step: assign one lap
            if li < len(laps): groups[si].append(laps[li]); li += 1
            continue
        # accumulate laps until we reach ~the step's duration (allow 40% slack for the last partial)
        while li < len(laps):
            groups[si].append(laps[li]); acc += (laps[li].get('duration') or 0); li += 1
            if acc >= target * 0.85:
                break
    # any leftover laps append to the last group
    while li < len(laps):
        groups[-1].append(laps[li]); li += 1
    return groups

# ---------- HR trace recovery lows ----------
def recovery_lows(activity_id, g, step_bounds):
    """step_bounds: list of (lo_s, hi_s) elapsed windows; return min HR per window."""
    try:
        det = g.raw('get_activity_details', activity_id, 4000)
    except Exception:
        return [None]*len(step_bounds)
    mds = {m.get('key'): m.get('metricsIndex') for m in det.get('metricDescriptors',[])}
    hi = mds.get('directHeartRate'); ti = mds.get('sumElapsedDuration')
    if hi is None or ti is None: return [None]*len(step_bounds)
    series = []
    for r in det.get('activityDetailMetrics',[]):
        m = r.get('metrics',[])
        if len(m) <= max(hi,ti): continue
        if m[ti] is None or m[hi] is None: continue
        series.append((m[ti], m[hi]))
    out = []
    for lo,h in step_bounds:
        seg = [hr for t,hr in series if lo <= t < h]
        out.append(int(min(seg)) if seg else None)
    return out

# ---------- session_code cascade ----------
def derive_session_code(workout_id, sport, session_name):
    if workout_id:
        rows = sql(f"SELECT spec->>'code' AS code FROM health_planned_session WHERE garmin_workout_id={int(workout_id)} AND (spec->>'code') IS NOT NULL LIMIT 1")
        if rows and rows[0].get('code'): return rows[0]['code']
    # map rules
    for kind, val in (('workout-id', str(workout_id) if workout_id else None),):
        if val:
            rows = sql(f"SELECT code FROM training_session_code_map WHERE match_kind={q_lit(kind)} AND match_value={q_lit(val)} LIMIT 1")
            if rows: return rows[0]['code']
    # name-pattern rules
    rows = sql("SELECT match_value, code FROM training_session_code_map WHERE match_kind='name-pattern'")
    for r in rows:
        if r['match_value'].lower() in (session_name or '').lower():
            return r['code']
    return None

# ---------- main ingest ----------
def ingest(activity_id, dry=False):
    g = GarminAPI()
    a = g.raw('get_activity', activity_id)
    summ = a.get('summaryDTO', {})
    md = a.get('metadataDTO', {})
    atype = (a.get('activityTypeDTO') or a.get('activityType') or {}).get('typeKey','')
    def sport_of(tk):
        if 'swim' in tk: return 'swimming','open_water' if 'open' in tk else 'pool'
        if 'cyc' in tk or 'bik' in tk: return 'cycling', ('turbo' if 'indoor' in tk or 'virtual' in tk else 'road')
        if 'run' in tk: return 'running', ('treadmill' if 'treadmill' in tk or 'indoor' in tk else 'outdoor')
        if 'strength' in tk: return 'strength', None
        return 'other', None
    sport, sub = sport_of(atype)

    dur = summ.get('duration'); dist = summ.get('distance')
    spd = summ.get('averageSpeed')  # m/s
    avg_speed_kmh = round(spd*3.6,2) if spd else None
    pace_km = round(1000.0/spd/60*60,1) if (spd and sport in ('running',)) else (round((dur/(dist/1000.0)),1) if dur and dist and sport=='running' else None)
    pace_100 = round(dur/(dist/100.0),1) if dur and dist and sport=='swimming' else None
    is_pool = sub=='pool'
    sess = {
        'garmin_activity_id': int(activity_id),
        'date': (summ.get('startTimeLocal') or a.get('startTimeLocal') or '')[:10],
        'sport': sport, 'sub_sport': sub,
        'session_name': a.get('activityName'),
        'session_type': None,
        'start_time': summ.get('startTimeLocal') or a.get('startTimeLocal'),
        'duration_s': int(dur) if dur else None,
        'moving_s': int(summ.get('movingDuration')) if summ.get('movingDuration') else None,
        'distance_m': round(dist,1) if dist else None,
        'avg_pace_s_per_km': (round(dur/(dist/1000.0),1) if dur and dist and sport=='running' else None),
        'avg_pace_s_per_100m': pace_100,
        'avg_speed_kmh': avg_speed_kmh,
        'avg_power_w': int(summ['averagePower']) if summ.get('averagePower') else None,
        'max_power_w': int(summ['maxPower']) if summ.get('maxPower') else None,
        'np_w': int(summ['normPower']) if summ.get('normPower') else None,
        'avg_hr': int(summ['averageHR']) if summ.get('averageHR') else None,
        'max_hr': int(summ['maxHR']) if summ.get('maxHR') else None,
        'min_hr': int(summ['minHR']) if summ.get('minHR') else None,
        'avg_cadence': int(summ['averageRunCadence']) if summ.get('averageRunCadence') else (int(summ['averageBikeCadence']) if summ.get('averageBikeCadence') else None),
        'elevation_gain_m': round(summ['elevationGain'],1) if summ.get('elevationGain') else None,
        'temp_c_avg': (round(summ['averageTemperature'],1) if summ.get('averageTemperature') is not None and not is_pool else None),
        'temp_c_min': (round(summ['minTemperature'],1) if summ.get('minTemperature') is not None and not is_pool else None),
        'temp_c_max': (round(summ['maxTemperature'],1) if summ.get('maxTemperature') is not None and not is_pool else None),
        'water_temp_c': (round(summ['minTemperature'],1) if summ.get('minTemperature') is not None and sub=='open_water' else None),
        'pool_length_m': summ.get('poolLength') if is_pool else None,
        'lengths': summ.get('numberOfActiveLengths') if is_pool else None,
        'calories': int(summ['calories']) if summ.get('calories') else None,
        'te_aerobic': summ.get('trainingEffect'),
        'te_anaerobic': summ.get('anaerobicTrainingEffect'),
        'training_load': summ.get('activityTrainingLoad'),
        'efficiency_index': (round(summ['averageHR']/avg_speed_kmh,2) if summ.get('averageHR') and avg_speed_kmh else None),
        'source': 'garmin-auto',
        'parent_garmin_id': md.get('associatedActivityId') if md.get('isMultiSportParent') is False else None,
    }
    # readiness from garmin_daily
    rd = sql(f"SELECT readiness FROM garmin_daily WHERE date='{sess['date']}'")
    sess['readiness'] = rd[0]['readiness'] if rd else None

    # prescription structure from the Garmin workout store
    wkt_id = md.get('associatedWorkoutId')
    steps = []
    if wkt_id:
        try:
            w = g.raw('get_workout_by_id', wkt_id)
            steps = expand_workout(w)
            sess['was_planned'] = True
        except Exception:
            steps = []
    sess['session_code'] = derive_session_code(wkt_id, sport, sess['session_name'])
    # planned_* mirror
    if wkt_id:
        pr = sql(f"SELECT date, seq FROM health_planned_session WHERE garmin_workout_id={int(wkt_id)} LIMIT 1")
        if pr:
            sess['planned_date'] = pr[0]['date']; sess['planned_seq'] = pr[0]['seq']

    # laps
    try:
        lp = g.raw('get_activity_splits', activity_id)
        laps = lp.get('lapDTOs') or lp.get('splits') or []
    except Exception:
        laps = []

    reps = []
    if steps and laps:
        groups = assign_laps_to_steps(laps, steps)
        # cumulative time bounds for recovery-low lookup
        bounds = []; acc = 0.0
        for grp in groups:
            gdur = sum((l.get('duration') or 0) for l in grp)
            bounds.append((acc, acc+gdur)); acc += gdur
        lows = recovery_lows(activity_id, g, bounds)
        set_counter = {}
        for i,(st,grp) in enumerate(zip(steps, groups), start=1):
            if not grp: continue
            m = merge_laps(grp)
            zs, ze, zmod, zlabel = parse_zone(st['label'])
            role = role_from(st.get('intensity'), st['label'])
            # swim never inferred
            if sport=='swimming': zs = zs  # only from prescription label; else None
            si = st.get('set_index')
            if si is not None:
                set_counter[si] = set_counter.get(si,0)+1
            reps.append({
                'rep_index': i, 'set_index': si, 'rep_in_set': None,
                'step_role': role, 'zone_slug': zs, 'zone_slug_end': ze,
                'zone_modifier': zmod, 'zone_label': zlabel,
                'zone_source': 'planned' if zs else 'unmapped',
                'zone_confidence': 'high' if zs else None,
                'duration_s': round(m['dur'],1) if m['dur'] else None,
                'distance_m': round(m['dist'],1) if m['dist'] else None,
                'avg_hr': m['avg_hr'], 'max_hr': m['max_hr'],
                'avg_power_w': m['avg_power_w'], 'max_power_w': m['max_power_w'],
                'avg_cadence': m['avg_cadence'],
                'recovery_hr_low': lows[i-1] if role in ('recovery',) else None,
                'avg_speed_kmh': (round(m['dist']/m['dur']*3.6,2) if m['dur'] and m['dist'] else None),
            })
    if dry:
        print(json.dumps({'session': sess, 'n_steps': len(steps), 'n_reps': len(reps),
                          'reps':[{'i':r['rep_index'],'role':r['step_role'],'zone':r['zone_slug'],
                                   'mod':r['zone_modifier'],'dur':r['duration_s'],'hr':r['avg_hr'],
                                   'rec_low':r['recovery_hr_low']} for r in reps]}, indent=2, default=str))
        return sess, reps

    # ---- write (idempotent) ----
    existing = sql(f"SELECT id FROM training_session WHERE garmin_activity_id={int(activity_id)}")
    # snapshot human rep columns before rewrite
    human = {}
    if existing:
        sid = existing[0]['id']
        hr = sql(f"SELECT rep_index, rep_rpe, rep_feel, zone_slug, zone_source FROM training_rep WHERE session_id='{sid}' AND (rep_rpe IS NOT NULL OR rep_feel IS NOT NULL OR zone_source='manual')")
        for r in hr: human[r['rep_index']] = r
    cols = [k for k in sess if sess[k] is not None]
    if existing:
        setc = ", ".join(f"{k}={q_lit(sess[k])}" for k in cols if k!='garmin_activity_id')
        sql(f"UPDATE training_session SET {setc}, updated_at=now() WHERE garmin_activity_id={int(activity_id)}")
        sid = existing[0]['id']
        sql(f"DELETE FROM training_rep WHERE session_id='{sid}'")
    else:
        collist = ", ".join(cols); vallist = ", ".join(q_lit(sess[k]) for k in cols)
        row = sql(f"INSERT INTO training_session ({collist}) VALUES ({vallist}) RETURNING id")
        sid = row[0]['id']
    for r in reps:
        # re-apply human columns
        h = human.get(r['rep_index'])
        if h:
            if h.get('rep_rpe') is not None: r['rep_rpe'] = h['rep_rpe']
            if h.get('rep_feel') is not None: r['rep_feel'] = h['rep_feel']
            if h.get('zone_source')=='manual': r['zone_slug']=h['zone_slug']; r['zone_source']='manual'
        rc = {k:v for k,v in r.items() if v is not None}
        rc['session_id'] = sid
        cl = ", ".join(rc); vl = ", ".join(q_lit(rc[k]) for k in rc)
        sql(f"INSERT INTO training_rep ({cl}) VALUES ({vl})")
    return sid, len(reps)

def backfill(start, end=None):
    g = GarminAPI()
    end = end or '2026-07-31'
    acts = g.activities(start, end)
    acts = acts if isinstance(acts, list) else [acts]
    done = 0; skipped = 0
    for a in acts:
        tk = (a.get('activityType') or {}).get('typeKey','')
        if not any(x in tk for x in ('run','swim','cyc','bik','strength')):
            skipped += 1; continue
        aid = a.get('activityId')
        try:
            sid, n = ingest(aid)
            print(f"  ingested {aid} ({tk}) -> {n} reps")
            done += 1
        except Exception as e:
            print(f"  FAILED {aid} ({tk}): {str(e)[:160]}")
    print(f"backfill done: {done} ingested, {skipped} skipped")

if __name__ == '__main__':
    args = [x for x in sys.argv[1:]]
    if '--backfill' in args:
        i = args.index('--backfill'); backfill(args[i+1], args[i+2] if len(args)>i+2 else None)
    elif '--dry' in args:
        i = args.index('--dry'); ingest(int(args[i+1]), dry=True)
    else:
        sid, n = ingest(int(args[0]))
        print(f"ingested {args[0]} -> session {sid}, {n} reps")
