#!/usr/bin/env python3
"""
seo-week-snapshot.py -- capture one week's position per mapped keyword, and keep only the last N.

Pete, 1 Aug 2026: "we have four columns with the data in it. Over time, when a new one's added,
the third one drops off." So this is a ROLLING board, not an archive: push a week, the oldest week
beyond the keep-limit is deleted.

WHY IT STORES best/worst AND the weighted average: for the search term "cat and genny training",
impression-weighted GSC daily positions across the 28 days to 30 Jul 2026 ranged 5.6 to 88.0. A
term can look ten places better on noise alone. Storing only the average would make every future
"did it move?" answer unfalsifiable — which is exactly how Pete got five different numbers for one
term in one evening. The weekly range is what lets a movement be tested against the term's own
volatility rather than asserted.

⛔ VOLUME (fixed 1 Aug 2026). This used to write `COALESCE(m.priority,0)` into the `volume`
column -- i.e. it copied `seo_keyword_map.priority`, a number set once when the map was built, and
labelled it "monthly searches". Confirmed on 1 Aug: 482 of 482 rows had priority == volume. When
all 482 keywords were finally pulled live from Ahrefs, 268 had drifted >25% and the store was
overstating total mapped demand by 67% (9,707 stored vs 5,804 live). That is the mechanical reason
Pete kept getting a different demand number every time he asked. Volume is now REFRESHED FROM
AHREFS on every snapshot, and the run FAILS LOUDLY rather than silently falling back to priority --
a stale number wearing a fresh label is worse than no number.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/seo-week-snapshot.py [--property KEY] [--week-ending YYYY-MM-DD]
                                                       [--keep 4] [--dry-run] [--no-volume-refresh]
"""
import os, sys, json, subprocess, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PROP = "sygma-solutions-website"
KEEP = 4


def sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=180)
    if r.returncode != 0:
        raise SystemExit("cc-sql FAILED: " + (r.stderr or r.stdout)[:300])
    return json.loads(r.stdout or "[]")


def q(s):
    return "$x$" + (s or "") + "$x$"



