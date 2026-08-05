#!/usr/bin/env python3
"""vehicle-tracking-sync.py -- what time did a trainer actually get to site, and leave.

Reads the master booking sheet, pulls each trainer's van journeys from Matrix Telematics, works out
arrival and departure from how the van behaved, and writes hub.vehicle_site_visit on the Sygma
Platform. Surfaced at sygmaportal.com/hub/vehicle-tracking.

    VAULT=/tmp/pbs python3 /tmp/pbs/vehicle-tracking-sync.py --month 2026-07
    VAULT=/tmp/pbs python3 /tmp/pbs/vehicle-tracking-sync.py --days 3        # the nightly shape
    VAULT=/tmp/pbs python3 /tmp/pbs/vehicle-tracking-sync.py --days 3 --dry

WHY THE VAN DECIDES AND THE ADDRESS ONLY CONFIRMS
Where a van arrives in the morning and then sits for half an hour or more, it is there. That is the
measurement (Pete, 5 Aug 2026). The booked site address is geocoded afterwards purely to LABEL the
stop -- matches the booking, or sits N km away. A wrong geocode therefore costs a mislabelled row,
which is visible, rather than a wrong time, which is not. It also still works on the 2-4% of sheet
rows with no site address at all.

THE TRAP THIS SCRIPT EXISTS TO AVOID (audit, 5 Aug 2026)
Matrix emits "journeys" of 0.0 miles while a van sits still. Andy Bartholomew's van produced 15 of
them in Doncaster between 01:34 and 04:26 on 2 Jul. Merged by the same-place rule that reads as a
169-minute dwell, so a naive version called it the site and reported ARRIVED 01:44. The real day was
Doncaster 04:33 -> 167 miles -> Greenford 08:01, against a booked start of 08:00. Across July that
produced 12 arrivals before 05:00, every one a hotel.
So: any stop BEFORE the day's first journey of a mile or more is ignored. Measured against real July
data this took impossible arrivals from 12 to 0 and lifted the sensible-band hit rate from 66% to
75%, while keeping 134 of 143 measurements. The blunter alternative (demand the arriving journey
itself be over a mile) was tried and REJECTED -- it lost 41 measurements, because the last hop from a
car park to the gate is often shorter than a mile.

# CRON-META
# what: Works out what time each trainer's van reached site and left, from the master booking sheet plus Matrix Telematics, and writes it to the Sygma Platform for the Vehicle Tracking page.
# why: The training feedback page guessed course start/finish from when a delegate happened to submit a JotForm. This measures the van instead. Also feeds the Soldo nights-away cross-check.
# reads: master booking sheet (Google Sheet 1_kS3-typ...), Matrix Telematics journeys, hub.fleet, hub.staff_directory
# writes: hub.vehicle_site_visit, hub.vehicle_night (Sygma Platform rsczwfstwkthaybxhszy)
# entity: sygma
# schedule: 0 2 * * *
# timezone: Europe/London
# CRON-META-END
"""
import argparse, datetime, importlib.util, json, math, os, re, sys, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PLATFORM_REF = "rsczwfstwkthaybxhszy"
SHEET_ID = "1_kS3-typOQs42PHNjWDe_x7uWqZWPVNeUNcTPCOATiU"
HEADER_ROW = 3

DWELL_MIN = 30       # minutes parked before it counts as arrived
SAME_PLACE_M = 300   # stops this close are the same place
RETURN_MIN = 150     # 2.5 hours; come back inside this and you never left (Pete's rule)
REAL_TRIP_MI = 1.0   # what counts as the van having actually driven somewhere

MONTHS = ["january", "february", "march", "april", "may", "june",
          "july", "august", "september", "october", "november", "december"]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mx = _load("mx", f"{VAULT}/matrix-api.py")
sheets = _load("sheets", f"{VAULT}/sheets-api.py")


# ------------------------------------------------------------------ platform
def _pf_token():
    return open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()


def pf_sql(sql, _tries=4):
    """One statement (or batch) against the platform. Retries a 5xx -- the management API returns
    502 under a rapid burst, which is what a row-at-a-time write loop looks like to it."""
    import time
    for attempt in range(_tries):
        req = urllib.request.Request(
            f"https://api.supabase.com/v1/projects/{PLATFORM_REF}/database/query",
            data=json.dumps({"query": sql}).encode(),
            headers={"Authorization": f"Bearer {_pf_token()}", "Content-Type": "application/json",
                     "User-Agent": "Mozilla/5.0"}, method="POST")
        try:
            return json.loads(urllib.request.urlopen(req, timeout=180).read())
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < _tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"platform SQL failed ({e.code}): "
                               f"{e.read().decode('utf-8','replace')[:300]}") from None


