#!/usr/bin/env python3
"""xhale-cron.py -- the scheduled Xhale loop: post the daily stats line, and pull in anything new
from Loren. Six runs a day. Deliberately does NOT touch the calendar sync yet.

    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-cron.py --dry-run     # report only, writes nothing
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-cron.py               # the real run (cron mode)
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-cron.py --lookback 14 # widen the catch-up window

THREE PHASES, in order:

  1. STATS -- for every date in the lookback window with all four Garmin numbers and no stats entry
     already in Xhale, post `Sleep 79, 6h42 | RHR 52 | HRV 59` as a Diary session at order -1.
  2. INBOUND -- read BOTH of Loren's message channels and store anything new in
     public.health_coach_message.
  3. EMAIL -- if anything landed in either phase, email Pete the detail. Silent when there is
     nothing to say; six empty emails a day is noise, and Pete asked for detail "if there's
     anything new".

WHY THE LOOKBACK EXISTS (Pete, 6 Aug 2026): "sometimes if i dont open my phone it might not sync
until afternoon". Garmin data for a given night can appear hours or days late, so a run that only
ever looked at today would permanently lose those days. Each run re-checks the whole window and
fills any gap it finds. That is also what makes the whole thing self-healing after an outage.

WHAT MAKES IT SAFE TO RUN OFTEN -- every write to Xhale notifies Loren, so:
  * ALL FOUR Garmin values or nothing. Partial is a real state (resting HR often lands hours before
    the sleep score); posting early and correcting later is two notifications for one day.
  * Ask Xhale what is already there before writing. Reads are free and notify nobody.
  * Never post an empty skeleton. Blank `SC- HRS. RHR-HRV-` placeholders are what killed the habit
    when Pete did this by hand.
  * Messages are keyed on Xhale's own message_id, so re-reporting is impossible.

THE CONVERSION TRAP -- garmin_daily.sleep_hours is DECIMAL, Pete's format is h:mm. Copying it
across unconverted renders 6.7 as "6h07" when it means 6h42. Wrong on every line and nobody would
notice. See [[xhale-operating-sop]].
"""
import argparse
import datetime
import html as htmllib
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VAULT = Path(os.environ.get("VAULT", "/tmp/pbs"))
SCRIPT_DIR = Path(__file__).resolve().parent
PETE = "pete.ashcroft@sygma-solutions.com"
LOREN_CONTACT_ID = 3280
ATHLETE_ID = 33431
DIARY_DISCIPLINE_ID = 17
STATS_ORDER = -1
DEFAULT_LOOKBACK = 7

# HARD CUTOFF -- NEW MESSAGES ONLY, NO HISTORY. Pete, 6 Aug 2026: "I don't think we need the
# messages back-filling... Let's put a blocker in from now."
# A seed run on 6 Aug pulled 1,851 messages going back to 2024; he did not want them, and they were
# deleted. Anything sent before this instant is history and is never ingested, however many times
# this runs. Moving this date forward is fine; moving it BACK re-imports the archive.
INGEST_MESSAGES_FROM = "2026-08-06T21:38:00Z"

# CRON-META
# what: Posts Pete's daily sleep/RHR/HRV line into Xhale as a diary entry at the top of the day, and pulls anything new Loren has written back into the Command Centre, emailing him the detail.
# why: Both jobs were manual and both slipped -- the hand-kept stats line ran Oct 2025 to 6 Feb 2026 then stopped dead. Loren asks for the numbers daily and they already sit in Garmin.
# reads: CC garmin_daily; Xhale sessions + both message channels (per-session messages[] and the direct thread with contact 3280)
# writes: Xhale diary sessions (the stats line, order -1); CC public.health_coach_message; an email to Pete
# entity: personal
# schedule: 0 6,10,13,16,20,23 * * *
# timezone: Atlantic/Canary
# secrets: SECRETFILE__xhale-oauth-app.json, SECRETFILE__xhale-oauth-tokens.json, GOOGLE_SA_JSON, SUPABASE_TOKEN
# CRON-META-END


def load_helper(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_DIR / filename)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sql(query, raw=True):
    """cc-sql is the one door to the CC database."""
    args = ["python3", str(SCRIPT_DIR / "cc-sql.py"), query] + (["--raw"] if raw else [])
    r = subprocess.run(args, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "VAULT": str(VAULT)})
    if r.returncode != 0:
        raise SystemExit(f"cc-sql failed: {(r.stderr or r.stdout)[:300]}")
    out = (r.stdout or "").strip()
    if not raw:
        return out
    try:
        return json.loads(out) if out else []
    except json.JSONDecodeError:
        raise SystemExit(f"cc-sql returned non-JSON: {out[:300]}")


