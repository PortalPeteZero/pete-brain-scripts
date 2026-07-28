#!/usr/bin/env python3
# CRON-META
# what: Sygma fleet <- DVSA MOT sync. Per hub.fleet reg: upsert hub.vehicle_mot_tests, set fleet.mot_due from the latest expiry, append MOT odometer readings to vehicle_mileage, bump last_mileage when the MOT reading is newest.
# why: MOT dates + mileage maintain themselves from the official record instead of being hand-typed (Pete approved monthly, 28 Jul 2026)
# reads: DVSA MOT History API (via mot-api.py); Sygma Platform hub.fleet
# writes: Sygma Platform (rsczwfstwkthaybxhszy) hub.vehicle_mot_tests + hub.vehicle_mileage + hub.fleet (mot_due, last_mileage) via PostgREST service key
# entity: sygma
# schedule: 15 6 1 * *
# timezone: Atlantic/Canary
# secrets: dvsa-mot-history-api.json, sygma-portal-supabase-keys.json
# CRON-META-END
"""
Sygma fleet <- DVSA MOT sync: pull every fleet vehicle's MOT record and write it into the platform.

For each reg in hub.fleet (Sygma Platform DB rsczwfstwkthaybxhszy):
  1. upsert the full MOT test history into hub.vehicle_mot_tests (dedupe on test_number)
  2. set hub.fleet.mot_due from the latest test's expiry date (only when DVSA has one)
  3. insert each MOT odometer reading into hub.vehicle_mileage (note 'MOT test',
     recorded_by 'DVSA'; the table's (reg, date, mileage) unique key dedupes)
  4. if the newest MOT reading is newer than fleet.mileage_date AND larger than
     last_mileage, bump fleet.last_mileage/mileage_date (the fleet trigger's own
     insert then hits the dedupe key, so no double row)

Writes go through PostgREST with the service key (secret sygma-portal-supabase-keys.json)
-- NOT the Management API query endpoint (2026-05-29 lesson). DVSA access via mot-api.py
(secret dvsa-mot-history-api.json). A vehicle under 3 years old has no tests -- reported,
not an error. Manual run:  VAULT=/tmp/pbs python3 /tmp/pbs/fleet-mot-sync.py [--dry-run]

Runs monthly on Railway (1st, 06:15 Atlantic/Canary) -- Pete approved the schedule 28 Jul 2026.
"""
import os, sys, json, urllib.request, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")
DRY = "--dry-run" in sys.argv

_spec = importlib.util.spec_from_file_location("mot_api", f"{VAULT}/mot-api.py")
mot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mot)

with open(f"{VAULT}/Library/processes/secrets/sygma-portal-supabase-keys.json") as f:
    _keys = json.load(f)
REST = _keys["url"].rstrip("/") + "/rest/v1"
HDRS = {
    "apikey": _keys["service_role"],
    "Authorization": f"Bearer {_keys['service_role']}",
    "Content-Type": "application/json",
    "Accept-Profile": "hub", "Content-Profile": "hub",
}


def rest(method, path, body=None, prefer=None):
    h = dict(HDRS)
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(REST + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"PostgREST {e.code} {method} {path}: {e.read().decode()[:300]}")


def main():
    fleet = rest("GET", "/fleet?select=vehicle_reg,mot_due,last_mileage,mileage_date&order=vehicle_reg")
    print(f"{len(fleet)} vehicles in hub.fleet{' (DRY RUN)' if DRY else ''}")
    totals = {"tests": 0, "mot_due_updates": 0, "mileage_rows": 0, "no_tests": 0, "errors": 0}
    for v in fleet:
        reg = v["vehicle_reg"]
        try:
            rec = mot.vehicle(reg)
        except SystemExit as e:
            print(f"  {reg}: DVSA error -- {e}")
            totals["errors"] += 1
            continue
        tests = rec.get("motTests", [])
        if not tests:
            print(f"  {reg}: no MOT tests on record (first MOT {rec.get('motTestDueDate', 'date unknown')})")
            totals["no_tests"] += 1
            # DVSA still tells us when the FIRST MOT is due on a new vehicle.
            due = rec.get("motTestDueDate")
            if due and due != v["mot_due"] and not DRY:
                rest("PATCH", f"/fleet?vehicle_reg=eq.{urllib.parse.quote(reg)}", {"mot_due": due})
                totals["mot_due_updates"] += 1
            continue

        rows = []
        for t in tests:
            rows.append({
                "vehicle_reg": reg,
                "test_number": t.get("motTestNumber"),
                "completed_date": (t.get("completedDate") or "")[:10] or None,
                "test_result": t.get("testResult"),
                "expiry_date": t.get("expiryDate"),
                "odometer_value": int(t["odometerValue"]) if t.get("odometerValue") and str(t.get("odometerValue")).isdigit() else None,
                "odometer_unit": t.get("odometerUnit"),
                "defects": t.get("defects", []),
            })
        if not DRY:
            rest("POST", "/vehicle_mot_tests?on_conflict=test_number", rows,
                 prefer="resolution=merge-duplicates,return=minimal")
        totals["tests"] += len(rows)

        latest = tests[0]
        expiry = latest.get("expiryDate")
        if expiry and expiry != v["mot_due"]:
            if not DRY:
                rest("PATCH", f"/fleet?vehicle_reg=eq.{urllib.parse.quote(reg)}", {"mot_due": expiry})
            totals["mot_due_updates"] += 1

        # Odometer readings -> mileage history (PASSED tests only; a fail can precede a same-day retest).
        mi_rows = [{"vehicle_reg": reg, "mileage": r["odometer_value"],
                    "reading_date": r["completed_date"], "note": "MOT test", "recorded_by": "DVSA"}
                   for r in rows if r["odometer_value"] and r["completed_date"] and r["test_result"] == "PASSED"
                   and (r["odometer_unit"] or "MI").upper().startswith("MI")]
        if mi_rows and not DRY:
            rest("POST", "/vehicle_mileage?on_conflict=vehicle_reg,reading_date,mileage", mi_rows,
                 prefer="resolution=ignore-duplicates,return=minimal")
        totals["mileage_rows"] += len(mi_rows)

        newest = max(mi_rows, key=lambda r: r["reading_date"], default=None)
        if newest and (v["mileage_date"] or "0000") < newest["reading_date"] and (v["last_mileage"] or 0) < newest["mileage"]:
            if not DRY:
                rest("PATCH", f"/fleet?vehicle_reg=eq.{urllib.parse.quote(reg)}",
                     {"last_mileage": newest["mileage"], "mileage_date": newest["reading_date"]})
            print(f"  {reg}: last_mileage {v['last_mileage']} -> {newest['mileage']} ({newest['reading_date']})")
        summary = f"{len(rows)} tests, latest {latest.get('testResult')} exp {expiry or '—'}"
        print(f"  {reg}: {summary}")
    print("TOTALS:", json.dumps(totals))


if __name__ == "__main__":
    import urllib.parse
    main()
