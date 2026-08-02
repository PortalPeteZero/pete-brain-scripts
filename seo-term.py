#!/usr/bin/env python3
"""
seo-term.py -- THE answer to "how are we doing for <term>". One command, one shape, every time.

WHY THIS EXISTS (1 Aug 2026). Pete asked how one term was doing and, across a single evening, got
five different numbers from me: "3rd", "5", "12.9", "13.6", "14.7". The DATA never changed. I kept
picking a different source and a different framing each time. His conclusion, and he was right:
"i coudl ask you 5 times and get 5 different answers".

So the answer stops being something I compose. It is this script's output, verbatim. Ask it five
times, get the same five answers -- because there is only one.

THE RULES IT ENFORCES, so they cannot be re-litigated per conversation:
  · GSC is THE answer. Impression-weighted, Google UK, over a stated window. Ahrefs is a single
    desktop snapshot from one location on one date and is printed as CONTEXT, never as the headline.
    (Pete's own standing rule: judge on GSC, never Ahrefs alone.)
  · The window is printed as real dates. No "recently", no "at the moment".
  · Daily spread is printed, because a weighted average of 13.6 on a term that bounces 5.6 to 17.7
    is not the same story as a steady 13.6, and quoting the average alone hides that.
  · The term is echoed on every line that carries a number.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/seo-term.py "cat and genny training" [--days 28] [--property KEY]
"""
import os, sys, json, subprocess, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
DEFAULT_PROP = "sygma-solutions-website"


def sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=90)
    if r.returncode != 0:
        raise SystemExit("cc-sql FAILED: " + (r.stderr or r.stdout)[:300])
    return json.loads(r.stdout or "[]")


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        raise SystemExit(__doc__)
    term = args[0].strip().lower()
    days = int(args[args.index("--days") + 1]) if "--days" in args else 28
    prop = args[args.index("--property") + 1] if "--property" in args else DEFAULT_PROP
    t = term.replace("'", "''")

    mapped = sql(f"SELECT keyword, target_page, cluster, volume AS vol FROM seo_keyword_map "
                 f"WHERE property_key='{prop}' AND lower(keyword)='{t}'")
    cur = sql(f"""SELECT min(date)::text lo, max(date)::text hi, sum(impressions) impr, sum(clicks) clicks,
                   round((sum(position*impressions)/nullif(sum(impressions),0))::numeric,1) wpos
                  FROM seo_gsc_daily WHERE property_key='{prop}' AND lower(query)='{t}'
                    AND date > current_date - {days}""")[0]
    prev = sql(f"""SELECT round((sum(position*impressions)/nullif(sum(impressions),0))::numeric,1) wpos,
                          sum(impressions) impr
                   FROM seo_gsc_daily WHERE property_key='{prop}' AND lower(query)='{t}'
                     AND date > current_date - {days*2} AND date <= current_date - {days}""")[0]
    daily = sql(f"""SELECT date::text d, round(position::numeric,1) p, impressions i
                    FROM seo_gsc_daily WHERE property_key='{prop}' AND lower(query)='{t}'
                      AND date > current_date - {days} ORDER BY date""")
    pages = sql(f"""SELECT replace(replace(page,'https://www.sygma-solutions.com',''),
                                   'https://sygma-solutions.com','') pg,
                           sum(impressions) impr
                    FROM seo_gsc_daily WHERE property_key='{prop}' AND lower(query)='{t}'
                      AND date > current_date - {days} GROUP BY 1 ORDER BY 2 DESC LIMIT 3""")

    print(f'\n=== "{term}" ===  ({prop})')
    if not mapped:
        print(f'  ⚠ "{term}" is NOT in seo_keyword_map. It is not something this property has decided')
        print( '    to target, so there is no "how are we doing" to report. Add it to the map first.')
    else:
        m = mapped[0]
        print(f'  in the map: target {m["target_page"]}  ·  {m["cluster"]}  ·  {m["vol"]}/mo searches (Ahrefs)')

    if not cur["impr"]:
        print(f'  no GSC impressions for "{term}" in the last {days} days. Nothing to report -- and that')
        print( '  is the answer, not a reason to go looking for a different number somewhere else.')
        return

    print(f'\n  THE ANSWER  ({cur["lo"]} to {cur["hi"]}, impression-weighted, Google UK, GSC)')
    print(f'    "{term}"  position {cur["wpos"]}   ·  {cur["impr"]} impressions  ·  {cur["clicks"]} clicks')
    if prev["wpos"] is not None:
        delta = float(prev["wpos"]) - float(cur["wpos"])
        arrow = "better" if delta > 0 else ("worse" if delta < 0 else "flat")
        print(f'    previous {days} days: {prev["wpos"]} on {prev["impr"]} impressions  ->  {abs(delta):.1f} {arrow}')
    else:
        print(f'    previous {days} days: no data -- cannot state a movement')

    ps = [float(r["p"]) for r in daily]
    if ps:
        print(f'\n  SPREAD (why the single number can mislead)')
        print(f'    "{term}" ranged {min(ps)} to {max(ps)} across {len(ps)} days with data.')
        best = [r for r in daily if float(r["p"]) == min(ps)][0]
        worst = [r for r in daily if float(r["p"]) == max(ps)][0]
        print(f'    best {best["d"]} at {best["p"]}  ·  worst {worst["d"]} at {worst["p"]}')
        if max(ps) - min(ps) >= 5:
            print(f'    That is a {max(ps)-min(ps):.1f}-place swing: any single-day reading of "{term}"')
            print(f'    (including any Ahrefs snapshot) can be anywhere in that band and prove nothing.')

    if pages:
        print(f'\n  WHICH PAGE ANSWERS "{term}"')
        for r in pages:
            flag = ""
            if mapped and r["pg"] != mapped[0]["target_page"]:
                flag = f'   <- NOT the assigned page ({mapped[0]["target_page"]})'
            print(f'    {r["pg"]:<52} {r["impr"]} impr{flag}')

    print(f'\n  (Ahrefs is deliberately NOT the headline here: it is one desktop snapshot from one')
    print(f'   location on one date. For "{term}" use it for competitors and volume, not for rank.)')


if __name__ == "__main__":
    main()
