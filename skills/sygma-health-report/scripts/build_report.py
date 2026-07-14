#!/usr/bin/env python3
"""
Sygma Solutions website health report — combined multi-source pull.

Sources (all live):
  - Ahrefs      : Domain Rating + Rank Tracker positions + 7-day per-keyword trajectory
  - GSC         : site top pages/queries + per-page query detail (28d)
  - GA4         : sessions/users/conversions + traffic-source split + per-page views (28d)
  - Google Ads  : ad-group + landing-page performance + 7-day spend (30d / 7d)

Output (post-cutover homes, verified 14 Jul 2026):
  - Markdown report -> /tmp/health-report-{YYYY-MM-DD}.md (ephemeral working copy)
  - AUTO-PUBLISHED -> CC reports.snapshots (report_key='sygma-health', one immutable
    row per run) -> rendered at commandcentre.info/m/sygma-reports, "Health reports" tab.
    Previous reports = SELECT period_date FROM reports.snapshots WHERE report_key='sygma-health'.
  - Headline summary -> stdout (for the triggering session to read + narrate)

Run:
  VAULT=/tmp/pbs python3 /tmp/pbs/skills/sygma-health-report/scripts/build_report.py

The four "deep-dive" cluster pages are defined in PAGES below; edit that list to
re-point the report at different pages. Everything else is derived live.
Tokens: Ahrefs token = CC public.secrets 'ahrefs-token', materialised by the boot
kernel to $VAULT/Library/processes/secrets/ahrefs-token. Helper APIs (gsc/ga4/ads)
auth themselves the same way. Helper-first per [[external-service-routing]].
State docs (ads ledger, non-issues) are read LIVE from vault_notes, not files.
Surfer dropped 2026-06-08 (Pete's call — audit quota perpetually exceeded, data not
needed here). See [[surfer-api-configuration]] § Quota & limits.
"""
import os, urllib.request, urllib.error, urllib.parse, json, time, importlib.util, re, sys
from datetime import date, timedelta

# VAULT path: env override (for Cowork sandbox) > host default (Pete's Mac)
VAULT = os.environ.get("VAULT_ROOT", "/tmp/pbs")
SCRIPTS = VAULT  # helpers live at the pbs root (repo flattened 2026-06)

# ----------------------------- CONFIG ---------------------------------------
SITE_DOMAIN    = "sygma-solutions.com"
GSC_PROP       = "sc-domain:sygma-solutions.com"
GA4_PROP       = "354127076"
AHREFS_PROJECT = "9613452"

# Deep-dive cluster pages (the "recently worked on" set). Each tracks a curated
# set of commercial terms day-by-day regardless of which URL they currently rank to.
PAGES = [
    {"path": "/courses/eusr-cat1", "kw": "eusr cat 1", "label": "EUSR CAT1",
     "terms": ["eusr cat and genny training", "eusr cat 1 training", "eusr cat 1",
               "eusr category 1", "locate utility services training"]},
    {"path": "/courses/cat-and-genny-training", "kw": "cat and genny training", "label": "Cat & Genny",
     "terms": ["cat and genny training", "cat and genny training near me", "cat and genny training online",
               "cat scanner training", "cat and genny training courses"]},
    {"path": "/courses/cable-avoidance-training", "kw": "cable avoidance training", "label": "Cable Avoidance",
     "terms": ["cable avoidance training", "cable avoidance course", "online cable avoidance training",
               "cable avoidance tool training", "cable avoidance courses uk"]},
    {"path": "/courses/hsg47-training", "kw": "hsg47 training", "label": "HSG47",
     "terms": ["hsg47 training", "hsg47 training near me", "hsg47 training course",
               "hsg 47 training", "hsg47"]},
]

# Authoritative state docs live in CC vault_notes and are read LIVE via _vault_note_body():
#   - ads ledger    -> "Sygma Google Ads -- Account State" (## Recent changes ledger)
#   - non-issues    -> "Sygma Solutions -- SEO non-issues and pre-work checks"
#   - state of play -> "Sygma SEO -- State of Play (single source of truth; update IN PLACE)"

