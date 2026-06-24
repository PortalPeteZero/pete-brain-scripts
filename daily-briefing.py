#!/usr/bin/env python3
"""daily-briefing.py — Pete's morning briefing email. HEADLESS + deterministic + styled HTML.

Replaces the old Cowork SKILL.md (and the short-lived LLM agent_jobs version that free-composed ugly
HTML). Gathers every section from real sources, renders ONE consistent styled template, sends to Pete,
and publishes the same HTML to the CC morning-brief page. No LLM in the render → it looks right every time.

Sources (Business OS — Asana retired): Actions tray = Gmail label:Actions · tasks = CC public.tasks ·
calendar = calendar-api · recovery = CC garmin_daily · GA4 = ga4-api (Sygma + Canary Detect). The PF
lesson lead reads the local journal when present (Mac) and is skipped silently otherwise.
"""
# CRON-META
# what: Pete's daily morning briefing email (Actions tray, tasks, calendar, Garmin recovery, GA4) + CC publish
# why: One morning operating email from one place; deterministic styled HTML, reads the CC task+garmin engine
# reads: Gmail label:Actions + public.tasks + Calendar + garmin_daily + GA4
# writes: email to Pete + reports.snapshots morning-brief (CC)
# entity: canary-detect
# report: morning-brief
# schedule: 30 7 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import os
import sys
import json
import html
import importlib.util
import urllib.request
import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
TZ = ZoneInfo("Atlantic/Canary")
PETE = "pete.ashcroft@sygma-solutions.com"
VAULT = os.environ.get("VAULT", "/tmp/pbs")
GA4_PROPS = [("Sygma Solutions", "354127076"), ("Canary Detect", "537126447")]

# palette
NAVY, BLUE, RED, AMBER, GREY, BORDER, BG = "#1B2340", "#2563eb", "#dc2626", "#d97706", "#64748b", "#e2e8f0", "#f8fafc"


def _helper(fname, mod):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _cc():
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        d = json.load(open(os.path.join(VAULT, "Library/processes/secrets/command-centre-supabase-keys.json")))
        url, key = d["url"], d["service_role_key"]
    return url.rstrip("/"), key


def cc_get(path):
    url, key = _cc()
    req = urllib.request.Request(f"{url}/rest/v1/{path}", headers={"apikey": key, "Authorization": "Bearer " + key})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def esc(s):
    # voice-principles: outbound text carries no em/en/double dashes, even inside reproduced data
    s = str(s if s is not None else "")
    for a, b in ((" — ", ", "), ("—", ", "), (" – ", ", "), ("–", ", "), (" -- ", ", ")):
        s = s.replace(a, b)
    return html.escape(s)


# ─────────────────────────── section data ───────────────────────────
def pf_lesson(today):
    """Best-effort yesterday's PF lesson (local vault only; skipped on cloud)."""
    yest = (today - datetime.timedelta(days=1)).isoformat()
    for p in [os.path.join(VAULT, f"Personal/passion-fit/journal/{yest}.md")]:
        if os.path.exists(p):
            t = open(p).read()
            if "## One lesson for tomorrow" in t:
                after = t.split("## One lesson for tomorrow", 1)[1].strip()
                for cut in ("\n## ", "\n---"):
                    i = after.find(cut)
                    if i > 0:
                        after = after[:i]
                return after.strip() or None
    return None


def actions_tray():
    g = _helper("gmail-api.py", "gmail_api").GmailAPI()
    threads = g.search_threads("label:Actions", max_results=15) or []
    items = []
    now = datetime.datetime.now(datetime.timezone.utc)
    for t in threads:
        try:
            td = g.get_thread(t["id"], fmt="metadata")
            msgs = td.get("messages", [])
            last = msgs[-1]
            hh = {x["name"].lower(): x["value"] for x in last.get("payload", {}).get("headers", [])}
            sender = (hh.get("from", "").split("<")[0].strip().strip('"')) or hh.get("from", "")
            subj = hh.get("subject", "(no subject)")
            ts = int(last.get("internalDate", "0")) / 1000
            age = (now - datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)).days if ts else 0
            items.append({"who": sender, "what": subj, "age": age, "ts": ts})
        except Exception:
            continue
    items.sort(key=lambda x: x["ts"])  # oldest first
    return items


