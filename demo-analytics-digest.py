#!/usr/bin/env python3
"""demo-analytics-digest — daily email of how the public LeakGuard demo was used.

Reads the LeakGuard `demo_analytics` table (first-party, service-role only) and emails Pete a plain
digest: visits + uniques, where they came from, the engagement funnel (arrived -> toured -> asked the
AI -> ran a report -> left an email), leads captured with their emails, and today vs the 7-day average.

Cron: daily 07:00 Atlantic/Canary. Default computes + emails; pass --dry to print only.
"""
# CRON-META
# what: Daily digest of public LeakGuard demo usage, emailed to Pete
# why: See how many prospects tried the demo, from where, what they did, and who left an email
# reads: LeakGuard demo_analytics (service role)
# writes: email to Pete
# entity: CD-LeakGuard
# report:
# schedule: 0 6 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import os
import sys
import json
import datetime
import importlib.util
import urllib.request
from collections import Counter
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_VAULT = os.environ.get("VAULT", "/tmp/pbs")
SECRETS = os.path.join(_VAULT, "Library", "processes", "secrets")
LG_URL = "https://uuhzjytscifrpuqpfrdc.supabase.co"
TO = "pete.ashcroft@sygma-solutions.com"
SEND = "--dry" not in sys.argv and "--dry-run" not in sys.argv and "--no-send" not in sys.argv


def _service_key():
    p = os.path.join(SECRETS, "leakguard-service-key")
    return open(p).read().strip()


def _get(path):
    key = _service_key()
    req = urllib.request.Request(
        f"{LG_URL}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=45).read())


def _rows_since(iso):
    """All analytics rows since `iso`, paged (PostgREST caps at 1000)."""
    out, offset = [], 0
    while True:
        req = urllib.request.Request(
            f"{LG_URL}/rest/v1/demo_analytics?select=*&created_at=gte.{iso}&order=created_at.asc",
            headers={"apikey": _service_key(), "Authorization": f"Bearer {_service_key()}",
                     "Range-Unit": "items", "Range": f"{offset}-{offset+999}"},
        )
        batch = json.loads(urllib.request.urlopen(req, timeout=45).read())
        out.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return out


def _sessions_with(rows, event):
    return {r["session_id"] for r in rows if r["event"] == event and r.get("session_id")}


def _domain(url):
    try:
        d = urlparse(url).netloc
        return d or "(direct)"
    except Exception:
        return "(direct)"


def build(now):
    day_ago = (now - datetime.timedelta(hours=24)).isoformat()
    week_ago = (now - datetime.timedelta(days=7)).isoformat()
    week_rows = _rows_since(week_ago)
    day_rows = [r for r in week_rows if r["created_at"] >= day_ago]

    visits = [r for r in day_rows if r["event"] == "visit_start"]
    sessions = {r["session_id"] for r in day_rows if r.get("session_id")}

    # Funnel (distinct sessions reaching each stage, over the day)
    arrived = _sessions_with(day_rows, "visit_start") or sessions
    toured = _sessions_with(day_rows, "tour_step")
    asked = _sessions_with(day_rows, "ai_question")
    reported = _sessions_with(day_rows, "report_generated")
    leads = _sessions_with(day_rows, "lead_captured")

    # Sources / geo / device
    referrers = Counter(_domain(r.get("referrer") or "") for r in visits)
    utms = Counter(r.get("utm_source") for r in visits if r.get("utm_source"))
    countries = Counter(r.get("country") for r in visits if r.get("country"))
    devices = Counter(r.get("device_type") for r in visits if r.get("device_type"))

    # Lead emails
    lead_emails = []
    for r in day_rows:
        if r["event"] == "lead_captured":
            e = (r.get("event_data") or {}).get("email")
            if e:
                lead_emails.append(e)

    # Day vs 7-day average visits
    total_week_visits = len([r for r in week_rows if r["event"] == "visit_start"])
    avg_daily = round(total_week_visits / 7, 1)

    def top(counter, n=5):
        items = [f"{k}: {v}" for k, v in counter.most_common(n)]
        return items or ["(none)"]

    def ul(items):
        return "<ul style='margin:4px 0'>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"

    pct = lambda part, whole: f"{round(100*len(part)/len(whole))}%" if whole else "0%"

    html = f"""<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:640px;color:#1e293b">
<h2 style="color:#0891b2;margin-bottom:2px">LeakGuard demo, last 24 hours</h2>
<p style="color:#64748b;margin-top:0">To {now.strftime('%d %b %Y, %H:%M')} Canary. Demo: <a href="https://leakguard-manager.com/demo">leakguard-manager.com/demo</a></p>

<h3 style="margin-bottom:2px">At a glance</h3>
<p style="margin-top:2px"><b>{len(visits)}</b> visits from <b>{len(sessions)}</b> people ·
7-day average <b>{avg_daily}</b>/day · <b>{len(lead_emails)}</b> lead(s) captured</p>

<h3 style="margin-bottom:2px">The funnel</h3>
{ul([
    f"Arrived: <b>{len(arrived)}</b>",
    f"Started the tour: <b>{len(toured)}</b> ({pct(toured, arrived)})",
    f"Asked the AI: <b>{len(asked)}</b> ({pct(asked, arrived)})",
    f"Ran a report: <b>{len(reported)}</b> ({pct(reported, arrived)})",
    f"Left an email (lead): <b>{len(leads)}</b> ({pct(leads, arrived)})",
])}

<h3 style="margin-bottom:2px">Where they came from</h3>
{ul(top(referrers))}
<p style="margin:2px 0;color:#64748b">Campaigns (UTM):</p>{ul(top(utms))}

<h3 style="margin-bottom:2px">Who + how</h3>
<p style="margin:2px 0;color:#64748b">Countries:</p>{ul(top(countries))}
<p style="margin:2px 0;color:#64748b">Devices:</p>{ul(top(devices))}

<h3 style="margin-bottom:2px">Leads (left an email)</h3>
{ul(lead_emails or ["(none today)"])}

<p style="color:#94a3b8;font-size:12px;margin-top:18px">First-party analytics from the demo itself. No third-party tracker. Reply to this email is not monitored by the demo.</p>
</div>"""
    subject = f"LeakGuard demo: {len(visits)} visits, {len(lead_emails)} lead(s) — {now.strftime('%d %b')}"
    return subject, html


def send_email(subject, html):
    gpath = os.path.join(_HERE, "gmail-api.py")
    if not os.path.exists(gpath):
        gpath = os.path.join(os.path.dirname(_HERE), "gmail-api.py")
    spec = importlib.util.spec_from_file_location("gmail_api", gpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    r = mod.GmailAPI().send(to=TO, subject=subject, body=html, html=True)
    return r.get("id", "ok")


def top_up_demo():
    """Roll the demo home's readings forward to now, even on days with zero visitors.
    The /demo page also tops up on open; this is the daily guarantee."""
    req = urllib.request.Request(
        f"{LG_URL}/functions/v1/demo-session",
        data=json.dumps({"topupOnly": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read().decode())
    print(f"demo top-up: latest reading {out.get('latestReading')}")


def main():
    # Canary is UTC in winter, UTC+1 in summer; the digest window is relative so exact tz is not critical.
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    try:
        top_up_demo()
    except Exception as e:
        print(f"demo top-up failed (digest continues): {e}")
    subject, html = build(now)
    if SEND:
        mid = send_email(subject, html)
        print(f"sent: {subject} (id={mid})")
    else:
        print(subject)
        print(html)


if __name__ == "__main__":
    main()
