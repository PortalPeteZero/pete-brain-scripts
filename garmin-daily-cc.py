#!/usr/bin/env python3
"""garmin-daily-cc.py — Railway-headless Garmin → CC garmin_daily updater.

The full garmin-daily-pull.py writes a rich per-day md + JSON to Google Drive + a dashboard repo
clone + the vault daily note — all Mac/Drive-coupled, so it can't run headless. The Business OS only
needs the Garmin data to reach the COMMAND CENTRE reliably (Pete 23 Jun: "not bothered about an email
or the morning briefing as long as it updates the CC properly"). This thin cron does exactly that: it
REUSES the canonical pull_day() + _upsert_garmin_daily() from garmin-daily-pull.py (no duplication, no
drift — same bootstrap philosophy) to pull yesterday + today and upsert the CC `garmin_daily` table.

Runs on Railway (always-on), so the CC's Garmin data stays current even when the Mac is asleep — the
whole reason Garmin moved to Railway. No Drive/vault/dashboard writes.

# CRON-META
# what: Garmin → CC garmin_daily (headless). Pulls yesterday+today, upserts the CC table.
# why: keeps the CC's Garmin data current from the cloud, Mac-independent — Pete: just needs to update the CC properly (no email/briefing/Drive narrative)
# reads: Garmin Connect API (stored OAuth tokens, GARMIN_TOKENS_JSON); reuses garmin-daily-pull.pull_day
# writes: CC public.garmin_daily (one row per date, idempotent on date) incl. the snapshot column (the live Health-dashboard feed)
# entity: canary-detect
# schedule: 0 7,22 * * *
# timezone: Atlantic/Canary
# CRON-META-END
"""
import importlib.util
import datetime as _dt
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Reuse the canonical garmin-daily-pull (flat-repo sibling on Railway = /app; Library/.../scripts locally).
# Importing it executes only Path-constant definitions + the garmin-api helper load — no Drive writes
# (those live in main(), which we never call). So the rich-narrative code never runs headless.
_p = Path(__file__).resolve().parent / "garmin-daily-pull.py"
_spec = importlib.util.spec_from_file_location("garmin_daily_pull", str(_p))
gdp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gdp)

TZ = ZoneInfo("Atlantic/Canary")


def main():
    g = gdp.garmin_mod.GarminAPI()
    today = _dt.datetime.now(TZ).date()
    dates = [(today - timedelta(days=1)).isoformat(), today.isoformat()]
    ok = 0
    for d in dates:
        try:
            day = gdp.pull_day(g, d)
            # Build the full dashboard snapshot (sleep/hrv/readiness/body-battery/daily/
            # training/activities) and store it in garmin_daily.snapshot — the live feed
            # the CC Health dashboard reads. Headless-safe: journal/signoff degrade to
            # null here (Drive/session-local, absent on Railway); the metrics are what
            # the dashboard needs and they all come from Garmin.
            snapshot = gdp.build_json_snapshot(day, g)
            gdp._upsert_garmin_daily(day, snapshot)
            has = (day.get("sleep") or {}).get("score") is not None or len(day.get("activities") or []) > 0
            print(f"garmin-daily-cc: {d} → garmin_daily upserted ({'data' if has else 'no-data'})")
            ok += 1
        except Exception as e:
            print(f"garmin-daily-cc: {d} FAILED: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"garmin-daily-cc: done — {ok}/{len(dates)} days upserted to CC garmin_daily")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