# URLs with locked/restricted decisions. Flagging anything against these URLs requires reading the linked decision first.
NO_WORK_URLS = {
    "/knowledge-hub/hsg47-explained":
        "No RANKING/CTR pitches (non-intent page; traffic doesn't convert) — but normal technical "
        "hygiene (title length, broken links, audit errors) IS fine, rule relaxed 7 Jul 2026. "
        "See vault_notes 'No Active Work on /knowledge-hub/hsg47-explained' + feedback_hsg47_explained_no_ranking_pitches.",
}

# Residue threshold — landing pages with no paid click in this window are pre-fix residue ageing out of the 30d rolling window.
RESIDUE_DAYS = 14

# ----------------------------- tokens / helpers -----------------------------
def _ahrefs_token():
    # SSOT = CC public.secrets 'ahrefs-token', materialised by the boot kernel. No hardcoded fallback:
    # a baked-in key silently outlives rotation and leaks in git.
    try:
        v = open(f"{VAULT}/Library/processes/secrets/ahrefs-token").read().strip()
        if v:
            return v
    except Exception:
        pass
    sys.exit("ahrefs-token not materialised — run the boot kernel (pete-session-bootstrap.py); "
             "SSOT is CC public.secrets 'ahrefs-token'.")

AHREFS_TOKEN = _ahrefs_token()

def _load(modname, path):
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

gsc = _load("gscapi", f"{SCRIPTS}/gsc-api.py").GSCAPI()
ga4 = _load("ga4api", f"{SCRIPTS}/ga4-api.py").GA4API()
ads = _load("adsapi", f"{SCRIPTS}/ads-api.py").GoogleAdsAPI()

def ah(path, params):
    url = f"https://api.ahrefs.com/v3/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {AHREFS_TOKEN}"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except Exception as e:
        return {"_err": str(e)}

def slug(u):
    s = (u or "").split(SITE_DOMAIN)[-1].split("?")[0]
    return s or "/"

def gbp(micros):
    return int(micros or 0) / 1e6

# ----------------------------- state-doc readers ----------------------------
def _vault_note_body(key):
    """Fetch a vault_notes body live (ledger/non-issues moved out of flat files 2026-06)."""
    import subprocess
    try:
        out = subprocess.run([sys.executable, f"{VAULT}/cc-sql.py",
            "SELECT body FROM vault_notes WHERE vault_path ILIKE '%%%s%%' OR title ILIKE '%%%s%%' ORDER BY length(body) DESC LIMIT 1" % (key, key)],
            capture_output=True, text=True, timeout=30, env={**os.environ, "VAULT": VAULT})
        rows = json.loads(out.stdout or "[]")
        return rows[0]["body"] if rows else ""
    except Exception:
        return ""

def read_ads_ledger(days=30):
    """Parse 'Recent changes ledger' section of google-ads-account.md.
    Returns list of {date, headline} for entries within the last `days` days, newest first."""
    txt = _vault_note_body("google-ads-account")
    if not txt:
        return []
    m = re.search(r"^## Recent changes ledger\s*\n(.*?)(?=\n## )", txt, re.S | re.M)
    if not m:
        return []
    body = m.group(1)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    entries = []
    for raw in body.split("\n- **"):
        raw = raw.strip()
        if not raw or not re.match(r"\d{4}-\d{2}-\d{2}", raw):
            continue
        em = re.match(r"(\d{4}-\d{2}-\d{2})([^*]*)\*\*\s*--\s*(.+)", raw, re.S)
        if not em:
            continue
        d, paren, summary = em.group(1), em.group(2).strip(), em.group(3).strip()
        if d < cutoff:
            continue
        first_line = summary.split("\n")[0].lstrip("*").strip()
        first_sent = re.split(r"(?<=[.!?])\s", first_line)[0]
        if len(first_sent) > 220:
            first_sent = first_sent[:217] + "…"
        head = f"{paren} — {first_sent}" if paren else first_sent
        entries.append({"date": d, "headline": head.strip(" —")})
    return entries

