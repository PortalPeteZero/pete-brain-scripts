#!/usr/bin/env python3
"""Weekly sync: JotForm Training Evaluation → vault → dashboard repo → Vercel.

Wired as the scheduled task `jotform-training-eval-sync` (Mon 06:30 UK).

Steps:
  1. Incremental pull from JotForm — only fetch submissions newer than
     the most recent already in `all-submissions-raw.json`.
  2. Save / extend the year-bucketed JSON in
     `Properties/Sygma Solutions Website/data/training-evaluations/`.
  3. Rebuild `all-normalised.json` (applies the latest YAML normaliser rules).
  4. Run `jotform-training-eval-aggregate.py` to refresh dashboard JSON files.
  5. `git push` the dashboard repo at `~/code/sygma-training-eval-dashboard`
     → Vercel auto-deploys.
  6. Append a summary line to the daily note + exit.

Idempotent. Safe to re-run. Designed for the Desktop Commander launch path:
  /opt/homebrew/bin/python3 Library/processes/scripts/jotform-training-eval-sync.py

Created 2026-05-30.
"""

import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import os, tempfile  # noqa: E402

# CRON-META
# what: Weekly JotForm Training Evaluation sync → Portal hub.training_evaluations (pull new submissions → normalise → aggregate → load the Portal table the /hub/training-evaluation page reads)
# why: keeps Sygma's training-evaluation dashboard current on the Platform (the Hub page renders from hub.training_evaluations)
# reads: JotForm API (jotform-api-key); chains aggregate + eval-hub-load
# writes: Portal hub.training_evaluations (rsczwfstwkthaybxhszy); intermediate JSON in an ephemeral data dir (headless)
# entity: sygma
# schedule: 34 7 * * 1
# timezone: Atlantic/Canary
# CRON-META-END

VAULT = Path(os.environ.get("VAULT", "/tmp/pbs"))
KEY_FILE = VAULT / "Library/processes/secrets/jotform-api-key"
# Headless (Railway): no vault / dashboard-repo — run the WHOLE pipeline through ONE ephemeral data dir
# (sync → aggregate → eval-hub-load all read/write it; only the final Portal table is persistent).
if os.environ.get("VAULT") and not os.environ.get("EVAL_DATA_DIR"):
    _dd = Path(tempfile.gettempdir()) / "training-eval-data"
    _dd.mkdir(parents=True, exist_ok=True)
    os.environ["EVAL_DATA_DIR"] = str(_dd)
DATA_DIR = Path(os.environ["EVAL_DATA_DIR"]) if os.environ.get("EVAL_DATA_DIR") else (VAULT / "Properties/Sygma Solutions Website/data/training-evaluations")
DASHBOARD_REPO = Path.home() / "code/sygma-training-eval-dashboard"
SCRIPTS = Path(__file__).resolve().parent  # flat-repo siblings on Railway (/app)
DAILY = VAULT / "Daily"
FORM_ID = "201324458767056"   # Avoidance — historical + new (legacy single-form ID)

# Multi-form era (from 2026-05-30): two forms feed the dashboard. Each tagged with form_source.
FORMS = {
    "avoidance": "201324458767056",
    "mapping":   "261502223670043",
}

import importlib.util


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def fetch_since(since: str) -> list[dict]:
    """Fetch all submissions newer than `since` from BOTH forms (avoidance + mapping).
    Each submission is tagged with form_source = 'avoidance' | 'mapping'."""
    KEY = KEY_FILE.read_text().strip()
    rows = []
    for form_source, form_id in FORMS.items():
        offset = 0
        while True:
            f = urllib.parse.quote(json.dumps({"created_at:gt": since}))
            url = f"https://api.jotform.com/form/{form_id}/submissions?apiKey={KEY}&limit=1000&offset={offset}&filter={f}"
            with urllib.request.urlopen(url, timeout=45) as r:
                page = json.loads(r.read())
            chunk = page.get("content", [])
            if not chunk: break
            for s in chunk: s["form_source"] = form_source
            rows.extend(chunk)
            offset += len(chunk)
            if len(chunk) < 1000: break
            time.sleep(0.3)
    return rows


