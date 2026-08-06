#!/usr/bin/env python3
"""Microsoft Teams helper -- the single canonical path for creating Teams meetings.

Pattern matches gmail-api.py / calendar-api.py. Delegated Microsoft Graph access as
pete.ashcroft@sygma-solutions.com via a PUBLIC CLIENT app (device-code flow), so there is
NO client secret anywhere. Secret: `microsoft-teams-graph.json` in the CC secrets table.

Why this exists: Google Calendar REFUSES to mint a Teams conference
(`conferenceSolutionKey.type="addOn"` -> HTTP 400 "Invalid conference type value"), because the
Workspace Teams add-on only runs in the Calendar UI as the signed-in user. Google Calendar DOES
accept a Teams conference attached directly. So: mint here, attach there.
See [[2026-07-26-teams-links-cannot-be-created-from-google-calendar-api]].

CLI:
  python3 teams-api.py create "Subject" 2026-07-31T12:30:00 2026-07-31T13:30:00 [Atlantic/Canary]
  python3 teams-api.py attach EVENT_ID "Subject"      # mint + attach to an existing GCal event
  python3 teams-api.py whoami          # identity + proves the refresh token still works

Library:
  import importlib.util
  spec = importlib.util.spec_from_file_location('teams_api','/tmp/pbs/teams-api.py')
  m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
  t = m.TeamsAPI()
  t.create_meeting("Subject", start_iso_utc, end_iso_utc)   -> dict with joinWebUrl
  t.attach_to_event(event_id, subject)                      -> attaches a real Teams conference

Refresh-token note: the stored refresh token ROLLS on every use and is written back to the CC
secret automatically. Left unused for ~90 days it expires and needs a fresh device-code sign-in
(see the lesson note for the exact steps).
"""
import json, os, sys, subprocess, urllib.request, urllib.parse, urllib.error, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SECRET_NAME = "microsoft-teams-graph.json"
AUTH = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"