def pull_last_paid_click_per_url():
    """Per landing-page URL, return the most recent date with clicks > 0 in the last 30 days.
    Used to flag rolling-window residue (URLs ageing out post-fix)."""
    try:
        rows = ads.query(
            "SELECT landing_page_view.unexpanded_final_url, segments.date, metrics.clicks "
            "FROM landing_page_view WHERE segments.date DURING LAST_30_DAYS AND metrics.clicks > 0 "
            "ORDER BY segments.date DESC"
        )
    except Exception:
        return {}
    latest = {}
    for r in rows or []:
        url = r.get("landingPageView", {}).get("unexpandedFinalUrl", "")
        d = r.get("segments", {}).get("date") or ""
        if not url or not d:
            continue
        if url not in latest or d > latest[url]:
            latest[url] = d
    return latest

# ----------------------------- data pulls -----------------------------------
def pull_ahrefs():
    dr = ah("site-explorer/domain-rating", {"target": SITE_DOMAIN, "date": date.today().isoformat()})
    dr_val = dr.get("domain_rating", {}).get("domain_rating") if isinstance(dr, dict) and "domain_rating" in dr else None
    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(7)]
    perday = {}
    for d in days:
        r = ah("rank-tracker/overview", {"project_id": AHREFS_PROJECT, "device": "desktop", "date": d,
                                         "select": "keyword,position,url,volume", "limit": "1000"})
        ov = r.get("overviews") if isinstance(r, dict) else None
        perday[d] = {}
        for row in (ov or []):
            perday[d][row.get("keyword", "").lower()] = (row.get("position"), row.get("url") or "", row.get("volume") or 0)
    latest = perday[days[0]]
    buckets = {"1-3": 0, "4-10": 0, "11-20": 0, "21-50": 0, "51+": 0, "unranked": 0}
    for kw, (pos, u, v) in latest.items():
        if pos is None:
            buckets["unranked"] += 1
        elif pos <= 3: buckets["1-3"] += 1
        elif pos <= 10: buckets["4-10"] += 1
        elif pos <= 20: buckets["11-20"] += 1
        elif pos <= 50: buckets["21-50"] += 1
        else: buckets["51+"] += 1
    return {"dr": dr_val, "days_desc": days, "days_asc": sorted(days), "perday": perday,
            "buckets": buckets, "tracked": len(latest)}

def pull_gsc():
    out = {"page_queries": {}}
    try: out["top_pages"] = gsc.top_pages(GSC_PROP, days=28, limit=12)
    except Exception as e: out["top_pages"] = []
    try: out["top_queries"] = gsc.top_queries(GSC_PROP, days=28, limit=15)
    except Exception as e: out["top_queries"] = []
    for p in PAGES:
        try:
            out["page_queries"][p["path"]] = gsc.page_queries(GSC_PROP, f"https://{SITE_DOMAIN}{p['path']}", days=28)[:8]
        except Exception:
            out["page_queries"][p["path"]] = []
    # Daily by-query positions for each head term — the mandatory cross-check for any
    # Ahrefs movement (judge on GSC, never Ahrefs alone; see Notes in the report).
    out["kw_daily"] = {}
    for p in PAGES:
        try:
            fg = [{"filters": [{"dimension": "query", "operator": "equals", "expression": p["kw"]}]}]
            rows = gsc.query(GSC_PROP, ["date"], date_range=14, limit=31, filters=fg)
            out["kw_daily"][p["kw"]] = [
                {"date": r["keys"][0], "position": round(r["position"], 1),
                 "impressions": r["impressions"], "clicks": r["clicks"]}
                for r in rows or []]
        except Exception:
            out["kw_daily"][p["kw"]] = []
    out["latest_day"] = max((r["date"] for rows in out["kw_daily"].values() for r in rows), default=None)
    return out

