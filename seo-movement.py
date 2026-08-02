#!/usr/bin/env python3
"""
seo-movement.py -- did a term actually MOVE, or is it inside its own noise?

Pete, 1 Aug 2026: "the key work is getting the pages to rank higher for key terms." The only honest
test of that work is whether a term moved. Two things had to be got right before the answer was
worth anything, and BOTH were wrong in the first version:

1. THE WINDOW. Week-to-week comparison at Sygma's impression volumes (many terms see 5-30
   impressions a week) called 2 of 133 terms real and everything else noise. Pete: "your tagging
   nearly every one as either noise or insuffienct data so not quite sure the use of this at all."
   He was right -- a test that says "nothing happened" about everything is useless. Over 28-day
   windows the same method calls 31 of 140 real. The board still SHOWS weekly columns; the VERDICT
   uses 28 days.

2. THE BAND. It was max(position)-min(position) over raw seo_gsc_daily rows. That table is
   (date, query, PAGE) grain, so the spread was being set by OTHER pages of ours ranking badly for
   the term, on single impressions. "eusr cat 1 training" read a 67-place band off two 1-impression
   rows at positions 72 and 54. That is the same fault that got avg(position) banned. The band is
   now the daily impression-WEIGHTED position (aggregated across pages first), and days under 3
   impressions are excluded entirely.

THE TEST: a change counts as REAL only when it exceeds half the term's own band. Fewer than 3 solid
days, or under 20 impressions in either window, is INSUFFICIENT and never scored. Deliberately
conservative -- a test that calls noise a win makes every future claim unfalsifiable, which is the
failure this whole exercise was about.

Writes public.seo_term_movement (one row per keyword, latest run) so the CC board reads a verdict
rather than recomputing it.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/seo-movement.py [--property KEY] [--days 28] [--min-impr 20] [--write]
"""
import os, sys, json, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PROP = "sygma-solutions-website"


def sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=180)
    if r.returncode != 0:
        raise SystemExit("cc-sql FAILED: " + (r.stderr or r.stdout)[:300])
    return json.loads(r.stdout or "[]")


def rows_for(prop, days, min_impr):
    return sql(f"""
      WITH daily AS (
        SELECT lower(query) k, date, sum(impressions) impr,
               sum(position*impressions)/nullif(sum(impressions),0) wp
        FROM seo_gsc_daily
        WHERE property_key='{prop}' AND date > current_date - {days*2}
        GROUP BY 1,2),
      cur AS (
        SELECT k, sum(impr) i, sum(wp*impr)/nullif(sum(impr),0) w,
               count(*) FILTER (WHERE impr >= 3) solid,
               max(wp) FILTER (WHERE impr >= 3) hi,
               min(wp) FILTER (WHERE impr >= 3) lo
        FROM daily WHERE date > current_date - {days} GROUP BY 1),
      prv AS (
        SELECT k, sum(impr) i, sum(wp*impr)/nullif(sum(impr),0) w
        FROM daily WHERE date <= current_date - {days} GROUP BY 1)
      SELECT m.keyword, m.volume AS vol, m.cluster,
             round(cur.w::numeric,1) wnow, round(prv.w::numeric,1) wprev,
             cur.i inow, prv.i iprev, cur.solid,
             round((cur.hi-cur.lo)::numeric,1) band
      FROM seo_keyword_map m
      LEFT JOIN cur ON cur.k = lower(m.keyword)
      LEFT JOIN prv ON prv.k = lower(m.keyword)
      WHERE m.property_key='{prop}' AND m.intent='commercial'""")