def tasks_today(today):
    iso = today.isoformat()
    due = cc_get(f"tasks?status=eq.todo&due_on=eq.{iso}&select=name,priority,entity_slug&order=priority.asc")
    overdue = cc_get(f"tasks?status=eq.todo&due_on=lt.{iso}&select=priority")
    counts = {}
    for r in overdue:
        counts[r.get("priority") or "none"] = counts.get(r.get("priority") or "none", 0) + 1
    return due, counts


def calendar_today(today):
    cal = _helper("calendar-api.py", "calendar_api").CalendarAPI()
    lo = datetime.datetime.combine(today, datetime.time(0, 0, tzinfo=TZ)).isoformat()
    hi = datetime.datetime.combine(today, datetime.time(23, 59, tzinfo=TZ)).isoformat()
    evs = cal.list_events(calendar_id="primary", time_min=lo, time_max=hi) or []
    out = []
    for e in evs:
        st = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date") or ""
        tm = st[11:16] if "T" in st else "all-day"
        out.append({"time": tm, "title": e.get("summary", "(untitled)"), "loc": e.get("location", "")})
    return out


def garmin_recovery():
    rows = cc_get("garmin_daily?order=date.desc&limit=1")
    return rows[0] if rows else None


def ga4_snapshot():
    api = _helper("ga4-api.py", "ga4_api").GA4API()
    out = []
    for name, prop in GA4_PROPS:
        try:
            rows = api.run_report(prop, [], ["sessions", "totalUsers"], days=2, limit=1)
            r = (rows or [{}])[0]
            sess = r.get("sessions") or r.get("metric_0") or (list(r.values())[0] if r else "?")
            users = r.get("totalUsers") or r.get("metric_1") or "?"
            out.append({"name": name, "sessions": sess, "users": users})
        except Exception as e:
            out.append({"name": name, "error": str(e)[:60]})
    return out


# ─────────────────────────── render ───────────────────────────
def _card(title, inner):
    return (f'<div style="background:#fff;border:1px solid {BORDER};border-radius:10px;margin:0 0 16px;overflow:hidden">'
            f'<div style="background:{BLUE};color:#fff;font-size:14px;font-weight:700;padding:10px 16px;letter-spacing:.3px">{title}</div>'
            f'<div style="padding:14px 16px;font-size:14px;color:#1e293b;line-height:1.5">{inner}</div></div>')


def _badge(p):
    c = {"P1": RED, "P2": AMBER}.get(p, GREY)
    return f'<span style="background:{c};color:#fff;font-size:11px;font-weight:700;border-radius:4px;padding:1px 6px">{esc(p or "P?")}</span>'


