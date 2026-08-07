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
# CRON-META
# what: Daily GSC pull -> public.seo_gsc_daily for every in-scope property (SEO platform free layer)
# why: Free source; builds real rank/traffic history so reports read the store, never a paid API
# reads: Google Search Console (searchAnalytics/query)
# writes: CC public.seo_gsc_daily (+ seo_api_usage at 0 cost)
# entity: personal
# report:
# secrets: GOOGLE_SA_JSON, SUPABASE_TOKEN
#   SUPABASE_TOKEN is needed by cc-sql.py itself (every write goes through it). Missing it
#   produced FileNotFoundError on Library/processes/secrets/supabase-token, 24 Jul 2026.
# note: GOOGLE_SA_JSON is REQUIRED -- it materialises google-seo-service-account.json, which the
#   GSC searchAnalytics helper reads. Both crons CRASHED on Railway from 23 Jul until 24 Jul because this line
#   was missing: they ran fine locally (the file is on disk) and died in the cron with no credentials.
# schedule: 30 6 * * *
# timezone: Atlantic/Canary
# CRON-META-END
# NOTE (2026-07-23): deploy-ready but NOT deployed -- awaiting Pete's go (standing rule: flag crons first).
import os, sys, json, datetime, importlib.util, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
WINDOW = 5          # rolling days pulled each run (covers 2-3d GSC lag + restatement)
FINAL_AFTER = 3     # a date is final once older than this many days (GSC stops restating)


def _sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=60)
    # ⚠ NEVER swallow a query failure into an empty list. This used to `except Exception: return []`,
    # so on Railway a failing cc-sql call produced "no in-scope properties with a GSC id" and the cron
    # exited SUCCESS having done nothing (24 Jul 2026). A silent no-op reported as a clean run is the
    # worst failure mode this platform can have: the store quietly stops filling and every report
    # downstream keeps answering from stale data.
    if r.returncode != 0 or (r.stderr.strip() and "ERROR" in r.stderr):
        raise RuntimeError(f"cc-sql FAILED (rc={r.returncode}): {(r.stderr or r.stdout).strip()[:400]}")
    if not r.stdout.strip():
        return []
    try:
        return json.loads(r.stdout)
    except Exception as e:
        raise RuntimeError(f"cc-sql returned unparseable output: {e} :: {r.stdout[:300]!r}")


def _q(s):
    return "$x$" + (s or "") + "$x$"


USAGE = ("Usage: VAULT=/tmp/pbs python3 seo-pull-gsc.py [--property <key>] [--days N]\n"
         "  --property <key>  one property (default: EVERY in-scope property)\n"
         "  --days N          how far back to pull (default: the %d-day rolling window)\n"
         "  -h / --help       this message\n" % WINDOW)


