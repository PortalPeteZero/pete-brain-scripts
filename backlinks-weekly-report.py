#!/usr/bin/env python3
"""Sygma Backlinks — weekly effectiveness report → Command Centre.

Composes a weekly snapshot from the bl.work_items ledger (the single source of truth for
every backlink action — Appear Online placements, our earned links, Jane's directory work)
and publishes it to reports.snapshots key `backlinks-weekly` (period = week-ending Sunday).
Feeds the Sygma Backlinks page (Weekly tab) at commandcentre.info/m/sygma-backlinks.

The work log is maintained manually (Jane's "Claude - Backlinks Sygma" intake + Claude-at-
filing of Appear Online emails). EXTENSION POINT: pull Ahrefs all-backlinks first_seen to
auto-flip live→crawled, Rank Tracker for movement, GSC/GA4 for the target pages — see
[[Projects/SY-Website/backlinks/files/cc-backlinks-module-plan-2026-06-12]]. Helper-first
(ahrefs-api.py / gsc + ga4 helpers) when added.

Run standalone any time; the Monday cron runs it for the just-ended week.
"""
# CRON-META
# what: Sygma backlinks weekly effectiveness report
# why: weekly visibility on Appear Online's off-site backlink work (Sygma)
# reads: bl.work_items (CC)
# writes: reports.snapshots key backlinks-weekly (CC) -> /m/sygma-backlinks
# entity: sygma
# report: sygma-backlinks
# schedule: 45 8 * * 1
# timezone: Atlantic/Canary
# CRON-META-END
import json, os, urllib.request, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# $VAULT-aware (set by railway-bootstrap on the cloud); falls back to the vault layout locally.
_SECRETS = (Path(os.environ["VAULT"]) / "Library/processes/secrets") if os.environ.get("VAULT") else (SCRIPT_DIR.parent / "secrets")
KEYS = json.load(open(_SECRETS / "command-centre-supabase-keys.json"))
SRK = KEYS["service_role_key"]; BASE = KEYS["url"] + "/rest/v1"

# The shared live tracker (SSOT for Appear Online placements; mirrored into bl.work_items by bl-sheet-sync).
# Routing: Library/processes/seo/sygma-backlink-tracker-routing.md
TRACKER_URL = "https://docs.google.com/spreadsheets/d/1XMMJDuvx95K2nV-jE7VjAr3UnaE1pSd-OnhpXZsigCM/edit"