def q(v):
    """Quote a value for SQL. None -> NULL."""
    if v is None or v == "":
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "$vt$" + str(v) + "$vt$"


# ------------------------------------------------------------------ geometry
def hav(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ------------------------------------------------------------------ the sheet
def sheet_rows(year, month):
    tab = MONTHS[month - 1].capitalize()
    tok = sheets.get_token()
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
           + urllib.request.quote(f"{tab}!A{HEADER_ROW + 1}:S1000"))
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    vals = json.loads(urllib.request.urlopen(req, timeout=90).read()).get("values", [])
    return [(r + [""] * 19)[:19] for r in vals if (r + [""])[0].strip()]


def parse_dates(s):
    """Every individual date a sheet row covers. Handles the range and ampersand forms."""
    s = (s or "").strip()
    m = re.match(r"^(\d{1,2})\s*[-&]\s*(\d{1,2})[/\s]+(\w+)[/\s]+(\d{4})$", s)
    if m:
        d1, d2, mon, yr = m.groups()
        mo = int(mon) if mon.isdigit() else next(
            (i + 1 for i, n in enumerate(MONTHS) if n.startswith(mon[:3].lower())), 0)
        if not mo:
            return []
        days = [int(d1), int(d2)] if "&" in s else list(range(int(d1), int(d2) + 1))
        try:
            return [datetime.date(int(yr), mo, d) for d in days]
        except ValueError:
            return []
    for fmt in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        try:
            return [datetime.datetime.strptime(s, fmt).date()]
        except ValueError:
            pass
    return []


def parse_time(s):
    m = re.match(r"^(\d{1,2}):(\d{2})\s*([ap])", (s or "").replace(" ", " ").strip().lower())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    try:
        return datetime.time(h, mi)
    except ValueError:
        return None


# ------------------------------------------------------------------ people + vans
def roster():
    """trainer name -> (employee_ref, vehicle_reg, matrix service id, is_subcontractor)."""
    rows = pf_sql(
        "SELECT s.employee_ref, s.full_name, s.worker_type, s.trainer_id, f.vehicle_reg "
        "FROM hub.staff_directory s "
        "LEFT JOIN hub.fleet f ON f.current_driver_ref = s.employee_ref "
        "WHERE s.employment_status IN ('Active','Subcontractor')")
    units = mx.units()

    def norm(x):
        return "".join(str(x or "").upper().split())

    svc_by_reg = {}
    for u in units:
        for r in rows:
            if r["vehicle_reg"] and norm(r["vehicle_reg"]) in norm(u.get("vehicleRegistration")):
                svc_by_reg[r["vehicle_reg"]] = u["serviceID"]
    out = {}
    for r in rows:
        out[r["full_name"]] = {
            "employee_ref": r["employee_ref"],
            "vehicle_reg": r["vehicle_reg"],
            "service_id": svc_by_reg.get(r["vehicle_reg"]),
            "subcontractor": (r["worker_type"] or "").lower() == "subcontractor",
            "is_trainer": r["trainer_id"] is not None,
        }
    return out


# ------------------------------------------------------------------ the measurement
_JCACHE = {}


def journeys(service_id, day):
    key = (service_id, day)
    if key not in _JCACHE:
        a = datetime.datetime.combine(day, datetime.time(0, 0))
        b = a + datetime.timedelta(days=1)
        js = mx.call(f"apioauth/v4/journey?serviceID={service_id}"
                     f"&from={a:%Y%m%d%H%M%S}&to={b:%Y%m%d%H%M%S}&showTowJourney=false")
        _JCACHE[key] = sorted(js, key=lambda x: x["LocalTimeStartDate"]) if isinstance(js, list) else []
    return _JCACHE[key]


