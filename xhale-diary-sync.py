#!/usr/bin/env python3
"""xhale-diary-sync.py -- Google Calendar -> Xhale, one direction only. Pete's commitments become
diary entries so Loren can plan around them.

    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-diary-sync.py                      # DRY RUN (default)
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-diary-sync.py --apply              # write for real
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-diary-sync.py --from 2026-08-01 --to 2026-10-30

DRY RUN IS THE DEFAULT AND THAT IS DELIBERATE. Every write notifies Loren, so the output is meant to
be read by Pete before anything happens. He set the standing rule on 6 Aug 2026: "before we write we
have to have it 100% correct with it shown to me here to stop lots of corrections."

WHAT IT DOES NOT DO. It does not touch training sessions -- those are Loren's and they flow the
other way. It does not delete. It does not update an entry it has already made. First round only,
by design; the calendar direction is being added one capability at a time.

LINK ON WRITE. Every entry it creates gets an xhale_gcal_link row in the SAME action, keyed on the
calendar event id. That is what stops a second run duplicating everything: run two reads the table
and skips what is already paired. Without it, matching would fall back to comparing titles, which is
exactly what the pairing exists to avoid.

THE EXCLUSIONS, in order:
  1. eventType != default   -- Gmail's own auto-imports; they are locked stubs, not real events
  2. visibility == private  -- PETE'S OWN VETO. One click in Google Calendar, always obeyed
  3. a training emoji       -- Loren owns training; sending it back is an echo
  4. travel to/from a pool  -- the drive attached to a training session
  5. already in xhale_gcal_link

Measured on the first 90-day window: 132 events in, 46 out.

THE RENDER, as Pete specified it:
  "{start} - {end} {what}, {where}"   e.g.  "3pm - 4pm Vislock meeting, Hindley Business Centre"
  * time in the title, because a Xhale session has NO time field
  * flights say origin AND destination -- "Fly Madrid to Manchester" -- and never a flight number
  * "online" ONLY for a real conferencing platform, never the loose word
  * detail after a dash is dropped: Loren does not know what a Main Laying intro is
  * an unbooked placeholder keeps its name and drops the time entirely
"""
import argparse
import collections
import datetime
import importlib.util
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VAULT = Path(os.environ.get("VAULT", "/tmp/pbs"))
SCRIPT_DIR = Path(__file__).resolve().parent
DIARY_DISCIPLINE_ID = 17

TRAIN = ("\U0001F3CA", "\U0001F3C3", "\U0001F6B4", "\U0001F3CB")   # swim run bike gym
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️‍]+")
CAMP = "Campamento Juvenil Raso de la Nava"
FLY = re.compile(r"^Flight to (.+?)\s*(?:\(|$)")
AIRPORTS = [("adolfo", "Madrid"), ("barajas", "Madrid"), ("lanzarote", "Lanzarote"),
            ("manchester", "Manchester"), ("barcelona", "Barcelona"), ("gatwick", "London"),
            ("heathrow", "London"), ("luton", "London"), ("birmingham", "Birmingham")]
SHORTEN = [(r"^Introduction meeting to discuss (\w+).*$", r"\1 meeting"),
           (r"^(Zero Strike Community event).*$", r"Zero Strike Community event"),
           (r"^(Camper van (?:pick-?up|drop-?off)).*$", r"\1")]
HAS_KIND = re.compile(r"meeting|catch ?up|review|call|seminar|training|intro|event|rehearsal", re.I)


def load(name, mod):
    spec = importlib.util.spec_from_file_location(mod, SCRIPT_DIR / name)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sql(query):
    r = subprocess.run(["python3", str(SCRIPT_DIR / "cc-sql.py"), query, "--raw"],
                       capture_output=True, text=True, timeout=120,
                       env={**os.environ, "VAULT": str(VAULT)})
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        raise SystemExit(f"cc-sql failed: {(r.stderr or out)[:300]}")
    return json.loads(out) if out.startswith("[") else []


_XH = None