def _work_items():
    req = urllib.request.Request(f"{BASE}/work_items?select=*&order=date.desc",
        headers={"apikey": SRK, "Authorization": f"Bearer {SRK}", "Accept-Profile": "bl"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def _week_ending(d=None):
    d = d or datetime.date.today()
    # Sunday of the just-completed week
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7 or 7)

# --- "Is it benefiting?" — target-page rank movement (added 2026-07-06) ------------
# A placement isn't done when it's live; it has to move the page it points at. For every
# target page our links point to, pull its GSC position now vs the prior 28d so a live link
# visibly turns into a ranking gain. Degrades gracefully — a GSC outage must not break the report.
GSC_SITE = "sc-domain:sygma-solutions.com"
TARGET_PAGE_MAP = {
    "/": "/", "homepage": "/",
    "cat and genny training": "/courses/cat-and-genny-training",
    "cable avoidance training": "/courses/cable-avoidance-training",
    "eusr cat1": "/courses/eusr-cat1", "eusr cat 1": "/courses/eusr-cat1",
    "hsg47 training": "/courses/hsg47-training", "hsg47": "/courses/hsg47-training",
}
def _norm_target(tp):
    if not tp:
        return None
    key = tp.strip().lower()
    if key in TARGET_PAGE_MAP:
        return TARGET_PAGE_MAP[key]
    return key if key.startswith("/") else None  # drop unmapped labels

def _load_gsc():
    try:
        import importlib
        return importlib.import_module("gsc-api").GSCAPI()
    except Exception:
        return None

def _benefit(items):
    ranked = ("live", "crawled", "counted")
    pending = ("submitted", "approved", "review", "proposed")
    pages = {}
    for i in items:
        n = _norm_target(i.get("target_page"))
        if not n:
            continue
        g = pages.setdefault(n, {"live": 0, "pending": 0})
        if i["status"] in ranked:
            g["live"] += 1
        elif i["status"] in pending:
            g["pending"] += 1
    gsc = _load_gsc()
    out = []
    for page, cnt in sorted(pages.items(), key=lambda kv: -(kv[1]["live"] + kv[1]["pending"])):
        row = {"page": page, "live_links": cnt["live"], "pending_links": cnt["pending"],
               "position_now": None, "position_prev": None,
               "clicks_now": None, "clicks_prev": None, "trend": "n/a"}
        if gsc:
            try:
                c = gsc.compare_page(GSC_SITE, page, 28, 28)
                tp = c.get("this") or {}
                pv = c.get("prev") or {}
                row["position_now"], row["position_prev"] = tp.get("position"), pv.get("position")
                row["clicks_now"], row["clicks_prev"] = tp.get("clicks"), pv.get("clicks")
                pn, pp = row["position_now"], row["position_prev"]
                if pn is not None and pp is not None:
                    row["trend"] = "improving" if pn < pp - 0.4 else "slipping" if pn > pp + 0.4 else "flat"
            except Exception:
                row["trend"] = "unavailable"
        out.append(row)
    return out

def build(items, week_end):
    wk_start = week_end - datetime.timedelta(days=6)
    ranked = ("live", "crawled", "counted")
    live = [i for i in items if i["status"] in ranked]
    crawled = [i for i in items if i["status"] in ("crawled", "counted")]
    new_this_week = [i for i in items if i.get("date") and wk_start.isoformat() <= i["date"] <= week_end.isoformat()]
    from collections import Counter
    by_status = Counter(i["status"] for i in items)
    def row(i):
        pub = i['publisher']
        if i.get('article_url'):
            pub += f" — <a href='{i['article_url']}' style='color:#2563eb'>{i.get('title') or 'published article'} ↗</a>"
        return (f"<tr><td style='padding:6px 9px;border:1px solid #e2e6f0'>{pub}</td>"
                f"<td style='padding:6px 9px;border:1px solid #e2e6f0'>{i.get('dr') or '—'}</td>"
                f"<td style='padding:6px 9px;border:1px solid #e2e6f0'>{i.get('target_page') or '—'}</td>"
                f"<td style='padding:6px 9px;border:1px solid #e2e6f0'>{i['status']}</td></tr>")
    live_rows = "".join(row(i) for i in live) or "<tr><td colspan=4 style='padding:6px 9px;border:1px solid #e2e6f0;color:#888'>none yet</td></tr>"
    new_rows = "".join(f"<li>{i['publisher']} — {i['status']} ({i.get('actor')})</li>" for i in new_this_week) or "<li>no new actions logged this week</li>"
    # "Is it benefiting?" — target-page rank movement
    benefit = _benefit(items)
    def brow(b):
        arrows = {"improving": "▲", "slipping": "▼", "flat": "→"}
        colors = {"improving": "#16a34a", "slipping": "#dc2626", "flat": "#64748b"}
        if b["position_now"] is not None:
            prev = f" <span style='color:#94a3b8'>(was {b['position_prev']:.1f})</span>" if b["position_prev"] is not None else ""
            pos = f"<span style='color:{colors.get(b['trend'], '#64748b')};font-weight:600'>{b['position_now']:.1f} {arrows.get(b['trend'], '')}</span>{prev}"
        else:
            pos = "<span style='color:#94a3b8'>no data</span>" if b["trend"] != "unavailable" else "<span style='color:#94a3b8'>GSC unavailable</span>"
        clicks = f"{b['clicks_now']}" + (f" <span style='color:#94a3b8'>(was {b['clicks_prev']})</span>" if b["clicks_now"] is not None and b["clicks_prev"] is not None else "") if b["clicks_now"] is not None else "—"
        links = f"{b['live_links']} live" + (f" · {b['pending_links']} pending" if b["pending_links"] else "")
        cell = "padding:6px 9px;border:1px solid #e2e6f0"
        return (f"<tr><td style='{cell}'>{b['page']}</td><td style='{cell}'>{links}</td>"
                f"<td style='{cell}'>{pos}</td><td style='{cell}'>{clicks}</td></tr>")
    benefit_rows = "".join(brow(b) for b in benefit) or "<tr><td colspan=4 style='padding:6px 9px;border:1px solid #e2e6f0;color:#888'>no target pages tracked yet</td></tr>"
    html = (f"<div style='font:14px/1.6 -apple-system,Segoe UI,sans-serif;padding:18px;color:#0b1220'>"
            f"<h2 style='margin:0 0 4px'>Backlinks — week ending {week_end:%-d %b %Y}</h2>"
            f"<p style='color:#16a34a;font-weight:600;margin:0 0 6px'>{len(crawled)} crawled &amp; counting · {len(live)} live or better · {by_status.get('approved',0)} approved · {by_status.get('proposed',0)} proposed.</p>"
            f"<p style='margin:0 0 12px'>📊 <a href='{TRACKER_URL}' style='color:#2563eb;font-weight:600'>Open the live Backlink Placement Tracker ↗</a> <span style='color:#94a3b8'>(shared with Appear Online; this page mirrors it, refreshed daily).</span></p>"
            f"<h3 style='margin:14px 0 4px;color:#1B2340'>Live placements — published articles</h3>"
            f"<table style='width:100%;border-collapse:collapse;font-size:13px;background:#fff'>"
            f"<tr style='background:#f8fafc'><td style='padding:6px 9px;border:1px solid #e2e6f0'><b>Publisher / article</b></td><td style='padding:6px 9px;border:1px solid #e2e6f0'><b>DR</b></td><td style='padding:6px 9px;border:1px solid #e2e6f0'><b>Target</b></td><td style='padding:6px 9px;border:1px solid #e2e6f0'><b>Status</b></td></tr>{live_rows}</table>"
            f"<h3 style='margin:16px 0 4px;color:#1B2340'>Is it benefiting? — target-page search rank (28d vs prior 28d)</h3>"
            f"<table style='width:100%;border-collapse:collapse;font-size:13px;background:#fff'>"
            f"<tr style='background:#f8fafc'><td style='padding:6px 9px;border:1px solid #e2e6f0'><b>Target page</b></td><td style='padding:6px 9px;border:1px solid #e2e6f0'><b>Links</b></td><td style='padding:6px 9px;border:1px solid #e2e6f0'><b>Avg position</b></td><td style='padding:6px 9px;border:1px solid #e2e6f0'><b>Clicks</b></td></tr>{benefit_rows}</table>"
            f"<p style='color:#94a3b8;font-size:12px;margin:4px 0 0'>Lower position = higher on Google. ▲ improving · ▼ slipping · → flat. This is where the deep-link strategy proves out.</p>"
            f"<h3 style='margin:16px 0 4px;color:#1B2340'>New / changed this week</h3><ul style='margin:0'>{new_rows}</ul>"
            f"<p style='color:#94a3b8;font-size:12px;margin:14px 0 0'>From the bl.work_items ledger ({len(items)} actions). Baseline: 0 external backlinks to target pages at the 11 May audit.</p></div>")
    # Structured data so the Command Centre can render the Weekly tab NATIVELY (no iframe) with
    # the shared status badges + app theme. The HTML above stays for the email; `data` powers the app.
    def item_d(i):
        return {"publisher": i["publisher"], "article_url": i.get("article_url"), "title": i.get("title"),
                "dr": i.get("dr"), "target_page": i.get("target_page"), "status": i["status"],
                "actor": i.get("actor"), "date": i.get("date")}
    data = {
        "week_end": week_end.isoformat(),
        "tracker_url": TRACKER_URL,
        "summary": {"crawled": len(crawled), "live": len(live),
                    "approved": by_status.get("approved", 0), "proposed": by_status.get("proposed", 0),
                    "submitted": by_status.get("submitted", 0), "review": by_status.get("review", 0),
                    "total": len(items)},
        "live": [item_d(i) for i in live],
        "new_this_week": [item_d(i) for i in new_this_week],
        "benefit": benefit,
    }
    return html, data

def main():
    items = _work_items()
    week_end = _week_ending()
    html, data = build(items, week_end)
    spec_path = SCRIPT_DIR / "cc_publish.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("cc_publish", str(spec_path))
    cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
    ok = cc.publish("backlinks-weekly", week_end.isoformat(), {"subject": f"Sygma backlinks — week ending {week_end:%-d %b %Y}", "html": html, "data": data})
    print(f"CC: backlinks-weekly {'published' if ok else 'FAILED'} (week ending {week_end}, {len(items)} work items)")

if __name__ == "__main__":
    main()