def main():
    args = sys.argv[1:]
    # ⚠ An UNRECOGNISED flag used to be silently ignored, so `--help` ran a full pull across all
    # 9 in-scope properties instead of printing usage (hit 7 Aug 2026 during a Lanzarote Lates
    # report). Anything that spends this much time on the estate must never be started by a typo.
    if "-h" in args or "--help" in args:
        print(USAGE); return
    known = {"--property", "--days"}
    flags = [a for a in args if a.startswith("-")]
    unknown = [f for f in flags if f not in known]
    if unknown:
        raise SystemExit(f"FAIL: unrecognised flag(s) {' '.join(unknown)}.\n{USAGE}")
    for f in known:
        if f in args and (args.index(f) + 1 >= len(args) or args[args.index(f) + 1].startswith("-")):
            raise SystemExit(f"FAIL: {f} needs a value.\n{USAGE}")
    only = args[args.index("--property") + 1] if "--property" in args else None
    days = int(args[args.index("--days") + 1]) if "--days" in args else WINDOW

    spec = importlib.util.spec_from_file_location("gsc", f"{VAULT}/gsc-api.py")
    gm = importlib.util.module_from_spec(spec); spec.loader.exec_module(gm)
    g = gm.GSCAPI()

    props = _sql("SELECT key, f->>'gsc' AS gsc FROM property_declarations "
                 "WHERE COALESCE(f->>'seo_scope','in') <> 'out' AND COALESCE(f->>'gsc','') <> ''"
                 + (f" AND key='{only}'" if only else ""))
    if not props:
        raise SystemExit("FAIL: zero in-scope properties with a GSC id. There are 8 in the CC, so this\n  means the query or the DB connection is broken -- NOT that there is nothing to pull.")

    today = datetime.date.today()
    start = (today - datetime.timedelta(days=days)).isoformat()
    end = (today - datetime.timedelta(days=1)).isoformat()
    total = 0
    total_pages = 0
    for p in props:
        key, site = p["key"], p["gsc"]
        try:
            # query_all, NOT query(limit=N): a single capped request returns the top rows across
            # the WHOLE window, starving older dates (23 Jul 2026: a 120-day pull gave May 95 rows
            # and June 4,744). Any trend built on that compares a month against a scrap.
            rows = g.query_all(site, ["date", "query", "page"], date_range=(start, end))
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
        # PAGE-LEVEL pass -> public.seo_gsc_page_daily.
        # WHY THIS EXISTS (1 Aug 2026): the [date,query,page] rows above are QUERY-grain, and Google
        # withholds low-volume queries for privacy. Summing them therefore loses most clicks. Measured
        # on sygma-solutions-website, same 28-day window: query-grain sum = 125 clicks, GSC's own
        # page-level total = 464. Every click figure any report produced was understated by ~73%.
        # Clicks must be read from THIS table; per-term work still reads seo_gsc_daily (valid for
        # position/impressions per term, never for total clicks).
        try:
            prows = g.query_all(site, ["date", "page"], date_range=(start, end))
        except Exception as e:
            print(f"  {key}: GSC PAGE-level pull FAILED -- {str(e)[:120]}")   # loud, never silent
            prows = []
        pvals = []
        for r in prows:
            d, pg = r["keys"]
            pvals.append(f"({_q(key)},'{d}',{_q(pg)},{int(r['clicks'])},{int(r['impressions'])},{round(r['position'],2)})")
        if pvals:
            for i in range(0, len(pvals), 500):
                chunk = ",".join(pvals[i:i + 500])
                _sql("INSERT INTO public.seo_gsc_page_daily (property_key,date,page,clicks,impressions,position) VALUES "
                     + chunk +
                     " ON CONFLICT (property_key,date,page) DO UPDATE SET "
                     "clicks=EXCLUDED.clicks, impressions=EXCLUDED.impressions, position=EXCLUDED.position, "
                     "final=false, loaded_at=now()")
        # finalise settled dates
        _sql(f"UPDATE public.seo_gsc_daily SET final=true WHERE property_key={_q(key)} "
             f"AND date < current_date - {FINAL_AFTER} AND final=false")
        _sql(f"UPDATE public.seo_gsc_page_daily SET final=true WHERE property_key={_q(key)} "
             f"AND date < current_date - {FINAL_AFTER} AND final=false")
        # free source -> log at 0 cost for a complete ledger
        _sql(f"INSERT INTO public.seo_api_usage (service,endpoint,units,cached,http_status,caller,property_key,note) "
             f"VALUES ('gsc','searchanalytics/query',0,false,200,'seo-pull-gsc',{_q(key)},$x$rows={len(rows)}$x$)")
        print(f"  {key}: {len(rows)} query-rows + {len(prows)} page-rows upserted")
        total += len(rows)
        # ⚠ The total used to count query-rows ONLY while the per-property line showed both, so a
        # backfill reported "70555 rows" when it had actually written 70555 + 4529. Page rows are
        # the CLICK source (see the note at the page-level pass) -- never leave them out of a count.
        total_pages += len(prows)
    print(f"done -- {total} query-rows + {total_pages} page-rows "
          f"across {len(props)} propert{'y' if len(props)==1 else 'ies'}")


if __name__ == "__main__":
    main()