def pull_ga4():
    out = {}
    try: out["summary"] = ga4.summary(GA4_PROP, days=28).get("totals", {})
    except Exception: out["summary"] = {}
    try: out["sources"] = ga4.top_sources(GA4_PROP, days=28)[:7]
    except Exception: out["sources"] = []
    try: out["conversions"] = [c for c in ga4.conversions(GA4_PROP, days=28) if float(c.get("conversions", 0) or 0) > 0]
    except Exception: out["conversions"] = []
    try:
        tp = ga4.top_pages(GA4_PROP, days=28, limit=120)
        agg = {}
        for r in tp:
            path = r.get("pagePath", "")
            agg[path] = agg.get(path, 0) + int(r.get("screenPageViews", 0) or 0)
        out["page_views"] = {p["path"]: agg.get(p["path"], 0) for p in PAGES}
    except Exception:
        out["page_views"] = {p["path"]: 0 for p in PAGES}
    return out

def pull_ads():
    def q(gaql):
        try: return ads.query(gaql)
        except Exception: return []
    return {
        "ad_groups": q("SELECT ad_group.name, metrics.cost_micros, metrics.clicks, metrics.impressions, "
                       "metrics.conversions FROM ad_group WHERE segments.date DURING LAST_30_DAYS "
                       "AND ad_group.status='ENABLED' ORDER BY metrics.cost_micros DESC"),
        "landing": q("SELECT landing_page_view.unexpanded_final_url, metrics.cost_micros, metrics.clicks, "
                     "metrics.conversions FROM landing_page_view WHERE segments.date DURING LAST_30_DAYS "
                     "ORDER BY metrics.cost_micros DESC LIMIT 12"),
        "by_day": q("SELECT segments.date, metrics.cost_micros, metrics.clicks, metrics.conversions "
                    "FROM campaign WHERE segments.date DURING LAST_7_DAYS ORDER BY segments.date DESC"),
        "last_paid_click": pull_last_paid_click_per_url(),
        "ledger": read_ads_ledger(days=30),
    }

# ----------------------------- markdown build -------------------------------
def arrow(delta):
    if delta is None: return ""
    if delta < 0: return f" ↑{abs(delta)}"   # position got smaller = improved
    if delta > 0: return f" ↓{delta}"
    return " →"

