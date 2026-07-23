#!/usr/bin/env python3
"""O'Connor's weekly SEO snapshot -> Command Centre.

Publishes TWO views (period = w/c Monday), each cycling by week in the CC:
  - oconnors-seo         "Overview": headline search performance, the TARGET
                          keyword -> page map with current positions + movement,
                          traffic, authority, site-health.
  - oconnors-seo-trends  "Trends":  week-by-week movement of the headline metrics
                          AND each target keyword's position over time (reads the
                          history of structured `data` stored on the overview rows).

Each overview row stores payload.data (structured metrics + keyword positions) so
the trend accumulates automatically. Sources: GSC, GA4, Ahrefs. Non-fatal per
source. Run weekly (Mon AM, launchd). Idempotent (newest row per period wins).
"""
# CRON-META
# what: O'Connor's weekly SEO snapshot (search performance + keyword->page positions + trends)
# why: weekly search visibility for the O'Connor's bar site
# reads: GSC + GA4 + Ahrefs
# writes: reports.snapshots keys oconnors-seo + oconnors-seo-trends (CC) -> /m/oconnors-seo
# entity: one-system
# report: oconnors-seo
# schedule: 0 9 * * 1
# timezone: Atlantic/Canary
# CRON-META-END
import importlib.util, json, os, urllib.request, urllib.parse, datetime as dt, sys

SC = os.path.dirname(os.path.abspath(__file__))                      # the script's own dir — correct locally + on Railway (flat /app)
_SECRETS = (os.path.join(os.environ["VAULT"], "Library/processes/secrets") if os.environ.get("VAULT")
            else os.path.join(SC, "..", "secrets"))                  # $VAULT-aware (bootstrap materialises secrets on Railway)
def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
gsc_m = _load("gsc_api", f"{SC}/gsc-api.py"); ga4_m = _load("ga4_api", f"{SC}/ga4-api.py"); cc = _load("cc_publish", f"{SC}/cc_publish.py")
_CCK = json.load(open(os.path.join(_SECRETS, "command-centre-supabase-keys.json")))

AHREFS = (os.environ.get("AHREFS_TOKEN") or open(os.path.join(_SECRETS, "ahrefs-token")).read().strip())  # env-first (Railway), then secrets/
SITE_GSC = "sc-domain:oconnors.bar"; GA4_PROP = "540368935"

# Target keyword -> assigned page (Playa Blanca specific; from the keyword tracker).
TARGETS = [
    ("irish bar playa blanca", "Home", 90), ("bars in playa blanca", "Home", 150),
    ("best bars in playa blanca", "Home", 60), ("pubs in playa blanca", "Home", 30),
    ("irish pub playa blanca", "Home", 10),
    ("playa blanca nightlife", "What's On", 350), ("live music playa blanca", "What's On", 30),
    ("nightlife playa blanca", "What's On", 30),
    ("best breakfast playa blanca", "Food & Drink", 50), ("full irish breakfast playa blanca", "Food & Drink", 10),
    ("full english breakfast playa blanca", "Food & Drink", 30), ("guinness playa blanca", "Food & Drink", 10),
]

def ah(path, params):
    url = "https://api.ahrefs.com/v3/" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {AHREFS}", "Accept": "application/json"})
    last = {"_err": "no response"}
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=50) as r: return json.loads(r.read())
        except urllib.error.HTTPError as e:
            # capture code + body so a 403 (units) is never silently rendered as a dash (phase 0b, 2026-07-23)
            try: body = e.read().decode()[:150]
            except Exception: body = ""
            last = {"_err": f"HTTP {e.code}: {body}".strip(), "_code": e.code}
            if e.code in (400, 401, 403):  # not transient -- don't retry
                break
        except Exception as e:
            last = {"_err": str(e)[:120]}
    return last

def da(n): return (dt.date.today() - dt.timedelta(days=n)).isoformat()

def history(key="oconnors-seo", weeks=12):
    """Newest-per-period structured data rows, oldest-first."""
    try:
        u = (_CCK["url"] + "/rest/v1/snapshots?report_key=eq." + key +
             "&select=period_date,payload&order=period_date.asc,published_at.asc")
        req = urllib.request.Request(u, headers={"apikey": _CCK["service_role_key"], "Authorization": "Bearer " + _CCK["service_role_key"], "Accept-Profile": "reports"})
        rows = json.load(urllib.request.urlopen(req, timeout=30))
    except Exception: return []
    byp = {}
    for r in rows:
        d = (r.get("payload") or {}).get("data")
        if d: byp[r["period_date"]] = d   # later row (newer published) overwrites
    return [dict(period=p, **byp[p]) for p in sorted(byp)][-weeks:]

