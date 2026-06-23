#!/usr/bin/env python3
"""
Lanzarote Lates daily owner report — emails Pete + Dave + Arabella every morning at 6am.

**Reporting window**: T-2 (two calendar days back). GA4 needs ~24-48h to finish processing
engagement, source attribution and event counts; querying at 06:00 of D-1 ("yesterday")
returned wildly wrong engagedSessions/bounceRate/Unassigned-source numbers. Shifting to D-2
gives ~30h+ of settling time. Booking-activity diff also moves to T-2 vs T-1 saved state.
Site health, upcoming arrivals are real-time.

The cron records SuperControl availability daily and diffs saved-state-on-saved-state
(target_date.json vs target_date+1.json) instead of "live now vs yesterday's saved", because
the diff needs to match the GA4 reporting window.

Contents:
- At a glance (headline)
- Booking activity: SuperControl calendar diff for the reporting day
- Upcoming arrivals (next 14 days from today, by villa)
- Tracked enquiry signals from GA4 (currently no custom form-submit event wired; expect 0s
  until phone_click / email_click / generate_lead / contact-form-submit triggers exist)
- Website traffic: sessions, users, pages viewed, engaged sessions, bounce rate, devices,
  countries, traffic sources, top pages
- Google Search performance: last 7 days from GSC (its own 3-day lag preserved)
- Site health: live HTTP checks on key URLs + SuperControl marker check on sample villas

Recipients: pete.ashcroft@sygma-solutions.com · david@mvplanzarote.com · arabella@lanzarotelates.com
Sender:     pete.ashcroft@sygma-solutions.com (via gmail-api.py DWD)

Usage:
  python3 lanza-lates-daily-report.py                   # T-2 (default)
  python3 lanza-lates-daily-report.py 2026-05-29        # specific reporting day
  python3 lanza-lates-daily-report.py --dry-run         # print HTML to /tmp, do not send

Data sources:
- GA4 Data API (property 539604544)         via ga4-api.py
- GSC API (https://www.lanzarotelates.com/) via gsc-api.py
- SuperControl public XML API (siteID 373)  via direct urllib
- Gmail send                                 via gmail-api.py
- Cloudflare Web Analytics (RUM API)        via direct urllib if token works

State (for SC calendar diff):
  ~/Library/Application Support/Claude/lanza-lates-daily/sc-state-{YYYY-MM-DD}.json
  One file per day. Today's run saves today's snapshot; the diff for the T-2 reporting
  day loads state(target_date) and state(target_date+1) from the saved files.
"""

# CRON-META
# what: Lanzarote Lates daily owner report (availability diff + bookings + enquiry signals + site health)
# why: morning visibility for the holiday-let owners on availability changes and enquiry signals
# reads: SuperControl XML API (siteID 373), GA4 (539604544), GSC, live site HTTP checks
# writes: HTML email to owners (LANZA_LIVE-gated to Pete until verified); day-over-day state in CC cron_state
# entity: lanza-lates
# schedule: 0 6 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import importlib.util
import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

VAULT = "/Users/peterashcroft/Second Brain"
GA4_PROPERTY = "539604544"
GSC_SITE = "https://www.lanzarotelates.com/"
SC_SITE_ID = "373"
GA4_SETTLE_DAYS = 2
# Send-gate: until LANZA_LIVE=1 the report routes to Pete only, so a migration/verification run never
# reaches David/Arabella. Flip it on once Pete's eyeballed a cloud run.
RECIPIENTS = (
    ["pete.ashcroft@sygma-solutions.com", "David@mvplanzarote.com", "arabella@lanzarotelates.com"]
    if os.environ.get("LANZA_LIVE") == "1"
    else ["pete.ashcroft@sygma-solutions.com"]
)
# Cron memory now lives in the CC (public.cron_state) so the cloud container keeps its day-over-day
# SuperControl snapshots across runs (a wiped local dir would lose the availability diff).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from cron_state import get_state as _cs_get, set_state as _cs_set


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- HTTP helper -----------------------------------------------------------

def _get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0 LL-Daily"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _retry_call(fn, max_attempts=4, backoff_seconds=10):
    """Wrap any callable in retry-with-backoff for transient 5xx/4xx-rate errors from Google APIs."""
    import time as _t
    last = None
    for i in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            msg = str(e)
            transient = any(code in msg for code in ["HTTP 502", "HTTP 503", "HTTP 504", "HTTP 429", "HTTP 500", "Service Unavailable", "Bad Gateway"])
            if not transient or i == max_attempts - 1:
                raise
            wait = backoff_seconds * (2 ** i)
            print(f"    transient error ({msg[:80]}), retry {i+1}/{max_attempts-1} after {wait}s")
            _t.sleep(wait)
    raise last  # unreachable


# --- SuperControl ----------------------------------------------------------

