#!/usr/bin/env python3
"""
Sygma daily Google report — emails Pete every morning at 7am with:
- Previous day's call events (date, time, length, source, caller area)
- Google Ads: spend / clicks / impressions / conversions, ad-group + landing-page breakdown
- GA4: sessions / users / conversions, traffic sources, top pages

Usage:
  python3 sygma-daily-google-report.py                # yesterday (default)
  python3 sygma-daily-google-report.py 2026-05-29     # specific date
  python3 sygma-daily-google-report.py --dry-run      # print HTML, do not send

Recipient: pete.ashcroft@sygma-solutions.com
Sender:    pete.ashcroft@sygma-solutions.com (via gmail-api.py DWD)

Data sources:
- Google Ads API (customer 1739090181) via Library/processes/scripts/ads-api.py
- GA4 Data API (property 354127076) via Library/processes/scripts/ga4-api.py
- Gmail send via Library/processes/scripts/gmail-api.py
"""

import importlib.util
import json
import sys
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")
_HERE = os.path.dirname(os.path.abspath(__file__))   # sibling helpers resolve here (flat /app on Railway)
ADS_CUSTOMER = "1739090181"          # Sygma Training, All Courses
GA4_PROPERTY = "354127076"           # Sygma Solutions GA4
RECIPIENT = "pete.ashcroft@sygma-solutions.com"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fmt_duration(seconds):
    s = int(seconds or 0)
    if s < 60:
        return f"{s} seconds"
    mins, secs = divmod(s, 60)
    if secs == 0:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    return f"{mins} minute{'s' if mins != 1 else ''} {secs} second{'s' if secs != 1 else ''}"


def fmt_money(micros):
    return f"£{(int(micros or 0) / 1_000_000):.2f}"


def fmt_pct(num, denom):
    if not denom:
        return "n/a"
    return f"{(num / denom * 100):.2f}%"


def pull_calls(ads, target_date):
    """Pull call events for the target date. call_view doesn't accept segments.date, so we filter in Python."""
    rows = ads.query("""
SELECT call_view.caller_country_code, call_view.caller_area_code,
       call_view.call_duration_seconds, call_view.start_call_date_time,
       call_view.type, call_view.call_tracking_display_location
FROM call_view
""", customer_id=ADS_CUSTOMER)
    target_prefix = target_date.isoformat()
    calls = []
    for r in rows:
        cv = r.get("callView", {})
        ts = cv.get("startCallDateTime", "")
        if not ts.startswith(target_prefix):
            continue
        calls.append({
            "datetime": ts[:19],
            "duration_s": int(cv.get("callDurationSeconds", 0) or 0),
            "source": cv.get("callTrackingDisplayLocation", "?"),
            "type": cv.get("type", "?"),
            "country": cv.get("callerCountryCode", "?"),
            "area": cv.get("callerAreaCode", "?"),
        })
    calls.sort(key=lambda c: c["datetime"])
    return calls


def pull_ads_summary(ads, target_date):
    """Pull yesterday's ads performance."""
    d = target_date.isoformat()
    # Per-campaign
    campaigns = ads.query(f"""
SELECT campaign.name, campaign.status,
       metrics.cost_micros, metrics.clicks, metrics.impressions,
       metrics.conversions, metrics.all_conversions, metrics.phone_calls, metrics.phone_impressions
FROM campaign
WHERE segments.date = '{d}'
""", customer_id=ADS_CUSTOMER)
    # Per-ad-group
    adgroups = ads.query(f"""
SELECT ad_group.name, campaign.name,
       metrics.cost_micros, metrics.clicks, metrics.impressions,
       metrics.conversions
FROM ad_group
WHERE segments.date = '{d}' AND ad_group.status = 'ENABLED'
""", customer_id=ADS_CUSTOMER)
    # Per-landing-page
    landings = ads.query(f"""
SELECT landing_page_view.unexpanded_final_url,
       metrics.cost_micros, metrics.clicks, metrics.conversions
FROM landing_page_view
WHERE segments.date = '{d}'
""", customer_id=ADS_CUSTOMER)
    # Per-conversion-action
    conv_actions = ads.query(f"""
SELECT segments.conversion_action_name, metrics.all_conversions
FROM customer
WHERE segments.date = '{d}'
""", customer_id=ADS_CUSTOMER)
    return {
        "campaigns": campaigns,
        "adgroups": adgroups,
        "landings": landings,
        "conv_actions": conv_actions,
    }


