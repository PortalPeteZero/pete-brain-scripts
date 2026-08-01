#!/usr/bin/env python3
"""
seo-section-report.py -- the Sygma section report (Avoidance | Utility Mapping).

Pete's spec, 1 Aug 2026: "i can run an avoidance report and mapping report ... you should view the
report 2 ways to show us how we are doing but also to guide us on what needs work."

TWO VIEWS, always in this order, same shape every run:
  1. HOW WE ARE DOING -- every keyword in the section, its position now vs the previous equal
     window, then the page roll-up, then the section total.
  2. WHAT NEEDS WORK  -- the same keywords ranked by opportunity, using the rules Pete signed off:
       striking distance (in the map, real impressions, position 4-20)  <- closest to money
       wrong page ranking (ranks via a page it is not assigned to)
       invisible (in the map, near-zero impressions)
       slipped (worse than the previous equal window)

THE SSOT IS public.seo_keyword_map. This script REFUSES to report a term that is not in it, and
REFUSES to silently drop one that is -- both are printed. Pete, 1 Aug 2026: "we decide, ahref
follows us". Ahrefs no longer decides what is measured; the map does.

CLICKS COME FROM seo_gsc_page_daily, NEVER from summing seo_gsc_daily. The latter is query-grain and
Google withholds low-volume queries, so summing it under-reported site clicks by ~73% (125 vs 464,
measured 1 Aug 2026). Per-term position/impressions still come from seo_gsc_daily, which is valid
for that and only that.

Usage:
  VAULT=/tmp/pbs python3 seo-section-report.py avoidance|mapping|both [--days 28] [--json]
                                              [--winnability N]   # PAID: SERP check on the top N opportunities
"""
import os, sys, json, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PROP = "sygma-solutions-website"
SECTIONS = {"avoidance": "Avoidance", "mapping": "Utility Mapping"}

# VOLUME FLOOR -- monthly searches below this cannot be presented as an opportunity.
# WHY (1 Aug 2026): "eusr superuser training" was recommended to Pete as a priority because it showed
# 376 impressions at position 4.5. Divided back out that is ~2 searches a DAY, and Ahrefs puts the term
# at 10/month -- it is a course Sygma invented, so almost nobody searches for it by name. Going 5 -> 1
# on it would win a handful of clicks a month. Pete caught it: "i cant see people searching that much
# for eusr superuser, its a very specific course we designed".
# THE TRAP: a 90-day impression TOTAL flatters a low-volume term. 376 reads big; 2/day does not.
# THE RULE, enforced below and not merely written down: volume first, position second. Every
# opportunity line prints monthly volume AND impressions-per-day, and anything under the floor is
# labelled LOW VOLUME so it can never again be dressed up as a priority.
LOW_VOLUME_FLOOR = 20


def sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"cc-sql FAILED: {(r.stderr or r.stdout)[:300]}")
    return json.loads(r.stdout or "[]")


def windows(days):
    return (f"current_date - {days*2}", f"current_date - {days}", f"current_date - {days}", "current_date")


def fetch(section, days):
    a0, a1, b0, b1 = windows(days)
    q = f"""
    WITH cur AS (
      SELECT lower(g.query) k, sum(g.impressions) impr, sum(g.clicks) clicks,
             sum(g.position*g.impressions)/nullif(sum(g.impressions),0) wpos
      FROM seo_gsc_daily g WHERE g.property_key='{PROP}' AND g.date > {b0} AND g.date <= {b1}
      GROUP BY 1),
    prev AS (
      SELECT lower(g.query) k, sum(g.impressions) impr,
             sum(g.position*g.impressions)/nullif(sum(g.impressions),0) wpos
      FROM seo_gsc_daily g WHERE g.property_key='{PROP}' AND g.date > {a0} AND g.date <= {a1}
      GROUP BY 1),
    ranks AS (
      SELECT DISTINCT ON (lower(query)) lower(query) k,
             replace(replace(page,'https://www.sygma-solutions.com',''),'https://sygma-solutions.com','') pg
      FROM seo_gsc_daily WHERE property_key='{PROP}' AND date > {b0} AND date <= {b1}
      ORDER BY lower(query), impressions DESC)
    SELECT m.keyword, m.target_page, m.priority AS vol,
           COALESCE(c.impr,0) impr, COALESCE(c.clicks,0) clicks,
           round(c.wpos::numeric,1) wpos, round(p.wpos::numeric,1) prev_wpos,
           COALESCE(p.impr,0) prev_impr, r.pg AS ranks_via
    FROM seo_keyword_map m
    LEFT JOIN cur  c ON c.k = lower(m.keyword)
    LEFT JOIN prev p ON p.k = lower(m.keyword)
    LEFT JOIN ranks r ON r.k = lower(m.keyword)
    WHERE m.property_key='{PROP}' AND m.cluster='{section}' AND m.intent='commercial'
    ORDER BY COALESCE(c.impr,0) DESC"""
    return sql(q)