def sc_pull_inventory():
    """Return {propertycode: {'lastupdate': dt, ...}}"""
    raw = _get(f"https://api.supercontrol.co.uk/xml/filter3.asp?siteID={SC_SITE_ID}&propertycode_only=1")
    root = ET.fromstring(raw)
    out = {}
    for p in root.findall(".//property"):
        code = p.find("propertycode").text
        lu_raw = p.find("lastupdate").text
        try:
            lu = datetime.strptime(lu_raw, "%d/%m/%Y %H:%M:%S")
        except Exception:
            lu = None
        photolu_el = p.find("photolastupdate")
        photolu_raw = (photolu_el.text if photolu_el is not None else "") or ""
        try:
            photolu = datetime.strptime(photolu_raw, "%d/%m/%Y %H:%M:%S")
        except Exception:
            photolu = None
        out[code] = {"lastupdate": lu, "photolastupdate": photolu, "raw_lu": lu_raw}
    return out


def sc_fetch_property_name(code):
    try:
        raw = _get(f"https://api.supercontrol.co.uk/xml/property_xml.asp?id={code}&siteID={SC_SITE_ID}&striphtml=1")
        r = ET.fromstring(raw)
        name_el = r.find(".//propertyname")
        return name_el.text if name_el is not None else code
    except Exception:
        return code


def sc_fetch_avail(code, start_date, end_date):
    """Return {date: status}"""
    url = f"https://api.supercontrol.co.uk/xml/property_avail.asp?propertycode={code}&startdate={start_date}&enddate={end_date}"
    try:
        raw = _get(url)
        r = ET.fromstring(raw)
        return {d.text: d.get("status") for d in r.findall(".//date")}
    except Exception:
        return {}


def sc_snapshot_today(properties, max_props=37, lookahead_days=180):
    """Snapshot availability for each property. Persisted via sc_save_state(today, ...) so
    future runs can load it as part of their T-2 diff window."""
    today = date.today()
    end = today + timedelta(days=lookahead_days)
    snap = {}
    for code in list(properties.keys())[:max_props]:
        snap[code] = sc_fetch_avail(code, today.isoformat(), end.isoformat())
    return snap


def sc_save_state(d, snapshot):
    _cs_set("lanza-lates", f"sc-{d.isoformat()}", snapshot)


def sc_load_state(d):
    return _cs_get("lanza-lates", f"sc-{d.isoformat()}", default=None)


def sc_calendar_diff(prev_snap, curr_snap):
    """Compare two SuperControl availability snapshots and return per-property changes.

    Called with prev_snap = state(target_date) and curr_snap = state(target_date+1) so
    the diff captures activity on the reporting day. Both snapshots came from previous
    runs (saved by sc_save_state) so the dates have a stable lookahead window.

    IMPORTANT: only diff dates that exist in BOTH snapshots. The two snapshots cover
    different 180-day windows (target_date+180d vs target_date+1+180d), so dates at
    the edges that exist in only one snapshot are window-shift artifacts, NOT real bookings.
    """
    changes = []
    if not prev_snap:
        return changes
    for code, prev_dates in prev_snap.items():
        curr_dates = curr_snap.get(code, {})
        newly_booked = []
        newly_available = []
        # Intersection only — dates that exist in BOTH snapshots
        overlap = set(prev_dates.keys()) & set(curr_dates.keys())
        for d in sorted(overlap):
            prev_status = prev_dates[d]
            curr_status = curr_dates[d]
            if prev_status == curr_status:
                continue
            if prev_status == "Available" and curr_status != "Available":
                newly_booked.append(d)
            elif prev_status != "Available" and curr_status == "Available":
                newly_available.append(d)
        if newly_booked or newly_available:
            changes.append({
                "code": code,
                "newly_booked": newly_booked,
                "newly_available": newly_available,
            })
    return changes


def sc_upcoming_arrivals(properties, days=14, max_props=37):
    """For each property, find the next arrival date (if any) within N days."""
    today = date.today()
    end = today + timedelta(days=days)
    upcoming = []
    for code in list(properties.keys())[:max_props]:
        avail = sc_fetch_avail(code, today.isoformat(), end.isoformat())
        # An "arrival" is a Booked day preceded by an Available (or boundary) day
        sorted_dates = sorted(avail.keys())
        first_booked = None
        for i, d in enumerate(sorted_dates):
            if avail[d] != "Available":
                # Check if previous day was Available (arrival point)
                if i == 0 or avail[sorted_dates[i - 1]] == "Available":
                    first_booked = d
                    break
        if first_booked:
            upcoming.append({"code": code, "arrival": first_booked})
    return sorted(upcoming, key=lambda x: x["arrival"])


# --- GA4 -------------------------------------------------------------------

