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
# schedule: 0 1,7,9,17,22 * * *
# timezone: Atlantic/Canary
# secrets: GARMIN_TOKENS_JSON, GOOGLE_SA_JSON
# note: a list-hour schedule bypasses tz-conversion, so this is authored in UTC — 0,6,8,16,21 UTC = 1am / 7am / 9am / 5pm / 10pm Atlantic/Canary (summer). 5x/day, set by Pete 26 Jun 2026.
# note: GOOGLE_SA_JSON materialises the Drive service-account key so the headless run can fetch the PF journal from Drive (Mac-independent).
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


def _preserve_local_fields(date_iso, snap):
    """The headless Railway pull can't read Pete's Drive journal or his local Claude
    sign-off, so build_json_snapshot returns journal=None / signoff(detected=None)
    here. Don't clobber a richer snapshot already stored for this date (written by the
    local full pull or the one-off backfill). Non-fatal: on any error, snapshot unchanged."""
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
        if not snap.get("journal") and prev.get("journal"):
            snap["journal"] = prev["journal"]
        ps = prev.get("signoff") or {}; ns = snap.get("signoff") or {}
        if (ps.get("detected") or ps.get("confirmed")) and not (ns.get("detected") or ns.get("confirmed")):
            snap["signoff"] = ps
    except Exception as e:
        print(f"  (preserve journal/signoff skipped for {date_iso}: {e})", file=sys.stderr)
    return snap


def _fetch_journal_from_drive(date_iso):
    """Headless journal fetch. The full pull reads the PF journal from a local Drive mount that only
    exists on Pete's Mac, so on Railway the journal is always null and never reaches the dashboard.
    Here we find the journal file in the CC `drive_files` index, download it from Drive via the API,
    and return the same dict shape as gdp.load_journal_entry. Non-fatal: any error → None."""
    import json as _j, urllib.request as _u, urllib.parse as _up, os as _o, importlib.util as _il
    try:
        # Lazy + guarded: drive-api.py loads the Google service-account key AT IMPORT TIME, and that
        # key isn't materialised on every Railway service. Import it inside the try so a missing key
        # degrades the journal to null — it must NEVER crash the metrics pipeline.
        _dp = Path(__file__).resolve().parent / "drive-api.py"
        _dspec = _il.spec_from_file_location("drive_api", str(_dp))
        _drive = _il.module_from_spec(_dspec); _dspec.loader.exec_module(_drive)

        url = _o.environ.get("CC_SUPABASE_URL"); key = _o.environ.get("CC_SUPABASE_SERVICE_KEY")
        if not (url and key):
            kp = Path(_o.environ.get("VAULT", ".")) / "Library/processes/secrets/command-centre-supabase-keys.json"
            kd = _j.loads(kp.read_text()); url, key = kd["url"], kd["service_role_key"]
        ppath = f"Passion Fit/journal/{date_iso}.md"
        q = (url.rstrip("/") + "/rest/v1/drive_files?select=drive_file_id&is_folder=eq.false&limit=1"
             "&path=eq." + _up.quote(ppath))
        rows = _j.loads(_u.urlopen(_u.Request(q, headers={"apikey": key, "Authorization": "Bearer " + key}), timeout=20).read())
        if not rows:
            return None
        fid = rows[0]["drive_file_id"]
        durl = f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media&supportsAllDrives=true"
        text = _u.urlopen(_u.Request(durl, headers={"Authorization": "Bearer " + _drive.get_token()}), timeout=30).read().decode("utf-8")
        fm, body = gdp._parse_md_frontmatter(text)
        return {"date": date_iso, "exists": True, "frontmatter": fm or {}, "body": body.strip()}
    except Exception as e:
        print(f"  (drive journal fetch skipped for {date_iso}: {e})", file=sys.stderr)
        return None


def main():
    g = gdp.garmin_mod.GarminAPI()
    gdp._persist_garmin_token(g)  # republish the live token to CC secrets each run so local boots never hit a stale copy
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
            snapshot = _preserve_local_fields(d, snapshot)
            # Headless: the local Drive mount isn't on Railway, so pull the journal from Drive directly
            # if neither the live snapshot nor a prior local pull supplied one. Keeps the dashboard's
            # journal current Mac-independently (fixes "no journal showing").
            if not snapshot.get("journal"):
                j = _fetch_journal_from_drive(d)
                if j:
                    snapshot["journal"] = j
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
