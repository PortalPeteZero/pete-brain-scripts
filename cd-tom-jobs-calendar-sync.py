#!/usr/bin/env python3
"""
cd-tom-jobs-calendar-sync.py -- twice-daily Odoo -> Tom Google Calendar sync.

Spec:    Library/processes/tom-jobs-calendar-sync.md
Pair:    cd-tom-jobs-photo-sort.py
Cron:    30 12 * * * + 0 18 * * *  Atlantic/Canary

One-way: Odoo calendar.event (Tom = partner_id 12) -> Tom's primary Google
Calendar (impersonated via DWD). Events are mirrored with full enrichment
(customer, contact, address, Maps link, lead notes, jobtype colour).

Usage:
  python3 cd-tom-jobs-calendar-sync.py             # incremental sync
  python3 cd-tom-jobs-calendar-sync.py --first-run # wipe Odoo built-in sync
                                                   # leftovers + full re-create
  python3 cd-tom-jobs-calendar-sync.py --dry-run   # plan only, no Cal writes
"""
# CRON-META
# what: Odoo → Tom Google Calendar sync (evening). Appends summary to daily note.
# why: Evening half of the Odoo→Tom calendar mirror; this run also writes the daily-note summary.
# reads: Odoo (CRM jobs: calendar.event + crm.lead)
# writes: Tom's Google Calendar events · daily-note block
# entity: canary-detect
# schedule: 0 19 * * *
# timezone: Atlantic/Canary
# note: multi-service: also deployed as the -noon key (30 13 local / 30 12 UTC)
# CRON-META-END

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Constants (locked from spec)
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.resolve()
LOGS_DIR    = SCRIPTS_DIR / "_logs"
CACHE_DIR   = SCRIPTS_DIR / "_cache"
GEOCODE_CACHE = CACHE_DIR / "geocode-calendar-sync.json"

TOM_PARTNER_ID  = 12
TOM_USER_ID     = 10
TOM_EMAIL       = "tom.robertson@canary-detect.com"
TOM_CAL_ID      = "primary"
ODOO_BASE       = "https://camello-blanco-sl.odoo.com"
ODOO_DB_KEY     = "camello-blanco-sl_odoo_id"   # the built-in sync's marker key
SYNCED_BY       = "cd-tom-jobs-calendar-sync"

ERROR_NOTIFY_TO     = ["pete.ashcroft@sygma-solutions.com"]
FIRST_RUN_NOTIFY_TO = ["pete.ashcroft@sygma-solutions.com"]

WINDOW_BACK_DAYS    = 0   # 2026-05-25: locked at 0. Past Odoo events are not processed.
                          # Past GCal events are frozen (not modified, not orphan-deleted) unless
                          # they happen to also be findable via wider scan AND their Odoo source
                          # has moved INTO the narrow window (then they get PATCHED to the new
                          # date). See the SCAN_BACK_DAYS comment below for the join-key story.
WINDOW_FORWARD_DAYS = 60

# Wider window used ONLY for "find existing GCal records by odoo_event_id". Necessary
# because Odoo's event_id is the join key, not the date — if Nicola reschedules an Odoo
# event forward, the existing GCal record may still be sitting in the past. The narrow
# Odoo query above doesn't return past Odoo events at all, but the GCal scan has to look
# wider to find the existing record so it can be PATCHED to the new date instead of a
# duplicate being created. Orphan cleanup remains scoped to events with start >= today.
SCAN_BACK_DAYS      = 365
LOG_RETENTION_DAYS  = 90
WIPE_SAFETY_CAP     = 200
DESCRIPTION_MAX_CHARS = 4000

CANARY = ZoneInfo("Atlantic/Canary")
UTC    = dt.timezone.utc

# Colour mapping
COLOR_BY_JOBTYPE = {
    "VLS":               "9",   # Blueberry
    "PLS":               "9",
    "Drain Survey":      "9",
    "Community Survey":  "9",
    "Repair":            "11",  # Tomato
    "Reinstatement":     "11",
    "LeakGuard Install": "10",  # Basil
    "LeakGuard Check":   "10",
    "Initial Visit":     "8",   # Graphite
    "EcoFinish":         "5",   # Banana
    "Epoxy":             "6",   # Tangerine
    "Pump":              "1",   # Lavender
    "Civils":            "1",
    "Site Clear":        "1",
    "Admin":             "1",
    "Fiesta":            "2",   # Sage
    "ITV":               "2",
    "Keep clear":        "2",
    "Holiday":           "2",
    "Reminder":          "2",
}

