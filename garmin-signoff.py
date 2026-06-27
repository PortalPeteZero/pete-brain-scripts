#!/usr/bin/env python3
"""garmin-signoff.py — set Pete's confirmed sleep sign-off for a date. CC-only.

Writes garmin_daily.snapshot.signoff.confirmed
in the Command Centre — Mac/Drive-independent. The brain calls this when Pete corrects the morning
sign-off estimate; garmin-daily-cc._preserve_signoff keeps it across cron re-runs.

Usage:  VAULT=/tmp/pbs python3 /tmp/pbs/garmin-signoff.py --set 2026-06-27 23:00
"""
import os, sys, json, argparse, urllib.request
from pathlib import Path


def _cc():
    url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        kp = Path(os.environ.get("VAULT", "/tmp/pbs")) / "Library/processes/secrets/command-centre-supabase-keys.json"
        kd = json.loads(kp.read_text()); url, key = kd["url"], kd["service_role_key"]
    return url.rstrip("/"), {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}


def set_signoff(date_iso: str, hhmm: str) -> int:
    base, hdr = _cc()
    rows = json.loads(urllib.request.urlopen(
        urllib.request.Request(base + f"/rest/v1/garmin_daily?date=eq.{date_iso}&select=snapshot", headers=hdr),
        timeout=30).read())
    if not rows:
        print(f"No garmin_daily row for {date_iso} in the CC — nothing to set.", file=sys.stderr)
        return 1
    snap = rows[0].get("snapshot") or {}
    so = snap.get("signoff") or {"detected": None, "detected_iso": None, "source": "pete-confirmed"}
    so["confirmed"] = hhmm
    snap["signoff"] = so
    urllib.request.urlopen(urllib.request.Request(
        base + f"/rest/v1/garmin_daily?date=eq.{date_iso}",
        data=json.dumps({"snapshot": snap}).encode(), headers={**hdr, "Prefer": "return=minimal"}, method="PATCH"),
        timeout=30)
    print(f"Sign-off for {date_iso} set to {hhmm} (confirmed) — written to the CC.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Set Pete's confirmed sleep sign-off (CC-only).")
    ap.add_argument("--set", nargs=2, metavar=("DATE", "HHMM"), required=True)
    a = ap.parse_args()
    sys.exit(set_signoff(a.set[0], a.set[1]))
