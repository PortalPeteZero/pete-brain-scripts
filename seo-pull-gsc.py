#!/usr/bin/env python3
"""
seo-pull-gsc.py -- daily GSC pull into public.seo_gsc_daily (SEO platform, phase 2).

FREE source (GSC is unlimited), so this is the pattern that gets a daily cron. For every in-scope
property (property_declarations where seo_scope <> 'out' AND f->>'gsc' is set) it pulls a rolling window
of query+page rows and UPSERTs them, so GSC's ~3-day restatement is absorbed idempotently. Rows older
than the restatement window are marked final=true (the immutability signal the cache/report trusts).

Reports read seo_gsc_daily; they never call the GSC API. This turns the 7-day keyhole into real history.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/seo-pull-gsc.py [--property <key>] [--days N]
"""
import os, sys, json, datetime, importlib.util, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
WINDOW = 5          # rolling days pulled each run (covers 2-3d GSC lag + restatement)
FINAL_AFTER = 3     # a date is final once older than this many days (GSC stops restating)


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


def main():
    args = sys.argv[1:]
    only = args[args.index("--property") + 1] if "--property" in args else None
    days = int(args[args.index("--days") + 1]) if "--days" in args else WINDOW

    spec = importlib.util.spec_from_file_location("gsc", f"{VAULT}/gsc-api.py")
    gm = importlib.util.module_from_spec(spec); spec.loader.exec_module(gm)
    g = gm.GSCAPI()

    props = _sql("SELECT key, f->>'gsc' AS gsc FROM property_declarations "
                 "WHERE COALESCE(f->>'seo_scope','in') <> 'out' AND COALESCE(f->>'gsc','') <> ''"
                 + (f" AND key='{only}'" if only else ""))
    if not props:
        print("no in-scope properties with a GSC id"); return

    today = datetime.date.today()
    start = (today - datetime.timedelta(days=days)).isoformat()
    end = (today - datetime.timedelta(days=1)).isoformat()
    total = 0
    for p in props:
        key, site = p["key"], p["gsc"]
        try:
            rows = g.query(site, ["date", "query", "page"], date_range=(start, end), limit=5000)
        except Exception as e:
            print(f"  {key}: GSC pull FAILED -- {str(e)[:120]}")   # loud, never silent
            continue
        # batch upsert
        vals = []
        for r in rows:
            d, qy, pg = r["keys"]
            vals.append(f"({_q(key)},'{d}',{_q(qy)},{_q(pg)},{int(r['clicks'])},{int(r['impressions'])},{round(r['position'],2)})")
        if vals:
            for i in range(0, len(vals), 500):
                chunk = ",".join(vals[i:i + 500])
                _sql("INSERT INTO public.seo_gsc_daily (property_key,date,query,page,clicks,impressions,position) VALUES "
                     + chunk +
                     " ON CONFLICT (property_key,date,query,page) DO UPDATE SET "
                     "clicks=EXCLUDED.clicks, impressions=EXCLUDED.impressions, position=EXCLUDED.position, "
                     "final=false, loaded_at=now()")
        # finalise settled dates
        _sql(f"UPDATE public.seo_gsc_daily SET final=true WHERE property_key={_q(key)} "
             f"AND date < current_date - {FINAL_AFTER} AND final=false")
        # free source -> log at 0 cost for a complete ledger
        _sql(f"INSERT INTO public.seo_api_usage (service,endpoint,units,cached,http_status,caller,property_key,note) "
             f"VALUES ('gsc','searchanalytics/query',0,false,200,'seo-pull-gsc',{_q(key)},$x$rows={len(rows)}$x$)")
        print(f"  {key}: {len(rows)} rows upserted")
        total += len(rows)
    print(f"done -- {total} rows across {len(props)} propert{'y' if len(props)==1 else 'ies'}")


if __name__ == "__main__":
    main()
