#!/usr/bin/env python3
"""lg-crosscheck.py — is our stored reading for a LeakGuard device the same as ThingsLog's?

THE RULE (Pete, 27 Jul 2026): ThingsLog's readings are the truth. Our `readings` table is a copy and
has been shown to corrupt data in at least three ways — future-dated rows poisoning the delta baseline,
negative counters discarded silently, half of every payload lost on an interval change. So "our
database says X" is EVIDENCE, NOT PROOF.

Run this before asserting any cause, rate or total for a device.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-crosscheck.py 04299212     # one device
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-crosscheck.py --all        # whole fleet
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-crosscheck.py 04299212 --series 2026-07-27

Exit code 1 if any device disagrees or cannot be checked, so it can gate a script.
"""
import json, os, ssl, subprocess, sys, urllib.request, urllib.error

BASE = "https://iot.thingslog.com:4443"
_ctx = ssl.create_default_context()
ENV = {**os.environ, "VAULT": os.environ.get("VAULT", "/tmp/pbs")}
TOL_M3 = 0.001          # one litre; below this the two agree


def _sql(q):
    r = subprocess.run(["python3", "/tmp/pbs/lg-sql.py", q],
                       capture_output=True, text=True, env=ENV)
    out = r.stdout.strip()
    i = out.find("[")
    if i < 0:
        raise SystemExit(f"SQL failed: {out[:300]}")
    return json.loads(out[i:])


def _creds():
    raw = subprocess.run(["python3", "/tmp/pbs/cc-sql.py",
                          "SELECT value FROM secrets WHERE name='thingslog-login.json'"],
                         capture_output=True, text=True, env=ENV).stdout
    return json.loads(json.loads(raw)[0]["value"])


def _login(c):
    body = json.dumps({"username": c["username"], "password": c["password"]}).encode()
    req = urllib.request.Request(c.get("base_url", BASE) + "/login", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, context=_ctx, timeout=30) as r:
        return r.headers.get("Authorization").replace("Bearer ", "")


def _get(tok, cid, path):
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": "Bearer " + tok, "Accept": "application/json",
        "X-Company-Id": str(cid), "User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, context=_ctx, timeout=40) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code}


def check(tok, cid, num, verbose=True):
    """Returns (ok, note). ok=False means disagreement or unverifiable."""
    tl = _get(tok, cid, f"/api/v2/devices/{num}/readings/current")
    tl_val = None
    if isinstance(tl, list):
        for s in tl:
            if s.get("sensorIndex") == 0 and s.get("reading") is not None:
                tl_val = float(s["reading"])
                tl_date = s.get("date")
    rows = _sql(f"""SELECT r.counter_m3, r.reading_time
                    FROM readings r JOIN devices d ON d.id=r.device_id
                    WHERE d.device_number='{num}' AND d.tl_output_index=0
                    ORDER BY r.reading_time DESC LIMIT 1""")
    ours = float(rows[0]["counter_m3"]) if rows else None

    if tl_val is None:
        note = "ThingsLog returned no sensor-0 reading — CANNOT VERIFY"
        ok = False
    elif ours is None:
        note = f"ThingsLog {tl_val} but we hold NO readings — CANNOT VERIFY"
        ok = False
    else:
        diff = abs(tl_val - ours)
        ok = diff <= TOL_M3
        note = (f"ThingsLog {tl_val:>10.3f} @ {tl_date}   ours {ours:>10.3f} @ {rows[0]['reading_time']}"
                f"   diff {diff*1000:,.0f} L")
    if verbose:
        print(f"  {'OK  ' if ok else 'FAIL'} {num}  {note}")
    return ok, note


def series(tok, cid, num, day):
    """Compare a full day's 15-min series. ThingsLog history may be absent for some devices."""
    tl = _get(tok, cid, f"/api/devices/{num}/0/counters?fromDate={day}T00:00:00Z&toDate={day}T23:59:00Z")
    if not isinstance(tl, list) or not tl:
        print(f"  ThingsLog holds NO series for {num} on {day}. "
              f"Use the current-reading check instead; our table is the only record.")
        return False
    ours = _sql(f"""SELECT r.reading_time, r.counter_m3
                    FROM readings r JOIN devices d ON d.id=r.device_id
                    WHERE d.device_number='{num}' AND d.tl_output_index=0
                      AND r.reading_time::date='{day}' ORDER BY r.reading_time""")
    ourmap = {o["reading_time"][11:16]: float(o["counter_m3"]) for o in ours}
    bad = 0
    for x in tl:
        # counter/reading dates carry a CORRECT +01:00 (unlike /api/transmissions — see docstring)
        hhmm = str(x.get("date"))[11:16]
        if x.get("counter") is None:
            continue
        o = ourmap.get(hhmm)
        if o is None or abs(float(x["counter"]) - o) > TOL_M3:
            bad += 1
            if bad <= 8:
                print(f"    {hhmm}  ThingsLog {x['counter']}  ours {o}")
    print(f"  {'OK  ' if bad == 0 else 'FAIL'} {num} {day}: {len(tl)} ThingsLog rows, "
          f"{len(ours)} ours, {bad} disagree")
    return bad == 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    c = _creds(); tok = _login(c); cid = c.get("company_id")

    if "--series" in args:
        i = args.index("--series")
        num, day = args[0], args[i + 1]
        sys.exit(0 if series(tok, cid, num, day) else 1)

    if "--all" in args:
        # Installed devices only. Unassigned spares correctly hold no readings, and DEMO0001 is
        # synthetic — neither is a cross-check failure, so they would only make the gate cry wolf.
        nums = [d["device_number"] for d in _sql(
            "SELECT device_number FROM devices WHERE tl_output_index=0 AND is_active "
            "AND property_id IS NOT NULL AND device_number <> 'DEMO0001' "
            "ORDER BY device_number")]
    else:
        nums = [args[0]]

    print(f"ThingsLog vs our readings — {len(nums)} device(s), tolerance {TOL_M3*1000:.0f} L\n")
    fails = [n for n in nums if not check(tok, cid, n)[0]]
    print(f"\n{len(nums)-len(fails)} agree, {len(fails)} disagree/unverifiable"
          + (f": {', '.join(fails)}" if fails else ""))
    sys.exit(1 if fails else 0)
