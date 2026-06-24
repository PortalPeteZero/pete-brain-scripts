#!/usr/bin/env python3
"""cron-tz.py — convert a Lanzarote-local cron to the UTC cron Railway needs (DST-aware).

Railway evaluates cron schedules in UTC — there is NO per-cron timezone. Pete's crons mean a
LOCAL Lanzarote (Atlantic/Canary) time: "07:00 briefing", "18:00 finance email", "18:10 journal".
Atlantic/Canary is UTC+0 in winter (WET) and UTC+1 in summer (WEST), so the correct UTC schedule
differs by season. This shifts the hour by the CURRENT Canary→UTC offset.

⚠ DST: the result is only correct for the current season. RE-DERIVE at each DST boundary
(last Sun of March → summer; last Sun of October → winter) and re-set the Railway schedules.
(The cloud agent can own this twice-yearly job.)

Usage:
  cron-tz.py "0 7 * * *"                # → UTC cron for 07:00 Lanzarote, today's DST
  cron-tz.py "30 18 * * 1-5" Atlantic/Canary
Importable: from cron_tz import local_to_utc
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

def offset_hours(tz="Atlantic/Canary"):
    """Current whole-hour offset of the zone from UTC (0 winter / 1 summer for Atlantic/Canary)."""
    return int(datetime.now(ZoneInfo(tz)).utcoffset().total_seconds() // 3600)

def local_to_utc(expr, tz="Atlantic/Canary"):
    """Return (utc_cron, offset, crosses_midnight). Only the hour field is shifted; a numeric hour
    that goes below 0 wraps to the previous day (day-of-week / day-of-month would then need care —
    flagged, since Pete's crons sit well inside the day)."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("need a 5-field cron: 'M H DOM MON DOW'")
    m, h, dom, mon, dow = parts
    off = offset_hours(tz)
    if h.isdigit():
        raw = int(h) - off
        return " ".join([m, str(raw % 24), dom, mon, dow]), off, raw < 0
    return expr, off, False   # non-numeric hour (*, lists, ranges) — left as-is

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: cron-tz.py \"<local 5-field cron>\" [tz=Atlantic/Canary]")
    expr = sys.argv[1]
    tz = sys.argv[2] if len(sys.argv) > 2 else "Atlantic/Canary"
    utc, off, crossed = local_to_utc(expr, tz)
    season = "summer/WEST" if off == 1 else "winter/WET"
    print(f"local '{expr}'  ({tz}, UTC+{off}, {season})  →  Railway UTC '{utc}'"
          + ("   ⚠ crosses midnight — check DOW/DOM" if crossed else ""))