def live_volumes(keywords, prop):
    """Live Ahrefs GB monthly volume for every mapped keyword. Raises rather than guessing.

    NEVER fall back to seo_keyword_map.priority here. Priority is a hand-set ranking field; it was
    being written into the volume column and read back as "searches per month" for weeks."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ahrefs", f"{VAULT}/ahrefs-api.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    api = m.AhrefsAPI(caller="seo-week-snapshot")
    out = {}
    for i in range(0, len(keywords), 100):
        chunk = keywords[i:i + 100]
        for row in api.keywords_overview(chunk, country="gb"):
            out[row["keyword"]] = row.get("volume_monthly")
    got = sum(1 for v in out.values() if v is not None)
    if got < len(keywords) * 0.8:
        raise SystemExit(
            f"FAIL: Ahrefs returned a volume for only {got}/{len(keywords)} keywords. Refusing to "
            f"write the snapshot -- a partial volume refresh silently reintroduces stale numbers.")
    print(f"  volume refreshed from Ahrefs: {got}/{len(keywords)} keywords")
    return out


def main():
    a = sys.argv[1:]
    prop = a[a.index("--property") + 1] if "--property" in a else PROP
    keep = int(a[a.index("--keep") + 1]) if "--keep" in a else KEEP
    dry = "--dry-run" in a
    if "--week-ending" in a:
        wk = a[a.index("--week-ending") + 1]
    else:
        # GSC lags 2-3 days; the latest settled day in the store is the honest week end.
        wk = sql(f"SELECT max(date)::text d FROM seo_gsc_daily WHERE property_key='{prop}'")[0]["d"]
    if not wk:
        raise SystemExit("no GSC data in the store for " + prop)

    rows = sql(f"""
      WITH win AS (
        SELECT lower(g.query) k,
               sum(g.impressions) impr, sum(g.clicks) clicks,
               round((sum(g.position*g.impressions)/nullif(sum(g.impressions),0))::numeric,1) wpos,
               min(g.position)::numeric best, max(g.position)::numeric worst,
               count(DISTINCT g.date) days
        FROM seo_gsc_daily g
        WHERE g.property_key='{prop}' AND g.date > date '{wk}' - 7 AND g.date <= date '{wk}'
        GROUP BY 1),
      rank AS (
        SELECT DISTINCT ON (lower(query)) lower(query) k,
               replace(replace(page,'https://www.sygma-solutions.com',''),
                       'https://sygma-solutions.com','') pg
        FROM seo_gsc_daily
        WHERE property_key='{prop}' AND date > date '{wk}' - 7 AND date <= date '{wk}'
        ORDER BY lower(query), impressions DESC)
      SELECT m.keyword, m.target_page, m.cluster, COALESCE(m.priority,0) vol,  -- REPLACED below by the live Ahrefs volume; see live_volumes()
             w.wpos, COALESCE(w.impr,0) impr, COALESCE(w.clicks,0) clicks,
             round(w.best,1) best, round(w.worst,1) worst, COALESCE(w.days,0) days, r.pg
      FROM seo_keyword_map m
      LEFT JOIN win  w ON w.k = lower(m.keyword)
      LEFT JOIN rank r ON r.k = lower(m.keyword)
      WHERE m.property_key='{prop}' AND m.intent='commercial'""")

    print(f"week ending {wk}: {len(rows)} mapped keywords, "
          f"{sum(1 for r in rows if (r['impr'] or 0) > 0)} with impressions")
    # Overwrite the priority-derived placeholder with a LIVE volume before anything is written.
    if "--no-volume-refresh" not in a:
        vols = live_volumes([r["keyword"] for r in rows], prop)
        for r in rows:
            r["vol"] = vols.get(r["keyword"])
    else:
        print("  ⚠ --no-volume-refresh: `volume` will carry seo_keyword_map.priority, which is NOT "
              "a monthly search figure. Do not quote a demand total off this run.")

    if dry:
        print("dry run - nothing written")
        return

    vals = []
    for r in rows:
        def n(v):
            return "NULL" if v is None else str(v)
        vals.append(f"('{prop}','{wk}',{q(r['keyword'])},{q(r['target_page'])},{q(r['cluster'])},"
                    f"{n(r['vol'])},{n(r['wpos'])},{n(r['impr'])},{n(r['clicks'])},"
                    f"{n(r['best'])},{n(r['worst'])},{n(r['days'])},{q(r['pg'])})")
    for i in range(0, len(vals), 200):
        sql("INSERT INTO public.seo_term_weekly (property_key,week_ending,keyword,target_page,cluster,"
            "volume,wpos,impressions,clicks,best_pos,worst_pos,days_with_data,ranking_page) VALUES "
            + ",".join(vals[i:i+200]) +
            " ON CONFLICT (property_key,week_ending,keyword) DO UPDATE SET "
            "target_page=EXCLUDED.target_page, cluster=EXCLUDED.cluster, volume=EXCLUDED.volume, "
            "wpos=EXCLUDED.wpos, impressions=EXCLUDED.impressions, clicks=EXCLUDED.clicks, "
            "best_pos=EXCLUDED.best_pos, worst_pos=EXCLUDED.worst_pos, "
            "days_with_data=EXCLUDED.days_with_data, ranking_page=EXCLUDED.ranking_page, "
            "captured_at=now()")

    # ROLL: keep only the most recent `keep` weeks for this property.
    weeks = [r["w"] for r in sql(f"SELECT DISTINCT week_ending::text w FROM public.seo_term_weekly "
                                 f"WHERE property_key='{prop}' ORDER BY w DESC")]
    drop = weeks[keep:]
    for d in drop:
        sql(f"DELETE FROM public.seo_term_weekly WHERE property_key='{prop}' AND week_ending='{d}'")
    print(f"kept {weeks[:keep]}" + (f"  ·  dropped {drop}" if drop else "  ·  nothing to drop yet"))


if __name__ == "__main__":
    main()
