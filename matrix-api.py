#!/usr/bin/env python3
"""matrix-api.py -- the ONE sanctioned path to Matrix Telematics, the vehicle trackers fitted to
the Sygma fleet. Supplied through Royle Auto Services (Chris Royle); the API details came from
Matrix support via Chris on 5 Aug 2026.

    VAULT=/tmp/pbs python3 /tmp/pbs/matrix-api.py vehicles
    VAULT=/tmp/pbs python3 /tmp/pbs/matrix-api.py where "YR68 JYU"
    VAULT=/tmp/pbs python3 /tmp/pbs/matrix-api.py journeys "YR68 JYU" --days 7
    VAULT=/tmp/pbs python3 /tmp/pbs/matrix-api.py reconcile

AUTH. POST /OAuth/token with {"client_id":"0","grant_type":"password","username":..,"password":..}
and header `content-type: text/json`. Returns a 1-hour access_token plus a refresh_token. The token
is cached in /tmp so a session does not re-authenticate on every call.
An earlier attempt on 3 Aug 2026 used `POST api/Account` and was recorded as "credentials rejected".
That was the wrong endpoint, not bad credentials -- Matrix retired that auth and support said so:
"All the calls that are not in the OAuthMethods section use an older authentication method that is
no longer supported."

ROUTE PREFIX IS `apioauth`, NOT `api/oauth`. api/oauth/... returns 404. Cost me a wrong turn.

WHAT THIS IS NOT. The tracker is NOT the vehicle register. Matrix names each unit with free text
typed by whoever installed it -- "MARK PEARCE FL25 CHJ", "SYGMA VAN HW70 XPF" -- and that string is
the SUPPLIER's, not ours. It has been wrong: on 5 Aug 2026 those labels were reported as fleet facts
and got both the vehicle type (a Kia Sportage called a van) and the fleet membership wrong. The
register is hub.fleet on the Sygma Platform. Use `reconcile` before believing anything about which
vehicles exist. The tracker answers WHERE A UNIT IS; hub.fleet answers WHAT WE RUN AND WHO DRIVES IT.

FOR THE RECORD, from Matrix's email of 5 Aug 2026: they prefer requests of a month or less at a
time, and asked to be contacted before any high-frequency polling. Recorded as information, not
enforced here -- how we use our own supplier's API is Pete's call.

271 of the 706 documented endpoints are OAuth-era (the rest use the retired auth). 41 of the 45
relevant reads were reachable on our account when probed 5 Aug 2026. Anything not wrapped below is
reachable with `raw`. Full help: https://restapi.matrixtelematics.com/help
"""
import json, os, sys, time, datetime, urllib.request, urllib.error, argparse

VAULT = os.environ.get("VAULT", "/tmp/pbs")
BASE = "https://restapi.matrixtelematics.com/"
CRED_FILE = f"{VAULT}/Library/processes/secrets/matrix-telematics-portal-login"
TOKEN_CACHE = "/tmp/.matrix-token.json"
PLATFORM_REF = "rsczwfstwkthaybxhszy"          # Sygma Platform -- where hub.fleet lives


# ---------------------------------------------------------------- auth
def _fresh_token():
    c = json.load(open(CRED_FILE))
    body = json.dumps({"client_id": "0", "grant_type": "password",
                       "username": c["username"], "password": c["password"]}).encode()
    req = urllib.request.Request(BASE + "OAuth/token", data=body,
                                 headers={"content-type": "text/json"}, method="POST")
    d = json.loads(urllib.request.urlopen(req, timeout=45).read().decode())
    d["_expires_at"] = time.time() + int(d.get("expires_in", 3600)) - 60
    try:
        os.umask(0o077)
        json.dump(d, open(TOKEN_CACHE, "w"))
    except OSError:
        pass
    return d


def token():
    """Cached token; refreshes silently when it has expired."""
    try:
        d = json.load(open(TOKEN_CACHE))
        if d.get("_expires_at", 0) > time.time():
            return d["access_token"]
    except Exception:
        pass
    return _fresh_token()["access_token"]


