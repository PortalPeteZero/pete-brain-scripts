#!/usr/bin/env python3
"""
cd-tom-jobs-photo-sort.py -- daily photo-sort scheduled task.

Spec:    Library/processes/tom-jobs-photo-workflow.md
Pair:    cd-tom-jobs-calendar-sync.py
Cron:    0 18 * * * Atlantic/Canary (daily 18:00 local)

Walks Drive: Pictures/tom/, audits managed folders, creates new ones from
Odoo events, sorts photos into matching job folders by GPS+EXIF date with
Cloud Vision fallback, colours folders, sweeps stale empties, and writes
tom/_MAP.md.

Usage:
  python3 cd-tom-jobs-photo-sort.py             # normal incremental run
  python3 cd-tom-jobs-photo-sort.py --first-run # Phase 0 adoption + cleanup
  python3 cd-tom-jobs-photo-sort.py --dry-run   # plan only, no Drive writes

Reads Odoo (calendar.event + crm.lead), writes Drive (folders + appProperties
+ moves), reads/writes Google Vision, reads geocoding-api.py cache, writes
gmail (unmapped notification email).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import importlib.util
import json
import math
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
# Constants (locked -- mirror the spec doc)
# ---------------------------------------------------------------------------

# CRON-META
# what: CD Tom-jobs photo sort — walks Drive Pictures/tom/, sorts photos into job folders by GPS+EXIF (Vision fallback), syncs folders from Odoo, writes _MAP.md
# why: keeps Tom's field-job photos organised per job (Drive) + flips Odoo photos-uploaded flags; daily ops hygiene for CD field work
# reads: Odoo (calendar.event + crm.lead); Google Drive (API); Google Vision; geocoding cache
# writes: Drive (folders + appProperties + photo moves + _MAP.md); Odoo (x_studio_photos_uploaded); gmail (unmapped notice)
# entity: canary-detect
# schedule: 0 18 * * *
# timezone: Atlantic/Canary
# CRON-META-END

SCRIPTS_DIR = Path(__file__).parent.resolve()
SECRETS_DIR = (Path(os.environ["VAULT"]) / "Library/processes/secrets") if os.environ.get("VAULT") else (SCRIPTS_DIR.parent / "secrets")
LOGS_DIR = SCRIPTS_DIR / "_logs"
CACHE_DIR = SCRIPTS_DIR / "_cache"

TOM_FOLDER_ID  = "1M4rFm3QQ-z0y1hi7Jp03s5M_qC19H0g7"
TOM_PARTNER_ID = 12
TOM_USER_ID    = 10
TOM_EMAIL      = "tom.robertson@canary-detect.com"
ORANGE         = "#ff7537"
GREY           = "#8f8f8f"
ODOO_BASE      = "https://camello-blanco-sl.odoo.com"
NOTIFY_TO      = ["pete.ashcroft@sygma-solutions.com",
                  "tom.robertson@canary-detect.com",
                  "nicola.brown@canary-detect.com"]
ERROR_NOTIFY_TO = ["pete.ashcroft@sygma-solutions.com"]

CLOSED_STAGE_IDS = {12, 13, 14, 18, 22}  # Invoiced / Invoice Paid / CD - Archive / Won / ECO Finish Won
ACTIVE_STAGE_IDS = {19, 1, 6, 7, 8, 9, 10, 11, 23, 24, 15}

GPS_NEAREST_MAX_M    = 1000  # was 250; Google geocodes addresses to street-level
                              # which can sit 200-500m from the actual property in
                              # Lanzarote villa clusters / urbanisations
GPS_NEAREST_MAX_M_APPROX = 3000  # When the lead's address geocodes to APPROXIMATE
                                  # precision (Google's signal that it resolved to
                                  # a town centre / area, not a street), use a
                                  # wider radius. Catches photos taken at villas
                                  # whose lead has only "PB" or "Tias" as the
                                  # site address. Added 2026-05-04 -- estimated
                                  # ~50-100 unmapped photos may now match.
GPS_RATIO_MULTIPLIER = 3      # was 5; nearest must be 3x closer than next-nearest
GPS_FAR_FALLBACK_M   = 2000   # was 800; when only one candidate is closer than this,
                              # accept it even without a tight ratio

# Two-pass date tolerance:
# Round 1 — tight: catch the confident same-day matches first (no risk of
#   misattribution to a different visit week).
# Round 2 — wide: for photos still in _unmapped after round 1, retry with a
#   broader date window. Picks up follow-up visits to the same property within
#   ~2 weeks. GPS distance constraint (1000m) still applies, so a photo only
#   matches a property it's genuinely near.
GPS_DATE_TOLERANCE_DAYS_TIGHT = 1
GPS_DATE_TOLERANCE_DAYS_WIDE  = 14
SWEEP_DAYS           = 28
WINDOW_BACK_DAYS     = 60
WINDOW_FORWARD_DAYS  = 14
LOG_RETENTION_DAYS   = 90

CANARY = ZoneInfo("Atlantic/Canary")
UTC = dt.timezone.utc

MANAGED_BY = "cd-tom-jobs-photo-sort"
UNMAPPED_NAME = "_photos to sort (no GPS)"  # historical name, keep
MAP_MD_NAME = "_MAP.md"

# File extensions the script treats as photos. Anything else (markdown, .DS_Store,
# README.md, _MAP.md etc) is left where it is. Keeping this allow-list explicit
# so we never accidentally sweep a documentation / system file into _unmapped/.
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif",
               ".tif", ".tiff", ".webp", ".bmp", ".dng", ".raw",
               ".mov", ".mp4", ".m4v", ".3gp", ".avi"}

def _is_photo_file(name):
    if not name or name.startswith("_") or name.startswith("."):
        return False
    if name.lower() in {"readme.md", "readme.txt"}:
        return False
    n = name.lower()
    for ext in _PHOTO_EXTS:
        if n.endswith(ext):
            return True
    return False

# Folder-worthy classification rules (spec-locked)
FOLDER_WORTHY = {"Community Survey", "VLS", "PLS", "Drain Survey",
                 "Repair", "Reinstatement", "LeakGuard Install"}

JOBTYPE_RULES = [
    (re.compile(r'\bvls\b', re.I), 'VLS'),
    (re.compile(r'\bpls\b', re.I), 'PLS'),
    (re.compile(r'\bcommunity\s+survey\b', re.I), 'Community Survey'),
    (re.compile(r'\bcommunity\b', re.I), 'Community Survey'),
    (re.compile(r'\bdrain\s+survey\b', re.I), 'Drain Survey'),
    (re.compile(r'\bleakguard\s+install\b', re.I), 'LeakGuard Install'),
    (re.compile(r'\bcheck\s+leakguard\b', re.I), 'LeakGuard Check'),  # skip-type
    (re.compile(r'\binitial\s+visit\b', re.I), 'Initial Visit'),  # skip-type
    (re.compile(r'\breinstate', re.I), 'Reinstatement'),
    (re.compile(r'\brepair\b', re.I), 'Repair'),
    (re.compile(r'\bquote\b', re.I), 'Repair'),
    (re.compile(r'\bcapacitor\b', re.I), 'Repair'),
    (re.compile(r'\becofinish\b', re.I), 'EcoFinish'),  # skip-type
    (re.compile(r'\bepoxy\b', re.I), 'Epoxy'),  # skip-type
    (re.compile(r'\bdrain\b', re.I), 'Drain Survey'),
    (re.compile(r'\bdishwasher\b', re.I), 'Repair'),
    (re.compile(r'\bvac\s+line\b', re.I), 'Repair'),
    (re.compile(r'\btemp\s+fix\b', re.I), 'Repair'),
    (re.compile(r'\bfaulty\s+pump\b', re.I), 'Repair'),
    (re.compile(r'\bclear\s+rubbish\b', re.I), 'Site Clear'),  # skip
    (re.compile(r'\bconcreting\b', re.I), 'Civils'),  # skip
    (re.compile(r'\bhoover\s+lawn\b', re.I), 'Civils'),  # skip
    (re.compile(r'\bbury\s+pipework\b', re.I), 'Civils'),  # skip
    (re.compile(r'\binstall\s+the\s+pool\s+light\b', re.I), 'Civils'),  # skip
    (re.compile(r'\bdomestic\s+pump\b', re.I), 'Pump'),  # skip
    (re.compile(r'\bcollect\s+payment\b', re.I), 'Admin'),  # skip
    (re.compile(r'\bbreak\s+out\s+behind\b', re.I), 'Repair'),
    (re.compile(r'\b(locate ?/ ?repair|find\s+leak|leak\s+(in\s+front|outside))\b', re.I), 'Repair'),
    (re.compile(r'\bmove\s+domestic\s+pump\b', re.I), 'Pump'),  # skip
    (re.compile(r'\bnew\s+vls\b', re.I), 'VLS'),
]

SKIP_PATTERNS = [
    re.compile(p, re.I) for p in [
        r'\bkeep\s+clear\b', r'^fiesta\b', r'^itv\b',
        r'\binvoiced\b', r'^reminder\b', r'\bnicola\s+holiday\b',
        r'\bensure\s+.*loaded\b', r'\bplanning\s+this\s+week\b',
        r'\bensure\s+jose',
    ]
]

# ---------------------------------------------------------------------------
# Lazy import of helper modules
# ---------------------------------------------------------------------------

def _load_helper(name):
    """Load a hyphenated script as a module."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"),
                                                  str(SCRIPTS_DIR / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_drive = None
_geocoding = None
_gmail = None

def drive_helper():
    global _drive
    if _drive is None:
        _drive = _load_helper("drive-api")
    return _drive

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
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOGS_DIR / f"cd-tom-jobs-photo-sort-{dt.datetime.now(CANARY).date().isoformat()}.log"
        self.fh = open(self.path, "a", encoding="utf-8")
        self.dry_run = dry_run

    def __call__(self, level, action, **fields):
        ts = dt.datetime.now(CANARY).isoformat(timespec="seconds")
        bits = " ".join(f"{k}={v!r}" for k, v in fields.items())
        line = f"[{ts}] {level:<5} {action:<25} {bits}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self):
        self.fh.close()

    def cleanup_old_logs(self):
        cutoff = dt.datetime.now(CANARY) - dt.timedelta(days=LOG_RETENTION_DAYS)
        for p in LOGS_DIR.glob("cd-tom-jobs-photo-sort-*.log"):
            try:
                m = re.match(r".*-(\d{4}-\d{2}-\d{2})\.log$", p.name)
                if m and dt.datetime.fromisoformat(m.group(1)).replace(tzinfo=CANARY) < cutoff:
                    p.unlink()
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Odoo helpers
# ---------------------------------------------------------------------------

def odoo(method, *args):
    """Run odoo-api.py as a subprocess with a specific command."""
    res = subprocess.run(["python3", str(SCRIPTS_DIR / "odoo-api.py"), method, *args],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Odoo {method} failed: {res.stderr[:300]}")
    if not res.stdout.strip():
        return None
    return json.loads(res.stdout)

def odoo_search_read(model, domain, fields, limit=500):
    return odoo("search-read", model, json.dumps(domain), ",".join(fields),
                "--limit", str(limit))

def odoo_read(model, ids, fields):
    if not ids:
        return []
    if isinstance(ids, list):
        ids = ",".join(str(i) for i in ids)
    return odoo("read", model, str(ids), ",".join(fields))

def odoo_write(model, record_id, values):
    """Like odoo() but tolerates non-JSON stdout (Odoo write returns 'True')."""
    res = subprocess.run(
        ["python3", str(SCRIPTS_DIR / "odoo-api.py"),
         "write", model, str(record_id), json.dumps(values)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Odoo write failed: {res.stderr[:300]}")
    out = res.stdout.strip()
    # Odoo write returns the Python literal "True" / "False" -- treat
    # success as non-empty stdout that isn't an error trace.
    if out in ("True", "true"):
        return True
    if out in ("False", "false"):
        return False
    # Anything else: try JSON parse for forward-compat, else assume success
    if out.startswith(("[", "{")):
        return json.loads(out)
    return out or True

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def today_canary():
    return dt.datetime.now(CANARY).date()

def utc_iso(d_or_dt):
    """Convert date or datetime to UTC ISO string for Odoo."""
    if isinstance(d_or_dt, dt.date) and not isinstance(d_or_dt, dt.datetime):
        d_or_dt = dt.datetime.combine(d_or_dt, dt.time(0, 0), tzinfo=CANARY)
    return d_or_dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

def parse_odoo_dt(s):
    """Odoo datetimes are naive UTC. Return aware UTC datetime."""
    if not s:
        return None
    if "T" in s:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)

def event_canary_date(ev):
    return parse_odoo_dt(ev["start"]).astimezone(CANARY).date()

def now_iso_canary():
    return dt.datetime.now(CANARY).isoformat(timespec="seconds")

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_jobtype(ev):
    """Pick the *current* jobtype from the event title.

    Rule: rightmost match wins. Supports Nicola's title-accumulation convention
    ([[cd-calendar-event-naming-convention]]) where one CRM lead spawns a chain
    of events whose titles accumulate the previous jobtypes:

        "VLS - Customer"                           -> VLS
        "VLS - Repair - Customer"                  -> Repair
        "VLS - Repair - Reinstatement - Customer"  -> Reinstatement

    For events with a single jobtype keyword in the title (the historical
    pattern before the convention), behaviour is identical to first-match.
    """
    name = ev.get("name", "")
    for pat in SKIP_PATTERNS:
        if pat.search(name):
            return None  # skip-types in spec sense
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

# ---------------------------------------------------------------------------
# Folder name builder (locked from spec)
# ---------------------------------------------------------------------------

LOCATION_ABBREVS = ["PB", "PDC", "PDR", "CT", "Tias", "Tías", "Arrecife", "Haria",
                    "Tahiche", "Conil", "Nazaret", "Muñique", "Munique",
                    "Playa Honda", "Puerto Calero", "Puerto del Carmen", "Playa Blanca",
                    "Costa Teguise"]

def extract_location_abbrev(events):
    """Find the location abbreviation Nicola used on the event title.
    Use the first folder-worthy event's title; fall back to lead.city."""
    for ev in events:
        name = ev.get("name", "")
        # Look for location embedded between dashes
        parts = [p.strip() for p in re.sub(r'\([^)]*\)', '', name).split(' - ')]
        # Common pattern: "Jobtype - Location - Customer"
        for p in parts[1:-1]:  # skip first (jobtype) and last (customer)
            for abbr in LOCATION_ABBREVS:
                if re.search(rf'\b{re.escape(abbr)}\b', p):
                    return abbr
        # Fallback: any abbreviation anywhere in title
        for abbr in LOCATION_ABBREVS:
            if re.search(rf'\b{re.escape(abbr)}\b', name):
                return abbr
    return ""

def build_folder_name(lead, folder_worthy_events):
    """Build the canonical folder name per spec."""
    # Determine jobtypes (deduplicated, first-occurrence order)
    seen = set()
    jobtypes = []
    for ev in folder_worthy_events:
        jt = classify_jobtype(ev)
        if jt and jt not in seen:
            seen.add(jt)
            jobtypes.append(jt)

    # Reinstatement absorption: if Repair AND Reinstatement both present, drop Reinstatement
    has_repair = "Repair" in jobtypes
    if has_repair and "Reinstatement" in jobtypes:
        jobtypes = [j for j in jobtypes if j != "Reinstatement"]

    title_jobtypes = " + ".join(jobtypes)

    # Customer + contact
    partner_name = (lead.get("partner_id") or [None, ""])[1] if lead.get("partner_id") else ""
    if not partner_name:
        partner_name = lead.get("partner_name") or ""
    contact_name = lead.get("contact_name") or ""
    customer_str = partner_name
    if contact_name and contact_name.strip().lower() != partner_name.strip().lower():
        customer_str = f"{partner_name} ({contact_name})"

    # Location -- prefer the abbreviation parsed from event titles ("PB", "Tias",
    # etc.); else fall back to the lead's Location (Survey) Studio field.
    # The structured `city` field is the invoice city and is no longer used.
    location = extract_location_abbrev(folder_worthy_events)
    if not location:
        location = (lead.get("x_studio_char_field_3qWjM") or "").strip()
        if location == "False":
            location = ""

    # Dates: chronological, Canary-local, Reinstatement dates absorbed if Repair exists
    dates = []
    for ev in sorted(folder_worthy_events, key=lambda e: e["start"]):
        jt = classify_jobtype(ev)
        if has_repair and jt == "Reinstatement":
            continue  # absorb reinstatement date
        dates.append(event_canary_date(ev).isoformat())

    name = title_jobtypes
    if customer_str:
        name += f" - {customer_str}"
    if location:
        name += f" - {location}"
    if dates:
        name += " - " + ", ".join(dates)

    # Sanitise: Drive doesn't allow / in names
    name = name.replace("/", "-")
    return name

# ---------------------------------------------------------------------------
# Drive helpers (raw API for the bits drive-api.py doesn't cover)
# ---------------------------------------------------------------------------

def drive_call(method, path, **kwargs):
    d = drive_helper()
    return d.api(method, path, **kwargs)

def drive_get(file_id, fields=None):
    params = {"supportsAllDrives": "true"}
    if fields:
        params["fields"] = fields
    return drive_call("GET", f"/files/{file_id}", params=params)

def drive_patch(file_id, body):
    return drive_call("PATCH", f"/files/{file_id}", params={"supportsAllDrives": "true"}, body=body)

def drive_create_folder(name, parent_id, app_properties=None):
    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    if app_properties:
        body["appProperties"] = app_properties
    return drive_call("POST", "/files",
                      params={"supportsAllDrives": "true"},
                      body=body)

def drive_list_in_folder(folder_id, mime_filter=None, page_size=1000):
    """List immediate children of a folder, paginated."""
    files = []
    token = None
    while True:
        q = f"'{folder_id}' in parents and trashed=false"
        if mime_filter == "folder":
            q += " and mimeType='application/vnd.google-apps.folder'"
        elif mime_filter == "non-folder":
            q += " and mimeType != 'application/vnd.google-apps.folder'"
        params = {
            "q": q, "pageSize": str(page_size),
            "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,createdTime,parents,folderColorRgb,appProperties,imageMediaMetadata)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
        }
        if token:
            params["pageToken"] = token
        resp = drive_call("GET", "/files", params=params)
        files.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return files

def drive_find_managed_folders(trashed=False):
    """All folders managed by this workflow."""
    files = []
    token = None
    while True:
        q = (f"trashed={str(trashed).lower()} "
             f"and mimeType='application/vnd.google-apps.folder' "
             f"and appProperties has {{ key='managed_by' and value='{MANAGED_BY}' }}")
        params = {
            "q": q, "pageSize": "1000",
            "fields": "nextPageToken,files(id,name,parents,folderColorRgb,appProperties,trashed)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
        }
        if token:
            params["pageToken"] = token
        resp = drive_call("GET", "/files", params=params)
        files.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return files

def drive_find_by_lead(lead_id, trashed=False):
    """Find managed folder for a specific lead."""
    q = (f"trashed={str(trashed).lower()} "
         f"and mimeType='application/vnd.google-apps.folder' "
         f"and appProperties has {{ key='managed_by' and value='{MANAGED_BY}' }} "
         f"and appProperties has {{ key='lead_id' and value='{lead_id}' }}")
    params = {
        "q": q, "pageSize": "5",
        "fields": "files(id,name,parents,folderColorRgb,appProperties,trashed)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "corpora": "allDrives",
    }
    resp = drive_call("GET", "/files", params=params)
    files = resp.get("files", [])
    return files[0] if files else None

def drive_trash(file_id):
    return drive_patch(file_id, {"trashed": True})

def drive_set_color(file_id, hex_color):
    return drive_patch(file_id, {"folderColorRgb": hex_color})

def drive_rename(file_id, new_name):
    return drive_patch(file_id, {"name": new_name})

def drive_move(file_id, new_parent_id, current_parents=None):
    """Move by removing old parents, adding new parent."""
    params = {"supportsAllDrives": "true", "addParents": new_parent_id}
    if current_parents:
        params["removeParents"] = ",".join(current_parents)
    return drive_call("PATCH", f"/files/{file_id}", params=params, body={})

def name_signature(name):
    """Return a short signature for a folder name suitable for last_known_name.
    For short names returns the name verbatim; for long names returns 'h:<sha1-12>'."""
    if not name:
        return ""
    if len(name.encode("utf-8")) <= 100:
        return name
    import hashlib
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    return f"h:{h}"

def name_matches_signature(name, sig):
    """True if the current folder name still matches a stored last_known_name signature."""
    if not sig:
        return False
    if sig.startswith("h:"):
        import hashlib
        return hashlib.sha1(name.encode("utf-8")).hexdigest()[:12] == sig[2:]
    return name == sig

def _trim_to_bytes(value, max_bytes):
    """Trim a string so its UTF-8 byte length is <= max_bytes."""
    if not isinstance(value, str):
        return value
    b = value.encode("utf-8")
    if len(b) <= max_bytes:
        return value
    # Truncate; back off until valid UTF-8
    truncated = b[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""

def _safe_app_property_value(key, value):
    """Drive appProperties limit is 124 bytes for key+value combined.
    Trim value so key+value <= 120 bytes (safety margin)."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    key_bytes = len(key.encode("utf-8"))
    max_value_bytes = 124 - key_bytes - 4  # 4-byte safety margin
    if max_value_bytes <= 0:
        return ""
    return _trim_to_bytes(value, max_value_bytes)

def drive_set_app_properties(file_id, props_dict):
    """Update appProperties. None values DELETE the key. Auto-trims values to 124-byte limit."""
    safe = {k: _safe_app_property_value(k, v) if v is not None else None
            for k, v in props_dict.items()}
    return drive_patch(file_id, {"appProperties": safe})

# ---------------------------------------------------------------------------
# do_not_recreate_leads (chunked appProperties on tom/ folder)
# ---------------------------------------------------------------------------

def read_do_not_recreate_set():
    tom = drive_get(TOM_FOLDER_ID, fields="appProperties")
    props = tom.get("appProperties") or {}
    parts = []
    for key in sorted(k for k in props if k.startswith("do_not_recreate_leads_")):
        parts.append(props[key])
    if not parts:
        return set()
    csv = ",".join(parts)
    return set(s.strip() for s in csv.split(",") if s.strip())

def write_do_not_recreate_set(lead_set):
    """Persist as chunked appProperty keys on tom/.

    The chunk size MUST match what `_safe_app_property_value()` will accept after
    trimming. Drive's hard limit is 124 bytes for key+value combined; the safe
    trimmer uses `124 - len(key) - 4` (4-byte margin). For our key pattern
    `do_not_recreate_leads_{N}` the key length is 22-23 bytes (depending on N),
    giving a value budget of ~97-98 bytes.

    Pre 2026-05-25 this chunker packed up to 110 chars per chunk, the trimmer
    then silently chopped 12+ characters off the end on every write, losing
    1-3 lead_ids per persist. That's how today's create-and-immediately-sweep
    leads (2125, 2127) kept never landing in the persisted set — they got
    chopped off the end alphabetically. Patched 2026-05-25 to use a per-chunk
    budget that's safe for keys up to do_not_recreate_leads_99 (key=24 bytes).
    """
    ids = sorted(str(x) for x in lead_set)
    # Safe budget per chunk value, given the longest key we expect to use.
    # do_not_recreate_leads_99 = 24 bytes. Budget = 124 - 24 - 4 = 96.
    # Use 96 as the universal per-chunk byte budget; works for all chunk
    # indices 1..99.
    MAX_BYTES = 96

    chunks = []
    cur = []
    cur_bytes = 0
    for lid in ids:
        added_bytes = len(lid.encode("utf-8")) + (1 if cur else 0)  # +1 for comma
        if cur_bytes + added_bytes > MAX_BYTES:
            chunks.append(",".join(cur))
            cur = [lid]
            cur_bytes = len(lid.encode("utf-8"))
        else:
            cur.append(lid)
            cur_bytes += added_bytes
    if cur:
        chunks.append(",".join(cur))

    # Read current chunked keys to know which to delete
    tom = drive_get(TOM_FOLDER_ID, fields="appProperties")
    existing_keys = [k for k in (tom.get("appProperties") or {})
                     if k.startswith("do_not_recreate_leads_")]

    new_props = {}
    for i, c in enumerate(chunks, 1):
        new_props[f"do_not_recreate_leads_{i}"] = c
    # Delete keys from previous run that are now empty
    for k in existing_keys:
        if k not in new_props:
            new_props[k] = None  # signals deletion to Drive

    drive_set_app_properties(TOM_FOLDER_ID, new_props)

# ---------------------------------------------------------------------------
# Photo matching
# ---------------------------------------------------------------------------

def haversine_m(a_lat, a_lon, b_lat, b_lon):
    R = 6371000
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlam = math.radians(b_lon - a_lon)
    h = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlam/2)**2
    return 2 * R * math.asin(math.sqrt(h))

def parse_exif_time_to_canary_date(t):
    """EXIF time '2026:04:24 09:50:27' -> Canary date."""
    if not t:
        return None
    try:
        # EXIF time is local-time-no-tz. Treat it as Canary local since Tom's tablet is local.
        d = dt.datetime.strptime(t, "%Y:%m:%d %H:%M:%S")
        return d.date()
    except Exception:
        return None

def get_geocode(address, cache_path):
    """Cached forward geocode. Returns dict or None."""
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
    else:
        cache = {}
    if address in cache:
        return cache[address]
    try:
        g = geocode_helper()
        result = g.geocode(address) if address else None
    except Exception:
        result = None
    cache[address] = result
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2))
    return result

def lead_address(lead, events_for_lead=None):
    """The chosen site address for this lead -- NOT the invoice address.

    Priority:
      1. Lead's Studio 'Location (Survey)' field (x_studio_char_field_3qWjM)
      2. First non-empty `location` field across the lead's calendar events
         (most useful for legacy leads where Nicola filled in the calendar
         event but never the Studio field on the lead).

    The structured `street/street2/city/zip` fields on the lead are the
    INVOICE address and are no longer consulted for GPS matching.
    """
    survey = (lead.get("x_studio_char_field_3qWjM") or "").strip()
    if survey and survey != "False":
        return survey
    if events_for_lead:
        for ev in events_for_lead:
            cal = (ev.get("location") or "").strip()
            if cal and cal != "False":
                return cal
    return ""

def lead_coords(lead, events_for_lead, cache_path):
    addr = lead_address(lead, events_for_lead)
    if not addr:
        return None
    g = get_geocode(addr, cache_path)
    if not g or not g.get("lat"):
        return None
    return (g["lat"], g["lon"], g.get("location_type", ""))

def match_photo_by_gps(photo, leads_by_id, geocode_cache):
    """Stage 1: GPS + EXIF-date matching."""
    img_md = photo.get("imageMediaMetadata") or {}
    loc = img_md.get("location")
    time_str = img_md.get("time")
    if not loc or not time_str:
        return None
    plat = loc.get("latitude")
    plon = loc.get("longitude")
    photo_date = parse_exif_time_to_canary_date(time_str)
    if not photo_date:
        return None

    # Find candidate same-day events (±1 day for tz edge cases)
    candidate_leads = []
    for lead_id, events in leads_by_id.items():
        for ev in events:
            ev_date = event_canary_date(ev)
            if abs((ev_date - photo_date).days) > 1:
                continue
            lead = leads_by_id.get(lead_id, [])
            if lead and isinstance(lead, list) and len(lead) > 0:
                # leads_by_id holds events; we need actual lead data from caller's lead_obj_by_id
                pass
            candidate_leads.append(lead_id)
            break

    # Note: this function operates on lead_id alone; caller resolves geocodes.
    return candidate_leads, plat, plon

# ---------------------------------------------------------------------------
# Main run loop -- orchestration
# ---------------------------------------------------------------------------

class Runner:
    def __init__(self, dry_run=False, first_run=False):
        self.dry_run = dry_run
        self.first_run = first_run
        self.log = Log(dry_run=dry_run)
        self.geocode_cache = CACHE_DIR / "geocode-photo-sort.json"
        self.summary = {
            "created": 0, "renamed": 0, "moved": 0, "swept": 0,
            "photos_moved": 0, "unmapped": 0, "emails": 0,
            "skipped_renames": 0, "errors": [],
        }
        self.queued_unmapped_emails = []
        self.do_not_recreate = set()

    def run(self):
        self.log("INFO", "run-start", dry_run=self.dry_run, first_run=self.first_run)
        try:
            # Step 1: pull Odoo events + leads
            events, leads_by_id, lead_obj_by_id = self._pull_odoo()

            # Step 2: load do_not_recreate FIRST (must happen before Step 0 detect_trashed,
            # otherwise Step 0's additions get silently overwritten by this load — that
            # was the bug fixed 2026-05-25 that allowed trashed-folder lead_ids to slip
            # past the create-block in _step_manage_folders).
            self.do_not_recreate = read_do_not_recreate_set()

            # Step 0: detect manually-trashed managed folders + UNION into do_not_recreate.
            # Order is "0 after 2" deliberately — the persisted set is the base, fresh
            # detections add to it. The 8-Persist step at the end writes the combined set
            # back.
            self._step_detect_trashed()
            self.log("INFO", "do-not-recreate", count=len(self.do_not_recreate))

            # Step 3: ensure month folders exist (per-month _unmapped is created on demand)
            month_folder_ids = self._ensure_month_folders(events)
            legacy_root_unmapped_id = self._ensure_unmapped()  # for migration only

            # Step 4: per-lead folder management
            self._step_manage_folders(events, leads_by_id, lead_obj_by_id, month_folder_ids)

            # Step 5a: sort photos -- ROUND 1 (tight ±1 day, confident matches)
            self._step_sort_photos(month_folder_ids, legacy_root_unmapped_id,
                                   leads_by_id, lead_obj_by_id,
                                   tolerance_days=GPS_DATE_TOLERANCE_DAYS_TIGHT)

            # Step 5b: sort photos -- ROUND 2 (wide ±14 days) on whatever is now
            # in per-month _unmapped buckets. GPS distance constraint still
            # applies, so this only catches follow-up visits to the same property.
            self._step_sort_unmapped_round2(month_folder_ids,
                                            leads_by_id, lead_obj_by_id)

            # Step 6: re-colour managed folders
            self._step_recolour()

            # Step 7: sweep (uses full lead history) -- adds to do_not_recreate.
            # Sweep MUST run before writeback (2026-05-25). Pre-patch order had
            # writeback before sweep, so writeback wrote photos_link URLs to
            # Odoo for folders that sweep then immediately trashed — leaving
            # 18+ leads with broken URLs pointing at deleted Drive folders.
            self._step_sweep()

            # Step 5c (now after sweep): write back per-lead status to Odoo CRM
            # (x_studio_photos_link + x_studio_photos_uploaded). Iterates
            # drive_find_managed_folders(trashed=False), so swept folders are
            # not in scope — no broken URLs written.
            self._step_writeback_to_odoo()

            # Step 8: persist do_not_recreate AFTER sweep so swept lead IDs survive
            if not self.dry_run:
                write_do_not_recreate_set(self.do_not_recreate)

            # Step 9: send unmapped email if queued
            if self.queued_unmapped_emails:
                self._send_unmapped_email()

            # Step 10: rewrite _MAP.md
            self._write_map_md()

            # Step 11: persist run state + daily note
            self._persist_run_state(status="ok")

            self.log("INFO", "run-complete", **{k: v for k, v in self.summary.items() if k != "errors"})
        except Exception as e:
            self.log("ERROR", "fatal", err=repr(e))
            self.summary["errors"].append(str(e))
            try:
                self._send_error_email(e)
            except Exception:
                pass
            self._persist_run_state(status="error")
            raise
        finally:
            self.log.cleanup_old_logs()
            self.log.close()

    # ----- step impls below -----

    def _step_detect_trashed(self):
        for f in drive_find_managed_folders(trashed=True):
            ap = f.get("appProperties") or {}
            last_updated = ap.get("last_updated", "")
            try:
                lu_dt = dt.datetime.fromisoformat(last_updated) if last_updated else None
            except Exception:
                lu_dt = None
            if lu_dt is None or (dt.datetime.now(CANARY) - lu_dt).total_seconds() > 86400:
                lead_id = ap.get("lead_id")
                if lead_id:
                    self.do_not_recreate.add(lead_id)
                    self.log("INFO", "trash-detected", lead_id=lead_id, name=f["name"])

    def _pull_odoo(self):
        domain = [
            ["partner_ids", "in", [TOM_PARTNER_ID]],
            ["start", ">=", utc_iso(today_canary() - dt.timedelta(days=WINDOW_BACK_DAYS))],
            ["start", "<=", utc_iso(today_canary() + dt.timedelta(days=WINDOW_FORWARD_DAYS))],
        ]
        fields = ["id", "name", "start", "stop", "location", "description",
                  "opportunity_id", "allday", "active"]
        events = odoo_search_read("calendar.event", domain, fields, limit=500)
        events = [e for e in events if e.get("active") is not False]
        self.log("INFO", "events-pulled", count=len(events))

        # Group by lead
        leads_by_id = {}
        for ev in events:
            opp = ev.get("opportunity_id")
            if opp:
                leads_by_id.setdefault(opp[0], []).append(ev)

        # Read lead details
        lead_objs = odoo_read("crm.lead", list(leads_by_id.keys()),
                              ["id", "name", "partner_id", "partner_name", "contact_name",
                               "phone", "x_studio_char_field_3qWjM",  # Location (Survey)
                               "stage_id", "description", "calendar_event_ids"])
        lead_obj_by_id = {l["id"]: l for l in lead_objs}
        self.log("INFO", "leads-pulled", count=len(lead_obj_by_id))
        return events, leads_by_id, lead_obj_by_id

    def _ensure_month_folders(self, events):
        """Create month subfolders under tom/ for any month touched by events."""
        existing = {f["name"]: f["id"] for f in drive_list_in_folder(TOM_FOLDER_ID, mime_filter="folder")}
        wanted_months = set()
        # Months for events
        for ev in events:
            d = event_canary_date(ev)
            wanted_months.add((d.year, d.month))
        # Always ensure current + previous month
        today = today_canary()
        wanted_months.add((today.year, today.month))
        if today.month == 1:
            wanted_months.add((today.year - 1, 12))
        else:
            wanted_months.add((today.year, today.month - 1))

        month_folder_ids = {}
        for (y, m) in wanted_months:
            name = f"{m:02d} {dt.date(y, m, 1).strftime('%b')} {y % 100:02d}"
            if name in existing:
                month_folder_ids[(y, m)] = existing[name]
            else:
                if self.dry_run:
                    self.log("INFO", "would-create-month", name=name)
                    month_folder_ids[(y, m)] = "DRY_RUN"
                else:
                    res = drive_create_folder(name, TOM_FOLDER_ID)
                    month_folder_ids[(y, m)] = res["id"]
                    self.log("INFO", "month-created", name=name, id=res["id"])

        # Eagerly ensure every existing month folder has a `_unmapped/`
        # subfolder so the presence/absence of contents is the meaningful
        # signal -- not the presence/absence of the bucket itself.
        # (Set 2026-05-04 evening.)
        # Scope: every "NN MMM YY"-named folder directly under tom/, not just
        # the ones currently in the event window. Skip months whose folder
        # doesn't yet exist -- those will get _unmapped when month_folder_ids
        # eventually pulls them in.
        import re as _re_month
        month_pattern = _re_month.compile(r"^\d{2}\s+\w{3}\s+\d{2}$")
        for f in drive_list_in_folder(TOM_FOLDER_ID, mime_filter="folder"):
            if not month_pattern.match(f["name"]):
                continue
            children = drive_list_in_folder(f["id"], mime_filter="folder")
            if any(c["name"] == "_unmapped" for c in children):
                continue
            if self.dry_run:
                self.log("INFO", "would-create-unmapped", month=f["name"])
            else:
                drive_create_folder("_unmapped", f["id"],
                                    app_properties={"managed_by": MANAGED_BY,
                                                    "kind": "month-unmapped"})
                self.log("INFO", "unmapped-created", month=f["name"])
        return month_folder_ids

    def _ensure_unmapped(self):
        """Legacy root-level _unmapped folder lookup (only used to find any existing
        photos to migrate to per-month buckets)."""
        existing = drive_list_in_folder(TOM_FOLDER_ID, mime_filter="folder")
        for f in existing:
            if f["name"] in (UNMAPPED_NAME, "_unmapped"):
                return f["id"]
        return None

    def _get_unmapped_for_month(self, year, month, month_folder_ids):
        """Return the _unmapped folder id INSIDE the given month folder.
        Creates the month folder AND the _unmapped subfolder on demand. Cached per run."""
        if not hasattr(self, "_unmapped_by_month_cache"):
            self._unmapped_by_month_cache = {}
        key = (year, month)
        if key in self._unmapped_by_month_cache:
            return self._unmapped_by_month_cache[key]
        month_id = month_folder_ids.get(key)
        if not month_id and not self.dry_run:
            # Lazy-create the month folder for older photos outside the event window
            name = f"{month:02d} {dt.date(year, month, 1).strftime('%b')} {year % 100:02d}"
            existing_root = {f["name"]: f["id"]
                             for f in drive_list_in_folder(TOM_FOLDER_ID, mime_filter="folder")}
            if name in existing_root:
                month_id = existing_root[name]
            else:
                res = drive_create_folder(name, TOM_FOLDER_ID)
                month_id = res["id"]
                self.log("INFO", "month-created-lazy", name=name, id=month_id)
            month_folder_ids[key] = month_id
        if not month_id or month_id == "DRY_RUN":
            self._unmapped_by_month_cache[key] = "DRY_RUN"
            return "DRY_RUN"
        # Look for existing _unmapped inside this month folder
        for f in drive_list_in_folder(month_id, mime_filter="folder"):
            ap = f.get("appProperties") or {}
            if f["name"] == "_unmapped":
                if ap.get("managed_by") != MANAGED_BY and not self.dry_run:
                    drive_set_app_properties(f["id"], {"managed_by": MANAGED_BY})
                self._unmapped_by_month_cache[key] = f["id"]
                return f["id"]
        # Create it
        if self.dry_run:
            self._unmapped_by_month_cache[key] = "DRY_RUN"
            return "DRY_RUN"
        res = drive_create_folder("_unmapped", month_id,
                                  app_properties={"managed_by": MANAGED_BY})
        if hasattr(self, "log"):
            self.log("INFO", "unmapped-month-created",
                     month=f"{year}-{month:02d}", id=res["id"])
        # Recolour grey so it stands out from job folders (orange)
        try:
            if not self.dry_run:
                drive_set_color(res["id"], GREY)
        except Exception:
            pass
        self._unmapped_by_month_cache[key] = res["id"]
        return res["id"]

    def _step_manage_folders(self, events, leads_by_id, lead_obj_by_id, month_folder_ids):
        # Bulk-fetch all managed folders once and build by-lead lookup
        managed_folders = drive_find_managed_folders(trashed=False)
        managed_groups = {}
        for f in managed_folders:
            ap = f.get("appProperties") or {}
            lid = ap.get("lead_id")
            if lid:
                managed_groups.setdefault(lid, []).append(f)
        self.log("INFO", "managed-folders-prefetched", count=len(managed_folders))

        # Resolve duplicates: when a lead has >1 managed folder, pick a keeper
        # (most children, then oldest created_at), merge children from the
        # duplicates into the keeper, then trash the duplicates.
        managed_by_lead = {}
        for lid, group in managed_groups.items():
            if len(group) == 1:
                managed_by_lead[lid] = group[0]
                continue
            # Annotate each with child list
            for f in group:
                f["_children"] = drive_list_in_folder(f["id"])
                f["_child_count"] = len(f["_children"])
            # Sort: most children first, then oldest created_at
            def score(f):
                ap = f.get("appProperties") or {}
                return (-f["_child_count"], ap.get("created_at") or "9999")
            group.sort(key=score)
            keeper = group[0]
            duplicates = group[1:]
            for dup in duplicates:
                if dup["_child_count"] == 0:
                    # Empty dup -- safe to trash
                    if not self.dry_run:
                        drive_trash(dup["id"])
                    self.log("INFO", "duplicate-trashed-empty", lead_id=lid,
                             kept=keeper["name"][:50], trashed=dup["name"][:50])
                    self.summary.setdefault("dup_trashed", 0)
                    self.summary["dup_trashed"] += 1
                else:
                    # Non-empty dup -- merge children into keeper, then trash
                    moved_ok = 0
                    moved_failed = 0
                    for child in dup["_children"]:
                        if self.dry_run:
                            moved_ok += 1
                            continue
                        try:
                            self._safe_move_child(child["id"], keeper["id"], dup["id"])
                            moved_ok += 1
                        except Exception as e:
                            moved_failed += 1
                            self.log("WARN", "dup-merge-child-move-failed",
                                     child=child["name"][:50], err=repr(e)[:80])
                    if moved_failed == 0 and not self.dry_run:
                        try:
                            drive_trash(dup["id"])
                        except Exception as e:
                            self.log("WARN", "dup-trash-after-merge-failed",
                                     folder=dup["name"][:50], err=repr(e)[:80])
                    self.log("INFO", "duplicate-merged", lead_id=lid,
                             kept=keeper["name"][:50], merged=dup["name"][:50],
                             moved=moved_ok, failed=moved_failed)
                    self.summary.setdefault("dup_merged", 0)
                    self.summary["dup_merged"] += 1
                    # If any move failed, leave dup in place (safer than trashing
                    # a folder that still has content). Will retry next run.
            managed_by_lead[lid] = keeper

        for lead_id, lead_events in leads_by_id.items():
            lead = lead_obj_by_id.get(lead_id)
            if not lead:
                continue

            # Filter to folder-worthy events
            folder_worthy = [e for e in lead_events if is_folder_worthy(classify_jobtype(e))]

            # Check do-not-recreate
            if str(lead_id) in self.do_not_recreate:
                if any(self._is_future_unseen(e, lead) for e in lead_events):
                    self.do_not_recreate.discard(str(lead_id))
                    self.log("INFO", "lifted-do-not-recreate", lead_id=lead_id)
                else:
                    continue

            if not folder_worthy:
                continue

            # Determine target name + month.
            # Phase C (2026-05-04): the folder's anchor month is the EARLIEST
            # event's month -- the folder lives there permanently. Later events
            # (Repair, Reinstatement) update the folder name but DO NOT move
            # the folder forward. This preserves Tom's mental model: "a job
            # that started in March stays under March, even if reinstatement
            # happens in July."
            target_name = build_folder_name(lead, folder_worthy)
            earliest_date = min(event_canary_date(e) for e in folder_worthy)
            target_month_id = month_folder_ids.get((earliest_date.year, earliest_date.month))

            existing = managed_by_lead.get(str(lead_id))

            if existing:
                self._update_existing_folder(existing, target_name, target_month_id,
                                             lead, lead_events, folder_worthy)
            else:
                # Sweep-eligibility pre-check (2026-05-25): if a NEW folder would be
                # created for a lead whose latest event is already past SWEEP_DAYS AND
                # whose stage is closed, _step_sweep would trash it later in the same
                # run. Skip the create + writeback churn entirely. Add to do_not_recreate
                # so future runs don't try again until something changes (e.g. Tom uploads
                # a photo that matches via Round 2 wide GPS, which lifts do_not_recreate).
                latest_event_date = max(event_canary_date(e) for e in folder_worthy)
                days_since_latest = (today_canary() - latest_event_date).days
                stage_id = (lead.get("stage_id") or [None])[0]
                if days_since_latest >= SWEEP_DAYS and stage_id in CLOSED_STAGE_IDS:
                    self.do_not_recreate.add(str(lead_id))
                    self.log("INFO", "skip-create-sweep-eligible",
                             lead_id=lead_id, days=days_since_latest, stage_id=stage_id,
                             name=target_name)
                    continue
                self._create_new_folder(target_name, target_month_id, lead,
                                        lead_events, folder_worthy)

    def _is_future_unseen(self, ev, lead):
        d = event_canary_date(ev)
        return d > today_canary()

    def _update_existing_folder(self, existing, target_name, target_month_id,
                                lead, lead_events, folder_worthy):
        ap = existing.get("appProperties") or {}
        last_known_name = ap.get("last_known_name", "")
        new_props = {
            "event_ids": ",".join(str(e["id"]) for e in lead_events),
            "jobtypes": ",".join(self._dedupe_jobtypes(folder_worthy)),
            "earliest_event_date": min(event_canary_date(e).isoformat() for e in folder_worthy),
            "latest_event_date":   max(event_canary_date(e).isoformat() for e in folder_worthy),
            "last_updated": now_iso_canary(),
        }

        # Manual-rename protection (uses hash for long names)
        # Self-healing: if existing.name already matches target_name, sync last_known_name
        # silently (covers the case where a previous run renamed but failed to write props)
        if existing["name"] == target_name:
            new_props["last_known_name"] = name_signature(target_name)
        elif name_matches_signature(existing["name"], last_known_name):
            if not self.dry_run:
                drive_rename(existing["id"], target_name)
            self.summary["renamed"] += 1
            self.log("INFO", "folder-renamed", lead_id=ap.get("lead_id"), to=target_name)
            new_props["last_known_name"] = name_signature(target_name)
        else:
            self.summary["skipped_renames"] += 1
            self.log("INFO", "skipped-rename-manual", folder_id=existing["id"], current=existing["name"])

        # Phase C (2026-05-04): existing folders are NEVER moved between
        # months. Once a folder is created, it lives in its anchor month
        # forever. The folder name still reflects all dates, so cross-month
        # context is preserved without physically moving the folder. This
        # also avoids the brittle cross-month-move race that could leave
        # photos orphaned mid-move.
        # (We still log when the target month would have differed, so any
        # surprises surface in the run summary.)
        current_parents = existing.get("parents", [])
        if target_month_id and target_month_id not in current_parents and target_month_id != "DRY_RUN":
            self.log("INFO", "folder-stays-anchored",
                     lead_id=ap.get("lead_id"),
                     anchor_parents=current_parents,
                     would_have_moved_to=target_month_id)

        if not self.dry_run:
            drive_set_app_properties(existing["id"], new_props)

    def _create_new_folder(self, target_name, target_month_id, lead, lead_events, folder_worthy):
        partner = lead.get("partner_id") or [None, ""]
        props = {
            "managed_by":      MANAGED_BY,
            "lead_id":         str(lead["id"]),
            "event_ids":       ",".join(str(e["id"]) for e in lead_events),
            "jobtypes":        ",".join(self._dedupe_jobtypes(folder_worthy)),
            "customer":        partner[1] if partner[1] else (lead.get("partner_name") or ""),
            "contact_name":    lead.get("contact_name") or "",
            "location":        extract_location_abbrev(folder_worthy),
            "earliest_event_date": min(event_canary_date(e).isoformat() for e in folder_worthy),
            "latest_event_date":   max(event_canary_date(e).isoformat() for e in folder_worthy),
            "created_at":      now_iso_canary(),
            "last_updated":    now_iso_canary(),
            "last_known_name": name_signature(target_name),
        }
        # drive_set_app_properties auto-trims to 124-byte key+value limit
        if self.dry_run or target_month_id == "DRY_RUN":
            self.log("INFO", "would-create-folder", name=target_name)
            return
        res = drive_create_folder(target_name, target_month_id, app_properties=props)
        self.summary["created"] += 1
        self.log("INFO", "folder-created", lead_id=lead["id"], name=target_name, id=res["id"])

    def _dedupe_jobtypes(self, events):
        seen = set()
        out = []
        for ev in events:
            jt = classify_jobtype(ev)
            if jt and jt not in seen:
                seen.add(jt)
                out.append(jt)
        return out

    def _resurrect_folder_for_lead(self, lead_id, leads_by_id, lead_obj_by_id,
                                    month_folder_ids):
        """Re-create a folder for a lead that was previously swept (in
        do_not_recreate). Triggered when a new photo matches this lead via
        GPS+date -- the new photo is itself the signal that filing is needed
        again.

        Returns the newly-created folder dict (with id, name, parents) so the
        caller can move the photo straight in. Returns None if the lead has
        no folder-worthy events in the current window (in which case the
        photo will fall through to _unmapped, same as today).

        Side effect: removes lead_id from self.do_not_recreate so it won't be
        re-suppressed by a re-run.
        """
        lead = lead_obj_by_id.get(lead_id)
        lead_events = leads_by_id.get(lead_id, [])
        if not lead or not lead_events:
            return None
        folder_worthy = [e for e in lead_events if is_folder_worthy(classify_jobtype(e))]
        if not folder_worthy:
            return None

        target_name = build_folder_name(lead, folder_worthy)
        earliest_date = min(event_canary_date(e) for e in folder_worthy)
        target_month_id = month_folder_ids.get((earliest_date.year, earliest_date.month))
        if not target_month_id or target_month_id == "DRY_RUN":
            # No month folder available (e.g. dry-run, or earliest event is
            # outside the window's month coverage). Bail; photo falls through
            # to _unmapped.
            return None

        # Lift suppression so this folder doesn't get re-swept on the same run
        self.do_not_recreate.discard(str(lead_id))

        partner = lead.get("partner_id") or [None, ""]
        props = {
            "managed_by":      MANAGED_BY,
            "lead_id":         str(lead["id"]),
            "event_ids":       ",".join(str(e["id"]) for e in lead_events),
            "jobtypes":        ",".join(self._dedupe_jobtypes(folder_worthy)),
            "customer":        partner[1] if partner[1] else (lead.get("partner_name") or ""),
            "contact_name":    lead.get("contact_name") or "",
            "location":        extract_location_abbrev(folder_worthy),
            "earliest_event_date": earliest_date.isoformat(),
            "latest_event_date":   max(event_canary_date(e).isoformat() for e in folder_worthy),
            "created_at":      now_iso_canary(),
            "last_updated":    now_iso_canary(),
            "last_known_name": name_signature(target_name),
            "resurrected_at":  now_iso_canary(),
        }
        if self.dry_run:
            self.log("INFO", "would-resurrect-folder",
                     lead_id=lead_id, name=target_name)
            # Return a stub so the caller's photo-move branch logs would-move
            return {"id": "DRY_RUN_RESURRECT", "name": target_name, "parents": [target_month_id]}
        res = drive_create_folder(target_name, target_month_id, app_properties=props)
        self.summary.setdefault("folders_resurrected", 0)
        self.summary["folders_resurrected"] += 1
        self.log("INFO", "folder-resurrected",
                 lead_id=lead_id, name=target_name, id=res["id"])
        # Return same shape as drive_find_by_lead would
        return {
            "id": res["id"],
            "name": target_name,
            "parents": [target_month_id],
            "appProperties": props,
        }

    def _step_sort_photos(self, month_folder_ids, legacy_root_unmapped_id,
                          leads_by_id, lead_obj_by_id,
                          tolerance_days=GPS_DATE_TOLERANCE_DAYS_TIGHT):
        # Walk root + each month root + each per-month _unmapped + legacy root _unmapped
        roots = [TOM_FOLDER_ID]
        if legacy_root_unmapped_id:
            roots.append(legacy_root_unmapped_id)
        for mid in month_folder_ids.values():
            if mid != "DRY_RUN":
                roots.append(mid)
                # Also walk any existing _unmapped inside the month folder
                for f in drive_list_in_folder(mid, mime_filter="folder"):
                    if f["name"] == "_unmapped":
                        roots.append(f["id"])

        all_photos = []
        for root_id in set(roots):
            if root_id == "DRY_RUN":
                continue
            for f in drive_list_in_folder(root_id, mime_filter="non-folder"):
                if not _is_photo_file(f["name"]):
                    continue  # skip _MAP.md, README.md, .DS_Store etc
                all_photos.append((root_id, f))

        self.log("INFO", "sort-round-start", tolerance_days=tolerance_days,
                 photos=len(all_photos))
        for root_id, photo in all_photos:
            self._sort_one_photo(photo, root_id, month_folder_ids,
                                 leads_by_id, lead_obj_by_id,
                                 tolerance_days=tolerance_days)

    def _step_sort_unmapped_round2(self, month_folder_ids, leads_by_id, lead_obj_by_id):
        """Round 2: walk only the per-month _unmapped folders and retry with
        the wider date tolerance. GPS distance still capped at GPS_NEAREST_MAX_M
        so this only catches follow-up visits to the same property."""
        unmapped_roots = []
        for mid in month_folder_ids.values():
            if mid == "DRY_RUN":
                continue
            for f in drive_list_in_folder(mid, mime_filter="folder"):
                if f["name"] == "_unmapped":
                    unmapped_roots.append(f["id"])

        all_photos = []
        for root_id in unmapped_roots:
            for f in drive_list_in_folder(root_id, mime_filter="non-folder"):
                if not _is_photo_file(f["name"]):
                    continue
                all_photos.append((root_id, f))

        self.log("INFO", "sort-round-start", round=2,
                 tolerance_days=GPS_DATE_TOLERANCE_DAYS_WIDE,
                 photos=len(all_photos))

        round2_moved = 0
        for root_id, photo in all_photos:
            target_lead_id = self._match_gps(
                photo, leads_by_id, lead_obj_by_id,
                tolerance_days=GPS_DATE_TOLERANCE_DAYS_WIDE)
            if not target_lead_id:
                continue
            target_folder = drive_find_by_lead(target_lead_id)
            # Resurrect a do-not-recreate folder if a new photo matches it
            # (closed-stage lead whose folder was swept; the new photo is
            # the signal that filing is needed again).
            if not target_folder and str(target_lead_id) in self.do_not_recreate:
                target_folder = self._resurrect_folder_for_lead(
                    target_lead_id, leads_by_id, lead_obj_by_id, month_folder_ids)
            if not target_folder:
                continue
            if target_folder["id"] == root_id:
                continue
            if not self.dry_run:
                drive_move(photo["id"], target_folder["id"], [root_id])
                drive_set_app_properties(photo["id"], {
                    "filed_at": now_iso_canary(),
                    "filed_by": MANAGED_BY,
                    "filed_to_lead_id": str(target_lead_id),
                    "filed_round": "2",
                })
            self.summary["photos_moved_round2"] = self.summary.get("photos_moved_round2", 0) + 1
            self.summary["photos_moved"] += 1
            self.summary["unmapped"] = max(0, self.summary["unmapped"] - 1)
            round2_moved += 1
            self.log("INFO", "photo-moved-round2", name=photo["name"],
                     to_lead=target_lead_id, to_folder=target_folder["name"])

        self.log("INFO", "sort-round2-complete", moved=round2_moved,
                 still_unmapped=len(all_photos) - round2_moved)

    def _sort_one_photo(self, photo, current_root_id, month_folder_ids,
                        leads_by_id, lead_obj_by_id,
                        tolerance_days=GPS_DATE_TOLERANCE_DAYS_TIGHT):
        target_lead_id = None

        # Stage 1: GPS + date (primary -- every Tom job's content looks the same
        # to Vision so content-match is useless; GPS is the only reliable signal)
        target_lead_id = self._match_gps(photo, leads_by_id, lead_obj_by_id,
                                          tolerance_days=tolerance_days)

        # Stage 2: Bracket-match (only iPhone-named consecutive shots)
        if not target_lead_id and re.match(r"^IMG_\d+\.(MOV|HEIC|JPG|JPEG|PNG)$", photo["name"], re.I):
            target_lead_id = self._match_bracket(photo, current_root_id)

        if target_lead_id:
            target_folder = drive_find_by_lead(target_lead_id)
            # If matched but folder doesn't exist, the lead may have been swept
            # into do_not_recreate by a previous run (closed-stage, empty folder
            # at sweep time). New photos arriving for this lead are themselves
            # signal that we need the folder back -- resurrect it.
            if not target_folder and str(target_lead_id) in self.do_not_recreate:
                target_folder = self._resurrect_folder_for_lead(
                    target_lead_id, leads_by_id, lead_obj_by_id, month_folder_ids)
            if target_folder:
                if target_folder["id"] == current_root_id:
                    return  # already in the right place
                if not self.dry_run:
                    drive_move(photo["id"], target_folder["id"], [current_root_id])
                    drive_set_app_properties(photo["id"], {
                        "filed_at": now_iso_canary(),
                        "filed_by": MANAGED_BY,
                        "filed_to_lead_id": str(target_lead_id),
                    })
                self.summary["photos_moved"] += 1
                self.log("INFO", "photo-moved", name=photo["name"],
                         to_lead=target_lead_id, to_folder=target_folder["name"])
                return

        # Otherwise: unmapped -- pick the right per-month bucket from EXIF date
        # (fallback: modifiedTime, then today)
        img_md = photo.get("imageMediaMetadata") or {}
        photo_date = parse_exif_time_to_canary_date(img_md.get("time"))
        if not photo_date:
            mt = photo.get("modifiedTime") or photo.get("createdTime")
            if mt:
                try:
                    photo_date = dt.datetime.fromisoformat(
                        mt.replace("Z", "+00:00")).astimezone(CANARY).date()
                except Exception:
                    photo_date = today_canary()
            else:
                photo_date = today_canary()

        unmapped_id = self._get_unmapped_for_month(
            photo_date.year, photo_date.month, month_folder_ids)

        if current_root_id == unmapped_id:
            return  # already there
        if unmapped_id == "DRY_RUN":
            return
        if not self.dry_run:
            drive_move(photo["id"], unmapped_id, [current_root_id])
            ap = photo.get("appProperties") or {}
            if not ap.get("unmapped_email_sent_at"):
                drive_set_app_properties(photo["id"], {
                    "unmapped_email_sent_at": now_iso_canary(),
                })
                self.queued_unmapped_emails.append(photo)
        self.summary["unmapped"] += 1
        self.log("INFO", "photo-unmapped", name=photo["name"],
                 month=f"{photo_date.year}-{photo_date.month:02d}")

    def _match_gps(self, photo, leads_by_id, lead_obj_by_id,
                   tolerance_days=GPS_DATE_TOLERANCE_DAYS_TIGHT):
        img_md = photo.get("imageMediaMetadata") or {}
        loc = img_md.get("location")
        time_str = img_md.get("time")
        if not loc or not time_str:
            return None
        plat = loc.get("latitude")
        plon = loc.get("longitude")
        photo_date = parse_exif_time_to_canary_date(time_str)
        if plat is None or plon is None or photo_date is None:
            return None

        # Candidate leads: any event within ±tolerance_days of photo date
        candidates = []
        for lead_id, events in leads_by_id.items():
            in_window = any(abs((event_canary_date(e) - photo_date).days) <= tolerance_days
                            for e in events)
            if not in_window:
                continue
            lead = lead_obj_by_id.get(lead_id)
            if not lead:
                continue
            coords = lead_coords(lead, leads_by_id.get(lead_id, []), self.geocode_cache)
            if not coords:
                continue
            d = haversine_m(plat, plon, coords[0], coords[1])
            # Per-lead radius based on geocode precision. Tight (1000m) for
            # rooftop/range-interpolated/geometric-center hits; wide (3000m)
            # for APPROXIMATE (town-centre level) hits. Filters at candidate
            # collection time so the ratio + far-fallback checks downstream
            # can't pull in candidates whose own geocode is too imprecise.
            location_type = coords[2] if len(coords) > 2 else ""
            this_max = (GPS_NEAREST_MAX_M_APPROX
                        if location_type == "APPROXIMATE"
                        else GPS_NEAREST_MAX_M)
            if d > this_max:
                continue
            candidates.append((d, lead_id))
        if not candidates:
            return None
        candidates.sort()
        nearest_d, nearest_lead = candidates[0]
        # No global cap here -- already enforced per-lead above.
        if len(candidates) == 1:
            return nearest_lead
        second_d = candidates[1][0]
        if second_d > GPS_RATIO_MULTIPLIER * nearest_d or second_d > GPS_FAR_FALLBACK_M:
            return nearest_lead
        return None

    # NOTE: Vision content-match (`_match_vision` + `_lead_keyword_set`) removed
    # 2026-05-02. Every Tom job photo (pressure gauges, pipes, water meters,
    # leaking tiles) looks generic to Vision so content keywords matched the
    # same words against every lead -- attributions were unreliable. GPS + EXIF
    # date is the only signal we use. Vision API helper still on disk for other
    # use cases; just not invoked from this script.

    def _match_bracket(self, photo, current_root_id):
        # Extract the number
        m = re.match(r"^IMG_(\d+)\.", photo["name"], re.I)
        if not m:
            return None
        n = int(m.group(1))
        # Find IMG_(n-1) and IMG_(n+1) anywhere
        ext_re = r"\.(MOV|HEIC|JPG|JPEG|PNG)$"
        prev_name_glob = f"IMG_{n-1:04d}"
        next_name_glob = f"IMG_{n+1:04d}"
        # Drive search by name (no wildcard, exact pattern test)
        # Just search for the prev / next names
        candidates = []
        for label, base in (("prev", n-1), ("next", n+1)):
            for ext in ("HEIC", "JPG", "JPEG", "PNG", "MOV"):
                fname = f"IMG_{base:04d}.{ext}"
                resp = drive_call("GET", "/files", params={
                    "q": f"name = '{fname}' and trashed=false",
                    "fields": "files(id,name,parents,appProperties)",
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                    "corpora": "allDrives",
                    "pageSize": "5",
                })
                for f in resp.get("files", []):
                    candidates.append(f)
                    break

        # Both must be in the same managed folder
        parents_seen = []
        for f in candidates:
            for p in f.get("parents", []):
                # Is this parent a managed folder?
                folder = drive_get(p, fields="appProperties,name")
                if (folder.get("appProperties") or {}).get("managed_by") == MANAGED_BY:
                    parents_seen.append((p, folder["appProperties"].get("lead_id")))
        if len(parents_seen) >= 2 and len(set(p[0] for p in parents_seen)) == 1:
            return parents_seen[0][1]
        return None

    def _step_recolour(self):
        for f in drive_find_managed_folders(trashed=False):
            children = drive_list_in_folder(f["id"])
            target = ORANGE if children else GREY
            current = f.get("folderColorRgb", "")
            if current.lower() != target.lower():
                if not self.dry_run:
                    drive_set_color(f["id"], target)
                self.log("INFO", "folder-recoloured",
                         folder=f["name"], to=target)

    def _step_writeback_to_odoo(self):
        """For every managed Drive folder, write the folder URL + photos-uploaded
        flag onto its `crm.lead`. Idempotent -- only writes when a value would
        actually change. See [[photo-sort-architectural-refactor-plan]] Phase F.

        Fields:
          x_studio_photos_link      -- char (url widget) -- the Drive folder URL
          x_studio_photos_uploaded  -- selection: 'no' / 'yes' -- 'yes' once at
                                       least one non-folder child exists.
                                       Latches forever -- once 'yes', never
                                       reverts to 'no' even if photos are
                                       removed (Tom's already photographed).

        Counters added to summary:
          photos_link_written        -- # leads whose URL got written this run
          photos_uploaded_flipped    -- # leads flipped from 'no' to 'yes'
        """
        self.summary.setdefault("photos_link_written", 0)
        self.summary.setdefault("photos_uploaded_flipped", 0)
        # Bulk-load every managed folder once
        managed = drive_find_managed_folders(trashed=False)
        # Collapse to one folder per lead_id (post-dedupe state). If two
        # somehow remain after the dedupe pass, prefer the keeper rule
        # (most children) -- defensive, shouldn't happen in steady state.
        by_lead = {}
        for f in managed:
            ap = f.get("appProperties") or {}
            lid = ap.get("lead_id")
            if not lid:
                continue
            by_lead.setdefault(lid, []).append(f)

        # Bulk-read current Odoo values so we only write deltas
        lead_ids_int = [int(lid) for lid in by_lead.keys()]
        if not lead_ids_int:
            self.log("INFO", "writeback-skipped-no-leads")
            return
        try:
            current = odoo_read("crm.lead", lead_ids_int,
                                ["id", "x_studio_photos_link", "x_studio_photos_uploaded"])
        except Exception as e:
            self.log("WARN", "writeback-odoo-read-failed", err=repr(e)[:120])
            return
        current_by_id = {str(c["id"]): c for c in current}

        for lid, folders in by_lead.items():
            # Choose the folder with the most non-folder children as the
            # "active" folder for this lead. In steady state there's exactly
            # one (the dedupe step earlier in the run resolves duplicates),
            # but we belt-and-braces in case dedupe was skipped.
            counts = {f["id"]: len(drive_list_in_folder(f["id"], mime_filter="non-folder"))
                      for f in folders}
            chosen = max(folders, key=lambda f: counts[f["id"]])
            folder_url = f"https://drive.google.com/drive/folders/{chosen['id']}"
            has_photos = counts[chosen["id"]] > 0

            cur = current_by_id.get(lid)
            if cur is None:
                # Lead not returned by Odoo read -- likely deleted/archived.
                # Skip the writeback; the sweep step will handle the orphan.
                self.log("INFO", "writeback-skipped-lead-missing", lead_id=lid)
                continue
            cur_link = cur.get("x_studio_photos_link") or ""
            cur_uploaded = cur.get("x_studio_photos_uploaded") or "no"

            payload = {}
            if cur_link != folder_url:
                payload["x_studio_photos_link"] = folder_url
            # Latching rule: once 'yes', never go back to 'no'
            desired_uploaded = "yes" if has_photos else cur_uploaded
            if desired_uploaded != cur_uploaded:
                payload["x_studio_photos_uploaded"] = desired_uploaded

            if not payload:
                continue

            if self.dry_run:
                self.log("INFO", "writeback-would-update",
                         lead_id=lid, **{k: str(v)[:60] for k, v in payload.items()})
            else:
                try:
                    odoo_write("crm.lead", int(lid), payload)
                    self.log("INFO", "writeback-updated",
                             lead_id=lid, **{k: str(v)[:60] for k, v in payload.items()})
                except Exception as e:
                    self.log("WARN", "writeback-write-failed",
                             lead_id=lid, err=repr(e)[:120])
                    continue

            if "x_studio_photos_link" in payload:
                self.summary["photos_link_written"] += 1
            if payload.get("x_studio_photos_uploaded") == "yes":
                self.summary["photos_uploaded_flipped"] += 1

    def _step_sweep(self):
        for f in drive_find_managed_folders(trashed=False):
            ap = f.get("appProperties") or {}
            children = drive_list_in_folder(f["id"])
            if children:
                continue
            lead_id = ap.get("lead_id")
            if not lead_id:
                continue
            try:
                lead_data = odoo_read("crm.lead", [int(lead_id)],
                                      ["stage_id", "calendar_event_ids"])
            except Exception:
                continue
            if not lead_data:
                # Lead deleted from Odoo entirely + folder is empty -> safe to sweep
                if not self.dry_run:
                    drive_trash(f["id"])
                self.do_not_recreate.add(lead_id)
                self.summary["swept"] += 1
                self.log("INFO", "swept-orphan-lead",
                         lead_id=lead_id, name=f["name"])
                continue
            lead = lead_data[0]
            stage_id = (lead.get("stage_id") or [None])[0]
            if stage_id not in CLOSED_STAGE_IDS:
                continue
            ev_ids = lead.get("calendar_event_ids") or []
            if not ev_ids:
                continue
            evs = odoo_read("calendar.event", ev_ids, ["start"])
            if not evs:
                continue
            latest = max(parse_odoo_dt(e["start"]) for e in evs)
            days_since = (dt.datetime.now(UTC) - latest).days
            if days_since >= SWEEP_DAYS:
                if not self.dry_run:
                    drive_trash(f["id"])
                    # Clear the wrongful writeback. Pre 2026-05-25 the writeback step
                    # ran before sweep, so URLs for folders that were about to be
                    # swept got written to Odoo first, leaving leads with broken
                    # x_studio_photos_link values pointing at trashed Drive folders.
                    # Even with writeback moved after sweep (2026-05-25), historical
                    # broken URLs need clearing — and any URL on a SWEPT lead is by
                    # definition broken. The folder is empty (no photos uploaded),
                    # so it's safe to reset both fields.
                    try:
                        odoo_write("crm.lead", int(lead_id),
                                   {"x_studio_photos_link": False,
                                    "x_studio_photos_uploaded": "no"})
                        self.log("INFO", "swept-cleared-photos-link", lead_id=lead_id)
                    except Exception as e:
                        self.log("WARN", "swept-clear-failed", lead_id=lead_id,
                                 err=repr(e)[:120])
                self.do_not_recreate.add(lead_id)
                self.summary["swept"] += 1
                self.log("INFO", "swept", lead_id=lead_id, name=f["name"], days=days_since)

    def _send_unmapped_email(self):
        if self.dry_run:
            return
        body_lines = [
            f"{len(self.queued_unmapped_emails)} photo(s) couldn't be auto-matched and have been moved to _unmapped/.",
            "",
            "Please open Drive and drag each into the right job folder.",
            "",
            "Drive: https://drive.google.com/drive/folders/" + TOM_FOLDER_ID,
            "",
            "Photos:",
        ]
        for p in self.queued_unmapped_emails:
            body_lines.append(f"  - {p['name']}")
        body = "\n".join(body_lines)
        subject = f"[CD photo sort] {len(self.queued_unmapped_emails)} photo(s) need your eye in _unmapped"
        try:
            g = gmail_helper()
            client = g.GmailAPI()
            for to in NOTIFY_TO:
                client.send(to, subject, body)
        except Exception as e:
            self.log("WARN", "email-failed", err=repr(e)[:200])
        self.summary["emails"] += 1

    def _send_error_email(self, e):
        try:
            g = gmail_helper()
            client = g.GmailAPI()
            for to in ERROR_NOTIFY_TO:
                client.send(to, "[CD photo sort] FATAL error",
                            f"The cd-tom-jobs-photo-sort run failed:\n\n{repr(e)}\n\nLog: {self.log.path}")
        except Exception:
            pass

    def _safe_move_child(self, child_id, new_parent_id, old_parent_id):
        """Move a single child file from one folder to another. Uses urllib
        directly so we can catch HTTPError without sys.exit."""
        d = drive_helper()
        token = d.get_token()
        params = {
            "addParents": new_parent_id,
            "removeParents": old_parent_id,
            "supportsAllDrives": "true",
        }
        url = (f"https://www.googleapis.com/drive/v3/files/{child_id}"
               f"?{urllib.parse.urlencode(params)}")
        req = urllib.request.Request(
            url, data=b"{}", method="PATCH",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req).read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:200]!r}") from e

    def _upload_md(self, parent_id, filename, content_bytes):
        """Create or overwrite a markdown file inside the given parent folder.

        Self-healing for the 'duplicate README accumulation' bug: if a previous
        run hit Drive eventual-consistency and created a second copy when the
        list lookup didn't yet see the first, we end up with N README.mds in
        the same folder. This function now finds ALL files matching the
        filename, keeps the OLDEST (deterministic across runs), PATCHes its
        content, and trashes any duplicates. Counter: `md_dupes_trashed`.
        """
        existing = drive_list_in_folder(parent_id, mime_filter="non-folder")
        matching = [f for f in existing if f["name"] == filename]
        # Deterministic order so every run agrees on which one is the keeper
        matching.sort(key=lambda f: f.get("createdTime", ""))
        existing_md = matching[0] if matching else None
        # Trash any duplicates (siblings of the keeper) to clean up past mess
        if len(matching) > 1:
            for extra in matching[1:]:
                try:
                    if not self.dry_run:
                        drive_trash(extra["id"])
                    self.summary.setdefault("md_dupes_trashed", 0)
                    self.summary["md_dupes_trashed"] += 1
                    self.log("INFO", "duplicate-md-trashed",
                             filename=filename, kept_id=existing_md["id"][:24],
                             trashed_id=extra["id"][:24])
                except Exception as e:
                    self.log("WARN", "duplicate-md-trash-failed",
                             filename=filename, err=repr(e)[:120])
        d = drive_helper()
        if existing_md:
            url = (f"https://www.googleapis.com/upload/drive/v3/files/{existing_md['id']}"
                   f"?uploadType=media&supportsAllDrives=true")
            req = urllib.request.Request(url, data=content_bytes, method="PATCH",
                                         headers={"Authorization": f"Bearer {d.get_token()}",
                                                  "Content-Type": "text/markdown"})
            urllib.request.urlopen(req).read()
        else:
            metadata = {"name": filename, "parents": [parent_id]}
            boundary = "==BOUNDARY=="
            body = (
                f"--{boundary}\r\n"
                f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(metadata)}\r\n"
                f"--{boundary}\r\n"
                f"Content-Type: text/markdown\r\n\r\n"
            ).encode() + content_bytes + f"\r\n--{boundary}--".encode()
            url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true"
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Authorization": f"Bearer {d.get_token()}",
                                                  "Content-Type": f"multipart/related; boundary={boundary}"})
            urllib.request.urlopen(req).read()

    def _write_map_md(self):
        # Build _MAP.md content (root + per-month) and README.md
        managed = drive_find_managed_folders(trashed=False)
        by_month = {}
        for f in managed:
            for p in f.get("parents") or []:
                by_month.setdefault(p, []).append(f)

        # Discover EVERY month folder under tom/ (even ones with no managed
        # folders -- they may have legacy / orphan folders worth surfacing).
        all_month_folders = drive_list_in_folder(TOM_FOLDER_ID, mime_filter="folder")
        month_re_local = re.compile(r"^(\d{2})\s+(\w{3})\s+(\d{2})$")
        all_month_ids = {f["id"]: f["name"] for f in all_month_folders
                         if month_re_local.match(f["name"])}
        # Seed by_month with empty lists for months that have no managed folders
        for mid, mname in all_month_ids.items():
            by_month.setdefault(mid, [])

        # Find orphan (non-managed) job folders inside each month
        orphans_by_month = {}
        for mid, mname in all_month_ids.items():
            try:
                kids = drive_list_in_folder(mid, mime_filter="folder")
                managed_ids = {f["id"] for f in by_month.get(mid, [])}
                orphans = [k for k in kids
                           if k["id"] not in managed_ids
                           and k["name"] != "_unmapped"]
                if orphans:
                    orphans_by_month[mid] = orphans
            except Exception:
                pass

        month_meta = {}
        for mid in by_month:
            if mid in all_month_ids:
                month_meta[mid] = all_month_ids[mid]
            else:
                try:
                    meta = drive_get(mid, fields="name")
                    month_meta[mid] = meta.get("name", "(unknown)")
                except Exception:
                    month_meta[mid] = "(unknown)"

        # Pull all leads' stage info for stage column
        lead_ids = list({(f.get("appProperties") or {}).get("lead_id") for f in managed
                         if (f.get("appProperties") or {}).get("lead_id")})
        stage_by_lead = {}
        if lead_ids:
            try:
                lead_data = odoo_read("crm.lead", [int(x) for x in lead_ids if x.isdigit()],
                                      ["id", "stage_id"])
                for l in lead_data:
                    stage_by_lead[str(l["id"])] = (l.get("stage_id") or [None, "?"])[1]
            except Exception:
                pass

        # Pre-compute per-folder children counts + last-add date so root + per-month
        # _MAP.md don't double-fetch
        children_meta = {}
        for f in managed:
            try:
                ch = drive_list_in_folder(f["id"])
                last_add = ""
                if ch:
                    try:
                        last_add = max(c.get("modifiedTime", "") for c in ch)[:10]
                    except Exception:
                        pass
                children_meta[f["id"]] = (len(ch), last_add)
            except Exception:
                children_meta[f["id"]] = (0, "")

        # Per-month _unmapped photo counts
        unmapped_counts = {}
        for mid in by_month:
            try:
                for sub in drive_list_in_folder(mid, mime_filter="folder"):
                    if sub["name"] == "_unmapped":
                        ucount = len(drive_list_in_folder(sub["id"], mime_filter="non-folder"))
                        unmapped_counts[mid] = ucount
                        break
            except Exception:
                pass

        # ---- Root _MAP.md ----
        root_lines = [
            "# Tom's Jobs Photo Map",
            "",
            f"Auto-managed by `{MANAGED_BY}` -- last updated {now_iso_canary()} Atlantic/Canary.",
            "",
            "Each month folder contains per-job subfolders + a per-month `_unmapped/` "
            "bucket for photos that didn't GPS-match any same-day or ±14d lead. "
            "Drill into any month folder for its own `_MAP.md` slice.",
            "",
            "## Summary",
            f"- Active months: {len(by_month)}",
            f"- Active job folders: {len(managed)}",
            f"- This run: created {self.summary['created']}, renamed {self.summary['renamed']}, "
            f"moved {self.summary['moved']}, swept {self.summary['swept']}",
            f"- Photos moved: {self.summary['photos_moved']}"
            f" (round-2 catch: {self.summary.get('photos_moved_round2', 0)}), "
            f"unmapped: {self.summary['unmapped']}",
            "",
            "## See also",
            "- `README.md` -- plain-English overview of the folder structure",
            "- Each `MM Mon YY/_MAP.md` -- month detail",
            "- Each `MM Mon YY/_unmapped/` -- queue for manual review",
            "",
        ]

        for mid, folders in sorted(by_month.items(),
                                   key=lambda kv: month_meta.get(kv[0], "")):
            mname = month_meta.get(mid, "(unknown month)")
            ucount = unmapped_counts.get(mid, 0)
            root_lines.append(f"## {mname}")
            root_lines.append("")
            root_lines.append(f"_{len(folders)} job folders · {ucount} unmapped · "
                              f"see `{mname}/_MAP.md` for detail._")
            root_lines.append("")
            root_lines.append("| Folder | Stage | Items | Last add | Lead |")
            root_lines.append("|---|---|---:|---|---|")
            for f in sorted(folders, key=lambda x: x["name"]):
                ap = f.get("appProperties") or {}
                lead_id = ap.get("lead_id", "?")
                stage = stage_by_lead.get(lead_id, "?")
                items, last_add = children_meta.get(f["id"], (0, ""))
                lead_link = f"[{lead_id}]({ODOO_BASE}/odoo/crm/{lead_id})"
                root_lines.append(f"| {f['name']} | {stage} | {items} | {last_add} | {lead_link} |")
            root_lines.append("")

        root_content = "\n".join(root_lines).encode("utf-8")

        # ---- Per-month _MAP.md ----
        per_month_writes = []
        for mid, folders in by_month.items():
            mname = month_meta.get(mid, "(unknown month)")
            ucount = unmapped_counts.get(mid, 0)
            orphans = orphans_by_month.get(mid, [])
            # Skip months with absolutely no content (empty future months)
            if not folders and not orphans and not ucount:
                continue
            mlines = [
                f"# {mname}",
                "",
                f"Slice of [Tom's Jobs Photo Map](../{MAP_MD_NAME}) covering this month only.",
                f"Auto-managed by `{MANAGED_BY}` -- last updated {now_iso_canary()} Atlantic/Canary.",
                "",
                "## Summary",
                f"- Managed job folders this month: {len(folders)}",
                f"- Photos in `_unmapped/` (this month): {ucount}",
            ]
            if orphans:
                mlines.append(f"- Legacy / unmanaged folders: {len(orphans)}")
            mlines.append("")

            if folders:
                mlines.append("## Managed job folders")
                mlines.append("")
                mlines.append("| Folder | Stage | Items | Last add | Lead |")
                mlines.append("|---|---|---:|---|---|")
                for f in sorted(folders, key=lambda x: x["name"]):
                    ap = f.get("appProperties") or {}
                    lead_id = ap.get("lead_id", "?")
                    stage = stage_by_lead.get(lead_id, "?")
                    items, last_add = children_meta.get(f["id"], (0, ""))
                    lead_link = f"[{lead_id}]({ODOO_BASE}/odoo/crm/{lead_id})"
                    mlines.append(f"| {f['name']} | {stage} | {items} | {last_add} | {lead_link} |")
                mlines.append("")

            if orphans:
                mlines.append("## Legacy / unmanaged folders")
                mlines.append("")
                mlines.append(f"_{len(orphans)} folder(s) in this month that the script doesn't "
                              "currently manage (no `managed_by` appProperty -- typically "
                              "pre-script archival folders). Run `--first-run` against the "
                              "wider date window to attempt adoption, or leave them as-is._")
                mlines.append("")
                mlines.append("| Folder | Items |")
                mlines.append("|---|---:|")
                for f in sorted(orphans, key=lambda x: x["name"]):
                    try:
                        kids = drive_list_in_folder(f["id"])
                        items = len(kids)
                    except Exception:
                        items = "?"
                    mlines.append(f"| {f['name']} | {items} |")
                mlines.append("")

            mlines.append("## Unmapped queue")
            mlines.append("")
            if ucount:
                mlines.append(f"`_unmapped/` contains {ucount} photo(s) that didn't GPS-match a "
                              f"same-day (±1d) or wider-window (±14d) lead. Drag any photo "
                              f"into the correct job folder manually if you can identify it.")
            else:
                mlines.append("Empty -- every photo for this month was matched.")
            mlines.append("")
            per_month_writes.append((mid, ("\n".join(mlines)).encode("utf-8")))

        # ---- README.md (plain-English overview) ----
        readme_lines = [
            "# Tom's Jobs photo folder",
            "",
            "Field shots from Tom's tablet land here. A scheduled task "
            f"(`{MANAGED_BY}`) runs every evening at 18:00 (Atlantic/Canary) "
            "and sorts them into per-job subfolders, organised by month.",
            "",
            "## How it works",
            "",
            "Every photo Tom takes has an embedded **GPS location** and **capture timestamp** "
            "(EXIF). The script reads those, looks up Tom's calendar in Odoo, and matches each "
            "photo to the lead whose property is closest in space (≤1km) and time (±1 day "
            "first, then ±14 days for any leftovers).",
            "",
            "If a photo can't be matched it lands in that month's `_unmapped/` folder for "
            "manual review.",
            "",
            "## Folder structure",
            "",
            "```",
            "tom/",
            "├── README.md            <- you are here",
            "├── _MAP.md              <- master index, all months",
            "├── 03 Mar 26/",
            "│   ├── _MAP.md          <- this month's job folders + unmapped count",
            "│   ├── _unmapped/       <- photos this month that didn't GPS-match",
            "│   ├── Repair - Customer A - PB - 2026-03-12/",
            "│   └── VLS - Customer B - PDC - 2026-03-15/",
            "├── 04 Apr 26/",
            "│   └── ...",
            "└── 05 May 26/",
            "    └── ...",
            "```",
            "",
            "## Folder colours",
            "",
            "- **Orange** -- folder has photos in it",
            "- **Grey** -- folder is currently empty (or a system folder like `_unmapped`)",
            "",
            "## What if photos are filed wrong?",
            "",
            "Drag the photo to the correct job folder yourself. The script never moves a "
            "photo OUT of a job folder it's already in -- once filed, it stays put.",
            "",
            "If a folder name is wrong, ask Pete to fix the lead's address in Odoo, then the "
            "next sync will pick up the correct geocode.",
            "",
            "## Don't manually rename folders",
            "",
            "If you rename a folder by hand, the script detects this (via the `last_known_name` "
            "appProperty) and stops auto-updating that folder's name. To restore auto-management, "
            "ask Pete to refresh the appProperty.",
            "",
            "## Maintenance",
            "",
            "- Spec: `Library/processes/tom-jobs-photo-workflow.md` in Pete's vault",
            f"- Source: `Library/processes/scripts/cd-tom-jobs-photo-sort.py`",
            f"- Last run: {now_iso_canary()} Atlantic/Canary",
            "",
            "## Sister task",
            "",
            "Tom's Google Calendar is also kept in sync with Odoo by "
            "`cd-tom-jobs-calendar-sync` (twice daily, 12:30 + 18:00). Same enrichment "
            "(customer / address / Maps link / lead notes / colour-coded by job type).",
            "",
        ]
        readme_content = "\n".join(readme_lines).encode("utf-8")

        if self.dry_run:
            self.log("INFO", "would-write-map", chars=len(root_content),
                     per_month_files=len(per_month_writes))
            return

        # Write root _MAP.md + README.md + per-month _MAP.md files
        self._upload_md(TOM_FOLDER_ID, MAP_MD_NAME, root_content)
        self.log("INFO", "map-written", chars=len(root_content))

        self._upload_md(TOM_FOLDER_ID, "README.md", readme_content)
        self.log("INFO", "readme-written", chars=len(readme_content))

        for mid, mcontent in per_month_writes:
            try:
                self._upload_md(mid, MAP_MD_NAME, mcontent)
            except Exception as e:
                self.log("WARN", "per-month-map-failed",
                         month=month_meta.get(mid, "?"), err=repr(e)[:120])
        self.log("INFO", "per-month-maps-written", count=len(per_month_writes))

    def _persist_run_state(self, status="ok"):
        if self.dry_run:
            return
        summary_str = (
            f"created={self.summary['created']} renamed={self.summary['renamed']} "
            f"moved={self.summary['moved']} swept={self.summary['swept']} "
            f"photos={self.summary['photos_moved']} unmapped={self.summary['unmapped']}"
        )
        try:
            drive_set_app_properties(TOM_FOLDER_ID, {
                "last_run_at": now_iso_canary(),
                "last_run_status": status,
                "last_run_summary": summary_str[:120],
            })
        except Exception:
            pass
        # Append to vault Daily note
        try:
            vault_daily = Path.home() / "Second Brain" / "Daily" / f"{today_canary().isoformat()}.md"
            if vault_daily.exists():
                with vault_daily.open("a", encoding="utf-8") as f:
                    f.write("\n## CD Tom Jobs Photo Sort (Automated)\n")
                    f.write(f"- Run at {now_iso_canary()}\n")
                    f.write(f"- Folders: created {self.summary['created']}, renamed {self.summary['renamed']}, "
                            f"moved {self.summary['moved']}, swept {self.summary['swept']}\n")
                    f.write(f"- Photos: moved {self.summary['photos_moved']}, unmapped {self.summary['unmapped']}\n")
                    f.write(f"- Status: {status}\n")
        except Exception as e:
            self.log("WARN", "daily-note-write-failed", err=repr(e)[:120])

# ---------------------------------------------------------------------------
# Phase 0 (first-run only) -- adopt existing folders + cleanup leftovers
# ---------------------------------------------------------------------------

def first_run_phase_0(dry_run=False):
    """Adopt April folders that lack appProperties, set managed_by, cleanup leftover non-folder-worthy folders."""
    log = Log(dry_run=dry_run)
    log("INFO", "phase-0-start", dry_run=dry_run)

    # Step 1: list every existing month folder + its subfolders
    months = drive_list_in_folder(TOM_FOLDER_ID, mime_filter="folder")
    month_re = re.compile(r"^(\d{2})\s+(\w{3})\s+(\d{2})$")  # NN Mon YY
    month_folders = [m for m in months if month_re.match(m["name"])]
    log("INFO", "months-found", count=len(month_folders))

    # Step 2: pull Odoo events for the broadest reasonable window (180 days back)
    domain = [
        ["partner_ids", "in", [TOM_PARTNER_ID]],
        ["start", ">=", utc_iso(today_canary() - dt.timedelta(days=180))],
        ["start", "<=", utc_iso(today_canary() + dt.timedelta(days=14))],
    ]
    events = odoo_search_read("calendar.event", domain,
                              ["id", "name", "start", "stop", "location",
                               "opportunity_id", "active"], limit=2000)
    events = [e for e in events if e.get("active") is not False]
    log("INFO", "events-pulled", count=len(events))

    # Group events by lead
    leads_by_id = {}
    for ev in events:
        opp = ev.get("opportunity_id")
        if opp:
            leads_by_id.setdefault(opp[0], []).append(ev)
    lead_objs = odoo_read("crm.lead", list(leads_by_id.keys()),
                          ["id", "name", "partner_id", "partner_name", "contact_name",
                           "x_studio_char_field_3qWjM",  # Location (Survey)
                           "stage_id", "calendar_event_ids"])
    lead_obj_by_id = {l["id"]: l for l in lead_objs}

    # Step 3: walk each subfolder, adopt or cleanup
    adopted = 0
    cleaned = 0
    skipped = 0
    for month in month_folders:
        for sub in drive_list_in_folder(month["id"], mime_filter="folder"):
            ap = sub.get("appProperties") or {}
            if ap.get("managed_by") == MANAGED_BY:
                continue  # already managed

            # Try to match by name to a lead
            matched_lead_id = match_name_to_lead(sub["name"], lead_obj_by_id, leads_by_id)
            if matched_lead_id:
                # Adopt
                lead = lead_obj_by_id[matched_lead_id]
                folder_worthy = [e for e in leads_by_id[matched_lead_id]
                                 if is_folder_worthy(classify_jobtype(e))]
                if not folder_worthy:
                    # Could be a non-folder-worthy job that got a folder
                    if not dry_run:
                        drive_trash(sub["id"])
                    log("INFO", "cleanup-non-worthy", name=sub["name"])
                    cleaned += 1
                    continue
                partner = lead.get("partner_id") or [None, ""]
                props = {
                    "managed_by":      MANAGED_BY,
                    "lead_id":         str(matched_lead_id),
                    "event_ids":       ",".join(str(e["id"]) for e in leads_by_id[matched_lead_id]),
                    "jobtypes":        ",".join(_dedupe(classify_jobtype(e) for e in folder_worthy)),
                    "customer":        partner[1] if partner[1] else (lead.get("partner_name") or ""),
                    "contact_name":    lead.get("contact_name") or "",
                    "location":        extract_location_abbrev(folder_worthy),
                    "earliest_event_date": min(event_canary_date(e).isoformat() for e in folder_worthy),
                    "latest_event_date":   max(event_canary_date(e).isoformat() for e in folder_worthy),
                    "created_at":      now_iso_canary(),
                    "last_updated":    now_iso_canary(),
                    "last_known_name": name_signature(sub["name"]),
                    "adopted_at":      now_iso_canary(),
                }
                # drive_set_app_properties auto-trims to 124-byte key+value limit
                if not dry_run:
                    drive_set_app_properties(sub["id"], props)
                adopted += 1
                log("INFO", "adopted", name=sub["name"][:60], lead_id=matched_lead_id)
            else:
                # Could not match -- check if it's a known leftover non-folder-worthy
                lower = sub["name"].lower()
                if any(kw in lower for kw in [" - pump - ", " - civils - ", " - site clear - ",
                                              " - admin - ", " - leakguard check - ",
                                              " - ecofinish - ", " - epoxy - "]):
                    # cleanup
                    children = drive_list_in_folder(sub["id"])
                    if not children:
                        if not dry_run:
                            drive_trash(sub["id"])
                        log("INFO", "cleanup-leftover", name=sub["name"][:60])
                        cleaned += 1
                    else:
                        log("WARN", "leftover-has-content", name=sub["name"][:60], items=len(children))
                else:
                    skipped += 1
                    log("WARN", "could-not-adopt", name=sub["name"][:60])

    log("INFO", "phase-0-summary", adopted=adopted, cleaned=cleaned, skipped=skipped)
    log.close()
    return adopted, cleaned, skipped

def _dedupe(seq):
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out

def match_name_to_lead(folder_name, lead_obj_by_id, leads_by_id):
    """Try to match a folder name like 'VLS - Customer Name - Loc - 2026-04-01' to a lead."""
    # Extract date suffix
    m = re.search(r"(\d{4}-\d{2}-\d{2})$", folder_name)
    target_date = None
    if m:
        try:
            target_date = dt.date.fromisoformat(m.group(1))
        except Exception:
            pass

    # Score each lead
    best = None
    best_score = 0
    folder_lower = folder_name.lower()
    for lead_id, lead in lead_obj_by_id.items():
        score = 0
        partner = (lead.get("partner_id") or [None, ""])[1] or ""
        if partner and partner.lower() in folder_lower:
            score += 5
        contact = lead.get("contact_name") or ""
        if contact and contact.lower() in folder_lower:
            score += 3
        # Date match
        if target_date:
            for ev in leads_by_id.get(lead_id, []):
                if event_canary_date(ev) == target_date:
                    score += 4
                    break
        if score > best_score and score >= 5:
            best_score = score
            best = lead_id
    return best

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan only; no Drive writes")
    parser.add_argument("--first-run", action="store_true",
                        help="Run Phase 0 (adoption + leftover cleanup) BEFORE the standard loop")
    args = parser.parse_args()

    if args.first_run:
        first_run_phase_0(dry_run=args.dry_run)

    runner = Runner(dry_run=args.dry_run)
    runner.run()

# --- Automations Log heartbeat (added 11 Jun 2026; non-fatal) ---
def _cc_pulse(summary: str):
    try:
        import sys as _s
        _s.path.insert(0, str(SCRIPTS_DIR))  # flat-repo sibling on Railway (/app); Library/.../scripts locally
        import cc_publish
        cc_publish.pulse("cd-tom-jobs-photo-sort", summary)
    except Exception:
        pass


if __name__ == "__main__":
    main()
    _cc_pulse("run completed")
