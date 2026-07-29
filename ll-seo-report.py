#!/usr/bin/env python3
"""Lanzarote Lates SEO report -> Command Centre (DB-driven; bakes NOTHING).

Executes the plan 'make the CC SEO report DB-driven': every figure, every paragraph and every
table on /m/ll-seo-report comes from a live source at generation time --
  prose + status        <- vault_notes 'Lanzarote Lates -- STATE OF PLAY' (the property SSOT)
  keyword cluster       <- vault_notes 'Lanzarote Lates Website -- SEO targeting registry'
  positions/clicks      <- the CC SEO store (seo_gsc_daily; impression-weighted, GB) -- NOT live GSC
                           (platform principle: read the store, not a paid/limited API; the store is
                           pulled nightly by seo-pull-gsc and was 140d-backfilled 29 Jul 2026)
  shipped work          <- public.work_log (property_name ~ 'Lanzarote Lates')
  authority             <- Ahrefs via the gated ahrefs-api helper (~2 metered calls/run)
Publishes ONE reports.snapshots row per run (report_key='ll-seo-report'); the CC's generic
[slug] renderer displays the newest row per period. Weekly + after any LL ship.

M0 NOTE: run with --preview to write /tmp/ll-seo-report-preview.html and SKIP publishing.
"""
# CRON-META
# what: Lanzarote Lates weekly SEO report (DB-driven; store + SSOT + registry + work_log + Ahrefs)
# why: owner-facing SEO report that cannot drift from the SSOT
# reads: seo_gsc_daily + vault_notes + work_log + Ahrefs (gated)
# writes: reports.snapshots key ll-seo-report -> /m/ll-seo-report
# entity: personal
# report: ll-seo-report
# schedule: 30 8 * * 1
# timezone: Atlantic/Canary
# CRON-META-END
import importlib.util, json, os, re, subprocess, sys, datetime as dt

SC = os.path.dirname(os.path.abspath(__file__))
VAULT = os.environ.get("VAULT", SC)

def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

md_html = _load("md_html", f"{SC}/md_html.py")
PROP = "lanzarote-lates-website"
SITE = "https://www.lanzarotelates.com"

