#!/usr/bin/env python3
"""cc-agent-worker.py — the Command Centre's 24/7 cloud agent (Business OS Part F).

A long-running worker that claims jobs off `public.agent_jobs`, runs each through Claude
(reading the CC as its source of truth), and writes the result back to the CC. This is the
keystone the cron migration (H) and the Telegram bridge (G) build on: they ENQUEUE jobs,
this RUNS them.

Runs anywhere — locally for testing, or 24/7 on Railway. Config is env-first (so
railway-bootstrap.py / Railway env vars drive it) with a fall-back to the vault secret files.

Usage:
  cc-agent-worker.py            # 24/7 loop: poll, claim, run, repeat
  cc-agent-worker.py --once     # claim + run a single job then exit (forced-fire test)
  cc-agent-worker.py --drain    # run all currently-pending jobs then exit
"""
import json, os, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
    _CANARY = ZoneInfo("Atlantic/Canary")
except Exception:
    _CANARY = None
# Pete is in Lanzarote (Atlantic/Canary). Railway runs UTC by default, so the service sets TZ
# AND we compute the time explicitly here — the agent must reason about dates/times in Pete's tz,
# never UTC, or "today" / "overdue" / "due now" come out wrong.
if os.environ.get("TZ"):
    try: time.tzset()
    except Exception: pass

def now_canary():
    if _CANARY:
        return datetime.now(_CANARY).strftime("%A %d %B %Y, %H:%M %Z")
    return datetime.now().strftime("%A %d %B %Y, %H:%M (local)")

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"

def _secret_file(name):
    try:
        return open(f"{SEC}/{name}").read().strip()
    except Exception:
        return None

def _cc_creds():
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        blob = _secret_file("command-centre-supabase-keys.json")
        if blob:
            k = json.loads(blob); url = url or k["url"]; key = key or k["service_role_key"]
    return url, key

SB_URL, SB_KEY = _cc_creds()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY") or _secret_file("anthropic-api-key")
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")  # fast + high-throughput default; Opus is 5 req/min on this org. Per-job model override still works.
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "4096"))
POLL_SECS = int(os.environ.get("AGENT_POLL_SECS", "5"))
IDLE_LOG_EVERY = int(os.environ.get("AGENT_IDLE_LOG_EVERY", "120"))  # log a heartbeat every N idle polls
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "12"))             # cap tool-use iterations per job