def update_year_files(new_rows: list[dict]) -> dict:
    """Distribute new rows into year-bucketed JSON files (dedup by id)."""
    by_year = {}
    for s in new_rows:
        try:
            y = s["created_at"][:4]
            by_year.setdefault(y, []).append(s)
        except: pass
    stats = {}
    for y, rows in by_year.items():
        p = DATA_DIR / f"submissions-{y}.json"
        existing = json.loads(p.read_text()) if p.exists() else []
        existing_ids = {s.get("id") for s in existing}
        truly_new = [s for s in rows if s.get("id") not in existing_ids]
        if truly_new:
            existing.extend(truly_new)
            p.write_text(json.dumps(existing, indent=1))
        stats[y] = len(truly_new)
    return stats


def rebuild_normalised():
    """Re-run the normaliser over every year file → all-normalised.json."""
    jn = _load_module("jotform_normalise", SCRIPTS / "jotform-normalise.py")
    from datetime import datetime, timezone, timedelta
    from zoneinfo import ZoneInfo

    # JotForm timestamps are DST-aware America/New_York (EDT in summer, EST in winter),
    # NOT a fixed -5 offset. See Library/lessons/2026-05-30-jotform-api-tz-is-dst-aware-us-eastern-not-fixed-utc5.md.
    # Previous fixed-offset code shifted every summer-month submission by 1 hour.
    JOTFORM_TZ = ZoneInfo("America/New_York")
    UK = ZoneInfo("Europe/London")

    SCORE_MAP = {"Strongly Agree":4, "Agree":3, "Disagree":2, "Strongly Disagree":1}
    SCORE_QS = [("11","objectives_clear"),("12","objectives_met"),("13","useful_in_work"),
                ("14","materials"),("15","duration"),("16","trainer_presented"),("18","trainer_knowledgeable")]
    POS = {"Interesting","Exciting","Fascinating","Easy","Realistic","Useful","Inspiring","Enjoyable","Comprehensive","Clear","Thorough","Helpful","Stimulating","Practical","Excellent","Engaging","Worthwhile","Informative","Relevant","Educational","Motivating","Valuable","Revealing"}
    NEG = {"Confusing","Basic","Theoretical","Complicated","Boring","Difficult","Dry","Disorganized","Slow","Poor","Tedious","Repetitive","Irrelevant"}

    def ans_by_qid(s, qid):
        a = s.get("answers",{}).get(qid,{})
        v = a.get("answer","")
        if isinstance(v, dict):
            if "first" in v: return f"{v.get('first','')} {v.get('last','')}".strip()
            if "day" in v: return f"{v.get('year','')}-{v.get('month','')}-{v.get('day','')}"
            return v.get("datetime","")
        if isinstance(v, list): return "|".join(map(str,v))
        return str(v).strip()

    def ans_by_name(s, name):
        """Find an answer by question NAME (not qid). Robust to form changes that
        delete + recreate a question with a new qid (e.g. textbox → dropdown)."""
        for qid, q in s.get("answers", {}).items():
            if q.get("name") == name:
                v = q.get("answer","")
                if isinstance(v, dict):
                    if "first" in v: return f"{v.get('first','')} {v.get('last','')}".strip()
                    if "day" in v: return f"{v.get('year','')}-{v.get('month','')}-{v.get('day','')}"
                    return v.get("datetime","")
                if isinstance(v, list): return "|".join(map(str,v))
                return str(v).strip()
        return ""

    # Keep ans() as a back-compat alias that prefers qid but falls through to name
    ans = ans_by_qid

    all_subs = []
    for f in sorted(DATA_DIR.glob("submissions-*.json")):
        all_subs.extend(json.loads(f.read_text()))

    records = []
    for s in all_subs:
        try:
            naive = datetime.strptime(s["created_at"], "%Y-%m-%d %H:%M:%S")
            ts_uk = naive.replace(tzinfo=JOTFORM_TZ).astimezone(UK)
        except: continue
        # Look up trainer + course by field NAME (qids changed when dropdowns were
        # introduced 2026-05-30; old submissions still have q5/q7 textbox answers
        # which carry name="course"/"trainer" so the lookup works both eras).
        raw_trainer = ans_by_name(s, "trainer") or ans_by_qid(s, "7")
        trainer_results = jn.normalise_trainer(raw_trainer) if raw_trainer else [(None,"unmatched","")]
        primary = trainer_results[0][0] if trainer_results else None
        raw_course = ans_by_name(s, "course") or ans_by_qid(s, "5")
        course_canon, course_conf, _ = jn.normalise_course(raw_course) if raw_course else (None,"unmatched","")
        # Practical-balance (new question added 2026-05-30, name="practicalBalance")
        practical = ans_by_name(s, "practicalBalance")
        form_source = s.get("form_source", "avoidance")   # default for legacy submissions
        # GPS — TWO widgets on each form (added 2026-05-31 evening). Identified by
        # `text` value (not `name` — JotForm auto-names widget fields as `typeA`,
        # `typeA44`, etc. which don't change when title is renamed):
        #
        #   1. Get Visitor Location widget (text="Training Location")
        #      Answer shape: multi-line "IP: X\nCountry: Y\nCity: Z\nLatitude: A\nLongitude: B"
        #      → geo_ip, geo_city, geo_country, geo_lat, geo_lon (IP-based, ~10-50km accuracy)
        #
        #   2. Location Coordinates widget (text="Location Coordinates")
        #      Answer shape: dict {"lat":..., "lng":...} or pipe-separated "lat|lon"
        #      → gps_lat, gps_lon (device GPS, ~metres accuracy, requires browser permission)
        #
        # Both fire automatically. IP widget needs no user interaction; device GPS
        # requires one-time browser permission prompt (then silent forever for that domain).
        raw_geo = s.get("answers", {})
        geo_lat = geo_lon = geo_ip = geo_city = geo_country = None
        gps_lat = gps_lon = None
        for qid, q in raw_geo.items():
            text = (q.get("text") or "").lower()
            qtype = q.get("type", "")
            if qtype != "control_widget": continue
            v = q.get("answer", "")

            # Device GPS widget — text="Location Coordinates"
            if "location coordinates" in text:
                if isinstance(v, dict):
                    gps_lat = v.get("lat") or v.get("latitude")
                    gps_lon = v.get("lng") or v.get("longitude") or v.get("lon")
                elif isinstance(v, str) and v.strip():
                    sep = "|" if "|" in v else "," if "," in v else None
                    if sep:
                        parts = [p.strip() for p in v.split(sep)]
                        if len(parts) >= 2:
                            try:
                                gps_lat = float(parts[0])
                                gps_lon = float(parts[1])
                            except: pass
                continue

            # IP widget — text="Training Location"
            if "training location" in text or q.get("name") == "trainingLocation":
                if isinstance(v, dict):
                    geo_lat = v.get("lat") or v.get("latitude")
                    geo_lon = v.get("lng") or v.get("longitude") or v.get("lon")
                    geo_ip = v.get("ip") or v.get("IP")
                    geo_city = v.get("city") or v.get("City")
                    geo_country = v.get("country") or v.get("Country")
                elif isinstance(v, str) and v.strip():
                    for line in v.splitlines():
                        line = line.strip()
                        if ":" not in line: continue
                        k, _, val = line.partition(":")
                        k, val = k.strip().lower(), val.strip()
                        if k == "latitude":
                            try: geo_lat = float(val)
                            except: pass
                        elif k == "longitude":
                            try: geo_lon = float(val)
                            except: pass
                        elif k == "ip": geo_ip = val
                        elif k == "city": geo_city = val
                        elif k == "country": geo_country = val
                continue
        likert = {}
        for qid, key in SCORE_QS:
            v = ans(s, qid)
            if v in SCORE_MAP: likert[key] = SCORE_MAP[v]
        rating = ans(s,"29")
        try: rating = int(rating) if rating else None
        except: rating = None
        sumup_raw = ans(s,"26")
        sumup = [w.strip() for w in (sumup_raw.split("|") if sumup_raw else []) if w.strip()]
        likert_avg = sum(likert.values())/len(likert) if likert else None
        flags = []
        for key in likert:
            if likert[key] <= 2: flags.append(f"low_{key}")
        if rating and rating <= 3: flags.append(f"rating_{rating}")
        if likert:
            v = list(likert.values())
            if all(x==1 for x in v) and (rating and rating >= 4): flags = ["SCALE_FLIP"]
            elif all(x==4 for x in v) and (rating and rating <= 2): flags = ["SCALE_FLIP"]
        records.append({
            "id": s["id"],
            "form_source": form_source,
            "practical_balance": practical,
            "geo_lat": geo_lat,
            "geo_lon": geo_lon,
            "geo_ip": geo_ip,
            "geo_city": geo_city,
            "geo_country": geo_country,
            "gps_lat": gps_lat,
            "gps_lon": gps_lon,
            "ts_uk": ts_uk.strftime("%Y-%m-%d %H:%M:%S"),
            "date_uk": ts_uk.strftime("%Y-%m-%d"),
            "ym": ts_uk.strftime("%Y-%m"),
            "hour_uk": ts_uk.hour,
            "delegate": ans(s,"3"),
            "company": ans(s,"4"),
            "trainer_raw": raw_trainer,
            "trainer": primary,
            "trainer_confidence": trainer_results[0][1] if trainer_results else "unmatched",
            "course_raw": raw_course,
            "course": course_canon,
            "course_confidence": course_conf,
            "location": ans(s,"8"),
            "likert": likert,
            "likert_avg": round(likert_avg,3) if likert_avg else None,
            "rating": rating,
            "sumup": sumup,
            "sumup_positive": [w for w in sumup if w in POS],
            "sumup_negative": [w for w in sumup if w in NEG],
            "learning": ans(s,"24"),
            "would_change": ans(s,"25"),
            "additional": ans(s,"28"),
            "flags": flags,
        })
    out = DATA_DIR / "all-normalised.json"
    out.write_text(json.dumps(records, indent=1))
    return len(records)