def dlt(now, prev, invert=False, pct=False):
    try: now = float(now); prev = float(prev)
    except (TypeError, ValueError): return ""
    d = now - prev
    if abs(d) < 0.05: return '<span style="color:#888">&middot;</span>'
    good = (d < 0) if invert else (d > 0)
    arrow = "&darr;" if d < 0 else "&uarr;"; col = "#1a8f3c" if good else "#c0392b"
    return f'<span style="color:{col};font-weight:600">{arrow}{abs(d):.1f}' + ('' if pct else '').rstrip() + '</span>'

def card(label, value, sub=""):
    return (f'<div style="display:inline-block;min-width:118px;background:#f4f6f8;border:1px solid #e3e7ea;border-radius:8px;padding:12px 16px;margin:0 8px 8px 0;vertical-align:top">'
            f'<div style="font-size:12px;color:#5f6b76;text-transform:uppercase;letter-spacing:.04em">{label}</div>'
            f'<div style="font-size:24px;font-weight:700;color:#1a3c5e;margin-top:2px">{value}</div>'
            f'<div style="font-size:12px;color:#5f6b76;margin-top:2px">{sub}</div></div>')
def h2(t): return f'<h2 style="font-size:17px;color:#1a3c5e;margin:26px 0 10px;border-bottom:2px solid #1a3c5e;padding-bottom:4px">{t}</h2>'
def th(*c): return '<tr style="background:#1a3c5e;color:#fff">' + ''.join(f'<th style="padding:6px 8px;text-align:{a}">{t}</th>' for t, a in c) + '</tr>'
def tr(i, *cells):
    bg = "#fff" if i % 2 == 0 else "#f4f6f8"
    return f'<tr style="background:{bg}">' + ''.join(f'<td style="padding:5px 8px;text-align:{a}">{v}</td>' for v, a in cells) + '</tr>'

monday = (dt.date.today() - dt.timedelta(days=dt.date.today().weekday())).isoformat()
metrics = {"date": monday}

# ===== GSC =====
gsc_block = ""; gsc_pos = {}
try:
    g = gsc_m.GSCAPI()
    def tot(s, e):
        r = g.query(SITE_GSC, [], date_range=(s, e), limit=1); return r[0] if r else {}
    now = tot(da(10), da(4)); prev = tot(da(17), da(11)); m28 = tot(da(31), da(4))
    metrics.update(clicks=now.get("clicks", 0), impressions=now.get("impressions", 0),
                   ctr=round((now.get("ctr", 0)) * 100, 2), avg_position=round(now.get("position", 0), 1))
    cards = (card("Clicks", int(now.get("clicks", 0)), f'7d &nbsp; {dlt(now.get("clicks"), prev.get("clicks"))}')
             + card("Impressions", f'{int(now.get("impressions",0)):,}', f'7d &nbsp; {dlt(now.get("impressions"), prev.get("impressions"))}')
             + card("Avg position", round(now.get("position", 0), 1), f'lower=better &nbsp; {dlt(now.get("position"), prev.get("position"), invert=True, pct=True)}')
             + card("CTR", f'{round((now.get("ctr",0))*100,1)}%', f'28d: {int(m28.get("clicks",0))} clicks'))
    # all queries (for the target-keyword position lookup)
    for q in (g.query(SITE_GSC, ["query"], date_range=(da(31), da(4)), limit=400) or []):
        gsc_pos[q["keys"][0].lower()] = {"pos": round(q.get("position", 0), 1), "impr": int(q.get("impressions", 0)), "clicks": int(q.get("clicks", 0))}
    gsc_block = h2("Search performance &middot; Google") + "<div>" + cards + "</div>"
except Exception as e:
    gsc_block = h2("Search performance &middot; Google") + f'<p style="color:#c0392b">Search Console unavailable ({str(e)[:70]}).</p>'

# ===== Target keywords -> page map =====
kw_positions = {}
rows_html = ""
for i, (kw, page, vol) in enumerate(TARGETS):
    hit = gsc_pos.get(kw.lower())
    pos = hit["pos"] if hit else None
    kw_positions[kw] = pos
    pos_disp = f'<b>{pos}</b>' if pos else '<span style="color:#8a949e">not yet</span>'
    impr = hit["impr"] if hit else 0
    rows_html += tr(i, (kw, "left"), (page, "left"), (f'{vol}/mo', "center"), (pos_disp, "center"), (impr or "&mdash;", "center"))
kw_block = (h2("Target keywords &rarr; page") +
            '<p style="font-size:13px;color:#5f6b76;margin:0 0 6px">The Playa-Blanca keywords we are aiming for, the page each targets, and the current Google position (from Search Console; blank = no impressions yet, the Ahrefs tracker fills these as it crawls).</p>' +
            '<table style="width:100%;border-collapse:collapse;font-size:13px">' +
            th(("Keyword", "left"), ("Target page", "left"), ("Searches", "center"), ("Position", "center"), ("Impressions", "center")) +
            rows_html + "</table>")