SYSTEM = (
    "You are the Command Centre cloud agent for Pete Ashcroft's businesses — a 24/7 Claude "
    "running on the always-on server. Your source of truth is the Command Centre (CC Supabase). "
    "Pete is based in Lanzarote and operates in the Atlantic/Canary timezone — reason about all dates "
    "and times in that timezone, never UTC. Be accurate and concise (British English); if unsure, say so "
    "rather than guessing. Tools: cc_read (read-only SQL over the WHOLE CC), cc_search (full-text knowledge "
    "search), cc_write (create/update/delete CC rows). Ground every answer in LIVE data via cc_read/cc_search; "
    "use cc_write to actually carry out work. Prefer doing over describing.\n\n"
    "HOW THE COMMAND CENTRE IS MODELLED (v2 — match these names/rules exactly when you read or write):\n"
    "• TASKS = public.tasks (Pete's work — he is OFF Asana; never route his tasks to Asana). Key columns: "
    "name, priority, due_on (date), entity_slug, project_slug, bucket, tags[] (text array), notes (free text — "
    "often holds the source email/Mimestream link), status ('todo'=open / 'done'), completed_at. Open tasks = "
    "status='todo'. PRIORITY IS MANUAL — P1=do now, P2=this week, P3=this month, P4=someday; PD='hard deadline' "
    "(stored as a dated P1, must have a due_on). To complete a task: set status='done', completed_at=now(). To "
    "add one: insert name + entity_slug + priority (+ due_on for P1–P3/PD).\n"
    "• PROJECTS = public.projects (slug, name, entity_slug, status active/archived, drive_folder_url). BUCKETS = "
    "public.buckets (project_slug, name) — sub-groups inside a project; every project has a default 'General' "
    "bucket. A task joins a project via project_slug and a bucket via its bucket column. ENTITIES (entity_slug): "
    "Sygma, Canary Detect, Personal, One System, El Atico. To stand up a NEW project properly (row + bucket + "
    "Drive folder + knowledge home), prefer the cc-project-api.py helper over a bare insert.\n"
    "• TAGS = tasks.tags[] — free labels; tag 'reply' means the task is gating an email reply to Pete.\n"
    "• QUICK NOTES = public.notes (title, body, pinned, colour, tags[], status open/archived) — Pete's Keep-style "
    "scratchpad. This is DISTINCT from vault_notes (the curated brain). When Pete says 'note: …', insert a row "
    "into public.notes — do NOT confuse it with vault_notes.\n"
    "• CALENDAR / EVENTS — Pete's Google Calendar is the SOURCE OF TRUTH. To SCHEDULE / MOVE / CANCEL an event "
    "use the calendar_create / calendar_update / calendar_delete tools — they write to Google Calendar at Pete's "
    "Atlantic/Canary local time and sync into the CC automatically. public.calendar_events is a READ-ONLY MIRROR "
    "(read it, or use calendar_list, for 'what's on') — NEVER write to it: a row written there isn't in Google, "
    "shows the wrong time, and is wiped on the next sync. A time Pete gives is his LOCAL Lanzarote time — pass it "
    "as 'YYYY-MM-DDTHH:MM' (or a bare 'YYYY-MM-DD' for all-day) and the tool handles the timezone. To move/cancel, "
    "first calendar_list to get the event's id, then calendar_update/delete it.\n"
    "• KNOWLEDGE = vault_notes (lessons/decisions/processes, semantic + full-text); AUTOMATIONS = crons; "
    "FILE INDEX = drive_files; for 'where does X live' consult DATA HOMES = data_map.\n"
    "MEMORY: the recent Telegram conversation is provided as prior messages — use it for context (follow-ups, "
    "pronouns like 'that meeting', 'move it', 'them'). Don't claim you have no memory; refer back when relevant.\n\n"
    "FRESHNESS & CORRECTNESS OF FACTS (critical): a single fact — a cabin number, date, price, address, status — "
    "has ONE current value. (1) READING: if cc_search, cc_read or the prior conversation give you DATED or "
    "CONFLICTING values for the same fact, trust the most recent value or the one explicitly marked "
    "'confirmed'/'now confirmed'; treat earlier 'TBC'/'guarantee'/provisional entries as SUPERSEDED; and if you "
    "cannot tell which is current, say so rather than quoting a possibly-stale one. (2) WRITING with cc_write: "
    "state a changing fact ONCE — REPLACE the old value in place; never append a line that contradicts an existing "
    "one, and if a note already holds an out-of-date value for what you are updating, remove or correct that line "
    "in the same write. Appending contradictions is exactly what makes the brain surface dead values."
)

def sb(method, path, body=None, prefer=None):
    url = f"{SB_URL}/rest/v1/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("apikey", SB_KEY); req.add_header("Authorization", f"Bearer {SB_KEY}")
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    with urllib.request.urlopen(req, timeout=60) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else None

def claim_job():
    """Atomically claim the oldest pending job (or None). RPC handles the skip-locked race."""
    j = sb("POST", "rpc/claim_agent_job", body={})
    if isinstance(j, list):
        j = j[0] if j else None
    return j if (j and j.get("id")) else None

# ───────────────────────── the agent's hands (tools) ─────────────────────────
# Pete wants maximal access on his owner system. READ = everything via cc_read (read-only enforced in
# the DB). WRITE = any data table EXCEPT credentials/access-control, and update/delete MUST target rows
# (no mass wipes). External world (email/Drive/calendar send) is the next, human-gated layer — not here.
# calendar_events is a READ-ONLY mirror of Google Calendar — writing there bypasses gcal, shows the
# wrong time, and gets wiped on the next sync. Force scheduling through the calendar_create/update/delete tools.
WRITE_DENY = {"secrets", "profiles", "grants", "group_grants", "module_grants", "groups", "calendar_events"}

