#!/usr/bin/env python3
"""
seo-report.py -- config-driven SEO report from the STORE (SEO platform, phase 3).

Reads ONLY the CC store (seo_gsc_daily + seo_ga4_daily + seo_property_config) -- never a paid API. GSC is
the source of truth for rank/traffic. The commercial-intent filter is enforced HERE, in code, so a vanity
term can never reach a report (the 23 Jul failure). Works for ANY property from its config row.

For each property it prints, commercial-intent terms ONLY:
  - a before/after GSC comparison over two equal windows (clicks are the measure, not impressions)
  - the per-term movers, with vanity terms excluded and money-page terms flagged
  - GA4 organic-vs-paid conversion split (never a blended total)

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/seo-report.py <property_key> [--days 13]
  VAULT=/tmp/pbs python3 /tmp/pbs/seo-report.py --list          # in-scope properties with config
"""
import os, sys, json, datetime, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")


def _sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=60)
    if r.stderr.strip() and "ERROR" in r.stderr:
        raise RuntimeError(r.stderr.strip()[:200])
    return json.loads(r.stdout) if r.stdout.strip() else []


def _cfg(key):
    rows = _sql(f"SELECT intent_commercial_patterns AS comm, intent_vanity_terms AS vanity, "
                f"money_pages, ads_running, reporting_cadence FROM seo_property_config WHERE property_key=$x${key}$x$")
    return rows[0] if rows else None


def is_commercial(term, comm, vanity):
    t = (term or "").lower()
    if any(v.lower() == t or v.lower() in t for v in (vanity or [])):
        return False   # explicit vanity always loses, even if it also contains a commercial word
    return any(p.lower() in t for p in (comm or []))


def window_agg(key, start, end, comm, vanity):
    rows = _sql(f"SELECT query, clicks, impressions, position FROM seo_gsc_daily "
                f"WHERE property_key=$x${key}$x$ AND date BETWEEN '{start}' AND '{end}'")
    clicks = impr = 0; posw = 0.0; terms = set(); per = {}
    for r in rows:
        if not is_commercial(r["query"], comm, vanity):
            continue
        c, i, p = int(r["clicks"] or 0), int(r["impressions"] or 0), float(r["position"] or 0)
        clicks += c; impr += i; posw += (p * i); terms.add(r["query"])
        d = per.setdefault(r["query"], {"c": 0, "i": 0, "pw": 0.0})
        d["c"] += c; d["i"] += i; d["pw"] += p * i
    avg = (posw / impr) if impr else 0
    per_pos = {q: (v["pw"] / v["i"] if v["i"] else 0, v["c"], v["i"]) for q, v in per.items()}
    return {"clicks": clicks, "impr": impr, "terms": len(terms), "avg": avg, "per": per_pos}


def report(key, days=13):
    cfg = _cfg(key)
    if not cfg:
        print(f"no seo_property_config row for {key} -- seed it first."); return
    comm, vanity, money = cfg["comm"] or [], cfg["vanity"] or [], cfg["money_pages"] or []
    today = datetime.date.today()
    # two equal, adjacent windows ending at the latest settled GSC date
    latest = _sql(f"SELECT max(date) AS d FROM seo_gsc_daily WHERE property_key=$x${key}$x$")
    if not latest or not latest[0]["d"]:
        print(f"no GSC data stored for {key} yet -- run seo-pull-gsc.py --property {key}"); return
    end2 = datetime.date.fromisoformat(latest[0]["d"])
    start2 = end2 - datetime.timedelta(days=days - 1)
    end1 = start2 - datetime.timedelta(days=1)
    start1 = end1 - datetime.timedelta(days=days - 1)
    w1 = window_agg(key, start1.isoformat(), end1.isoformat(), comm, vanity)
    w2 = window_agg(key, start2.isoformat(), end2.isoformat(), comm, vanity)

    print(f"\n=== {key} -- COMMERCIAL-INTENT terms only ({days}-day windows, GSC) ===")
    print(f"{'':22}{'prev':>10}{'latest':>10}")
    print(f"{'  clicks':22}{w1['clicks']:>10}{w2['clicks']:>10}   <- the measure")
    print(f"{'  impressions':22}{w1['impr']:>10}{w2['impr']:>10}")
    print(f"{'  terms ranking':22}{w1['terms']:>10}{w2['terms']:>10}")
    print(f"{'  avg position':22}{w1['avg']:>10.1f}{w2['avg']:>10.1f}   (lower = better)")
    # movers on money terms
    movers = []
    for q, (p2, c2, i2) in w2["per"].items():
        p1 = w1["per"].get(q, (None,))[0]
        if p1 and p2 and max(i2, w1["per"].get(q, (0, 0, 0))[2]) >= 20:
            movers.append((p1 - p2, q, p1, p2, "money" if any(m in q for m in []) else ""))
    movers.sort(reverse=True)
    if movers:
        print("\n  biggest commercial position moves (>=20 impr, - = worse):")
        for d, q, p1, p2, _ in movers[:4] + movers[-4:]:
            print(f"    {d:+6.1f}  {q[:44]:46} {p1:.1f}->{p2:.1f}")
    # GA4 organic vs paid (never blended)
    ga = _sql(f"SELECT channel, sum(sessions) AS s, sum(conversions) AS c FROM seo_ga4_daily "
              f"WHERE property_key=$x${key}$x$ AND date >= '{start2}' GROUP BY channel ORDER BY sum(conversions) DESC")
    if ga:
        print(f"\n  GA4 conversions by channel (last {days}d) -- organic vs paid, never blended:")
        for r in ga:
            if (r["c"] or 0) > 0:
                print(f"    {str(r['channel'])[:22]:24} {r['s'] or 0:>5} sess  {r['c'] or 0:>4} conv")
    if not cfg["ads_running"]:
        print("  (no ads on this property)")