metrics["keyword_positions"] = kw_positions

# ===== GA4 =====
ga4_block = ""
try:
    ga = ga4_m.GA4API()
    def gs(d1, d0):
        r = ga.run_report(GA4_PROP, [], ["sessions", "totalUsers", "screenPageViews"], date_ranges=[{"startDate": d1, "endDate": d0}])
        r = r[0] if r else {}; return {"sessions": int(r.get("sessions", 0)), "users": int(r.get("totalUsers", 0)), "views": int(r.get("screenPageViews", 0))}
    gn = gs(da(7), da(1)); gp = gs(da(14), da(8))
    metrics.update(sessions=gn["sessions"], users=gn["users"], views=gn["views"])
    gc = (card("Sessions", gn["sessions"], f'7d &nbsp; {dlt(gn["sessions"], gp["sessions"])}')
          + card("Visitors", gn["users"], f'7d &nbsp; {dlt(gn["users"], gp["users"])}')
          + card("Page views", gn["views"], f'7d &nbsp; {dlt(gn["views"], gp["views"])}'))
    src = ga.top_sources(GA4_PROP, days=28, limit=6) or []
    sh = '<p style="font-size:13px;color:#5f6b76;margin:12px 0 4px"><b>Where visitors come from</b> (28 days)</p><table style="width:100%;border-collapse:collapse;font-size:13px">' + th(("Channel", "left"), ("Sessions", "center"))
    for i, s in enumerate(src):
        sh += tr(i, (s.get("sessionDefaultChannelGroup") or s.get("sessionSource") or "?", "left"), (s.get("sessions"), "center"))
    ga4_block = h2("Traffic &middot; Analytics") + "<div>" + gc + "</div>" + sh + "</table>"
except Exception as e:
    ga4_block = h2("Traffic &middot; Analytics") + f'<p style="color:#c0392b">Analytics unavailable ({str(e)[:70]}).</p>'

# ===== Ahrefs authority =====
auth_block = ""
try:
    td = (dt.date.today() - dt.timedelta(days=1)).isoformat()  # Ahrefs needs a PAST date; today = 400 bad date (phase 0a, 2026-07-23)
    _bl_r = ah("site-explorer/backlinks-stats", {"target": "oconnors.bar", "mode": "subdomains", "date": td})
    _met_r = ah("site-explorer/metrics", {"target": "oconnors.bar", "date": td, "volume_mode": "monthly"})
    _dr_r = ah("site-explorer/domain-rating", {"target": "oconnors.bar", "date": td})
    _ah_errs = [r["_err"] for r in (_bl_r, _met_r, _dr_r) if isinstance(r, dict) and r.get("_err")]
    if _ah_errs:
        # LOUD: never render a silent dash when Ahrefs actually failed (phase 0b)
        _reason = _ah_errs[0]
        _tag = "quota (units exhausted)" if "403" in _reason else "auth" if "401" in _reason else "error"
        auth_block = h2("Authority &middot; Ahrefs") + f'<p style="color:#c0392b;font-weight:600">⚠️ Ahrefs pull FAILED [{_tag}] — figures blank due to the pull error, not a ranking loss. {_reason[:120]}</p>'
    else:
      bl = _bl_r.get("metrics", {})
      met = _met_r.get("metrics", {})
      dr = _dr_r.get("domain_rating", {}).get("domain_rating")
      metrics.update(backlinks=int(bl.get("live", 0)), refdomains=int(bl.get("live_refdomains", 0)), org_keywords=int(met.get("org_keywords", 0)), dr=dr)
      auth_block = h2("Authority &amp; index &middot; Ahrefs") + "<div>" + (
        card("Domain Rating", dr if dr is not None else "&mdash;", "0-100") + card("Backlinks", f'{int(bl.get("live",0)):,}', "live")
        + card("Ref. domains", bl.get("live_refdomains", "&mdash;"), "sites linking") + card("Organic keywords", met.get("org_keywords", "&mdash;"), f'{met.get("org_keywords_1_3","0")} in top 3')) + "</div>"
except Exception as e:
    auth_block = h2("Authority &middot; Ahrefs") + f'<p style="color:#c0392b">Ahrefs unavailable ({str(e)[:70]}).</p>'