# Same regex set as photo-sort but with all jobtypes (none "skip" in calendar context)
JOBTYPE_RULES = [
    (re.compile(r"\bvls\b", re.I),                    "VLS"),
    (re.compile(r"\bpls\b", re.I),                    "PLS"),
    (re.compile(r"\bcommunity\s+survey\b", re.I),     "Community Survey"),
    (re.compile(r"\bcommunity\b", re.I),              "Community Survey"),
    (re.compile(r"\bdrain\s+survey\b", re.I),         "Drain Survey"),
    (re.compile(r"\bleakguard\s+install\b", re.I),    "LeakGuard Install"),
    (re.compile(r"\bcheck\s+leakguard\b", re.I),      "LeakGuard Check"),
    (re.compile(r"\binitial\s+visit\b", re.I),        "Initial Visit"),
    (re.compile(r"\breinstate", re.I),                "Reinstatement"),
    (re.compile(r"\brepair\b", re.I),                 "Repair"),
    (re.compile(r"\bquote\b", re.I),                  "Repair"),
    (re.compile(r"\bcapacitor\b", re.I),              "Repair"),
    (re.compile(r"\becofinish\b", re.I),              "EcoFinish"),
    (re.compile(r"\bepoxy\b", re.I),                  "Epoxy"),
    (re.compile(r"\bdrain\b", re.I),                  "Drain Survey"),
    (re.compile(r"\bdishwasher\b", re.I),             "Repair"),
    (re.compile(r"\bvac\s+line\b", re.I),             "Repair"),
    (re.compile(r"\btemp\s+fix\b", re.I),             "Repair"),
    (re.compile(r"\bfaulty\s+pump\b", re.I),          "Repair"),
    (re.compile(r"\bclear\s+rubbish\b", re.I),        "Site Clear"),
    (re.compile(r"\bconcreting\b", re.I),             "Civils"),
    (re.compile(r"\bhoover\s+lawn\b", re.I),          "Civils"),
    (re.compile(r"\bbury\s+pipework\b", re.I),        "Civils"),
    (re.compile(r"\binstall\s+the\s+pool\s+light\b", re.I), "Civils"),
    (re.compile(r"\bdomestic\s+pump\b", re.I),        "Pump"),
    (re.compile(r"\bcollect\s+payment\b", re.I),      "Admin"),
    (re.compile(r"\bbreak\s+out\s+behind\b", re.I),   "Repair"),
    (re.compile(r"\b(locate ?/ ?repair|find\s+leak|leak\s+(in\s+front|outside))\b", re.I), "Repair"),
    (re.compile(r"\bmove\s+domestic\s+pump\b", re.I), "Pump"),
    (re.compile(r"\bnew\s+vls\b", re.I),              "VLS"),
    (re.compile(r"\bfiesta\b", re.I),                 "Fiesta"),
    (re.compile(r"\bitv\b", re.I),                    "ITV"),
    (re.compile(r"\bkeep\s+clear\b", re.I),           "Keep clear"),
    (re.compile(r"\bnicola\s+holiday\b", re.I),       "Holiday"),
    (re.compile(r"^reminder\b", re.I),                "Reminder"),
]

FOLDER_WORTHY = {"Community Survey", "VLS", "PLS", "Drain Survey",
                 "Repair", "Reinstatement", "LeakGuard Install"}

LOCATION_ABBREVS = ["PB", "PDC", "PDR", "CT", "Tias", "Tías", "Arrecife", "Haria",
                    "Tahiche", "Conil", "Nazaret", "Muñique", "Munique",
                    "Playa Honda", "Puerto Calero", "Puerto del Carmen",
                    "Playa Blanca", "Costa Teguise"]

# ---------------------------------------------------------------------------
# Lazy helpers
# ---------------------------------------------------------------------------

def _load_helper(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), str(SCRIPTS_DIR / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_calendar = None
_geocoding = None
_gmail = None

def calendar_helper():
    global _calendar
    if _calendar is None:
        _calendar = _load_helper("calendar-api")
    return _calendar

def geocode_helper():
    global _geocoding
    if _geocoding is None:
        _geocoding = _load_helper("geocoding-api")
    return _geocoding

def gmail_helper():
    global _gmail
    if _gmail is None:
        _gmail = _load_helper("gmail-api")
    return _gmail

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class Log:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        LOGS_DIR.mkdir(exist_ok=True, parents=True)
        date = dt.datetime.now(CANARY).date().isoformat()
        self.path = LOGS_DIR / f"{SYNCED_BY}-{date}.log"
        self._fh = open(self.path, "a", encoding="utf-8")

    def __call__(self, level, action, **kw):
        ts = dt.datetime.now(CANARY).isoformat(timespec="seconds")
        bits = " ".join(f"{k}={v!r}" for k, v in kw.items())
        line = f"[{ts}] {level:5s} {action:25s} {bits}"
        print(line)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self):
        self._fh.close()
        self._cleanup()

    def _cleanup(self):
        cutoff = dt.datetime.now(CANARY).date() - dt.timedelta(days=LOG_RETENTION_DAYS)
        for f in LOGS_DIR.glob(f"{SYNCED_BY}-*.log"):
            try:
                d = dt.date.fromisoformat(f.stem.split("-")[-3] + "-" + f.stem.split("-")[-2] + "-" + f.stem.split("-")[-1])
                if d < cutoff:
                    f.unlink()
            except (ValueError, IndexError):
                continue