def _hub_sql(sql):
    """Run SQL against the Portal (Hub) DB — used only for the durable submission cursor."""
    import urllib.request as _u
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = _u.Request("https://api.supabase.com/v1/projects/rsczwfstwkthaybxhszy/database/query",
                     data=json.dumps({"query": sql}).encode(), method="POST",
                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                              "User-Agent": "Mozilla/5.0"})
    return json.loads(_u.urlopen(req, timeout=60).read())


def _cursor_from_hub():
    """The last submission timestamp this pipeline saw, stored where it SURVIVES the working space
    being wiped. Without this the run falls back to a hardcoded date and silently republishes a
    part-year as the whole dataset — the defect that hid 16,000+ submissions."""
    try:
        r = _hub_sql("SELECT data->>'last_submission_ts' AS ts "
                     "FROM hub.training_evaluations WHERE key='metadata'")
        return (r[0].get("ts") if r else None) or None
    except Exception as e:
        print(f"  (could not read cursor from the Hub: {e})", file=sys.stderr)
        return None


def _cursor_to_hub(ts):
    """Persist the cursor after a successful fetch, so the next run picks up where this one stopped."""
    if not ts:
        return
    try:
        _hub_sql("UPDATE hub.training_evaluations "
                 f"SET data = jsonb_set(data, '{{last_submission_ts}}', '\"{ts}\"'::jsonb), "
                 "updated_at = now() WHERE key='metadata'")
        print(f"  cursor stored in the Hub: {ts}")
    except Exception as e:
        print(f"  WARN: could not store cursor in the Hub ({e}) — next run may re-fetch", file=sys.stderr)

