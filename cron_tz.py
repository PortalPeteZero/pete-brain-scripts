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

def _shift_hour_token(tok, off):
    """Shift ONE hour-field token (a single hour, or an 'a-b' range) by -off, wrapping at 24.
    Returns (shifted_token, crossed_midnight). '*' / '*/n' steps can't be offset meaningfully → left as-is."""
    if tok.isdigit():
        raw = int(tok) - off
        return str(raw % 24), raw < 0
    if "-" in tok:
        a, _, b = tok.partition("-")
        if a.isdigit() and b.isdigit():
            ra, rb = int(a) - off, int(b) - off
            return f"{ra % 24}-{rb % 24}", (ra < 0 or rb < 0)
    return tok, False

def local_to_utc(expr, tz="Atlantic/Canary"):
    """Return (utc_cron, offset, crosses_midnight). EVERY element of the hour field is shifted — a single
    hour, a comma-list ('7,22') AND a range ('9-17') — fixing the old bug where a list/range passed through
    UNCONVERTED (so a local '0 7,22' silently fired at 08:00/23:00 in summer). '*' and steps are left as-is."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("need a 5-field cron: 'M H DOM MON DOW'")
    m, h, dom, mon, dow = parts
    off = offset_hours(tz)
    if off == 0 or h == "*":
        return expr, off, False                        # winter (no shift) or every-hour → unchanged
    shifted, crossed = [], False
    for tok in h.split(","):
        s, c = _shift_hour_token(tok, off)
        shifted.append(s); crossed = crossed or c
    return " ".join([m, ",".join(shifted), dom, mon, dow]), off, crossed

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: cron-tz.py \"<local 5-field cron>\" [tz=Atlantic/Canary]")
    expr = sys.argv[1]
    tz = sys.argv[2] if len(sys.argv) > 2 else "Atlantic/Canary"
    utc, off, crossed = local_to_utc(expr, tz)
    season = "summer/WEST" if off == 1 else "winter/WET"
    print(f"local '{expr}'  ({tz}, UTC+{off}, {season})  →  Railway UTC '{utc}'"
          + ("   ⚠ crosses midnight — check DOW/DOM" if crossed else ""))