# ---------------------------------------------------------------------------
# Odoo wrapper (subprocess via odoo-api.py)
# ---------------------------------------------------------------------------

def odoo(method, model, *args):
    cmd = ["python3", str(SCRIPTS_DIR / "odoo-api.py"), method, model, *args]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"Odoo {method} failed: {res.stderr[:300]}")
    out = res.stdout.strip()
    return json.loads(out) if out and out.startswith(("[", "{")) else None

def odoo_search_read(model, domain, fields, limit=500):
    return odoo("search-read", model, json.dumps(domain), ",".join(fields), str(limit))

def odoo_read(model, ids, fields):
    if not ids:
        return []
    ids_csv = ",".join(str(i) for i in ids)
    return odoo("read", model, ids_csv, ",".join(fields))

def odoo_write(model, record_id, values):
    return odoo("write", model, str(record_id), json.dumps(values))

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def today_canary():
    return dt.datetime.now(CANARY).date()

def now_iso_canary():
    return dt.datetime.now(CANARY).isoformat(timespec="seconds")

def now_iso_utc():
    return dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

def utc_iso(d_or_dt):
    if isinstance(d_or_dt, dt.date) and not isinstance(d_or_dt, dt.datetime):
        d_or_dt = dt.datetime.combine(d_or_dt, dt.time(0, 0), tzinfo=CANARY)
    return d_or_dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

def parse_odoo_dt(s):
    if not s:
        return None
    if "T" in s:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)

def to_rfc3339_canary(odoo_dt):
    if not odoo_dt:
        return None
    canary_dt = parse_odoo_dt(odoo_dt).astimezone(CANARY)
    return canary_dt.isoformat(timespec="seconds")

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_jobtype(name):
    """Pick the *current* jobtype from the event title.

    Rule: rightmost match wins. Supports Nicola's title-accumulation convention
    ([[cd-calendar-event-naming-convention]]):

        "VLS - Customer"                           -> VLS
        "VLS - Repair - Customer"                  -> Repair
        "VLS - Repair - Reinstatement - Customer"  -> Reinstatement
    """
    if not name:
        return None
    best_pos = -1
    best_jt = None
    for pat, jt in JOBTYPE_RULES:
        m = pat.search(name)
        if m and m.start() > best_pos:
            best_pos = m.start()
            best_jt = jt
    return best_jt

def is_folder_worthy(jt):
    return jt in FOLDER_WORTHY

def extract_location_abbrev(name):
    if not name:
        return ""
    parts = [p.strip() for p in re.sub(r"\([^)]*\)", "", name).split(" - ")]
    for p in parts[1:-1]:
        for abbr in LOCATION_ABBREVS:
            if re.search(rf"\b{re.escape(abbr)}\b", p):
                return abbr
    for abbr in LOCATION_ABBREVS:
        if re.search(rf"\b{re.escape(abbr)}\b", name):
            return abbr
    return ""

# ---------------------------------------------------------------------------
# HTML strip (no BeautifulSoup)
# ---------------------------------------------------------------------------

def strip_html(s):
    if not s:
        return ""
    # Remove script/style entirely
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", s, flags=re.I | re.S)
    # Replace <br>, <p>, </p>, </div> with newlines
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</\s*(p|div|li|tr)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    # HTML entities
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
           .replace("&lt;", "<").replace("&gt;", ">")
           .replace("&#39;", "'").replace("&quot;", '"')
           .replace("&euro;", "€").replace("&pound;", "£"))
    # Collapse whitespace
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

# ---------------------------------------------------------------------------
# Geocoding cache (DEDICATED for calendar-sync)
# ---------------------------------------------------------------------------

def load_geocode_cache():
    if not GEOCODE_CACHE.exists():
        return {}
    try:
        return json.loads(GEOCODE_CACHE.read_text())
    except Exception:
        return {}

def save_geocode_cache(cache):
    CACHE_DIR.mkdir(exist_ok=True, parents=True)
    GEOCODE_CACHE.write_text(json.dumps(cache, indent=2))

def geocode_lead(lead, cache, address_override=None):
    """Geocode the chosen site address. `address_override` is whatever the
    caller has decided is the canonical site address (Location (Survey) or
    the event's location); we no longer geocode the structured invoice fields.

    Return (lat, lon, location_type) or (None, None, None).
    """
    address = (address_override or "").strip()
    if not address:
        return None, None, None
    if address in cache:
        c = cache[address]
        return c.get("lat"), c.get("lon"), c.get("location_type")
    try:
        result = geocode_helper().geocode(address)
        if not result:
            cache[address] = {"lat": None, "lon": None, "location_type": None}
            return None, None, None
        cache[address] = {"lat": result["lat"], "lon": result["lon"],
                          "location_type": result.get("location_type", "APPROXIMATE")}
        return result["lat"], result["lon"], result.get("location_type", "APPROXIMATE")
    except Exception:
        cache[address] = {"lat": None, "lon": None, "location_type": None}
        return None, None, None