def rebuild_dashboard_data():
    """Run jotform-training-eval-aggregate.py → dashboard /data/ files.

    A non-zero exit used to be swallowed here: stdout/stderr were captured, printed, and the run
    reported SUCCESS regardless. That is how a broken aggregate could publish partial or zero-valued
    data with nothing flagging it. The return code is now authoritative (19 Jul 2026)."""
    res = subprocess.run([sys.executable, str(SCRIPTS / "jotform-training-eval-aggregate.py")],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"aggregate FAILED (exit {res.returncode}) — refusing to report success.\n"
            f"stdout: {res.stdout.strip()[-1500:]}\nstderr: {res.stderr.strip()[-1500:]}")
    return res.stdout.strip(), res.stderr.strip()


def git_push():
    """RETIRED 2026-06-08 — the standalone Vercel dashboard is being decommissioned; the Sygma
    Internal Hub (sygmaportal.com/hub/training-evaluation) is now the live target, fed by the
    aggregator's `eval-hub-load.py` final step (runs inside rebuild_dashboard_data, above). This
    is left as a no-op so the weekly sync still refreshes the Hub but no longer deploys the
    standalone. To re-enable the standalone, restore the git add/commit/push below:
        # subprocess.run(["git","-C",str(DASHBOARD_REPO),"add","data/"], check=True)
        # subprocess.run(["git","-C",str(DASHBOARD_REPO),"commit","-m", ...], check=True)
        # subprocess.run(["git","-C",str(DASHBOARD_REPO),"push","origin","HEAD"], check=False)
    """
    return "skipped (standalone retired — Hub is the live target)"