def _f(v, d=0.0):
    """cc-sql returns numerics as STRINGS. Coerce, or every comparison silently misbehaves."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# A term with real monthly demand that Google barely SHOWS us for is not "insufficient data" --
# it is a finding. Pete, 1 Aug 2026: "so assuming these 351 will always be insufficent?" Splitting
# that bucket showed 15 terms with 30+ monthly searches drawing under a quarter of that in
# impressions: "safe digging training" has 70/mo and drew 3 impressions in 28 days at position 36.7.
# The term is not too small to judge; we are too far down to be seen. Calling that INSUFFICIENT
# buried it next to genuinely tiny terms like "osca training" (50/mo, position 4.8), which is a
# completely different situation and needs no work.
INVISIBLE_MIN_VOL = 30      # below this the term really is too small to reason about
INVISIBLE_SHARE  = 0.25     # impressions under this share of monthly volume = we are not being shown


def judge(r, min_impr):
    vol, impr = _f(r["vol"]), _f(r["inow"])
    if vol >= INVISIBLE_MIN_VOL and impr < vol * INVISIBLE_SHARE:
        return ("INVISIBLE", None)
    if r["wnow"] is None or r["wprev"] is None:
        return ("LOW TRAFFIC", None)
    if impr < min_impr or _f(r["iprev"]) < min_impr or _f(r["solid"]) < 3:
        return ("LOW TRAFFIC", None)
    band = _f(r["band"])
    delta = _f(r["wprev"]) - _f(r["wnow"])          # + = better
    if abs(delta) <= band / 2:
        return ("NOISE", delta)
    return (("IMPROVED" if delta > 0 else "DECLINED"), delta)


def main():
    a = sys.argv[1:]
    prop = a[a.index("--property") + 1] if "--property" in a else PROP
    days = int(a[a.index("--days") + 1]) if "--days" in a else 28
    mi = int(a[a.index("--min-impr") + 1]) if "--min-impr" in a else 20
    rows = rows_for(prop, days, mi)

    out = []
    for r in rows:
        v, d = judge(r, mi)
        out.append((v, d, r))
    order = {"IMPROVED": 0, "DECLINED": 1, "INVISIBLE": 2, "NOISE": 3, "LOW TRAFFIC": 4}
    out.sort(key=lambda x: (order.get(x[0], 9), -_f(x[2]["vol"])))

    print(f"\nMOVEMENT  ·  last {days} days vs the {days} before  ·  {prop}")
    print(f"  Real only if the change beats half the term's own daily spread (days under 3 impressions excluded).")
    print(f"\n  {'term':<42}{'vol':>5}{'now':>7}{'was':>7}{'move':>7}{'band':>7}  verdict")
    for v, d, r in out[:35]:
        if v == "LOW TRAFFIC":
            continue
        print(f"  {r['keyword'][:41]:<42}{r['vol'] or 0:>5}{str(r['wnow']):>7}{str(r['wprev']):>7}"
              f"{(f'{d:+.1f}' if d is not None else '-'):>7}{str(r['band'] or '-'):>7}  {v}")
    c = {}
    for v, _, _ in out:
        c[v] = c.get(v, 0) + 1
    print("\n  " + " | ".join(f"{k} {v}" for k, v in sorted(c.items())))

    if "--write" in a:
        sql(f"DELETE FROM public.seo_term_movement WHERE property_key='{prop}'")
        vals = []
        for v, d, r in out:
            def n(x):
                return "NULL" if x is None else str(x)
            kw = r["keyword"].replace("'", "''")
            vals.append(f"('{prop}','{kw}',current_date,{days},{n(r['wnow'])},{n(r['wprev'])},"
                        f"{n(r['inow'])},{n(r['iprev'])},{n(r['band'])},"
                        f"{'NULL' if d is None else round(d,1)},'{v}')")
        for i in range(0, len(vals), 200):
            sql("INSERT INTO public.seo_term_movement (property_key,keyword,as_of,window_days,"
                "wpos_now,wpos_prev,impr_now,impr_prev,band,delta,verdict) VALUES "
                + ",".join(vals[i:i+200]))
        print(f"  written to seo_term_movement ({len(vals)} rows)")


if __name__ == "__main__":
    main()
