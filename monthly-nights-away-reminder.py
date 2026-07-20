#!/usr/bin/env python3
"""monthly-nights-away-reminder.py — last-Friday-of-month email to the 6 card-holding trainers asking
them to log their nights-away count on Sunday's calendar entry. Headless extraction of the Cowork SKILL.

Deterministic: only acts on the last Friday of the month (exits silently otherwise). Sends a clean 1:1
email per trainer. Live to the trainers by DEFAULT (recipients verified 2026-07-07); set NIGHTS_PREVIEW=1
to route every email to Pete for a test run. NIGHTS_FORCE=1 bypasses the last-Friday guard for a
verification run. Live-by-default means a service rebuilt from scratch can never revert to preview.
"""
# CRON-META
# what: Last-Friday-of-month reminder to the 6 card-holding trainers to log nights-away
# why: So trainers log nights worked away on Sunday's calendar entry (cross-refs Soldo card spend)
# reads: schedule (date logic) + hard-coded trainer roster
# writes: 1:1 emails to trainers (live by default; NIGHTS_PREVIEW=1 routes to Pete for a test run)
# entity: sygma
# report:
# schedule: 0 9 * * 5
# timezone: Atlantic/Canary
# CRON-META-END
import os
import sys
import importlib.util
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Atlantic/Canary")
PETE = "pete.ashcroft@sygma-solutions.com"
LIVE = os.environ.get("NIGHTS_PREVIEW") != "1"       # live by default; NIGHTS_PREVIEW=1 → route all to Pete
FORCE = os.environ.get("NIGHTS_FORCE") == "1"        # bypass the last-Friday guard (verification)

TRAINERS = [
    ("Andy", "andy.bartholomew@sygma-solutions.com"),
    ("Andrew", "andrew.foster@sygma-solutions.com"),
    ("Gareth", "gareth.phillips@sygma-solutions.com"),
    ("Geoff", "geoff.astley@sygma-solutions.com"),
    ("Kevin", "kevin.morley@sygma-solutions.com"),   # added 20 Jul 2026 — real trainer, was missing
    ("Mark", "mark.pearce@sygma-solutions.com"),
    ("Neal", "neal.sadd@sygma-solutions.com"),
]


def is_last_friday(d):
    return d.weekday() == 4 and (d + timedelta(days=7)).month != d.month


def main():
    today = datetime.now(TZ).date()
    if not (is_last_friday(today) or FORCE):
        print("monthly-nights-away: not the last Friday of the month, skipping.")
        return 0
    sunday = today + timedelta(days=2)
    sunday_str = sunday.strftime("%d %B %Y").lstrip("0")
    month_str = today.strftime("%B")

    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("gmail_api", os.path.join(here, "gmail-api.py"))
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)
    g = gm.GmailAPI()

    sent, errors = [], []
    for first, email in TRAINERS:
        subject = f"Nights worked away, please log {month_str}"
        body = (
            f"Hi {first},\n\n"
            f"Quick reminder, this Sunday {sunday_str} you'll see a \"Nights worked away\" event in your calendar.\n\n"
            f"Please open it and add your total nights away from home for {month_str} (a number is fine, e.g. \"11\" or \"12 nights\").\n\n"
            f"Why it helps: it lets us cross-reference Soldo card spend (hotels, food, fuel, vehicle) against actual time "
            f"on the road, so we can spot anything that needs reviewing and keep your accounts clean.\n\n"
            f"Cheers,\nPete"
        )
        to = email if LIVE else PETE
        if not LIVE:
            subject = f"[would-send to {first}] " + subject
        try:
            r = g.send(to=to, subject=subject, body=body)
            sent.append(f"{first}({r.get('id', 'ok')})")
        except Exception as e:
            errors.append(f"{first}: {e}")
    print(f"monthly-nights-away: live={LIVE} sent={len(sent)} [{', '.join(sent)}] errors={errors or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