def trend(key, term=None, page=None, by="month"):
    """Month-by-month IMPRESSION-WEIGHTED position for one term (or one page). Use this instead of
    writing ad-hoc SQL -- that is how today's wrong answers happened.

    ⚠ NEVER use a plain avg(position) on seo_gsc_daily (23 Jul 2026, three wrong answers to Pete).
    Each row is one (date, query, PAGE), so a stray page picking up 1 impression at position 88
    counts exactly as much as the real page with 96 impressions at 16.4. On the cat-and-genny head
    term a plain average read 22.0 for July; impression-weighted it was 16.1, and the main page on
    its own was 15.6. The plain average also invented two "collapse" weeks that never happened.
    Weight by impressions, always, and say which page you are quoting.
    """
    if not term and not page:
        print("give --term or --page"); return
    where = f"property_key=$x${key}$x$"
    if term:
        where += f" AND query=$x${term}$x$"
    if page:
        where += f" AND page LIKE $x$%{page}%$x$"
    fmt = "'YYYY-MM'" if by == "month" else "'YYYY-MM-DD'"
    grp = "date" if by == "month" else "date_trunc('week',date)"
    rows = _sql(f"SELECT to_char({grp},{fmt}) AS p, "
                f"round((sum(position*impressions)/NULLIF(sum(impressions),0))::numeric,1) AS wpos, "
                f"round(avg(position)::numeric,1) AS plain, sum(impressions) AS impr, "
                f"sum(clicks) AS clk, count(DISTINCT page) AS pages "
                f"FROM seo_gsc_daily WHERE {where} GROUP BY 1 ORDER BY 1")
    if not rows:
        print(f"no stored rows for {term or page} on {key}"); return
    print(f"{term or page} -- impression-WEIGHTED position (the plain average is shown only to "
          f"prove why it must not be used)")
    print(f"  {'period':10}{'WEIGHTED':>10}{'(plain)':>10}{'impr':>7}{'clicks':>8}{'pages':>7}")
    for r in rows:
        print(f"  {r['p']:10}{str(r['wpos']):>10}{str(r['plain']):>10}"
              f"{r['impr']:>7}{r['clk']:>8}{r['pages']:>7}")
    print("  pages>1 means several of our URLs shared the term that period -- quote the main page "
          "separately with --page before drawing any conclusion.")


def main():
    a = sys.argv[1:]
    if not a or a[0] == "--list":
        for r in _sql("SELECT property_key, reporting_cadence FROM seo_property_config ORDER BY property_key"):
            print(f"  {r['property_key']:34} cadence={r['reporting_cadence']}")
        return
    if "--term" in a or "--page" in a:
        trend(a[0],
              term=a[a.index("--term") + 1] if "--term" in a else None,
              page=a[a.index("--page") + 1] if "--page" in a else None,
              by=("week" if "--weekly" in a else "month"))
        return
    days = int(a[a.index("--days") + 1]) if "--days" in a else 13
    report(a[0], days)


if __name__ == "__main__":
    main()