def prime_secrets_from_db():
    """Railway injects secrets as env vars at DEPLOY time, but the access token ROTATES. If we let
    the helper read a stale env token it would 401-and-refresh on every single run. So pull the
    current pair from the secrets table first and hand those to the helper instead."""
    for name, env in (("xhale-oauth-app.json", "XHALE_OAUTH_APP"),
                      ("xhale-oauth-tokens.json", "XHALE_OAUTH_TOKENS")):
        rows = sql(f"SELECT value FROM secrets WHERE name='{name}'")
        if not rows:
            raise SystemExit(f"secret {name} missing from public.secrets")
        os.environ[env] = rows[0]["value"]


def hhmm(decimal_hours):
    """Garmin gives decimal hours; Pete's format is h:mm. 6.7 -> 6h42, never 6h07."""
    d = float(decimal_hours)
    h = int(d)
    m = round((d - h) * 60)
    if m == 60:
        h, m = h + 1, 0
    return f"{h}h{m:02d}"


# ---------------------------------------------------------------- phase 1: stats
def phase_stats(xh, lookback, dry):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=lookback)
    garmin = {r["date"]: r for r in sql(
        "SELECT date, sleep_score, sleep_hours, resting_hr, hrv FROM garmin_daily "
        f"WHERE date BETWEEN '{start}' AND '{today}'")}

    existing = [s for s in xh.call("GET", f"/api/sessions/?start_date={start}&end_date={today}")
                if not s.get("deleted")]
    already = {s["date"] for s in existing
               if str(s.get("brief_description", "")).lower().startswith(("sleep ", "sc-", "sc "))}

    posted, waiting = [], []
    d = start
    while d <= today:
        ds = d.isoformat()
        g = garmin.get(ds)
        d += datetime.timedelta(days=1)
        if ds in already:
            continue
        if not g or any(g[k] is None for k in ("sleep_score", "sleep_hours", "resting_hr", "hrv")):
            waiting.append(ds)          # not an error - Garmin has not synced, or no watch worn
            continue
        title = (f"Sleep {g['sleep_score']}, {hhmm(g['sleep_hours'])} | "
                 f"RHR {g['resting_hr']} | HRV {g['hrv']}")
        if dry:
            posted.append((ds, title, None))
            continue
        r = xh.call("POST", "/api/sessions/", {"date": ds, "discipline_id": DIARY_DISCIPLINE_ID,
                                               "brief_description": title, "order": STATS_ORDER})
        posted.append((ds, title, r.get("id")))
    return posted, waiting


# ---------------------------------------------------------------- phase 2: inbound
def _too_old(sent_at):
    """The blocker. Anything sent before INGEST_MESSAGES_FROM is history and never comes in."""
    if not sent_at:
        return True                     # no timestamp, cannot prove it is new -> treat as history
    return str(sent_at)[:19] < INGEST_MESSAGES_FROM[:19]


def phase_inbound(xh, lookback, dry):
    """Loren writes in TWO places with ZERO overlap (verified across 1,794 messages). Reading one
    and not the other silently loses half the conversation. coach_comments is NOT a third channel -
    it is empty on every session she has ever touched.

    NEW MESSAGES ONLY -- see INGEST_MESSAGES_FROM. This is a running conversation, not an archive."""
    today = datetime.date.today()
    start = today - datetime.timedelta(days=max(lookback, 30))
    seen = {r["message_id"] for r in sql("SELECT message_id FROM health_coach_message")}
    found = []

    for s in xh.call("GET", f"/api/sessions/?start_date={start}&end_date={today}"):
        if s.get("deleted"):
            continue
        for m in (s.get("messages") or []):
            if m["id"] in seen or _too_old(m.get("datetime_sent")):
                continue
            found.append(dict(
                message_id=m["id"], channel="session", session_id=s["id"],
                session_date=s["date"],
                session_title=(s.get("brief_description") or s.get("discipline_title") or ""),
                sender="pete" if m.get("sender_user_id") == ATHLETE_ID else "loren",
                content=m.get("content") or "", sent_at=m.get("datetime_sent"),
                seen_at=m.get("datetime_seen")))

    direct = xh.call("GET", f"/api/contacts/{LOREN_CONTACT_ID}/messages/")
    for m in (direct if isinstance(direct, list) else [direct]):
        if not isinstance(m, dict) or m.get("id") in seen:
            continue
        if _too_old(m.get("datetime_sent")):
            continue
        found.append(dict(
            message_id=m["id"], channel="direct", session_id=None, session_date=None,
            session_title=None,
            sender="pete" if m.get("sender_user_id") == ATHLETE_ID else "loren",
            content=m.get("content") or "", sent_at=m.get("datetime_sent"),
            seen_at=m.get("datetime_seen")))

    if found and not dry:
        def q(v):
            """NULL only for genuinely absent values. An empty string is a real value here --
            Xhale does carry blank-content messages, and content is NOT NULL."""
            return "NULL" if v is None else "$q$%s$q$" % v

        def q_or_null(v):
            return "NULL" if v in (None, "") else "$q$%s$q$" % v
        values = ",".join(
            "(%d,%s,%s,%s,%s,%s,%s,%s,%s)" % (
                f["message_id"], q(f["channel"]),
                f["session_id"] if f["session_id"] else "NULL",
                q_or_null(f["session_date"]), q_or_null(f["session_title"]), q(f["sender"]),
                q(f["content"] or ""), q_or_null(f["sent_at"]), q_or_null(f["seen_at"]))
            for f in found)
        sql("INSERT INTO health_coach_message (message_id, channel, session_id, session_date, "
            "session_title, sender, content, sent_at, seen_at) VALUES " + values +
            " ON CONFLICT (message_id) DO NOTHING RETURNING message_id")
    return found