def _cc_sql(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        raise RuntimeError(f"cc-sql failed: {r.stdout}{r.stderr}")
    out = r.stdout.strip()
    return json.loads(out) if out.startswith("[") else []


class TeamsAPI:
    def __init__(self):
        # Prefer the materialised local copy, fall back to the CC secrets table.
        local = f"{VAULT}/Library/processes/secrets/{SECRET_NAME}"
        if os.path.exists(local):
            self.cfg = json.load(open(local))
        else:
            rows = _cc_sql(f"SELECT value FROM secrets WHERE name='{SECRET_NAME}'")
            if not rows:
                raise RuntimeError(f"secret {SECRET_NAME} not found -- run the device-code sign-in")
            self.cfg = json.loads(rows[0]["value"])
        self._token = None

    # --- auth ---------------------------------------------------------------
    def _access_token(self):
        if self._token:
            return self._token
        d = urllib.parse.urlencode({
            "client_id": self.cfg["client_id"],
            "grant_type": "refresh_token",
            "refresh_token": self.cfg["refresh_token"],
            "scope": "https://graph.microsoft.com/OnlineMeetings.ReadWrite offline_access",
        }).encode()
        url = f"{AUTH}/{self.cfg['tenant_id']}/oauth2/v2.0/token"
        try:
            tok = json.loads(urllib.request.urlopen(urllib.request.Request(url, data=d)).read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                "Teams refresh token rejected (%s). It has most likely expired through disuse -- "
                "re-run the device-code sign-in. Detail: %s" % (e.code, e.read().decode()[:300]))
        self._token = tok["access_token"]
        # Refresh tokens roll -- persist the new one or the next call fails.
        new = tok.get("refresh_token")
        if new and new != self.cfg.get("refresh_token"):
            self.cfg["refresh_token"] = new
            _cc_sql("UPDATE secrets SET value=$v$" + json.dumps(self.cfg, indent=1)
                    + f"$v$ WHERE name='{SECRET_NAME}'")
        return self._token

    def _call(self, method, path, body=None):
        h = {"Authorization": "Bearer " + self._access_token(), "Content-Type": "application/json"}
        req = urllib.request.Request(GRAPH + path, headers=h, method=method,
                                     data=json.dumps(body).encode() if body else None)
        try:
            raw = urllib.request.urlopen(req).read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Graph {method} {path} -> HTTP {e.code}: {e.read().decode()[:400]}")
        return json.loads(raw) if raw else {}

    # --- meetings -----------------------------------------------------------
    def whoami(self):
        """Identity + a live token check. NOTE: this deliberately does NOT call GET /me --
        the token carries ONLY OnlineMeetings.ReadWrite (no User.Read), so /me returns 403
        Authorization_RequestDenied. That is correct, least-privilege behaviour, not a fault."""
        self._access_token()   # proves the refresh token still works
        return {"user": self.cfg["user"], "tenant": self.cfg["tenant_id"],
                "app": self.cfg["app_name"], "token": "refreshed OK"}

    def create_meeting(self, subject, start, end, open_presenters=False, open_lobby=False):
        """start/end: ISO 8601. A naive string is treated as UTC.

        open_presenters / open_lobby default to False, so every existing caller keeps the exact
        behaviour it had. Pass both True for an OPEN CLINIC -- a meeting where guests from outside
        the tenant must be able to join without being admitted, and must be able to share their
        screen. Proved against live Graph on 6 Aug 2026 (both settings accepted and echoed back on
        v1.0); see [[Clancy Online Support Sessions]].

        MEASURED DEFAULTS, 6 Aug 2026 -- a meeting created with neither flag comes back as
        `allowedPresenters: everyone` and `lobbyBypassSettings.scope: organization`. So:

          * Screen sharing was never actually broken. This tenant already lets any joiner present.
            `open_presenters=True` PINS that rather than fixing it, which is still worth doing:
            the default is tenant policy and tenant policy can be changed by someone else.
          * The LOBBY is the real one. `organization` scope means guests from outside the tenant
            wait in a lobby. On an open clinic for a customer's staff that is fatal -- they sit
            outside and, if nobody with rights is in the meeting yet, nobody can let them in.
            `open_lobby=True` is what makes an external-guest meeting work.

        An earlier reading of this file concluded attendees could not share because create_meeting
        sends no roles. Sending no roles is not the same as the roles being restrictive -- the
        tenant default filled them in. Measure before concluding.

        NOT settable from here: the `coorganizer` role. Graph accepts the attendee and resolves the
        UPN to a real object id, but returns role `unknownFutureValue` on BOTH v1.0 and beta, so it
        does not stick (tested 6 Aug 2026). `open_presenters=True` is the working substitute: it
        gives the trainer share, mute and admit rights. It does not give them "end meeting for all".

        Trade-off to state out loud: presenter rights also carry recording rights, so with
        open_presenters=True any participant could start a recording if tenant policy allows.
        Where a session is promised as not recorded, that promise is a commitment, not a lock.
        """
        def z(x):
            return x if x.endswith("Z") or "+" in x[10:] else x + "Z"
        body = {"subject": subject, "startDateTime": z(start), "endDateTime": z(end)}
        if open_presenters:
            body["allowedPresenters"] = "everyone"
        if open_lobby:
            body["lobbyBypassSettings"] = {"scope": "everyone", "isDialInBypassEnabled": True}
        return self._call("POST", "/me/onlineMeetings", body)

    def delete_meeting(self, meeting_id):
        self._call("DELETE", f"/me/onlineMeetings/{meeting_id}")

    # --- calendar glue ------------------------------------------------------
    def attach_to_event(self, event_id, subject=None, calendar_id="primary"):
        """Mint a Teams meeting for an existing Google Calendar event and attach it as a real
        conference (join button), not just a line of text in the description."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("cal_api", f"{VAULT}/calendar-api.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        cal = m.CalendarAPI()
        ev = cal.get_event(event_id, calendar_id=calendar_id)
        subject = subject or ev.get("summary") or "Meeting"

        def to_utc(node):
            dt = node["dateTime"]
            if dt.endswith("Z"):
                return dt
            d = datetime.datetime.fromisoformat(dt)
            if d.tzinfo is None:
                return dt + "Z"
            return d.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        mtg = self.create_meeting(subject, to_utc(ev["start"]), to_utc(ev["end"]))
        join = mtg["joinWebUrl"]
        conf = {
            "conferenceId": mtg["id"],
            "conferenceSolution": {"key": {"type": "addOn"}, "name": "Microsoft Teams"},
            "entryPoints": [{"entryPointType": "video", "uri": join,
                             "label": "Join Microsoft Teams meeting"}],
        }
        cal._call("PATCH",
                  f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events/{event_id}"
                  f"?conferenceDataVersion=1",
                  body={"conferenceData": conf})
        return {"event_id": event_id, "joinWebUrl": join, "meeting_id": mtg["id"]}


def _cli():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); return
    t = TeamsAPI()
    if a[0] == "whoami":
        u = t.whoami(); print(u["user"], "|", u["app"], "|", u["token"])
    elif a[0] == "create":
        m = t.create_meeting(a[1], a[2], a[3]); print(m["joinWebUrl"])
    elif a[0] == "attach":
        r = t.attach_to_event(a[1], a[2] if len(a) > 2 else None)
        print("attached:", r["joinWebUrl"])
    else:
        print("unknown command:", a[0]); sys.exit(1)


if __name__ == "__main__":
    _cli()
