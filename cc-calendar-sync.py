#!/usr/bin/env python3
"""cc-calendar-sync.py (B5) — sync Google Calendar events into the CC `calendar_events` table so the
Schedule page can overlay them on its month/week grid. Pulls every calendar Pete has, for a window of
today-30d .. today+120d, and clean-replaces that window (delete then insert) so cancelled events drop
out. Reuses calendar-api.py (CalendarAPI) for auth + listing. Run on a schedule (e.g. hourly) — and
once now for the initial fill.

Usage: VAULT=/tmp/pbs python3 /tmp/pbs/cc-calendar-sync.py
"""
import json, os, importlib.util, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
HR = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}

def rest(method, path, body=None, headers=None):
    h = dict(HR)
    if headers: h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, headers=h, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read() or "null")
    except urllib.error.HTTPError as e:
        return {"_error": f"{e.code} {e.read().decode()[:200]}"}

# load calendar-api.py (hyphenated → importlib)
spec = importlib.util.spec_from_file_location("calendar_api", f"{VAULT}/calendar-api.py")
calmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calmod)
cal = calmod.CalendarAPI()

now = datetime.now(timezone.utc)
win_start = (now - timedelta(days=30))
win_end = (now + timedelta(days=120))
tmin = win_start.isoformat(timespec="seconds").replace("+00:00", "Z")
tmax = win_end.isoformat(timespec="seconds").replace("+00:00", "Z")

# Pete's OWN calendar only — syncing the 16 subscribed colleague calendars would just clutter his
# schedule overlay. (Add more ids here if a shared calendar should appear.)
CALENDARS = [("primary", "My calendar")]

def items(res):
    return res if isinstance(res, list) else (res.get("items", []) if isinstance(res, dict) else [])

rows = []
for cid, cname in CALENDARS:
    try:
        res = cal.list_events(cid, time_min=tmin, time_max=tmax, max_results=250)
    except Exception as e:
        print(f"  ! {cname}: {e}"); continue
    for ev in items(res):
        start, end = ev.get("start", {}), ev.get("end", {})
        all_day = "date" in start
        sdt = start.get("dateTime") or start.get("date")
        edt = end.get("dateTime") or end.get("date")
        edate = (sdt or "")[:10] or None
        if not edate: continue
        rows.append({
            "id": f"{cid}:{ev.get('id')}",
            "calendar": cname,
            "title": ev.get("summary") or "(no title)",
            "starts_at": None if all_day else sdt,
            "ends_at": None if all_day else edt,
            "all_day": all_day,
            "event_date": edate,
            "location": ev.get("location"),
            "html_link": ev.get("htmlLink"),
            "synced_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        })

# clean-replace the window
rest("DELETE", f"calendar_events?event_date=gte.{win_start.date()}&event_date=lte.{win_end.date()}", headers={"Prefer": "return=minimal"})
for i in range(0, len(rows), 100):
    rest("POST", "calendar_events", rows[i:i+100], {"Prefer": "return=minimal"})
print(f"synced {len(rows)} events across the {win_start.date()}..{win_end.date()} window")