def render(today, lesson, tray, due, overdue, events, garmin, ga4):
    parts = []
    # PF lesson lead
    if lesson:
        parts.append(_card("Lesson from yesterday",
                           f'<div style="border-left:3px solid {AMBER};padding-left:12px;color:#334155;font-style:italic">{esc(lesson)}</div>'))
    # Actions tray
    aging = sum(1 for i in tray if i["age"] > 3)
    if tray:
        rows = ""
        for n, i in enumerate(tray[:10], 1):
            flag = f' <span style="color:{RED};font-weight:700">({i["age"]}d)</span>' if i["age"] > 3 else ""
            rows += f'<div style="padding:5px 0;border-bottom:1px solid {BG}">{n}. <b>{esc(i["who"])}</b>: {esc(i["what"])}{flag}</div>'
        if len(tray) > 10:
            rows += f'<div style="padding:5px 0;color:{GREY}">+{len(tray)-10} more in tray</div>'
        rows += f'<div style="margin-top:10px;color:{GREY};font-style:italic">Say "actions" in Cowork to walk these with drafts ready.</div>'
        parts.append(_card(f"ACTIONS TRAY ({len(tray)}, {aging} aging)", rows))
    else:
        parts.append(_card("ACTIONS TRAY", "Tray clear."))
    # Priority tasks
    if due:
        rows = ""
        for t in due:
            ent = f' <span style="color:{GREY}">· {esc(t.get("entity_slug"))}</span>' if t.get("entity_slug") else ""
            rows += f'<div style="padding:5px 0;border-bottom:1px solid {BG}">{_badge(t.get("priority"))} {esc(t.get("name"))}{ent}</div>'
    else:
        rows = '<div style="padding:4px 0">No tasks due today.</div>'
    od = " / ".join(f'{overdue.get(p,0)} {p}' for p in ("P1", "P2", "P3")) if overdue else "0"
    rows += f'<div style="margin-top:8px;color:{GREY}">Overdue: {od}</div>'
    parts.append(_card("PRIORITY TASKS, due today", rows))
    # Calendar
    if events:
        rows = ""
        for e in events:
            loc = f' <span style="color:{GREY}">· {esc(e["loc"])}</span>' if e["loc"] else ""
            rows += f'<div style="padding:5px 0;border-bottom:1px solid {BG}"><b>{esc(e["time"])}</b> {esc(e["title"])}{loc}</div>'
    else:
        rows = "Clear calendar today."
    parts.append(_card("CALENDAR TODAY", rows))
    # Garmin
    if garmin:
        g = garmin
        def cell(lbl, val):
            return f'<td style="padding:6px 10px;border:1px solid {BORDER}"><div style="color:{GREY};font-size:11px">{lbl}</div><div style="font-weight:700">{esc(val)}</div></td>'
        rows = ('<table style="border-collapse:collapse;width:100%"><tr>'
                + cell("Sleep", f'{g.get("sleep_score")} ({g.get("sleep_hours")}h)')
                + cell("HRV", g.get("hrv"))
                + cell("Readiness", f'{g.get("readiness")} {g.get("readiness_label") or ""}')
                + '</tr><tr>'
                + cell("Steps", g.get("steps"))
                + cell("Resting HR", g.get("resting_hr"))
                + cell("Stress", g.get("stress_avg"))
                + '</tr></table>')
    else:
        rows = "Garmin recovery unavailable this run."
    parts.append(_card("GARMIN RECOVERY", rows))
    # GA4
    rows = ""
    for p in ga4:
        if p.get("error"):
            rows += f'<div style="padding:5px 0">{esc(p["name"])}: snapshot unavailable.</div>'
        else:
            rows += f'<div style="padding:5px 0;border-bottom:1px solid {BG}"><b>{esc(p["name"])}</b>: {esc(p["sessions"])} sessions, {esc(p["users"])} users</div>'
    parts.append(_card("GA4 SNAPSHOT (last 2 days)", rows))

    body = "".join(parts)
    dstr = today.strftime("%A %d %B %Y")
    return (f'<div style="background:{BG};padding:20px 0;font-family:-apple-system,Segoe UI,Roboto,sans-serif">'
            f'<div style="max-width:640px;margin:0 auto">'
            f'<div style="background:{NAVY};color:#fff;border-radius:10px;padding:18px 20px;margin-bottom:16px">'
            f'<div style="font-size:20px;font-weight:800">Morning Briefing</div>'
            f'<div style="color:#cbd5e1;font-size:13px;margin-top:2px">{dstr}</div></div>'
            f'{body}'
            f'<div style="color:{GREY};font-size:11px;text-align:center;padding:8px">Command Centre · commandcentre.info/m/morning-brief</div>'
            f'</div></div>')


def main():
    today = datetime.datetime.now(TZ).date()
    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            print(f"  section {fn.__name__} failed: {e}", file=sys.stderr)
            return None
    lesson = safe(pf_lesson, today)
    tray = safe(actions_tray) or []
    due, overdue = safe(tasks_today, today) or ([], {})
    events = safe(calendar_today, today) or []
    garmin = safe(garmin_recovery)
    ga4 = safe(ga4_snapshot) or []

    html_body = render(today, lesson, tray, due, overdue, events, garmin, ga4)
    subject = "Morning Briefing, " + today.strftime("%A %d %b")

    g = _helper("gmail-api.py", "gmail_api").GmailAPI()
    r = g.send(to=PETE, subject=subject, body=html_body, html=True)
    print(f"daily-briefing: SENT msg_id={r.get('id')} (tray={len(tray)}, due={len(due)}, events={len(events)}, garmin={'y' if garmin else 'n'})")

    # publish to the CC morning-brief page (reports.snapshots) — non-fatal
    try:
        _helper("cc_publish.py", "cc_publish").publish("morning-brief", today.isoformat(), {"subject": subject, "html": html_body})
        print("  CC: morning-brief published")
    except Exception as e:
        print(f"  CC publish failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
