#!/usr/bin/env python3
"""trainer-home-points.py -- turn each trainer's HR home address into a map point, once.

    VAULT=/tmp/pbs python3 /tmp/pbs/trainer-home-points.py [--dry]

The address itself stays in hub.staff_hr where it already lives. Only the coordinates are copied to
hub.trainer_home_point, which is admin/owner only by RLS -- the Vehicle Tracking page is open to all
staff, but where a colleague lives is not the same kind of information as what time a van reached a
customer site, and only the second one is being opened up (Pete, 5 Aug 2026).

Used to answer one question: did the van rest at home that night, or was the trainer away? That
feeds the nights-away figure on the Soldo expenses report, which until now has been whatever the
trainer wrote in a calendar entry.

This script PRINTS NO ADDRESSES and no coordinates. It reports counts and names only.
"""
import argparse, importlib.util, json, os, sys, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PLATFORM_REF = "rsczwfstwkthaybxhszy"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def pf_sql(sql):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PLATFORM_REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=90).read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    geo = _load("geo", f"{VAULT}/geocoding-api.py")
    rows = pf_sql("""
        SELECT s.employee_ref, s.full_name, h.home_address
        FROM hub.staff_directory s
        JOIN hub.staff_hr h ON h.employee_ref = s.employee_ref
        WHERE s.trainer_id IS NOT NULL
          AND s.employment_status = 'Active'
          AND h.home_address IS NOT NULL AND h.home_address <> ''
        ORDER BY s.full_name""")
    print(f"{len(rows)} active trainer(s) with a home address on file")

    done = fail = 0
    for r in rows:
        try:
            # geocoding-api.py defaults to Lanzarote/Spain (it was built for Canary Detect):
            # region="es", components country:ES, and it APPENDS ", Lanzarote, Spain" to any
            # address that does not already mention them. Every Sygma trainer lives in the UK, so
            # all three defaults have to be turned off or the home points land in the Canaries.
            hit = geo.geocode(r["home_address"], region="uk", bias_country="GB",
                              auto_lanzarote=False)
        except Exception as e:
            print(f"  FAILED  {r['full_name']:<22} ({type(e).__name__})")
            fail += 1
            continue
        if not hit:
            print(f"  FAILED  {r['full_name']:<22} (no match)")
            fail += 1
            continue
        lat, lon = hit["lat"], hit["lon"]
        conf = hit.get("location_type") or hit.get("type") or "unknown"
        if a.dry:
            print(f"  would set  {r['full_name']:<22} confidence {conf}")
            done += 1
            continue
        pf_sql(f"""
INSERT INTO hub.trainer_home_point (employee_ref, lat, lon, confidence, geocoded_at)
VALUES ({r['employee_ref']}, {lat}, {lon}, $g${conf}$g$, now())
ON CONFLICT (employee_ref) DO UPDATE SET
  lat=EXCLUDED.lat, lon=EXCLUDED.lon, confidence=EXCLUDED.confidence, geocoded_at=now()""")
        print(f"  set     {r['full_name']:<22} confidence {conf}")
        done += 1

    print(f"\n{done} point(s) {'previewed' if a.dry else 'stored'}, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
