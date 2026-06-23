#!/usr/bin/env python3
"""xhale-sync.py — twice-daily Train Xhale ICS → Pete's primary GCal sync.

Plan: Library/processes/xhale-sync/plan-2026-05-24.md (24 locked decisions).
README: Library/processes/xhale-sync/README.md (live operational rules).

Runs at 07:00 + 18:00 Atlantic/Canary via the `gcal-twice-daily-sync` cron.

Phases per run:
  1. Fetch ICS from Train Xhale feed
  2. Parse VEVENTs, filter to today−7d → today+90d
  3. Classify each (training / travel / update / journal / filtered / unknown)
  4. Journal miss-detection: if yesterday has no `journal`-classified entry → urgent email
  5. Travel: verify a flight exists in Pete's diary on that date; flag if missing
  6. Update / filtered: skip
  7. Training: parse time via Haiku 4.5 → dedupe via ledger → create / patch / skip
  8. Deletions: removed-from-feed UIDs we created → delete in GCal (even if hand-edited; source-deletion wins)
  9. Colour-coder fold: run calendar_colour.run('apply-recent', 2, 365)
  10. Append daily-note line + write ledger + write run-log
  11. Exit

Usage:
  python3 xhale-sync.py run            # full sync (cron mode)
  python3 xhale-sync.py dry-run        # report what would happen, no mutations
  python3 xhale-sync.py test-llm       # run the test cases from README.md against Haiku
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

# CRON-META
# what: Xhale → Google Calendar twice-daily sync — training events from the Xhale iCal feed parsed + created/patched/deleted in GCal, colour-coded
# why: Pete's Xhale training plan mirrored onto his calendar (his daily operating view), deduped against existing events
# reads: Xhale iCal feed (token); Anthropic Haiku (time parse); GCal (calendar-api, dedup scan); CC cron_state (ledger)
# writes: GCal events (create/patch/delete on primary); CC cron_state (xhale-sync ledger)
# entity: canary-detect
# schedule: 0 7,18 * * *
# timezone: Atlantic/Canary
# CRON-META-END

# ============================================================
# Config
# ============================================================

VAULT = Path(os.environ.get("VAULT", "/Users/peterashcroft/Second Brain"))
SCRIPTS = Path(__file__).resolve().parent  # flat-repo siblings on Railway (/app); Library/.../scripts locally
SECRETS = VAULT / "Library/processes/secrets"
WORK = VAULT / "Library/processes/xhale-sync"
DAILY = VAULT / "Daily"

FEED_TOKEN_FILE = SECRETS / "xhale-feed-token"
ANTHROPIC_KEY_FILE = SECRETS / "anthropic-api-key"
LEDGER_FILE = WORK / "ledger.json"
RUNLOG_FILE = WORK / "run-log.md"
README_FILE = WORK / "README.md"

DEFAULT_DURATION_MIN = 90
PLACEHOLDER_TIME = "07:00"  # morning — Pete's expected default for unparseable events (2026-05-25)
TIMEZONE = "Atlantic/Canary"
DAYS_BACK = 7
DAYS_AHEAD = 90
COLOUR_PERSONAL = "2"

PETE_EMAIL = "pete.ashcroft@sygma-solutions.com"

# Anthropic
ANTHROPIC_MODEL = "claude-haiku-4-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Flight-match regex for travel verification
FLIGHT_REGEX = re.compile(
    r"\b(flight|fly|ryanair|jet2|easyjet|ba |british airways|"
    r"FR\d{2,4}|LS\d{3,4}|EZY\d{3,4}|U2\d{3,4}|BY\d{3,4}|BA\d{2,4}|"
    r"LHR|LGW|MAN|EDI|GLA|BHX|STN|LTN|ACE|TFS|LPA|FUE|MAD|BCN|AGP|PMI)\b",
    re.IGNORECASE,
)

# ============================================================
# Lazy imports of vault helpers
# ============================================================

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def cal_api():
    """Return a CalendarAPI() instance."""
    m = _load_module("calendar_api", SCRIPTS / "calendar-api.py")
    return m.CalendarAPI()


def gmail_api():
    m = _load_module("gmail_api", SCRIPTS / "gmail-api.py")
    return m.GmailAPI()


def colour_coder():
    return _load_module("calendar_colour", SCRIPTS / "calendar-colour.py")


# ============================================================
# Secrets
# ============================================================

def read_secret(path: Path) -> str:
    return path.read_text().strip()


# ============================================================
# ICS fetch + parse
# ============================================================

class FetchError(Exception):
    pass


def fetch_ics() -> str:
    token = read_secret(FEED_TOKEN_FILE)
    url = f"https://trainxhale.com/icalendar/33431/{token}.ics"
    req = urllib.request.Request(url, headers={"User-Agent": "xhale-sync/1.0"})
    last_err = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status != 200:
                    raise FetchError(f"HTTP {r.status}")
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            # 401/403 are token rotation — don't retry, surface immediately
            if e.code in (401, 403):
                raise FetchError(f"HTTP {e.code} — token may have been rotated") from e
            last_err = e
        except Exception as e:
            last_err = e
        time.sleep(2)
    raise FetchError(f"fetch failed after 2 attempts: {last_err}")


def _unfold(text: str) -> str:
    """ICS line-folding: a line starting with space or tab is a continuation."""
    out = []
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return "\n".join(out)


def _ics_unescape(s: str) -> str:
    return s.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_vevents(ics_text: str) -> list:
    """Parse VEVENT blocks into list of dicts."""
    text = _unfold(ics_text)
    events = []
    cur = None
    for line in text.splitlines():
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key_part, _, value = line.partition(":")
            key = key_part.split(";")[0]
            cur[key] = _ics_unescape(value.strip())
    return events


def parse_dtstart_date(dtstart: str) -> dt.date:
    """ICS DTSTART can be YYYYMMDD (all-day) or YYYYMMDDTHHMMSSZ. We only care about the date."""
    s = dtstart.strip()
    if "T" in s:
        s = s.split("T")[0]
    return dt.datetime.strptime(s, "%Y%m%d").date()


# ============================================================
# Classification
# ============================================================

def load_loren_patterns() -> list:
    """Read Loren-exclusion patterns from README.md.

    Format: lines under '## Loren-exclusion list' inside a ``` fenced block.
    Comment lines start with `#`. Blank lines ignored.
    """
    if not README_FILE.exists():
        return []
    text = README_FILE.read_text()
    # Find the loren section
    m = re.search(r"## Loren-exclusion list.*?```(.*?)```", text, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    patterns = []
    for line in block.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        patterns.append(s)
    return patterns


def load_journal_start_date() -> dt.date:
    """Read JOURNAL_START_DATE from README.md operational-rules table.

    Format: a markdown row matching `Journal practice start date | YYYY-MM-DD`.
    Returns date.max if not configured (effectively disables the nag).
    """
    if not README_FILE.exists():
        return dt.date.max
    text = README_FILE.read_text()
    m = re.search(r"\|\s*Journal practice start date\s*\|\s*`?(\d{4}-\d{2}-\d{2})`?\s*\|", text)
    if not m:
        return dt.date.max
    try:
        return dt.date.fromisoformat(m.group(1))
    except Exception:
        return dt.date.max


def safe_delete_event(api, event_id: str, calendar_id: str = "primary") -> str:
    """Delete a GCal event. Treat HTTP 410 ("already deleted") and HTTP 404
    ("not found") as benign — the desired state (event gone) already exists.

    Returns:
      "deleted"        — event was deleted by this call
      "already-gone"   — event was already deleted (410/404); benign, not an error
      Raises Exception — anything else (auth, network, server error)

    Without this wrapper, manual deletions in GCal (or already-cleaned UIDs the
    ledger still points at) crash the run with `Status: errors (N)` even though
    the deletion goal is satisfied. Added 2026-05-25.
    """
    try:
        api.delete_event(event_id, calendar_id)
        return "deleted"
    except Exception as e:
        s = str(e)
        if "410" in s or "404" in s or "Resource has been deleted" in s or "Not Found" in s:
            return "already-gone"
        raise


# ============================================================
# Session vs diary detection (2026-06-14)
# ============================================================
# Pete's model: Loren programs TRAINING SESSIONS in Xhale (the sync pulls them);
# Pete owns DIARY ENTRIES and RACES in GCal (the sync ignores them). The Xhale
# iCal feed does NOT expose the entry type — a diary entry and a session both
# export as `session-####`. So we infer "is this a Loren session?" from two
# independent signals, EITHER of which is sufficient:
#   1. A real intensity zone in the description. Xhale makes intensity mandatory
#      on a session; the only "none" option is an explicit 0, which exports as an
#      EMPTY description (verified 2026-06-14 with a 0-intensity test swim). So a
#      non-zero zone is a guaranteed session marker.
#   2. The title-head — the text before the first ':' once Xhale's "2." order
#      prefix is stripped — is a clean session-type phrase (discipline + optional
#      qualifier, or "other training"). This catches 0-intensity sessions, whose
#      title is still a type name ("Swim Tech").
# Crucially we match signal 2 ONLY as a clean phrase at the HEAD — never the bare
# words run/bike/swim anywhere in free text. That keeps Pete's diary entries
# ("10am 5k run", "Bike fit at Halfords") on the diary side, exactly as he warned.
_INTENSITY_RX = re.compile(r"\bintensity\s*:|zone\s*[1-9]|\brpe\b", re.I)
_SESSION_DISCIPLINE = r"(?:swim|swimming|bike|biking|cycling|turbo|run|running|brick|gym)"
_SESSION_QUALIFIER = r"(?:tech|intervals|endurance|tempo|core/specifics|core|specifics|lifting)"
_SESSION_TYPE_HEAD_RX = re.compile(
    rf"^(?:{_SESSION_DISCIPLINE}(?:\s+{_SESSION_QUALIFIER})?|other\s+training)$", re.I
)


def is_training_session(subject: str, description: str) -> bool:
    """True = Loren-programmed training session (sync to GCal); False = diary
    entry Pete owns in GCal (ignore). See the block comment above for the two
    signals. Head-only, exact-phrase type matching is what keeps freeform diary
    text ('10am 5k run', 'Bike fit') from being mistaken for a session."""
    if _INTENSITY_RX.search(description or ""):
        return True
    core = re.sub(r"^\s*\d+[.)]\s*", "", subject or "").strip()
    head = core.split(":", 1)[0].strip()
    return bool(_SESSION_TYPE_HEAD_RX.match(head))


def classify(subject: str, description: str, loren_patterns: list) -> str:
    s = (subject or "").strip()
    sl = s.lower()
    sl_core = re.sub(r"^\s*\d+[.)]\s*", "", sl)   # drop Xhale's "2." session-order prefix
    # `description` accepted for future use but NOT pattern-matched. Pete's
    # training events often contain notes TO Loren in the description (e.g.
    # "Loren, I moved this because..."), which is legitimate athlete→coach
    # communication on a real training session — not a Loren-only event.
    # filtered — Loren's OWN markers LEAD with her name ("Loren to plan…",
    # "Loren: sign-off", "Loren away"). Narrowed 2026-06-14 from a broad
    # substring to a PREFIX match (on the number-stripped subject) so Pete can
    # keep Loren-related commitments he DOES want in the diary (e.g. "Coaching
    # call with Loren") — those don't lead with her name. Description never matched.
    for pat in loren_patterns:
        if sl_core.startswith(pat.lower()):
            return "filtered"
    # travel — Xhale's iCal export prefixes a numbering tag ("1. FLY UK") not
    # visible in the UI; sl_core already has it stripped so the marker is caught
    # and routed to travel-verify (never created).
    if sl_core in ("fly uk", "fly home"):
        return "travel"
    # update
    if sl == "update":
        return "update"
    # journal — case-insensitive substring
    if "journal" in sl:
        return "journal"
    # rest day — case-insensitive substring (Pete's rule 2026-05-25:
    # rest days from Xhale should NEVER be created in GCal; they're noise
    # in the diary. Subject patterns observed: "Rest day", "Rest Day",
    # "Rest", "Active Rest", etc. — anything starting/containing "rest day"
    # or just the bare token "rest" as a standalone subject.)
    if "rest day" in sl or sl == "rest" or sl.startswith("rest "):
        return "rest_day"
    # session vs diary (2026-06-14): a Loren-programmed session → "training"
    # (sync); anything else with a real subject → "diary" (Pete owns it in GCal,
    # the sync ignores it). Races are caught earlier by their `race-` UID and
    # also routed to "diary". See is_training_session() for the two signals.
    if s:
        if is_training_session(s, description):
            return "training"
        return "diary"
    return "unknown"


# ============================================================
# Time parser — regex first (deterministic), LLM as fallback
# ============================================================

# Regex matches a single time token: "8am", "9.30am", "5:40am", "10pm", "9.30AM" etc.
# Groups: hour (1-12), minute optional (.MM or :MM), meridiem (am|pm)
_TIME_RX = re.compile(
    r"(?<![0-9])"                      # no leading digit (avoid '15km' / 'zone 2')
    r"(1[0-2]|[1-9])"                  # hour 1-12
    r"(?:[.:]([0-5]\d))?"              # optional .MM or :MM
    r"\s*(am|pm)\b",
    re.IGNORECASE,
)


def _hhmm_from_match(m) -> str:
    """Convert a _TIME_RX match to 24-hour HH:MM string."""
    h = int(m.group(1))
    mm = int(m.group(2)) if m.group(2) else 0
    mer = m.group(3).lower()
    if mer == "pm" and h != 12:
        h += 12
    elif mer == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mm:02d}"


def parse_time_regex(subject: str) -> tuple:
    """Deterministic time extraction. Recognises patterns like:
      - "8am Group Pool Swim"          -> ("08:00", None)
      - "9.30am Open Water"            -> ("09:30", None)
      - "5.40am - 7am Indoor Bike"     -> ("05:40", "07:00")
      - "3pm Short Run"                -> ("15:00", None)

    Returns (start_HHMM or None, end_HHMM or None). Free, no network call,
    no rate limit. Use as the first pass before falling back to LLM for
    edge-case subjects with no clear time token.
    """
    if not subject:
        return (None, None)
    # Strip leading "1. " / "2. " session-order prefixes
    s = re.sub(r"^\s*\d+\.\s*", "", subject)
    matches = list(_TIME_RX.finditer(s))
    if not matches:
        return (None, None)
    start = _hhmm_from_match(matches[0])
    end = _hhmm_from_match(matches[1]) if len(matches) >= 2 else None
    return (start, end)


def parse_time_llm(subject: str, description: str) -> tuple:
    """Call Haiku to extract start + optional end time.

    Used only when parse_time_regex returns no start (unusual subjects).
    Returns (start_HHMM or None, end_HHMM or None).
    """
    api_key = read_secret(ANTHROPIC_KEY_FILE)
    system = (
        "You extract training session start and end times from short training-diary subject lines. "
        "Reply with strict JSON only: {\"start\":\"HH:MM\"|null,\"end\":\"HH:MM\"|null} in 24-hour time, Atlantic/Canary local. "
        "Return null for any field where no time is mentioned. "
        "When AM/PM is ambiguous, assume daylight training hours (06:00–21:00). "
        "Strip session-order prefixes like '1.' or '2.' before parsing. "
        "Ignore numbers that are zones, distances, or quantities (e.g. 'zone 2', '5km', '400m'). "
        "ONLY set end if a clear end time appears in the subject (e.g. '5.40am - 7am Indoor Bike'). "
        "Do NOT infer end from descriptive words like 'Short', 'Tempo', 'Endurance', 'Long' — those describe intensity or distance, not duration. "
        "If only a start time is mentioned and no end is stated, return null for end. The caller applies a 90-minute default. "
        "Do not output anything except the JSON object."
    )
    user = f"Subject: {subject}\nDescription: {description or ''}"
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 100,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    data = None
    last_err = None
    for attempt in (1, 2, 3):
        req = urllib.request.Request(
            ANTHROPIC_URL,
            data=json.dumps(body).encode(),
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode())
                break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Rate-limited — exponential backoff
                time.sleep(2 ** attempt)
                last_err = e
                continue
            last_err = e
            break
        except Exception as e:
            last_err = e
            break
    if data is None:
        print(f"  LLM call failed for {subject!r}: {last_err}", file=sys.stderr)
        return (None, None)
    text = (data.get("content") or [{}])[0].get("text", "").strip()
    # Strip code fences if model added them
    text = re.sub(r"^```(?:json)?", "", text)
    text = re.sub(r"```$", "", text).strip()
    try:
        out = json.loads(text)
    except Exception:
        print(f"  LLM returned malformed JSON for {subject!r}: {text!r}", file=sys.stderr)
        return (None, None)
    start = out.get("start")
    end = out.get("end")
    # Validate HH:MM format
    hhmm = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
    if start is not None and not hhmm.match(start):
        start = None
    if end is not None and not hhmm.match(end):
        end = None
    return (start, end)


# ============================================================
# Ledger I/O
# ============================================================

def _cron_state():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cron_state
    return cron_state


def load_ledger() -> dict:
    """Durable ledger in CC cron_state (survives Railway's wiped FS); local file is a fallback/mirror.
    A fresh/empty ledger on first cloud run is SAFE — the destination-calendar dedup below prevents
    duplicate creates, and deletes only ever touch ledger-known events."""
    try:
        v = _cron_state().get_state("xhale-sync", "ledger", default=None)
        if v is not None:
            return v
    except Exception:
        pass
    if LEDGER_FILE.exists():
        try:
            return json.loads(LEDGER_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_ledger(ledger: dict):
    try:
        _cron_state().set_state("xhale-sync", "ledger", ledger)
    except Exception as e:
        print(f"  cron_state ledger save failed: {e}", file=sys.stderr)
    if WORK.exists():  # local mirror only (skipped headless on Railway)
        try:
            LEDGER_FILE.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
        except Exception:
            pass


def hash_str(s: str) -> str:
    return hashlib.sha1((s or "").encode()).hexdigest()


# ============================================================
# GCal helpers
# ============================================================

def gcal_event_updated_after(event: dict, iso_ts: str) -> bool:
    """True if GCal event's `updated` timestamp is meaningfully after iso_ts.

    Used for manually-modified detection. Allows a 60-second grace window
    to absorb create-time clock-skew between our local timestamp (set before
    the API call) and GCal's `updated` field (set when the server processes
    the create). Without this buffer, every newly-created event flips
    `manually_modified: true` on the next sync — false positive.
    """
    upd = event.get("updated")
    if not upd or not iso_ts:
        return False
    try:
        upd_dt = dt.datetime.fromisoformat(upd.replace("Z", "+00:00"))
        ref_dt = dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except Exception:
        # Fall back to lexicographic — strict, may false-positive
        return upd > iso_ts
    return (upd_dt - ref_dt).total_seconds() > 60


def find_flight_on_date(api, date_iso: str) -> dict:
    """Return the first GCal event on date_iso whose summary matches a flight pattern, or None."""
    start = f"{date_iso}T00:00:00Z"
    end_dt = dt.date.fromisoformat(date_iso) + dt.timedelta(days=1)
    end = f"{end_dt.isoformat()}T00:00:00Z"
    try:
        evs = api.list_events("primary", time_min=start, time_max=end, max_results=50)
    except Exception:
        return None
    for e in evs:
        title_loc = f"{e.get('summary','')} {e.get('location','')}"
        if FLIGHT_REGEX.search(title_loc):
            return e
    return None


# ============================================================
# Duplicate detection (Pete's rule: no duplicates with non-Xhale events)
# ============================================================
#
# The ledger only knows about events THIS script created. It can't see events
# Pete (or any other system) put on GCal directly. Before creating any new
# event we therefore scan Pete's primary calendar for same-date events whose
# activity category overlaps with the Xhale event's category. If a match is
# found, skip the create and record `linked_to_existing` in the ledger so we
# never re-create across runs.

ACTIVITY_KEYWORDS = {
    "swim":    ("swim", "swimming"),
    "run":     ("run", "running", "jog", "jogging"),
    "bike":    ("bike", "biking", "cycle", "cycling", "ride", "turbo", "spin"),
    "meeting": ("meeting",),
    # Note: training-specific words (tech / endurance / tempo / intervals /
    # short / long) are intensity tags, NOT activity keywords. Don't add them.
}


def event_categories(summary: str) -> set:
    """Return the set of activity categories a summary belongs to.

    Categories overlap on word-boundary matches against ACTIVITY_KEYWORDS.
    Examples:
      "Swimming lesson"                          -> {"swim"}
      "1. Swim Tech: 8am Group Pool Swim"        -> {"swim"}
      "Turbo Intervals: 5.40am - 7am Indoor Bike"-> {"bike"}
      "Team meeting"                              -> {"meeting"}
      "Repair - Comunidad de Propietarios"       -> set()
    """
    s = (summary or "").lower()
    words = set(re.findall(r"\b[a-z]+\b", s))
    cats = set()
    for cat, kws in ACTIVITY_KEYWORDS.items():
        if any(kw in words for kw in kws):
            cats.add(cat)
    return cats


def _core_name_tokens(summary: str) -> set:
    """Distinctive name tokens of a summary: drop the leading order-prefix
    ("3."), time words, short words and stopwords. Lets us spot the same diary
    entry under slightly different wording (e.g. "3. Seminar 5.30pm" vs "Seminar")."""
    s = re.sub(r"^\s*\d+\.\s*", "", summary or "").lower()
    STOP = {"with", "open", "the", "and", "for", "your", "you", "from"}
    return {w for w in re.findall(r"[a-z]+", s) if len(w) >= 4 and w not in STOP}


def _start_hhmm(e: dict):
    """The HH:MM start of a timed GCal event, or None for an all-day event."""
    est = (e.get("start", {}) or {}).get("dateTime")
    return est.split("T", 1)[1][:5] if est and "T" in est else None


def find_existing_match(api, date: "dt.date", xhale_subject: str, cache: dict,
                        start_hhmm: str = None):
    """Inspect Pete's GCal for a same-date event this Xhale event interacts with.
    Returns a tuple (event, kind):

      - (event, "duplicate") — the SAME thing is already on the calendar; skip the
        create and link to it. Duplicate = same activity category (training), or —
        for a diary entry (no sport) — same start time (Pete's rule "same date +
        same time = same thing") or same distinctive name ("seminar", "scouts").
      - (event, "conflict")  — a DIFFERENT event sits at the same start time as a
        training session (e.g. a swim vs a flight at 08:00). Not a duplicate but a
        real clash for Pete to resolve — flag it, never auto-create.
      - (None, None)         — nothing relevant; create normally.
    `cache` (keyed by ISO date) avoids repeated API calls for the same date.
    """
    iso = date.isoformat()
    if iso not in cache:
        try:
            cache[iso] = api.list_events(
                "primary",
                time_min=f"{iso}T00:00:00Z",
                time_max=f"{(date + dt.timedelta(days=1)).isoformat()}T00:00:00Z",
                max_results=50,
            )
        except Exception:
            cache[iso] = []
    xc = event_categories(xhale_subject)
    xn = _core_name_tokens(xhale_subject)
    time_clash = None
    for e in cache[iso]:
        # Skip events we created ourselves (they carry xhale_uid).
        ext = e.get("extendedProperties", {}).get("private", {}) or {}
        if ext.get("xhale_uid"):
            continue
        ec = event_categories(e.get("summary", ""))
        same_time = bool(start_hhmm) and _start_hhmm(e) == start_hhmm
        if xc:
            # training: a shared sport category is the same session → duplicate
            if ec & xc:
                return e, "duplicate"
            # a DIFFERENT event at the same clock time is a clash, not a dup.
            # Remember the first and flag it after the scan (Pete's rule: flag
            # conflicts, don't auto-sort — e.g. a swim landing on a flight morning).
            if same_time and time_clash is None:
                time_clash = e
        else:
            # diary entry (no sport): same time = same thing, or a shared
            # distinctive name → duplicate, skip it.
            if same_time:
                return e, "duplicate"
            if xn and (_core_name_tokens(e.get("summary", "")) & xn):
                return e, "duplicate"
    if time_clash is not None:
        return time_clash, "conflict"
    return None, None


# Per-session duration overrides — applied when Xhale sends no explicit end
# time (so we'd otherwise fall back to DEFAULT_DURATION_MIN). Pete confirmed
# (2026-06-14) these recurring sessions are 60 min, not the 90-min default:
#   - Monday "Group Pool Swim"         → real 08:00–09:00
#   - Thursday "Open Water with Kimbo" → real 09:30–10:30
# Saturday "Open Water Swim" is deliberately NOT listed — Pete confirmed
# (2026-06-14) it genuinely runs the full 90 min — so it keeps the default. Match is case-insensitive substring on the
# Xhale subject. Add a (regex, minutes) row here for any future fixed-length session.
SESSION_DURATION_OVERRIDES = [
    (re.compile(r"pool swim", re.I), 60),
    (re.compile(r"\bkimbo\b", re.I), 60),
    (re.compile(r"5k run", re.I), 105),   # Pete's Sat 5k-run block runs 10:00–11:45 (door-to-door), 2026-06-14
]


def duration_for(subject: str) -> int:
    """Minutes to use when no explicit end time was parsed from the subject.
    Returns a per-session override if one matches, else DEFAULT_DURATION_MIN."""
    for rx, mins in SESSION_DURATION_OVERRIDES:
        if rx.search(subject or ""):
            return mins
    return DEFAULT_DURATION_MIN


# Emoji rules for event summaries — Pete likes the icons at a glance (2026-06-14;
# extended beyond sport to cover diary entries too). Ordered, first whole-word
# match wins. Matched on word tokens (not substring) so "brunch" can't trip "run".
EMOJI_RULES = [
    (("swim", "swimming"), "🏊"),
    (("run", "running", "jog", "jogging", "parkrun"), "🏃"),
    (("bike", "biking", "cycle", "cycling", "turbo", "spin", "ride"), "🚴"),
    (("gym", "strength", "weights", "workout"), "🏋️"),
    (("coffee", "cafe", "brunch"), "☕"),
    (("meeting", "seminar", "webinar", "call", "zoom", "standup", "appointment"), "📅"),
    (("hotel", "airbnb"), "🏨"),
    (("flight", "fly", "airport"), "✈️"),
]
# Sport subset kept as an alias for any caller/test that wants it.
ACTIVITY_EMOJI = {"swim": "🏊", "run": "🏃", "bike": "🚴"}


def decorated_summary(subject: str) -> str:
    """Clean + emoji-decorate an Xhale subject for GCal display: strip Xhale's
    session-order prefix ("2. "), then prepend the matching activity/diary emoji.
    Idempotent — a subject already carrying any emoji/symbol is returned as-is
    (never double-decorates). Pass the RAW Xhale subject, not an already-decorated
    summary, when you want the prefix stripped."""
    s = re.sub(r"^\s*\d+[.)]\s*", "", (subject or "").strip())   # drop "2." session-order prefix
    if s[:1] and ord(s[0]) > 0x2300:                             # already starts with an emoji/symbol
        return s
    if any(em in s for _, em in EMOJI_RULES):                    # already carries an emoji somewhere
        return s
    words = set(re.findall(r"[a-z]+", s.lower()))
    for keys, emoji in EMOJI_RULES:
        if any(k in words for k in keys):
            return f"{emoji} {s}"
    return s


def _event_description(uid: str, source_desc: str) -> str:
    """Visible GCal description for a synced event: Xhale's own session notes
    (intensity / zone / coach comments) on top, then a provenance line so Pete
    sees at a glance where it came from and when. Machine-readable sync state
    lives separately in extendedProperties (hidden, can't be fat-fingered)."""
    stamp = dt.datetime.now(_canary_tz()).strftime("%Y-%m-%d %H:%M")
    parts = []
    note = (source_desc or "").strip()
    if note:
        parts.append(note)
    parts.append(
        f"— Synced from Train Xhale · last synced {stamp} · "
        f"edits here lock the event · UID {uid}"
    )
    return "\n\n".join(parts)


def build_event_payload(subject: str, date_iso: str, start_hhmm: str, end_hhmm: str,
                       uid: str, classification: str, source_desc: str = "") -> dict:
    sh, sm = map(int, start_hhmm.split(":"))
    eh, em = map(int, end_hhmm.split(":"))
    start_iso = f"{date_iso}T{sh:02d}:{sm:02d}:00"
    end_iso = f"{date_iso}T{eh:02d}:{em:02d}:00"
    return {
        "summary": decorated_summary(subject),
        "description": _event_description(uid, source_desc),
        "start": {"dateTime": start_iso, "timeZone": TIMEZONE},
        "end":   {"dateTime": end_iso, "timeZone": TIMEZONE},
        "colorId": COLOUR_PERSONAL,
        "extendedProperties": {
            "private": {
                "xhale_uid": uid,
                "xhale_subject_hash": hash_str(subject),
                "xhale_synced_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "xhale_classification": classification,
            }
        },
    }


# ============================================================
# Time slot helper (D17 stacking)
# ============================================================

def stacking_slot(stack_index: int) -> tuple:
    """For same-day no-time events: event 1 → 07:00, event 2 → 08:30, event 3 → 10:00.

    Each session 90 min, with no gap. Morning slot is Pete's expected default —
    most unparseable training events are early-morning sessions where Xhale
    simply omitted the time. (Flipped from 19:00 evening slot on 2026-05-25
    after the Loren-filter removed the evening-context events that had
    motivated the original evening default.)
    """
    base_minutes = 7 * 60
    start_minutes = base_minutes + (stack_index - 1) * 90
    end_minutes = start_minutes + 90
    return (f"{start_minutes // 60:02d}:{start_minutes % 60:02d}",
            f"{end_minutes // 60:02d}:{end_minutes % 60:02d}")


# ============================================================
# Daily-note append
# ============================================================

def append_daily_note_line(line: str):
    if not DAILY.exists():  # headless (Railway): no vault Daily/ dir — skip the daily-note mirror
        return
    today_iso = dt.datetime.now().astimezone(_canary_tz()).date().isoformat()
    note_path = DAILY / f"{today_iso}.md"
    section = "## GCal twice-daily sync (Automated)"
    if note_path.exists():
        text = note_path.read_text()
        if section in text:
            # Append to existing section
            text = text.rstrip() + f"\n{line}\n"
        else:
            text = text.rstrip() + f"\n\n{section}\n{line}\n"
        note_path.write_text(text)
    else:
        note_path.write_text(f"---\ntype: daily\ndate: {today_iso}\ntags: [daily]\n---\n\n# Daily {today_iso}\n\n{section}\n{line}\n")


def _canary_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Atlantic/Canary")
    except Exception:
        return dt.timezone.utc


def append_run_log(block: str):
    if not WORK.exists():  # headless (Railway): no vault work dir — skip the run-log
        return
    if not RUNLOG_FILE.exists():
        RUNLOG_FILE.write_text("---\ntype: run-log\nstatus: active\ncreated: 2026-05-24\ntags: [automation, cron, xhale, run-log]\n---\n\n# Xhale → GCal sync — run log\n\n")
    with RUNLOG_FILE.open("a") as f:
        f.write(block)


# ============================================================
# Urgent email (D21 token rotation, D23 journal-miss)
# ============================================================

def send_urgent_email(subject: str, body: str):
    try:
        g = gmail_api()
        g.send(PETE_EMAIL, subject, body)
    except Exception as e:
        print(f"  send_urgent_email failed: {e}", file=sys.stderr)


# ============================================================
# Main run
# ============================================================

def run(dry: bool = False):
    started_at = dt.datetime.now(dt.timezone.utc)
    started_local = started_at.astimezone(_canary_tz())
    print(f"=== xhale-sync run | {started_local.isoformat()} | dry={dry} ===")

    counts = {
        "feed": 0, "created": 0, "patched": 0, "linked_existing": 0,
        "skipped_already_correct": 0, "filtered": 0, "unknowns": 0,
        "deletions": 0, "travel_verified": 0, "travel_missing": 0,
        "skipped_update": 0, "skipped_journal": 0, "skipped_rest_day": 0, "skipped_duplicate": 0,
        "skipped_diary": 0,
        "conflict": 0,
    }
    # Per-date cache of existing GCal events on Pete's primary, used by the
    # duplicate-detection check. Populated lazily, one API call per date.
    same_day_cache: dict[str, list] = {}
    attention_lines = []
    errors = []

    # 1. Fetch ICS
    ics_text = None
    fetch_error = None
    try:
        ics_text = fetch_ics()
    except FetchError as e:
        fetch_error = str(e)
        errors.append(f"fetch: {fetch_error}")
        print(f"  Fetch failed: {fetch_error}", file=sys.stderr)
        # D21: urgent email on fetch failure
        if any(t in fetch_error for t in ("401", "403", "rotated")):
            if not dry:
                send_urgent_email(
                    "URGENT: Train Xhale feed token rotated — sync stopped",
                    f"The xhale-sync cron failed at {started_local.isoformat()} with:\n\n  {fetch_error}\n\n"
                    f"Token rotation likely. To fix: log in to trainxhale.com, copy the new webcal URL, "
                    f"extract the token from it, paste it into Library/processes/secrets/xhale-feed-token "
                    f"(replace the existing single-line value). Next cron run will resume normally.",
                )
            suffix = " — see urgent email" if not dry else " — (dry-run, email suppressed)"
            attention_lines.append(f"ATTENTION: Xhale feed token may have rotated{suffix}")
        else:
            attention_lines.append(f"ATTENTION: Xhale fetch failed ({fetch_error[:80]})")
    else:
        events = parse_vevents(ics_text)
        counts["feed"] = len(events)
        print(f"  Feed events: {len(events)}")

        # 2. Filter to date window
        today = started_local.date()
        window_start = today - dt.timedelta(days=DAYS_BACK)
        window_end = today + dt.timedelta(days=DAYS_AHEAD)

        in_window = []
        for ev in events:
            try:
                d = parse_dtstart_date(ev.get("DTSTART", ""))
            except Exception:
                continue
            if window_start <= d <= window_end:
                ev["_date"] = d
                in_window.append(ev)

        # Sort by date+UID for stable processing + stacking
        in_window.sort(key=lambda e: (e["_date"], e.get("UID", "")))

        # 3. Classify
        loren_patterns = load_loren_patterns()
        for ev in in_window:
            # Races are Pete's — owned in GCal, sync ignores them (2026-06-14).
            # The feed tags them unambiguously with a `race-` UID, so route them
            # straight to "diary" (the ignore branch) without text classification.
            if ev.get("UID", "").startswith("race-"):
                ev["_class"] = "diary"
            else:
                ev["_class"] = classify(ev.get("SUMMARY", ""), ev.get("DESCRIPTION", ""), loren_patterns)

        # 4. Journal miss-detection (D23) — runs at BOTH 07:00 and 18:00
        yesterday = today - dt.timedelta(days=1)
        journal_start = load_journal_start_date()
        journal_dates = {e["_date"] for e in in_window if e["_class"] == "journal"}
        if yesterday < journal_start:
            print(f"  Journal check skipped — yesterday {yesterday.isoformat()} pre-dates start ({journal_start.isoformat()})")
        elif yesterday not in journal_dates:
            print(f"  Journal MISSING for {yesterday.isoformat()}")
            if not dry:
                send_urgent_email(
                    f"URGENT: Xhale journal missing for {yesterday.isoformat()}",
                    f"Yesterday ({yesterday.isoformat()}) has no journal entry in Train Xhale.\n\n"
                    f"Open Xhale and add a daily journal post for {yesterday.isoformat()} so Loren gets the continuity. "
                    f"Pattern recognised: any subject containing 'journal' (case-insensitive).\n\n"
                    f"Cron: gcal-twice-daily-sync. Run at {started_local.isoformat()}.",
                )
            suffix = " — urgent email sent" if not dry else " — (dry-run, email suppressed)"
            attention_lines.append(f"ATTENTION: Xhale journal MISSING for {yesterday.isoformat()}{suffix}")
        else:
            print(f"  Journal present for {yesterday.isoformat()}")

        # 5+. Per-row processing
        ledger = load_ledger()
        api = cal_api()

        # Track stacking: per-date count of training events that need placeholder time
        no_time_seen_per_date = {}

        for ev in in_window:
            uid = ev.get("UID", "")
            subject = ev.get("SUMMARY", "")
            desc = ev.get("DESCRIPTION", "")
            date = ev["_date"]
            cls = ev["_class"]

            if cls == "filtered":
                counts["filtered"] += 1
                # If a filtered event was previously created (classified as
                # something else before the pattern was added — e.g. "loren"
                # added 2026-05-25 after 5 Loren events had already been
                # created as "training"), clean it up: delete the GCal event +
                # drop the ledger entry, unless Pete has manually modified it.
                entry = ledger.get(uid)
                if entry and entry.get("gcal_event_id") and \
                   entry.get("gcal_match_type") == "created-by-sync" and \
                   not entry.get("manually_modified"):
                    try:
                        ev_date = dt.date.fromisoformat(entry.get("xhale_date", "1970-01-01"))
                    except Exception:
                        ev_date = today
                    if ev_date >= today:
                        if dry:
                            print(f"  DRY FILTERED DELETE: {uid} {subject!r} -> {entry['gcal_event_id']}")
                        else:
                            try:
                                result = safe_delete_event(api, entry["gcal_event_id"], "primary")
                                tag = "FILTERED DELETE" if result == "deleted" else "FILTERED ALREADY-GONE"
                                print(f"  {tag}: {uid} {subject!r}")
                                if result == "deleted":
                                    counts["deletions"] += 1
                                ledger.pop(uid, None)
                            except Exception as e:
                                errors.append(f"filtered delete {uid}: {e}")
                                print(f"  FILTERED DELETE FAILED for {uid}: {e}", file=sys.stderr)
                continue
            if cls == "update":
                counts["skipped_update"] += 1
                continue
            if cls == "journal":
                counts["skipped_journal"] += 1
                continue
            if cls == "rest_day":
                # Pete's rule 2026-05-25: rest days are never created in GCal.
                # If a rest-day event was previously created (classified as
                # "training" before this filter existed), clean it up: delete the
                # GCal event + drop the ledger entry, unless Pete has manually
                # modified it.
                counts["skipped_rest_day"] += 1
                entry = ledger.get(uid)
                if entry and entry.get("gcal_event_id") and \
                   entry.get("gcal_match_type") == "created-by-sync" and \
                   not entry.get("manually_modified"):
                    try:
                        ev_date = dt.date.fromisoformat(entry.get("xhale_date", "1970-01-01"))
                    except Exception:
                        ev_date = today
                    if ev_date >= today:
                        if dry:
                            print(f"  DRY REST-DAY DELETE: {uid} {subject!r} -> {entry['gcal_event_id']}")
                        else:
                            try:
                                result = safe_delete_event(api, entry["gcal_event_id"], "primary")
                                tag = "REST-DAY DELETE" if result == "deleted" else "REST-DAY ALREADY-GONE"
                                print(f"  {tag}: {uid} {subject!r}")
                                if result == "deleted":
                                    counts["deletions"] += 1
                                ledger.pop(uid, None)
                            except Exception as e:
                                errors.append(f"rest-day delete {uid}: {e}")
                                print(f"  REST-DAY DELETE FAILED for {uid}: {e}", file=sys.stderr)
                continue
            if cls == "diary":
                # Pete owns diary entries + races in GCal (2026-06-14). The sync
                # never creates, patches or deletes them. If we were previously
                # tracking this UID (created-by-sync or linked-to-existing),
                # RELEASE it: drop the ledger entry so it's never touched again
                # — but LEAVE any existing GCal event exactly where it is (it
                # becomes Pete's). This is a release, NOT a delete (cf. rest_day):
                # dropping the ledger entry also makes the event immune to the
                # deletion-propagation pass below, so removing the Xhale copy can
                # never pull Pete's diary event off the calendar.
                counts["skipped_diary"] += 1
                if uid in ledger:
                    ledger.pop(uid, None)
                continue
            if cls == "unknown":
                counts["unknowns"] += 1
                attention_lines.append(f"ATTENTION: unknown classification — uid={uid} subject={subject!r}")
                continue

            if cls == "travel":
                # Verify flight in diary on this date
                fl = find_flight_on_date(api, date.isoformat())
                if fl:
                    counts["travel_verified"] += 1
                else:
                    counts["travel_missing"] += 1
                    attention_lines.append(
                        f"ATTENTION: Xhale says '{subject}' on {date.isoformat()} but no flight in diary"
                    )
                continue

            # === Training rows ===
            assert cls == "training"

            new_subject_hash = hash_str(subject)
            new_desc_hash = hash_str(desc)

            entry = ledger.get(uid)

            # === Decide if we need to call the LLM ===
            # LLM is called ONLY when:
            #   (a) no ledger entry exists (first-time event), OR
            #   (b) the Xhale subject/description hash has changed since last sync
            # Otherwise we reuse the cached parsed time from the ledger.
            need_llm = False
            if entry is None:
                need_llm = True  # brand new event
            elif entry.get("manually_modified"):
                need_llm = False  # we'll skip this entry below anyway
            elif (new_subject_hash != entry.get("xhale_subject_hash") or
                  new_desc_hash != entry.get("xhale_description_hash")):
                need_llm = True  # Loren/Pete edited the Xhale subject

            if need_llm:
                # Try regex first (deterministic, free, rate-limit-proof).
                start_hhmm, end_hhmm = parse_time_regex(subject)
                time_source = "regex"
                if not start_hhmm:
                    # No clear time pattern -- fall back to LLM with brief
                    # inter-call delay for rate-limit politeness when batching.
                    time.sleep(1.0)
                    start_hhmm, end_hhmm = parse_time_llm(subject, desc)
                    time_source = "llm"
                if not start_hhmm:
                    idx = no_time_seen_per_date.get(date, 0) + 1
                    no_time_seen_per_date[date] = idx
                    start_hhmm, end_hhmm = stacking_slot(idx)
                    time_source = "placeholder-stacked" if idx > 1 else "placeholder"
                elif not end_hhmm:
                    h, m = map(int, start_hhmm.split(":"))
                    total = h * 60 + m + duration_for(subject)
                    end_hhmm = f"{(total // 60) % 24:02d}:{total % 60:02d}"
            elif entry is not None:
                # Reuse cached times from ledger (no LLM call)
                start_iso = entry.get("parsed_start_iso", "")
                end_iso = entry.get("parsed_end_iso", "")
                start_hhmm = start_iso.split("T")[1][:5] if "T" in start_iso else PLACEHOLDER_TIME
                end_hhmm = end_iso.split("T")[1][:5] if "T" in end_iso else PLACEHOLDER_TIME
                time_source = entry.get("time_source", "cached")
            else:
                # Should never reach here — entry is None handled above
                start_hhmm, end_hhmm = PLACEHOLDER_TIME, "08:30"
                time_source = "fallback"

            # **PROTECT PRE-EXISTING EVENTS** — if ledger entry says we linked
            # this Xhale UID to one of Pete's own GCal events (created outside
            # this script), the script must NEVER patch the GCal event's title,
            # time, or description, even if Xhale's subject/description hash
            # changes. Pete's event is his event. We only remember the link.
            # (Bug 2026-05-25: this check used to live below the patch path
            # at line ~906, so a hash change overwrote a real Team Meeting
            # event title + time.)
            if entry and entry.get("linked_to_existing"):
                counts["skipped_duplicate"] += 1
                # Keep the last_synced fresh + record any subject drift in the
                # ledger so we know Xhale changed, without touching the GCal event.
                if (new_subject_hash != entry.get("xhale_subject_hash") or
                        new_desc_hash != entry.get("xhale_description_hash")):
                    entry["xhale_subject"] = subject
                    entry["xhale_description"] = desc
                    entry["xhale_subject_hash"] = new_subject_hash
                    entry["xhale_description_hash"] = new_desc_hash
                entry["last_synced"] = dt.datetime.now(dt.timezone.utc).isoformat()
                ledger[uid] = entry
                continue

            if entry and entry.get("gcal_event_id") and not entry.get("manually_modified"):
                # Ledger hit, not manually modified — check live
                try:
                    live = api.get_event(entry["gcal_event_id"], "primary")
                except Exception:
                    live = None
                if live is None or live.get("status") == "cancelled":
                    # Event deleted in GCal — treat as manually-removed; never recreate
                    entry["manually_modified"] = True
                    entry["manually_modified_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
                    entry["manually_modified_reason"] = "deleted in GCal"
                    counts["skipped_already_correct"] += 1
                    continue

                # D4: check if Pete edited it in GCal
                if gcal_event_updated_after(live, entry.get("last_synced", "")):
                    entry["manually_modified"] = True
                    entry["manually_modified_at"] = live.get("updated")
                    counts["skipped_already_correct"] += 1
                    continue

                # D5: if Xhale subject/description hash changed, patch
                # (only reached for created-by-sync events — linked-to-existing
                # short-circuited above)
                if (new_subject_hash != entry.get("xhale_subject_hash") or
                        new_desc_hash != entry.get("xhale_description_hash")):
                    if dry:
                        print(f"  DRY PATCH: {uid} {subject!r}")
                    else:
                        api.update_event(
                            entry["gcal_event_id"], "primary",
                            summary=decorated_summary(subject),
                            description=_event_description(uid, desc),
                            start={"dateTime": f"{date.isoformat()}T{start_hhmm}:00", "timeZone": TIMEZONE},
                            end={"dateTime": f"{date.isoformat()}T{end_hhmm}:00", "timeZone": TIMEZONE},
                        )
                    entry["xhale_subject"] = subject
                    entry["xhale_description"] = desc
                    entry["xhale_subject_hash"] = new_subject_hash
                    entry["xhale_description_hash"] = new_desc_hash
                    entry["parsed_start_iso"] = f"{date.isoformat()}T{start_hhmm}"
                    entry["parsed_end_iso"] = f"{date.isoformat()}T{end_hhmm}"
                    entry["time_source"] = time_source
                    entry["last_synced"] = dt.datetime.now(dt.timezone.utc).isoformat()
                    counts["patched"] += 1
                else:
                    counts["skipped_already_correct"] += 1
                    entry["last_synced"] = dt.datetime.now(dt.timezone.utc).isoformat()
                ledger[uid] = entry
                continue

            if entry and entry.get("manually_modified"):
                counts["skipped_already_correct"] += 1
                continue

            # === Duplicate detection — Pete's "no duplicates" rule ===
            # Before creating, scan Pete's calendar for same-date events whose
            # activity category overlaps with this Xhale event. If a match is
            # found, skip create + remember it in the ledger so we don't keep
            # re-scanning across runs.
            existing, match_kind = find_existing_match(api, date, subject, same_day_cache, start_hhmm)
            if match_kind == "duplicate":
                msg = (f"  DUPLICATE SKIPPED: {date.isoformat()} {subject!r} "
                       f"matches existing {existing.get('summary','?')!r} "
                       f"({existing.get('start',{}).get('dateTime') or existing.get('start',{}).get('date','?')})")
                print(msg)
                attention_lines.append(
                    f"DUPLICATE: Xhale '{subject}' on {date.isoformat()} skipped — "
                    f"matches existing diary entry '{existing.get('summary','?')}'."
                )
                ledger[uid] = {
                    "first_seen": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "last_synced": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "classification": cls,
                    "xhale_subject": subject,
                    "xhale_description": desc,
                    "xhale_date": date.isoformat(),
                    "xhale_subject_hash": new_subject_hash,
                    "xhale_description_hash": new_desc_hash,
                    "parsed_start_iso": f"{date.isoformat()}T{start_hhmm}",
                    "parsed_end_iso": f"{date.isoformat()}T{end_hhmm}",
                    "time_source": time_source,
                    "gcal_event_id": existing.get("id"),
                    "gcal_match_type": "linked-to-existing",
                    "manually_modified": False,
                    "linked_to_existing": True,
                    "linked_existing_summary": existing.get("summary"),
                    "linked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
                counts["skipped_duplicate"] += 1
                continue

            if match_kind == "conflict":
                # A training session lands at the same time as a DIFFERENT event
                # already in the diary. Don't auto-create — flag it for Pete to
                # resolve (e.g. cancel the session). Flag once on first detection
                # (re-flagging every run would spam the daily note).
                already = bool(entry) and entry.get("gcal_match_type") == "conflict-flagged"
                est = existing.get("start", {}).get("dateTime") or existing.get("start", {}).get("date", "?")
                print(f"  CONFLICT {'(known)' if already else 'FLAGGED'}: {date.isoformat()} {start_hhmm} "
                      f"{subject!r} clashes with {existing.get('summary','?')!r} ({est})")
                if not already:
                    attention_lines.append(
                        f"CONFLICT: Xhale '{subject}' on {date.isoformat()} at {start_hhmm} clashes with existing "
                        f"'{existing.get('summary','?')}' (same time, different event). NOT created — resolve in "
                        f"Xhale (e.g. cancel the session if you can't make it)."
                    )
                ledger[uid] = {
                    "first_seen": (entry or {}).get("first_seen", dt.datetime.now(dt.timezone.utc).isoformat()),
                    "last_synced": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "classification": cls,
                    "xhale_subject": subject,
                    "xhale_date": date.isoformat(),
                    "parsed_start_iso": f"{date.isoformat()}T{start_hhmm}",
                    "gcal_event_id": None,
                    "gcal_match_type": "conflict-flagged",
                    "conflict_with": existing.get("summary"),
                    "manually_modified": False,
                }
                counts["conflict"] += 1
                continue

            # === No ledger entry, no duplicate — create ===
            payload = build_event_payload(subject, date.isoformat(), start_hhmm, end_hhmm, uid, cls, desc)
            if dry:
                print(f"  DRY CREATE: {date.isoformat()} {start_hhmm}-{end_hhmm} {subject!r}")
                created_id = "DRY-RUN"
            else:
                try:
                    created = api.create_event(calendar_id="primary", event=payload)
                    created_id = created.get("id", "")
                    # Use GCal's `updated` timestamp as our last_synced so the
                    # next run's D4 check has a reference equal to (not before)
                    # the event's recorded last-edit time. Prevents the
                    # create-time race from false-positiving manually_modified.
                    created_updated = created.get("updated") or dt.datetime.now(dt.timezone.utc).isoformat()
                except Exception as e:
                    errors.append(f"create {uid}: {e}")
                    print(f"  CREATE FAILED for {uid}: {e}", file=sys.stderr)
                    continue
            counts["created"] += 1
            ledger[uid] = {
                "first_seen": dt.datetime.now(dt.timezone.utc).isoformat(),
                "last_synced": created_updated if not dry else dt.datetime.now(dt.timezone.utc).isoformat(),
                "classification": cls,
                "xhale_subject": subject,
                "xhale_description": desc,
                "xhale_date": date.isoformat(),
                "xhale_subject_hash": new_subject_hash,
                "xhale_description_hash": new_desc_hash,
                "parsed_start_iso": f"{date.isoformat()}T{start_hhmm}",
                "parsed_end_iso": f"{date.isoformat()}T{end_hhmm}",
                "time_source": time_source,
                "gcal_event_id": created_id,
                "gcal_match_type": "created-by-sync",
                "manually_modified": False,
            }

        # === Deletions: ledger UIDs no longer in feed → remove from GCal ===
        # Pete's rule (2026-06-14): "anything deleted from Xhale should be pulled
        # from GCal." Source-deletion wins, so we delete even hand-edited / trimmed
        # sessions (manually_modified). The manually_modified flag only protects
        # against OVERWRITING Pete's edits WHILE the session still exists in the
        # feed; once it's gone from the feed, the GCal copy goes too. We still only
        # touch events WE created (created-by-sync — never Pete's own) and never
        # rewrite history (future-dated only). safe_delete_event is idempotent, so
        # an already-deleted-in-GCal event is a harmless no-op.
        feed_uids = {e.get("UID", "") for e in in_window}
        for uid, entry in list(ledger.items()):
            if uid in feed_uids:
                continue
            if entry.get("gcal_match_type") != "created-by-sync":
                continue
            try:
                ev_date = dt.date.fromisoformat(entry.get("xhale_date", "1970-01-01"))
            except Exception:
                continue
            if ev_date < today:
                continue
            # Delete in GCal
            if dry:
                print(f"  DRY DELETE: {uid} {entry.get('xhale_subject','')}")
                counts["deletions"] += 1
            else:
                try:
                    result = safe_delete_event(api, entry["gcal_event_id"], "primary")
                except Exception as e:
                    errors.append(f"delete {uid}: {e}")
                    continue
                # Either way (deleted-now or already-gone), the ledger should
                # drop the entry so it doesn't keep being retried.
                if result == "deleted":
                    counts["deletions"] += 1
            entry["removed_from_feed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            ledger[uid] = entry

        if not dry:
            save_ledger(ledger)

    # 9. Colour-coder fold (D24) — always runs, even if Xhale failed
    colour_result = {}
    try:
        cc = colour_coder()
        if dry:
            # In dry mode, use the dry-run mode of the colour coder so it doesn't mutate
            colour_result = cc.run("dry-run", 2, 365)
        else:
            colour_result = cc.run("apply-recent", 2, 365)
    except Exception as e:
        errors.append(f"colour-coder: {e}")
        print(f"  Colour-coder failed: {e}", file=sys.stderr)

    # 10. Daily-note line
    finished_at = dt.datetime.now(dt.timezone.utc).astimezone(_canary_tz())
    hh = finished_at.strftime("%H:%M")
    if fetch_error:
        xhale_summary = f"Xhale FETCH FAILED ({fetch_error[:60]})"
    else:
        xhale_summary = (
            f"Xhale: {counts['feed']} in feed, {counts['created']} created, "
            f"{counts['patched']} patched, {counts['linked_existing'] + counts['skipped_already_correct']} already-linked, "
            f"{counts['skipped_duplicate']} dup-skipped, {counts['conflict']} conflicts flagged, "
            f"{counts['filtered']} filtered, {counts['skipped_rest_day']} rest-days skipped, "
            f"{counts['skipped_diary']} diary-ignored, "
            f"{counts['unknowns']} unknowns, {counts['deletions']} deletions"
        )
    if counts['travel_verified'] or counts['travel_missing']:
        xhale_summary += f", travel {counts['travel_verified']} verified / {counts['travel_missing']} missing"

    cc_summary = (
        f"Colour: {colour_result.get('personal', 0) + colour_result.get('sygma', 0) + colour_result.get('cd', 0) + colour_result.get('travel', 0)} newly-coloured, "
        f"{colour_result.get('skipped_already_coloured', 0)} skipped"
    )

    status = "ok"
    if errors:
        status = f"errors ({len(errors)})"

    line = f"- {hh} run | {xhale_summary} | {cc_summary} | Status: {status}{' [DRY-RUN]' if dry else ''}."
    if not dry:
        append_daily_note_line(line)
    print()
    print(line)
    for al in attention_lines:
        print(f"  {al}")
        if not dry:
            append_daily_note_line(f"- {al}")

    # Run-log block
    run_block = (
        f"\n## {finished_at.isoformat()}{' (DRY-RUN)' if dry else ''}\n\n"
        f"{line}\n"
    )
    for al in attention_lines:
        run_block += f"- {al}\n"
    if errors:
        run_block += "\n### Errors\n\n"
        for err in errors:
            run_block += f"- {err}\n"
    if not dry:
        append_run_log(run_block)

    return {"counts": counts, "colour": colour_result, "attention": attention_lines, "errors": errors}


# ============================================================
# Test mode: run README test cases through Haiku
# ============================================================

def test_llm():
    cases = [
        ("2. Running Endurance: 2pm run", "Intensity: zone 2", "14:00", None),
        ("Endless Pool & Catch Up: 11am-1pm", "", "11:00", "13:00"),
        ("Swim Endurance: Open Water with kimbo", "Intensity: zone 1", None, None),
        ("1. Swim Tech: Group Pool", "Intensity: zone 2", None, None),
        ("Run 10:00 . 11:30", "", "10:00", "11:30"),
        ("evening run around 6", "", "18:00", None),
        ("Pool 6.30am", "", "06:30", None),
        ("Sea swim 7-8.30", "", "07:00", "08:30"),
        ("Brick: 1hr bike then 30min run from 5pm", "", "17:00", "18:30"),
        ("Z2 long run", "Intensity: zone 2", None, None),
        ("9.30 swim", "", "09:30", None),
        ("1. Swim 10am-11:30", "", "10:00", "11:30"),
        ("2. Bike at noon", "", "12:00", None),
    ]
    passes = 0
    fails = []
    for subject, desc, exp_s, exp_e in cases:
        time.sleep(5.0)  # space out test calls to avoid rate limit (production is naturally spaced)
        got_s, got_e = parse_time_llm(subject, desc)
        ok = got_s == exp_s and got_e == exp_e
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {subject!r:60s} → got=({got_s!r},{got_e!r}) expected=({exp_s!r},{exp_e!r})")
        if ok:
            passes += 1
        else:
            fails.append((subject, (got_s, got_e), (exp_s, exp_e)))
    print(f"\n{passes}/{len(cases)} cases passed.")
    if fails:
        print(f"{len(fails)} fails — see above. Tune the system prompt or add to lessons.")
    return passes == len(cases)


# ============================================================
# CLI
# ============================================================

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")  # default = run (the Railway bootstrap invokes with no subcommand)
    sub.add_parser("run")
    sub.add_parser("dry-run")
    sub.add_parser("test-llm")
    args = p.parse_args()

    if args.cmd in (None, "run"):
        try:
            run(dry=False)
        except Exception as e:
            print(f"FATAL: {e}", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)
    elif args.cmd == "dry-run":
        run(dry=True)
    elif args.cmd == "test-llm":
        ok = test_llm()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