def page_clicks(days):
    a0, a1, b0, b1 = windows(days)
    rows = sql(f"""
      SELECT replace(replace(page,'https://www.sygma-solutions.com',''),'https://sygma-solutions.com','') pg,
             sum(clicks) FILTER (WHERE date > {b0} AND date <= {b1}) clicks,
             sum(impressions) FILTER (WHERE date > {b0} AND date <= {b1}) impr,
             sum(clicks) FILTER (WHERE date > {a0} AND date <= {a1}) prev_clicks
      FROM seo_gsc_page_daily WHERE property_key='{PROP}' AND date > {a0} AND date <= {b1}
      GROUP BY 1""")
    return {r["pg"]: r for r in rows}


def classify(r):
    """The four 'needs work' rules, in Pete's priority order. Returns (label, rank) or None."""
    impr, pos = r["impr"] or 0, r["wpos"]
    if r["ranks_via"] and r["target_page"] and r["ranks_via"] != r["target_page"] and impr >= 10:
        return ("WRONG PAGE", 2)
    if pos is not None and 4 <= float(pos) <= 20 and impr >= 20:
        return ("STRIKING DISTANCE", 1)
    if r["prev_wpos"] is not None and pos is not None and float(pos) - float(r["prev_wpos"]) >= 5 and impr >= 20:
        return ("SLIPPED", 3)
    if impr < 5:
        return ("INVISIBLE", 4)
    return None


def report(section_key, days):
    section = SECTIONS[section_key]
    rows = fetch(section, days)
    pc = page_clicks(days)
    print(f"\n{'='*78}\n{section.upper()} REPORT  --  last {days} days vs the previous {days}")
    print(f"{'='*78}")
    print(f"source: seo_keyword_map ({len(rows)} keywords) | clicks from seo_gsc_page_daily | "
          f"positions from seo_gsc_daily, impression-weighted")

    # ---- VIEW 1: HOW WE ARE DOING
    print(f"\n--- 1. HOW WE ARE DOING ---")
    live = [r for r in rows if (r["impr"] or 0) > 0]
    print(f"  {len(live)} of {len(rows)} keywords earned impressions.")
    print(f"\n  {'keyword':<44}{'impr':>7}{'pos':>7}{'was':>7}{'move':>7}")
    for r in live[:25]:
        pos = r["wpos"]; prev = r["prev_wpos"]
        mv = "-" if (pos is None or prev is None) else f"{float(prev)-float(pos):+.1f}"
        print(f"  {r['keyword'][:43]:<44}{r['impr']:>7}{(pos if pos is not None else '-'):>7}"
              f"{(prev if prev is not None else '-'):>7}{mv:>7}")
    if len(live) > 25:
        print(f"  ... and {len(live)-25} more with impressions (full set in --json)")

    print(f"\n  PAGE ROLL-UP (clicks are page-level, the true figure)")
    print(f"  {'page':<50}{'kw':>4}{'clicks':>8}{'was':>6}{'impr':>8}")
    bypage = {}
    for r in rows:
        bypage.setdefault(r["target_page"], []).append(r)
    tot_c = tot_p = tot_i = 0
    for pg, rs in sorted(bypage.items(), key=lambda kv: -(pc.get(kv[0], {}).get("clicks") or 0)):
        d = pc.get(pg, {})
        c, p, i = d.get("clicks") or 0, d.get("prev_clicks") or 0, d.get("impr") or 0
        tot_c += c; tot_p += p; tot_i += i
        print(f"  {pg[:49]:<50}{len(rs):>4}{c:>8}{p:>6}{i:>8}")
    print(f"  {'SECTION TOTAL':<50}{len(rows):>4}{tot_c:>8}{tot_p:>6}{tot_i:>8}")

    # ---- VIEW 2: WHAT NEEDS WORK
    print(f"\n--- 2. WHAT NEEDS WORK ---")
    work = []
    for r in rows:
        c = classify(r)
        if c:
            work.append((c[1], c[0], r))
    # Rank by monthly VOLUME, never by impressions. Sorting by impressions is what pushed a
    # 10/month term to the top of a priority list on 1 Aug 2026 (see LOW_VOLUME_FLOOR above).
    work.sort(key=lambda x: (x[0], -(x[2].get("vol") or 0), -(x[2]["impr"] or 0)))
    if not work:
        print("  nothing crosses the thresholds.")
    # Show a slice of EVERY category. A flat top-N sorted by priority let the largest category fill
    # the cap and hid the others entirely: on the first run 47 striking-distance rows consumed all 22
    # slots and all 65 WRONG PAGE findings rendered nowhere. A report that silently drops a whole
    # category is worse than one that shows less of each.
    PER_CAT = 8
    shown = {}
    display = []
    for rank, label, r in work:
        if shown.get(label, 0) >= PER_CAT:
            continue
        shown[label] = shown.get(label, 0) + 1
        display.append((rank, label, r))
    print(f"  {'':<19}{'keyword':<40}{'vol/mo':>8}{'impr/day':>10}{'pos':>7}   detail")
    for rank, label, r in display:
        vol = r.get("vol") or 0
        perday = (r["impr"] or 0) / float(days)
        lowvol = " ⚠LOW VOLUME" if vol < LOW_VOLUME_FLOOR else ""
        if label == "WRONG PAGE":
            extra = f"ranks via {r['ranks_via']} not {r['target_page']}"
        elif label == "SLIPPED":
            extra = f"was {r['prev_wpos']}"
        else:
            extra = f"-> {r['target_page']}"
        print(f"  [{label:<17}]{r['keyword'][:39]:<40}{vol:>8}{perday:>10.1f}"
              f"{(r['wpos'] if r['wpos'] is not None else '-'):>7}   {extra}{lowvol}")
    counts = {}
    for _, l, _ in work:
        counts[l] = counts.get(l, 0) + 1
    for l, n in sorted(counts.items()):
        if n > PER_CAT:
            print(f"  ... {l}: showing {PER_CAT} of {n}")
    print(f"\n  totals: " + " | ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"  VOLUME FIRST, POSITION SECOND. vol/mo is monthly searches (Ahrefs); anything under "
          f"{LOW_VOLUME_FLOOR}/mo is flagged LOW VOLUME and is not a priority however good its position. "
          f"A 90-day impression total flatters a low-volume term -- impr/day is the honest read.")
    return rows


