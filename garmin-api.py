#!/usr/bin/env python3
"""Garmin Connect helper — single canonical path for all Garmin Connect work.

Parallels `gmail-api.py` and `calendar-api.py` in pattern and style. Wraps the
`python-garminconnect` library (which uses curl_cffi for Cloudflare TLS
impersonation, post-March-2026 bypass) and exposes:

  * A `GarminAPI` class for library use from other scripts (pf-journal cron,
    pf-weekly-loop, anywhere else we ever need health data).
  * A CLI for ad-hoc pulls from the shell.

Auth:
  * Bootstrap once with `garmin-bootstrap-login.py` (see config doc).
  * OAuth tokens stored at:
      /tmp/pbs/Library/processes/secrets/garminconnect-tokens/
  * Tokens valid ~1 year. No password / MFA needed for subsequent runs.

CLI usage:
  python3 garmin-api.py whoami
  python3 garmin-api.py sleep [YYYY-MM-DD]            # default: yesterday
  python3 garmin-api.py hrv [YYYY-MM-DD]
  python3 garmin-api.py body-battery [YYYY-MM-DD]
  python3 garmin-api.py stats [YYYY-MM-DD]            # daily summary
  python3 garmin-api.py training-readiness [YYYY-MM-DD]
  python3 garmin-api.py heart-rate [YYYY-MM-DD]
  python3 garmin-api.py stress [YYYY-MM-DD]
  python3 garmin-api.py steps [YYYY-MM-DD]
  python3 garmin-api.py activities [start] [end]      # default: last 30 days
  python3 garmin-api.py recovery [YYYY-MM-DD]         # composite: sleep+HRV+BB+TR
  python3 garmin-api.py methods                       # list all 132 available
  python3 garmin-api.py raw <method_name> [args...]   # passthrough

Library usage:
  from garmin_api import GarminAPI
  g = GarminAPI()
  s = g.sleep("2026-05-23")
  h = g.hrv("2026-05-23")
  r = g.recovery("2026-05-23")           # composite dict
"""

import importlib
import inspect
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import garminconnect  # type: ignore

VAULT = Path(os.environ.get("VAULT", "/tmp/pbs"))
SECRETS = VAULT / "Library/processes/secrets"
TOKENS_DIR = SECRETS / "garminconnect-tokens"
EMAIL_FILE = SECRETS / "garmin-email"
PASSWORD_FILE = SECRETS / "garmin-password"


def yesterday_iso() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def _date_or_yesterday(arg: str | None) -> str:
    """Accept ISO date or default to yesterday."""
    return arg if arg else yesterday_iso()


