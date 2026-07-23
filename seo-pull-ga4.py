#!/usr/bin/env python3
"""
seo-pull-ga4.py -- daily GA4 pull into public.seo_ga4_daily (SEO platform, phase 2).

FREE source (GA4 Standard). For every in-scope property with a ga4 id it pulls a rolling window of
per-day, per-channel sessions / users / conversions and UPSERTs them, so any GA4 reprocessing is absorbed
idempotently. Reports read seo_ga4_daily; they never call the GA4 API.

Conversions = the property's key events summed (contact events, all counted once-per-session on Sygma+CD).

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/seo-pull-ga4.py [--property <key>] [--days N]
"""
# CRON-META
# what: Daily GA4 pull -> public.seo_ga4_daily for every in-scope property (SEO platform free layer)
# why: Free source; per-day per-channel sessions/conversions so reports read the store, never the API
# reads: Google Analytics 4 Data API (runReport)
# writes: CC public.seo_ga4_daily (+ seo_api_usage at 0 cost)
# entity: personal
# report:
# schedule: 40 6 * * *
# timezone: Atlantic/Canary
# CRON-META-END
# NOTE (2026-07-23): deploy-ready but NOT deployed -- awaiting Pete's go (standing rule: flag crons first).
import os, sys, json, datetime, importlib.util, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
WINDOW = 3


def _sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=60)
    if r.stderr.strip() and "ERROR" in r.stderr:
        raise RuntimeError(r.stderr.strip()[:200])
    try:
        return json.loads(r.stdout) if r.stdout.strip() else []
    except Exception:
        return []


def _q(s):
    return "$x$" + (s or "") + "$x$"


CONV_EVENTS = {"form_submit", "phone_click", "chat_started", "thank_you", "email_click",
               "generate_lead", "qualify_lead", "close_convert_lead"}


def main():
    args = sys.argv[1:]
    only = args[args.index("--property") + 1] if "--property" in args else None
    days = int(args[args.index("--days") + 1]) if "--days" in args else WINDOW

    spec = importlib.util.spec_from_file_location("ga4", f"{VAULT}/ga4-api.py")
    gm = importlib.util.module_from_spec(spec); spec.loader.exec_module(gm)
    g = gm.GA4API()

    props = _sql("SELECT key, f->>'ga4' AS ga4 FROM property_declarations "
                 "WHERE COALESCE(f->>'seo_scope','in') <> 'out' AND COALESCE(f->>'ga4','') <> ''"
                 + (f" AND key='{only}'" if only else ""))
    if not props:
        print("no in-scope properties with a GA4 id"); return

    total = 0
    for p in props:
        key, pid = p["key"], p["ga4"]
        try:
            # sessions + users per day per channel
            srows = g.run_report(pid, ["date", "sessionDefaultChannelGroup"], ["sessions", "totalUsers"], days=days, limit=2000)
            # conversion events per day per channel
            erows = g.run_report(pid, ["date", "sessionDefaultChannelGroup", "eventName"], ["eventCount"], days=days, limit=5000)
        except Exception as e:
            print(f"  {key}: GA4 pull FAILED -- {str(e)[:120]}")   # loud, never silent
            continue
        agg = {}  # (date, channel) -> {sessions, users, conv}
        for r in srows:
            k = (r["date"], r["sessionDefaultChannelGroup"])
            agg.setdefault(k, {"sessions": 0, "users": 0, "conv": 0})
            agg[k]["sessions"] = int(r["sessions"]); agg[k]["users"] = int(r["totalUsers"])
        for r in erows:
            if r["eventName"] in CONV_EVENTS:
                k = (r["date"], r["sessionDefaultChannelGroup"])
                agg.setdefault(k, {"sessions": 0, "users": 0, "conv": 0})
                agg[k]["conv"] += int(r["eventCount"])
        vals = []
        for (d, ch), v in agg.items():
            dd = f"{d[:4]}-{d[4:6]}-{d[6:8]}"   # GA4 returns YYYYMMDD
            vals.append(f"({_q(key)},'{dd}',{_q(ch)},{v['sessions']},{v['users']},{v['conv']})")
        if vals:
            for i in range(0, len(vals), 500):
                _sql("INSERT INTO public.seo_ga4_daily (property_key,date,channel,sessions,users,conversions) VALUES "
                     + ",".join(vals[i:i + 500]) +
                     " ON CONFLICT (property_key,date,channel) DO UPDATE SET "
                     "sessions=EXCLUDED.sessions, users=EXCLUDED.users, conversions=EXCLUDED.conversions, loaded_at=now()")
        _sql(f"INSERT INTO public.seo_api_usage (service,endpoint,units,cached,http_status,caller,property_key,note) "
             f"VALUES ('ga4','runReport',0,false,200,'seo-pull-ga4',{_q(key)},$x$rows={len(vals)}$x$)")
        print(f"  {key}: {len(vals)} day/channel rows upserted")
        total += len(vals)
    print(f"done -- {total} rows across {len(props)} propert{'y' if len(props)==1 else 'ies'}")


if __name__ == "__main__":
    main()