def stops(js):
    """The gaps between journeys: where the van was parked, and for how long.

    Stops that happen before the day's first real drive are dropped -- that is the overnight rest,
    and the tracker's zero-distance noise inside it would otherwise look like a long dwell.
    """
    first_real = None
    for j in js:
        if (j.get("Distance") or 0) >= REAL_TRIP_MI:
            first_real = datetime.datetime.fromisoformat(j["LocalTimeEndDate"])
            break
    # The van never actually drove anywhere today. Every record is the tracker talking to itself
    # while it sits still, so there is no arrival to find and picking the earliest noise would
    # invent one. Andy Bartholomew, 1 Jul 2026: 20+ journeys, all 0.0 miles, Doncaster to Doncaster,
    # 05:27 to 22:03 -- a naive read called that "arrived 05:38".
    if first_real is None:
        return []
    raw = []
    for i, j in enumerate(js[:-1]):
        arrive = datetime.datetime.fromisoformat(j["LocalTimeEndDate"])
        if first_real and arrive < first_real:
            continue
        raw.append({"arrive": arrive,
                    "depart": datetime.datetime.fromisoformat(js[i + 1]["LocalTimeStartDate"]),
                    "lat": j["EndLat"], "lon": j["EndLong"],
                    "town": j.get("EndTown") or "", "street": j.get("EndStreet") or ""})
    merged = []
    for s in raw:
        if merged and hav(merged[-1]["lat"], merged[-1]["lon"], s["lat"], s["lon"]) <= SAME_PLACE_M:
            merged[-1]["depart"] = max(merged[-1]["depart"], s["depart"])
        else:
            merged.append(dict(s))
    for m in merged:
        m["mins"] = round((m["depart"] - m["arrive"]).total_seconds() / 60)
    return merged


def arrive_leave(st):
    """First stop of 30 minutes or more is the site. Leaving means not coming back within 2.5h."""
    site = next((s for s in st if s["mins"] >= DWELL_MIN), None)
    if not site:
        return None, None, None
    i = st.index(site)
    leave = site["depart"]
    for later in st[i + 1:]:
        same = hav(site["lat"], site["lon"], later["lat"], later["lon"]) <= SAME_PLACE_M
        gap = (later["arrive"] - leave).total_seconds() / 60
        if same and gap <= RETURN_MIN:
            leave = later["depart"]
        else:
            break
    return site["arrive"], leave, site


# ------------------------------------------------------------------ overnight
def overnight(service_id, day):
    """Where the van rested on the night of `day`: the last place it stopped."""
    js = journeys(service_id, day)
    if not js:
        return None
    last = js[-1]
    return {"lat": last["EndLat"], "lon": last["EndLong"], "town": last.get("EndTown") or ""}


def home_points():
    rows = pf_sql("SELECT employee_ref, lat, lon FROM hub.trainer_home_point")
    return {r["employee_ref"]: (r["lat"], r["lon"]) for r in rows}


HOME_RADIUS_M = 500      # a postcode-level geocode is routinely this far from the actual driveway


def usual_rest_places(service_ids, upto, lookback=60):
    """Where each van USUALLY spends the night, learned from its own behaviour.

    The HR home address is not good enough to judge this on. Measured 5 Aug 2026 over July: five
    trainers' vans rested a median 0.0 km from their geocoded home, but Andy Bartholomew's sat 0.3 km
    away and Gareth Phillips' 0.6 km -- both postcode-level geocodes, so a tight radius called every
    single night "away from home". Paul Baxter's van rests a median 44.7 km from the address on file,
    every night, which is a genuine discrepancy rather than a rounding one.

    So the van's own habit decides, exactly as it does for arrivals, and the HR address is kept only
    as a cross-check. Returns {service_id: (lat, lon, nights_seen)}.
    """
    out = {}
    for sid in service_ids:
        seen = []
        for i in range(1, lookback + 1):
            r = overnight(sid, upto - datetime.timedelta(days=i))
            if r:
                seen.append(r)
        if not seen:
            continue
        # the biggest cluster of overnight positions is where it lives
        best, best_n = None, 0
        for cand in seen:
            n = sum(1 for o in seen
                    if hav(cand["lat"], cand["lon"], o["lat"], o["lon"]) <= HOME_RADIUS_M)
            if n > best_n:
                best, best_n = cand, n
        out[sid] = (best["lat"], best["lon"], best_n)
    return out