def _retry(fn, max_attempts=4, backoff_seconds=10):
    """Retry-with-backoff for transient Google API 5xx / 429."""
    import time as _t
    for i in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            transient = any(c in msg for c in ["HTTP 502","HTTP 503","HTTP 504","HTTP 429","HTTP 500","Service Unavailable","Bad Gateway"])
            if not transient or i == max_attempts - 1:
                raise
            wait = backoff_seconds * (2 ** i)
            print(f"    transient error ({msg[:80]}), retry {i+1} after {wait}s")
            _t.sleep(wait)


def pull_ga4(ga4, target_date):
    """Pull yesterday's GA4 data using the helper's _call method directly for raw responses."""
    d = target_date.isoformat()
    date_ranges = [{"startDate": d, "endDate": d}]
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport"

    _c = lambda body: _retry(lambda: ga4._call("POST", url, body=body))
    summary = _c({
        "dateRanges": date_ranges,
        "metrics": [
            {"name": "sessions"}, {"name": "totalUsers"}, {"name": "screenPageViews"},
            {"name": "engagedSessions"}, {"name": "conversions"},
            {"name": "averageSessionDuration"}, {"name": "bounceRate"},
        ],
    })
    sources = _c({
        "dateRanges": date_ranges,
        "dimensions": [{"name": "sessionDefaultChannelGroup"}, {"name": "sessionSource"}],
        "metrics": [{"name": "sessions"}, {"name": "conversions"}],
        "limit": 10,
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    })
    pages = _c({
        "dateRanges": date_ranges,
        "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": "screenPageViews"}, {"name": "sessions"}, {"name": "conversions"}],
        "limit": 15,
        "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
    })
    events = _c({
        "dateRanges": date_ranges,
        "dimensions": [{"name": "eventName"}],
        "metrics": [{"name": "eventCount"}],
        "dimensionFilter": {"filter": {"fieldName": "eventName", "inListFilter": {
            "values": ["form_submit", "phone_click", "email_click", "chat_started", "thank_you", "generate_lead"]
        }}},
        "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}],
    })
    return {"summary": summary, "sources": sources, "pages": pages, "events": events}


