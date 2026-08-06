#!/usr/bin/env python3
"""xhale-api.py -- the ONE sanctioned path to the Xhale Athlete API (trainxhale.com), Pete's
Passion Fit coaching platform. Coach: Loren 'Lollipop' Ward. Athlete id 33431.

    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-api.py me
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-api.py sessions --from 2026-08-01 --to 2026-08-10
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-api.py session 5759464
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-api.py diary 2026-08-12 "9am Dentist"
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-api.py feedback 5759464 --text "how it went..."
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-api.py pending
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-api.py attach 5759464 9848643
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-api.py messages
    VAULT=/tmp/pbs python3 /tmp/pbs/xhale-api.py raw GET /api/races/

AUTH. OAuth2 authorization-code, already granted (6 Aug 2026). Two secrets, both pointer-only in
notes: `xhale-oauth-app.json` (client id + secret + redirect) and `xhale-oauth-tokens.json`
(access + refresh). Access tokens last 10h; this helper refreshes automatically when the stored
expiry has passed AND retries once on a 401, then persists whatever token pair comes back to BOTH
the CC `secrets` table and the materialised file. Re-authorising by hand is only needed if the
refresh token itself is revoked.

THINGS THAT WILL CATCH YOU OUT -- all measured 6 Aug 2026, full write-up in
[[xhale-api-capability-map]]:

  * A SESSION HAS NO TIME OF DAY. Only a `date` plus an integer `order` for sequence within the
    day. Pete's convention is to put the time in the text ("9am Dentist", "3pm Run").
  * `subtitle` IS READ-ONLY IN EVERY STATE -- not on create, not on PATCH, not even when a value
    already exists. It fails SILENTLY with a 200. On a training discipline that is where the time
    line lives, so an API-created swim shows as a bare "Swim Endurance" and nothing we send fixes
    it. Diary items have no such problem: `brief_description` IS their title and is writable.
  * `brief_description` DOES NOT RENDER on a training-discipline session. It stores, but the
    session page shows only `training_plan`. Do not use it to carry meaning on a swim/bike/run.
  * DELETE IS A SOFT DELETE. Tombstones stay in list responses with `deleted: true`. Every list
    here filters them out unless --include-deleted is passed.
  * NO PAGINATION. Bare JSON arrays, no envelope; `?page=`/`?limit=` are ignored. Unfiltered
    `/api/sessions/` returns the current season only (686 rows when measured). Always date-bound.
  * BAD ENUM/FK VALUES RETURN HTTP 500, not a validation error. A 500 here is usually your input.
  * `coach_comments` and the per-session `messages` thread are Loren's -- read-only to us. To send
    her something use `send`, which posts to /api/contacts/{id}/messages/.
  * NO UPLOAD SCOPE. Pete holds read+write only, so POST /api/uploads/ is 403. Files already
    uploaded by Garmin can still be attached with `attach` -- that needs no upload scope.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
BASE = "https://trainxhale.com"
APP_FILE = f"{VAULT}/Library/processes/secrets/xhale-oauth-app.json"
TOK_FILE = f"{VAULT}/Library/processes/secrets/xhale-oauth-tokens.json"
LOREN_CONTACT_ID = 3280
DIARY_DISCIPLINE_ID = 17


# ---------------------------------------------------------------- secrets
def _load(env_name, path):
    """env-first, file-fallback -- a Railway cron carries the JSON in the environment."""
    env = (os.environ.get(env_name) or "").strip()
    return json.loads(env) if env else json.load(open(path))


def _app():
    return _load("XHALE_OAUTH_APP", APP_FILE)


def _tokens():
    return _load("XHALE_OAUTH_TOKENS", TOK_FILE)


def _save_tokens(tok):
    """Persist to BOTH homes. The local file is what this helper reads next time; the CC table is
    the durable one that survives /tmp being wiped."""
    tok = dict(tok)
    now = datetime.datetime.now(datetime.timezone.utc)
    tok["_obtained_utc"] = now.isoformat()
    tok["_expires_at_utc"] = (now + datetime.timedelta(seconds=int(tok.get("expires_in", 0)))).isoformat()
    blob = json.dumps(tok, indent=1)
    try:
        os.makedirs(os.path.dirname(TOK_FILE), exist_ok=True)
        with open(TOK_FILE, "w") as fh:
            fh.write(blob)
    except OSError as exc:
        print(f"WARN: could not write {TOK_FILE}: {exc}", file=sys.stderr)
    sql = ("UPDATE secrets SET value=$v$%s$v$, updated_at=now() "
           "WHERE name='xhale-oauth-tokens.json'" % blob)
    try:
        subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "VAULT": VAULT})
    except Exception as exc:                                    # noqa: BLE001
        print(f"WARN: token stored locally but NOT in the CC secrets table: {exc}", file=sys.stderr)
    return tok


def _refresh():
    app, tok = _app(), _tokens()
    import base64
    basic = base64.b64encode(f"{app['client_id']}:{app['client_secret']}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "refresh_token",
                                   "refresh_token": tok["refresh_token"]}).encode()
    req = urllib.request.Request(app["token_url"], data=body, method="POST",
                                 headers={"Authorization": "Basic " + basic,
                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        fresh = json.loads(urllib.request.urlopen(req, timeout=45).read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        raise SystemExit(
            f"XHALE REFRESH FAILED ({exc.code}): {detail}\n"
            "The refresh token is dead. Re-run the authorization-code flow: build the authorize URL "
            "from xhale-oauth-app.json, have Pete click it, then lift ?code= out of the redirect URL "
            "(the /oauth/xhale/callback route is a 404 by design -- the code is still in the address "
            "bar) and POST it to the token endpoint. See [[xhale-api-configuration]].") from exc
    return _save_tokens(fresh)


def _access_token(force=False):
    tok = _tokens()
    exp = tok.get("_expires_at_utc")
    stale = force or not exp
    if exp and not force:
        try:
            stale = datetime.datetime.fromisoformat(exp) <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=2)
        except ValueError:
            stale = True
    return (_refresh() if stale else tok)["access_token"]


# ---------------------------------------------------------------- transport
def call(method, path, data=None, _retried=False):
    headers = {"Authorization": "Bearer " + _access_token()}
    body = None
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        raw = resp.read().decode("utf8", "replace")
        return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and not _retried:
            _refresh()
            return call(method, path, data, _retried=True)
        detail = exc.read().decode("utf8", "replace")[:400]
        if exc.code == 500:
            detail += ("\n  (a 500 from Xhale is usually YOUR input -- an invalid discipline_id, "
                       "race_priority, distance or out-of-range intensity all return 500 rather "
                       "than a validation error)")
        if exc.code == 403 and "/uploads/" in path:
            detail += "\n  (POST /api/uploads/ needs the 'upload' scope, which Pete does not hold)"
        raise SystemExit(f"Xhale API {method} {path} -> HTTP {exc.code}: {detail}") from exc


def _live(rows, include_deleted=False):
    """Strip soft-deleted tombstones -- DELETE leaves the record in list responses."""
    if not isinstance(rows, list) or include_deleted:
        return rows
    return [r for r in rows if isinstance(r, dict) and not r.get("deleted")]


# ---------------------------------------------------------------- commands
def cmd_me(a):
    u = call("GET", "/api/users/current/")
    if a.json:
        print(json.dumps(u, indent=1)); return
    print(f"{u.get('first_name')} {u.get('last_name')}  (id {u.get('id')})  {u.get('email')}")
    print(f"account_type={u.get('account_type')} diary_type={u.get('diary_type')} "
          f"units={u.get('distance_unit_preference')}/{u.get('pool_unit_preference')}")
    print(f"season_dates={u.get('season_dates')}")
    print("\ndisciplines:")
    for d in u.get("discipline_options", []):
        print(f"  {str(d.get('id')):>3}  {d.get('category'):8} {d.get('title')}")
    print("\nzones (READ THESE -- they are Loren's names, not the docs' example set):")
    for sport, z in (u.get("zones") or {}).items():
        print(f"  {sport} (max HR {z.get('max_heart_rate')}):")
        for band in z.get("zones", []):
            hr = f"{band.get('heart_rate_min')}-{band.get('heart_rate_max')}"
            pw = band.get("power_min")
            extra = f" power {pw}-{band.get('power_max')}" if pw else ""
            print(f"      {band.get('number')} {str(band.get('name')).strip():14} HR {hr}{extra}")


def cmd_sessions(a):
    q = {}
    if a.frm:
        q["start_date"] = a.frm
    if a.to:
        q["end_date"] = a.to
    if a.since:
        q["updated_since_datetime"] = a.since
    if not q:
        print("NOTE: no date filter -- returning the CURRENT SEASON only. There is no pagination.",
              file=sys.stderr)
    rows = _live(call("GET", "/api/sessions/?" + urllib.parse.urlencode(q)), a.include_deleted)
    if a.json:
        print(json.dumps(rows, indent=1)); return
    print(f"{len(rows)} session(s)")
    for s in sorted(rows, key=lambda r: (str(r.get("date")), r.get("order") or 0)):
        title = s.get("discipline_title") or "(no discipline)"
        sub = (s.get("subtitle") or "").strip()
        fb = "Y" if (s.get("athlete_feedback") or "").strip() else "-"
        cc = "Y" if (s.get("coach_comments") or "").strip() else "-"
        msg = len(s.get("messages") or [])
        print(f"  {s.get('date')} #{str(s.get('order')):<3} id={s.get('id'):<8} "
              f"{title[:26]:26} {sub[:24]:24} mine={fb} coach={cc} msgs={msg}")


def cmd_session(a):
    print(json.dumps(call("GET", f"/api/sessions/{a.id}/"), indent=1))


def cmd_diary(a):
    """A diary entry is the ONE session shape whose title we fully control."""
    if len(a.text) > 200:
        raise SystemExit(f"brief_description is capped at 200 chars (got {len(a.text)})")
    payload = {"date": a.date, "discipline_id": DIARY_DISCIPLINE_ID, "brief_description": a.text}
    if a.order is not None:
        payload["order"] = a.order
    if a.body:
        payload["athlete_feedback"] = a.body
    out = call("POST", "/api/sessions/", payload)
    print(f"created diary session {out.get('id')} on {out.get('date')} "
          f"order={out.get('order')}: {out.get('discipline_title')}")


def cmd_feedback(a):
    text = a.text if a.text is not None else sys.stdin.read()
    out = call("PATCH", f"/api/sessions/{a.id}/", {"athlete_feedback": text})
    print(f"session {a.id}: athlete_feedback set ({len(text)} chars)")
    if a.rpe is not None:
        call("PATCH", f"/api/sessions/{a.id}/", {"rpe": a.rpe})
        print(f"session {a.id}: rpe={a.rpe}")
    if (out.get("coach_comments") or "").strip():
        print("  NOTE: Loren has already commented on this session.")


def cmd_attach(a):
    call("PATCH", f"/api/sessions/{a.session_id}/", {"training_log_files_to_add": a.file_id})
    s = call("GET", f"/api/sessions/{a.session_id}/")
    print(f"attached file {a.file_id} -> session {a.session_id}; "
          f"has_uploaded_training={s.get('has_uploaded_training')} "
          f"completed_km={s.get('completed_km')} completed_minutes={s.get('completed_minutes')}")


def cmd_pending(a):
    rows = call("GET", "/api/uploads/pending/") or []
    if a.json:
        print(json.dumps(rows, indent=1)); return
    print(f"{len(rows)} file(s) uploaded but not attached to a session")
    for f in rows:
        print(f"  id={f.get('training_file_id')} {f.get('activity_start_datetime')} "
              f"{f.get('activity_type')} {f.get('file_type')} "
              f"{f.get('device_company')} {f.get('device_name')} src={f.get('source')}")


def cmd_messages(a):
    rows = call("GET", f"/api/contacts/{a.contact}/messages/")
    if a.json:
        print(json.dumps(rows, indent=1)); return
    rows = rows if isinstance(rows, list) else [rows]
    for m in rows[-a.limit:]:
        who = "Pete" if m.get("sender_user_id") != a.contact else "Loren"
        print(f"  [{m.get('datetime_sent')}] {who}: {str(m.get('content'))[:400]}")


def cmd_send(a):
    call("POST", f"/api/contacts/{a.contact}/messages/", {"content": a.text})
    print(f"sent to contact {a.contact} ({len(a.text)} chars)")


def cmd_races(a):
    rows = _live(call("GET", "/api/races/"), a.include_deleted)
    if a.json:
        print(json.dumps(rows, indent=1)); return
    print(f"{len(rows)} race(s)")
    for r in sorted(rows, key=lambda x: str(x.get("date"))):
        print(f"  {r.get('date')} id={r.get('id'):<8} {str(r.get('race_name'))[:38]:38} "
              f"priority={r.get('race_priority')} distance_code={r.get('distance')}")
    print("\n  distance / race_priority / attendance_probability are OPAQUE CODES with no published"
          "\n  lookup -- do not write them blind, ask support@trainxhale.com first.")


def cmd_workouts(a):
    print(json.dumps(call("GET", "/api/workouts/"), indent=1))


def cmd_token(a):
    if a.refresh:
        t = _refresh()
        print("refreshed. expires", t.get("_expires_at_utc"))
        return
    t = _tokens()
    print("expires_at_utc :", t.get("_expires_at_utc"))
    print("scope          :", t.get("scope"))
    print("access_token   :", len(t.get("access_token", "")), "chars")
    print("refresh_token  :", len(t.get("refresh_token", "")), "chars")


def cmd_raw(a):
    data = dict(kv.split("=", 1) for kv in a.pairs) if a.pairs else None
    print(json.dumps(call(a.method.upper(), a.path, data), indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="raw JSON instead of the digest")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("me", help="current user: disciplines + Loren's zone names").set_defaults(fn=cmd_me)

    s = sub.add_parser("sessions", help="list sessions (date-bound; tombstones filtered)")
    s.add_argument("--from", dest="frm"); s.add_argument("--to")
    s.add_argument("--since", help="updated_since_datetime, ISO 8601")
    s.add_argument("--include-deleted", action="store_true")
    s.set_defaults(fn=cmd_sessions)

    s = sub.add_parser("session", help="one session, full JSON")
    s.add_argument("id"); s.set_defaults(fn=cmd_session)

    s = sub.add_parser("diary", help="create a diary entry (the only fully controllable title)")
    s.add_argument("date"); s.add_argument("text", help="the visible title, <=200 chars, time included")
    s.add_argument("--order", type=int); s.add_argument("--body", help="goes in athlete_feedback")
    s.set_defaults(fn=cmd_diary)

    s = sub.add_parser("feedback", help="write athlete_feedback on a session (- reads stdin)")
    s.add_argument("id"); s.add_argument("--text"); s.add_argument("--rpe", type=int)
    s.set_defaults(fn=cmd_feedback)

    s = sub.add_parser("attach", help="attach an already-uploaded file to a session")
    s.add_argument("session_id"); s.add_argument("file_id"); s.set_defaults(fn=cmd_attach)

    sub.add_parser("pending", help="files uploaded but not attached").set_defaults(fn=cmd_pending)

    s = sub.add_parser("messages", help="the thread with Loren")
    s.add_argument("--contact", type=int, default=LOREN_CONTACT_ID)
    s.add_argument("--limit", type=int, default=10); s.set_defaults(fn=cmd_messages)

    s = sub.add_parser("send", help="send Loren a message")
    s.add_argument("text"); s.add_argument("--contact", type=int, default=LOREN_CONTACT_ID)
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("races", help="list races")
    s.add_argument("--include-deleted", action="store_true"); s.set_defaults(fn=cmd_races)

    sub.add_parser("workouts", help="planned structured workouts (read-only)").set_defaults(fn=cmd_workouts)

    s = sub.add_parser("token", help="token state / force a refresh")
    s.add_argument("--refresh", action="store_true"); s.set_defaults(fn=cmd_token)

    s = sub.add_parser("raw", help="escape hatch: raw call")
    s.add_argument("method"); s.add_argument("path"); s.add_argument("pairs", nargs="*")
    s.set_defaults(fn=cmd_raw)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