class GarminAPI:
    """Thin wrapper over python-garminconnect.

    Loads tokens from the vault. If tokens are missing or expired, raises with
    a clear instruction to run the bootstrap script.
    """

    def __init__(self, tokens_dir: Path = TOKENS_DIR):
        self.tokens_dir = Path(tokens_dir)
        if not (self.tokens_dir / "garmin_tokens.json").exists():
            raise RuntimeError(
                f"No Garmin tokens at {self.tokens_dir}. "
                f"Bootstrap login first — see [[garmin-api-configuration]]."
            )
        self.client = garminconnect.Garmin()
        try:
            self.client.login(str(self.tokens_dir))
        except Exception as e:
            raise RuntimeError(
                f"Garmin token load failed: {e}\n"
                f"Tokens may have expired. Re-run the bootstrap script."
            )

    # -------------------------------------------------------------- core data

    def sleep(self, date_iso: str | None = None) -> dict:
        """Sleep data for the night ending on `date_iso`. Default yesterday."""
        d = _date_or_yesterday(date_iso)
        raw = self.client.get_sleep_data(d) or {}
        dto = raw.get("dailySleepDTO") or {}
        scores = dto.get("sleepScores") or {}
        overall = scores.get("overall") or {}
        return {
            "date": d,
            "score": overall.get("value"),
            "qualifier": overall.get("qualifierKey"),
            "total_minutes": (dto.get("sleepTimeSeconds") or 0) // 60,
            "deep_minutes": (dto.get("deepSleepSeconds") or 0) // 60,
            "rem_minutes": (dto.get("remSleepSeconds") or 0) // 60,
            "light_minutes": (dto.get("lightSleepSeconds") or 0) // 60,
            "awake_minutes": (dto.get("awakeSleepSeconds") or 0) // 60,
            "raw": raw,
        }

    def hrv(self, date_iso: str | None = None) -> dict:
        """HRV data for the night ending on `date_iso`. Default yesterday."""
        d = _date_or_yesterday(date_iso)
        raw = self.client.get_hrv_data(d) or {}
        s = raw.get("hrvSummary") or {}
        return {
            "date": d,
            "last_night_avg": s.get("lastNightAvg"),
            "last_night_5min_high": s.get("lastNight5MinHigh"),
            "weekly_avg": s.get("weeklyAvg"),
            "status": s.get("status"),
            "feedback_phrase": s.get("feedbackPhrase"),
            "raw": raw,
        }

    def body_battery(self, date_iso: str | None = None) -> dict:
        """Body battery summary for a single day. Default yesterday."""
        d = _date_or_yesterday(date_iso)
        raw = self.client.get_body_battery(d, d) or []
        if isinstance(raw, list) and raw:
            b = raw[0]
            return {
                "date": d,
                "charged": b.get("charged"),
                "drained": b.get("drained"),
                "highest": b.get("highestBatteryLevel"),
                "lowest": b.get("lowestBatteryLevel"),
                "raw": b,
            }
        return {"date": d, "charged": None, "drained": None, "highest": None, "lowest": None, "raw": raw}

    def stats(self, date_iso: str | None = None) -> dict:
        """Daily user summary (steps, calories, sleep summary, etc.)."""
        d = _date_or_yesterday(date_iso)
        return self.client.get_stats(d) or {}

    def training_readiness(self, date_iso: str | None = None) -> dict:
        d = _date_or_yesterday(date_iso)
        raw = self.client.get_training_readiness(d) or []
        if isinstance(raw, list) and raw:
            r = raw[0]
            return {
                "date": d,
                "score": r.get("score"),
                "level": r.get("level"),
                "feedback_long": r.get("feedbackLong"),
                "feedback_short": r.get("feedbackShort"),
                "raw": r,
            }
        return {"date": d, "score": None, "level": None, "raw": raw}

    def heart_rate(self, date_iso: str | None = None) -> dict:
        d = _date_or_yesterday(date_iso)
        return self.client.get_heart_rates(d) or {}

    def stress(self, date_iso: str | None = None) -> dict:
        d = _date_or_yesterday(date_iso)
        return self.client.get_all_day_stress(d) or {}

    def steps(self, date_iso: str | None = None) -> dict:
        d = _date_or_yesterday(date_iso)
        return self.client.get_steps_data(d) or {}

    def activities(self, start: str | None = None, end: str | None = None) -> list:
        if not end:
            end = date.today().isoformat()
        if not start:
            start = (date.today() - timedelta(days=30)).isoformat()
        return self.client.get_activities_by_date(start, end) or []

    def activity_splits(self, activity_id) -> dict:
        """Per-lap splits for a single activity. Hits Garmin's `/splits` endpoint
        (NOT `/typedsplits`, which returns whole-activity-by-type rollups —
        useless for showing per-km / per-lap detail). Returns the raw payload
        with `lapDTOs` array; callers reshape as needed."""
        if not activity_id:
            return {}
        try:
            return self.client.get_activity_splits(str(activity_id)) or {}
        except Exception:
            return {}

    def activity_weather(self, activity_id) -> dict:
        """Weather observed during one activity, from Garmin's per-activity
        `/weather` endpoint (nearest station, e.g. GCRR Lanzarote Airport,
        issued mid-session). Returns Garmin's raw payload — IMPERIAL units
        (temp °F, windSpeed mph); callers convert to metric. {} on any
        failure or for indoor activities with no weather attached."""
        if not activity_id:
            return {}
        try:
            return self.client.connectapi(
                f"/activity-service/activity/{activity_id}/weather") or {}
        except Exception:
            return {}

    def training_status(self, date_iso: str | None = None) -> dict:
        """Per-day training status: status enum (1-7), feedback phrase, ACWR,
        load tunnel, VO2 max. Returns flat dict; callers don't need to walk the
        nested deviceId-keyed structure Garmin returns."""
        d = _date_or_yesterday(date_iso)
        raw = self.client.get_training_status(d) or {}
        out: dict = {"date": d}
        # Walk into mostRecentTrainingStatus.latestTrainingStatusData.{anyDeviceId}
        mrts = (raw.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
        ts_inner = next(iter(mrts.values()), None) if mrts else None
        if ts_inner:
            out["status_int"] = ts_inner.get("trainingStatus")
            out["status_phrase"] = ts_inner.get("trainingStatusFeedbackPhrase")
            out["fitness_trend"] = ts_inner.get("fitnessTrend")
            out["primary_sport"] = ts_inner.get("sport")
            out["since_date"] = ts_inner.get("sinceDate")
            acwr = ts_inner.get("acuteTrainingLoadDTO") or {}
            out["acute_load"] = acwr.get("dailyTrainingLoadAcute")
            out["chronic_load"] = acwr.get("dailyTrainingLoadChronic")
            out["acwr_ratio"] = acwr.get("dailyAcuteChronicWorkloadRatio")
            out["acwr_status"] = acwr.get("acwrStatus")
            out["acwr_percent"] = acwr.get("acwrPercent")
            out["load_tunnel_min"] = acwr.get("minTrainingLoadChronic")
            out["load_tunnel_max"] = acwr.get("maxTrainingLoadChronic")
        vo2 = (raw.get("mostRecentVO2Max") or {}).get("generic") or {}
        if vo2:
            out["vo2_max"] = vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue")
            out["vo2_max_date"] = vo2.get("calendarDate")
            out["fitness_age"] = vo2.get("fitnessAge")
        return out

    def race_predictions(self) -> dict:
        """Latest race-time predictions (5K / 10K / HM / Marathon, in seconds).
        Garmin returns one snapshot, not historical — point-in-time fitness check."""
        raw = self.client.get_race_predictions() or {}
        return {
            "calendar_date": raw.get("calendarDate"),
            "time_5k_sec": raw.get("time5K"),
            "time_10k_sec": raw.get("time10K"),
            "time_half_marathon_sec": raw.get("timeHalfMarathon"),
            "time_marathon_sec": raw.get("timeMarathon"),
        }

    # -------------------------------------------------------------- composite

    def recovery(self, date_iso: str | None = None) -> dict:
        """Composite single-shot pull for pf-journal: sleep + HRV + body battery
        + training readiness. One call from the cron / Cowork session."""
        d = _date_or_yesterday(date_iso)
        out = {"date": d}
        try:
            out["sleep"] = self.sleep(d)
        except Exception as e:
            out["sleep_error"] = str(e)
        try:
            out["hrv"] = self.hrv(d)
        except Exception as e:
            out["hrv_error"] = str(e)
        try:
            out["body_battery"] = self.body_battery(d)
        except Exception as e:
            out["body_battery_error"] = str(e)
        try:
            out["training_readiness"] = self.training_readiness(d)
        except Exception as e:
            out["training_readiness_error"] = str(e)
        return out

    # -------------------------------------------------------------- introspection

    def methods(self) -> list:
        """Return every public method on the underlying Garmin client.

        Future sessions can use this to discover what data they can pull
        without re-reading the library README.
        """
        return sorted([
            name for name, _ in inspect.getmembers(self.client, predicate=callable)
            if not name.startswith("_")
        ])

    def raw(self, method_name: str, *args):
        """Passthrough — call any method on the underlying client by name.

        Use when you need an endpoint this wrapper doesn't expose. Returns the
        raw response unchanged.
        """
        fn = getattr(self.client, method_name, None)
        if fn is None or not callable(fn):
            raise ValueError(f"Unknown method: {method_name!r}")
        return fn(*args)


# ============================================================================
# CLI
# ============================================================================

def _print(obj):
    """Print as JSON if dict/list, else as a string."""
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def _summarise_recovery(r: dict):
    """Compact one-screen view of the composite recovery pull."""
    s = r.get("sleep") or {}
    h = r.get("hrv") or {}
    b = r.get("body_battery") or {}
    t = r.get("training_readiness") or {}
    print(f"=== Recovery for {r.get('date')} ===")
    print(f"  Sleep:              score {s.get('score','n/a')} ({s.get('qualifier','?')})")
    print(f"                      total {s.get('total_minutes',0)}m  deep {s.get('deep_minutes',0)}m  REM {s.get('rem_minutes',0)}m  light {s.get('light_minutes',0)}m  awake {s.get('awake_minutes',0)}m")
    print(f"  HRV:                last night {h.get('last_night_avg','n/a')}  weekly avg {h.get('weekly_avg','n/a')}  status {h.get('status','?')}")
    print(f"                      feedback: {h.get('feedback_phrase','?')}")
    print(f"  Body Battery:       charged {b.get('charged','n/a')}  drained {b.get('drained','n/a')}  highest {b.get('highest','n/a')}  lowest {b.get('lowest','n/a')}")
    print(f"  Training Readiness: score {t.get('score','n/a')}  level {t.get('level','?')}")
    if t.get("feedback_long"):
        print(f"                      feedback: {t['feedback_long']}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    g = GarminAPI()

    if cmd == "whoami":
        try:
            print(g.client.get_full_name())
        except Exception:
            print(g.client.username if hasattr(g.client, "username") else "n/a")
    elif cmd == "sleep":
        _print(g.sleep(*args))
    elif cmd == "hrv":
        _print(g.hrv(*args))
    elif cmd == "body-battery":
        _print(g.body_battery(*args))
    elif cmd == "stats":
        _print(g.stats(*args))
    elif cmd == "training-readiness":
        _print(g.training_readiness(*args))
    elif cmd == "heart-rate":
        _print(g.heart_rate(*args))
    elif cmd == "stress":
        _print(g.stress(*args))
    elif cmd == "steps":
        _print(g.steps(*args))
    elif cmd == "activities":
        _print(g.activities(*args))
    elif cmd == "recovery":
        r = g.recovery(*args)
        # Default to summary; pass `--json` to dump full structure
        if "--json" in args:
            _print(r)
        else:
            _summarise_recovery(r)
    elif cmd == "training-status":
        _print(g.training_status(*args))
    elif cmd == "race-predictions":
        _print(g.race_predictions())
    elif cmd == "methods":
        ms = g.methods()
        print(f"{len(ms)} methods on Garmin client:")
        for m in ms:
            print(f"  {m}")
    elif cmd == "raw":
        if not args:
            print("Usage: raw <method_name> [args...]", file=sys.stderr)
            sys.exit(2)
        _print(g.raw(args[0], *args[1:]))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