TOOLS = [
    {"name": "cc_read",
     "description": ("Run a READ-ONLY SQL query (SELECT or WITH only) against the Command Centre Postgres "
                     "and get rows back as JSON. Your window onto the source of truth: tasks, vault_notes "
                     "(knowledge), crons, data_map, drive_files (the ~150k-file index), and every other "
                     "table. Add a LIMIT when a query might return many rows."),
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "a single SELECT/WITH statement"}}, "required": ["query"]}},
    {"name": "cc_search",
     "description": ("Full-text search across the knowledge base (vault_notes) — Pete's notes, lessons, "
                     "decisions and processes. Returns the most relevant notes. Use for 'what do we know about X'."),
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "limit": {"type": "integer", "description": "max results, default 8"}},
         "required": ["query"]}},
    {"name": "cc_write",
     "description": ("Create, update or delete rows in the Command Centre to DO work — create or complete a "
                     "task, save a note/memory, etc. Works on any data table EXCEPT credentials/access "
                     "tables. For update/delete you MUST pass a 'match' filter (you cannot change every row "
                     "at once)."),
     "input_schema": {"type": "object", "properties": {
         "table": {"type": "string"}, "op": {"type": "string", "enum": ["insert", "update", "delete"]},
         "data": {"type": "object", "description": "column→value (insert/update)"},
         "match": {"type": "object", "description": "column→value filter — REQUIRED for update/delete"}},
         "required": ["table", "op"]}},
]

# cc_read reads note BODIES (a fact can sit deep in a long note, e.g. char ~9k in the cruise note), so it
# gets a much larger ceiling than the other tools; and every truncation carries an explicit marker so the
# model knows output was cut (silent truncation is what hid the confirmed cabin behind the 8k window).
CC_READ_CAP = 40000

def _cap_marked(s, n, hint="narrow the query / add LIMIT"):
    """Truncate to n chars with an explicit marker — never silent."""
    s = str(s)
    if len(s) <= n:
        return s
    m = f"\n…[truncated at {n} chars — {hint}]"
    return s[:max(0, n - len(m))] + m

def t_cc_read(a):
    try:
        return _cap_marked(json.dumps(sb("POST", "rpc/cc_read", body={"q": (a.get("query") or "").strip()})), CC_READ_CAP)
    except urllib.error.HTTPError as e:
        return f"ERROR: {e.read().decode()[:300]}"

def t_cc_search(a):
    try:
        rows = sb("POST", "rpc/search_notes", body={"q": a.get("query") or "", "lim": int(a.get("limit") or 8)}) or []
        # NOTE: search_notes returns a 'snippet' column only, so the body/content slice below is inert today.
        # If search_notes is ever widened to return full bodies, add an explicit truncation marker here too.
        slim = [{k: (v[:400] if isinstance(v, str) and k in ("body", "content") else v) for k, v in r.items()} for r in rows]
        return _cap_marked(json.dumps(slim), 8000, "refine the search / lower limit")
    except urllib.error.HTTPError as e:
        return f"ERROR: {e.read().decode()[:300]}"