def xh():
    """Go through xhale-api.py, NOT a raw request. It checks the stored expiry, refreshes, and
    retries once on a 401. An earlier version read the token file directly and would simply have
    failed all 49 posts inside the ten-hour window after the token went stale."""
    global _XH
    if _XH is None:
        _XH = load("xhale-api.py", "xhale_api")
    return _XH


def clock(iso):
    t = datetime.datetime.fromisoformat(iso)
    h, mi = t.hour, t.minute
    ap = "am" if h < 12 else "pm"
    return f"{h % 12 or 12}{ap}" if mi == 0 else f"{h % 12 or 12}.{mi:02d}{ap}"


def clean(s):
    s = re.sub(r"^\s*(FW:|FWD:|RE:)\s*", "", (s or "").strip(), flags=re.I)
    s = EMOJI.sub("", s).strip()
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)          # trailing attendee lists
    s = re.sub(r"\s*[—–]\s+.*$", "", s)             # ALL detail after a dash (Pete, asked 3x)
    s = re.sub(r"\s*-\s*Wave\s*\d+$", "", s, flags=re.I)
    for pat, rep in SHORTEN:
        s = re.sub(pat, rep, s, flags=re.I)
    return re.sub(r"\s{2,}", " ", s).strip(" -–—/")


def is_online(e):
    r"""A conferencing PLATFORM only, never the bare word. A \bonline\b test matched the phrase
    'online check-in' inside a location Claude itself wrote, and labelled two physical van
    collections in Madrid as video calls."""
    loc = e.get("location") or ""
    return bool(re.search(r"microsoft teams|teams meeting|zoom\.us|meet\.google|google meet|webex",
                          loc, re.I) or e.get("conferenceData"))


def where(e, title=""):
    """The place, unless it merely repeats the title.
    GUARD THE EMPTY TITLE: '' is a substring of every string, so a bare `title in place` test
    silently suppressed EVERY location on a run where the title was not passed through."""
    if is_online(e):
        return "online"
    loc = (e.get("location") or "").strip()
    if not loc:
        return None
    if CAMP in loc:
        return "Madrid"                              # Pete's call: the campsite name is noise
    place = re.sub(r"\s*\([^)]*\)\s*$", "", re.split(r",", loc)[0].strip()).strip()
    if not place:
        return None
    tl, pl = (title or "").lower().strip(), place.lower()
    if not tl:
        return place[:34]
    if pl in tl or tl in pl:
        return None
    first = pl.split()[0]
    if len(first) > 4 and first in tl:
        return None
    return place[:34]


def origin_city(loc):
    low = (loc or "").lower()
    for needle, city in AIRPORTS:
        if needle in low:
            return city
    head = re.sub(r"\s*Airport\s*$", "", re.split(r"[,(]", loc or "")[0], flags=re.I).strip()
    return head or None


