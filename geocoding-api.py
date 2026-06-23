#!/usr/bin/env python3
"""
geocoding-api.py -- Google Geocoding API helper.

Auth: API key (Geocoding API does NOT accept service-account JWT).
Key:  Library/processes/secrets/google-maps-api-key  (chmod 600)

API enabled in GCP project `sygma-seo-tools` from 2 May 2026.
Cost: $5 per 1,000 requests, $200/month free tier.

Usage:
  # forward (address -> coords)
  python3 geocoding-api.py geocode "Calle Apolo 31, Arrecife, Lanzarote, Spain"

  # reverse (coords -> address)
  python3 geocoding-api.py reverse 28.965759 -13.551138

  # batch from a file (one address per line)
  python3 geocoding-api.py batch addresses.txt
  python3 geocoding-api.py batch addresses.txt --out results.json

  # JSON output (default is human-readable)
  python3 geocoding-api.py geocode "..." --json

Library use:
  from geocoding_api import geocode, reverse_geocode
  result = geocode("Calle Apolo 31, Arrecife")
  # -> {'lat': 28.965, 'lon': -13.551, 'formatted': '...', 'place_id': '...',
  #     'location_type': 'ROOFTOP', 'types': ['street_address'], 'raw': {...}}
  # Returns None on no result; raises on API/network failure.

Notes:
- For Spanish/Lanzarote addresses the helper auto-appends ", Lanzarote, Spain"
  if you don't include a country hint. Disable with bias=False.
- location_type values are ROOFTOP (best), RANGE_INTERPOLATED, GEOMETRIC_CENTER,
  APPROXIMATE (worst). For pure distance-matching anything ROOFTOP/RANGE is good;
  GEOMETRIC_CENTER means "centre of an urbanisation" so use a wider threshold.
- Free-text city + street is the cleanest input. Avoid noise like phone numbers,
  gate codes, contact names -- the helper does NOT clean these for you.

Built: 2 May 2026 alongside Vision API to support photo-job matching for the
Tom-jobs photo workflow ([[Properties/Canary Detect Mapping]]).
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

# $VAULT-aware so it runs headless on Railway (VAULT=/app), Mac-relative otherwise.
KEY_PATH = (os.path.join(os.environ["VAULT"], "Library/processes/secrets/google-maps-api-key")
            if os.environ.get("VAULT")
            else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-maps-api-key"))
GEOCODE_BASE = "https://maps.googleapis.com/maps/api/geocode/json"


def _key():
    with open(KEY_PATH) as f:
        return f.read().strip()


def _call(params, retries=3):
    """Single Geocoding API call with retry on 5xx."""
    url = GEOCODE_BASE + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def geocode(address, *, region="es", bias_country="ES", auto_lanzarote=True):
    """Forward geocode an address to lat/lon.

    Returns dict with lat, lon, formatted, place_id, location_type, types, raw
    on success; None on no result; raises on hard failure.
    """
    if auto_lanzarote and "lanzarote" not in address.lower() and "spain" not in address.lower():
        address = address + ", Lanzarote, Spain"
    params = {"address": address, "key": _key()}
    if region:
        params["region"] = region
    if bias_country:
        params["components"] = f"country:{bias_country}"
    data = _call(params)
    status = data.get("status")
    if status == "ZERO_RESULTS":
        return None
    if status != "OK":
        raise RuntimeError(f"Geocoding failed: {status} -- {data.get('error_message','')}")
    res = data["results"][0]
    loc = res["geometry"]["location"]
    return {
        "lat": loc["lat"],
        "lon": loc["lng"],
        "formatted": res["formatted_address"],
        "place_id": res.get("place_id", ""),
        "location_type": res["geometry"].get("location_type", ""),
        "types": res.get("types", []),
        "raw": res,
    }


def reverse_geocode(lat, lon, *, language="en"):
    """Reverse geocode coords to a street address.

    Returns dict like geocode(); None on no result.
    """
    params = {"latlng": f"{lat},{lon}", "language": language, "key": _key()}
    data = _call(params)
    if data.get("status") == "ZERO_RESULTS":
        return None
    if data.get("status") != "OK":
        raise RuntimeError(f"Reverse geocoding failed: {data.get('status')} -- {data.get('error_message','')}")
    res = data["results"][0]
    loc = res["geometry"]["location"]
    return {
        "lat": loc["lat"],
        "lon": loc["lng"],
        "formatted": res["formatted_address"],
        "place_id": res.get("place_id", ""),
        "location_type": res["geometry"].get("location_type", ""),
        "types": res.get("types", []),
        "raw": res,
    }


def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    json_out = "--json" in sys.argv
    args = [a for a in sys.argv[2:] if a != "--json" and not a.startswith("--out")]
    out_path = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), None)
    if not out_path:
        # support --out PATH (next-positional)
        for i, a in enumerate(sys.argv):
            if a == "--out" and i + 1 < len(sys.argv):
                out_path = sys.argv[i + 1]
                args = [x for x in args if x != out_path]
                break

    if cmd == "geocode":
        if len(args) < 1:
            print("Usage: geocoding-api.py geocode \"address\"")
            sys.exit(2)
        r = geocode(" ".join(args))
        if r is None:
            print("(no result)")
            sys.exit(1)
        if json_out:
            print(json.dumps(r, indent=2))
        else:
            print(f"  lat:           {r['lat']:.6f}")
            print(f"  lon:           {r['lon']:.6f}")
            print(f"  formatted:     {r['formatted']}")
            print(f"  location_type: {r['location_type']}")
            print(f"  types:         {', '.join(r['types'])}")
            print(f"  place_id:      {r['place_id']}")

    elif cmd == "reverse":
        if len(args) < 2:
            print("Usage: geocoding-api.py reverse LAT LON")
            sys.exit(2)
        r = reverse_geocode(float(args[0]), float(args[1]))
        if r is None:
            print("(no result)")
            sys.exit(1)
        if json_out:
            print(json.dumps(r, indent=2))
        else:
            print(f"  formatted: {r['formatted']}")
            print(f"  types:     {', '.join(r['types'])}")
            print(f"  place_id:  {r['place_id']}")

    elif cmd == "batch":
        if len(args) < 1:
            print("Usage: geocoding-api.py batch addresses.txt [--out results.json]")
            sys.exit(2)
        path = args[0]
        results = []
        with open(path) as f:
            addresses = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        for i, addr in enumerate(addresses, 1):
            try:
                r = geocode(addr)
                results.append({"input": addr, "result": r})
                tag = "OK" if r else "ZERO_RESULTS"
                if r:
                    print(f"  [{i:>3}/{len(addresses)}] {tag:<13} {addr[:60]:<60} -> {r['lat']:.5f}, {r['lon']:.5f} ({r['location_type']})")
                else:
                    print(f"  [{i:>3}/{len(addresses)}] {tag:<13} {addr[:60]}")
            except Exception as e:
                results.append({"input": addr, "error": str(e)})
                print(f"  [{i:>3}/{len(addresses)}] ERROR         {addr[:60]} -- {e}")
            time.sleep(0.05)  # polite, well under 50 req/s limit
        if out_path:
            with open(out_path, "w") as f:
                json.dump(results, f, indent=2)
            print(f"\nSaved {len(results)} results -> {out_path}")
        elif json_out:
            print(json.dumps(results, indent=2))

    else:
        print(__doc__)
        print(f"\nUnknown command: {cmd}")
        sys.exit(2)


if __name__ == "__main__":
    _cli()
