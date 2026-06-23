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
from datetime import datetime
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

VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")
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
    "running on the always-on server. Your source of truth is the Command Centre (CC Supabase): "
    "knowledge in vault_notes, work in tasks, automations in crons, file index in drive_files, "
    "data homes in data_map. When you act, you write results back to the CC. Pete is based in "
    "Lanzarote and operates in the Atlantic/Canary timezone — reason about all dates and times in "
    "that timezone, never UTC. Be accurate and concise; if you are unsure, say so rather than "
    "guessing. British English. You have tools: cc_read (read-only SQL over the WHOLE Command Centre), "
    "cc_search (full-text knowledge search), and cc_write (create/update/delete CC rows). Use cc_read / "
    "cc_search to ground every answer in LIVE data rather than memory, and cc_write to actually carry out "
    "work (create or complete tasks, save notes). Prefer doing over describing."
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
WRITE_DENY = {"secrets", "profiles", "grants", "group_grants", "module_grants", "groups"}

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

def t_cc_read(a):
    try:
        return json.dumps(sb("POST", "rpc/cc_read", body={"q": (a.get("query") or "").strip()}))[:8000]
    except urllib.error.HTTPError as e:
        return f"ERROR: {e.read().decode()[:300]}"

def t_cc_search(a):
    try:
        rows = sb("POST", "rpc/search_notes", body={"q": a.get("query") or "", "lim": int(a.get("limit") or 8)}) or []
        slim = [{k: (v[:400] if isinstance(v, str) and k in ("body", "content") else v) for k, v in r.items()} for r in rows]
        return json.dumps(slim)[:8000]
    except urllib.error.HTTPError as e:
        return f"ERROR: {e.read().decode()[:300]}"

def t_cc_write(a):
    table = (a.get("table") or "").strip().lower(); op = a.get("op")
    data = a.get("data") or {}; match = a.get("match") or {}
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
PETE = "pete.ashcroft@sygma-solutions.com"

# On Railway the repo is flat (helpers at REPO root) and bootstrap materialises the SA key at
# REPO/Library/processes/secrets/. The helpers' default KEY_PATH (dirname(__file__)/../secrets) only
# resolves correctly in the vault layout, so pass the Railway path explicitly when it exists.
_SA_RAILWAY = os.path.join(_SCRIPTS, "Library", "processes", "secrets", "google-seo-service-account.json")
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

def _rfc3339(s):
    if not s: return s
    return s if (s.endswith("Z") or "+" in s[10:] or s[10:].count("-") > 0) else s + "Z"  # GCal needs a tz suffix

def t_calendar_list(a):
    try:
        c = _helper("calendar-api.py", "calendar_api").CalendarAPI(**_google_kwargs())
        evs = c.list_events(calendar_id=a.get("calendar", "primary"), time_min=_rfc3339(a.get("from")), time_max=_rfc3339(a.get("to")))
        return json.dumps([{"summary": e.get("summary"), "start": e.get("start"), "end": e.get("end")} for e in (evs or [])][:50])[:8000]
    except Exception as e:
        return f"ERROR calendar_list: {e}"

def t_odoo_query(a):
    try:
        o = _helper("odoo-api.py", "odoo_api")
        rows = o._execute(a.get("model"), "search_read", [a.get("domain", [])], {"fields": a.get("fields"), "limit": int(a.get("limit", 50))})
        return json.dumps(rows)[:8000]
    except (Exception, SystemExit) as e:                      # odoo_api sys.exit()s at import if config absent
        return f"ERROR odoo_query: {e}"

TOOL_FN.update({"gmail_search": t_gmail_search, "gmail_send": t_gmail_send, "calendar_list": t_calendar_list, "odoo_query": t_odoo_query})
TOOLS += [
    {"name": "gmail_search", "description": "Search Pete's Gmail (Gmail query syntax, e.g. 'is:unread', 'from:x newer_than:7d'). Returns thread snippets. READ — safe.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max": {"type": "integer"}}, "required": ["query"]}},
    {"name": "gmail_send", "description": "Send an email as Pete. SAFETY: until AGENT_EMAIL_LIVE=1 this routes to Pete only (test). Client sends go live only once verified correct.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "html": {"type": "string"}}, "required": ["to", "subject", "body"]}},
    {"name": "calendar_list", "description": "List Pete's Google Calendar events between two ISO datetimes. READ — safe.",
     "input_schema": {"type": "object", "properties": {"calendar": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}}, "required": ["from", "to"]}},
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

def run_agentic(prompt, model):
    """Tool-use loop: Claude calls cc_read / cc_search / cc_write until it has a final answer."""
    system = f"{SYSTEM}\n\nThe current time is {now_canary()} (Pete's timezone, Atlantic/Canary)."
    messages = [{"role": "user", "content": prompt}]
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
            results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": str(out)[:8000]})
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
    if job.get("context"):
        prompt = f"{prompt}\n\n[context]\n{json.dumps(job['context'])[:4000]}"
    print(f"  ▶ job {jid[:8]} ({job.get('kind')}/{job.get('source')}) → {model}", flush=True)
    try:
        text, usage, steps = run_agentic(prompt, model)
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