def build_html(target_date, calls, ads_data, ga4_data):
    d_long = target_date.strftime("%A %-d %B %Y")
    d_short = target_date.strftime("%a %-d %b")

    # ---- Calls section ----
    if calls:
        call_rows = "".join(
            f"<tr><td style='padding:6px 8px'>{c['datetime'][11:16]}</td>"
            f"<td style='padding:6px 8px'>{fmt_duration(c['duration_s'])}</td>"
            f"<td style='padding:6px 8px'>{'<b>DNI swap on the website</b>' if c['source'] == 'LANDING_PAGE' else 'Tap on ad call asset'}</td>"
            f"<td style='padding:6px 8px'>+{c['country']} area {c['area']}</td></tr>"
            for c in calls
        )
        call_html = f"""
<h3 style="color:#1a3a5c;margin-top:24px">Calls received yesterday: {len(calls)}</h3>
<table style="border-collapse:collapse;width:100%;font-size:14px">
<thead><tr style="background:#f4f6f8;border-bottom:2px solid #1a3a5c">
<th style="padding:6px 8px;text-align:left">Time</th>
<th style="padding:6px 8px;text-align:left">Duration</th>
<th style="padding:6px 8px;text-align:left">Source</th>
<th style="padding:6px 8px;text-align:left">Caller</th>
</tr></thead>
<tbody>{call_rows}</tbody>
</table>
<p style="font-size:13px;color:#666;margin-top:4px"><b>DNI (Dynamic Number Insertion)</b> calls came via the website. Ask office who took the call at the time shown.</p>
"""
    else:
        call_html = f'<h3 style="color:#1a3a5c;margin-top:24px">Calls received yesterday: 0</h3>'

    # ---- Ads summary ----
    camp_tot = {"cost": 0, "clicks": 0, "impr": 0, "conv": 0.0, "all_conv": 0.0, "phone_calls": 0, "phone_impr": 0}
    for c in ads_data["campaigns"]:
        mm = c.get("metrics", {})
        camp_tot["cost"] += int(mm.get("costMicros", 0) or 0)
        camp_tot["clicks"] += int(mm.get("clicks", 0) or 0)
        camp_tot["impr"] += int(mm.get("impressions", 0) or 0)
        camp_tot["conv"] += float(mm.get("conversions", 0) or 0)
        camp_tot["all_conv"] += float(mm.get("allConversions", 0) or 0)
        camp_tot["phone_calls"] += int(mm.get("phoneCalls", 0) or 0)
        camp_tot["phone_impr"] += int(mm.get("phoneImpressions", 0) or 0)

    ads_summary = f"""
<h3 style="color:#1a3a5c;margin-top:24px">Google Ads, {d_short}</h3>
<table style="border-collapse:collapse;width:100%;font-size:14px">
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Spend</th>
    <td style="padding:6px 8px">{fmt_money(camp_tot['cost'])}</td>
    <th style="padding:6px 8px;background:#f4f6f8;text-align:left">Conversions</th>
    <td style="padding:6px 8px">{camp_tot['conv']:.1f} primary / {camp_tot['all_conv']:.1f} all</td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Clicks</th>
    <td style="padding:6px 8px">{camp_tot['clicks']}</td>
    <th style="padding:6px 8px;background:#f4f6f8;text-align:left">CPC</th>
    <td style="padding:6px 8px">{fmt_money(camp_tot['cost']/camp_tot['clicks'] if camp_tot['clicks'] else 0)}</td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Impressions</th>
    <td style="padding:6px 8px">{camp_tot['impr']}</td>
    <th style="padding:6px 8px;background:#f4f6f8;text-align:left">Phone impr / calls</th>
    <td style="padding:6px 8px">{camp_tot['phone_impr']} / {camp_tot['phone_calls']}</td></tr>
</table>
"""

    # Per-ad-group
    ag_rows = ""
    for ag in sorted(ads_data["adgroups"], key=lambda a: int(a.get("metrics", {}).get("costMicros", 0) or 0), reverse=True)[:10]:
        agn = ag.get("adGroup", {}).get("name", "")
        cpn = ag.get("campaign", {}).get("name", "")
        mm = ag.get("metrics", {})
        cost = fmt_money(mm.get("costMicros", 0))
        clicks = mm.get("clicks", "0")
        conv = float(mm.get("conversions", 0) or 0)
        if int(mm.get("costMicros", 0) or 0) > 0 or float(conv) > 0:
            ag_rows += f"<tr><td style='padding:6px 8px'>{agn}</td><td style='padding:6px 8px;text-align:right'>{cost}</td><td style='padding:6px 8px;text-align:right'>{clicks}</td><td style='padding:6px 8px;text-align:right'>{conv:.1f}</td></tr>"
    if ag_rows:
        ads_summary += f"""
<p style="margin-top:16px"><b>By ad group</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Ad group</th>
<th style="padding:6px 8px;text-align:right">Spend</th><th style="padding:6px 8px;text-align:right">Clicks</th>
<th style="padding:6px 8px;text-align:right">Conv</th></tr></thead>
<tbody>{ag_rows}</tbody></table>
"""

    # Per-landing-page
    lp_rows = ""
    for lp in sorted(ads_data["landings"], key=lambda a: int(a.get("metrics", {}).get("costMicros", 0) or 0), reverse=True)[:10]:
        url = lp.get("landingPageView", {}).get("unexpandedFinalUrl", "")
        path = "/" + url.split("/", 3)[-1] if url.startswith("http") and "/" in url[8:] else url
        mm = lp.get("metrics", {})
        cost = fmt_money(mm.get("costMicros", 0))
        clicks = mm.get("clicks", "0")
        conv = float(mm.get("conversions", 0) or 0)
        if int(mm.get("costMicros", 0) or 0) > 0 or float(conv) > 0:
            lp_rows += f"<tr><td style='padding:6px 8px'>{path}</td><td style='padding:6px 8px;text-align:right'>{cost}</td><td style='padding:6px 8px;text-align:right'>{clicks}</td><td style='padding:6px 8px;text-align:right'>{conv:.1f}</td></tr>"
    if lp_rows:
        ads_summary += f"""
<p style="margin-top:16px"><b>By landing page</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Page</th>
<th style="padding:6px 8px;text-align:right">Spend</th><th style="padding:6px 8px;text-align:right">Clicks</th>
<th style="padding:6px 8px;text-align:right">Conv</th></tr></thead>
<tbody>{lp_rows}</tbody></table>
"""

    # Per-conversion-action
    ca_rows = ""
    for ca in sorted(ads_data["conv_actions"], key=lambda a: float(a.get("metrics", {}).get("allConversions", 0) or 0), reverse=True):
        nm = ca.get("segments", {}).get("conversionActionName", "")
        ac = float(ca.get("metrics", {}).get("allConversions", 0) or 0)
        if ac > 0:
            ca_rows += f"<tr><td style='padding:6px 8px'>{nm}</td><td style='padding:6px 8px;text-align:right'>{ac:.1f}</td></tr>"
    if ca_rows:
        ads_summary += f"""
<p style="margin-top:16px"><b>By conversion type</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Conversion action</th>
<th style="padding:6px 8px;text-align:right">Count</th></tr></thead>
<tbody>{ca_rows}</tbody></table>
"""

    # ---- GA4 summary ----
    ga_metrics = (ga4_data["summary"].get("rows", []) or [{}])[0].get("metricValues", [])
    if ga_metrics:
        sessions = int(float(ga_metrics[0].get("value", "0")))
        users = int(float(ga_metrics[1].get("value", "0")))
        pageviews = int(float(ga_metrics[2].get("value", "0")))
        engaged = int(float(ga_metrics[3].get("value", "0")))
        conversions = float(ga_metrics[4].get("value", "0"))
        avg_sess = float(ga_metrics[5].get("value", "0"))
        bounce = float(ga_metrics[6].get("value", "0"))
    else:
        sessions = users = pageviews = engaged = 0
        conversions = avg_sess = bounce = 0

    ga4_summary = f"""
<h3 style="color:#1a3a5c;margin-top:24px">GA4, {d_short}</h3>
<table style="border-collapse:collapse;width:100%;font-size:14px">
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Sessions</th>
    <td style="padding:6px 8px">{sessions}</td>
    <th style="padding:6px 8px;background:#f4f6f8;text-align:left">Users</th>
    <td style="padding:6px 8px">{users}</td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Pageviews</th>
    <td style="padding:6px 8px">{pageviews}</td>
    <th style="padding:6px 8px;background:#f4f6f8;text-align:left">Conversions</th>
    <td style="padding:6px 8px">{conversions:.0f}</td></tr>
<tr><th style="padding:6px 8px;background:#f4f6f8;text-align:left">Engaged sessions</th>
    <td style="padding:6px 8px">{engaged}</td>
    <th style="padding:6px 8px;background:#f4f6f8;text-align:left">Bounce / avg session</th>
    <td style="padding:6px 8px">{bounce*100:.0f}% / {int(avg_sess//60)}m {int(avg_sess%60)}s</td></tr>
</table>
"""

    # Traffic sources
    src_rows = ""
    for row in ga4_data["sources"].get("rows", [])[:8]:
        dims = row.get("dimensionValues", [])
        mets = row.get("metricValues", [])
        channel = dims[0].get("value", "") if dims else ""
        source = dims[1].get("value", "") if len(dims) > 1 else ""
        s = mets[0].get("value", "0") if mets else "0"
        c = float(mets[1].get("value", "0")) if len(mets) > 1 else 0
        src_rows += f"<tr><td style='padding:6px 8px'>{channel} / {source}</td><td style='padding:6px 8px;text-align:right'>{s}</td><td style='padding:6px 8px;text-align:right'>{c:.0f}</td></tr>"
    if src_rows:
        ga4_summary += f"""
<p style="margin-top:16px"><b>Traffic sources</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Channel / source</th>
<th style="padding:6px 8px;text-align:right">Sessions</th><th style="padding:6px 8px;text-align:right">Conv</th></tr></thead>
<tbody>{src_rows}</tbody></table>
"""

    # Top pages
    page_rows = ""
    for row in ga4_data["pages"].get("rows", [])[:10]:
        dims = row.get("dimensionValues", [])
        mets = row.get("metricValues", [])
        path = dims[0].get("value", "") if dims else ""
        pv = mets[0].get("value", "0") if mets else "0"
        s = mets[1].get("value", "0") if len(mets) > 1 else "0"
        c = float(mets[2].get("value", "0")) if len(mets) > 2 else 0
        page_rows += f"<tr><td style='padding:6px 8px'>{path}</td><td style='padding:6px 8px;text-align:right'>{pv}</td><td style='padding:6px 8px;text-align:right'>{s}</td><td style='padding:6px 8px;text-align:right'>{c:.0f}</td></tr>"
    if page_rows:
        ga4_summary += f"""
<p style="margin-top:16px"><b>Top pages</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Path</th>
<th style="padding:6px 8px;text-align:right">Views</th><th style="padding:6px 8px;text-align:right">Sessions</th>
<th style="padding:6px 8px;text-align:right">Conv</th></tr></thead>
<tbody>{page_rows}</tbody></table>
"""

    # Conversion events
    ev_rows = ""
    for row in ga4_data["events"].get("rows", []):
        nm = row.get("dimensionValues", [{}])[0].get("value", "")
        n = row.get("metricValues", [{}])[0].get("value", "0")
        ev_rows += f"<tr><td style='padding:6px 8px'>{nm}</td><td style='padding:6px 8px;text-align:right'>{n}</td></tr>"
    if ev_rows:
        ga4_summary += f"""
<p style="margin-top:16px"><b>Conversion events</b></p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f4f6f8"><th style="padding:6px 8px;text-align:left">Event</th>
<th style="padding:6px 8px;text-align:right">Count</th></tr></thead>
<tbody>{ev_rows}</tbody></table>
"""

    # ---- Assemble ----
    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#333;max-width:760px">