def maps_url(lat, lon, address):
    if lat is not None and lon is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    if address:
        return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(address)}"
    return None

# ---------------------------------------------------------------------------
# Build event payload
# ---------------------------------------------------------------------------

def _clean(s):
    """Coerce Odoo False / None / whitespace to empty string."""
    v = (s or "").strip()
    return v if v and v != "False" else ""

def _norm_for_compare(s):
    """Lowercase + collapse whitespace + drop trailing punctuation, for
    deciding 'do these two address strings basically agree?'."""
    if not s:
        return ""
    return re.sub(r"[\s,.]+$", "", " ".join(s.lower().split()))

def get_survey_address(lead):
    """Lead's 'Location (Survey)' Studio field x_studio_char_field_3qWjM."""
    return _clean(lead.get("x_studio_char_field_3qWjM")) if lead else ""

def get_event_location(ev):
    """The calendar event's free-text Location field."""
    return _clean(ev.get("location")) if ev else ""

def pick_site_address(lead, ev):
    """Decide what goes in the calendar event's `location` field (and what gets
    geocoded for the Maps link).

    Sources: Location (Survey) on the lead + calendar event Location.
    Structured street/zip on the lead is the INVOICE address and is NOT used.

    Merge rule:
      - both empty                                  -> '', no differ
      - only one populated                          -> that one, no differ
      - both populated & one contains the other     -> the more specific
                                                       (longer) one, no differ.
                                                       This catches the common
                                                       'PB' vs 'X street, PB'
                                                       abbreviation case.
      - both populated & genuinely independent      -> Location (Survey) wins
                                                       as the primary, BOTH
                                                       surfaced in description,
                                                       differ=True.

    Returns:
        site_address (str): the chosen address, or '' if neither source exists
        survey       (str): raw Location (Survey)
        cal          (str): raw event location
        differ       (bool): True iff both sources exist AND don't agree by
                             containment.
    """
    survey = get_survey_address(lead)
    cal = get_event_location(ev)

    if not survey and not cal:
        return "", "", "", False
    if not survey:
        return cal, "", cal, False
    if not cal:
        return survey, survey, "", False

    n_survey = _norm_for_compare(survey)
    n_cal = _norm_for_compare(cal)

    if n_survey == n_cal:
        # Identical (modulo whitespace/case) -- prefer survey for stability
        return survey, survey, cal, False
    if n_survey in n_cal:
        # Survey is a less-specific version of cal -> use cal
        return cal, survey, cal, False
    if n_cal in n_survey:
        # Cal is a less-specific version of survey -> use survey
        return survey, survey, cal, False

    # Survey is a short area code (e.g. "PB", "Tias") and cal is a real
    # address -- treat as a more-specific update, not a disagreement.
    if len(n_survey) <= 8 and len(n_cal) > len(n_survey):
        return cal, survey, cal, False

    # Genuine disagreement (both substantial, neither contains the other)
    # Survey wins as primary, both surfaced in description.
    return survey, survey, cal, True

# Kept for backwards-compatibility with anything that imports this module.
def build_address_string(lead):
    return get_survey_address(lead)

def build_title(ev, lead):
    raw = ev.get("name") or ""
    jobtype = classify_jobtype(raw)
    if not lead or not jobtype or not is_folder_worthy(jobtype):
        return raw
    customer = ((lead.get("partner_id") or [None, ""])[1] or
                lead.get("partner_name") or "").strip()
    contact = (lead.get("contact_name") or "").strip()
    if not customer and contact:
        customer = contact
    elif customer and contact and customer.lower() != contact.lower():
        customer = f"{customer} ({contact})"
    location = extract_location_abbrev(raw)
    if not location:
        location = (lead.get("city") or "").strip()
    bits = [jobtype, customer]
    if location:
        bits.append(location)
    return " - ".join(b for b in bits if b)