def t_cc_write(a):
    table = (a.get("table") or "").strip().lower(); op = a.get("op")
    data = a.get("data") or {}; match = a.get("match") or {}
    if table == "calendar_events":
        return ("ERROR: calendar_events is a READ-ONLY mirror of Google Calendar. To schedule/move/cancel an "
                "event use the calendar_create / calendar_update / calendar_delete tools instead (they write to "
                "Google Calendar at Pete's local time and sync back to the CC).")
    if table in WRITE_DENY:
        return f"ERROR: '{table}' is a credentials/access table — read-only to the agent."
    if op in ("update", "delete") and not match:
        return "ERROR: update/delete require a 'match' filter (no mass operations)."
    try:
        if op == "insert":
            return f"inserted: {json.dumps(sb('POST', table, body=[data], prefer='return=representation'))[:1000]}"
        qs = "&".join(f"{k}=eq.{urllib.parse.quote(str(v))}" for k, v in match.items())
        if op == "update":
            r = sb("PATCH", f"{table}?{qs}", body=data, prefer="return=representation")
            return f"updated {len(r or [])} row(s): {json.dumps(r)[:800]}"
        if op == "delete":
            r = sb("DELETE", f"{table}?{qs}", prefer="return=representation")
            return f"deleted {len(r or [])} row(s)"
        return "ERROR: unknown op"
    except urllib.error.HTTPError as e:
        return f"ERROR: {e.read().decode()[:300]}"

TOOL_FN = {"cc_read": t_cc_read, "cc_search": t_cc_search, "cc_write": t_cc_write}

# ───────── external-world tools (Pete-authorised 23 Jun) — wrap the existing helper scripts ─────────
import importlib.util as _ilu
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
_HCACHE = {}
def _helper(fname, modname):
    if modname in _HCACHE: return _HCACHE[modname]
    spec = _ilu.spec_from_file_location(modname, os.path.join(_SCRIPTS, fname))
    m = _ilu.module_from_spec(spec); spec.loader.exec_module(m); _HCACHE[modname] = m; return m

EMAIL_LIVE = os.environ.get("AGENT_EMAIL_LIVE", "0") == "1"   # safety: off → sends route to Pete only
# Tool surface (23 Jun): cc_read/search/write · gmail_search/send(gated) · calendar_list · odoo_query · ga4_query · gsc_query
PETE = "pete.ashcroft@sygma-solutions.com"

# On Railway the repo is flat (helpers at REPO root) and bootstrap materialises the SA key at
# REPO/Library/processes/secrets/. The helpers' default KEY_PATH (dirname(__file__)/../secrets) only
# resolves correctly in the vault layout, so pass the Railway path explicitly when it exists.
_SA_RAILWAY = os.path.join(_SCRIPTS, "Library", "processes", "secrets", "google-seo-service-account.json")
_sa_env = os.environ.get("GOOGLE_SA_JSON")                    # clean env-var name (Railway rejects dots/hyphens)
if _sa_env and not os.path.exists(_SA_RAILWAY):              # materialise the SA key on the container at startup
    os.makedirs(os.path.dirname(_SA_RAILWAY), exist_ok=True)
    with open(_SA_RAILWAY, "w") as _f: _f.write(_sa_env)
def _google_kwargs():
    return {"key_path": _SA_RAILWAY} if os.path.exists(_SA_RAILWAY) else {}

def t_gmail_search(a):
    try:
        g = _helper("gmail-api.py", "gmail_api").GmailAPI(**_google_kwargs())
        ths = g.search_threads(a.get("query", ""), max_results=int(a.get("max", 10)))
        return json.dumps([{"id": t.get("id"), "snippet": (t.get("snippet") or "")[:200]} for t in (ths or [])])[:8000]
    except Exception as e:
        return f"ERROR gmail_search: {e}"

def t_gmail_send(a):
    try:
        g = _helper("gmail-api.py", "gmail_api").GmailAPI(**_google_kwargs())
        to = a.get("to") or PETE; subj = a.get("subject", ""); body = a.get("body", "")
        if not EMAIL_LIVE:                                   # route to Pete + tag until AGENT_EMAIL_LIVE=1
            subj = f"[TEST→would-send to {to}] {subj}"; to = PETE
        r = g.send(to=to, subject=subj, body=body, html=a.get("html"))
        return f"sent (live={EMAIL_LIVE}) to {to}: {json.dumps(r)[:300]}"
    except Exception as e:
        return f"ERROR gmail_send: {e}"

def _canary_offset():
    """Pete's current Atlantic/Canary UTC offset as '+01:00'/'+00:00' (DST-aware), or 'Z' fallback."""
    if not _CANARY: return "Z"
    o = datetime.now(_CANARY).strftime("%z")            # e.g. +0100
    return (o[:3] + ":" + o[3:]) if o else "Z"

