#!/usr/bin/env python3
"""cd-daily-briefing.py — the MERGED CD daily briefing launcher (weekdays + Sunday-for-Monday).

Runs the two self-contained briefing scripts in order, each targeting TOMORROW's events:
  1. cd-team-briefing.py    — CD field jobs from Odoo → the team (gated to Pete until BRIEFING_LIVE=1)
  2. pete-personal-briefing.py — Pete's Google Calendar → Pete only

Replaces the two Cowork SKILL.md crons cd-daily-briefing-weekdays (Tue–Sat) + cd-daily-briefing-sunday
(Mon) with ONE Railway service on `15 18 * * 0-5` (Sun–Fri 18:15 Lanzarote). cd-week-ahead stays
separate (different --window week output). Both child scripts read Odoo/Calendar live + send via the
SA-backed gmail-api; this launcher just sequences them and reports. The daily-note mirror the old
SKILL.md wrote is dropped (the emails are the output).
"""
# CRON-META
# what: CD team briefing (Odoo) + Pete's personal briefing (Calendar) for tomorrow, weekday + Sunday
# why: The CD field team + Pete get tomorrow's jobs/calendar the evening before, from one cloud run
# reads: Odoo (CD jobs) + Google Calendar (Pete)
# writes: 2 emails (team briefing gated to Pete until BRIEFING_LIVE=1; personal to Pete)
# entity: canary-detect
# report: cd-briefings
# schedule: 15 18 * * 0-5
# timezone: Atlantic/Canary
# CRON-META-END
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def run(name, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, name), *args], capture_output=True, text=True)
    print(f"=== {name} ===")
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode != 0:
        print(f"[exit {r.returncode}] {r.stderr.strip()[:400]}")
    return r.returncode


def main():
    rc_team = run("cd-team-briefing.py")
    rc_pete = run("pete-personal-briefing.py")
    print(f"cd-daily-briefing: team rc={rc_team} personal rc={rc_pete}")
    return 1 if (rc_team or rc_pete) else 0


if __name__ == "__main__":
    sys.exit(main())
