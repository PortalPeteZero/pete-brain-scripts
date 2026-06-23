#!/usr/bin/env python3
"""Aggregate Sygma Training Evaluation data into dashboard-ready JSON files.

Reads the normalised submission records from
  `Properties/Sygma Solutions Website/data/training-evaluations/all-normalised.json`
and writes per-page aggregate files into the Vercel dashboard repo's
`/data/` directory:

  data/overview.json            - top KPIs + monthly trend
  data/trainers.json            - trainer leaderboard + rolling 30/90/365d
  data/trainer/{slug}.json      - per-trainer detail (one file per trainer)
  data/courses.json             - course bucket leaderboard
  data/course/{slug}.json       - per-course detail
  data/concerns.json            - flagged-for-investigation queue
  data/share/{ym}.json          - per-month client-share data ("Apr 2026" page)
  data/metadata.json            - last-sync time + dataset stats

Runs cheap (~5 sec on the full 17,929 set). Idempotent. Safe to re-run.

Designed to be called by:
  - manual: `python3 jotform-training-eval-aggregate.py`
  - the weekly cron: `jotform-training-eval-sync`
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import os  # noqa: E402
VAULT = Path(os.environ.get("VAULT", "/Users/peterashcroft/Second Brain"))
_EVAL_DD = os.environ.get("EVAL_DATA_DIR")
VAULT_DATA = Path(_EVAL_DD) if _EVAL_DD else (VAULT / "Properties/Sygma Solutions Website/data/training-evaluations")
NORMALISED = VAULT_DATA / "all-normalised.json"
DASHBOARD_REPO = Path.home() / "code/sygma-training-eval-dashboard"
DATA_OUT = Path(_EVAL_DD) if _EVAL_DD else (DASHBOARD_REPO / "data")


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s or "unknown"


def percentile(values, q):
    if not values:
        return None
    vs = sorted(values)
    return vs[min(len(vs) - 1, int(len(vs) * q))]


def hour_dec(r):
    """Finish time as a DECIMAL hour (14:40 -> 14.67), parsed from the full ts_uk
    timestamp. The old code used the integer hour_uk, which truncated every finish to a
    whole hour and made finish-time reports look artificially uniform (everything landed
    on 13:00/14:00/15:00). The minutes were never lost — they live in ts_uk. Falls back
    to the integer hour_uk if ts_uk is missing/unparseable."""
    ts = r.get("ts_uk")
    if ts:
        try:
            dt = datetime.fromisoformat(str(ts).replace(" ", "T"))
            return dt.hour + dt.minute / 60 + dt.second / 3600
        except Exception:
            pass
    h = r.get("hour_uk")
    return (h + 0.0) if h is not None else 0.0


# Plain-English labels for internal flag names → used by concerns + per-trainer pages
FLAG_LABELS = {
    "low_objectives_clear":     "Said objectives weren't clear",
    "low_objectives_met":       "Said objectives weren't met",
    "low_useful_in_work":       "Said learning won't be useful in their work",
    "low_materials":            "Said materials were hard to follow",
    "low_duration":             "Said duration wasn't right",
    "low_trainer_presented":    "Said trainer didn't present clearly",
    "low_trainer_knowledgeable":"Said trainer wasn't knowledgeable",
    "rating_1":                 "1-star rating",
    "rating_2":                 "2-star rating",
    "rating_3":                 "3-star rating",
    "SCALE_FLIP":               "Looks like the form was filled in upside-down (likely mis-fill)",
}


def humanise_flags(flags: list[str]) -> list[str]:
    return [FLAG_LABELS.get(f, f) for f in flags]


def short_ref(submission_id) -> str:
    """C-XXXXXX from last 6 chars of submission id — stable forever, copy-paste friendly."""
    return f"C-{str(submission_id)[-6:]}"


def iso_week(date_str: str) -> str:
    """YYYY-Www format (ISO week), e.g. '2026-W22'."""
    d = datetime.fromisoformat(date_str)
    iy, iw, _ = d.isocalendar()
    return f"{iy}-W{iw:02d}"


def main():
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    (DATA_OUT / "trainer").mkdir(exist_ok=True)
    (DATA_OUT / "course").mkdir(exist_ok=True)
    (DATA_OUT / "share").mkdir(exist_ok=True)
    (DATA_OUT / "weekly").mkdir(exist_ok=True)
    (DATA_OUT / "monthly").mkdir(exist_ok=True)

    records = json.loads(NORMALISED.read_text())
    today = datetime.now(timezone.utc).date()

    # ---------- Top-level metadata ----------
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_submissions": len(records),
        "first_submission": min((r["date_uk"] for r in records), default=None),
        "last_submission": max((r["date_uk"] for r in records), default=None),
        "trainer_normalisation": {
            "matched": sum(1 for r in records if r["trainer"]),
            "ambiguous": sum(1 for r in records if r["trainer_confidence"] == "ambiguous"),
            "unmatched": sum(1 for r in records if r["trainer_confidence"] == "unmatched"),
        },
        "course_normalisation": {
            "matched": sum(1 for r in records if r["course"]),
            "ambiguous": sum(1 for r in records if r["course_confidence"] == "ambiguous"),
            "unmatched": sum(1 for r in records if r["course_confidence"] == "unmatched"),
        },
    }

    # ---------- Overview ----------
    valid_ratings = [r["rating"] for r in records if r["rating"]]
    promoters = sum(1 for r in valid_ratings if r == 5)
    passives = sum(1 for r in valid_ratings if r == 4)
    detractors = sum(1 for r in valid_ratings if r and r <= 3)
    nps = (
        round((promoters - detractors) / len(valid_ratings) * 100, 1)
        if valid_ratings else None
    )

    by_month = defaultdict(list)
    for r in records:
        by_month[r["ym"]].append(r)
    monthly = []
    for ym in sorted(by_month):
        rows = by_month[ym]
        rs = [x["rating"] for x in rows if x["rating"]]
        monthly.append({
            "ym": ym,
            "n": len(rows),
            "avg_rating": round(sum(rs) / len(rs), 2) if rs else None,
            "pct_5_star": round(sum(1 for x in rs if x == 5) / len(rs) * 100, 1) if rs else None,
            "flagged": sum(1 for x in rows if x["flags"]),
        })

    dim_avgs = {}
    for key in ["objectives_clear","objectives_met","useful_in_work","materials","duration","trainer_presented","trainer_knowledgeable"]:
        vals = [r["likert"].get(key) for r in records if r["likert"].get(key)]
        if vals:
            dim_avgs[key] = {
                "n": len(vals),
                "avg": round(sum(vals)/len(vals), 3),
                "pct_top": round(sum(1 for v in vals if v == 4) / len(vals) * 100, 1),
            }

    sentiment = Counter()
    for r in records:
        for w in r["sumup"]: sentiment[w] += 1

    overview = {
        "kpis": {
            "total": len(records),
            "avg_rating": round(sum(valid_ratings)/len(valid_ratings), 2) if valid_ratings else None,
            "pct_5_star": round(promoters/len(valid_ratings)*100, 1) if valid_ratings else None,
            "nps": nps,
            "rating_distribution": {str(i): sum(1 for r in valid_ratings if r == i) for i in range(1, 6)},
            "flagged_total": sum(1 for r in records if r["flags"]),
            "flagged_pct": round(sum(1 for r in records if r["flags"])/len(records)*100, 2) if records else None,
        },
        "monthly": monthly,
        "dim_avgs": dim_avgs,
        "sentiment_top": sentiment.most_common(25),
    }
    (DATA_OUT / "overview.json").write_text(json.dumps(overview, indent=1))

    # ---------- Trainers ----------
    by_trainer = defaultdict(list)
    for r in records:
        if r["trainer"]:
            by_trainer[r["trainer"]].append(r)

    trainer_summaries = []
    for trainer, rows in sorted(by_trainer.items(), key=lambda kv: -len(kv[1])):
        rs = [x["rating"] for x in rows if x["rating"]]
        ds = [x["likert_avg"] for x in rows if x["likert_avg"]]
        # 30/90/365d windows
        windows = {}
        for label, days in [("30d", 30), ("90d", 90), ("365d", 365)]:
            cutoff = today - timedelta(days=days)
            window_rows = [x for x in rows if datetime.fromisoformat(x["date_uk"]).date() >= cutoff]
            wrs = [x["rating"] for x in window_rows if x["rating"]]
            windows[label] = {
                "n": len(window_rows),
                "avg_rating": round(sum(wrs)/len(wrs), 2) if wrs else None,
                "flagged": sum(1 for x in window_rows if x["flags"]),
            }
        # Trend across the trainer's tenure (by month)
        by_m = defaultdict(list)
        for x in rows: by_m[x["ym"]].append(x)
        trend = []
        for ym in sorted(by_m):
            mrows = by_m[ym]
            mrs = [x["rating"] for x in mrows if x["rating"]]
            trend.append({"ym": ym, "n": len(mrows), "avg_rating": round(sum(mrs)/len(mrs), 2) if mrs else None})
        # Sum-up word picks (sentiment proxy)
        sent_words = Counter()
        for x in rows:
            for w in x["sumup"]: sent_words[w] += 1
        trainer_summaries.append({
            "trainer": trainer,
            "slug": slugify(trainer),
            "n_total": len(rows),
            "avg_rating_lifetime": round(sum(rs)/len(rs), 2) if rs else None,
            "avg_dim_score_lifetime": round(sum(ds)/len(ds), 2) if ds else None,
            "flagged_lifetime": sum(1 for x in rows if x["flags"]),
            "windows": windows,
            "first_seen": min((x["date_uk"] for x in rows), default=None),
            "last_seen": max((x["date_uk"] for x in rows), default=None),
            "trend_monthly": trend,
            "top_sumup_words": sent_words.most_common(10),
        })

        # Per-trainer detail file
        # Recent comments (last 90d, non-empty)
        recent_cutoff = today - timedelta(days=90)
        comments = []
        for x in rows:
            d = datetime.fromisoformat(x["date_uk"]).date()
            if d < recent_cutoff: continue
            if x["learning"] or x["would_change"] or x["additional"]:
                comments.append({
                    "date": x["date_uk"],
                    "delegate": x["delegate"],
                    "company": x["company"],
                    "course": x["course"] or x["course_raw"],
                    "rating": x["rating"],
                    "learning": x["learning"],
                    "would_change": x["would_change"],
                    "additional": x["additional"],
                    "sumup": x["sumup"],
                    "flags": x["flags"],
                })
        # Concerns for this trainer (any flagged)
        concerns = [
            {
                "id": x["id"], "date": x["date_uk"], "delegate": x["delegate"], "company": x["company"],
                "course": x["course"] or x["course_raw"], "rating": x["rating"],
                "flags": x["flags"], "would_change": x["would_change"], "additional": x["additional"],
            }
            for x in rows if x["flags"]
        ]
        # Top quotes (positive, recent)
        pos_quotes = []
        for x in rows[-200:]:  # last 200 only for performance
            if x["rating"] == 5 and x["additional"] and len(x["additional"]) > 25:
                pos_quotes.append({
                    "date": x["date_uk"], "delegate": x["delegate"], "company": x["company"],
                    "course": x["course"] or x["course_raw"], "text": x["additional"],
                })
        pos_quotes = pos_quotes[-20:]  # most recent 20

        per_t = {
            "trainer": trainer,
            "slug": slugify(trainer),
            "summary": trainer_summaries[-1],
            "comments_last_90d": comments,
            "concerns": concerns,
            "praise_quotes": pos_quotes,
        }
        (DATA_OUT / "trainer" / f"{slugify(trainer)}.json").write_text(json.dumps(per_t, indent=1))

    (DATA_OUT / "trainers.json").write_text(json.dumps({"trainers": trainer_summaries}, indent=1))

    # ---------- Courses ----------
    by_course = defaultdict(list)
    for r in records:
        if r["course"]:
            by_course[r["course"]].append(r)

    course_summaries = []
    for course, rows in sorted(by_course.items(), key=lambda kv: -len(kv[1])):
        rs = [x["rating"] for x in rows if x["rating"]]
        ds = [x["likert_avg"] for x in rows if x["likert_avg"]]
        # Finish-time spread (UK local hour)
        hours = [hour_dec(x) for x in rows]
        # By trainer
        by_t = Counter()
        for x in rows:
            if x["trainer"]: by_t[x["trainer"]] += 1
        # Trend
        by_m = defaultdict(list)
        for x in rows: by_m[x["ym"]].append(x)
        trend = []
        for ym in sorted(by_m):
            mrows = by_m[ym]
            mrs = [x["rating"] for x in mrows if x["rating"]]
            trend.append({"ym": ym, "n": len(mrows), "avg_rating": round(sum(mrs)/len(mrs), 2) if mrs else None})
        course_summaries.append({
            "course": course,
            "slug": slugify(course),
            "n": len(rows),
            "avg_rating": round(sum(rs)/len(rs), 2) if rs else None,
            "avg_dim": round(sum(ds)/len(ds), 2) if ds else None,
            "flagged": sum(1 for x in rows if x["flags"]),
            "finish_time_median_hour": percentile(hours, 0.5),
            "finish_time_p25_hour": percentile(hours, 0.25),
            "finish_time_p75_hour": percentile(hours, 0.75),
            "top_trainers": by_t.most_common(5),
            "trend_monthly": trend,
        })
        # Per-course detail file
        recent_comments = [
            {"date": x["date_uk"], "delegate": x["delegate"], "company": x["company"],
             "trainer": x["trainer"] or x["trainer_raw"], "rating": x["rating"],
             "learning": x["learning"], "would_change": x["would_change"], "additional": x["additional"]}
            for x in rows[-100:]
            if x["learning"] or x["would_change"] or x["additional"]
        ]
        per_c = {
            "course": course,
            "slug": slugify(course),
            "summary": course_summaries[-1],
            "comments_recent": recent_comments[-50:],
        }
        (DATA_OUT / "course" / f"{slugify(course)}.json").write_text(json.dumps(per_c, indent=1))

    (DATA_OUT / "courses.json").write_text(json.dumps({"courses": course_summaries}, indent=1))

    # ---------- Concerns / flagged-for-investigation queue ----------
    flagged = [
        {
            "ref": short_ref(r["id"]),
            "id": r["id"],
            "date": r["date_uk"],
            "delegate": r["delegate"], "company": r["company"],
            "trainer": r["trainer"] or r["trainer_raw"],
            "course": r["course"] or r["course_raw"],
            "rating": r["rating"],
            "flags": r["flags"],
            "flag_labels": humanise_flags(r["flags"]),
            "would_change": r["would_change"],
            "additional": r["additional"],
            "learning": r["learning"],
            "scale_flip": "SCALE_FLIP" in r["flags"],
        }
        for r in records if r["flags"]
    ]
    flagged.sort(key=lambda x: x["date"], reverse=True)
    (DATA_OUT / "concerns.json").write_text(json.dumps({"concerns": flagged}, indent=1))

    # ---------- Finish times — dedicated analysis (course-level + trainer×course matrix) ----------
    finish_times = []
    for course, rows in sorted(by_course.items(), key=lambda kv: -len(kv[1])):
        hours = [hour_dec(x) for x in rows]
        if not hours: continue
        # Per-trainer breakdown WITHIN this course
        by_trainer_in_course = defaultdict(list)
        for x in rows:
            if x["trainer"]:
                by_trainer_in_course[x["trainer"]].append(hour_dec(x))
        trainer_breakdown = []
        for t, ths in sorted(by_trainer_in_course.items(), key=lambda kv: -len(kv[1])):
            if len(ths) < 3: continue   # need at least 3 deliveries to be meaningful
            trainer_breakdown.append({
                "trainer": t,
                "n": len(ths),
                "median_hour": percentile(ths, 0.5),
                "p25_hour": percentile(ths, 0.25),
                "p75_hour": percentile(ths, 0.75),
            })
        finish_times.append({
            "course": course,
            "n": len(rows),
            "median_hour": percentile(hours, 0.5),
            "p25_hour": percentile(hours, 0.25),
            "p75_hour": percentile(hours, 0.75),
            "earliest_hour": min(hours),
            "latest_hour": max(hours),
            "pct_finishing_early": round(sum(1 for h in hours if h < 15) / len(hours) * 100, 1),
            "by_trainer": trainer_breakdown,
        })

    # Trainer×course finish-time matrix — for the dedicated finish-times page
    matrix = []   # one row per trainer, columns = course buckets
    courses_in_matrix = [f["course"] for f in finish_times if f["n"] >= 20]   # only courses with enough volume
    for trainer in sorted({r["trainer"] for r in records if r["trainer"]}):
        row = {"trainer": trainer, "courses": {}}
        any_data = False
        for cb in courses_in_matrix:
            relevant = [r for r in records if r["trainer"] == trainer and r["course"] == cb]
            if len(relevant) >= 3:
                hours = [hour_dec(r) for r in relevant]
                row["courses"][cb] = {
                    "n": len(relevant),
                    "median_hour": percentile(hours, 0.5),
                }
                any_data = True
        if any_data:
            matrix.append(row)
    # Compute the early-finish signal: which courses' median is materially earlier than 1-day reference
    one_day_courses = [f for f in finish_times if f["n"] >= 10 and "2d" not in f["course"]]
    if one_day_courses:
        oneday_median = sum(f["median_hour"] for f in one_day_courses) / len(one_day_courses)
    else:
        oneday_median = None
    for f in finish_times:
        if oneday_median is not None:
            f["minutes_vs_1day_median"] = round((f["median_hour"] - oneday_median) * 60)
    (DATA_OUT / "finish-times.json").write_text(json.dumps({
        "courses": finish_times,
        "oneday_reference_median_hour": oneday_median,
        "matrix": {
            "trainers": matrix,
            "courses": courses_in_matrix,
        },
    }, indent=1))

    # Per-trainer × course finish times — WINDOWED (30d / 90d / 365d / lifetime)
    trainer_course_finish = {}
    for trainer in {r["trainer"] for r in records if r["trainer"]}:
        rows_t = [r for r in records if r["trainer"] == trainer]
        # Build per-course breakdown for each window
        result_by_course = {}   # keyed on course → dict with window stats
        all_courses = sorted({r["course"] for r in rows_t if r["course"]})
        for c in all_courses:
            entry = {"course": c}
            for label, days in [("30d", 30), ("90d", 90), ("365d", 365), ("lifetime", None)]:
                if days is None:
                    xs = [r for r in rows_t if r["course"] == c]
                else:
                    cutoff = today - timedelta(days=days)
                    xs = [r for r in rows_t if r["course"] == c and datetime.fromisoformat(r["date_uk"]).date() >= cutoff]
                if len(xs) < 2:
                    entry[label] = None
                    continue
                hours = [hour_dec(x) for x in xs]
                ratings = [x["rating"] for x in xs if x["rating"]]
                entry[label] = {
                    "n": len(xs),
                    "median_hour": percentile(hours, 0.5),
                    "p25_hour": percentile(hours, 0.25),
                    "p75_hour": percentile(hours, 0.75),
                    "avg_rating": round(sum(ratings)/len(ratings), 2) if ratings else None,
                }
            # Keep only courses with at least lifetime data
            if entry.get("lifetime"):
                result_by_course[c] = entry
        # Sort by lifetime n descending
        sorted_courses = sorted(result_by_course.values(), key=lambda e: -e["lifetime"]["n"])
        trainer_course_finish[slugify(trainer)] = sorted_courses

    # Same windowed structure for per-course × trainer finish times
    course_trainer_finish = {}
    for course in {r["course"] for r in records if r["course"]}:
        rows_c = [r for r in records if r["course"] == course]
        result_by_trainer = {}
        all_trainers = sorted({r["trainer"] for r in rows_c if r["trainer"]})
        for t in all_trainers:
            entry = {"trainer": t}
            for label, days in [("30d", 30), ("90d", 90), ("365d", 365), ("lifetime", None)]:
                if days is None:
                    xs = [r for r in rows_c if r["trainer"] == t]
                else:
                    cutoff = today - timedelta(days=days)
                    xs = [r for r in rows_c if r["trainer"] == t and datetime.fromisoformat(r["date_uk"]).date() >= cutoff]
                if len(xs) < 2:
                    entry[label] = None
                    continue
                hours = [hour_dec(x) for x in xs]
                ratings = [x["rating"] for x in xs if x["rating"]]
                entry[label] = {
                    "n": len(xs),
                    "median_hour": percentile(hours, 0.5),
                    "p25_hour": percentile(hours, 0.25),
                    "p75_hour": percentile(hours, 0.75),
                    "avg_rating": round(sum(ratings)/len(ratings), 2) if ratings else None,
                }
            if entry.get("lifetime"):
                result_by_trainer[t] = entry
        sorted_trainers = sorted(result_by_trainer.values(), key=lambda e: -e["lifetime"]["n"])
        course_trainer_finish[slugify(course)] = sorted_trainers

    # ---------- Weekly view — last 12 weeks ----------
    by_week = defaultdict(list)
    for r in records:
        try: by_week[iso_week(r["date_uk"])].append(r)
        except: pass
    weeks_summary = []
    sorted_weeks = sorted(by_week.keys())[-26:]  # last 26 weeks (6 months)
    for w in sorted_weeks:
        rows = by_week[w]
        rs = [x["rating"] for x in rows if x["rating"]]
        first = min(x["date_uk"] for x in rows)
        last  = max(x["date_uk"] for x in rows)
        weeks_summary.append({
            "week": w,
            "first_date": first, "last_date": last,
            "n": len(rows),
            "avg_rating": round(sum(rs)/len(rs), 2) if rs else None,
            "pct_5_star": round(sum(1 for r in rs if r == 5)/len(rs)*100, 1) if rs else None,
            "concerns": sum(1 for r in rows if r["flags"] and "SCALE_FLIP" not in r["flags"]),
            "trainers_count": len({r["trainer"] for r in rows if r["trainer"]}),
            "courses_count": len({r["course"] for r in rows if r["course"]}),
        })
    (DATA_OUT / "weekly.json").write_text(json.dumps({"weeks": weeks_summary}, indent=1))

    # Per-week detail files (last 12 weeks only)
    for w in sorted_weeks[-12:]:
        rows = by_week[w]
        rs = [x["rating"] for x in rows if x["rating"]]
        # By trainer
        tr_breakdown = defaultdict(list)
        for x in rows:
            if x["trainer"]: tr_breakdown[x["trainer"]].append(x)
        tr_rows = []
        for t, xs in sorted(tr_breakdown.items(), key=lambda kv: -len(kv[1])):
            xs_r = [x["rating"] for x in xs if x["rating"]]
            tr_rows.append({"trainer": t, "n": len(xs), "avg_rating": round(sum(xs_r)/len(xs_r), 2) if xs_r else None,
                            "concerns": sum(1 for x in xs if x["flags"] and "SCALE_FLIP" not in x["flags"])})
        # By course
        co_breakdown = defaultdict(list)
        for x in rows:
            if x["course"]: co_breakdown[x["course"]].append(x)
        co_rows = []
        for c, xs in sorted(co_breakdown.items(), key=lambda kv: -len(kv[1])):
            xs_r = [x["rating"] for x in xs if x["rating"]]
            co_rows.append({"course": c, "n": len(xs), "avg_rating": round(sum(xs_r)/len(xs_r), 2) if xs_r else None})
        # Praise quotes (5★ + substantive comment)
        praise = []
        for x in rows:
            if x["rating"] == 5 and not x["flags"] and x["additional"] and len(x["additional"]) > 20:
                praise.append({"ref": short_ref(x["id"]), "date": x["date_uk"], "delegate": x["delegate"], "company": x["company"], "course": x["course"] or x["course_raw"], "trainer": x["trainer"] or x["trainer_raw"], "text": x["additional"]})
        # Concerns
        concerns = []
        for x in rows:
            if x["flags"]:
                concerns.append({"ref": short_ref(x["id"]), "date": x["date_uk"], "delegate": x["delegate"], "company": x["company"], "course": x["course"] or x["course_raw"], "trainer": x["trainer"] or x["trainer_raw"], "rating": x["rating"], "flag_labels": humanise_flags(x["flags"]), "would_change": x["would_change"], "additional": x["additional"], "scale_flip": "SCALE_FLIP" in x["flags"]})
        # What delegates would change (substantive)
        changes = []
        for x in rows:
            w_c = (x["would_change"] or "").strip()
            if w_c and len(w_c) > 5 and w_c.lower() not in ("nothing","none","n/a","no","nil"):
                changes.append({"ref": short_ref(x["id"]), "date": x["date_uk"], "delegate": x["delegate"], "course": x["course"] or x["course_raw"], "trainer": x["trainer"] or x["trainer_raw"], "text": w_c})

        # Per-trainer per-DELIVERY view (each trainer × course × date = one delivery)
        def _delivery_geo(rows):
            """Return median lat/lon for delegates who granted GPS in this delivery (else None)."""
            lats = [r.get("geo_lat") for r in rows if r.get("geo_lat") is not None]
            lons = [r.get("geo_lon") for r in rows if r.get("geo_lon") is not None]
            if not lats or not lons: return None, None, 0
            return round(percentile(sorted(lats), 0.5), 5), round(percentile(sorted(lons), 0.5), 5), len(lats)
        trainer_deliveries = []
        for trainer in sorted({r["trainer"] for r in rows if r["trainer"]}):
            t_rows = [r for r in rows if r["trainer"] == trainer]
            t_rs = [r["rating"] for r in t_rows if r["rating"]]
            # Group by (course, date) → one row per delivery
            by_delivery = defaultdict(list)
            for r in t_rows:
                key = (r["course"] or r["course_raw"] or "(unknown course)", r["date_uk"])
                by_delivery[key].append(r)
            delivery_rows = []
            for (course, dt), xs in sorted(by_delivery.items(), key=lambda kv: (kv[0][1], kv[0][0])):
                hours = sorted([hour_dec(x) for x in xs])
                xs_r = [x["rating"] for x in xs if x["rating"]]
                spread_min = (max(hours) - min(hours)) * 60 if hours else 0
                geo_lat, geo_lon, geo_n = _delivery_geo(xs)
                delivery_rows.append({
                    "date": dt,
                    "course": course,
                    "n_delegates": len(xs),
                    "earliest_submit_hour": min(hours),
                    "latest_submit_hour": max(hours),
                    "median_submit_hour": percentile(hours, 0.5),
                    "spread_minutes": round(spread_min),
                    "wide_spread": spread_min > 60,
                    "avg_rating": round(sum(xs_r)/len(xs_r), 2) if xs_r else None,
                    "concerns": sum(1 for x in xs if x["flags"] and "SCALE_FLIP" not in x["flags"]),
                    "geo_lat": geo_lat, "geo_lon": geo_lon, "geo_n": geo_n,
                })
            trainer_deliveries.append({
                "trainer": trainer,
                "total_delegates": len(t_rows),
                "n_deliveries": len(delivery_rows),
                "avg_rating": round(sum(t_rs)/len(t_rs), 2) if t_rs else None,
                "deliveries": delivery_rows,
            })

        (DATA_OUT / "weekly" / f"{w}.json").write_text(json.dumps({
            "week": w,
            "first_date": min(x["date_uk"] for x in rows),
            "last_date": max(x["date_uk"] for x in rows),
            "n": len(rows),
            "avg_rating": round(sum(rs)/len(rs), 2) if rs else None,
            "pct_5_star": round(sum(1 for r in rs if r == 5)/len(rs)*100, 1) if rs else None,
            "by_trainer": tr_rows,
            "by_course": co_rows,
            "trainer_deliveries": trainer_deliveries,
            "praise": praise,
            "concerns": concerns,
            "would_change": changes,
        }, indent=1))

    # ---------- Monthly view (internal, NOT the share/marketing version) ----------
    months_summary = []
    sorted_months = sorted(by_month.keys())[-24:]
    for ym in sorted_months:
        rows = by_month[ym]
        rs = [x["rating"] for x in rows if x["rating"]]
        months_summary.append({
            "ym": ym,
            "n": len(rows),
            "avg_rating": round(sum(rs)/len(rs), 2) if rs else None,
            "pct_5_star": round(sum(1 for r in rs if r == 5)/len(rs)*100, 1) if rs else None,
            "concerns": sum(1 for r in rows if r["flags"] and "SCALE_FLIP" not in r["flags"]),
        })
    (DATA_OUT / "monthly.json").write_text(json.dumps({"months": months_summary}, indent=1))

    # Per-month detail files (last 24 months)
    for ym in sorted_months:
        rows = by_month[ym]
        rs = [x["rating"] for x in rows if x["rating"]]
        tr_breakdown = defaultdict(list)
        for x in rows:
            if x["trainer"]: tr_breakdown[x["trainer"]].append(x)
        tr_rows = []
        for t, xs in sorted(tr_breakdown.items(), key=lambda kv: -len(kv[1])):
            xs_r = [x["rating"] for x in xs if x["rating"]]
            tr_rows.append({"trainer": t, "n": len(xs), "avg_rating": round(sum(xs_r)/len(xs_r), 2) if xs_r else None,
                            "concerns": sum(1 for x in xs if x["flags"] and "SCALE_FLIP" not in x["flags"])})
        co_breakdown = defaultdict(list)
        for x in rows:
            if x["course"]: co_breakdown[x["course"]].append(x)
        co_rows = []
        for c, xs in sorted(co_breakdown.items(), key=lambda kv: -len(kv[1])):
            xs_r = [x["rating"] for x in xs if x["rating"]]
            co_rows.append({"course": c, "n": len(xs), "avg_rating": round(sum(xs_r)/len(xs_r), 2) if xs_r else None})
        praise = []
        for x in rows:
            if x["rating"] == 5 and not x["flags"] and x["additional"] and len(x["additional"]) > 20:
                praise.append({"ref": short_ref(x["id"]), "date": x["date_uk"], "delegate": x["delegate"], "company": x["company"], "course": x["course"] or x["course_raw"], "trainer": x["trainer"] or x["trainer_raw"], "text": x["additional"]})
        concerns = []
        for x in rows:
            if x["flags"]:
                concerns.append({"ref": short_ref(x["id"]), "date": x["date_uk"], "delegate": x["delegate"], "company": x["company"], "course": x["course"] or x["course_raw"], "trainer": x["trainer"] or x["trainer_raw"], "rating": x["rating"], "flag_labels": humanise_flags(x["flags"]), "would_change": x["would_change"], "additional": x["additional"], "scale_flip": "SCALE_FLIP" in x["flags"]})
        changes = []
        for x in rows:
            w_c = (x["would_change"] or "").strip()
            if w_c and len(w_c) > 5 and w_c.lower() not in ("nothing","none","n/a","no","nil"):
                changes.append({"ref": short_ref(x["id"]), "date": x["date_uk"], "delegate": x["delegate"], "course": x["course"] or x["course_raw"], "trainer": x["trainer"] or x["trainer_raw"], "text": w_c})

        # Monthly finish time averages — by trainer, by course, by trainer×course
        trainer_finish_month = []
        for trainer in sorted({r["trainer"] for r in rows if r["trainer"]}):
            t_rows = [r for r in rows if r["trainer"] == trainer]
            hours = [hour_dec(r) for r in t_rows]
            t_rs = [r["rating"] for r in t_rows if r["rating"]]
            # Per-course finish times for THIS trainer this month
            courses_taught = defaultdict(list)
            for r in t_rows:
                if r["course"]: courses_taught[r["course"]].append(r)
            per_course = []
            for c, xs in sorted(courses_taught.items(), key=lambda kv: -len(kv[1])):
                ch = [hour_dec(x) for x in xs]
                cr = [x["rating"] for x in xs if x["rating"]]
                per_course.append({
                    "course": c,
                    "n": len(xs),
                    "median_finish_hour": percentile(ch, 0.5),
                    "avg_rating": round(sum(cr)/len(cr), 2) if cr else None,
                })
            trainer_finish_month.append({
                "trainer": trainer,
                "n": len(t_rows),
                "median_finish_hour": percentile(hours, 0.5) if hours else None,
                "avg_rating": round(sum(t_rs)/len(t_rs), 2) if t_rs else None,
                "courses": per_course,
            })

        course_finish_month = []
        for course in sorted({r["course"] for r in rows if r["course"]}):
            c_rows = [r for r in rows if r["course"] == course]
            hours = [hour_dec(r) for r in c_rows]
            c_rs = [r["rating"] for r in c_rows if r["rating"]]
            course_finish_month.append({
                "course": course,
                "n": len(c_rows),
                "median_finish_hour": percentile(hours, 0.5) if hours else None,
                "earliest_hour": min(hours) if hours else None,
                "latest_hour": max(hours) if hours else None,
                "avg_rating": round(sum(c_rs)/len(c_rs), 2) if c_rs else None,
            })
        course_finish_month.sort(key=lambda x: -x["n"])

        # Trainer × course matrix FOR THIS MONTH (same shape as /finish-times/ matrix but month-scoped)
        month_trainers = sorted({r["trainer"] for r in rows if r["trainer"]})
        # Use courses that had at least 3 deliveries this month
        course_counts_m = Counter(r["course"] for r in rows if r["course"])
        month_matrix_courses = [c for c, cnt in course_counts_m.most_common() if cnt >= 3]
        # Column medians for this month
        month_col_median = {}
        for c in month_matrix_courses:
            hs = [hour_dec(r) for r in rows if r["course"] == c]
            if hs: month_col_median[c] = percentile(hs, 0.5)
        # Build the rows
        month_matrix_rows = []
        for tname in month_trainers:
            row_data = {"trainer": tname, "courses": {}}
            any_data = False
            for c in month_matrix_courses:
                relevant = [r for r in rows if r["trainer"] == tname and r["course"] == c]
                if len(relevant) >= 2:   # lower threshold for monthly (vs lifetime needs 3)
                    hs = [hour_dec(r) for r in relevant]
                    row_data["courses"][c] = {
                        "n": len(relevant),
                        "median_hour": percentile(hs, 0.5),
                    }
                    any_data = True
            if any_data: month_matrix_rows.append(row_data)

        (DATA_OUT / "monthly" / f"{ym}.json").write_text(json.dumps({
            "ym": ym,
            "n": len(rows),
            "avg_rating": round(sum(rs)/len(rs), 2) if rs else None,
            "pct_5_star": round(sum(1 for r in rs if r == 5)/len(rs)*100, 1) if rs else None,
            "by_trainer": tr_rows,
            "by_course": co_rows,
            "trainer_finish_times": trainer_finish_month,
            "course_finish_times": course_finish_month,
            "matrix": {
                "trainers": month_matrix_rows,
                "courses": month_matrix_courses,
                "col_median": month_col_median,
            },
            "praise": praise[:40],
            "concerns": concerns,
            "would_change": changes,
        }, indent=1))

    # ---------- Per-month client-share pages ----------
    # Only for months with enough data (n>=10) and the last 24 months
    months_to_make = [m["ym"] for m in monthly if m["n"] >= 10][-24:]
    for ym in months_to_make:
        rows = by_month[ym]
        rs = [x["rating"] for x in rows if x["rating"]]
        # Curated positive quotes — 5★, additional comments >25 chars, no flags
        quotes = []
        for x in rows:
            if x["rating"] == 5 and not x["flags"] and x["additional"] and len(x["additional"]) > 25:
                quotes.append({
                    "delegate": x["delegate"], "company": x["company"],
                    "course": x["course"] or x["course_raw"],
                    "text": x["additional"], "rating": x["rating"],
                })
        # Course mix
        cmix = Counter(r["course"] or "(uncategorised)" for r in rows)
        # Top 5 companies (clients trained)
        clients = Counter(r["company"] for r in rows if r["company"])
        share = {
            "ym": ym,
            "n": len(rows),
            "avg_rating": round(sum(rs)/len(rs), 2) if rs else None,
            "pct_5_star": round(sum(1 for x in rs if x == 5)/len(rs)*100, 1) if rs else None,
            "nps": round((sum(1 for x in rs if x == 5) - sum(1 for x in rs if x and x <= 3))/len(rs)*100, 1) if rs else None,
            "curated_quotes": quotes[:8],
            "course_mix": cmix.most_common(10),
            "top_clients": clients.most_common(15),
        }
        (DATA_OUT / "share" / f"{ym}.json").write_text(json.dumps(share, indent=1))

    # Augment per-trainer + per-course concerns with refs + labels + finish-times-per-course
    for slug in [t["slug"] for t in trainer_summaries]:
        p = DATA_OUT / "trainer" / f"{slug}.json"
        d = json.loads(p.read_text())
        for c in d.get("concerns", []):
            c["ref"] = short_ref(c["id"])
            c["flag_labels"] = humanise_flags(c.get("flags", []))
        for c in d.get("praise_quotes", []):
            c["ref"] = short_ref(c.get("id", "")) if c.get("id") else ""
        d["finish_times_by_course"] = trainer_course_finish.get(slug, [])
        p.write_text(json.dumps(d, indent=1))

    # Per-course detail: add windowed trainer-breakdown
    finish_by_course = {f["course"]: f for f in finish_times}
    for slug in [c["slug"] for c in course_summaries]:
        p = DATA_OUT / "course" / f"{slug}.json"
        d = json.loads(p.read_text())
        course_name = d["course"]
        f_data = finish_by_course.get(course_name)
        if f_data:
            d["finish_times_by_trainer"] = f_data["by_trainer"]   # legacy lifetime-only
            d["finish_times_overall"] = {
                "median_hour": f_data["median_hour"],
                "p25_hour": f_data["p25_hour"],
                "p75_hour": f_data["p75_hour"],
            }
        # New: windowed trainer × this course
        d["finish_times_by_trainer_windowed"] = course_trainer_finish.get(slug, [])
        p.write_text(json.dumps(d, indent=1))

    # ---------- Missing-feedback detector ----------
    # For every (trainer, date) on/after cutover in the calendar-truth cache:
    #   if the trainer had at least one training event that day BUT no submissions came in,
    #   flag it as "missing feedback" for the dashboard.
    missing_feedback = []
    try:
        ct_cache_path = VAULT_DATA / "calendar-truth-cache.json"
        if ct_cache_path.exists():
            ct = json.loads(ct_cache_path.read_text()).get("by_trainer_date", {})
            from datetime import date as _date
            from collections import defaultdict as _defaultdict
            cutoff_d = _date(2026, 4, 1)  # missing-feedback covers April + onwards
            today_d = today
            received = {(r.get("trainer"), r.get("date_uk")) for r in records
                        if r.get("trainer") and r.get("date_uk")
                        and _date.fromisoformat(r["date_uk"]) >= cutoff_d}
            NON_DELIVERY_KEYWORDS = [
                "van tyres", "kwickfit", "onboarding", "demo tbc",
                "gnss", "testing", "collect all stuff",
                "tbc", "demo ", "fixposition",
            ]
            # Group cache entries by calendar_event_id (so multi-day events are ONE thing).
            # An event is "covered" if ANY of its (trainer, date) keys has a submission.
            # For 2-day courses, feedback lands on Day 2 - so Day 1 alone having no
            # submissions doesn't mean missing-feedback; need to check the whole event.
            event_groups = _defaultdict(list)
            for key, events in ct.items():
                if not events: continue
                trainer, d_str = key.split("|", 1)
                d = _date.fromisoformat(d_str)
                ev = events[0]
                ev_id = ev.get("calendar_event_id") or f"{trainer}|{d_str}"
                event_groups[ev_id].append({
                    "trainer": trainer, "date_uk": d_str, "date": d, "event": ev
                })
            for ev_id, days in event_groups.items():
                # All days in this event group share the same trainer + event details
                trainer = days[0]["trainer"]
                ev = days[0]["event"]
                # Skip if every day is in the future (event hasn't happened)
                if all(day["date"] > today_d for day in days): continue
                # Skip if any (trainer, date) for this event has a submission — covered
                if any((trainer, day["date_uk"]) in received for day in days): continue
                # Skip pre-cutover
                if all(day["date"] < cutoff_d for day in days): continue
                # Apply non-delivery filters
                title = (ev.get("event_summary") or "").lower()
                if any(kw in title for kw in NON_DELIVERY_KEYWORDS): continue
                if not ev.get("code") and not ev.get("customer"): continue
                # Flag as missing — use Day 1 (earliest) for display
                first_day = min(days, key=lambda x: x["date"])
                missing_feedback.append({
                    "trainer": trainer,
                    "date": first_day["date_uk"],
                    "course_code": ev.get("code"),
                    "event_title": ev.get("event_summary", "")[:120],
                    "customer": ev.get("customer"),
                    "is_multi_day": ev.get("is_multi_day", False),
                    "day_total": ev.get("day_total", 1),
                })
            missing_feedback.sort(key=lambda x: x["date"], reverse=True)
    except Exception as e:
        print(f"  WARN missing-feedback detector: {e}")
    # Enrich missing-feedback with derived per-trainer / per-week / per-month / per-day breakdowns
    from datetime import datetime as _dt
    by_trainer_mf = defaultdict(list)
    by_week_mf = defaultdict(list)
    by_month_mf = defaultdict(list)
    by_day_mf = defaultdict(list)
    for m in missing_feedback:
        by_trainer_mf[m["trainer"]].append(m)
        try:
            d = _dt.fromisoformat(m["date"]).date()
        except Exception:
            continue
        iy, iw, _ = d.isocalendar()
        wkey = f"{iy}-W{iw:02d}"
        mkey = d.strftime("%Y-%m")
        by_week_mf[wkey].append(m)
        by_month_mf[mkey].append(m)
        by_day_mf[m["date"]].append(m)
    (DATA_OUT / "missing-feedback.json").write_text(json.dumps({
        "missing": missing_feedback,
        "total": len(missing_feedback),
        "by_trainer": {k: v for k, v in by_trainer_mf.items()},
        "by_week":    {k: v for k, v in by_week_mf.items()},
        "by_month":   {k: v for k, v in by_month_mf.items()},
        "by_day":     {k: v for k, v in by_day_mf.items()},
    }, indent=1))

    # Inject n_missing_feedback into trainers index + per-trainer JSON + weekly + monthly + overview
    # Per-trainer
    for trainer_entry in trainer_summaries:
        trainer_entry["n_missing_feedback"] = len(by_trainer_mf.get(trainer_entry["trainer"], []))
    (DATA_OUT / "trainers.json").write_text(json.dumps({"trainers": trainer_summaries}, indent=1))
    # Per-trainer detail
    for trainer in by_trainer_mf:
        slug_file = DATA_OUT / "trainer" / f"{slugify(trainer)}.json"
        if slug_file.exists():
            d = json.loads(slug_file.read_text())
            d["missing_feedback_list"] = sorted(by_trainer_mf[trainer], key=lambda x: x["date"], reverse=True)
            d["n_missing_feedback"] = len(by_trainer_mf[trainer])
            slug_file.write_text(json.dumps(d, indent=1))
    # Trainers without any missing-feedback still get the field for consistency
    for trainer_entry in trainer_summaries:
        slug_file = DATA_OUT / "trainer" / f"{slugify(trainer_entry['trainer'])}.json"
        if slug_file.exists():
            d = json.loads(slug_file.read_text())
            if "missing_feedback_list" not in d:
                d["missing_feedback_list"] = []
                d["n_missing_feedback"] = 0
                slug_file.write_text(json.dumps(d, indent=1))
    # Weekly
    weekly_dir = DATA_OUT / "weekly"
    for wf in weekly_dir.glob("*.json"):
        w = json.loads(wf.read_text())
        wk = w.get("week")
        w["missing_feedback_list"] = sorted(by_week_mf.get(wk, []), key=lambda x: x["date"])
        w["n_missing_feedback"] = len(w["missing_feedback_list"])
        wf.write_text(json.dumps(w, indent=1))
    # Monthly
    monthly_dir = DATA_OUT / "monthly"
    for mf2 in monthly_dir.glob("*.json"):
        m_data = json.loads(mf2.read_text())
        ym = m_data.get("ym")
        m_data["missing_feedback_list"] = sorted(by_month_mf.get(ym, []), key=lambda x: x["date"])
        m_data["n_missing_feedback"] = len(m_data["missing_feedback_list"])
        mf2.write_text(json.dumps(m_data, indent=1))
    # Overview gets lifetime total
    overview_path = DATA_OUT / "overview.json"
    if overview_path.exists():
        ov = json.loads(overview_path.read_text())
        ov["kpis"]["missing_feedback_lifetime"] = len(missing_feedback)
        overview_path.write_text(json.dumps(ov, indent=1))

    # ---------- Duration computation (per-delivery actual duration) ----------
    # Built 2026-05-31 evening per Pete's spec:
    #   - Submissions-first iteration (efficient: only look up calendar where deliveries exist)
    #   - For each (trainer, date_uk, course_code) delivery: find matching calendar event
    #     in the truth cache, use its start_iso as start, latest submission ts_uk as end
    #   - duration_min = (last_submission_utc - calendar_start_utc).total_seconds() // 60
    #   - Skip all-day events (no start time → undefined start)
    #   - Skip pre-cutover (two-era rule: calendar-truth only reliable post 2026-05-01)
    #   - Sanity gates: drop negative durations + drop > 12h (flag as anomaly)
    # Timezone discipline: ALL datetimes tz-aware. JotForm ts_uk parsed as Europe/London.
    # Calendar start_iso parsed from RFC3339 offset. Both compared in UTC.
    from zoneinfo import ZoneInfo
    UK_TZ = ZoneInfo("Europe/London")
    UTC_TZ = timezone.utc
    CUTOVER = datetime(2026, 5, 1).date()

    # Reload cache fresh (we need start_iso + all_day fields, beyond the missing-feedback usage)
    cache_entries_by_key: dict[tuple[str, str], list[dict]] = {}
    try:
        if ct_cache_path.exists():
            ct_full = json.loads(ct_cache_path.read_text()).get("by_trainer_date", {})
            for key, events in ct_full.items():
                trainer, d_str = key.split("|", 1)
                cache_entries_by_key[(trainer, d_str)] = events
    except Exception as e:
        print(f"  WARN duration: cache load failed: {e}")
        cache_entries_by_key = {}

    # Group submissions into deliveries
    deliveries_records: dict[tuple, list] = defaultdict(list)
    for r in records:
        trainer = r.get("trainer")
        date_uk = r.get("date_uk")
        course = r.get("course")
        ts = r.get("ts_uk")
        if not (trainer and date_uk and ts):
            continue
        try:
            d = datetime.fromisoformat(date_uk).date()
        except ValueError:
            continue
        if d < CUTOVER:
            continue  # two-era rule
        deliveries_records[(trainer, date_uk, course or "")].append(r)

    per_delivery_durations = []
    durations_by_course = defaultdict(list)
    durations_by_trainer = defaultdict(list)
    skipped_all_day = 0
    no_calendar_match = 0
    anomaly_negative = 0
    anomaly_too_long = 0

    for (trainer, date_uk, _course), subs in deliveries_records.items():
        # Find matching calendar entry
        cal_events = cache_entries_by_key.get((trainer, date_uk), [])
        if not cal_events:
            no_calendar_match += 1
            continue
        # If multiple events on the day, pick the one whose code matches the submission's course_code
        # (records carry r["course"] as the canonical course name; we'd need to map to C-code).
        # Pragmatic: pick the first event that has a code AND is timed. If none, fall back to first.
        candidate = None
        for ev in cal_events:
            if ev.get("code") and not ev.get("all_day"):
                candidate = ev
                break
        if not candidate:
            # All-day event(s) only → skip duration
            if any(ev.get("all_day") for ev in cal_events):
                skipped_all_day += 1
            else:
                no_calendar_match += 1
            continue

        start_iso = candidate.get("start_iso")
        if not start_iso:
            skipped_all_day += 1
            continue

        # Parse start (tz-aware from RFC3339 offset)
        try:
            start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        except ValueError:
            no_calendar_match += 1
            continue
        start_utc = start_dt.astimezone(UTC_TZ)

        # Find latest submission (ts_uk is "YYYY-MM-DD HH:MM:SS" naive UK local string)
        latest_ts_str = max(r["ts_uk"] for r in subs)
        try:
            latest_dt_naive = datetime.fromisoformat(latest_ts_str)
        except ValueError:
            continue
        latest_dt_uk = latest_dt_naive.replace(tzinfo=UK_TZ)
        latest_utc = latest_dt_uk.astimezone(UTC_TZ)

        delta_min = int((latest_utc - start_utc).total_seconds() // 60)

        # Sanity gates
        if delta_min < 0:
            anomaly_negative += 1
            continue
        if delta_min > 12 * 60:
            anomaly_too_long += 1
            continue

        delivery_record = {
            "trainer": trainer,
            "date_uk": date_uk,
            "course_code": candidate["code"],
            "course": _course,
            "customer": candidate.get("customer"),
            "start_iso": start_iso,
            "last_submission_iso": latest_dt_uk.isoformat(),
            "duration_min": delta_min,
            "n_submissions": len(subs),
            "all_day": False,
        }
        per_delivery_durations.append(delivery_record)
        durations_by_course[candidate["code"]].append(delta_min)
        durations_by_trainer[trainer].append(delta_min)

    # Per-course averages
    def _stats(arr):
        if not arr:
            return None
        s = sorted(arr)
        n = len(s)
        median = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2
        return {
            "n": n,
            "avg_min": round(sum(s) / n, 1),
            "median_min": round(median, 1),
            "min_min": s[0],
            "max_min": s[-1],
        }

    duration_per_course = [
        {"course_code": code, **_stats(durs)}
        for code, durs in sorted(durations_by_course.items())
    ]
    duration_per_trainer = [
        {"trainer": trainer, **_stats(durs)}
        for trainer, durs in sorted(durations_by_trainer.items(), key=lambda kv: -len(kv[1]))
    ]

    durations_output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": {
            "from_date": CUTOVER.isoformat(),
            "rule": "post-cutover only (two-era rule, calendar-truth era)",
        },
        "summary": {
            "deliveries_with_duration": len(per_delivery_durations),
            "deliveries_skipped_all_day": skipped_all_day,
            "deliveries_no_calendar_match": no_calendar_match,
            "anomaly_negative_duration": anomaly_negative,
            "anomaly_duration_over_12h": anomaly_too_long,
        },
        "per_delivery": sorted(per_delivery_durations, key=lambda d: d["date_uk"], reverse=True),
        "per_course": duration_per_course,
        "per_trainer": duration_per_trainer,
    }
    (DATA_OUT / "durations.json").write_text(json.dumps(durations_output, indent=1))

    # Inject avg_duration_min into per-trainer + per-course detail JSONs (additive only)
    duration_by_trainer_slug = {slugify(t["trainer"]): t for t in duration_per_trainer}
    for trainer_entry in duration_per_trainer:
        slug_file = DATA_OUT / "trainer" / f"{slugify(trainer_entry['trainer'])}.json"
        if slug_file.exists():
            d = json.loads(slug_file.read_text())
            d["actual_duration"] = {
                "n_deliveries": trainer_entry["n"],
                "avg_min": trainer_entry["avg_min"],
                "median_min": trainer_entry["median_min"],
            }
            slug_file.write_text(json.dumps(d, indent=1))
    # Inject avg_duration_min into trainers.json leaderboard rows
    trainers_idx_path = DATA_OUT / "trainers.json"
    if trainers_idx_path.exists():
        t_idx = json.loads(trainers_idx_path.read_text())
        for tr in t_idx.get("trainers", []):
            d_entry = duration_by_trainer_slug.get(tr.get("slug"))
            if d_entry:
                tr["actual_duration_median_min"] = d_entry["median_min"]
                tr["actual_duration_n"] = d_entry["n"]
        trainers_idx_path.write_text(json.dumps(t_idx, indent=1))
    # Per-course: courses on the dashboard use course-name slugs not C-codes,
    # so map code → name via a reverse lookup against records.
    code_to_course_name = {}
    for r in records:
        if r.get("course_code") and r.get("course"):
            code_to_course_name.setdefault(r["course_code"], r["course"])
    # === UNIVERSAL DURATION INJECTION ===
    # Walk every per-trainer + per-course breakdown structure across all JSONs and
    # inject avg_duration_min / n_deliveries_with_duration where available.
    # Lookup: (trainer, course_name) -> list of duration_min (all post-cutover)
    tc_durs_all = defaultdict(list)
    for d_rec in per_delivery_durations:
        cn = code_to_course_name.get(d_rec["course_code"])
        if cn:
            tc_durs_all[(d_rec["trainer"], cn)].append(d_rec["duration_min"])
    def _avg_dur(arr):
        if not arr: return None, 0
        return round(sum(arr) / len(arr), 1), len(arr)

    # 1. Monthly: trainer_finish_times[].courses[] (the "Each trainer's courses this month" section)
    monthly_dir2 = DATA_OUT / "monthly"
    if monthly_dir2.exists():
        for mf in monthly_dir2.glob("*.json"):
            m = json.loads(mf.read_text())
            ym = m.get("ym")
            if not ym: continue
            for tr_entry in m.get("trainer_finish_times", []):
                trainer_name = tr_entry.get("trainer")
                for c_entry in tr_entry.get("courses", []):
                    course_name = c_entry.get("course")
                    arr = [d["duration_min"] for d in per_delivery_durations
                           if d["trainer"] == trainer_name
                           and code_to_course_name.get(d["course_code"]) == course_name
                           and d["date_uk"].startswith(ym)]
                    avg, n = _avg_dur(arr)
                    if avg is not None:
                        c_entry["avg_duration_min"] = avg
                        c_entry["n_deliveries_with_duration"] = n
            mf.write_text(json.dumps(m, indent=1))

    # 2. Per-trainer detail: finish_times_by_course[] (WindowedFinishTable)
    trainer_dir = DATA_OUT / "trainer"
    if trainer_dir.exists():
        for tf in trainer_dir.glob("*.json"):
            t = json.loads(tf.read_text())
            trainer_name = t.get("trainer")
            for c_entry in t.get("finish_times_by_course", []):
                course_name = c_entry.get("course")
                arr = tc_durs_all.get((trainer_name, course_name), [])
                avg, n = _avg_dur(arr)
                if avg is not None:
                    c_entry["avg_duration_min"] = avg
                    c_entry["n_deliveries_with_duration"] = n
            tf.write_text(json.dumps(t, indent=1))

    # 3. Per-course detail: finish_times_by_trainer_windowed[] (WindowedTrainerTable)
    course_dir = DATA_OUT / "course"
    if course_dir.exists():
        for cf in course_dir.glob("*.json"):
            c = json.loads(cf.read_text())
            course_name = c.get("course")
            for t_entry in c.get("finish_times_by_trainer_windowed", []):
                trainer_name = t_entry.get("trainer")
                arr = tc_durs_all.get((trainer_name, course_name), [])
                avg, n = _avg_dur(arr)
                if avg is not None:
                    t_entry["avg_duration_min"] = avg
                    t_entry["n_deliveries_with_duration"] = n
            cf.write_text(json.dumps(c, indent=1))

    # Inject per (trainer, course) duration into the lifetime finish-times matrix.
    # Two-era caveat: duration only computable from post-cutover deliveries; cell N (delegates)
    # is all-time. Display will read both fields separately.
    ft_path = DATA_OUT / "finish-times.json"
    if ft_path.exists():
        ft_data = json.loads(ft_path.read_text())
        ft_matrix = ft_data.get("matrix", {})
        for trainer_row in ft_matrix.get("trainers", []):
            trainer_name = trainer_row.get("trainer")
            for course_name, cell in (trainer_row.get("courses") or {}).items():
                code = code_to_course_name and {v: k for k, v in code_to_course_name.items()}.get(course_name)
                if not code: continue
                arr = [
                    d["duration_min"] for d in per_delivery_durations
                    if d["trainer"] == trainer_name and d["course_code"] == code
                ]
                if arr:
                    s = sorted(arr)
                    cell["avg_duration_min"] = round(sum(s) / len(s), 1)
                    cell["n_deliveries_with_duration"] = len(s)
        ft_path.write_text(json.dumps(ft_data, indent=1))

    duration_by_course_slug = {}
    for course_entry in duration_per_course:
        course_name = code_to_course_name.get(course_entry["course_code"])
        if not course_name:
            continue
        slug = slugify(course_name)
        duration_by_course_slug[slug] = course_entry
        slug_file = DATA_OUT / "course" / f"{slug}.json"
        if slug_file.exists():
            d = json.loads(slug_file.read_text())
            d["actual_duration"] = {
                "course_code": course_entry["course_code"],
                "n_deliveries": course_entry["n"],
                "avg_min": course_entry["avg_min"],
                "median_min": course_entry["median_min"],
                "min_min": course_entry["min_min"],
                "max_min": course_entry["max_min"],
            }
            slug_file.write_text(json.dumps(d, indent=1))
    # Inject avg_duration_min into courses.json leaderboard rows
    courses_idx_path = DATA_OUT / "courses.json"
    if courses_idx_path.exists():
        c_idx = json.loads(courses_idx_path.read_text())
        for cs in c_idx.get("courses", []):
            d_entry = duration_by_course_slug.get(cs.get("slug"))
            if d_entry:
                cs["actual_duration_median_min"] = d_entry["median_min"]
                cs["actual_duration_n"] = d_entry["n"]
        courses_idx_path.write_text(json.dumps(c_idx, indent=1))

    # Inject duration into weekly + monthly JSONs (per-trainer + per-course rows)
    # Build (trainer, date) -> duration_min lookup for per-delivery injection
    delivery_duration = {}
    for d in per_delivery_durations:
        delivery_duration[(d["trainer"], d["date_uk"])] = d["duration_min"]
    # Weekly files
    weekly_dir = DATA_OUT / "weekly"
    if weekly_dir.exists():
        for wf in weekly_dir.glob("*.json"):
            w = json.loads(wf.read_text())
            # Add avg_duration_min to by_trainer rows where we have data
            # (week-scoped: average of deliveries by this trainer in this week)
            wk_first = w.get("first_date")
            wk_last  = w.get("last_date")
            if not (wk_first and wk_last):
                continue
            wk_first_d = datetime.fromisoformat(wk_first).date()
            wk_last_d  = datetime.fromisoformat(wk_last).date()
            # Group deliveries-in-window by trainer
            tr_durs_wk = defaultdict(list)
            cr_durs_wk = defaultdict(list)
            for d_rec in per_delivery_durations:
                dd = datetime.fromisoformat(d_rec["date_uk"]).date()
                if wk_first_d <= dd <= wk_last_d:
                    tr_durs_wk[d_rec["trainer"]].append(d_rec["duration_min"])
                    cr_durs_wk[d_rec["course_code"]].append(d_rec["duration_min"])
            for bt in w.get("by_trainer", []):
                arr = tr_durs_wk.get(bt.get("trainer"), [])
                if arr:
                    s = sorted(arr)
                    bt["avg_duration_min"] = round(sum(s) / len(s), 1)
                    bt["n_deliveries_with_duration"] = len(s)
            # Per-delivery row injection (trainer_deliveries carries per-day entries)
            for td in w.get("trainer_deliveries", []):
                key = (td.get("trainer"), td.get("date"))
                if key in delivery_duration:
                    td["duration_min"] = delivery_duration[key]
            wf.write_text(json.dumps(w, indent=1))
    # Monthly files - inject into by_trainer + by_course AND trainer_finish_times + course_finish_times
    # (latter two are what the monthly React page actually renders)
    monthly_dir = DATA_OUT / "monthly"
    name_to_code = {v: k for k, v in code_to_course_name.items()}
    if monthly_dir.exists():
        for mf in monthly_dir.glob("*.json"):
            m = json.loads(mf.read_text())
            ym = m.get("ym")
            if not ym:
                continue
            tr_durs_m = defaultdict(list)
            cr_durs_m = defaultdict(list)
            for d_rec in per_delivery_durations:
                if d_rec["date_uk"].startswith(ym):
                    tr_durs_m[d_rec["trainer"]].append(d_rec["duration_min"])
                    cr_durs_m[d_rec["course_code"]].append(d_rec["duration_min"])
            for collection_key, key_field, lookup in [
                ("by_trainer", "trainer", lambda x: tr_durs_m.get(x.get("trainer"), [])),
                ("trainer_finish_times", "trainer", lambda x: tr_durs_m.get(x.get("trainer"), [])),
                ("by_course", "course", lambda x: cr_durs_m.get(name_to_code.get(x.get("course")), [])),
                ("course_finish_times", "course", lambda x: cr_durs_m.get(name_to_code.get(x.get("course")), [])),
            ]:
                for entry in m.get(collection_key, []):
                    arr = lookup(entry)
                    if arr:
                        s = sorted(arr)
                        entry["avg_duration_min"] = round(sum(s) / len(s), 1)
                        entry["n_deliveries_with_duration"] = len(s)
            # Inject per (trainer, course) duration into the matrix — fair comparison view
            # (avoids the unfair averaging where 2-day Day-2-earlier finishes drag down a
            # trainer's overall finish time)
            for trainer_row in (m.get("matrix") or {}).get("trainers", []):
                trainer_name = trainer_row.get("trainer")
                for course_name, cell in (trainer_row.get("courses") or {}).items():
                    code = name_to_code.get(course_name)
                    if not code: continue
                    # Filter per-delivery durations: this trainer, this course, this month
                    arr = [
                        d["duration_min"] for d in per_delivery_durations
                        if d["trainer"] == trainer_name
                        and d["course_code"] == code
                        and d["date_uk"].startswith(ym)
                    ]
                    if arr:
                        s = sorted(arr)
                        cell["avg_duration_min"] = round(sum(s) / len(s), 1)
                        cell["n_deliveries_with_duration"] = len(s)
            mf.write_text(json.dumps(m, indent=1))

    (DATA_OUT / "metadata.json").write_text(json.dumps(metadata, indent=1))

    # Repoint (2026-06-07): also feed the Sygma Internal Hub, which renders the eval at
    # sygmaportal.com/hub/training-evaluation from hub.training_evaluations. Guarded so a
    # Hub-write hiccup never breaks the standalone dashboard aggregation/deploy.
    try:
        import subprocess
        subprocess.run([sys.executable, str(Path(__file__).with_name("eval-hub-load.py"))], check=True)
        print("Hub fed: hub.training_evaluations updated.")
    except Exception as _hub_err:
        print(f"[warn] Hub feed (eval-hub-load.py) failed: {_hub_err}")

    print(f"Aggregation complete. {len(records)} records → {len(trainer_summaries)} trainers, {len(course_summaries)} courses, {len(flagged)} flagged, {len(sorted_weeks)} weekly summaries, {len(sorted_months)} monthly summaries, {len(months_to_make)} share pages, {len(missing_feedback)} missing-feedback flags.")
    print(f"Duration: {len(per_delivery_durations)} deliveries computed | {skipped_all_day} all-day skipped | {no_calendar_match} no calendar match | {anomaly_negative} negative + {anomaly_too_long} >12h anomalies dropped.")


if __name__ == "__main__":
    main()