def _rfc3339(s):
    if not s: return s
    if "T" not in s: s = s + "T00:00:00"                      # bare date (YYYY-MM-DD) → start-of-day…
    tail = s[11:]
    if tail.endswith("Z") or "+" in tail or "-" in tail: return s
    off = _canary_offset()                                    # …in PETE'S timezone, not UTC (so 'today' boundaries are local)
    return s + (off if off != "Z" else "Z")

# build Google start/end dicts from local (Atlantic/Canary) date or 'YYYY-MM-DDTHH:MM' strings
def _gcal_when(start_raw, end_raw, dur_min):
    if "T" not in start_raw:                                  # all-day event (bare dates)
        return {"date": start_raw}, {"date": (end_raw or start_raw)}
    sdt = datetime.strptime(start_raw[:16], "%Y-%m-%dT%H:%M")
    if _CANARY: sdt = sdt.replace(tzinfo=_CANARY)
    if end_raw and "T" in end_raw:
        edt = datetime.strptime(end_raw[:16], "%Y-%m-%dT%H:%M")
        if _CANARY: edt = edt.replace(tzinfo=_CANARY)
    else:
        edt = sdt + timedelta(minutes=dur_min)
    return ({"dateTime": sdt.isoformat(), "timeZone": "Atlantic/Canary"},
            {"dateTime": edt.isoformat(), "timeZone": "Atlantic/Canary"})

def t_calendar_list(a):
    try:
        c = _helper("calendar-api.py", "calendar_api").CalendarAPI(**_google_kwargs())
        evs = c.list_events(calendar_id=a.get("calendar", "primary"), time_min=_rfc3339(a.get("from")), time_max=_rfc3339(a.get("to")))
        return json.dumps([{"id": e.get("id"), "summary": e.get("summary"), "start": e.get("start"), "end": e.get("end"), "location": e.get("location")} for e in (evs or [])][:50])[:8000]
    except Exception as e:
        return f"ERROR calendar_list: {e}"

def t_calendar_create(a):
    try:
        c = _helper("calendar-api.py", "calendar_api").CalendarAPI(**_google_kwargs())
        title = (a.get("title") or a.get("summary") or "").strip()
        start_raw = (a.get("start") or "").strip()
        if not title or not start_raw:
            return "ERROR: need 'title' + 'start' ('YYYY-MM-DD' for all-day, or 'YYYY-MM-DDTHH:MM' in Pete's local time)."
        start, end = _gcal_when(start_raw, (a.get("end") or "").strip(), int(a.get("duration_minutes", 30)))
        ev = {"summary": title, "start": start, "end": end}
        if a.get("location"): ev["location"] = a["location"]
        if a.get("description"): ev["description"] = a["description"]
        r = c.create_event(a.get("calendar", "primary"), ev) or {}
        when = (r.get("start") or {}).get("dateTime") or (r.get("start") or {}).get("date")
        return f"✓ created in Google Calendar: '{r.get('summary')}' @ {when} (id {str(r.get('id',''))[:26]}). Syncs into the CC automatically."
    except Exception as e:
        return f"ERROR calendar_create: {e}"

def t_calendar_update(a):
    try:
        c = _helper("calendar-api.py", "calendar_api").CalendarAPI(**_google_kwargs())
        eid = (a.get("event_id") or "").strip()
        if not eid: return "ERROR: need 'event_id' — calendar_list first to find it."
        fields = {}
        if a.get("title"): fields["summary"] = a["title"]
        if a.get("location") is not None: fields["location"] = a["location"]
        if a.get("description") is not None: fields["description"] = a["description"]
        if a.get("start"):
            fields["start"], fields["end"] = _gcal_when((a.get("start") or "").strip(), (a.get("end") or "").strip(), int(a.get("duration_minutes", 30)))
        if not fields: return "ERROR: nothing to change (pass title/start/location)."
        r = c.update_event(eid, calendar_id=a.get("calendar", "primary"), **fields) or {}
        when = (r.get("start") or {}).get("dateTime") or (r.get("start") or {}).get("date")
        return f"✓ updated Google Calendar event: '{r.get('summary')}' @ {when}."
    except Exception as e:
        return f"ERROR calendar_update: {e}"