def ga4_pull(ga4, target_date):
    d = target_date.isoformat()
    date_ranges = [{"startDate": d, "endDate": d}]
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport"

    def _call(body):
        return _retry_call(lambda: ga4._call("POST", url, body=body))

    out = {}
    out["summary"] = _call({
        "dateRanges": date_ranges,
        "metrics": [
            {"name": "sessions"}, {"name": "totalUsers"}, {"name": "newUsers"},
            {"name": "screenPageViews"}, {"name": "engagedSessions"},
            {"name": "averageSessionDuration"}, {"name": "bounceRate"},
        ],
    })
    out["sources"] = _call({
        "dateRanges": date_ranges,
        "dimensions": [{"name": "sessionDefaultChannelGroup"}, {"name": "sessionSource"}],
        "metrics": [{"name": "sessions"}],
        "limit": 10,
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    })
    out["pages"] = _call({
        "dateRanges": date_ranges,
        "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": "screenPageViews"}, {"name": "sessions"}],
        "limit": 15,
        "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
    })
    out["countries"] = _call({
        "dateRanges": date_ranges,
        "dimensions": [{"name": "country"}],
        "metrics": [{"name": "sessions"}, {"name": "totalUsers"}],
        "limit": 10,
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    })
    out["devices"] = _call({
        "dateRanges": date_ranges,
        "dimensions": [{"name": "deviceCategory"}],
        "metrics": [{"name": "sessions"}, {"name": "screenPageViews"}],
    })
    # Top-30 events: used for context (form_submit guard, breakdown table)
    out["events"] = _call({
        "dateRanges": date_ranges,
        "dimensions": [{"name": "eventName"}],
        "metrics": [{"name": "eventCount"}],
        "limit": 30,
        "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}],
    })
    # Targeted enquiry events: query the exact set we count as enquiries so low-volume
    # signals (e.g. a single phone_click) are not missed if the day has >30 distinct events.
    enquiry_event_names = ["phone_click", "email_click", "chat_started", "generate_lead", "thank_you"]
    out["enquiry_events_exact"] = _call({
        "dateRanges": date_ranges,
        "dimensions": [{"name": "eventName"}],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {
            "orGroup": {"expressions": [
                {"filter": {"fieldName": "eventName", "stringFilter": {"value": nm}}}
                for nm in enquiry_event_names
            ]}
        },
    })
    return out


# --- GSC -------------------------------------------------------------------

def gsc_pull(gsc, end_date, days=7):
    """GSC has 2-3 day lag, so we look at the trailing 7 days ending 3 days before target."""
    end = end_date - timedelta(days=3)
    start = end - timedelta(days=days - 1)
    out = {}
    try:
        out["site_totals"] = gsc.query(
            site=GSC_SITE, dimensions=[], start_date=start.isoformat(), end_date=end.isoformat()
        )
        out["queries"] = gsc.query(
            site=GSC_SITE, dimensions=["query"],
            start_date=start.isoformat(), end_date=end.isoformat(), limit=15,
        )
        out["pages"] = gsc.query(
            site=GSC_SITE, dimensions=["page"],
            start_date=start.isoformat(), end_date=end.isoformat(), limit=15,
        )
        out["window"] = f"{start.isoformat()} to {end.isoformat()}"
    except Exception as e:
        out["error"] = str(e)
    return out


# --- Site health -----------------------------------------------------------

def site_health():
    key_urls = {
        "Homepage": "https://www.lanzarotelates.com/",
        "Villas listing": "https://www.lanzarotelates.com/accommodation/",
        "Booking thanks (post-payment landing)": "https://www.lanzarotelates.com/booking-thanks/",
        "Playa Blanca area page": "https://www.lanzarotelates.com/playa-blanca/",
        "Lanzarote guide": "https://www.lanzarotelates.com/lanzarote-guide/",
        "FAQs": "https://www.lanzarotelates.com/faqs/",
    }
    results = {}
    for name, url in key_urls.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 LL-Daily-Health"})
            with urllib.request.urlopen(req, timeout=15) as r:
                results[name] = {"status": r.status, "size": len(r.read())}
        except Exception as e:
            results[name] = {"status": "ERR", "error": str(e)[:80]}
    # SC marker check on 2 villas (proves SuperControl scripts still loading)
    sc_check = {}
    for slug in ["casa-calma", "villa-grace"]:
        url = f"https://www.lanzarotelates.com/accommodation/{slug}/"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
                body = r.read().decode("utf-8", errors="ignore")
                sc_check[slug] = {
                    "embed.js": body.count("embed.js"),
                    "se=55492151": body.count("se=55492151"),
                }
        except Exception as e:
            sc_check[slug] = {"error": str(e)[:80]}
    return {"pages": results, "sc_markers": sc_check}


# --- HTML builder ----------------------------------------------------------

def fmt_pct(n, d):
    if not d:
        return "n/a"
    return f"{(n / d * 100):.1f}%"


def fmt_int(v):
    try:
        return f"{int(float(v)):,}"
    except Exception:
        return str(v)