# ---------------------------------------------------------------- phase 3: email
def phase_email(posted, found, waiting, dry):
    if dry:
        # dry mode wrote nothing, so the DB queue is empty -- preview from what phase 2 FOUND
        unemailed = sorted(found, key=lambda m: str(m.get("sent_at") or ""))
    else:
        unemailed = sql("SELECT message_id, channel, session_date, session_title, sender, "
                        "content, sent_at FROM health_coach_message WHERE emailed_at IS NULL "
                        "ORDER BY sent_at")
    from_loren = [m for m in unemailed if m["sender"] == "loren"]
    if not posted and not from_loren:
        return None                      # nothing worth an email; stay quiet

    def esc(t):
        return htmllib.escape(str(t or ""))

    parts = ["<div style='font-family:system-ui,sans-serif;font-size:15px;line-height:1.5'>"]
    if from_loren:
        parts.append(f"<h2 style='margin:0 0 4px'>{len(from_loren)} new from Loren</h2>")
        for m in from_loren:
            where = (f"on <b>{esc(m['session_title'])}</b>, {esc(m['session_date'])}"
                     if m["channel"] == "session" else "direct message")
            parts.append(
                f"<div style='margin:14px 0;padding:10px 14px;border-left:3px solid #6b7cff;"
                f"background:#f6f7ff'><div style='font-size:12.5px;color:#666'>"
                f"{esc(str(m['sent_at'])[:16])} &middot; {where}</div>"
                f"<div style='margin-top:6px;white-space:pre-wrap'>{esc(m['content'])}</div></div>")
    if posted:
        parts.append(f"<h2 style='margin:18px 0 4px'>{len(posted)} stats line"
                     f"{'s' if len(posted) != 1 else ''} posted to Xhale</h2><ul>")
        for ds, title, _ in posted:
            parts.append(f"<li>{esc(ds)} &mdash; {esc(title)}</li>")
        parts.append("</ul>")
    if waiting:
        parts.append(f"<p style='font-size:12.5px;color:#777'>Still waiting on Garmin for "
                     f"{len(waiting)} day(s): {esc(', '.join(waiting))}. These are re-checked "
                     f"every run, so a late sync is picked up automatically.</p>")
    parts.append("</div>")
    body = "".join(parts)

    subject = "Xhale — " + " · ".join(filter(None, [
        f"{len(from_loren)} from Loren" if from_loren else "",
        f"{len(posted)} stats posted" if posted else ""]))

    if dry:
        return subject
    g = load_helper("gmail-api.py", "gmail_api").GmailAPI()
    g.send(to=PETE, subject=subject, body=body, html=True)
    if from_loren:
        ids = ",".join(str(m["message_id"]) for m in unemailed)
        sql(f"UPDATE health_coach_message SET emailed_at=now() WHERE message_id IN ({ids}) "
            "RETURNING message_id")
    return subject


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing anywhere")
    ap.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK,
                    help=f"days of catch-up for late Garmin syncs (default {DEFAULT_LOOKBACK})")
    a = ap.parse_args()

    prime_secrets_from_db()
    xh = load_helper("xhale-api.py", "xhale_api")

    posted, waiting = phase_stats(xh, a.lookback, a.dry_run)
    found = phase_inbound(xh, a.lookback, a.dry_run)
    subject = phase_email(posted, found, waiting, a.dry_run)

    tag = "DRY RUN" if a.dry_run else "run"
    print(f"xhale-cron {tag}: {len(posted)} stats posted, {len(found)} new message(s), "
          f"{len(waiting)} day(s) still waiting on Garmin")
    for ds, title, sid in posted:
        print(f"   stats {ds}  {title}" + (f"  (id {sid})" if sid else ""))
    for f in found[:10]:
        print(f"   msg   {str(f['sent_at'])[:16]} [{f['sender']}/{f['channel']}] "
              f"{f['content'][:70]}")
    print(f"   email: {subject or 'none sent (nothing new)'}")


if __name__ == "__main__":
    main()