def t_calendar_delete(a):
    try:
        c = _helper("calendar-api.py", "calendar_api").CalendarAPI(**_google_kwargs())
        eid = (a.get("event_id") or "").strip()
        if not eid: return "ERROR: need 'event_id' — calendar_list first to find it."
        c.delete_event(eid, calendar_id=a.get("calendar", "primary"))
        return f"✓ cancelled Google Calendar event {eid[:24]}."
    except Exception as e:
        return f"ERROR calendar_delete: {e}"

def t_odoo_query(a):
    try:
        o = _helper("odoo-api.py", "odoo_api")
        rows = o._execute(a.get("model"), "search_read", [a.get("domain", [])], {"fields": a.get("fields"), "limit": int(a.get("limit", 50))})
        return json.dumps(rows)[:8000]
    except (Exception, SystemExit) as e:                      # odoo_api sys.exit()s at import if config absent
        return f"ERROR odoo_query: {e}"

def t_ga4_query(a):
    try:
        api = _helper("ga4-api.py", "ga4_api").GA4API(**_google_kwargs())
        rows = api.run_report(a.get("property_id"), a.get("dimensions", []), a.get("metrics", ["sessions"]),
                              days=int(a.get("days", 7)), limit=int(a.get("limit", 20)))
        return json.dumps(rows, default=str)[:8000]
    except Exception as e:
        return f"ERROR ga4_query: {e}"

def t_gsc_query(a):
    try:
        api = _helper("gsc-api.py", "gsc_api").GSCAPI(**_google_kwargs())
        rows = api.query(a.get("site"), a.get("dimensions", ["query"]), date_range=int(a.get("date_range", 28)), limit=int(a.get("limit", 25)))
        return json.dumps(rows, default=str)[:8000]
    except Exception as e:
        return f"ERROR gsc_query: {e}"

TOOL_FN.update({"gmail_search": t_gmail_search, "gmail_send": t_gmail_send, "calendar_list": t_calendar_list,
                "calendar_create": t_calendar_create, "calendar_update": t_calendar_update, "calendar_delete": t_calendar_delete,
                "odoo_query": t_odoo_query, "ga4_query": t_ga4_query, "gsc_query": t_gsc_query})