def humanise_date(s):
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        return d.strftime("%a %-d %b")
    except Exception:
        return s


def build_html(target_date, sc_changes, upcoming, ga4_data, gsc_data, health):
    d_long = target_date.strftime("%A %-d %B %Y")
    d_short = target_date.strftime("%a %-d %b")

    # Headline metrics
    rows = ga4_data["summary"].get("rows", [])
    summary_metrics = rows[0].get("metricValues", []) if rows else []
    sessions = int(float(summary_metrics[0].get("value", "0"))) if summary_metrics else 0
    users = int(float(summary_metrics[1].get("value", "0"))) if summary_metrics else 0
    new_users = int(float(summary_metrics[2].get("value", "0"))) if summary_metrics else 0
    pageviews = int(float(summary_metrics[3].get("value", "0"))) if summary_metrics else 0
    engaged = int(float(summary_metrics[4].get("value", "0"))) if summary_metrics else 0
    avg_sess = float(summary_metrics[5].get("value", "0")) if summary_metrics else 0
    bounce = float(summary_metrics[6].get("value", "0")) if summary_metrics else 0

    # Booking activity headline
    new_bookings_count = sum(1 for c in sc_changes if c.get("newly_booked"))
    total_newly_booked_nights = sum(len(c.get("newly_booked", [])) for c in sc_changes)

    # Enquiry count from GA4 events — use the targeted exact-filter query (catches low-volume
    # signals that fall outside the top-30 events list when the site has many distinct events)
    enquiry_events = ["phone_click", "email_click", "chat_started", "generate_lead"]
    enquiries_exact = {}
    for row in ga4_data.get("enquiry_events_exact", {}).get("rows", []):
        nm = row.get("dimensionValues", [{}])[0].get("value", "")
        cnt = int(float(row.get("metricValues", [{}])[0].get("value", "0")))
        enquiries_exact[nm] = cnt
    enquiries = sum(enquiries_exact.get(nm, 0) for nm in enquiry_events)

    # Top-30 events breakdown — used for the form_submit guard + general debug display
    event_breakdown = []
    for row in ga4_data["events"].get("rows", []):
        nm = row.get("dimensionValues", [{}])[0].get("value", "")
        cnt = int(float(row.get("metricValues", [{}])[0].get("value", "0")))
        event_breakdown.append((nm, cnt))

    # Site health flag
    health_ok = all(p.get("status") == 200 for p in health["pages"].values()) and all(
        m.get("embed.js", 0) >= 1 and m.get("se=55492151", 0) >= 1 for m in health["sc_markers"].values()
    )

    # --- Headline ---
    headline_parts = []
    if total_newly_booked_nights:
        headline_parts.append(f"<b>{total_newly_booked_nights}</b> new booked nights")
    elif sc_changes:
        headline_parts.append(f"{len(sc_changes)} calendar updates")
    headline_parts.append(f"<b>{enquiries}</b> tracked enquiry signals")
    headline_parts.append(f"<b>{sessions}</b> visits ({users} people)")

    health_pill = (
        '<span style="background:#dff7e0;color:#1a6a1f;padding:2px 8px;border-radius:10px;font-size:12px">All systems normal</span>'
        if health_ok else
        '<span style="background:#fde2e2;color:#a02020;padding:2px 8px;border-radius:10px;font-size:12px">Issue detected (see Site health below)</span>'
    )

    html = f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#333;max-width:760px">
<h2 style="color:#1a3a5c;margin-bottom:4px">Lanzarote Lates daily snapshot</h2>
<p style="color:#666;margin-top:0">For <b>{d_long}</b>. {health_pill}</p>

<div style="background:#f4f6f8;padding:14px 18px;border-radius:8px;margin:16px 0">
<p style="margin:0 0 8px 0;font-size:15px"><b>At a glance:</b> {' · '.join(headline_parts)}.</p>
<p style="margin:0;font-size:12px;color:#666">We report two days back so Google Analytics has finished processing the data. Numbers here are settled and will not change. Site health and upcoming arrivals are live.</p>
</div>
"""

    # --- 1. Booking activity ---
    html += '<h3 style="color:#1a3a5c">Booking activity</h3>'
    if not sc_changes:
        html += f'<p>No new bookings detected on {d_short}.</p>'
    else:
        rows_html = ""
        for c in sc_changes:
            code = c["code"]
            name = sc_fetch_property_name(code)
            nb = c.get("newly_booked", [])
            na = c.get("newly_available", [])
            cells = []
            if nb:
                first = humanise_date(nb[0])
                last = humanise_date(nb[-1])
                if len(nb) == 1:
                    cells.append(f"<b>Booked</b>: {first} ({len(nb)} night)")
                else:
                    cells.append(f"<b>Booked</b>: {first} to {last} ({len(nb)} nights)")
            if na:
                cells.append(f"<i>Freed up</i>: {len(na)} night(s)")
            rows_html += f"<tr><td style='padding:6px 8px;font-weight:600'>{name}</td><td style='padding:6px 8px'>{' · '.join(cells)}</td></tr>"
        html += f"""