def build_description(ev, lead, lat, lon, geocode_summary,
                     site_address="", survey_addr="", cal_addr="", addrs_differ=False):
    if not lead:
        # No lead: still surface the event's location so Tom sees it
        cal_only = get_event_location(ev)
        if cal_only:
            base = strip_html(ev.get("description") or "")
            extra = f"\n\nAddress:   {cal_only}"
            return (base + extra)[:DESCRIPTION_MAX_CHARS]
        return strip_html(ev.get("description") or "")[:DESCRIPTION_MAX_CHARS]
    lines = []
    customer = ((lead.get("partner_id") or [None, ""])[1] or
                lead.get("partner_name") or "").strip()
    contact = (lead.get("contact_name") or "").strip()
    phone = (lead.get("phone") or "").strip()

    if customer:
        lines.append(f"Customer:  {customer}")
    if contact and contact.lower() != customer.lower():
        lines.append(f"Contact:   {contact}")
    if phone and phone != "False":
        lines.append(f"Phone:     {phone}")
    if site_address:
        lines.append("")
        lines.append(f"Address:   {site_address}")
        if addrs_differ:
            # Show the source that did NOT win, so Tom sees the disagreement
            # site_address == survey_addr by priority rule, so the disagreeing
            # one is the calendar event's location.
            other = cal_addr if _norm_for_compare(site_address) == _norm_for_compare(survey_addr) else survey_addr
            label = "Calendar:" if other == cal_addr else "Site:    "
            lines.append(f"{label}  {other}  (differs)")
    url = maps_url(lat, lon, site_address)
    if url:
        lines.append("")
        lines.append("Open in Maps:")
        lines.append(url)
    # CRM stage + notes + lead URL
    stage = (lead.get("stage_id") or [None, ""])[1] or ""
    if stage:
        lines.append("")
        lines.append(f"CRM stage:  {stage}")
    notes_raw = lead.get("description") or ""
    notes = strip_html(notes_raw)
    if notes:
        lines.append("Lead notes:")
        for ln in notes.splitlines():
            lines.append(f"  {ln}")
    lid = lead.get("id")
    if lid:
        lines.append("")
        lines.append(f"Lead in Odoo: {ODOO_BASE}/odoo/crm/{lid}")
    lines.append("")
    lines.append("─" * 40)
    # Note: deliberately no timestamp line here -- last_synced lives in
    # extendedProperties.private and is excluded from content_hash.
    # Including a per-run timestamp in the visible description would cause
    # content_hash to differ on every run, forcing a no-op PATCH every time.
    lines.append(f"Synced from Odoo  ·  [odoo:{ev['id']}]")  # stable marker
    desc = "\n".join(lines)
    return desc[:DESCRIPTION_MAX_CHARS]

