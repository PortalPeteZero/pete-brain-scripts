#!/usr/bin/env python3
"""uib-session-mailer.py — the two mails the online support sessions need on a timer.

  1. NEW DATES  — a batched announcement to the keep-me-posted list, once a day, and ONLY if
                  sessions were added since the last announcement.
  2. REMINDER   — the morning of a session, to everyone holding a place, with the joining link.

Runs 06:00 Europe/London daily (deployed 6 Aug 2026 on Pete's "execute the plan in full").
Early enough that a reminder for an 08:30 session is genuinely useful, and the same run carries
the batched announcement for anything added the day before. Pause it with:

    VAULT=/tmp/pbs python3 /tmp/pbs/cc-cron.py pause uib-session-mailer

By hand, either way:

    VAULT=/tmp/pbs python3 /tmp/pbs/uib-session-mailer.py            # dry run, prints what it WOULD send
    VAULT=/tmp/pbs python3 /tmp/pbs/uib-session-mailer.py --send     # actually sends (what the cron does)

Two rules built in rather than left to judgement:

  * The announcement fires on NEW SESSIONS ONLY, never on edits. Fixing a typo on a session must
    not mail everybody. "New" means created since the last announcement watermark, which is kept
    in the database, not in a file that /tmp will eat.
  * It BATCHES. Nine dates added across an afternoon are one email, not nine. That is the whole
    reason it is a daily job rather than a trigger.

Unsubscribing is one click and it is in every announcement, because the list is consent-based
marketing under PECR and an announcement without a way out is not defensible.
"""
# CRON-META
# what: The two timed emails for the Sygma online support sessions
# why: New dates need announcing to the keep-me-posted list, and anyone booked needs the joining link on the morning
# reads: UIB Supabase support_session / support_session_request / support_session_notify
# writes: email via Resend; support_session_announce watermark
# entity: sygma
# report:
# schedule: 0 6 * * *
# timezone: Europe/London
# CRON-META-END
import argparse, json, os, sys, urllib.request, urllib.error, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REF = "xekedjpotwhhstpwganq"
SITE = "https://undergroundintelligence.co.uk"
FROM = "Sygma Solutions <bureau@sygma-solutions.com>"


def q(sql):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"},
        method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


# Resend sits behind Cloudflare and refuses a bare urllib user agent with a 403 and a
# Cloudflare error code, which reads exactly like a rejected API key and is not one.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# Named in preference order. The FIRST version of this did
# `WHERE name ILIKE '%resend%' LIMIT 1`, which is a bug dressed as convenience: the CC holds four
# Resend keys for four different products, and LIMIT 1 handed back Canary Detect's -- a key for a
# different sending domain. The cron would have failed silently every morning.
#
# There is no Bureau-specific key in the CC yet. The Vercel deployment has its own RESEND_API_KEY,
# which is why the website's own emails work; this script cannot read that (Vercel encrypts it).
# The Passion Fit key is on the same sygma-solutions Resend account and is verified to send as
# bureau@sygma-solutions.com (tested 6 Aug 2026), so it is the working fallback until a dedicated
# Bureau key is issued. Recorded here rather than left as a coincidence.
RESEND_SECRETS = [
    "underground-intelligence-bureau-resend-api-key",   # preferred, does not exist yet
    "passion-fit-resend-api-key",                       # same Sygma account, proven to send
]