def build_md(A, G, GA, ADS):
    today = date.today().isoformat()
    L = []
    L.append(f"---\ntype: report\nsubtype: sygma-health\ndate: {today}\nproperty: \"[[Properties/Sygma Solutions Website]]\"\n"
             f"tags: [report, seo, sygma, multi-source]\n---\n")
    L.append(f"# Sygma Solutions — Website Health Report")
    L.append(f"*Generated {today} · sources: Ahrefs · GSC · GA4 · Google Ads*\n")

    # ---- site overview ----
    sm = GA.get("summary", {})
    dur = float(sm.get("averageSessionDuration", 0) or 0)
    L.append("## Site overview\n")
    L.append("| Source | Reading |")
    L.append("|---|---|")
    b = A.get("buckets", {})
    L.append(f"| **Ahrefs** | DR **{A.get('dr')}** · Rank Tracker {A.get('tracked')} kw: "
             f"{b.get('1-3',0)} top-3, {b.get('1-3',0)+b.get('4-10',0)} top-10, "
             f"{b.get('1-3',0)+b.get('4-10',0)+b.get('11-20',0)} top-20, {b.get('unranked',0)} unranked |")
    L.append(f"| **GA4** (28d) | {sm.get('sessions','?')} sessions · {sm.get('activeUsers','?')} users · "
             f"{sm.get('screenPageViews','?')} views · bounce {round(float(sm.get('bounceRate',0) or 0)*100,1)}% · "
             f"avg {int(dur//60)}m{int(dur%60)}s |")
    convstr = " · ".join(f"{int(float(c.get('conversions',0)))} {c.get('eventName')}" for c in GA.get("conversions", [])[:6])
    L.append(f"| **GA4 conversions** | {convstr or '—'} |")
    total_spend = sum(gbp(r.get('metrics',{}).get('costMicros')) for r in ADS.get('ad_groups',[]))
    total_conv = sum(float(r.get('metrics',{}).get('conversions',0) or 0) for r in ADS.get('ad_groups',[]))
    L.append(f"| **Google Ads** (30d) | £{total_spend:,.0f} spend · {total_conv:.1f} conversions across "
             f"{len([r for r in ADS.get('ad_groups',[]) if gbp(r.get('metrics',{}).get('costMicros'))>0])} live ad groups |")
    L.append("")

    # GA4 sources
    if GA.get("sources"):
        L.append("**Traffic sources (28d):** " + " · ".join(
            f"{r.get('sessionDefaultChannelGroup','?')}/{r.get('sessionSource','?')} {r.get('sessions','?')}"
            for r in GA["sources"][:5]) + "\n")

    # ---- recent ledger entries (state-of-play, must read before flagging anything) ----
    ledger = ADS.get("ledger", [])
    if ledger:
        L.append("## Recent ad-account changes (last 30d)\n")
        L.append("Pulled live from vault_notes [[Sygma Google Ads -- Account State]] (## Recent changes ledger). "
                 "**Read these BEFORE flagging anything below — if a finding sits inside this window, "
                 "it has already been investigated or actioned.**\n")
        for e in ledger[:12]:
            L.append(f"- **{e['date']}** — {e['headline']}")
        L.append("")

    # ---- locked no-work URLs (decision-locked, do not propose work) ----
    if NO_WORK_URLS:
        L.append("## Locked no-work pages\n")
        L.append("These URLs have **locked decisions against active work**. State data factually if asked; never propose CTR rescues, title rewrites, Surfer iteration, or new tasks.\n")
        for url, note in NO_WORK_URLS.items():
            L.append(f"- `{url}` — {note}")
        L.append("")

    # ---- per-page scorecard ----
    L.append("## Cluster scorecard\n")
    L.append("| Page | Head term — pos (Ahrefs) | GA4 views 28d | Ads 30d (spend / conv) |")
    L.append("|---|---|---|---|")
    land = {slug(r.get('landingPageView',{}).get('unexpandedFinalUrl','')): r.get('metrics',{}) for r in ADS.get('landing',[])}
    latest = A["perday"][A["days_desc"][0]]
    for p in PAGES:
        head = latest.get(p["kw"].lower())
        if head and head[0] is not None:
            headpos = f"{head[0]} → {slug(head[1])}"
        else:
            headpos = "not captured"
        views = GA.get("page_views", {}).get(p["path"], 0)
        lm = land.get(p["path"], {})
        adcell = f"£{gbp(lm.get('costMicros')):.0f} / {float(lm.get('conversions',0) or 0):.1f}" if lm else "£0 / 0"
        L.append(f"| **{p['label']}** | {headpos} | {views} | {adcell} |")
    L.append("")

    # ---- 7-day trajectories ----
    L.append("## 7-day rank trajectory (Ahrefs, desktop)\n")
    days_asc = A["days_asc"]
    hdr_days = " | ".join(d[5:] for d in days_asc)
    for p in PAGES:
        L.append(f"### {p['label']}  ({p['path']})\n")
        L.append(f"| Keyword | {hdr_days} | Δ7d |")
        L.append("|" + "---|" * (len(days_asc) + 2))
        for kw in p["terms"]:
            cells = []
            first = last = None
            for d in days_asc:
                v = A["perday"][d].get(kw.lower())
                pos = v[0] if v else None
                if pos is not None:
                    if first is None: first = pos
                    last = pos
                cells.append(str(pos) if pos is not None else "–")
            delta = (last - first) if (first is not None and last is not None) else None
            L.append(f"| {kw} | " + " | ".join(cells) + f" | {('—' if delta is None else ('+' if delta>0 else '')+str(delta))}{arrow(delta)} |")
        # which URL the head term ranks to (cannibalisation check)
        head = latest.get(p["kw"].lower())
        if head and head[1]:
            L.append(f"\n*Head term \"{p['kw']}\" currently ranks via* `{slug(head[1])}`")
        L.append("")

    # ---- GSC daily cross-check (the judge for any Ahrefs movement) ----
    kwd = G.get("kw_daily", {})
    if any(kwd.values()):
        L.append("## GSC daily cross-check — head terms (the judge)\n")
        L.append("**Rule: judge movement on GSC, never on Ahrefs alone.** Ahrefs trajectory rows repeat "
                 "between its actual crawls — a flat run of identical values is carried-forward samples, so a "
                 "Δ7d is two single-location snapshots, not a trend. The table below is Google's own daily "
                 "blended average position per query. GSC lags 2–3 days: if an Ahrefs step-change happened "
                 f"after **{G.get('latest_day') or 'the latest GSC day'}**, GSC cannot confirm or refute it yet — "
                 "say exactly that, do not narrate the Ahrefs move as fact.\n")
        all_dates = sorted({r["date"] for rows in kwd.values() for r in rows})
        terms = [p["kw"] for p in PAGES]
        L.append("| Date | " + " | ".join(terms) + " |")
        L.append("|" + "---|" * (len(terms) + 1))
        bydate = {t: {r["date"]: r for r in kwd.get(t, [])} for t in terms}
        for d in all_dates:
            cells = []
            for t in terms:
                r = bydate[t].get(d)
                cells.append(f"{r['position']} ({r['impressions']})" if r and r["impressions"] else "–")
            L.append(f"| {d[5:]} | " + " | ".join(cells) + " |")
        L.append("\n*Cell = GSC avg position (impressions) for the exact query that day. Positions on <5 "
                 "impressions bounce hard — read the band, not single days.*\n")

    # ---- GSC detail ----
    L.append("## GSC — top pages (28d)\n")
    L.append("| Clicks | Impr | CTR% | Pos | Page |")
    L.append("|---|---|---|---|---|")
    for r in G.get("top_pages", [])[:10]:
        L.append(f"| {r['clicks']} | {r['impressions']} | {r['ctr']} | {r['position']} | {slug(r['page'])} |")
    L.append("\n## GSC — top queries (28d)\n")
    L.append("| Clicks | Impr | CTR% | Pos | Query |")
    L.append("|---|---|---|---|---|")
    for r in G.get("top_queries", [])[:12]:
        L.append(f"| {r['clicks']} | {r['impressions']} | {r['ctr']} | {r['position']} | {r['query']} |")
    L.append("")

    # ---- Ads detail ----
    L.append("## Google Ads (30d)\n")
    L.append("**Ad groups (spending):**\n")
    L.append("| Spend | Clicks | Impr | Conv | Ad group |")
    L.append("|---|---|---|---|---|")
    for r in ADS.get("ad_groups", []):
        m = r.get("metrics", {})
        if gbp(m.get("costMicros")) <= 0: continue
        L.append(f"| £{gbp(m.get('costMicros')):.2f} | {m.get('clicks',0)} | {m.get('impressions',0)} | "
                 f"{float(m.get('conversions',0) or 0):.1f} | {r.get('adGroup',{}).get('name','?')} |")
    L.append("\n**Landing pages:**\n")
    L.append("| Spend | Clicks | Conv | Page | Note |")
    L.append("|---|---|---|---|---|")
    last_clicks = ADS.get("last_paid_click", {})
    residue_cutoff = (date.today() - timedelta(days=RESIDUE_DAYS)).isoformat()
    for r in ADS.get("landing", [])[:10]:
        m = r.get("metrics", {})
        url = r.get("landingPageView", {}).get("unexpandedFinalUrl", "")
        spath = slug(url)
        notes = []
        if spath in NO_WORK_URLS:
            notes.append("**locked no-work page** — do not propose work, see Locked no-work pages above")
        last = last_clicks.get(url)
        clicks_now = int(m.get("clicks", 0) or 0)
        if last and last < residue_cutoff:
            days_ago = (date.today() - date.fromisoformat(last)).days
            notes.append(f"decaying residue — last paid click {last} ({days_ago}d ago), 0 paid clicks in last {RESIDUE_DAYS}d (spend is pre-fix history ageing out of 30d window)")
        elif not last and clicks_now == 0:
            notes.append("no paid clicks in 30d")
        L.append(f"| £{gbp(m.get('costMicros')):.2f} | {clicks_now} | {float(m.get('conversions',0) or 0):.1f} | "
                 f"{spath} | {'; '.join(notes) if notes else ''} |")
    L.append("")

    L.append("## Notes\n")
    L.append("- Position arrows: ↑ = improved (smaller number), ↓ = dropped. Δ7d compares oldest vs newest capture in window.")
    L.append("- GSC position/CTR are 28-day blended averages across all queries a page appears for — read alongside the live Ahrefs head-term position.")
    L.append("- Ahrefs site-metrics organic counts lag post-migration; GSC is the organic source of truth.")
    L.append("- **Judge movement on GSC, never Ahrefs alone.** Any Ahrefs step-change (several terms moving the same day) MUST be read against the GSC daily cross-check table before it is narrated. Ahrefs flat runs = carried-forward crawls; the step is the gap between two single-location samples. If the step post-dates the latest GSC day, its reality is UNCONFIRMED — report it as such.")
    L.append("- Landing-page \"Note\" column: \"decaying residue\" = URL has no paid clicks in the last "
             f"{RESIDUE_DAYS} days and is ageing out of the 30-day rolling window; the spend is pre-fix history, not live waste. \"locked no-work page\" = decision-locked, do not propose work.")
    L.append("- Before flagging any finding: cross-check it against the Recent ad-account changes section + [[Properties/Sygma Solutions Website/seo-non-issues]]. If it appears in either, it has been investigated.")
    return "\n".join(L)