<h2 style="color:#1a3a5c">Sygma daily Google report</h2>
<p style="color:#666">For <b>{d_long}</b>. Sources: Google Ads (advertiser 1739090181), GA4 (property 354127076).</p>
{call_html}
{ads_summary}
{ga4_summary}
<hr style="margin-top:32px;border:0;border-top:1px solid #ddd">
<p style="font-size:12px;color:#999">Auto-generated by sygma-daily-google-report.py. Runs daily at 07:00 Atlantic/Canary. To change, edit Library/processes/scripts/sygma-daily-google-report.py.</p>
</body></html>"""


def build_subject(target_date, calls, ads_data, ga4_data):
    d_short = target_date.strftime("%a %-d %b")
    n_calls = len(calls)
    spend_micros = sum(int(c.get("metrics", {}).get("costMicros", 0) or 0) for c in ads_data["campaigns"])
    sessions = 0
    rows = ga4_data["summary"].get("rows", [])
    if rows and rows[0].get("metricValues"):
        sessions = int(float(rows[0]["metricValues"][0].get("value", "0")))
    conv = float(rows[0]["metricValues"][4].get("value", "0")) if rows and len(rows[0].get("metricValues", [])) >= 5 else 0
    return f"Sygma Google • {d_short} • {n_calls} call{'s' if n_calls != 1 else ''} · {fmt_money(spend_micros)} spend · {sessions} sessions · {int(conv)} conv"


def main(argv):
    dry_run = "--dry-run" in argv
    date_args = [a for a in argv[1:] if not a.startswith("--")]
    if date_args:
        target_date = date.fromisoformat(date_args[0])
    else:
        target_date = date.today() - timedelta(days=1)

    print(f"Building report for {target_date.isoformat()}")

    ads = _load("ads_api", f"{_HERE}/ads-api.py").GoogleAdsAPI()
    ga4 = _load("ga4_api", f"{_HERE}/ga4-api.py").GA4API()

    calls = pull_calls(ads, target_date)
    print(f"  {len(calls)} call event(s)")
    ads_data = pull_ads_summary(ads, target_date)
    print(f"  {len(ads_data['campaigns'])} campaign(s), {len(ads_data['adgroups'])} ad group(s), {len(ads_data['landings'])} landing page(s)")
    ga4_data = pull_ga4(ga4, target_date)
    rows = ga4_data["summary"].get("rows", [])
    sessions = int(float(rows[0]["metricValues"][0]["value"])) if rows else 0
    print(f"  GA4: {sessions} sessions")

    html = build_html(target_date, calls, ads_data, ga4_data)
    subject = build_subject(target_date, calls, ads_data, ga4_data)
    print(f"  Subject: {subject}")
    print(f"  HTML body: {len(html)} chars")

    # Voice rule: outbound texts must be em/en/double-dash free.
    assert html.count("—") == 0, "em dash leak"
    assert " -- " not in html, "double dash leak"
    # en-dash check (— vs – — used by build_html in two places intentionally, both must be replaced with em-equivalent visuals?)
    # We deliberately use plain ASCII characters in this script. The "—" we send is in the subject builder only
    # if any. Above check covers that.

    if dry_run:
        out = f"/tmp/sygma-daily-google-report-{target_date.isoformat()}.html"
        with open(out, "w") as f:
            f.write(html)
        print(f"  DRY RUN: html written to {out}")
        return

    gmail = _load("gmail_api", f"{_HERE}/gmail-api.py").GmailAPI()
    r = gmail.send(
        to=RECIPIENT,
        subject=subject,
        body=html,
        html=html,
    )
    print(f"  SENT  id={r.get('id')}")
    # --- Command Centre publish (P5, 2026-06-11): snapshot to reports.snapshots; the email above is unchanged. Non-fatal.
    try:
        import importlib.util as _il, datetime as _dt
        _spec = _il.spec_from_file_location("cc_publish", "/tmp/pbs/Library/processes/scripts/cc_publish.py")
        _cc = _il.module_from_spec(_spec); _spec.loader.exec_module(_cc)
        _cc.publish("sygma-google-daily", _dt.date.today().isoformat(), {"subject": subject, "html": html})
        print("  CC: snapshot published")
    except Exception as _e:
        print(f"  CC PUBLISH FAILED: {_e}")



if __name__ == "__main__":
    main(sys.argv)