def render(e):
    raw = e.get("summary") or ""
    s = clean(raw)
    st, en = e["start"].get("dateTime"), e["end"].get("dateTime")
    t = f"{clock(st)} - {clock(en)}" if st and en else "all day"

    if "NOT BOOKED" in raw.upper():
        return f"{s} (not booked yet)"        # Pete: a place marker, updated once booked

    fl = FLY.match(EMOJI.sub("", raw).strip())
    if fl:
        orig = origin_city(e.get("location"))
        dest = fl.group(1).strip()
        return f"{t} Fly {orig} to {dest}" if orig else f"{t} Fly to {dest}"

    if (e.get("attendees") or is_online(e)) and not HAS_KIND.search(s):
        s = f"{s} meeting"
    w = where(e, s)
    return f"{t} {s}" + (f", {w}" if w else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="frm", default="2026-08-01")
    ap.add_argument("--to", default="2026-10-30")
    ap.add_argument("--apply", action="store_true", help="write for real (default is a dry run)")
    a = ap.parse_args()

    cal = load("calendar-api.py", "cal_api").CalendarAPI()
    linked = {r["gcal_event_id"] for r in sql("SELECT gcal_event_id FROM xhale_gcal_link")}
    evs = cal.list_events(time_min=f"{a.frm}T00:00:00Z", time_max=f"{a.to}T23:59:59Z")
    items = evs.get("items", evs) if isinstance(evs, dict) else evs

    keep, dropped = [], collections.Counter()
    for e in items:
        s = (e.get("summary") or "").strip()
        if e.get("eventType", "default") != "default":
            dropped["google-made stub"] += 1
        elif e.get("visibility") == "private":
            dropped["Pete marked private"] += 1
        elif any(s.startswith(t) for t in TRAIN):
            dropped["training (Loren owns)"] += 1
        elif "Travel — pool" in s or "Travel — open water" in s:
            dropped["drive to/from training"] += 1
        elif e["id"] in linked:
            dropped["already linked"] += 1
        else:
            keep.append(e)

    byday = collections.defaultdict(list)
    for e in keep:
        byday[(e["start"].get("dateTime") or e["start"].get("date"))[:10]].append(e)

    print(f"{'APPLYING' if a.apply else 'DRY RUN - nothing written'}   {a.frm} to {a.to}")
    print(f"{len(items)} events in the window")
    for k, v in dropped.most_common():
        print(f"   -{v:3}  {k}")
    print(f"   ={len(keep):3}  to Loren, across {len(byday)} days\n")

    # Xhale already holds sessions on many of these days. Numbering only over what WE write
    # collides with them -- a 7pm rehearsal written at order 1 sits alongside an 8am swim also at
    # order 1, and the tie breaks by whichever was created first. Start above the day's existing
    # maximum instead. Stats are unaffected: they live at -1.
    existing = collections.defaultdict(int)
    for s_ in xh().call("GET", f"/api/sessions/?start_date={a.frm}&end_date={a.to}"):
        if not s_.get("deleted") and s_.get("order") is not None and s_["order"] > 0:
            existing[s_["date"]] = max(existing[s_["date"]], s_["order"])

    order_of = {}
    for d in sorted(byday):
        rows = sorted(byday[d], key=lambda q: q["start"].get("dateTime") or "")
        base = existing.get(d, 0)
        for i, e in enumerate(rows, start=1):
            order_of[e["id"]] = base + i
        print(f"{d} {datetime.date.fromisoformat(d).strftime('%a')}")
        for e in rows:
            print(f'   {order_of[e["id"]]}  "{render(e)}"')

    if not a.apply:
        print(f"\n>>> {len(keep)} notifications to Loren if applied. Re-run with --apply.")
        return

    print("\nwriting...")
    done = failed = 0
    for d in sorted(byday):
        for e in sorted(byday[d], key=lambda q: q["start"].get("dateTime") or ""):
            title = render(e)[:200]
            try:
                x = xh().call("POST", "/api/sessions/",
                              {"date": d, "discipline_id": DIARY_DISCIPLINE_ID,
                               "brief_description": title, "order": order_of[e["id"]]})
            except (urllib.error.HTTPError, urllib.error.URLError, SystemExit) as exc:
                failed += 1
                print(f"   FAILED {d} {title[:44]}: {str(exc)[:140]}")
                continue
            sql("INSERT INTO xhale_gcal_link (xhale_session_id, gcal_event_id, gcal_series_id, "
                "on_date, title, origin) VALUES (%d, $q$%s$q$, %s, $q$%s$q$, $q$%s$q$, 'gcal') "
                "ON CONFLICT (xhale_session_id) DO UPDATE SET gcal_event_id=EXCLUDED.gcal_event_id "
                "RETURNING xhale_session_id"
                % (x["id"], e["id"],
                   ("$q$%s$q$" % e["recurringEventId"]) if e.get("recurringEventId") else "NULL",
                   d, title.replace("$", "")))
            done += 1
            print(f'   {d}  xhale {x["id"]}  order {x.get("order")}  {title[:56]}')
    print(f"\nposted {done}, each linked to its calendar event"
          + (f"; {failed} FAILED" if failed else ""))


if __name__ == "__main__":
    main()