<table style="border-collapse:collapse;width:100%;font-size:14px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Villa</th>
<th style="padding:6px 8px;text-align:left">Calendar change</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
"""

    # --- 2. Upcoming arrivals ---
    html += '<h3 style="color:#1a3a5c;margin-top:24px">Upcoming arrivals (next 14 days)</h3>'
    if not upcoming:
        html += '<p>No new arrivals in the next 14 days.</p>'
    else:
        rows_html = ""
        for u in upcoming[:15]:
            name = sc_fetch_property_name(u["code"])
            rows_html += f"<tr><td style='padding:6px 8px'>{humanise_date(u['arrival'])}</td><td style='padding:6px 8px'>{name}</td></tr>"
        html += f"""
<table style="border-collapse:collapse;width:100%;font-size:14px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Arrival</th>
<th style="padding:6px 8px;text-align:left">Villa</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
"""

    # --- 3. Website enquiries / conversion events ---
    html += '<h3 style="color:#1a3a5c;margin-top:24px">Website enquiries &amp; key interactions</h3>'
    interesting_events = {
        "phone_click": "Phone number clicked",
        "email_click": "Email address clicked",
        "chat_started": "Chat started",
        "generate_lead": "Lead generated",
        "thank_you": "Thank-you page viewed (post-enquiry)",
    }
    # Use the exact-filter results (catches low-volume signals), fall back to top-30 breakdown
    seen_events = {nm: enquiries_exact.get(nm, 0) for nm in interesting_events if enquiries_exact.get(nm, 0) > 0}
    html += '<p style="font-size:13px;color:#666;margin-top:0">Counts of tracked enquiry signals only. The Kadence contact form on /contact-us/ does not yet fire a custom GA4 event on submit, so genuine enquiries are not yet measurable in Analytics. Real enquiries land in arabella@lanzarotelates.com directly. Wiring a custom event is on the to-do list.</p>'
    if seen_events:
        rows_html = ""
        for nm, label in interesting_events.items():
            cnt = seen_events.get(nm, 0)
            if cnt > 0:
                rows_html += f"<tr><td style='padding:6px 8px'>{label}</td><td style='padding:6px 8px;text-align:right'><b>{cnt}</b></td></tr>"
        html += f"""
<table style="border-collapse:collapse;width:100%;font-size:14px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Interaction</th>
<th style="padding:6px 8px;text-align:right">Count</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
"""
    else:
        html += f'<p>No tracked enquiry signals on {d_short}.</p>'

    # --- 4. Website traffic ---
    html += '<h3 style="color:#1a3a5c;margin-top:24px">Website traffic</h3>'
    html += f"""