# ------------------------------------------------------------------ run
def collect(days_wanted, people):
    """Group sheet rows into deliveries: one per trainer per day per site address."""
    by_month = {}
    for d in days_wanted:
        by_month.setdefault((d.year, d.month), []).append(d)
    deliveries = {}
    for (yr, mo), days in by_month.items():
        for r in sheet_rows(yr, mo):
            dates = parse_dates(r[0])
            if not dates or len(dates) > 20:      # no date, or a month-header row
                continue
            trainer = r[13].strip()
            if not trainer:
                continue
            for d in dates:
                if d not in days:
                    continue
                # group key = trainer + day + site address. A public course is many bookings on ONE
                # delivery (Andrew Foster had 7 rows on 13 Jul 2026, all at Hindley), so without
                # this the page repeats itself and every average is weighted by how many companies
                # happened to book.
                dkey = (r[7].strip() or r[6].strip()).lower()
                key = (trainer, d, dkey)
                dl = deliveries.setdefault(key, {
                    "trainer": trainer, "date": d, "address": r[7].strip() or r[6].strip(),
                    "delivery_key": dkey, "booked_start": parse_time(r[1]), "courses": []})
                dl["courses"].append({"company": r[2].strip(), "course": r[14].strip(),
                                      "location": r[6].strip(), "price": r[10].strip()})
    return deliveries


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # Deliberately NOT required: the Railway cron invokes this script bare, so a bare run has to be
    # the nightly job. With a required group it would have exited 2 on an argparse error every night
    # and written nothing, which looks identical to "no courses that day".
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--month", help="YYYY-MM, backfill a whole month")
    g.add_argument("--days", type=int, help="recompute the last N days (default, the nightly shape)")
    ap.add_argument("--dry", action="store_true", help="compute and print, write nothing")
    a = ap.parse_args()

    if not a.month and not a.days:
        a.days = 3            # bare run = the nightly job: redo the last 3 days so a late sheet
                              # edit or a delayed journey upload gets picked up.
    if a.month:
        yr, mo = (int(x) for x in a.month.split("-"))
        last = (datetime.date(yr + (mo == 12), (mo % 12) + 1, 1) - datetime.timedelta(days=1)).day
        days = [datetime.date(yr, mo, d) for d in range(1, last + 1)]
    else:
        today = datetime.date.today()
        days = [today - datetime.timedelta(days=i) for i in range(1, a.days + 1)]

    print(f"vehicle-tracking-sync: {len(days)} day(s), {days[0]} to {days[-1]}")
    people = roster()
    homes = home_points()
    deliveries = collect(set(days), people)
    print(f"  {len(deliveries)} deliveries on the sheet")

    rows, counts = [], {}
    for (trainer, day, _), dl in sorted(deliveries.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        p = people.get(trainer)
        if not p:
            counts["unknown trainer"] = counts.get("unknown trainer", 0) + 1
            continue
        base = {"visit_date": day, "employee_ref": p["employee_ref"], "trainer_name": trainer,
                "vehicle_reg": p["vehicle_reg"], "service_id": p["service_id"],
                "booked_start": dl["booked_start"].strftime("%H:%M") if dl["booked_start"] else None,
                "booked_address": dl["address"], "delivery_key": dl["delivery_key"],
                "courses": dl["courses"], "stops": [],
                "arrived_at": None, "left_at": None, "minutes_on_site": None,
                "site_lat": None, "site_lon": None, "site_town": None, "site_street": None,
                "address_match_km": None, "note": None}
        if p["subcontractor"]:
            base["status"] = "subcontractor"
            base["note"] = "Drives their own vehicle, so there is no tracker to read."
        elif not p["service_id"]:
            base["status"] = "no-van"
            base["note"] = ("No tracker on this trainer's vehicle yet."
                            if p["vehicle_reg"] else "No vehicle assigned in hub.fleet.")
        else:
            st = stops(journeys(p["service_id"], day))
            if not st:
                base["status"] = "no-vehicle-data"
                base["note"] = ("The van did not drive anywhere that day, so there is no arrival to "
                                "measure. Usually means the trainer was already at or beside the "
                                "site."
                                if journeys(p["service_id"], day)
                                else "The tracker reported no journeys that day.")
            else:
                arr, lv, site = arrive_leave(st)
                if not arr:
                    base["status"] = "no-long-stop"
                    base["note"] = f"The van moved but never stopped for {DWELL_MIN} minutes."
                else:
                    base.update(status="measured", arrived_at=arr.isoformat(), left_at=lv.isoformat(),
                                minutes_on_site=round((lv - arr).total_seconds() / 60),
                                site_lat=site["lat"], site_lon=site["lon"],
                                site_town=site["town"], site_street=site["street"],
                                stops=[{"from": s["arrive"].strftime("%H:%M"),
                                        "to": s["depart"].strftime("%H:%M"), "mins": s["mins"],
                                        "where": f'{s["street"]}, {s["town"]}'.strip(", ")}
                                       for s in st])
        counts[base["status"]] = counts.get(base["status"], 0) + 1
        rows.append(base)

    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {v:>4}  {k}")

    if a.dry:
        print("\n--dry: nothing written. Sample:")
        for r in [x for x in rows if x["status"] == "measured"][:8]:
            print(f"    {r['visit_date']}  {r['trainer_name']:<20} booked {r['booked_start'] or '--:--'}"
                  f"  arrived {r['arrived_at'][11:16]}  left {r['left_at'][11:16]}  {r['site_town']}")
        return 0

    # Batched: one statement per 40 rows. A row-at-a-time loop over a month made the management
    # API return 502 partway through (5 Aug 2026), which is both slow and leaves a half-written day.
    written = 0
    COLS = ("(visit_date, employee_ref, trainer_name, vehicle_reg, service_id, booked_start, "
            "arrived_at, left_at, minutes_on_site, site_lat, site_lon, site_town, site_street, "
            "booked_address, delivery_key, courses, stops, status, note, computed_at)")
    UPSERT = ("ON CONFLICT (visit_date, employee_ref, delivery_key) DO UPDATE SET "
              "arrived_at=EXCLUDED.arrived_at, left_at=EXCLUDED.left_at, "
              "minutes_on_site=EXCLUDED.minutes_on_site, courses=EXCLUDED.courses, "
              "stops=EXCLUDED.stops, status=EXCLUDED.status, note=EXCLUDED.note, "
              "booked_start=EXCLUDED.booked_start, booked_address=EXCLUDED.booked_address, "
              "site_lat=EXCLUDED.site_lat, site_lon=EXCLUDED.site_lon, site_town=EXCLUDED.site_town, "
              "site_street=EXCLUDED.site_street, vehicle_reg=EXCLUDED.vehicle_reg, "
              "computed_at=now()")
    for i in range(0, len(rows), 40):
        chunk = rows[i:i + 40]
        values = ",\n".join(
            f"({q(str(r['visit_date']))}, {r['employee_ref']}, {q(r['trainer_name'])}, "
            f"{q(r['vehicle_reg'])}, {q(r['service_id'])}, {q(r['booked_start'])}, "
            f"{q(r['arrived_at'])}, {q(r['left_at'])}, {q(r['minutes_on_site'])}, "
            f"{q(r['site_lat'])}, {q(r['site_lon'])}, {q(r['site_town'])}, {q(r['site_street'])}, "
            f"{q(r['booked_address'])}, {q(r['delivery_key'])}, {q(json.dumps(r['courses']))}::jsonb, "
            f"{q(json.dumps(r['stops']))}::jsonb, {q(r['status'])}, {q(r['note'])}, now())"
            for r in chunk)
        pf_sql(f"INSERT INTO hub.vehicle_site_visit {COLS} VALUES\n{values}\n{UPSERT}")
        written += len(chunk)

    # Overnight rest, for the Soldo nights-away comparison. Batched for the same reason.
    sids = sorted({p["service_id"] for p in people.values()
                   if p["service_id"] and p["is_trainer"]})
    print(f"  learning each van's usual overnight place from the 60 days before {days[0]} ...")
    usual = usual_rest_places(sids, days[0])
    for sid, (_, _, n) in sorted(usual.items()):
        who = next((k for k, v in people.items() if v["service_id"] == sid), "?")
        print(f"    {who:<22} usual overnight place seen on {n} of the last 60 nights")
    night_rows = []
    for day in days:
        for name, p in people.items():
            if not p["service_id"] or not p["is_trainer"]:
                continue
            rest = overnight(p["service_id"], day)
            if not rest:
                continue
            # AWAY is judged against where this van usually sleeps, not against the HR address.
            base = usual.get(p["service_id"])
            away = None
            if base:
                away = hav(base[0], base[1], rest["lat"], rest["lon"]) > HOME_RADIUS_M
            # km_from_home stays the distance from the ADDRESS ON FILE -- kept as the cross-check
            # that surfaces a stale HR record (Paul Baxter's van rests 44.7 km from his, every night).
            home = homes.get(p["employee_ref"])
            km = round(hav(home[0], home[1], rest["lat"], rest["lon"]) / 1000, 1) if home else None
            night_rows.append(
                f"({q(str(day))}, {q(p['vehicle_reg'])}, {q(p['service_id'])}, {p['employee_ref']}, "
                f"{q(rest['lat'])}, {q(rest['lon'])}, {q(rest['town'])}, {q(km)}, {q(away)}, now())")
    nights = 0
    for i in range(0, len(night_rows), 60):
        chunk = night_rows[i:i + 60]
        pf_sql("INSERT INTO hub.vehicle_night (night_of, vehicle_reg, service_id, employee_ref, "
               "rest_lat, rest_lon, rest_town, km_from_home, away_from_home, computed_at) VALUES\n"
               + ",\n".join(chunk) +
               "\nON CONFLICT (night_of, vehicle_reg) DO UPDATE SET rest_lat=EXCLUDED.rest_lat, "
               "rest_lon=EXCLUDED.rest_lon, rest_town=EXCLUDED.rest_town, "
               "km_from_home=EXCLUDED.km_from_home, away_from_home=EXCLUDED.away_from_home, "
               "computed_at=now()")
        nights += len(chunk)

    print(f"\n  wrote {written} visit row(s), {nights} overnight row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