TOOLS += [
    {"name": "ga4_query", "description": "Query Google Analytics 4 (runReport) for a property. property_id = numeric GA4 id; metrics e.g. ['sessions','conversions','totalUsers']; dimensions e.g. ['date']. days = lookback. READ — safe.",
     "input_schema": {"type": "object", "properties": {"property_id": {"type": "string"}, "dimensions": {"type": "array"}, "metrics": {"type": "array"}, "days": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["property_id"]}},
    {"name": "gsc_query", "description": "Query Google Search Console search-analytics for a site. site = the GSC property URL (e.g. 'sc-domain:sygma-solutions.com'); dimensions e.g. ['query'] / ['page']. READ — safe.",
     "input_schema": {"type": "object", "properties": {"site": {"type": "string"}, "dimensions": {"type": "array"}, "date_range": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["site"]}},
    {"name": "gmail_search", "description": "Search Pete's Gmail (Gmail query syntax, e.g. 'is:unread', 'from:x newer_than:7d'). Returns thread snippets. READ — safe.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max": {"type": "integer"}}, "required": ["query"]}},
    {"name": "gmail_send", "description": "Send an email as Pete. SAFETY: until AGENT_EMAIL_LIVE=1 this routes to Pete only (test). Client sends go live only once verified correct.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "html": {"type": "string"}}, "required": ["to", "subject", "body"]}},
    {"name": "calendar_list", "description": "List Pete's Google Calendar events between two dates/datetimes (Pete's local time). Returns each event's id (needed to move/cancel), summary, start, end, location. READ — safe.",
     "input_schema": {"type": "object", "properties": {"calendar": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}}, "required": ["from", "to"]}},
    {"name": "calendar_create", "description": "Create an event in Pete's Google Calendar (the source of truth — it syncs into the CC automatically). Use THIS to schedule, never write calendar_events. 'start'/'end' are Pete's LOCAL Lanzarote time as 'YYYY-MM-DDTHH:MM' (timed) or 'YYYY-MM-DD' (all-day); if no end, give duration_minutes (default 30). No attendees — Pete's own calendar only.",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "duration_minutes": {"type": "integer"}, "location": {"type": "string"}, "description": {"type": "string"}, "calendar": {"type": "string"}}, "required": ["title", "start"]}},
    {"name": "calendar_update", "description": "Move/edit a Google Calendar event (reschedule, rename, relocate). Get event_id from calendar_list first. 'start'/'end' are Pete's local time 'YYYY-MM-DDTHH:MM'.",
     "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}, "title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "duration_minutes": {"type": "integer"}, "location": {"type": "string"}, "description": {"type": "string"}, "calendar": {"type": "string"}}, "required": ["event_id"]}},
    {"name": "calendar_delete", "description": "Cancel/delete a Google Calendar event. Get event_id from calendar_list first.",
     "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}, "calendar": {"type": "string"}}, "required": ["event_id"]}},
    {"name": "odoo_query", "description": "Read Canary Detect's Odoo (search_read). model e.g. 'calendar.event'/'crm.lead'/'account.move'; domain = Odoo domain list; fields = list. READ — safe.",
     "input_schema": {"type": "object", "properties": {"model": {"type": "string"}, "domain": {"type": "array"}, "fields": {"type": "array"}, "limit": {"type": "integer"}}, "required": ["model"]}},
]

def _anthropic(model, system, messages):
    body = {"model": model, "max_tokens": MAX_TOKENS, "system": system, "messages": messages, "tools": TOOLS}
    payload = json.dumps(body).encode()
    for attempt in range(6):
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            # 429 rate-limit / 529 overloaded / 5xx → wait and retry (honour Retry-After); else raise
            if e.code in (429, 500, 502, 503, 529) and attempt < 5:
                ra = e.headers.get("retry-after")
                wait = int(ra) if (ra and str(ra).isdigit()) else min(2 ** attempt + 1, 30)
                print(f"    ⏳ {e.code} — backoff {wait}s (attempt {attempt+1}/6)", flush=True)
                time.sleep(wait)
                continue
            raise

def chat_history(chat_id, exclude_id, limit=6):
    """Recent prior turns for this Telegram chat as Claude messages (oldest→newest). Best-effort: any
    failure → no history (the agent simply behaves as before), so memory can never break a job."""
    if not chat_id:
        return []
    try:
        rows = sb("GET", f"agent_jobs?context-%3E%3Echat_id=eq.{urllib.parse.quote(str(chat_id))}"
                          f"&status=eq.done&id=neq.{exclude_id}&result=not.is.null"
                          f"&select=prompt,result&order=created_at.desc&limit={int(limit)}") or []
    except Exception as e:
        print(f"    · history skipped ({e})", flush=True)
        return []
    msgs = []
    for r in reversed(rows):                       # oldest first
        p = (r.get("prompt") or "").strip(); ans = (r.get("result") or "").strip()
        if not p or not ans:
            continue
        msgs.append({"role": "user", "content": _cap_marked(p, 1500, "older turn, trimmed")})
        msgs.append({"role": "assistant", "content": _cap_marked(ans, 1500, "older turn, trimmed")})
    return msgs

