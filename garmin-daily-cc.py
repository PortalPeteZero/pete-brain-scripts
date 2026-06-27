#!/usr/bin/env python3
"""garmin-daily-cc.py — Railway-headless Garmin → CC garmin_daily updater.

Pulls yesterday + today from Garmin and upserts the CC `garmin_daily` table. It imports the pure,
cloud-native **garmin-pull-lib.py** (pull_day / build_json_snapshot / _upsert_garmin_daily /
_persist_garmin_token — no Drive, no local files, no main) so there is no duplication and no drift.
The old local `garmin-daily-pull.py` was deleted 27 Jun 2026.

Runs on Railway (always-on), so the CC's Garmin data stays current even when the Mac is asleep — the
whole reason Garmin moved to Railway. ONE JOB: Garmin → CC. No Drive/vault/dashboard writes.

# CRON-META
# what: Garmin → CC garmin_daily (headless). Pulls yesterday+today, upserts the CC table.
# why: keeps the CC's Garmin data current from the cloud, Mac-independent — Pete: just needs to update the CC properly (no email/briefing/Drive narrative)
# reads: Garmin Connect API (stored OAuth tokens, GARMIN_TOKENS_JSON); reuses garmin-pull-lib.pull_day
# writes: CC public.garmin_daily (one row per date, idempotent on date) incl. the snapshot column (the live Health-dashboard feed)
# entity: canary-detect
# schedule: 0 1,7,9,17,22 * * *
# timezone: Atlantic/Canary
# secrets: GARMIN_TOKENS_JSON
# note: a list-hour schedule bypasses tz-conversion, so this is authored in UTC — 0,6,8,16,21 UTC = 1am / 7am / 9am / 5pm / 10pm Atlantic/Canary (summer). 5x/day, set by Pete 26 Jun 2026.
# note: ONE JOB — pull Garmin → CC garmin_daily. No Drive, no journal (journal lives in health_journal, authored in the app). Drive key removed 27 Jun 2026.
# CRON-META-END
"""
import importlib.util
import datetime as _dt
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Reuse the pure cloud-native Garmin library (flat-repo sibling on Railway = /app). No Drive, no main.
_p = Path(__file__).resolve().parent / "garmin-pull-lib.py"
_spec = importlib.util.spec_from_file_location("garmin_pull_lib", str(_p))
gdp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gdp)

TZ = ZoneInfo("Atlantic/Canary")


def _preserve_signoff(date_iso, snap):
    """Keep a confirmed/detected sign-off already stored for this date (the cron can't re-detect the
    sign-off headlessly). Sign-off only — the **journal is no longer in the snapshot**; it lives in the
    CC `health_journal` table (authored in the app) and the dashboard reads it from there directly."""
    import json as _j, urllib.request as _u, os as _o
    try:
        url = _o.environ.get("CC_SUPABASE_URL"); key = _o.environ.get("CC_SUPABASE_SERVICE_KEY")
        if not (url and key):
            kp = Path(_o.environ.get("VAULT", ".")) / "Library/processes/secrets/command-centre-supabase-keys.json"
            kd = _j.loads(kp.read_text()); url, key = kd["url"], kd["service_role_key"]
        req = _u.Request(url.rstrip("/") + f"/rest/v1/garmin_daily?date=eq.{date_iso}&select=snapshot",
                         headers={"apikey": key, "Authorization": "Bearer " + key})
        rows = _j.loads(_u.urlopen(req, timeout=20).read())
        prev = (rows[0].get("snapshot") if rows else None) or {}
        ps = prev.get("signoff") or {}; ns = snap.get("signoff") or {}
        if (ps.get("detected") or ps.get("confirmed")) and not (ns.get("detected") or ns.get("confirmed")):
            snap["signoff"] = ps
    except Exception as e:
        print(f"  (preserve signoff skipped for {date_iso}: {e})", file=sys.stderr)
    return snap


def main():
    g = gdp.garmin_mod.GarminAPI()
    gdp._persist_garmin_token(g)  # republish the live token to CC secrets each run so local boots never hit a stale copy
    today = _dt.datetime.now(TZ).date()
    dates = [(today - timedelta(days=1)).isoformat(), today.isoformat()]
    ok = 0
    for d in dates:
        try:
            day = gdp.pull_day(g, d)
            # Build the dashboard snapshot (sleep/hrv/readiness/body-battery/daily/training/activities)
            # from Garmin and store it in garmin_daily.snapshot — the metrics feed the dashboard reads.
            # ONE JOB: pull Garmin → CC. The journal is NOT here any more (lives in health_journal,
            # authored in the app); sign-off is preserved across re-runs. No Drive, no journal fetch.
            snapshot = gdp.build_json_snapshot(day, g)
            snapshot = _preserve_signoff(d, snapshot)
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