def winnability(rows, top_n, days):
    """For the top opportunities, WHO is above us and on what authority.

    Why this is worth units (added 1 Aug 2026): "striking distance" on its own only says a term is
    CLOSE. It does not say whether the page above us is weak. On `hsg47 training` the pages at 4, 5
    and 6 are DR 11, DR 0 and DR 8 with 0-1 referring domains each, against Sygma's DR 20 -- we
    out-authority half of page one and sit at 21. That is a different instruction from "you are at
    21". Ahrefs charges ~572 units per SERP, so this is OPT-IN (--winnability N), never automatic:
    a cron may never spend money, and a routine report must stay free.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("ahrefs_api", f"{VAULT}/ahrefs-api.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    api = m.AhrefsAPI(caller="seo-section-report:winnability")
    cand = [r for r in rows if r["wpos"] is not None and 4 <= float(r["wpos"]) <= 25
            and (r.get("vol") or 0) >= LOW_VOLUME_FLOOR]
    cand.sort(key=lambda r: -(r.get("vol") or 0))
    cand = cand[:top_n]
    print(f"\n--- 3. WINNABILITY (paid: ~572 Ahrefs units per term, {len(cand)} terms) ---")
    print("    Who is ABOVE us organically, and on what authority. Sygma's own DR is the yardstick.")
    for r in cand:
        try:
            serp = api.serp_overview(r["keyword"])
        except Exception as e:
            print(f"  {r['keyword'][:44]:<46} SERP pull FAILED: {str(e)[:60]}")
            continue
        drs = [float(x["domain_rating"]) for x in serp[:10] if x.get("domain_rating") is not None]
        if not drs:
            print(f"  {r['keyword'][:44]:<46} no organic DR data returned — cannot judge, say so")
            continue
        med = sorted(drs)[len(drs)//2]
        weak = sum(1 for d in drs if d < 20)
        verdict = "WEAK field" if med < 20 else ("EVEN" if med < 35 else "STRONG field")
        print(f"  {r['keyword'][:44]:<46} vol {r.get('vol') or 0:>4}  pos {r['wpos']:>5}  "
              f"median DR above {med:>5.0f}  ({weak}/{len(drs)} under DR20)  {verdict}")


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("avoidance", "mapping", "both"):
        raise SystemExit(__doc__)
    days = int(args[args.index("--days")+1]) if "--days" in args else 28
    which = ["avoidance", "mapping"] if args[0] == "both" else [args[0]]
    win = int(args[args.index("--winnability")+1]) if "--winnability" in args else 0
    out = {}
    for s in which:
        out[s] = report(s, days)
        if win:
            winnability(out[s], win, days)
    if "--json" in args:
        print("\n" + json.dumps(out, default=str))


if __name__ == "__main__":
    main()
