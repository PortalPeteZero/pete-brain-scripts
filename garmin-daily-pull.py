#!/usr/bin/env python3
"""Garmin daily pull — write one md file per day under Personal/health/garmin/.

Pulls everything useful from Garmin Connect for a given date and writes a
structured md file with rich frontmatter (queryable by Dataview) plus a human
narrative body. Built 2026-05-24 per Pete's request: "do a daily cron at 8am
to pull this info into the vault, it should also pull any other relevant
garmin info, build up a picture of what i am doing and good vault context."

Cron: 07:00 Atlantic/Canary, pulls yesterday + today's data.

Usage:
  python3 garmin-daily-pull.py                       # pull yesterday, write file
  python3 garmin-daily-pull.py 2026-05-22            # pull a specific date
  python3 garmin-daily-pull.py --backfill 90         # backfill last 90 days
  python3 garmin-daily-pull.py --backfill 30 --dry   # dry-run for backfill
  python3 garmin-daily-pull.py --range 2026-05-01 2026-05-23  # date range

Output:
  /tmp/pbs/Personal/health/garmin/YYYY-MM-DD.md

Idempotency: re-running for an existing date overwrites the file. Safe.

Failure modes:
  * Garmin token expired -> raise, log to daily note, do NOT retry. Pete
    re-bootstraps via the bootstrap script.
  * Per-endpoint exception -> log, write what we have, skip the section.
  * No data for a date (e.g. watch wasn't worn) -> still writes the file but
    every field is null. Surfaces as "no data" rather than missing file.
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

VAULT = Path(os.environ.get("VAULT", "/tmp/pbs"))
# Business OS (H1, 2026-06-22): data homes moved to Google Drive (synced mount). VAULT kept only for the helper-script path below.
DRIVE = Path("/Users/peterashcroft/Library/CloudStorage/GoogleDrive-pete.ashcroft@sygma-solutions.com")
OUT_DIR = DRIVE / "My Drive/Health/garmin"             # md narrative, per-day
JSON_DIR = DRIVE / "My Drive/Health/garmin/data"        # JSON snapshots for dashboard
WEEKLY_JSON_DIR = DRIVE / "My Drive/Health/garmin/data/weekly"  # weekly snapshots
JOURNAL_DIR = DRIVE / "My Drive/Passion Fit/journal"    # source of journal md
WEEKLY_DIR = DRIVE / "My Drive/Passion Fit/weekly"      # source of weekly md
DAILY = VAULT / "Daily"
TZ = ZoneInfo("Atlantic/Canary")

# Optional second JSON destination — a local clone of the dashboard repo.
# Cron extends to git-commit + push from there. Disabled until repo + clone exist.
DASHBOARD_REPO_ROOT = Path.home() / "code/command-centre"  # repointed 2026-06-11: health dashboard lives in the Command Centre (was code/pete-health-dashboard)
DASHBOARD_REPO_DATA = DASHBOARD_REPO_ROOT / "data/garmin"
DASHBOARD_REPO_WEEKLY = DASHBOARD_REPO_ROOT / "data/weekly"

# Load the GarminAPI helper from the canonical location.
spec = importlib.util.spec_from_file_location(
    "garmin_api", str(Path(__file__).resolve().parent / "garmin-api.py")  # flat-repo sibling (Railway = /app); resolves to Library/processes/scripts locally
)
garmin_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(garmin_mod)


def _safe(fn, *args, **kwargs):
    """Call fn(*args), swallow exceptions, return (value, error_str_or_None)."""
    try:
        return fn(*args, **kwargs), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _format_min_as_hm(total_min: int) -> str:
    """450 -> '7h 30m'."""
    if total_min is None:
        return "n/a"
    h, m = divmod(int(total_min), 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def pull_day(g: "garmin_mod.GarminAPI", d: str) -> dict:
    """Pull every interesting endpoint for a single CALENDAR DAY. Returns a
    dict with parsed fields + any per-endpoint errors. Never raises.

    Date semantics (Garmin-native — matches Garmin Connect exactly):
      * Everything for date D is queried at D. Garmin files a night's sleep
        (and the HRV / body battery / training readiness derived from it) under
        the date you WOKE. So file D = the sleep that ended on D's morning +
        D's morning HRV / readiness + D's daytime activity / steps / stress.

    Example: file 2026-05-24.md (Sunday) holds the sleep Pete woke from on
    Sunday morning, Sunday's training readiness (a forward-looking "ready to
    train today" metric), and Sunday's daytime activity — identical to what
    Garmin shows for Sunday.

    NOTE: do NOT reintroduce a +1 recovery shift. A previous version did that
    to co-locate a day's training load with the following night's sleep; it
    mis-dated readiness onto the prior day and contradicted Garmin. Workload→
    sleep causality is handled at analysis time (pair day D's activity with day
    D+1's sleep), never by shifting the display dates.
    """
    out: dict = {"date": d, "errors": {}}

    # Recovery (sleep / HRV / body battery / training readiness) is filed by
    # Garmin under the date you WOKE — the SAME calendar date as this file. No
    # shift: file D = the sleep that ended on D's morning + D's morning HRV /
    # readiness + D's daytime activity. Matches Garmin Connect exactly.
    # (A previous version shifted +1 to co-locate a day's load with the
    # FOLLOWING night's sleep; that mis-dated training readiness onto the prior
    # day and contradicted what Garmin shows — reverted 2026-05-24. Workload→
    # sleep causality is a query-time join, NOT a display shift: when analysing,
    # pair day D's activity with day D+1's sleep.)
    recovery_date = d
    out["recovery_query_date"] = recovery_date

    recovery = g.recovery(recovery_date)
    out["sleep"] = recovery.get("sleep") or {}
    out["hrv"] = recovery.get("hrv") or {}
    out["body_battery"] = recovery.get("body_battery") or {}
    out["training_readiness"] = recovery.get("training_readiness") or {}
    for k in ("sleep_error", "hrv_error", "body_battery_error", "training_readiness_error"):
        if k in recovery:
            out["errors"][k] = recovery[k]
    # Overwrite the "date" field inside sleep/hrv/etc with the CALENDAR day
    # (so downstream consumers see file-d, not the recovery-query date).
    for key in ("sleep", "hrv", "body_battery", "training_readiness"):
        if isinstance(out.get(key), dict):
            out[key]["calendar_date"] = d
            out[key]["recovery_query_date"] = recovery_date

    # Daytime data — pulled for day d itself.
    stats, err = _safe(g.stats, d)
    if err:
        out["errors"]["stats"] = err
    out["stats"] = stats or {}

    stress, err = _safe(g.stress, d)
    if err:
        out["errors"]["stress"] = err
    out["stress"] = stress or {}

    hr, err = _safe(g.heart_rate, d)
    if err:
        out["errors"]["heart_rate"] = err
    out["heart_rate"] = hr or {}

    steps, err = _safe(g.steps, d)
    if err:
        out["errors"]["steps"] = err
    out["steps_detail"] = steps or []

    activities, err = _safe(g.activities, d, d)
    if err:
        out["errors"]["activities"] = err
    out["activities"] = activities or []

    # Per-activity weather (added 2026-06-11 for the session-intro template in
    # [[training-feedback-loop]]). Attached to the raw payload as `_weather` so
    # both the md formatter and the rich JSON mapper read it without extra
    # fetches. Decoration, never fatal — None on any failure / indoor session.
    for act in out["activities"]:
        act["_weather"] = _fetch_weather_metric(g, act.get("activityId"))

    # Training context (per-day): status, ACWR, VO2 max, race predictions.
    # All Garmin-native (Garmin's status / ACWR / VO2 are computed daily on the
    # date you query). Same date semantics as everything else — D = D.
    training_status, err = _safe(g.training_status, d)
    if err:
        out["errors"]["training_status"] = err
    out["training_status"] = training_status or {}

    # Race predictions are a point-in-time snapshot, not date-filtered. Garmin
    # returns the LATEST prediction regardless of `d` — fine, we read it as
    # today's belief about fitness. Mostly useful for the run side (5K / 10K
    # / HM / Marathon); swim + bike predictions don't exist here.
    race_predictions, err = _safe(g.race_predictions)
    if err:
        out["errors"]["race_predictions"] = err
    out["race_predictions"] = race_predictions or {}

    return out


def _fetch_weather_metric(g, activity_id):
    """Per-activity weather from Garmin's `/weather` endpoint, converted
    imperial → metric. Lean dict for the dashboard JSON + md conditions line,
    or None when unavailable (indoor sessions, endpoint failure)."""
    if not activity_id:
        return None
    w, _err = _safe(g.activity_weather, activity_id)
    if not w or w.get("temp") is None:
        return None

    def f2c(f):
        return round((f - 32) * 5 / 9) if f is not None else None

    ws = w.get("windSpeed")
    return {
        "temp_c": f2c(w.get("temp")),
        "feels_like_c": f2c(w.get("apparentTemp")),
        "humidity_pct": w.get("relativeHumidity"),
        "wind_kmh": round(ws * 1.609) if ws is not None else None,
        "wind_dir": (w.get("windDirectionCompassPoint") or "").upper() or None,
        "desc": (w.get("weatherTypeDTO") or {}).get("desc"),
        "station": (w.get("weatherStationDTO") or {}).get("name"),
    }


def _frontmatter(day: dict) -> str:
    """Build YAML frontmatter from a day's parsed data."""
    iso = day["date"]
    s = day.get("sleep") or {}
    h = day.get("hrv") or {}
    b = day.get("body_battery") or {}
    tr = day.get("training_readiness") or {}
    stats = day.get("stats") or {}
    activities = day.get("activities") or []

    # Day-of-week
    try:
        dt_obj = datetime.fromisoformat(iso).replace(tzinfo=TZ)
        dow = dt_obj.strftime("%A")
    except Exception:
        dow = ""

    def yamlval(v):
        if v is None:
            return "null"
        if isinstance(v, str):
            return v.replace("\n", " ").replace('"', '\\"')
        return v

    total_activity_min = 0
    for a in activities:
        dur = a.get("duration") or 0  # seconds
        total_activity_min += int(dur) // 60

    fields = [
        ("type", "garmin-day"),
        ("date", iso),
        ("day_of_week", dow),
        # Sleep
        ("sleep_score", s.get("score")),
        ("sleep_qualifier", s.get("qualifier")),
        ("sleep_total_min", s.get("total_minutes")),
        ("sleep_deep_min", s.get("deep_minutes")),
        ("sleep_rem_min", s.get("rem_minutes")),
        ("sleep_light_min", s.get("light_minutes")),
        ("sleep_awake_min", s.get("awake_minutes")),
        # HRV
        ("hrv_last_night", h.get("last_night_avg")),
        ("hrv_weekly_avg", h.get("weekly_avg")),
        ("hrv_status", h.get("status")),
        # Body battery
        ("body_battery_charged", b.get("charged")),
        ("body_battery_drained", b.get("drained")),
        # Training readiness
        ("training_readiness_score", tr.get("score")),
        ("training_readiness_level", tr.get("level")),
        # Daily stats
        ("steps", stats.get("totalSteps")),
        ("step_goal", stats.get("dailyStepGoal")),
        ("resting_hr", stats.get("restingHeartRate")),
        ("max_hr", stats.get("maxHeartRate")),
        ("stress_avg", stats.get("averageStressLevel")),
        ("calories_active", stats.get("activeKilocalories")),
        ("calories_total", stats.get("totalKilocalories")),
        ("floors_climbed", stats.get("floorsAscended")),
        ("distance_metres", stats.get("totalDistanceMeters")),
        ("intensity_min_moderate", stats.get("moderateIntensityMinutes")),
        ("intensity_min_vigorous", stats.get("vigorousIntensityMinutes")),
        ("vo2_max", stats.get("vo2Max")),
        # Activities
        ("activities_count", len(activities)),
        ("activities_total_min", total_activity_min),
    ]

    lines = ["---"]
    for k, v in fields:
        if v is None:
            continue  # skip null fields to keep frontmatter clean
        if isinstance(v, str):
            lines.append(f'{k}: "{yamlval(v)}"')
        else:
            lines.append(f"{k}: {yamlval(v)}")
    lines.append("tags: [garmin, health, daily]")
    lines.append("---")
    return "\n".join(lines)


def _format_activity(a: dict, idx: int) -> str:
    """Format one activity as a markdown subsection."""
    name = a.get("activityName") or a.get("activityType", {}).get("typeKey", "Activity")
    sport = (a.get("activityType") or {}).get("typeKey", "?")
    start_local = a.get("startTimeLocal") or a.get("startTimeGMT", "?")
    duration_sec = a.get("duration") or 0
    duration_min = int(duration_sec) // 60
    distance_m = a.get("distance") or 0
    distance_km = round(distance_m / 1000, 2) if distance_m else 0
    avg_hr = a.get("averageHR")
    max_hr = a.get("maxHR")
    calories = a.get("calories")
    elevation_gain = a.get("elevationGain")
    avg_pace_sec_per_km = None
    if distance_m and duration_sec:
        avg_pace_sec_per_km = duration_sec / (distance_m / 1000)

    lines = [f"### {name} ({sport})"]
    lines.append(f"- Start: {start_local}")
    lines.append(f"- Duration: {duration_min}m  ({_format_min_as_hm(duration_min)})")
    if distance_m:
        lines.append(f"- Distance: {distance_km} km")
    if avg_pace_sec_per_km and distance_m > 0:
        # min:sec per km
        m_per_km = int(avg_pace_sec_per_km // 60)
        s_per_km = int(avg_pace_sec_per_km % 60)
        lines.append(f"- Avg pace: {m_per_km}:{s_per_km:02d} /km")
    if avg_hr:
        lines.append(f"- Avg HR: {avg_hr}{f' (max {max_hr})' if max_hr else ''}")
    if calories:
        lines.append(f"- Calories: {calories}")
    if elevation_gain:
        lines.append(f"- Elevation gain: {round(elevation_gain)}m")
    w = a.get("_weather")
    if w and w.get("temp_c") is not None:
        bits = [f"{w['temp_c']}°C"]
        if w.get("feels_like_c") is not None and w["feels_like_c"] != w["temp_c"]:
            bits[0] += f" (feels {w['feels_like_c']}°C)"
        if w.get("wind_kmh") is not None:
            bits.append(f"wind {w['wind_kmh']} km/h {w.get('wind_dir') or ''}".strip())
        if w.get("humidity_pct") is not None:
            bits.append(f"humidity {w['humidity_pct']}%")
        if w.get("desc"):
            bits.append(w["desc"])
        lines.append(f"- Conditions: {', '.join(bits)}")
    return "\n".join(lines)


def render_md(day: dict) -> str:
    """Render the full md file content for a day."""
    iso = day["date"]
    s = day.get("sleep") or {}
    h = day.get("hrv") or {}
    b = day.get("body_battery") or {}
    tr = day.get("training_readiness") or {}
    stats = day.get("stats") or {}
    activities = day.get("activities") or []
    errors = day.get("errors") or {}

    try:
        dt_obj = datetime.fromisoformat(iso).replace(tzinfo=TZ)
        title_date = dt_obj.strftime("%A %d %B %Y")
    except Exception:
        title_date = iso

    parts = []
    parts.append(_frontmatter(day))
    parts.append("")
    parts.append(f"# Garmin — {title_date}")
    parts.append("")

    # Recovery
    parts.append(f"## Recovery (sleep ending the morning of {iso} + that "
                 f"morning's HRV / body battery / training readiness)")
    parts.append(f"- Sleep score: **{s.get('score','n/a')}** ({s.get('qualifier','?')})")
    parts.append(f"- Total sleep: {_format_min_as_hm(s.get('total_minutes'))} "
                 f"(deep {s.get('deep_minutes','n/a')}m / "
                 f"REM {s.get('rem_minutes','n/a')}m / "
                 f"light {s.get('light_minutes','n/a')}m / "
                 f"awake {s.get('awake_minutes','n/a')}m)")
    parts.append(f"- HRV: last night {h.get('last_night_avg','n/a')}  ·  "
                 f"weekly avg {h.get('weekly_avg','n/a')}  ·  "
                 f"status {h.get('status','?')}")
    if h.get("feedback_phrase"):
        parts.append(f"- HRV feedback: {h.get('feedback_phrase')}")
    parts.append(f"- Body battery: {b.get('charged','n/a')} charged · "
                 f"{b.get('drained','n/a')} drained")
    parts.append(f"- Training readiness: **{tr.get('score','n/a')}** "
                 f"({tr.get('level','?')})")
    if tr.get("feedback_long"):
        parts.append(f"- Readiness feedback: {tr.get('feedback_long')}")
    parts.append("")

    # Daily
    parts.append("## Daily")
    parts.append(f"- Steps: {stats.get('totalSteps','n/a')} / goal "
                 f"{stats.get('dailyStepGoal','n/a')}")
    parts.append(f"- Resting HR: {stats.get('restingHeartRate','n/a')}  ·  "
                 f"max HR: {stats.get('maxHeartRate','n/a')}")
    parts.append(f"- Stress (day avg): {stats.get('averageStressLevel','n/a')}")
    parts.append(f"- Floors climbed: {stats.get('floorsAscended','n/a')}")
    if stats.get("totalDistanceMeters"):
        km = round(stats["totalDistanceMeters"] / 1000, 2)
        parts.append(f"- Total distance (all movement): {km} km")
    parts.append(f"- Intensity minutes (this week-to-date): "
                 f"moderate {stats.get('moderateIntensityMinutes','n/a')}  ·  "
                 f"vigorous {stats.get('vigorousIntensityMinutes','n/a')}")
    parts.append(f"- Active calories: {stats.get('activeKilocalories','n/a')}  ·  "
                 f"total {stats.get('totalKilocalories','n/a')}")
    if stats.get("vo2Max"):
        parts.append(f"- VO2 max: {stats.get('vo2Max')}")
    parts.append("")

    # Activities
    if activities:
        parts.append(f"## Activities ({len(activities)})")
        for i, a in enumerate(activities, 1):
            parts.append("")
            parts.append(_format_activity(a, i))
        parts.append("")
    else:
        parts.append("## Activities")
        parts.append("- None recorded.")
        parts.append("")

    # Errors (if any)
    if errors:
        parts.append("## Pull errors")
        parts.append("(Logged for diagnostics — file still written so downstream consumers see the day.)")
        for k, v in errors.items():
            parts.append(f"- `{k}`: {v}")
        parts.append("")

    return "\n".join(parts)


def append_daily_note_line(line: str):
    """Append a one-line status to today's daily note under a Garmin section."""
    today_iso = datetime.now(TZ).date().isoformat()
    note_path = DAILY / f"{today_iso}.md"
    section = "## Garmin daily pull (Automated)"
    if note_path.exists():
        text = note_path.read_text()
        if section in text:
            text = text.rstrip() + f"\n{line}\n"
        else:
            text = text.rstrip() + f"\n\n{section}\n{line}\n"
        note_path.write_text(text)
    else:
        # Create minimal daily note if missing
        front = (
            f"---\ntype: daily\ndate: {today_iso}\ntags: [daily]\n---\n\n"
            f"# Daily {today_iso}\n\n"
        )
        note_path.write_text(front + f"{section}\n{line}\n")


def _parse_md_frontmatter(text: str) -> tuple:
    """Split a markdown file into (frontmatter_dict, body_str). Frontmatter is
    a YAML block bounded by `---`. Returns (None, full_text) if no frontmatter."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    yaml_block = text[4:end]
    body = text[end + 5:]
    # Minimal YAML parse — handle key: value lines, no nesting needed for our use
    fm = {}
    for line in yaml_block.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        elif v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
        fm[k.strip()] = v
    return fm, body


def load_journal_entry(date_iso: str) -> dict:
    """Return the PF journal entry for `date_iso` as a dict, or None if missing.

    Pete journals in the evening of the day for that day. So the journal for
    2026-05-23 (Saturday) lives at Personal/passion-fit/journal/2026-05-23.md
    and was written Saturday evening. This is the same date key as the daily
    Garmin file, so embedding is straightforward.
    """
    journal_path = JOURNAL_DIR / f"{date_iso}.md"
    if not journal_path.exists():
        return None
    try:
        text = journal_path.read_text()
    except Exception:
        return None
    fm, body = _parse_md_frontmatter(text)
    return {
        "date": date_iso,
        "exists": True,
        "frontmatter": fm or {},
        "body": body.strip(),
    }


def _map_activity_rich(a: dict, g=None) -> dict:
    """Map a raw Garmin activity payload to the rich dashboard shape.

    Adds (vs the old shallow shape): training effect chips, HR zone breakdown,
    sport-specific stats (pace / power / cadence / SWOLF / strokes), splits.

    Old fields (name, sport, duration_min, distance_km, avg_hr, max_hr, calories,
    elevation_gain_m) are preserved — Day-view cards keep rendering unchanged.

    `g` is the GarminAPI client. When provided, splits are fetched per-activity
    via the `/splits` endpoint (real per-lap data: avg HR, pace, distance per
    lap). Without it, splits are empty. Earlier versions read `splitSummaries`
    from the activity payload — those are typed-aggregate rollups (e.g.
    INTERVAL_ACTIVE + RWD_RUN = full activity twice with null HR), not per-lap
    data. See lesson 2026-05-26-garmin-splits-use-lap-endpoint."""
    dur_sec = a.get("duration") or 0
    dist_m = a.get("distance") or 0
    sport = (a.get("activityType") or {}).get("typeKey", "?")

    # HR zones (seconds) — raw Garmin returns hrTimeInZone_1..5. Cardinal data
    # for the Z2-discipline metric; cornerstone of half-Ironman base training.
    hr_zones = {
        f"z{i}": round(a.get(f"hrTimeInZone_{i}") or 0)
        for i in (1, 2, 3, 4, 5)
    }
    hr_zone_total = sum(hr_zones.values())

    # Splits — fetched from Garmin's `/splits` endpoint per-activity. Returns
    # an array of lapDTOs (auto-1km laps for runs, auto-1mile/manual laps for
    # bikes, per-pool-length for swims). Reshaped to the lean shape the
    # dashboard SplitsTable component consumes (split_type / duration_sec /
    # distance_m / avg_hr / max_hr / avg_speed_mps — the table derives min/km
    # pace from avg_speed_mps).
    splits_lean: list[dict] = []
    activity_id = a.get("activityId")
    if g is not None and activity_id:
        splits_raw = g.activity_splits(activity_id) or {}
        laps = splits_raw.get("lapDTOs") or []
        for i, lap in enumerate(laps, start=1):
            if not isinstance(lap, dict):
                continue
            avg_hr = lap.get("averageHR")
            max_hr = lap.get("maxHR")
            avg_pwr = lap.get("averagePower")
            max_pwr = lap.get("maxPower")
            norm_pwr = lap.get("normalizedPower")
            # Cadence — Garmin uses different field names per sport. Pick whichever exists.
            avg_cad = (lap.get("averageBikeCadence")
                       or lap.get("averageRunCadence")
                       or lap.get("averageCadence"))
            max_cad = (lap.get("maxBikeCadence")
                       or lap.get("maxRunCadence")
                       or lap.get("maxCadence"))
            splits_lean.append({
                "split_type": f"lap {i}",
                "duration_sec": lap.get("duration"),
                "distance_m": lap.get("distance"),
                "avg_hr": round(avg_hr) if avg_hr else None,
                "max_hr": round(max_hr) if max_hr else None,
                "avg_speed_mps": lap.get("averageSpeed"),
                "avg_power_w": round(avg_pwr) if avg_pwr else None,
                "max_power_w": round(max_pwr) if max_pwr else None,
                "normalized_power_w": round(norm_pwr) if norm_pwr else None,
                "avg_cadence": round(avg_cad) if avg_cad else None,
                "max_cadence": round(max_cad) if max_cad else None,
                "elevation_gain_m": lap.get("elevationGain"),
                "calories": lap.get("calories"),
            })

    # Speed → human pace. Calculate from average_speed so we have it for every
    # sport regardless of which sport-specific pace field is populated.
    avg_speed = a.get("averageSpeed")  # m/s
    pace_min_per_km = None
    pace_sec_per_100m = None
    if avg_speed and avg_speed > 0:
        pace_min_per_km = round((1000 / avg_speed) / 60, 2)
        pace_sec_per_100m = round(100 / avg_speed, 1)

    return {
        # --- preserved shallow shape (old dashboard cards stay working) ---
        "name": a.get("activityName") or sport,
        "sport": sport,
        "start_local": a.get("startTimeLocal"),
        "duration_min": int(dur_sec) // 60,
        "distance_km": round(dist_m / 1000, 2) if dist_m else None,
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "calories": a.get("calories"),
        "elevation_gain_m": round(a["elevationGain"]) if a.get("elevationGain") else None,
        # --- new rich fields ---
        "activity_id": a.get("activityId"),
        "weather": a.get("_weather"),
        "duration_sec": dur_sec,
        "distance_m": dist_m,
        "moving_duration_sec": a.get("movingDuration"),
        # Training effect (the single most useful per-session number for HIM prep)
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "aerobic_te_msg": a.get("aerobicTrainingEffectMessage"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "anaerobic_te_msg": a.get("anaerobicTrainingEffectMessage"),
        "te_label": a.get("trainingEffectLabel"),
        "training_load": a.get("activityTrainingLoad"),
        # HR zones (cardinal for Z2 discipline)
        "hr_zones": hr_zones,
        "hr_zone_total_sec": hr_zone_total,
        # Pace (derived from avg speed; sport-agnostic)
        "avg_speed_mps": avg_speed,
        "max_speed_mps": a.get("maxSpeed"),
        "pace_min_per_km": pace_min_per_km,
        "pace_sec_per_100m": pace_sec_per_100m,
        # Intensity minutes
        "moderate_intensity_min": a.get("moderateIntensityMinutes"),
        "vigorous_intensity_min": a.get("vigorousIntensityMinutes"),
        # Sport-specific (will be None when not applicable)
        # Running
        "run_cadence_avg_spm": a.get("averageRunningCadenceInStepsPerMinute"),
        "run_cadence_max_spm": a.get("maxRunningCadenceInStepsPerMinute"),
        # Cycling
        "bike_power_avg_w": a.get("averageWatts"),
        "bike_power_max_w": a.get("maxWatts"),
        "bike_power_normalized_w": a.get("normPower"),
        "bike_intensity_factor": a.get("intensityFactor"),
        "bike_tss": a.get("trainingStressScore"),
        # Swimming
        "swim_strokes": a.get("strokes"),
        "swim_avg_strokes_per_length": a.get("avgStrokes"),
        "swim_cadence_avg_spm": a.get("averageSwimCadenceInStrokesPerMinute"),
        "swim_avg_swolf": a.get("avgSwolf"),
        "swim_fastest_100_sec": a.get("fastestSplit_100"),
        "swim_active_lengths": a.get("activeLengths"),
        "swim_pool_length_m": (a.get("poolLength") / 100) if a.get("poolLength") else None,
        "lap_count": a.get("lapCount"),
        # Splits
        "splits": splits_lean,
    }


def _map_training_block(day: dict) -> dict:
    """Build the per-day `training` block from training_status + race_predictions.
    Empty fields stay None so the dashboard can gate on `if training.status_int`."""
    ts = day.get("training_status") or {}
    rp = day.get("race_predictions") or {}

    # status_int → human label (1..7 enum from Garmin)
    status_label = {
        1: "Detraining", 2: "Recovery", 3: "Unproductive", 4: "Maintaining",
        5: "Productive", 6: "Peaking", 7: "Overreaching",
    }.get(ts.get("status_int"))

    return {
        "status_int": ts.get("status_int"),
        "status_label": status_label,
        "status_phrase": ts.get("status_phrase"),
        "fitness_trend": ts.get("fitness_trend"),
        "primary_sport": ts.get("primary_sport"),
        "since_date": ts.get("since_date"),
        "acute_load": ts.get("acute_load"),
        "chronic_load": ts.get("chronic_load"),
        "acwr_ratio": ts.get("acwr_ratio"),
        "acwr_status": ts.get("acwr_status"),
        "acwr_percent": ts.get("acwr_percent"),
        "load_tunnel_min": ts.get("load_tunnel_min"),
        "load_tunnel_max": ts.get("load_tunnel_max"),
        "vo2_max": ts.get("vo2_max"),
        "vo2_max_date": ts.get("vo2_max_date"),
        "fitness_age": ts.get("fitness_age"),
        "race_predictions": {
            "calendar_date": rp.get("calendar_date"),
            "time_5k_sec": rp.get("time_5k_sec"),
            "time_10k_sec": rp.get("time_10k_sec"),
            "time_half_marathon_sec": rp.get("time_half_marathon_sec"),
            "time_marathon_sec": rp.get("time_marathon_sec"),
        },
    }


def build_json_snapshot(day: dict, g=None) -> dict:
    """Flat, dashboard-ready JSON shape. Decoupled from md rendering — what the
    dashboard consumes lives here, in one place, easy to type in TypeScript."""
    s = day.get("sleep") or {}
    h = day.get("hrv") or {}
    b = day.get("body_battery") or {}
    tr = day.get("training_readiness") or {}
    stats = day.get("stats") or {}
    activities_raw = day.get("activities") or []

    # Pass the Garmin client `g` so each activity can fetch its per-lap splits.
    activities = [_map_activity_rich(a, g) for a in activities_raw]

    try:
        dt_obj = datetime.fromisoformat(day["date"]).replace(tzinfo=TZ)
        dow = dt_obj.strftime("%A")
    except Exception:
        dow = ""

    # Embed the PF journal for this calendar day if it exists. Pete journals
    # in the evening for the day just lived, so journal/{date}.md is the right
    # match for Garmin file /{date}.json — same calendar-day mental model.
    journal = load_journal_entry(day["date"])

    return {
        "date": day["date"],
        "day_of_week": dow,
        "recovery_query_date": day.get("recovery_query_date"),
        "journal": journal,
        "signoff": detect_signoff(day["date"]),
        "sleep": {
            "score": s.get("score"),
            "qualifier": s.get("qualifier"),
            "total_min": s.get("total_minutes"),
            "deep_min": s.get("deep_minutes"),
            "rem_min": s.get("rem_minutes"),
            "light_min": s.get("light_minutes"),
            "awake_min": s.get("awake_minutes"),
        },
        "hrv": {
            "last_night": h.get("last_night_avg"),
            "last_night_5min_high": h.get("last_night_5min_high"),
            "weekly_avg": h.get("weekly_avg"),
            "status": h.get("status"),
            "feedback_phrase": h.get("feedback_phrase"),
        },
        "body_battery": {
            "charged": b.get("charged"),
            "drained": b.get("drained"),
            "highest": b.get("highest"),
            "lowest": b.get("lowest"),
        },
        "training_readiness": {
            "score": tr.get("score"),
            "level": tr.get("level"),
            "feedback_short": tr.get("feedback_short"),
            "feedback_long": tr.get("feedback_long"),
        },
        "daily": {
            "steps": stats.get("totalSteps"),
            "step_goal": stats.get("dailyStepGoal"),
            "resting_hr": stats.get("restingHeartRate"),
            "max_hr": stats.get("maxHeartRate"),
            "stress_avg": stats.get("averageStressLevel"),
            "floors": stats.get("floorsAscended"),
            "distance_m": stats.get("totalDistanceMeters"),
            "active_kcal": stats.get("activeKilocalories"),
            "total_kcal": stats.get("totalKilocalories"),
            "intensity_min_moderate": stats.get("moderateIntensityMinutes"),
            "intensity_min_vigorous": stats.get("vigorousIntensityMinutes"),
            "vo2_max": stats.get("vo2Max"),
        },
        "training": _map_training_block(day),
        "activities": activities,
    }


def detect_signoff(d: str) -> dict:
    """Best-effort "when did Pete last work with Claude" for the EVENING BEFORE
    day d — i.e. the latest Claude Code / Cowork session-transcript activity on
    calendar day d-1. Shown on day d's card as "last night you signed off ~HH:MM"
    and surfaced at the morning Resume, where Pete confirms or corrects it (the
    `confirmed` field, set via --set-signoff, wins). It is a PROXY — last tracked
    session activity, not a hard sign-off — which is exactly why the confirm loop
    exists. Day d's sleep (woken d morning) and this sign-off are the SAME night,
    so they pair on one card."""
    out = {"detected": None, "detected_iso": None, "confirmed": None,
           "source": "claude-session-activity"}
    try:
        prev = date.fromisoformat(d) - timedelta(days=1)
    except Exception:
        return out
    start = datetime(prev.year, prev.month, prev.day, tzinfo=TZ)
    start_ts = start.timestamp()
    end_ts = (start + timedelta(days=1)).timestamp()
    roots = [
        Path.home() / ".claude/projects",
        Path.home() / "Library/Application Support/Claude/local-agent-mode-sessions",
    ]
    latest = None
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if start_ts <= m < end_ts and (latest is None or m > latest):
                latest = m
    if latest is not None:
        dt = datetime.fromtimestamp(latest, TZ)
        out["detected"] = dt.strftime("%H:%M")
        out["detected_iso"] = dt.isoformat()
    return out


def _iso_week_dates(iso_week: str) -> list[str]:
    """Return the 7 ISO dates (Mon..Sun) for an ISO week string '2026-W22'."""
    try:
        year, w = iso_week.split("-W")
        # ISO 8601: week 1 contains the first Thursday of the year.
        d = date.fromisocalendar(int(year), int(w), 1)  # Monday
        return [(d + timedelta(days=i)).isoformat() for i in range(7)]
    except Exception:
        return []


def _aggregate_training_week(iso_week: str) -> dict:
    """Read the 7 daily JSONs for an ISO week and compute the training rollup.

    Skips missing days silently — backfill may not yet cover the whole week.
    All metrics are sums or weighted averages over days that DO have data.
    """
    dates = _iso_week_dates(iso_week)
    if not dates:
        return {}

    duration_by_sport: dict = {}
    distance_by_sport: dict = {}
    longest_session_by_sport: dict = {}
    total_training_load = 0.0
    hr_time_by_zone = {f"z{i}": 0 for i in (1, 2, 3, 4, 5)}
    aerobic_buckets: dict = {}  # te_label → count
    intensity_min_moderate = 0
    intensity_min_vigorous = 0
    acwr_values: list = []
    weekly_status_phrases: list = []
    activity_count = 0
    days_with_data = 0

    for d in dates:
        p = JSON_DIR / f"{d}.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        days_with_data += 1
        # Training block (per-day status + ACWR)
        tr = data.get("training") or {}
        if tr.get("acwr_ratio") is not None:
            acwr_values.append(tr["acwr_ratio"])
        if tr.get("status_phrase"):
            weekly_status_phrases.append(tr["status_phrase"])
        # Activities
        for a in data.get("activities") or []:
            activity_count += 1
            sport = a.get("sport") or "?"
            dur_min = a.get("duration_min") or 0
            dist_km = a.get("distance_km") or 0
            duration_by_sport[sport] = duration_by_sport.get(sport, 0) + dur_min
            distance_by_sport[sport] = round(distance_by_sport.get(sport, 0) + dist_km, 2)
            if dur_min > longest_session_by_sport.get(sport, 0):
                longest_session_by_sport[sport] = dur_min
            total_training_load += a.get("training_load") or 0
            for z in ("z1", "z2", "z3", "z4", "z5"):
                hr_time_by_zone[z] += (a.get("hr_zones") or {}).get(z, 0)
            label = a.get("te_label")
            if label:
                aerobic_buckets[label] = aerobic_buckets.get(label, 0) + 1
            intensity_min_moderate += a.get("moderate_intensity_min") or 0
            intensity_min_vigorous += a.get("vigorous_intensity_min") or 0

    hr_total = sum(hr_time_by_zone.values())
    z2_percent = round(100 * hr_time_by_zone["z2"] / hr_total, 1) if hr_total else None
    avg_acwr = round(sum(acwr_values) / len(acwr_values), 2) if acwr_values else None

    return {
        "iso_week": iso_week,
        "days_with_data": days_with_data,
        "activity_count": activity_count,
        "duration_min_by_sport": duration_by_sport,
        "distance_km_by_sport": distance_by_sport,
        "longest_session_min_by_sport": longest_session_by_sport,
        "total_training_load": round(total_training_load, 1),
        "hr_time_by_zone": hr_time_by_zone,
        "z2_percent": z2_percent,
        "aerobic_effect_distribution": aerobic_buckets,
        "intensity_min_moderate": intensity_min_moderate,
        "intensity_min_vigorous": intensity_min_vigorous,
        "acwr_avg": avg_acwr,
        "acwr_count": len(acwr_values),
        "status_phrases": weekly_status_phrases,
    }


def build_weekly_snapshot(weekly_md_path: Path) -> dict:
    """Build a JSON snapshot for a PF weekly entry. The file's ISO-week stem
    (e.g. 2026-W22) is the key; body + frontmatter pass through, training
    rollup is computed live from the 7 daily JSONs.

    The dashboard's Week view renders this alongside the 7 daily Garmin cards.
    """
    text = weekly_md_path.read_text()
    fm, body = _parse_md_frontmatter(text)
    iso_week = weekly_md_path.stem  # "2026-W22"
    return {
        "iso_week": iso_week,
        "exists": True,
        "frontmatter": fm or {},
        "body": body.strip(),
        "training_rollup": _aggregate_training_week(iso_week),
    }


def write_all_weekly_snapshots(dry: bool = False) -> int:
    """Walk Personal/passion-fit/weekly/ and write a JSON file for each .md
    entry. Idempotent — overwrites on each run so late edits propagate.
    Cheap (small number of files, no API calls).

    Returns the count of files written.
    """
    if not WEEKLY_DIR.exists():
        return 0
    written = 0
    WEEKLY_JSON_DIR.mkdir(parents=True, exist_ok=True)
    for md in sorted(WEEKLY_DIR.glob("*.md")):
        # Filename must look like an ISO week (e.g. 2026-W22.md) — skip READMEs etc.
        if not re.match(r"^\d{4}-W\d{2}$", md.stem):
            continue
        snapshot = build_weekly_snapshot(md)
        out_path = WEEKLY_JSON_DIR / f"{md.stem}.json"
        if dry:
            written += 1
            continue
        out_path.write_text(json.dumps(snapshot, indent=2, default=str))

        # Mirror to dashboard repo clone if it exists
        if DASHBOARD_REPO_ROOT.exists():
            try:
                DASHBOARD_REPO_WEEKLY.mkdir(parents=True, exist_ok=True)
                (DASHBOARD_REPO_WEEKLY / f"{md.stem}.json").write_text(
                    json.dumps(snapshot, indent=2, default=str)
                )
            except Exception as e:
                print(f"  Warning: failed to mirror weekly JSON to dashboard repo: {e}", file=sys.stderr)
        written += 1
    return written


def write_all_lessons(dry: bool = False) -> int:
    """Build data/lessons.json from the PF journals — one framework lesson per
    day. The journal md is the source of truth: extract the `## One lesson for
    tomorrow` line + the `lesson_tags` frontmatter; skip journals with no lesson
    heading (a skip day). Newest-first, idempotent. Writes the vault copy +
    mirrors to the dashboard repo clone (data/lessons.json) so the Daily Lessons
    page renders it. Cheap — no API calls. Mirrors write_all_weekly_snapshots."""
    if not JOURNAL_DIR.exists():
        return 0
    H = "## One lesson for tomorrow"
    rows = []
    for md in sorted(JOURNAL_DIR.glob("*.md")):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", md.stem):
            continue
        fm, body = _parse_md_frontmatter(md.read_text())
        if H not in body:
            continue
        after = body.split(H, 1)[1]
        nxt = re.search(r"\n##\s", after)
        seg = after[:nxt.start()] if nxt else after
        lesson = re.sub(r"\s+", " ", seg.strip().strip("-")).strip()
        if not lesson:
            continue
        fmd = fm if isinstance(fm, dict) else {}
        rt = fmd.get("lesson_tags")
        if isinstance(rt, list):
            tags = [str(t).strip() for t in rt if str(t).strip()]
        elif rt:
            tags = [t.strip() for t in str(rt).split(",") if t.strip()]
        else:
            tags = []
        dow = str(fmd.get("day_of_week") or "").strip()
        if not dow:
            try:
                dow = datetime.fromisoformat(md.stem).strftime("%A")
            except Exception:
                dow = ""
        rows.append({"date": md.stem, "day": dow, "lesson": lesson, "tags": tags})
    rows.sort(key=lambda r: r["date"], reverse=True)
    if dry:
        return len(rows)
    payload = json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    vault_out = WEEKLY_JSON_DIR.parent / "lessons.json"
    vault_out.parent.mkdir(parents=True, exist_ok=True)
    vault_out.write_text(payload)
    if DASHBOARD_REPO_ROOT.exists():
        try:
            DASHBOARD_REPO_DATA.parent.mkdir(parents=True, exist_ok=True)
            (DASHBOARD_REPO_DATA.parent / "lessons.json").write_text(payload)
        except Exception as e:
            print(f"  Warning: failed to mirror lessons JSON to dashboard repo: {e}", file=sys.stderr)
    return len(rows)


def _upsert_garmin_daily(day: dict, snapshot: dict | None = None):
    """Write the day's headline metrics to the CC `garmin_daily` table (queryable; feeds the morning
    briefing's recovery section). The full processed `snapshot` (same dict written to the data/garmin
    JSON) is stored in the `snapshot` column — that is the LIVE feed the CC Health dashboard reads, so
    the dashboard reflects each pull without a code deploy (no longer depends on the git-mirror).
    Non-fatal — never breaks the md/json write. Business-OS re-target: structured Garmin data → a CC table."""
    try:
        import urllib.request
        url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
        if not (url and key):
            kp = (Path(os.environ["VAULT"]) if os.environ.get("VAULT") else VAULT) / "Library/processes/secrets/command-centre-supabase-keys.json"
            kd = json.loads(kp.read_text()); url, key = kd["url"], kd["service_role_key"]
        s = day.get("sleep") or {}; h = day.get("hrv") or {}; b = day.get("body_battery") or {}
        tr = day.get("training_readiness") or {}; stats = day.get("stats") or {}
        tot_min = s.get("total_minutes")
        row = {
            "date": day["date"],
            "readiness": tr.get("score"), "readiness_label": tr.get("level"),
            "steps": stats.get("totalSteps"), "resting_hr": stats.get("restingHeartRate"),
            "hrv": h.get("last_night_avg"), "sleep_score": s.get("score"),
            "sleep_hours": round(tot_min / 60, 1) if tot_min else None,
            "stress_avg": stats.get("averageStressLevel"),
            "body_battery_high": b.get("charged"),
            "payload": {k: day.get(k) for k in ("sleep", "hrv", "body_battery", "training_readiness", "stats", "stress", "activities")},
            "snapshot": snapshot,
            "updated_at": datetime.now(TZ).isoformat(),
        }
        req = urllib.request.Request(
            url.rstrip("/") + "/rest/v1/garmin_daily?on_conflict=date",
            data=json.dumps([row], default=str).encode(), method="POST",
            headers={"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"})
        urllib.request.urlopen(req, timeout=30)
        print(f"  CC: garmin_daily upserted ({day['date']})")
    except Exception as e:
        print(f"  Warning: garmin_daily upsert failed: {e}", file=sys.stderr)


def _persist_garmin_token(g):
    """Save the (refreshed/rotated) Garmin token back to the CC `secrets` table after a
    successful auth. Garmin ROTATES the refresh token, so a snapshot that is never
    re-saved eventually gets invalidated -> forced credential re-login (MFA hell, and a
    dead local bootstrap). This closes that loop: every Railway run re-seeds the cloud
    copy, so any session/bootstrap pulls a live, self-refreshing token. Non-fatal."""
    try:
        import urllib.request
        tokens_dir = Path(garmin_mod.TOKENS_DIR)
        # force-write the current in-memory (post-refresh) token to disk, then read it
        try:
            g.client.client.dump(str(tokens_dir))
        except Exception:
            pass  # fall back to whatever is already on disk (the materialised copy)
        tf = tokens_dir / "garmin_tokens.json"
        val = tf.read_text()
        d = json.loads(val)
        if not (isinstance(d, dict) and d.get("di_refresh_token")):
            print("  Warning: garmin token missing refresh — not persisting", file=sys.stderr)
            return
        url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
        if not (url and key):
            kp = (Path(os.environ["VAULT"]) if os.environ.get("VAULT") else VAULT) / "Library/processes/secrets/command-centre-supabase-keys.json"
            kd = json.loads(kp.read_text()); url, key = kd["url"], kd["service_role_key"]
        row = {"name": "garminconnect-tokens/garmin_tokens.json", "value": val, "encoding": "text"}
        req = urllib.request.Request(
            url.rstrip("/") + "/rest/v1/secrets?on_conflict=name",
            data=json.dumps([row]).encode(), method="POST",
            headers={"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"})
        urllib.request.urlopen(req, timeout=30)
        print("  CC: garmin token persisted back to secrets (refresh loop healthy)")
    except Exception as e:
        print(f"  Warning: garmin token persist failed: {e}", file=sys.stderr)


def write_day(g: "garmin_mod.GarminAPI", iso_date: str, dry: bool = False) -> tuple:
    """Pull + render + write one day. Returns (path, status, has_data, day_dict).

    Writes BOTH the md narrative (for vault reading + brain skill + journal use)
    AND the JSON snapshot (for the Vercel dashboard, lives at JSON_DIR + optionally
    DASHBOARD_REPO_DATA if the local clone exists)."""
    day = pull_day(g, iso_date)
    md = render_md(day)
    out_path = OUT_DIR / f"{iso_date}.md"

    has_data = (
        (day.get("sleep") or {}).get("score") is not None
        or (day.get("stats") or {}).get("totalSteps") is not None
        or len(day.get("activities") or []) > 0
    )

    if dry:
        return out_path, "dry-run", has_data, day

    # md (vault)
    out_path.write_text(md)

    # JSON (vault, primary location for dashboard data)
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    json_path = JSON_DIR / f"{iso_date}.json"
    snapshot = build_json_snapshot(day, g)
    # Preserve a Pete-confirmed sign-off across re-runs — the cron must never
    # clobber a correction Pete made at Resume (via --set-signoff).
    if json_path.exists():
        try:
            prev_signoff = json.loads(json_path.read_text()).get("signoff") or {}
            if prev_signoff.get("confirmed") and snapshot.get("signoff"):
                snapshot["signoff"]["confirmed"] = prev_signoff["confirmed"]
        except Exception:
            pass
    json_path.write_text(json.dumps(snapshot, indent=2, default=str))

    # CC garmin_daily table (Business OS — structured, queryable; feeds the morning briefing + the
    # live Health dashboard via the `snapshot` column). Non-fatal.
    _upsert_garmin_daily(day, snapshot)

    # JSON (dashboard repo clone, if it exists). Non-fatal if missing.
    if DASHBOARD_REPO_DATA.parent.exists():
        try:
            DASHBOARD_REPO_DATA.mkdir(parents=True, exist_ok=True)
            (DASHBOARD_REPO_DATA / f"{iso_date}.json").write_text(
                json.dumps(snapshot, indent=2, default=str)
            )
        except Exception as e:
            print(f"  Warning: failed to mirror JSON to dashboard repo: {e}", file=sys.stderr)

    return out_path, "written", has_data, day


def _daily_note_summary_line(day: dict) -> str:
    """Build a one-line headline summary suitable for the daily note. Pulled by
    the brain skill at Resume Step 2."""
    iso = day.get("date", "?")
    s = day.get("sleep") or {}
    h = day.get("hrv") or {}
    tr = day.get("training_readiness") or {}
    stats = day.get("stats") or {}
    activities = day.get("activities") or []

    parts = []
    if s.get("score") is not None:
        total_min = s.get("total_minutes") or 0
        h_, m_ = divmod(int(total_min), 60)
        sleep_str = f"Sleep {s.get('score')} {s.get('qualifier','?')} ({h_}h {m_}m)"
        parts.append(sleep_str)
    if h.get("last_night_avg") is not None:
        parts.append(f"HRV {h.get('last_night_avg')} {h.get('status','?')}")
    if tr.get("score") is not None:
        parts.append(f"readiness {tr.get('score')} {tr.get('level','?')}")
    if activities:
        # Brief: count + first activity name
        first = activities[0]
        name = (first.get("activityType") or {}).get("typeKey", "activity")
        dur_min = int((first.get("duration") or 0) // 60)
        if len(activities) == 1:
            parts.append(f"1 activity ({name} {dur_min}m)")
        else:
            parts.append(f"{len(activities)} activities")
    steps = stats.get("totalSteps")
    if steps:
        parts.append(f"{steps} steps")

    # Sign-off (night before this day) — read the just-written JSON so a
    # Pete-confirmed value wins over the detected estimate.
    try:
        so = json.loads((JSON_DIR / f"{iso}.json").read_text()).get("signoff") or {}
        t = so.get("confirmed") or so.get("detected")
        if t:
            parts.append(f"signed off ~{t} (night before)")
    except Exception:
        pass

    if not parts:
        return f"- {iso}: no data"
    return f"- {iso}: " + ", ".join(parts)


def push_dashboard(commit_msg=None):
    """Sync the dashboard repo clone with origin/main, then commit + push the
    new data/ snapshot so Vercel auto-deploys.

    Sync-first ordering matters: the clone is shared with Claude Code sessions
    that may have pushed UI/code commits to origin since the last cron run
    (e.g. 24 May PWA commit b7df428). Without `pull --rebase`, the cron's local
    commit diverges from origin, the push is rejected non-fast-forward, and
    Vercel silently doesn't deploy (lesson 2026-05-25).

    Non-fatal on failure (warns, never raises), but writes a SYNC_FAILED marker
    to the report dict so the daily-note line shows the breakage loudly instead
    of swallowing it.

    Retries up to 3 times with 15s delay to handle LibreSSL SSL_ERROR_SYSCALL
    (network not yet ready when cron fires after Mac wake-from-sleep).
    """
    if not DASHBOARD_REPO_ROOT.exists():
        return None
    import subprocess, time
    repo = str(DASHBOARD_REPO_ROOT)
    last_err = None
    for attempt in range(1, 4):
        try:
            # 1. Pull-rebase first so we're on top of the latest origin/main.
            #    --autostash protects any uncommitted data/ writes during the rebase.
            subprocess.run(["git", "-C", repo, "fetch", "origin", "main"], check=True)
            subprocess.run(["git", "-C", repo, "pull", "--rebase", "--autostash",
                            "origin", "main"], check=True)
            # 2. Stage + commit (if there's anything new).
            subprocess.run(["git", "-C", repo, "add", "data/"], check=True)
            staged = subprocess.run(["git", "-C", repo, "diff", "--cached", "--quiet"])
            if staged.returncode != 0:
                msg = commit_msg or f"data: Garmin pull {datetime.now(TZ).date().isoformat()}"
                subprocess.run(["git", "-C", repo,
                                "-c", "user.name=PortalPeteZero",
                                "-c", "user.email=pete.ashcroft@sygma-solutions.com",
                                "commit", "-m", msg], check=True)
                subprocess.run(["git", "-C", repo, "push", "origin", "main"], check=True)
                print("  Pushed JSON snapshot to dashboard repo (Vercel will auto-deploy)")
                return "pushed"
            else:
                print("  No JSON changes to push")
                return "no-changes"
        except Exception as e:
            last_err = e
            if attempt < 3:
                print(f"  dashboard push attempt {attempt} failed (SSL/network), retrying in 15s: {e}", file=sys.stderr)
                time.sleep(15)
    print(f"  WARNING: dashboard push failed after 3 attempts (non-fatal): {last_err}", file=sys.stderr)
    return f"FAILED: {last_err}"


def set_signoff(date_iso: str, hhmm: str):
    """Write Pete's confirmed sign-off for a day (brain skill calls this when Pete
    corrects the morning estimate). Updates the vault JSON's signoff.confirmed,
    mirrors to the dashboard clone, pushes so Vercel redeploys. No Garmin auth."""
    json_path = JSON_DIR / f"{date_iso}.json"
    if not json_path.exists():
        print(f"  No Garmin file for {date_iso} — nothing to set.", file=sys.stderr)
        return
    data = json.loads(json_path.read_text())
    so = data.get("signoff") or {"detected": None, "detected_iso": None,
                                  "source": "claude-session-activity"}
    so["confirmed"] = hhmm
    data["signoff"] = so
    json_path.write_text(json.dumps(data, indent=2, default=str))
    if DASHBOARD_REPO_DATA.parent.exists():
        try:
            DASHBOARD_REPO_DATA.mkdir(parents=True, exist_ok=True)
            (DASHBOARD_REPO_DATA / f"{date_iso}.json").write_text(
                json.dumps(data, indent=2, default=str))
        except Exception as e:
            print(f"  Warning: mirror failed: {e}", file=sys.stderr)
    push_dashboard(commit_msg=f"signoff: {date_iso} confirmed {hhmm}")
    print(f"  Sign-off for {date_iso} set to {hhmm} (confirmed).")


def main():
    ap = argparse.ArgumentParser(description="Pull Garmin data for a day or range and write to vault.")
    ap.add_argument("date", nargs="?", help="ISO date (default: yesterday Atlantic/Canary)")
    ap.add_argument("--backfill", type=int, help="Backfill N days back from today (exclusive)")
    ap.add_argument("--range", nargs=2, metavar=("START", "END"), help="Date range (inclusive)")
    ap.add_argument("--dry", action="store_true", help="Don't write files, just report")
    ap.add_argument("--publish-only", action="store_true",
                    help="Skip the Garmin fetch; regenerate weekly + lessons JSON and push (PF processes publish on completion)")
    ap.add_argument("--sleep-between", type=float, default=0.5,
                    help="Seconds to sleep between days when batching (default 0.5)")
    ap.add_argument("--no-push", action="store_true",
                    help="Write files + CC garmin_daily (incl. live snapshot) but skip the git commit/push "
                         "to the dashboard repo. The CC dashboard reads the snapshot live, so a push isn't "
                         "needed to surface the data.")
    ap.add_argument("--set-signoff", nargs=2, metavar=("DATE", "HHMM"),
                    help="Set Pete-confirmed sign-off (HH:MM) for a day; the brain calls this on correction. No Garmin pull.")
    args = ap.parse_args()

    if args.set_signoff:
        set_signoff(args.set_signoff[0], args.set_signoff[1])
        return

    if args.publish_only:
        # No Garmin fetch — regenerate the vault-derived JSON the PF processes
        # own (weekly + lessons) and push, so a journal/weekly entry shows on the
        # dashboard immediately instead of waiting for the next cron.
        started = datetime.now(TZ)
        print(f"=== garmin-daily-pull --publish-only | {started.isoformat()} | dry={args.dry} ===")
        n_w = write_all_weekly_snapshots(dry=args.dry)
        n_l = write_all_lessons(dry=args.dry)
        push_res = "skipped(dry)" if args.dry else (push_dashboard(commit_msg="publish: PF weekly + lessons") or "no-changes")
        print(f"  publish-only: weekly={n_w} lessons={n_l} push={push_res}")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    started = datetime.now(TZ)
    print(f"=== garmin-daily-pull | {started.isoformat()} | dry={args.dry} ===")

    g = garmin_mod.GarminAPI()
    _persist_garmin_token(g)  # keep the CC token copy fresh (Garmin rotates the refresh token)

    # Decide the list of dates
    dates: list[str] = []
    today = datetime.now(TZ).date()
    if args.range:
        start = date.fromisoformat(args.range[0])
        end = date.fromisoformat(args.range[1])
        d = start
        while d <= end:
            dates.append(d.isoformat())
            d += timedelta(days=1)
    elif args.backfill:
        # Backfill ends yesterday, goes back N days
        for i in range(1, args.backfill + 1):
            dates.append((today - timedelta(days=i)).isoformat())
        dates.reverse()  # chronological order
    elif args.date:
        dates = [args.date]
    else:
        # Pull yesterday (now complete) AND today (last night's sleep + today's
        # readiness are already in; today's activity fills in by tomorrow's run)
        # so the dashboard's latest card is TODAY and matches Garmin.
        dates = [(today - timedelta(days=1)).isoformat(), today.isoformat()]

    print(f"  Pulling {len(dates)} day(s)...")

    written = 0
    empty = 0
    errors = 0
    last_day = None
    for i, d in enumerate(dates, 1):
        try:
            path, status, has_data, day = write_day(g, d, dry=args.dry)
            marker = "OK " if has_data else "EMP"
            print(f"  [{i}/{len(dates)}] {marker} {d} -> {path.name} ({status})")
            if has_data:
                written += 1
                last_day = day  # remember the most recent day with data
            else:
                empty += 1
        except Exception as e:
            print(f"  [{i}/{len(dates)}] ERR {d}: {type(e).__name__}: {e}", file=sys.stderr)
            errors += 1
        if i < len(dates):
            time.sleep(args.sleep_between)

    # Weekly snapshots — write a JSON for every PF weekly entry on disk.
    # Cheap, idempotent, picks up late edits.
    weekly_written = write_all_weekly_snapshots(dry=args.dry)
    if weekly_written:
        print(f"  Weekly snapshots: {weekly_written} file(s){' [DRY-RUN]' if args.dry else ''}")
    lessons_written = write_all_lessons(dry=args.dry)
    if lessons_written:
        print(f"  Lessons index: {lessons_written} lesson(s){' [DRY-RUN]' if args.dry else ''}")

    # Push the day's JSON to the dashboard repo clone so Vercel auto-deploys.
    # (Legacy path; the CC dashboard now reads the snapshot live from garmin_daily,
    # so --no-push skips this and the data still surfaces.)
    push_status = None
    if not args.dry and not args.no_push:
        push_status = push_dashboard()

    # Loud push-failure tag for the daily-note line so silent breakage stops
    # (lesson 2026-05-25: rebase-needed → push rejected → Vercel didn't deploy
    # → brain Resume happily reported "ok" because nothing in the line said
    # otherwise).
    push_tag = ""
    if push_status and push_status.startswith("FAILED"):
        push_tag = f" | PUSH FAILED ({push_status[8:80]})"

    # Daily-note line: rich headline if single-day or last-day-of-batch had data,
    # otherwise a terse summary line.
    if last_day and len(dates) == 1:
        # Single-day cron run: write the rich headline so brain Resume Step 2 picks it up
        summary = _daily_note_summary_line(last_day)
        if push_tag:
            summary = summary + push_tag
    elif last_day:
        # Batch (e.g. backfill): write a terse status + the last-day headline for context
        summary = (
            f"- {datetime.now(TZ).strftime('%H:%M')} batch | "
            f"days {len(dates)}, written {written}, empty {empty}, errors {errors}"
            f"{push_tag}"
            f"{' [DRY-RUN]' if args.dry else ''}.  Latest: "
            + _daily_note_summary_line(last_day).lstrip("- ")
        )
    else:
        summary = (
            f"- {datetime.now(TZ).strftime('%H:%M')} run | "
            f"days {len(dates)}, written {written}, empty {empty}, errors {errors}"
            f"{push_tag}"
            f"{' [DRY-RUN]' if args.dry else ''}."
        )
    print()
    print(summary)
    if not args.dry:
        append_daily_note_line(summary)


if __name__ == "__main__":
    main()