def sql(q):
    r = subprocess.run(["python3", os.path.join(VAULT, "cc-sql.py"), q], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, cwd=VAULT, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"cc-sql failed: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout) if r.stdout.strip() else []

def note_body(title_ilike):
    rows = sql(f"SELECT body FROM vault_notes WHERE title ILIKE $x${title_ilike}$x$ LIMIT 1")
    return rows[0]["body"] if rows else None

def section(body, heading_prefix):
    """Extract one '## heading...' section's markdown (to the next ## or EOF)."""
    m = re.search(rf"^##\s+{re.escape(heading_prefix)}.*?$", body, re.M)
    if not m: return None
    rest = body[m.end():]
    nxt = re.search(r"^##\s+", rest, re.M)
    return rest[: nxt.start() if nxt else len(rest)].strip()

# ---------- house style (shared shapes with the oconnors template) ----------
def card(label, value, sub=""):
    return (f'<div style="display:inline-block;min-width:118px;background:#f4f6f8;border:1px solid #e3e7ea;border-radius:8px;padding:12px 16px;margin:0 8px 8px 0;vertical-align:top">'
            f'<div style="font-size:12px;color:#5f6b76;text-transform:uppercase;letter-spacing:.04em">{label}</div>'
            f'<div style="font-size:24px;font-weight:700;color:#1a3c5e;margin-top:2px">{value}</div>'
            f'<div style="font-size:12px;color:#5f6b76;margin-top:2px">{sub}</div></div>')
def h2(t): return f'<h2 style="font-size:17px;color:#1a3c5e;margin:26px 0 10px;border-bottom:2px solid #1a3c5e;padding-bottom:4px">{t}</h2>'
def th(*c): return '<tr style="background:#1a3c5e;color:#fff">' + ''.join(f'<th style="padding:6px 8px;text-align:{a}">{t}</th>' for t, a in c) + '</tr>'
def trow(i, *cells):
    bg = "#fff" if i % 2 == 0 else "#f4f6f8"
    return f'<tr style="background:{bg}">' + ''.join(f'<td style="padding:5px 8px;text-align:{a}">{v}</td>' for v, a in cells) + '</tr>'
def dlt(now, prev, invert=False):
    try: now = float(now); prev = float(prev)
    except (TypeError, ValueError): return ""
    d = now - prev
    if abs(d) < 0.05: return '<span style="color:#888">&middot;</span>'
    good = (d < 0) if invert else (d > 0)
    arrow = "&darr;" if d < 0 else "&uarr;"; col = "#1a8f3c" if good else "#c0392b"
    return f'<span style="color:{col};font-weight:600">{arrow}{abs(d):.1f}</span>'

# ---------- 1. registry cluster (page jobs; parse the target tables) ----------
def registry_cluster():
    body = note_body("Lanzarote Lates Website%SEO targeting registry")
    if not body: return [], "REGISTRY NOT FOUND"
    terms, seen = [], set()
    for page_m in re.finditer(r"### `(/[^`]*)`", body):
        page = page_m.group(1)
        seg = body[page_m.end(): page_m.end() + 4000]
        for row in re.finditer(r"^\|\s*`?([a-z][a-z0-9 &'-]{3,60})`?\s*\|\s*([\d,~]+)[^|]*\|", seg, re.M):
            kw = row.group(1).strip()
            if kw.lower() in seen or kw.lower() in ("keyword", "term", "field", "page"): continue
            seen.add(kw.lower())
            vol = row.group(2).replace(",", "").replace("~", "")
            terms.append({"keyword": kw, "page": page, "vol": int(vol) if vol.isdigit() else None})
    return terms, None

# ---------- 2. store reads (impression-weighted; NEVER plain averages) ----------
def window(days_back_start, days_back_end):
    return sql(
        f"SELECT SUM(clicks) AS clicks, SUM(impressions) AS impr, "
        f"ROUND((SUM(position*impressions)/NULLIF(SUM(impressions),0))::numeric,1) AS wpos "
        f"FROM seo_gsc_daily WHERE property_key='{PROP}' "
        f"AND date >= CURRENT_DATE - {days_back_start} AND date < CURRENT_DATE - {days_back_end}")[0]

def term_positions(terms):
    if not terms: return {}
    inlist = ",".join("$x$" + t["keyword"] + "$x$" for t in terms)
    rows = sql(
        f"SELECT query, SUM(clicks) AS clicks, SUM(impressions) AS impr, "
        f"ROUND((SUM(position*impressions)/NULLIF(SUM(impressions),0))::numeric,1) AS wpos "
        f"FROM seo_gsc_daily WHERE property_key='{PROP}' AND date >= CURRENT_DATE - 28 "
        f"AND query IN ({inlist}) GROUP BY query")
    return {r["query"].lower(): r for r in rows}

# ---------- 3. authority (gated Ahrefs; ~2 metered calls) ----------
def authority():
    try:
        amod = _load("ahrefs_api", f"{SC}/ahrefs-api.py")
        api = amod.AhrefsAPI(caller="ll-seo-report")
        y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        dr = api.call("site-explorer/domain-rating", {"target": "lanzarotelates.com", "date": y}, property_key=PROP)
        bl = api.call("site-explorer/backlinks-stats", {"target": "lanzarotelates.com", "date": y, "mode": "domain"}, property_key=PROP)
        return {"dr": (dr.get("domain_rating") or {}).get("domain_rating"),
                "backlinks": (bl.get("metrics") or {}).get("live"),
                "refdomains": (bl.get("metrics") or {}).get("live_refdomains")}
    except Exception as e:
        return {"_err": str(e)[:150]}

# ---------- build ----------
def build():
    today = dt.date.today().isoformat()
    ssot = note_body("Lanzarote Lates%STATE OF PLAY%") or ""
    one_liner = section(ssot, "Site in one line")
    verified = section(ssot, "✅ Verified current state") or section(ssot, "Verified current state")

    terms, reg_err = registry_cluster()
    pos = term_positions(terms)
    now28, prev28 = window(28, 0), window(56, 28)
    auth = authority()
    ships = sql("SELECT date, title, outcome FROM work_log WHERE property_name ILIKE '%lanzarote lates%' ORDER BY date DESC LIMIT 12")

    cards = (card("Clicks", int(now28["clicks"] or 0), f'28d &nbsp; {dlt(now28["clicks"], prev28["clicks"])} vs prior 28d')
             + card("Impressions", f'{int(now28["impr"] or 0):,}', f'28d &nbsp; {dlt(now28["impr"], prev28["impr"])}')
             + card("Weighted position", now28["wpos"], f'lower=better &nbsp; {dlt(now28["wpos"], prev28["wpos"], invert=True)}')
             + (card("Domain Rating", auth.get("dr", "?"), f'{auth.get("refdomains","?")} ref. domains') if "_err" not in auth
                else card("Domain Rating", "n/a", "Ahrefs unavailable")))

    kw_rows = ""
    shown = [t for t in terms if t.get("vol")] or terms
    shown = sorted(shown, key=lambda t: -(t.get("vol") or 0))[:20]
    for i, t in enumerate(shown):
        hit = pos.get(t["keyword"].lower())
        p = f"<b>{hit['wpos']}</b>" if hit else '<span style="color:#8a949e">&mdash;</span>'
        impr = f"{int(hit['impr']):,}" if hit else "&mdash;"
        clk = int(hit["clicks"]) if hit else 0
        kw_rows += trow(i, (t["keyword"], "left"), (t["page"], "left"),
                        (f"{t['vol']:,}/mo" if t.get("vol") else "&mdash;", "center"), (p, "center"), (impr, "center"), (clk or "&mdash;", "center"))
    kw_block = (h2("Target keywords &rarr; assigned page (the registry is the law)")
                + '<p style="font-size:13px;color:#5f6b76;margin:0 0 6px">Positions are impression-weighted from Google Search Console (UK), last 28 days, from the CC store. Page assignments come live from the targeting registry.</p>'
                + '<table style="width:100%;border-collapse:collapse;font-size:13px">'
                + th(("Keyword", "left"), ("Assigned page", "left"), ("Searches", "center"), ("Position", "center"), ("Impressions", "center"), ("Clicks", "center"))
                + kw_rows + "</table>")
    if reg_err:
        kw_block = h2("Target keywords") + f'<p style="color:#c0392b">{reg_err} — cluster table unavailable; fix the registry note.</p>'

    ships_rows = "".join(trow(i, (s["date"], "left"), (s["title"], "left"), (s.get("outcome") or "", "center")) for i, s in enumerate(ships))
    ships_block = (h2("Shipped work (live from the Work Log)")
                   + '<table style="width:100%;border-collapse:collapse;font-size:13px">'
                   + th(("Date", "left"), ("What shipped", "left"), ("Outcome", "center")) + ships_rows + "</table>")

    prose_block = ""
    if one_liner:
        prose_block += h2("The property in one line") + md_html.md_to_html(one_liner)
    if verified:
        prose_block += h2("Verified current state (from the property SSOT)") + md_html.md_to_html(verified)

    html_doc = (
        f'<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:860px">'
        f'<p style="font-size:12px;color:#5f6b76;margin:0 0 12px">Generated {today} &middot; every figure and paragraph read live at generation time from: the CC search store (Google UK), the property SSOT note, the targeting registry, the Work Log, Ahrefs. Nothing hand-authored.</p>'
        f'<div>{cards}</div>' + kw_block + ships_block + prose_block + "</div>")

    payload = {
        "meta": {"property": PROP, "generated_at": dt.datetime.utcnow().isoformat() + "Z",
                 "sources": ["seo_gsc_daily", "vault_notes(SSOT)", "seo-targets registry", "work_log", "ahrefs"]},
        "data": {"gsc_28d": dict(now28), "gsc_prev28": dict(prev28), "authority": auth,
                 "cluster": [{**t, **(pos.get(t["keyword"].lower()) or {})} for t in shown]},
        "subject": f"Lanzarote Lates — SEO report, {today}",
        "html": html_doc,
    }
    return payload

if __name__ == "__main__":
    preview = "--preview" in sys.argv
    p = build()
    if preview:
        out = "/tmp/ll-seo-report-preview.html"
        open(out, "w").write(p["html"])
        print(f"PREVIEW written: {out}  (no publish)")
        print(json.dumps(p["data"]["gsc_28d"], indent=1))
        print("authority:", json.dumps(p["data"]["authority"]))
        print("cluster terms parsed:", len(p["data"]["cluster"]))
    else:
        cc = _load("cc_publish", f"{SC}/cc_publish.py")
        monday = (dt.date.today() - dt.timedelta(days=dt.date.today().weekday())).isoformat()
        r = cc.publish("ll-seo-report", monday, p)
        print("published:", r)
