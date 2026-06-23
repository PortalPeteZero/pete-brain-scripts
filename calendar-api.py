#!/usr/bin/env python3
"""
Google Calendar API helper -- single canonical path for all Calendar work.

Parallels `gmail-api.py` in pattern and style. Uses the shared Google service
account (`sygma-seo-reader@sygma-seo-tools.iam.gserviceaccount.com`) via
domain-wide delegation impersonating pete.ashcroft@sygma-solutions.com.

Replaces the old Google Calendar MCP connector (`9854eedd`).

Scope: https://www.googleapis.com/auth/calendar

CLI usage:
  python3 calendar-api.py calendars
  python3 calendar-api.py events primary 2026-05-01 2026-05-31
  python3 calendar-api.py create primary "Flight LHR->ACE" 2026-05-15T14:30:00+01:00 2026-05-15T19:00:00+00:00
  python3 calendar-api.py freebusy primary 2026-05-15T09:00:00+01:00 2026-05-15T18:00:00+01:00
  python3 calendar-api.py delete primary EVENT_ID
  python3 calendar-api.py whoami

Library usage:
  from calendar_api import CalendarAPI
  c = CalendarAPI()
  c.list_calendars()
  c.list_events("primary", time_min="2026-05-01T00:00:00Z", time_max="2026-05-31T23:59:59Z")
  c.create_event("primary", {"summary": "Flight", "start": {...}, "end": {...}})
  c.detect_from_email(gmail_thread_id)  # requires gmail-api.py
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

KEY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "secrets", "google-seo-service-account.json",
)
DEFAULT_USER = "pete.ashcroft@sygma-solutions.com"
DEFAULT_TZ = "Atlantic/Canary"  # Pete is in Lanzarote. Override only when an email/invite explicitly states a different tz (e.g. UK BST -> Europe/London).
SCOPE = "https://www.googleapis.com/auth/calendar"
BASE = "https://www.googleapis.com/calendar/v3"


def _b64u(data):
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class CalendarAPI:
    def __init__(self, user=DEFAULT_USER, key_path=KEY_PATH, scope=SCOPE):
        self.user = user
        with open(os.path.abspath(key_path)) as f:
            self.creds = json.load(f)
        self.scope = scope
        self._token = None
        self._token_exp = 0

    # --- auth -----------------------------------------------------------------

    def _get_token(self):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        now = int(time.time())
        header = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
        claim = _b64u(json.dumps({
            "iss": self.creds["client_email"],
            "sub": self.user,
            "scope": self.scope,
            "aud": "https://oauth2.googleapis.com/token",
            "exp": now + 3600,
            "iat": now,
        }))
        ts = f"{header}.{claim}"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(self.creds["private_key"])
            kf = f.name
        try:
            sig = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", kf, "-binary"],
                input=ts.encode(), capture_output=True, check=True,
            ).stdout
        finally:
            os.unlink(kf)
        jwt = f"{ts}.{_b64u(sig)}"
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt,
            }).encode(),
        )
        resp = json.loads(urllib.request.urlopen(req).read())
        self._token = resp["access_token"]
        self._token_exp = now + resp.get("expires_in", 3600)
        return self._token

    def _call(self, method, path, body=None, query=None):
        url = f"{BASE}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            raise RuntimeError(f"Calendar API {method} {path} -> HTTP {e.code}: {msg}") from e

    # --- calendars ------------------------------------------------------------

    def list_calendars(self):
        return self._call("GET", "/users/me/calendarList").get("items", [])

    def get_calendar(self, calendar_id="primary"):
        return self._call("GET", f"/calendars/{urllib.parse.quote(calendar_id, safe='')}")

    # --- events ---------------------------------------------------------------

    def list_events(self, calendar_id="primary", time_min=None, time_max=None,
                    q=None, max_results=250, single_events=True):
        """Time params are RFC3339 strings (e.g. '2026-05-01T00:00:00Z' or
           '2026-05-01T00:00:00+01:00'). Defaults: next 30 days from now."""
        if time_min is None:
            time_min = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        query = {
            "timeMin": time_min,
            "maxResults": max_results,
            "singleEvents": "true" if single_events else "false",
            "orderBy": "startTime" if single_events else "updated",
        }
        if time_max: query["timeMax"] = time_max
        if q: query["q"] = q
        return self._call("GET", f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events",
                          query=query).get("items", [])

    def get_event(self, event_id, calendar_id="primary"):
        return self._call("GET", f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events/{event_id}")

    def create_event(self, calendar_id="primary", event=None, **kwargs):
        """Pass either a complete `event` dict (Google's schema) or shorthand kwargs:
           summary, start (RFC3339 str), end, location, description, time_zone,
           attendees (list of email strings)."""
        if event is None:
            event = {}
            if "summary" in kwargs: event["summary"] = kwargs["summary"]
            if "location" in kwargs: event["location"] = kwargs["location"]
            if "description" in kwargs: event["description"] = kwargs["description"]
            tz = kwargs.get("time_zone", DEFAULT_TZ)
            if "start" in kwargs:
                event["start"] = {"dateTime": kwargs["start"], "timeZone": tz}
            if "end" in kwargs:
                event["end"] = {"dateTime": kwargs["end"], "timeZone": tz}
            if "attendees" in kwargs:
                event["attendees"] = [{"email": e} for e in kwargs["attendees"]]
        return self._call("POST", f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events",
                          body=event)

    def update_event(self, event_id, calendar_id="primary", **fields):
        """Partial update. Passes `fields` straight to PATCH body."""
        return self._call("PATCH",
                          f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events/{event_id}",
                          body=fields)

    def delete_event(self, event_id, calendar_id="primary"):
        return self._call("DELETE", f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events/{event_id}")

    def search_events(self, q, calendar_id="primary", time_min=None, time_max=None, max_results=50):
        return self.list_events(calendar_id=calendar_id, time_min=time_min,
                                time_max=time_max, q=q, max_results=max_results)

    # --- free / busy ----------------------------------------------------------

    def freebusy(self, calendars, time_min, time_max):
        """calendars: list of calendar IDs (e.g. ['primary', 'colleague@example.com']).
           Returns dict of {calendar_id: [{start, end}, ...]} for busy slots."""
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": c} for c in calendars],
        }
        resp = self._call("POST", "/freeBusy", body=body)
        return {cid: info.get("busy", []) for cid, info in resp.get("calendars", {}).items()}

    # --- detection from emails (integrates with gmail-api.py) ----------------

    FLIGHT_DOMAINS = {
        "britishairways.com", "ba.com", "easyjet.com", "ryanair.com", "lufthansa.com",
        "virgin.com", "virginatlantic.com", "jet2.com", "klm.com", "airfrance.com",
        "iberia.com", "tui.co.uk", "norse.com", "aa.com", "delta.com", "united.com",
        "amadeus.com", "sabre.com",
    }
    HOTEL_DOMAINS = {
        "booking.com", "hotels.com", "expedia.com", "marriott.com", "hilton.com",
        "ihg.com", "accor.com", "accorhotels.com", "airbnb.com", "travelperk.com",
        "trivago.com", "hotels.combined.com",
    }
    CAR_DOMAINS = {
        "enterprise.com", "enterprisecars.com", "hertz.com", "avis.com", "sixt.com",
        "europcar.com", "budget.com", "alamo.com", "thrifty.com",
    }

    def detect_from_email(self, thread_id, gmail_api=None):
        """Detect flight/hotel/car/meeting events in a Gmail thread. Returns a
        list of proposed event dicts (ready to pass to create_event) with a
        `_kind` key indicating what was detected.

        Requires access to gmail-api.py's helper (pass instance as gmail_api,
        or this function imports it lazily)."""
        if gmail_api is None:
            # Lazy import -- avoids hard dependency
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "gmail_api", os.path.join(os.path.dirname(__file__), "gmail-api.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            gmail_api = mod.GmailAPI()

        thread = gmail_api.get_thread(thread_id, fmt="full")
        proposals = []

        for msg in thread.get("messages", []):
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            from_addr = headers.get("from", "")
            subject = headers.get("subject", "")
            body_text = self._extract_text(msg.get("payload", {}))

            # Identify what kind of event this might be
            kind = self._classify(from_addr, subject, body_text)
            if not kind:
                continue

            # Try to parse the dates/times from the body
            date_range = self._extract_datetime_range(body_text)
            if not date_range:
                # Kind detected but no usable time -- skip for now
                continue

            start, end = date_range
            proposal = {
                "_kind": kind,
                "summary": self._build_summary(kind, subject, body_text),
                "location": self._extract_location(kind, body_text),
                "description": f"Auto-detected from Gmail thread. Thread: https://mail.google.com/mail/u/0/#inbox/{thread_id}\n\n{subject}",
                "start": {"dateTime": start, "timeZone": "Europe/London"},
                "end": {"dateTime": end, "timeZone": "Europe/London"},
            }
            proposals.append(proposal)

        return proposals

    def _classify(self, from_addr, subject, body):
        """Decide if this email looks like a flight, hotel, car, or meeting."""
        from_lower = from_addr.lower()
        subject_lower = subject.lower()

        for dom in self.FLIGHT_DOMAINS:
            if dom in from_lower:
                return "flight"
        if re.search(r"\b(flight|e-?ticket|boarding pass|seat selection)\b", subject_lower):
            return "flight"

        for dom in self.HOTEL_DOMAINS:
            if dom in from_lower:
                return "hotel"
        if re.search(r"\b(reservation|booking) (confirm|confirmed|at)\b", subject_lower):
            return "hotel"

        for dom in self.CAR_DOMAINS:
            if dom in from_lower:
                return "car"
        if re.search(r"\b(car rental|vehicle pickup|rental confirm)\b", subject_lower):
            return "car"

        if re.search(r"\b(meeting|call|chat) (scheduled|proposed|on)\b", subject_lower):
            return "meeting"

        return None

    def _extract_datetime_range(self, body):
        """Very loose date+time extractor. Returns (start_iso, end_iso) or None.
        Production implementation would use a proper NLP date parser; this is a
        scaffold that handles common patterns so the sync flow can smoke-test."""
        # Date patterns: "15 May 2026", "2026-05-15", "15/05/2026"
        # Time patterns: "14:30", "2:30 PM"
        # This is deliberately conservative -- if we can't find a clear range,
        # return None and let Pete enter the details manually.
        date_match = re.search(
            r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
            body, re.IGNORECASE
        )
        if not date_match:
            return None
        day, month_name, year = date_match.groups()
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        }
        month = months[month_name.lower()]
        time_match = re.search(r"(\d{1,2}):(\d{2})", body)
        if time_match:
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
        else:
            hour, minute = 9, 0  # default
        start = f"{year}-{month:02d}-{int(day):02d}T{hour:02d}:{minute:02d}:00"
        # Default 1 hour duration
        end_hour = (hour + 1) % 24
        end = f"{year}-{month:02d}-{int(day):02d}T{end_hour:02d}:{minute:02d}:00"
        return (start, end)

    def _build_summary(self, kind, subject, body):
        """Build a clean event summary. Strips Re: / Fwd: noise and adds a kind prefix."""
        cleaned = re.sub(r"^(Re:|Fwd:|FW:)\s*", "", subject, flags=re.IGNORECASE).strip()
        prefix = {"flight": "✈ ", "hotel": "🏨 ", "car": "🚗 ", "meeting": "📅 "}.get(kind, "")
        return f"{prefix}{cleaned}"[:100]

    def _extract_location(self, kind, body):
        """Extract a location from the body. Kind-specific heuristics."""
        # Simple heuristic: airport codes for flights, "at X" patterns for hotels
        if kind == "flight":
            m = re.search(r"\b([A-Z]{3})\s*[-→]\s*([A-Z]{3})\b", body)
            if m:
                return f"{m.group(1)} → {m.group(2)}"
        return ""

    def _extract_text(self, payload):
        """Walk MIME parts, return plain text body."""
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"] + "===").decode("utf-8", errors="replace")
        for part in payload.get("parts", []):
            text = self._extract_text(part)
            if text:
                return text
        return ""


# --- CLI ----------------------------------------------------------------------

def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    c = CalendarAPI()
    cmd, *args = sys.argv[1:]

    if cmd == "whoami":
        print(f"Impersonating: {c.user}")
        print(f"Scope: {c.scope}")
    elif cmd == "calendars":
        for cal in c.list_calendars():
            primary = " (primary)" if cal.get("primary") else ""
            print(f"{cal['id']:50s}  {cal.get('summary', '')}{primary}")
    elif cmd == "events":
        calendar_id = args[0] if args else "primary"
        time_min = args[1] + "T00:00:00Z" if len(args) > 1 else None
        time_max = args[2] + "T23:59:59Z" if len(args) > 2 else None
        events = c.list_events(calendar_id=calendar_id, time_min=time_min, time_max=time_max)
        for e in events:
            start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "")
            print(f"{start:30s}  {e.get('summary', '')}")
    elif cmd == "create":
        calendar_id, summary, start, end = args[0], args[1], args[2], args[3]
        e = c.create_event(calendar_id=calendar_id, summary=summary, start=start, end=end)
        print(json.dumps({"id": e["id"], "htmlLink": e.get("htmlLink")}, indent=2))
    elif cmd == "delete":
        calendar_id, event_id = args[0], args[1]
        c.delete_event(event_id, calendar_id=calendar_id)
        print(f"deleted {event_id}")
    elif cmd == "freebusy":
        calendar_id, time_min, time_max = args[0], args[1], args[2]
        busy = c.freebusy([calendar_id], time_min, time_max)
        print(json.dumps(busy, indent=2))
    elif cmd == "detect":
        thread_id = args[0]
        proposals = c.detect_from_email(thread_id)
        print(json.dumps(proposals, indent=2))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