def build_payload(ev, lead, geocode_cache):
    """Build the full Google Calendar event body."""
    title = build_title(ev, lead)
    jobtype = classify_jobtype(ev.get("name") or "")
    color_id = COLOR_BY_JOBTYPE.get(jobtype) if jobtype else None
    all_day = bool(ev.get("allday"))

    # Decide the site address (Location (Survey) preferred, then event location)
    site_address, survey_addr, cal_addr, addrs_differ = pick_site_address(lead, ev)

    # Geocode the chosen site address (NOT the invoice fields)
    lat = lon = location_type = None
    if site_address:
        lat, lon, location_type = geocode_lead(lead, geocode_cache,
                                               address_override=site_address)
    geocode_summary = location_type or ("none" if lead else "no-lead")

    description = build_description(
        ev, lead, lat, lon, geocode_summary,
        site_address=site_address, survey_addr=survey_addr,
        cal_addr=cal_addr, addrs_differ=addrs_differ,
    )
    # The calendar event's `location` field is the chosen site address.
    # If we have nothing at all, leave blank (no fallback to invoice address).
    location_field = site_address

    # Start/end
    start_iso = ev.get("start")
    stop_iso  = ev.get("stop")
    if all_day:
        start_date = parse_odoo_dt(start_iso).astimezone(CANARY).date()
        stop_date = parse_odoo_dt(stop_iso).astimezone(CANARY).date()
        start = {"date": start_date.isoformat()}
        end   = {"date": stop_date.isoformat()}
    else:
        start = {"dateTime": to_rfc3339_canary(start_iso), "timeZone": "Atlantic/Canary"}
        end   = {"dateTime": to_rfc3339_canary(stop_iso),  "timeZone": "Atlantic/Canary"}

    # Compute content_hash (excluding things like odoo_event_id / first_synced /
    # last_synced -- those don't affect "did the visible event change")
    hash_input = json.dumps({
        "title":       title,
        "start":       start.get("dateTime") or start.get("date"),
        "end":         end.get("dateTime") or end.get("date"),
        "location":    location_field or "",
        "description": description or "",
        "color_id":    color_id or "",
        "all_day":     all_day,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    content_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

    private_props = {
        "odoo_event_id": str(ev["id"]),
        "odoo_lead_id":  str(lead["id"]) if lead else "",
        "synced_by":     SYNCED_BY,
        "content_hash":  content_hash,
        # first_synced / last_synced filled in by caller
    }

    body = {
        "summary":     title,
        "start":       start,
        "end":         end,
        "location":    location_field,
        "description": description,
        "extendedProperties": {"private": private_props},
    }
    if color_id:
        body["colorId"] = color_id
    return body, content_hash, location_type

# ---------------------------------------------------------------------------
# Calendar API low-level helpers
# ---------------------------------------------------------------------------

def cal_api():
    return calendar_helper().CalendarAPI(user=TOM_EMAIL)

def list_events_in_window(cal, time_min_utc, time_max_utc, private_extended=None):
    """Direct call to support privateExtendedProperty filter."""
    query = {
        "timeMin": time_min_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "timeMax": time_max_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "maxResults": 250,
        "singleEvents": "true",
        "orderBy": "startTime",
        "showDeleted": "false",
    }
    if private_extended:
        # privateExtendedProperty=key=value, can repeat
        query["privateExtendedProperty"] = private_extended
    items = []
    page_token = None
    while True:
        if page_token:
            query["pageToken"] = page_token
        resp = cal._call("GET", f"/calendars/{TOM_CAL_ID}/events", query=query)
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items

def list_events_with_marker(cal, time_min_utc, time_max_utc, marker_key=ODOO_DB_KEY):
    """List events with sharedExtendedProperty marker (used for first-run wipe)."""
    query = {
        "timeMin": time_min_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "timeMax": time_max_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "maxResults": 250,
        "singleEvents": "true",
        "orderBy": "startTime",
        "showDeleted": "false",
    }
    items = []
    page_token = None
    while True:
        if page_token:
            query["pageToken"] = page_token
        resp = cal._call("GET", f"/calendars/{TOM_CAL_ID}/events", query=query)
        for it in resp.get("items", []):
            shared = (it.get("extendedProperties") or {}).get("shared") or {}
            if marker_key in shared:
                items.append(it)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items

# ---------------------------------------------------------------------------
# Sync runner
# ---------------------------------------------------------------------------

class SyncRunner:
    def __init__(self, dry_run=False, first_run=False):
        self.dry_run = dry_run
        self.first_run = first_run
        self.log = Log(dry_run=dry_run)
        self.summary = {"created": 0, "patched": 0, "deleted": 0, "skipped_unchanged": 0,
                        "errors": [], "wiped_built_in": 0,
                        "maps_links": {"ROOFTOP": 0, "RANGE_INTERPOLATED": 0,
                                       "GEOMETRIC_CENTER": 0, "APPROXIMATE": 0,
                                       "skipped": 0, "no-lead": 0, "none": 0}}
        self.geocode_cache = load_geocode_cache()
        self.cal = cal_api()

    def run(self):
        try:
            self.log("INFO", "run-start", dry_run=self.dry_run, first_run=self.first_run)

            # Today's midnight Atlantic/Canary, expressed in UTC. This is the floor for
            # the narrow window (Odoo query + orphan-cleanup scope). The wide window used
            # for finding existing GCal records is built off this same floor for consistency.
            today_midnight_canary = dt.datetime.combine(today_canary(), dt.time(0, 0), tzinfo=CANARY)
            self.today_midnight_canary = today_midnight_canary  # stash for orphan-cleanup check
            window_start_utc = (today_midnight_canary - dt.timedelta(days=WINDOW_BACK_DAYS)).astimezone(UTC)
            window_end_utc   = (today_midnight_canary + dt.timedelta(days=WINDOW_FORWARD_DAYS)).astimezone(UTC)
            # Wide window (scan only) extends 365 days into the past so we can locate
            # existing GCal records by odoo_event_id wherever they currently sit in time.
            scan_start_utc = (today_midnight_canary - dt.timedelta(days=SCAN_BACK_DAYS)).astimezone(UTC)

            # Phase 0: First-run only -- disable Odoo built-in sync + wipe its leftovers
            if self.first_run:
                self._first_run_phase_0(window_start_utc, window_end_utc)

            # Pull Odoo events
            domain = [
                ["partner_ids", "in", [TOM_PARTNER_ID]],
                ["start", ">=", utc_iso(today_canary() - dt.timedelta(days=WINDOW_BACK_DAYS))],
                ["start", "<=", utc_iso(today_canary() + dt.timedelta(days=WINDOW_FORWARD_DAYS))],
            ]
            events = odoo_search_read("calendar.event", domain,
                                      ["id", "name", "start", "stop", "location",
                                       "description", "opportunity_id", "duration",
                                       "allday", "active"], limit=500)
            events = [e for e in events if e.get("active") is not False]
            self.log("INFO", "odoo-events-pulled", count=len(events))

            # Resolve leads
            lead_ids = list({e["opportunity_id"][0] for e in events if e.get("opportunity_id")})
            leads_list = odoo_read("crm.lead", lead_ids,
                                   ["id", "name", "partner_id", "partner_name",
                                    "contact_name", "phone",
                                    "x_studio_char_field_3qWjM",  # Location (Survey)
                                    "stage_id", "description"])
            leads = {l["id"]: l for l in leads_list}
            self.log("INFO", "leads-resolved", count=len(leads))

            # Pull existing managed Google events using the WIDE window (365d back) so we can
            # find a managed record by odoo_event_id wherever it lives in time. Orphan cleanup
            # later in this run is scoped back to the narrow window (start >= today).
            # Defensive: build dict-of-lists keyed by odoo_event_id. If a cluster has >1 event,
            # the in-cluster dedup in _sync_event() keeps the oldest first_synced and deletes
            # the rest.
            existing_managed = list_events_in_window(
                self.cal, scan_start_utc, window_end_utc,
                private_extended=f"synced_by={SYNCED_BY}",
            )
            existing_by_odoo_id = {}
            for ev in existing_managed:
                priv = (ev.get("extendedProperties") or {}).get("private") or {}
                oid = priv.get("odoo_event_id")
                if oid:
                    existing_by_odoo_id.setdefault(oid, []).append(ev)
            # Sort each cluster oldest-first by first_synced (so [0] is the keeper)
            for oid, lst in existing_by_odoo_id.items():
                lst.sort(key=lambda e: ((e.get("extendedProperties") or {}).get("private") or {}).get("first_synced") or "9999")
            cluster_sizes = [len(lst) for lst in existing_by_odoo_id.values()]
            n_clusters = len(existing_by_odoo_id)
            n_dupes = sum(s - 1 for s in cluster_sizes if s > 1)
            self.log("INFO", "existing-managed-fetched",
                     count=n_clusters, total_events=sum(cluster_sizes), dupe_events=n_dupes)

            # Process each Odoo event
            for ev in events:
                try:
                    self._sync_event(ev, leads, existing_by_odoo_id)
                except Exception as e:
                    self.summary["errors"].append({"event_id": ev.get("id"), "err": str(e)[:200]})
                    self.log("ERROR", "event-sync-failed", id=ev.get("id"), err=str(e)[:200])

            # Orphan cleanup. Managed events with no current Odoo source AND start >= today
            # midnight Canary get deleted (Odoo source was cancelled/deleted). Past events
            # are frozen — never orphan-deleted, even if Odoo no longer has them.
            for orphan_oid, orphan_cluster in existing_by_odoo_id.items():
                for orphan_event in orphan_cluster:
                    if self._event_start_in_narrow_window(orphan_event):
                        self._delete_orphan(orphan_event, orphan_oid)
                    else:
                        self.log("INFO", "past-event-frozen-skip-orphan",
                                 odoo_id=orphan_oid, google_id=orphan_event.get("id"),
                                 start=(orphan_event.get("start") or {}).get("dateTime")
                                       or (orphan_event.get("start") or {}).get("date"))

            save_geocode_cache(self.geocode_cache)
            self._log_summary()

            if self.first_run:
                self._send_first_run_email()

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.log("FATAL", "fatal", err=str(e)[:300])
            self._send_error_email(str(e), tb)
            raise
        finally:
            self.log.close()

    def _first_run_phase_0(self, window_start_utc, window_end_utc):
        self.log("INFO", "phase-0-start")
        # 1. Disable Odoo's built-in sync programmatically
        try:
            if not self.dry_run:
                odoo_write("res.users", TOM_USER_ID,
                           {"google_calendar_token": False,
                            "google_calendar_rtoken": False,
                            "google_calendar_sync_token": False})
            self.log("INFO", "odoo-builtin-sync-disabled")
        except Exception as e:
            self.log("WARN", "odoo-builtin-sync-disable-failed", err=str(e)[:200])

        # 2. List events with built-in sync's marker in window
        candidates = list_events_with_marker(
            self.cal, window_start_utc, window_end_utc, ODOO_DB_KEY)
        self.log("INFO", "phase-0-marker-events-found", count=len(candidates))

        if len(candidates) > WIPE_SAFETY_CAP:
            raise RuntimeError(
                f"Phase 0 wipe candidates ({len(candidates)}) exceed safety cap "
                f"({WIPE_SAFETY_CAP}). Aborting run."
            )

        # 3. Delete each candidate
        for ev in candidates:
            try:
                if not self.dry_run:
                    self.cal.delete_event(ev["id"], calendar_id=TOM_CAL_ID)
                self.summary["wiped_built_in"] += 1
                self.log("INFO", "phase-0-wiped",
                         id=ev["id"], summary=(ev.get("summary") or "")[:60])
            except Exception as e:
                self.log("WARN", "phase-0-wipe-failed", id=ev.get("id"),
                         err=str(e)[:200])
        self.log("INFO", "phase-0-done", wiped=self.summary["wiped_built_in"])

    def _sync_event(self, ev, leads, existing_by_odoo_id):
        lead = None
        opp = ev.get("opportunity_id")
        if opp:
            lead = leads.get(opp[0])

        body, content_hash, location_type = build_payload(ev, lead, self.geocode_cache)

        # Update maps_links summary
        key = location_type or ("no-lead" if not lead else "none")
        self.summary["maps_links"][key] = self.summary["maps_links"].get(key, 0) + 1

        ev_id_str = str(ev["id"])
        cluster = existing_by_odoo_id.pop(ev_id_str, None)
        # In-cluster dedup: keep the oldest first_synced, delete the rest right here.
        if cluster and len(cluster) > 1:
            keeper = cluster[0]  # already sorted oldest-first
            extras = cluster[1:]
            for extra in extras:
                try:
                    if not self.dry_run:
                        self.cal.delete_event(extra["id"], calendar_id=TOM_CAL_ID)
                    self.summary["deleted"] += 1
                    self.log("INFO", "dedup-cleanup", odoo_id=ev_id_str,
                             kept_google_id=keeper["id"], deleted_google_id=extra["id"],
                             title=(extra.get("summary") or "")[:60])
                except Exception as e:
                    self.log("WARN", "dedup-cleanup-failed", odoo_id=ev_id_str,
                             google_id=extra.get("id"), err=str(e)[:200])
            existing = keeper
        elif cluster:
            existing = cluster[0]
        else:
            existing = None
        if existing:
            existing_priv = (existing.get("extendedProperties") or {}).get("private") or {}
            existing_hash = existing_priv.get("content_hash")
            if existing_hash == content_hash:
                self.summary["skipped_unchanged"] += 1
                return
            # PATCH
            body["extendedProperties"]["private"]["first_synced"] = (
                existing_priv.get("first_synced") or now_iso_utc()
            )
            body["extendedProperties"]["private"]["last_synced"] = now_iso_utc()
            if not self.dry_run:
                self.cal.update_event(existing["id"], calendar_id=TOM_CAL_ID, **body)
            self.summary["patched"] += 1
            self.log("INFO", "event-patched", odoo_id=ev_id_str,
                     google_id=existing["id"], title=body["summary"][:60])
        else:
            # CREATE
            body["extendedProperties"]["private"]["first_synced"] = now_iso_utc()
            body["extendedProperties"]["private"]["last_synced"] = now_iso_utc()
            if not self.dry_run:
                created = self.cal.create_event(calendar_id=TOM_CAL_ID, event=body)
                gid = created.get("id", "?")
            else:
                gid = "DRY_RUN"
            self.summary["created"] += 1
            self.log("INFO", "event-created", odoo_id=ev_id_str,
                     google_id=gid, title=body["summary"][:60])

    def _event_start_in_narrow_window(self, ev):
        """True iff the GCal event's start is on or after today midnight Canary.
        Used to decide whether an orphaned managed event should be deleted (yes if
        future/today) or frozen (no if past). Handles both date-only (all-day) and
        dateTime formats."""
        start = ev.get("start") or {}
        s = start.get("dateTime") or start.get("date")
        if not s:
            return False
        try:
            if "T" not in s:
                # All-day event
                ev_start = dt.datetime.combine(dt.date.fromisoformat(s), dt.time(0, 0), tzinfo=CANARY)
            else:
                ev_start = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return False
        return ev_start >= self.today_midnight_canary

    def _delete_orphan(self, orphan_event, orphan_oid):
        try:
            if not self.dry_run:
                self.cal.delete_event(orphan_event["id"], calendar_id=TOM_CAL_ID)
            self.summary["deleted"] += 1
            self.log("INFO", "event-deleted-orphan", odoo_id=orphan_oid,
                     google_id=orphan_event["id"],
                     title=(orphan_event.get("summary") or "")[:60])
        except Exception as e:
            self.log("WARN", "orphan-delete-failed", odoo_id=orphan_oid,
                     err=str(e)[:200])

    def _log_summary(self):
        self.log("INFO", "run-summary",
                 created=self.summary["created"],
                 patched=self.summary["patched"],
                 deleted=self.summary["deleted"],
                 skipped_unchanged=self.summary["skipped_unchanged"],
                 wiped=self.summary["wiped_built_in"],
                 maps_links=self.summary["maps_links"],
                 errors=len(self.summary["errors"]))

    def _send_first_run_email(self):
        try:
            g = gmail_helper().GmailAPI()
            body = f"""Tom Jobs Calendar Sync — first-run report

Wiped Odoo built-in sync events: {self.summary['wiped_built_in']}
Created (new from Odoo): {self.summary['created']}
Patched (updated): {self.summary['patched']}
Deleted (orphans): {self.summary['deleted']}
Errors: {len(self.summary['errors'])}

Maps links: {json.dumps(self.summary['maps_links'])}

Logs: {self.log.path}
"""
            if not self.dry_run:
                g.send(to=", ".join(FIRST_RUN_NOTIFY_TO),
                       subject="Tom Jobs Calendar Sync — first-run complete",
                       body=body)
        except Exception as e:
            self.log("WARN", "first-run-email-failed", err=str(e)[:200])

    def _send_error_email(self, err_msg, traceback_str):
        try:
            g = gmail_helper().GmailAPI()
            body = f"""Tom Jobs Calendar Sync — fatal error

Error: {err_msg}

Traceback:
{traceback_str[:3000]}

Logs: {self.log.path}
"""
            if not self.dry_run:
                g.send(to=", ".join(ERROR_NOTIFY_TO),
                       subject="Tom Jobs Calendar Sync — FATAL",
                       body=body)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run",   action="store_true",
                        help="Plan only; no Calendar writes")
    parser.add_argument("--first-run", action="store_true",
                        help="Disable Odoo built-in sync + wipe its leftovers BEFORE the standard sync")
    args = parser.parse_args()

    runner = SyncRunner(dry_run=args.dry_run, first_run=args.first_run)
    runner.run()

if __name__ == "__main__":
    main()