def append_daily(line):
    if not DAILY.exists():  # headless (Railway): no vault Daily/ — skip
        return
    today = date.today().strftime("%Y-%m-%d")
    p = DAILY / f"{today}.md"
    if not p.exists():
        p.write_text(f"---\ntype: daily\ndate: {today}\ntags: [daily]\n---\n\n# Daily {today}\n\n")
    txt = p.read_text()
    section = "## JotForm Training Eval sync (Automated)\n"
    if section not in txt:
        txt += "\n" + section
    txt += f"- {line}\n"
    p.write_text(txt)


def main():
    # Find most recent timestamp already in our cache
    latest_ts = None
    for f in sorted(DATA_DIR.glob("submissions-*.json")):
        try:
            for s in json.loads(f.read_text()):
                ts = s.get("created_at")
                if ts and (latest_ts is None or ts > latest_ts):
                    latest_ts = ts
        except: pass
    if not latest_ts:
        # The working space is wiped between runs on Railway, so there is never a local cursor and
        # this fires EVERY run. Warning about it was not enough (19 Jul) — the run still republished
        # a part-year as though it were everything. So REMEMBER THE CURSOR SOMEWHERE THAT SURVIVES:
        # the Hub table itself, which is the one thing that persists. (20 Jul 2026)
        latest_ts = _cursor_from_hub()
        if latest_ts:
            print(f"  cursor recovered from the Hub: {latest_ts} (no local cache, as expected on Railway)")
        else:
            latest_ts = os.environ.get("EVAL_BACKFILL_SINCE", "2026-01-01 00:00:00")
            print(
                "WARNING: no local cache AND no cursor stored in the Hub — this run will fetch only\n"
                f"         from {latest_ts}, so the rebuilt dataset is NOT the full history.\n"
                "         Set EVAL_BACKFILL_SINCE to widen it.", file=sys.stderr)
    print(f"Last cached submission: {latest_ts}")

    new_rows = fetch_since(latest_ts)
    print(f"Fetched {len(new_rows)} new submissions since {latest_ts}")

    if not new_rows:
        append_daily(f"No new submissions since {latest_ts}.")
        print("No changes to push.")
        return

    _cursor_to_hub(max((r.get("created_at") or "") for r in new_rows) or None)
    stats = update_year_files(new_rows)
    print(f"Updated year files: {stats}")

    total = rebuild_normalised()
    print(f"Normalised dataset rebuilt: {total} records")

    # Rebuild calendar-truth cache BEFORE the aggregator (aggregator depends on it).
    # Truth-builder enforces one-event-per-trainer-date + no-future-dates + tz-aware-start invariants.
    truth_result = rebuild_calendar_truth_cache()
    print(f"Calendar truth cache: {truth_result}")

    out, err = rebuild_dashboard_data()
    print(f"Aggregate: {out}")

    push_result = git_push()
    print(f"Git: {push_result}")

    append_daily(f"+{len(new_rows)} new submissions ({sum(stats.values())} after dedup). Total dataset: {total}. Calendar truth: {truth_result}. Dashboard sync: {push_result}.")


def rebuild_calendar_truth_cache():
    """jotform-calendar-truth-builder.py was retired 2 Jul 2026 (dead code, never invoked on
    Railway where this cron actually runs). This function is kept as a no-op so callers/logging
    below don't need to change."""
    return "skipped (calendar-truth-builder retired)"


if __name__ == "__main__":
    main()