# ----------------------------- main -----------------------------------------
def main():
    print("[1/4] Ahrefs (DR + 7-day rank tracker)…", flush=True); A = pull_ahrefs()
    print("[2/4] GSC (site + per-page)…", flush=True);             G = pull_gsc()
    print("[3/4] GA4 (traffic + conversions)…", flush=True);       GA = pull_ga4()
    print("[4/4] Google Ads (30d)…", flush=True);                  ADS = pull_ads()

    md = build_md(A, G, GA, ADS)
    today = date.today().isoformat()
    out_path = f"/tmp/health-report-{today}.md"
    with open(out_path, "w") as f:
        f.write(md)

    # Auto-publish the snapshot to the CC (reports.snapshots, report_key='sygma-health'
    # -> /m/sygma-reports "Health reports" tab). Non-fatal: warn loudly, never die.
    import html as _html
    try:
        cc = _load("ccpublish", f"{SCRIPTS}/cc_publish.py")
        body = ("<pre style='font:13px/1.55 ui-monospace,Menlo,monospace;white-space:pre-wrap;"
                "padding:18px'>" + _html.escape(md) + "</pre>")
        ok = cc.publish("sygma-health", today, {"subject": f"Sygma health report — {today}", "html": body})
        print(f"\nCC publish: {'OK — verify the ' + today + ' chip at commandcentre.info/m/sygma-reports (Health reports tab)' if ok else '⚠️ FAILED — publish manually via cc_publish.publish(\"sygma-health\", …)'}")
    except Exception as e:
        print(f"\nCC publish: ⚠️ FAILED ({e}) — publish manually via cc_publish.publish('sygma-health', …)")

    # headline summary to stdout
    print("\n===== HEADLINE =====")
    print(f"DR {A.get('dr')} | tracked {A.get('tracked')} kw | "
          f"top-10 {A['buckets']['1-3']+A['buckets']['4-10']}")

    print("\n--- MANDATORY READS BEFORE NARRATING (all in CC vault_notes / daily_log) ---")
    print("Cross-check every finding against these before flagging:")
    print("  - 'Sygma SEO -- State of Play (single source of truth; update IN PLACE)'  [vault_notes]")
    print("  - 'Sygma Solutions -- SEO non-issues and pre-work checks' (9 traps)       [vault_notes]")
    print("  - 'Sygma Google Ads -- Account State' (## Recent changes ledger)          [vault_notes]")
    print("  - 'No Active Work on /knowledge-hub/hsg47-explained' (relaxed 7 Jul 26)   [vault_notes]")
    print("  - Last 3 session rows in CC daily_log (cc-sql.py)")
    print("  Fetch: VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py \"<title>\"")

    ledger = ADS.get("ledger", [])
    if ledger:
        print(f"\n--- Recent ad-account changes (last 30d, top 10) ---")
        for e in ledger[:10]:
            print(f"  {e['date']}: {e['headline'][:150]}")

    if NO_WORK_URLS:
        print(f"\n--- Locked no-work pages (do NOT propose work) ---")
        for url, note in NO_WORK_URLS.items():
            print(f"  {url} — {note.split('.')[0]}")

    last_clicks = ADS.get("last_paid_click", {})
    residue_cutoff = (date.today() - timedelta(days=RESIDUE_DAYS)).isoformat()
    residue_lines = []
    for r in ADS.get("landing", [])[:10]:
        m = r.get("metrics", {})
        url = r.get("landingPageView", {}).get("unexpandedFinalUrl", "")
        spath = slug(url)
        last = last_clicks.get(url)
        if last and last < residue_cutoff:
            days_ago = (date.today() - date.fromisoformat(last)).days
            residue_lines.append(f"  {spath} — £{gbp(m.get('costMicros')):.2f} 30d, last paid click {last} ({days_ago}d ago) → DECAYING RESIDUE, not live waste")
    if residue_lines:
        print(f"\n--- Decaying residue (do NOT flag as live waste) ---")
        for l in residue_lines:
            print(l)

    print()
    for p in PAGES:
        latest = A["perday"][A["days_desc"][0]].get(p["kw"].lower())
        days_asc = A["days_asc"]
        f0 = next((A["perday"][d].get(p["kw"].lower()) for d in days_asc if A["perday"][d].get(p["kw"].lower())), None)
        ln = A["perday"][days_asc[-1]].get(p["kw"].lower())
        d7 = (ln[0]-f0[0]) if (f0 and ln and f0[0] is not None and ln[0] is not None) else None
        # GSC 3-day read for the same head term (the judge — see report Notes)
        kwrows = G.get("kw_daily", {}).get(p["kw"], [])
        tail = [r for r in kwrows if r["impressions"]][-3:]
        gsc_str = " ".join(f"{r['date'][5:]}:{r['position']}" for r in tail) or "no GSC data"
        print(f"  {p['label']:16s} head '{p['kw']}' pos {latest[0] if latest else '—'} "
              f"(Δ7d {('—' if d7 is None else ('+'+str(d7) if d7>0 else str(d7)))}) "
              f"via {slug(latest[1]) if latest else '—'}  ||  GSC daily: {gsc_str}")
    print(f"\n⚖️  JUDGE ON GSC, NEVER AHREFS ALONE. Latest GSC day: {G.get('latest_day')} (lags 2-3d).")
    print("   An Ahrefs step-change after that date is UNCONFIRMED until GSC covers it — narrate it that way.")
    print(f"\nReport saved: {out_path}")

if __name__ == "__main__":
    main()