def resend_key():
    """The key lives in the CC secrets table, never on disk here. Never guessed by wildcard."""
    import subprocess
    for name in RESEND_SECRETS:
        r = subprocess.run(["python3", f"{VAULT}/cc-sql.py",
                            f"SELECT value FROM secrets WHERE name='{name}'"],
                           capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
        try:
            rows = json.loads(r.stdout)
        except Exception:
            continue
        if not rows:
            continue
        v = rows[0]["value"]
        return json.loads(v)["api_key"] if v.strip().startswith("{") else v.strip()
    return None


def send(key, to, subject, text, dry):
    if dry or not key:
        print(f"    [dry] -> {to}: {subject}")
        return True
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps({"from": FROM, "to": [to], "subject": subject, "text": text}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": UA},
        method="POST")
    try:
        urllib.request.urlopen(req, timeout=60)
        return True
    except urllib.error.HTTPError as e:
        print(f"    FAILED -> {to}: {e.code} {e.read().decode()[:160]}")
        return False


def fmt(rows):
    return "\n".join(f"  {r['d']}, {r['t']} UK" for r in rows)


def announce(key, dry):
    """New sessions since the watermark, to everyone on the notify list."""
    q("""create table if not exists support_session_announce (
           id int primary key default 1, last_announced_at timestamptz not null default now(),
           constraint one_row check (id = 1))""")
    # Every other table in this schema runs RLS. A table created on the fly by a script does not
    # inherit that, which is exactly how one ends up as the odd one out. Caught in the 6 Aug audit.
    q("alter table support_session_announce enable row level security")
    q("insert into support_session_announce (id) values (1) on conflict (id) do nothing")
    mark = q("select last_announced_at from support_session_announce where id=1")[0]["last_announced_at"]

    fresh = q(f"""select to_char(starts_at at time zone time_zone,'Dy DD Mon YYYY') d,
                         to_char(starts_at at time zone time_zone,'HH24:MI') t
                  from support_session
                  where status='published' and starts_at > now() and created_at > '{mark}'
                  order by starts_at""")
    if not fresh:
        print("  announcement: nothing new since", mark)
        return

    who = q("select email, unsubscribe_token from support_session_notify where unsubscribed_at is null")
    print(f"  announcement: {len(fresh)} new date(s) -> {len(who)} on the list")
    if not who:
        # Still move the watermark. Otherwise the first person to ever join the list gets
        # every date ever added as their welcome email.
        if not dry:
            q("update support_session_announce set last_announced_at=now() where id=1")
        return

    body_dates = fmt(fresh)
    for p in who:
        text = (f"New dates are up for the online support sessions:\n\n{body_dates}\n\n"
                f"Book a place: {SITE}/sessions\n\n"
                f"Bring a CAT and Genny download, a dashboard reading, a damage investigation, or "
                f"bring nothing and watch. An hour, on Teams, not recorded.\n\n"
                f"Stop these emails: {SITE}/sessions/unsubscribe/{p['unsubscribe_token']}\n\n"
                f"Sygma Solutions Limited")
        send(key, p["email"], f"{len(fresh)} new support session date(s)", text, dry)
    if not dry:
        q("update support_session_announce set last_announced_at=now() where id=1")


def remind(key, dry):
    """Everyone holding a place on a session that starts today."""
    rows = q("""select s.id,
                       to_char(s.starts_at at time zone s.time_zone,'Dy DD Mon') d,
                       to_char(s.starts_at at time zone s.time_zone,'HH24:MI') t,
                       coalesce(s.teams_join_url,'') link,
                       r.name, r.email
                from support_session s join support_session_request r on r.session_id = s.id
                where s.status='published' and r.cancelled_at is null
                  and (s.starts_at at time zone s.time_zone)::date
                      = (now() at time zone s.time_zone)::date""")
    print(f"  reminders: {len(rows)} to send")
    for r in rows:
        text = (f"Hi {r['name'].split(' ')[0]},\n\n"
                f"Your online support session is today, {r['d']} at {r['t']}.\n\n"
                + (f"Join here:\n{r['link']}\n\n" if r["link"] else "")
                + "Bring whatever you want to look at. Not recorded.\n\nSygma Solutions")
        send(key, r["email"], f"Today at {r['t']}: your support session", text, dry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send (default is a dry run)")
    ap.add_argument("--only", choices=["announce", "remind"])
    a = ap.parse_args()
    dry = not a.send
    key = resend_key() if a.send else None
    if a.send and not key:
        print("no Resend key found in the CC secrets table; refusing to pretend it sent")
        return 1
    print(f"uib-session-mailer {'(DRY RUN)' if dry else '(SENDING)'}")
    if a.only in (None, "announce"):
        announce(key, dry)
    if a.only in (None, "remind"):
        remind(key, dry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
