#!/usr/bin/env python3
"""garmin-pull-lib.py — pure, cloud-native Garmin pull library.

Imported by garmin-daily-cc.py (the Railway cron). Exposes the functions that fetch a day's data from
Garmin Connect and shape it for the CC: `pull_day()`, `build_json_snapshot()`, `_upsert_garmin_daily()`,
`_persist_garmin_token()` (+ their Garmin-data helpers). NO Drive, NO local files, NO Mac paths, NO
standalone entry point — the cron is the only caller and it writes ONLY the CC `garmin_daily` table
(metrics). The journal/feedback/weekly/zones live in their own CC `health_*` tables, authored in the app.

Failure modes:
  * Garmin token expired -> raise; Pete re-bootstraps via the bootstrap script.
  * Per-endpoint exception -> log, return what we have, skip the section.
  * No data for a date (watch not worn) -> the row's metrics are null ("no data"), not a missing row.
"""

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
TZ = ZoneInfo("Atlantic/Canary")

# Optional second JSON destination — a local clone of the dashboard repo.
# Cron extends to git-commit + push from there. Disabled until repo + clone exist.

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

    # Journal is CC-native (the `health_journal` table, authored in the app) — NOT read from Drive here.
    # The dashboard merges it from health_journal; the snapshot carries no journal.
    journal = None

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
    """Sign-off placeholder. Cloud-native: there is no local-session estimate any more (that read the
    Mac's Claude session dirs, absent on Railway). Pete sets his confirmed sign-off via the CC
    (`garmin-signoff.py --set DATE HHMM`), and garmin-daily-cc._preserve_signoff keeps it across re-runs."""
    return {"detected": None, "detected_iso": None, "confirmed": None, "source": "pete-confirmed"}


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