def call(path, method="GET", body=None, _retry=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path.lstrip("/"), data=data, method=method,
                                 headers={"Authorization": "Bearer " + token(),
                                          "content-type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=90).read().decode() or "null")
    except urllib.error.HTTPError as e:
        if e.code == 401 and _retry:          # token died early -- one clean retry
            try:
                os.remove(TOKEN_CACHE)
            except OSError:
                pass
            return call(path, method, body, _retry=False)
        sys.exit(f"matrix-api: HTTP {e.code} on {path}\n{e.read().decode('utf-8','replace')[:400]}")


# ---------------------------------------------------------------- dates
def window(args):
    to = datetime.datetime.now()
    frm = to - datetime.timedelta(days=args.days)
    if args.frm:
        frm = datetime.datetime.strptime(args.frm, "%Y-%m-%d")
    if args.to:
        to = datetime.datetime.strptime(args.to, "%Y-%m-%d") + datetime.timedelta(days=1)
    return frm.strftime("%Y%m%d%H%M%S"), to.strftime("%Y%m%d%H%M%S")


# ---------------------------------------------------------------- fleet lookup
def group_id():
    g = call("apioauth/v4/group?groupID=0")
    if not g:
        sys.exit("matrix-api: no groups visible to this account.")
    return g[0]["ID"]


def units():
    """Every tracker unit, as Matrix holds it. Supplier free-text labels -- see the module note."""
    return call(f"apioauth/v4/servicestatuslite?serviceID=0&groupID={group_id()}")


def _norm(r):
    return "".join(str(r or "").upper().split())


def resolve(needle):
    """Registration (or service id) -> the unit. Matches the reg INSIDE the free-text label."""
    if str(needle).isdigit():
        for u in units():
            if str(u["serviceID"]) == str(needle):
                return u
        sys.exit(f"matrix-api: no unit with service id {needle}")
    n = _norm(needle)
    hits = [u for u in units() if n in _norm(u.get("vehicleRegistration"))]
    if not hits:
        sys.exit(f"matrix-api: no tracker unit matching {needle!r}. `vehicles` lists them all; a "
                 f"vehicle in hub.fleet may simply not be fitted with a tracker.")
    if len(hits) > 1:
        sys.exit("matrix-api: ambiguous -- " +
                 ", ".join(f"{h['serviceID']}={h.get('vehicleRegistration')}" for h in hits))
    return hits[0]


# ---------------------------------------------------------------- hub.fleet (the real register)
def fleet_rows():
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    sql = ("SELECT f.vehicle_reg, f.make_model, f.category, s.full_name AS driver "
           "FROM hub.fleet f LEFT JOIN hub.staff_directory s "
           "ON s.employee_ref = f.current_driver_ref ORDER BY f.vehicle_reg")
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PLATFORM_REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


# ---------------------------------------------------------------- commands
def cmd_vehicles(a):
    rows = units()
    if a.json:
        return print(json.dumps(rows, indent=1))
    print(f"{'SERVICE':<9} {'UNIT LABEL (Matrix free text)':<34} {'LAST SEEN':<17} {'WHERE':<22} VOLTS")
    print("-" * 96)
    for u in sorted(rows, key=lambda r: r.get("vehicleRegistration") or ""):
        t = (u.get("gpsTime") or "").replace("T", " ")[:16]
        v = u.get("Voltage")
        print(f"{u['serviceID']:<9} {(u.get('vehicleRegistration') or '')[:33]:<34} {t:<17} "
              f"{(u.get('geoTown') or '')[:21]:<22} {('%.1f' % v) if isinstance(v, (int, float)) else '-'}")
    print(f"\n{len(rows)} tracker unit(s). Labels are the supplier's text -- run `reconcile` for the "
          f"real register.")


def cmd_where(a):
    u = resolve(a.vehicle)
    loc = call("apioauth/v4/latestlocation", "POST", {"ServiceIDList": [u["serviceID"]]})
    if a.json:
        return print(json.dumps(loc, indent=1))
    for L in loc:
        print(f"{u.get('vehicleRegistration')}  (service {u['serviceID']})")
        print(f"  {L.get('GPSLatitude')}, {L.get('GPSLongitude')}")
        print(f"  {L.get('GeoStreet')}, {L.get('GeoTown')} {L.get('GeoPostcode') or ''}".rstrip())
        print(f"  as at {(L.get('GPSTime') or u.get('gpsTime') or '').replace('T',' ')}")
        print(f"  https://www.google.com/maps?q={L.get('GPSLatitude')},{L.get('GPSLongitude')}")


def cmd_locations(a):
    rows = call(f"apioauth/v4/grouplatestlocation?groupID={group_id()}")
    if a.json:
        return print(json.dumps(rows, indent=1))
    by_svc = {u["serviceID"]: u.get("vehicleRegistration") for u in units()}
    print(f"{'UNIT LABEL':<34} {'LAT':>10} {'LON':>10}  WHERE")
    print("-" * 92)
    for L in rows:
        print(f"{(by_svc.get(L.get('ServiceId')) or '?')[:33]:<34} {L.get('GPSLatitude'):>10} "
              f"{L.get('GPSLongitude'):>10}  {L.get('GeoStreet') or ''}, {L.get('GeoTown') or ''}")


def cmd_journeys(a):
    u = resolve(a.vehicle)
    frm, to = window(a)
    rows = call(f"apioauth/v4/journey?serviceID={u['serviceID']}&from={frm}&to={to}"
                f"&showTowJourney=false")
    if a.json:
        return print(json.dumps(rows, indent=1))
    print(f"{u.get('vehicleRegistration')}  --  {len(rows)} journey(s), {a.days} day(s)\n")
    print(f"{'START':<17} {'END':<17} {'FROM':<24} {'TO':<24} MILES")
    print("-" * 96)
    total = 0.0
    for j in rows:
        d = j.get("Distance") or j.get("Mileage") or 0
        total += d if isinstance(d, (int, float)) else 0
        print(f"{(j.get('LocalTimeStartDate') or '').replace('T',' ')[:16]:<17} "
              f"{(j.get('LocalTimeEndDate') or '').replace('T',' ')[:16]:<17} "
              f"{(j.get('StartTown') or j.get('StartStreet') or '')[:23]:<24} "
              f"{(j.get('EndTown') or j.get('EndStreet') or '')[:23]:<24} {d}")
    print(f"\ntotal distance: {total:.1f}  (Matrix's own units -- check before quoting as miles)")


def cmd_mileage(a):
    frm, to = window(a)
    rows = call(f"apioauth/v4/group/{group_id()}/service/distanceduration")
    if a.json:
        return print(json.dumps(rows, indent=1))
    by_svc = {u["serviceID"]: u.get("vehicleRegistration") for u in units()}
    print(f"{'UNIT LABEL':<34} {'DISTANCE':>12} {'DURATION':>12}")
    print("-" * 60)
    for r in rows:
        print(f"{(by_svc.get(r.get('ServiceId')) or '?')[:33]:<34} "
              f"{r.get('ServiceDistance'):>12} {r.get('ServiceDuration'):>12}")


def cmd_odometer(a):
    u = resolve(a.vehicle)
    d = call(f"apioauth/matrixv2/services/{u['serviceID']}/odometer")
    print(json.dumps(d, indent=1) if a.json else
          f"{u.get('vehicleRegistration')}: distance {d.get('Distance')}, duration "
          f"{d.get('Duration')}, last updated {d.get('LastUpdated')}")


def cmd_score(a):
    u = resolve(a.vehicle)
    frm, to = window(a)
    d = call(f"apioauth/v4/driverscore?serviceID={u['serviceID']}&from={frm}&to={to}")
    print(json.dumps(d, indent=1))


def cmd_telemetry(a):
    u = resolve(a.vehicle)
    frm, to = window(a)
    rows = call(f"apioauth/v4/telem?serviceID={u['serviceID']}&from={frm}&to={to}&msgType=0")
    print(json.dumps(rows, indent=1) if a.json else
          f"{u.get('vehicleRegistration')}: {len(rows)} telemetry record(s) over {a.days} day(s)")


def cmd_alerts(a):
    u = resolve(a.vehicle)
    frm, to = window(a)
    print(json.dumps(call(f"apioauth/matrixv2/services/{u['serviceID']}/alertlogs"
                          f"?startDate={frm}&endDate={to}"), indent=1))


def cmd_reconcile(a):
    """The check that stops a supplier's label being reported as our fleet."""
    tracked = units()
    fleet = fleet_rows()
    freg = {_norm(f["vehicle_reg"]): f for f in fleet}

    matched, unregistered = [], []
    for u in tracked:
        label = _norm(u.get("vehicleRegistration"))
        hit = next((f for k, f in freg.items() if k and k in label), None)
        (matched if hit else unregistered).append((u, hit))

    matched_regs = {_norm(h["vehicle_reg"]) for _, h in matched if h}
    untracked = [f for f in fleet if _norm(f["vehicle_reg"]) not in matched_regs]

    if a.json:
        return print(json.dumps({
            "matched": [{"service_id": u["serviceID"], "label": u.get("vehicleRegistration"),
                         "reg": h["vehicle_reg"], "vehicle": h["make_model"],
                         "driver": h["driver"]} for u, h in matched],
            "tracked_not_registered": [{"service_id": u["serviceID"],
                                        "label": u.get("vehicleRegistration")}
                                       for u, _ in unregistered],
            "registered_not_tracked": untracked}, indent=1))

    print(f"MATCHED -- tracker unit reconciled to hub.fleet ({len(matched)})")
    print(f"  {'REG':<10} {'VEHICLE':<30} {'DRIVER':<20} SERVICE")
    for u, h in sorted(matched, key=lambda x: x[1]["vehicle_reg"]):
        print(f"  {h['vehicle_reg']:<10} {(h['make_model'] or '')[:29]:<30} "
              f"{(h['driver'] or '-')[:19]:<20} {u['serviceID']}")

    print(f"\nTRACKED BUT NOT IN hub.fleet ({len(unregistered)})"
          + ("  <- a unit reporting against no vehicle record" if unregistered else ""))
    for u, _ in unregistered:
        print(f"  service {u['serviceID']}  label {u.get('vehicleRegistration')!r}  "
              f"last seen {(u.get('gpsTime') or '').replace('T',' ')[:16]} {u.get('geoTown') or ''}")

    print(f"\nIN hub.fleet BUT NOT TRACKED ({len(untracked)})")
    for f in untracked:
        print(f"  {f['vehicle_reg']:<10} {(f['make_model'] or '')[:29]:<30} {f['driver'] or '-'}")

    print("\nhub.fleet is the register. A tracker label is the supplier's free text and is not "
          "evidence that a vehicle exists, what it is, or who drives it.")


def cmd_raw(a):
    print(json.dumps(call(a.path, a.method, json.loads(a.body) if a.body else None), indent=1))


def cmd_endpoints(a):
    print(__doc__.strip().split("271 of")[1].strip()[:400] if a else "")


# ---------------------------------------------------------------- cli
def main():
    p = argparse.ArgumentParser(description="Matrix Telematics -- Sygma vehicle trackers",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help, vehicle=False, dates=False):
        s = sub.add_parser(name, help=help)
        if vehicle:
            s.add_argument("vehicle", help="registration (e.g. 'YR68 JYU') or service id")
        if dates:
            s.add_argument("--days", type=int, default=7, help="days back from now (default 7)")
            s.add_argument("--from", dest="frm", help="YYYY-MM-DD")
            s.add_argument("--to", help="YYYY-MM-DD")
        s.add_argument("--json", action="store_true", help="raw JSON")
        s.set_defaults(fn=fn)
        return s

    add("vehicles", cmd_vehicles, "every tracker unit, with last position and voltage")
    add("where", cmd_where, "where one vehicle is now", vehicle=True)
    add("locations", cmd_locations, "latest position of every unit")
    add("journeys", cmd_journeys, "journeys for one vehicle", vehicle=True, dates=True)
    add("mileage", cmd_mileage, "distance + duration for the whole group", dates=True)
    add("odometer", cmd_odometer, "current odometer for one vehicle", vehicle=True)
    add("score", cmd_score, "driver behaviour score", vehicle=True, dates=True)
    add("telemetry", cmd_telemetry, "raw telemetry records", vehicle=True, dates=True)
    add("alerts", cmd_alerts, "alert log for one vehicle", vehicle=True, dates=True)
    add("reconcile", cmd_reconcile, "tracker units vs hub.fleet -- run this before believing labels")

    r = sub.add_parser("raw", help="any of the 271 OAuth endpoints (see /help)")
    r.add_argument("path", help="e.g. apioauth/matrixv2/devicetypes")
    r.add_argument("--method", default="GET")
    r.add_argument("--body", help="JSON body")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_raw)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