# ===== health =====
health_block = ""
try:
    def fx(p):
        req = urllib.request.Request("https://oconnors.bar" + p, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r: return r.status, r.read().decode("utf-8", "ignore")
    nurls = fx("/sitemap.xml")[1].count("<loc>")
    ok = sum(1 for p in ["/", "/whats-on", "/food-and-drink", "/news", "/contact"] if fx(p)[0] == 200)
    health_block = h2("Site health") + "<div>" + card("Pages live", f"{ok}/5", "key pages 200") + card("Sitemap", nurls, "URLs") + card("Last full audit", "15 Jun", "all green") + "</div>"
except Exception as e:
    health_block = h2("Site health") + f'<p style="color:#c0392b">Health check failed ({str(e)[:70]}).</p>'

shell = lambda body: (f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#202124;line-height:1.5;max-width:780px">{body}'
                      f'<p style="margin:20px 0 0;font-size:11px;color:#8a949e">Sources: Google Search Console, Google Analytics 4, Ahrefs. All targets Playa Blanca specific. Re-pulled weekly (Mondays).</p></div>')

# ----- Overview -----
ov_html = shell(f'<p style="margin:0 0 4px"><b>O\'Connor\'s SEO</b> &middot; week of {monday}</p>'
                f'<p style="margin:0 0 12px;color:#5f6b76;font-size:13px">Arrows compare the last 7 days with the 7 before. Flip between weeks for each week\'s figures; the <b>Trends</b> tab tracks movement over time.</p>'
                + gsc_block + kw_block + ga4_block + auth_block + health_block)
ok1 = cc.publish("oconnors-seo", monday, {"subject": f"O'Connor's SEO &mdash; week of {monday}", "html": ov_html, "data": metrics})

# ----- Trends (reads history, incl. this week) -----
hist = history()
if not any(h.get("date") == monday for h in hist):
    hist = hist + [metrics]
def spark(vals):
    return "  ".join(str(v) for v in vals if v is not None) if vals else "&mdash;"
# headline metric trend table (one row per metric, columns = weeks)
weeks = [h["date"][5:] for h in hist]   # MM-DD
mrows = ""
for label, key, inv in [("Clicks (7d)", "clicks", False), ("Impressions (7d)", "impressions", False),
                        ("Avg position", "avg_position", True), ("Sessions (7d)", "sessions", False),
                        ("Backlinks", "backlinks", False), ("Organic keywords", "org_keywords", False)]:
    cells = "".join(f'<td style="padding:5px 8px;text-align:center">{h.get(key,"&mdash;")}</td>' for h in hist)
    mrows += f'<tr><td style="padding:5px 8px;font-weight:600">{label}</td>{cells}</tr>'
metric_tbl = ('<table style="width:100%;border-collapse:collapse;font-size:13px">'
              + '<tr style="background:#1a3c5e;color:#fff"><th style="padding:6px 8px;text-align:left">Metric</th>'
              + "".join(f'<th style="padding:6px 8px">{w}</th>' for w in weeks) + "</tr>" + mrows + "</table>")
# keyword position trend (one row per target keyword, columns = weeks)
krows = ""
for i, (kw, page, vol) in enumerate(TARGETS):
    cells = ""
    for h in hist:
        p = (h.get("keyword_positions") or {}).get(kw)
        cells += f'<td style="padding:5px 8px;text-align:center">{p if p else "&mdash;"}</td>'
    bg = "#fff" if i % 2 == 0 else "#f4f6f8"
    krows += f'<tr style="background:{bg}"><td style="padding:5px 8px">{kw}</td><td style="padding:5px 8px;color:#5f6b76">{page}</td>{cells}</tr>'
kw_tbl = ('<table style="width:100%;border-collapse:collapse;font-size:12.5px">'
          + '<tr style="background:#1a3c5e;color:#fff"><th style="padding:6px 8px;text-align:left">Keyword</th><th style="padding:6px 8px;text-align:left">Page</th>'
          + "".join(f'<th style="padding:6px 8px">{w}</th>' for w in weeks) + "</tr>" + krows + "</table>")
tr_html = shell(f'<p style="margin:0 0 4px"><b>O\'Connor\'s SEO &middot; movement over time</b></p>'
                f'<p style="margin:0 0 12px;color:#5f6b76;font-size:13px">Each column is a week (w/c, newest right). Lower position = better. Builds up one column per week.</p>'
                + h2("Headline metrics by week") + metric_tbl
                + h2("Target keyword positions by week") + '<p style="font-size:12px;color:#5f6b76;margin:0 0 6px">Google position per target keyword (blank = no impressions that week).</p>' + kw_tbl)
ok2 = cc.publish("oconnors-seo-trends", monday, {"subject": f"O'Connor's SEO trends &mdash; {monday}", "html": tr_html})

print(f"overview={'ok' if ok1 else 'FAIL'} trends={'ok' if ok2 else 'FAIL'} | period {monday} | weeks of history: {len(hist)}")
sys.exit(0 if (ok1 and ok2) else 1)
