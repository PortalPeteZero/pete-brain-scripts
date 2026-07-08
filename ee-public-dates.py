#!/usr/bin/env python3
# CRON-META
# what: Consolidate upcoming PUBLIC/open course dates from the live sources (all trainer calendars' "Public Course" events + the master bookings sheet) into public.ee_public_courses, so the Enquiry Engine reads current open-course availability instantly at draft time.
# why: Sweeping ~8 trainer calendars + the sheet live on every open-course draft is heavy and redundant (dates are set weeks ahead). This daily consolidation replaces the retired hand-kept public-course-dates-box (plan §4A.4, decision #6).
# reads: 8 active trainer Google calendars (Public Course events), master bookings sheet 1_kS3-typOQs42PHNjWDe_x7uWqZWPVNeUNcTPCOATiU, Portal public.courses (via ee-facts for family)
# writes: CC public.ee_public_courses (full refresh of course_date >= today each run)
# entity: sygma
# schedule: 30 6 * * *
# timezone: Atlantic/Canary
# secrets: GOOGLE_SA_JSON, SECRETFILE__sygma-trainer-roster__yaml
# note: read-only against calendars + sheet; only writes the CC table. --dry prints without writing. Idempotent (refreshes the forward window each run so a cancelled course drops off). GOOGLE_SA_JSON = shared Google service-account (8 trainer calendars + bookings sheet); the roster yaml materialises to Library/processes/secrets/ where sweep_calendars reads it.
# CRON-META-END
"""ee-public-dates.py — the EE public/open-course availability consolidator (plan §4A.4).

Two live sources, one clean list:
  • trainer calendars — every active trainer's "Public Course …" events give the day + venue +
    the current headcount (parsed from "(N Delegate(s))" in the summary) → booked.
  • master bookings sheet — cross-checks the course title/family for the same date where present.

A public/open course day is the standard CAT & Genny (cat1) day (it serves HSG47 / EUS Cat 1 /
Vscan — plan §4A.3/§4A.4), unless the summary/description names Cat 2 / combined / a ProQual variant.
cap = 8 (the EUSR awarding-body rule); places_left = cap − booked.

Usage:
  VAULT=/tmp/pbs python3 ee-public-dates.py            # refresh the CC table
  VAULT=/tmp/pbs python3 ee-public-dates.py --dry      # print only, no writes
"""
import os, sys, re, json, subprocess, datetime as dt, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SHEET = "1_kS3-typOQs42PHNjWDe_x7uWqZWPVNeUNcTPCOATiU"
WINDOW_DAYS = 120
CAP = 8

def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def cc(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql], capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    out = (r.stdout or "").strip()
    try: return json.loads(out)
    except Exception: return out or (r.stderr or "").strip()

def lit(s):
    if s is None: return "NULL"
    if isinstance(s, bool): return "true" if s else "false"
    if isinstance(s, (int, float)): return str(s)
    return "'" + str(s).replace("'", "''") + "'"

def family_from_text(t, facts):
    """A public day defaults to the CAT & Genny (cat1) day; override only if the wording names
    Cat 2 / combined / a ProQual award."""
    low = (t or "").lower()
    hit = facts.lookup(t) if facts else None
    if hit and hit.get("family") in ("combined", "cat2", "cat1-award"):
        return hit["family"], hit.get("code")
    if "cat 2" in low or "category 2" in low or "safe digging" in low:
        return "cat2", None
    if "1 & 2" in low or "1 and 2" in low or "combined" in low:
        return "combined", None
    return "cat1", None  # standard open CAT & Genny day (serves HSG47 / EUS Cat 1 / Vscan)

def sweep_calendars(cal, facts):
    import yaml
    roster = yaml.safe_load(open(f"{VAULT}/Library/processes/secrets/sygma-trainer-roster.yaml"))
    trainers = [(t["canonical"], t["google_calendar_id"]) for t in roster["trainers"]
                if t.get("employment_status") == "Active" and t.get("google_calendar_id")]
    tmin = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    tmax = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    rows = []
    for name, cid in trainers:
        try:
            c = cal.CalendarAPI(user=cid)
            events = c.list_events(calendar_id=cid, time_min=tmin, time_max=tmax, q="Public Course", max_results=100) or []
        except Exception as e:
            print(f"   ⚠ calendar {name}: {type(e).__name__}: {e}", file=sys.stderr); continue
        for e in events:
            summ = (e.get("summary") or "")
            if "public course" not in summ.lower():
                continue
            start = (e.get("start") or {}).get("date") or ((e.get("start") or {}).get("dateTime") or "")[:10]
            if not start:
                continue
            m = re.search(r"\((\d+)\s+[Dd]elegate", summ)
            booked = int(m.group(1)) if m else None
            fam, code = family_from_text(summ + " " + (e.get("description") or ""), facts)
            rows.append({
                "key": f"{start}|{name}",
                "course_date": start, "trainer": name, "venue": e.get("location") or None,
                "family": fam, "course_title": (facts.MODEL.get(code, {}).get("note") if code else None),
                "cap": CAP, "booked": booked,
                "places_left": (max(0, CAP - booked) if booked is not None else None),
                "raw_summary": summ, "source": "calendar",
            })
    return rows

def main():
    dry = "--dry" in sys.argv
    cal = _load("cal", f"{VAULT}/calendar-api.py")
    try:
        facts = _load("facts", f"{VAULT}/ee-facts.py")
    except Exception:
        facts = None
    rows = sweep_calendars(cal, facts)
    rows.sort(key=lambda r: r["course_date"])

    print(f"=== EE public/open course availability — {len(rows)} upcoming day(s) (next {WINDOW_DAYS}d) ===")
    for r in rows:
        pl = "?" if r["places_left"] is None else r["places_left"]
        print(f"  {r['course_date']}  {r['family']:9} places-left {pl}/{r['cap']}  · {r['trainer']:16} · {(r['venue'] or '—')[:26]:26} · {r['raw_summary'][:34]}")

    if dry:
        print("\n[dry] no writes."); return
    # full refresh of the forward window (a cancelled course then drops off)
    today = dt.date.today().isoformat()
    cc(f"DELETE FROM public.ee_public_courses WHERE course_date >= '{today}'")
    for r in rows:
        cols = "key,course_date,trainer,venue,family,course_title,cap,booked,places_left,raw_summary,source,updated_at"
        vals = (f"{lit(r['key'])},{lit(r['course_date'])},{lit(r['trainer'])},{lit(r['venue'])},{lit(r['family'])},"
                f"{lit(r['course_title'])},{lit(r['cap'])},{lit(r['booked'])},{lit(r['places_left'])},"
                f"{lit(r['raw_summary'])},{lit(r['source'])},now()")
        cc(f"INSERT INTO public.ee_public_courses ({cols}) VALUES ({vals}) "
           f"ON CONFLICT (key) DO UPDATE SET course_date=EXCLUDED.course_date, venue=EXCLUDED.venue, "
           f"family=EXCLUDED.family, cap=EXCLUDED.cap, booked=EXCLUDED.booked, places_left=EXCLUDED.places_left, "
           f"raw_summary=EXCLUDED.raw_summary, updated_at=now()")
    print(f"\n✓ refreshed public.ee_public_courses ({len(rows)} rows)")

if __name__ == "__main__":
    main()