def run_agentic(prompt, model, history=None):
    """Tool-use loop: Claude calls cc_read / cc_search / cc_write until it has a final answer."""
    system = f"{SYSTEM}\n\nThe current time is {now_canary()} (Pete's timezone, Atlantic/Canary)."
    messages = list(history or []) + [{"role": "user", "content": prompt}]
    tin = tout = 0
    for step in range(MAX_STEPS):
        resp = _anthropic(model, system, messages)
        u = resp.get("usage") or {}; tin += u.get("input_tokens", 0); tout += u.get("output_tokens", 0)
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if resp.get("stop_reason") != "tool_use" or not tool_uses:
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            return text, {"input_tokens": tin, "output_tokens": tout}, step
        results = []
        for tu in tool_uses:
            fn = TOOL_FN.get(tu["name"])
            out = fn(tu.get("input") or {}) if fn else f"ERROR: unknown tool {tu['name']}"
            print(f"    ↳ {tu['name']} {json.dumps(tu.get('input', {}))[:90]} → {str(out)[:90]}", flush=True)
            # cc_read gets the larger ceiling (it reads note bodies); every other tool keeps the 8k cap. Each
            # tool already self-caps, so this is a backstop; _cap_marked makes any cut explicit, never silent.
            cap = CC_READ_CAP if tu["name"] == "cc_read" else 8000
            results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": _cap_marked(str(out), cap)})
        messages.append({"role": "user", "content": results})
    return "(stopped: hit max tool steps)", {"input_tokens": tin, "output_tokens": tout}, MAX_STEPS

def complete(job_id, result, usage, model):
    sb("PATCH", f"agent_jobs?id=eq.{job_id}",
       body={"status": "done", "result": result, "usage": usage, "model": model,
             "finished_at": "now()"}, prefer="return=minimal")

def fail(job_id, err):
    sb("PATCH", f"agent_jobs?id=eq.{job_id}",
       body={"status": "error", "error": str(err)[:4000], "finished_at": "now()"},
       prefer="return=minimal")

def process(job):
    jid = job["id"]; model = job.get("model") or MODEL
    prompt = job["prompt"]
    ctx = job.get("context") or {}
    history = chat_history(ctx.get("chat_id"), jid) if ctx.get("chat_id") else []
    if ctx:
        prompt = f"{prompt}\n\n[context]\n{_cap_marked(json.dumps(ctx), 4000, 'context trimmed')}"
    print(f"  ▶ job {jid[:8]} ({job.get('kind')}/{job.get('source')}) → {model}{f' · +{len(history)//2} prior turns' if history else ''}", flush=True)
    try:
        text, usage, steps = run_agentic(prompt, model, history)
        complete(jid, text, usage, model)
        print(f"  ✓ job {jid[:8]} done ({steps} tool-steps, {(usage or {}).get('output_tokens','?')} out tok): {text[:90]!r}", flush=True)
        return True
    except Exception as e:
        detail = ""
        if isinstance(e, urllib.error.HTTPError):
            try: detail = e.read().decode()[:300]
            except Exception: pass
        fail(jid, f"{e} {detail}")
        print(f"  ✗ job {jid[:8]} failed: {e} {detail}", flush=True)
        return False

def preflight():
    miss = [n for n, v in [("CC url", SB_URL), ("CC key", SB_KEY), ("anthropic key", ANTHROPIC_KEY)] if not v]
    if miss:
        print("FATAL: missing config:", ", ".join(miss)); sys.exit(2)

def main():
    preflight()
    mode = "once" if "--once" in sys.argv else "drain" if "--drain" in sys.argv else "loop"
    print(f"cc-agent-worker up — model={MODEL} mode={mode} cc={SB_URL[:34]}…", flush=True)
    idle = 0
    while True:
        job = claim_job()
        if job:
            idle = 0
            process(job)
            if mode == "once":
                print("done (--once)"); return
            continue
        # no pending job
        if mode in ("once", "drain"):
            print(f"queue empty — exiting ({mode})"); return
        idle += 1
        if idle % IDLE_LOG_EVERY == 0:
            print(f"  · idle ({idle} polls)", flush=True)
        time.sleep(POLL_SECS)

if __name__ == "__main__":
    main()