<table style="border-collapse:collapse;width:100%;font-size:14px">
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left;width:30%">Visitors (users)</th>
    <td style="padding:6px 8px">{users} <span style="color:#666">({new_users} new)</span></td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Sessions (visits)</th>
    <td style="padding:6px 8px">{sessions}</td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Pages viewed</th>
    <td style="padding:6px 8px">{pageviews}</td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Average visit length</th>
    <td style="padding:6px 8px">{int(avg_sess//60)}m {int(avg_sess%60)}s</td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Engaged sessions</th>
    <td style="padding:6px 8px">{engaged} ({fmt_pct(engaged, sessions)})</td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Bounce rate</th>
    <td style="padding:6px 8px">{bounce*100:.0f}%</td></tr>
</table>
"""

    # Devices breakdown
    devices = ga4_data["devices"].get("rows", [])
    if devices:
        rows_html = ""
        for row in devices:
            dev = row.get("dimensionValues", [{}])[0].get("value", "")
            s = row.get("metricValues", [{}])[0].get("value", "0")
            rows_html += f"<tr><td style='padding:6px 8px'>{dev.title()}</td><td style='padding:6px 8px;text-align:right'>{s}</td></tr>"
        html += f"""
<p style="margin-top:16px"><b>Device split</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Device</th>
<th style="padding:6px 8px;text-align:right">Sessions</th></tr></thead>
<tbody>{rows_html}</tbody></table>
"""

    # Countries
    countries = ga4_data["countries"].get("rows", [])
    if countries:
        rows_html = ""
        for row in countries[:8]:
            ct = row.get("dimensionValues", [{}])[0].get("value", "")
            s = row.get("metricValues", [{}])[0].get("value", "0")
            u = row.get("metricValues", [{}])[1].get("value", "0") if len(row.get("metricValues", [])) > 1 else "0"
            rows_html += f"<tr><td style='padding:6px 8px'>{ct}</td><td style='padding:6px 8px;text-align:right'>{s}</td><td style='padding:6px 8px;text-align:right'>{u}</td></tr>"
        html += f"""
<p style="margin-top:16px"><b>Top countries</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Country</th>
<th style="padding:6px 8px;text-align:right">Sessions</th><th style="padding:6px 8px;text-align:right">Users</th></tr></thead>
<tbody>{rows_html}</tbody></table>
"""

    # Traffic sources
    sources = ga4_data["sources"].get("rows", [])
    if sources:
        rows_html = ""
        for row in sources[:8]:
            dims = row.get("dimensionValues", [])
            channel = dims[0].get("value", "") if dims else ""
            source = dims[1].get("value", "") if len(dims) > 1 else ""
            s = row.get("metricValues", [{}])[0].get("value", "0")
            rows_html += f"<tr><td style='padding:6px 8px'>{channel}</td><td style='padding:6px 8px'>{source}</td><td style='padding:6px 8px;text-align:right'>{s}</td></tr>"
        html += f"""
<p style="margin-top:16px"><b>Top traffic sources</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Channel</th>
<th style="padding:6px 8px;text-align:left">Source</th><th style="padding:6px 8px;text-align:right">Sessions</th></tr></thead>
<tbody>{rows_html}</tbody></table>
"""

    # Top pages
    pages = ga4_data["pages"].get("rows", [])
    if pages:
        rows_html = ""
        for row in pages[:10]:
            path = row.get("dimensionValues", [{}])[0].get("value", "")
            pv = row.get("metricValues", [{}])[0].get("value", "0")
            s = row.get("metricValues", [{}])[1].get("value", "0") if len(row.get("metricValues", [])) > 1 else "0"
            rows_html += f"<tr><td style='padding:6px 8px'>{path}</td><td style='padding:6px 8px;text-align:right'>{pv}</td><td style='padding:6px 8px;text-align:right'>{s}</td></tr>"
        html += f"""
<p style="margin-top:16px"><b>Top pages visited</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Page</th>
<th style="padding:6px 8px;text-align:right">Views</th><th style="padding:6px 8px;text-align:right">Sessions</th></tr></thead>
<tbody>{rows_html}</tbody></table>
"""

    # --- 5. Search performance ---
    html += '<h3 style="color:#1a3a5c;margin-top:24px">Google search performance (last 7 days)</h3>'
    html += f'<p style="color:#666;font-size:13px">Window: {gsc_data.get("window","n/a")}. Google Search Console data has a 2 to 3 day delay so this is the most recent 7-day window available.</p>'
    site_totals = gsc_data.get("site_totals", []) or []
    if site_totals:
        t = site_totals[0]
        html += f"""
<table style="border-collapse:collapse;width:100%;font-size:14px">
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Clicks from Google</th>
    <td style="padding:6px 8px"><b>{int(t.get('clicks',0))}</b></td>
    <th style="padding:6px 8px;background:#f4f6f8;text-align:left">Impressions</th>
    <td style="padding:6px 8px">{int(t.get('impressions',0)):,}</td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Click-through rate</th>
    <td style="padding:6px 8px">{t.get('ctr',0)*100:.2f}%</td>
    <th style="padding:6px 8px;background:#f4f6f8;text-align:left">Average position</th>
    <td style="padding:6px 8px">{t.get('position',0):.1f}</td></tr>
</table>
"""
        # Top queries
        queries = gsc_data.get("queries", [])[:10]
        if queries:
            rows_html = ""
            for q in queries:
                rows_html += f"<tr><td style='padding:6px 8px'>{q.get('keys',['?'])[0]}</td><td style='padding:6px 8px;text-align:right'>{int(q.get('clicks',0))}</td><td style='padding:6px 8px;text-align:right'>{int(q.get('impressions',0)):,}</td><td style='padding:6px 8px;text-align:right'>{q.get('position',0):.1f}</td></tr>"
            html += f"""
<p style="margin-top:16px"><b>Top search terms</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Search term</th>
<th style="padding:6px 8px;text-align:right">Clicks</th><th style="padding:6px 8px;text-align:right">Impressions</th>
<th style="padding:6px 8px;text-align:right">Avg position</th></tr></thead>
<tbody>{rows_html}</tbody></table>
"""
        # Top pages from GSC
        gsc_pages = gsc_data.get("pages", [])[:10]
        if gsc_pages:
            rows_html = ""
            for p in gsc_pages:
                url = p.get('keys',['?'])[0]
                path = url.replace("https://www.lanzarotelates.com", "") or "/"
                rows_html += f"<tr><td style='padding:6px 8px'>{path}</td><td style='padding:6px 8px;text-align:right'>{int(p.get('clicks',0))}</td><td style='padding:6px 8px;text-align:right'>{int(p.get('impressions',0)):,}</td><td style='padding:6px 8px;text-align:right'>{p.get('position',0):.1f}</td></tr>"
            html += f"""
<p style="margin-top:16px"><b>Top pages in Google search</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Page</th>
<th style="padding:6px 8px;text-align:right">Clicks</th><th style="padding:6px 8px;text-align:right">Impressions</th>
<th style="padding:6px 8px;text-align:right">Avg position</th></tr></thead>
<tbody>{rows_html}</tbody></table>
"""
    else:
        html += '<p>No search performance data available.</p>'

    # --- 6. Site health ---
    html += '<h3 style="color:#1a3a5c;margin-top:24px">Site health</h3>'
    rows_html = ""
    for name, r in health["pages"].items():
        ok = r.get("status") == 200
        badge = '<span style="color:#1a6a1f">OK</span>' if ok else f'<span style="color:#a02020">Status {r.get("status")}</span>'
        rows_html += f"<tr><td style='padding:6px 8px'>{name}</td><td style='padding:6px 8px'>{badge}</td></tr>"
    html += f"""
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Page</th>
<th style="padding:6px 8px;text-align:left">Status</th></tr></thead>
<tbody>{rows_html}</tbody></table>
"""
    # SC marker confirm
    sc_html = ""
    for slug, m in health["sc_markers"].items():
        if m.get("embed.js", 0) >= 1 and m.get("se=55492151", 0) >= 1:
            sc_html += f"<li>{slug}: SuperControl scripts present (booking calendar will render)</li>"
        else:
            sc_html += f"<li><b>{slug}: PROBLEM</b> with SuperControl scripts (embed.js={m.get('embed.js',0)}, se={m.get('se=55492151',0)})</li>"
    html += f"<p style='margin-top:12px'><b>SuperControl booking integration:</b><ul>{sc_html}</ul></p>"

    # --- Footer ---
    html += f"""
<hr style="margin-top:32px;border:0;border-top:1px solid #ddd">
<p style="font-size:12px;color:#999">
Auto-generated for <b>www.lanzarotelates.com</b>. Recipients: Pete (Sygma Solutions, SEO) · Dave (MVP Group) · Arabella (owner).
<br><br>
<b>Data windows in this report:</b>
<br>Booking activity + website traffic + enquiry signals = the reporting day ({d_short}, two calendar days back, GA4 data fully settled).
<br>Upcoming arrivals + site health = real time, captured at send.
<br>Google Search performance = a separate 7-day window ending three days before today (Google Search Console always carries a 2 to 3 day delay).
<br><br>
Sources: SuperControl public XML (siteID 373), Google Analytics 4 (property 539604544), Google Search Console (www.lanzarotelates.com), live HTTP checks.
<br>Daily at 06:00 Atlantic/Canary time. To stop the email, reply to this thread and ask Pete to disable.
</p>
</body></html>"""

    return html


def build_subject(target_date, sc_changes, ga4_data, enquiries):
    d_short = target_date.strftime("%a %-d %b")
    rows = ga4_data["summary"].get("rows", [])
    sessions = int(float(rows[0]["metricValues"][0].get("value", "0"))) if rows else 0
    nights = sum(len(c.get("newly_booked", [])) for c in sc_changes)
    parts = []
    if nights:
        parts.append(f"{nights}n booked")
    parts.append(f"{enquiries} tracked enquiries")
    parts.append(f"{sessions} visits")
    return f"Lanzarote Lates • {d_short} • " + " · ".join(parts)


def main(argv):
    dry_run = "--dry-run" in argv
    date_args = [a for a in argv[1:] if not a.startswith("--")]
    target_date = date.fromisoformat(date_args[0]) if date_args else date.today() - timedelta(days=GA4_SETTLE_DAYS)
    today = date.today()

    print(f"Building Lanzarote Lates daily report for {target_date.isoformat()} (reporting day; today is {today.isoformat()})")

    # ---- 1. SuperControl: take today's snapshot for future runs, then diff target_date vs target_date+1 from saved state ----
    print("  [1/5] SuperControl inventory + availability...")
    properties = sc_pull_inventory()
    print(f"    {len(properties)} properties")
    today_snap = sc_snapshot_today(properties)
    sc_save_state(today, today_snap)
    print(f"    Saved today's snapshot: cron_state lanza-lates/sc-{today.isoformat()}")
    prev_snap = sc_load_state(target_date)
    curr_snap_for_diff = sc_load_state(target_date + timedelta(days=1))
    if prev_snap and curr_snap_for_diff:
        sc_changes = sc_calendar_diff(prev_snap, curr_snap_for_diff)
        print(f"    {len(sc_changes)} properties with calendar changes on {target_date.isoformat()}")
    else:
        sc_changes = []
        print(f"    No state files available for the T-{GA4_SETTLE_DAYS} window (prev={bool(prev_snap)}, curr={bool(curr_snap_for_diff)}); skipping booking diff")
    upcoming = sc_upcoming_arrivals(properties, days=14)
    print(f"    {len(upcoming)} upcoming arrivals in next 14d")

    # ---- 2. GA4 ----
    print("  [2/5] GA4...")
    ga4 = _load("ga4_api", os.path.join(_HERE, "ga4-api.py")).GA4API()
    ga4_data = ga4_pull(ga4, target_date)
    rows = ga4_data["summary"].get("rows", [])
    sessions = int(float(rows[0]["metricValues"][0]["value"])) if rows else 0
    print(f"    {sessions} sessions")

    # ---- 3. GSC ----
    print("  [3/5] GSC...")
    gsc = _load("gsc_api", os.path.join(_HERE, "gsc-api.py")).GSCAPI()
    gsc_data = gsc_pull(gsc, target_date, days=7)
    site_totals = gsc_data.get("site_totals", []) or []
    if site_totals:
        print(f"    {int(site_totals[0].get('clicks',0))} clicks / {int(site_totals[0].get('impressions',0))} impressions over the 7d window")
    else:
        print(f"    no GSC data (error: {gsc_data.get('error','?')[:80]})")

    # ---- 4. Site health ----
    print("  [4/5] Site health...")
    health = site_health()
    all_ok = all(p.get("status") == 200 for p in health["pages"].values())
    print(f"    Pages all 200: {all_ok}")

    # ---- Compute enquiries ---- (mirrors build_html — uses targeted filter, not top-30)
    enquiry_events = ["phone_click", "email_click", "chat_started", "generate_lead"]
    enquiries_exact = {}
    for row in ga4_data.get("enquiry_events_exact", {}).get("rows", []):
        nm = row.get("dimensionValues", [{}])[0].get("value", "")
        cnt = int(float(row.get("metricValues", [{}])[0].get("value", "0")))
        enquiries_exact[nm] = cnt
    enquiries = sum(enquiries_exact.get(nm, 0) for nm in enquiry_events)
    print(f"    {enquiries} tracked enquiry signals on {target_date.isoformat()}: {enquiries_exact}")

    # ---- Check for form_submit Enhanced Measurement regression ----
    form_submit_count = 0
    for row in ga4_data["events"].get("rows", []):
        if row.get("dimensionValues", [{}])[0].get("value", "") == "form_submit":
            form_submit_count = int(float(row.get("metricValues", [{}])[0].get("value", "0")))
            break
    if form_submit_count > 5:
        print(f"    WARNING: form_submit fired {form_submit_count} times on {target_date.isoformat()} — Enhanced Measurement may have been re-enabled or a new GTM tag is firing it. Check GA4 Admin > Data Stream > Enhanced Measurement > Form interactions.")

    # ---- 5. Build + send ----
    print("  [5/5] Build email...")
    html = build_html(target_date, sc_changes, upcoming, ga4_data, gsc_data, health)
    subject = build_subject(target_date, sc_changes, ga4_data, enquiries)
    print(f"    Subject: {subject}")
    print(f"    HTML: {len(html)} chars")

    assert html.count("—") == 0, "em dash leak"
    assert html.count("–") == 0, "en dash leak"
    assert " -- " not in html, "double dash leak"

    # Sanity guard. The only abort condition is mathematically-impossible enquiry rates: enquiries
    # over 50% of sessions implies every visitor enquired multiple times, which can only happen if
    # the event filter is leaking. (form_submit is NOT in enquiry_events any more, so historical
    # high form_submit counts are noise, not a problem — but we still warn so future sessions know
    # Enhanced Measurement crept back on.)
    if sessions > 0 and enquiries > sessions * 0.5:
        msg = f"SANITY GUARD: enquiries={enquiries} > 50% of sessions ({sessions}) — impossible conversion rate. Aborting send."
        print(f"  {msg}")
        if not dry_run:
            raise SystemExit(msg)

    if dry_run:
        out = f"/tmp/lanza-lates-daily-{target_date.isoformat()}.html"
        with open(out, "w") as f:
            f.write(html)
        print(f"  DRY RUN: html written to {out}")
        return

    gmail = _load("gmail_api", os.path.join(_HERE, "gmail-api.py")).GmailAPI()
    r = gmail.send(
        to=",".join(RECIPIENTS),
        subject=subject,
        body=html,
        html=html,
    )
    print(f"  SENT to {len(RECIPIENTS)} recipients  id={r.get('id')}")
    # --- Command Centre publish (P5, 2026-06-11): snapshot to reports.snapshots; the email above is unchanged. Non-fatal.
    try:
        import importlib.util as _il, datetime as _dt
        _spec = _il.spec_from_file_location("cc_publish", os.path.join(_HERE, "cc_publish.py"))
        _cc = _il.module_from_spec(_spec); _spec.loader.exec_module(_cc)
        _cc.publish("ll-owner-report", _dt.date.today().isoformat(), {"subject": subject, "html": html})
        print("  CC: snapshot published")
    except Exception as _e:
        print(f"  CC PUBLISH FAILED: {_e}")



if __name__ == "__main__":
    main(sys.argv)
