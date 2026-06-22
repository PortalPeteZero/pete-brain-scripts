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
import json, os, sys, time, urllib.request, urllib.error
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
MODEL = os.environ.get("AGENT_MODEL", "claude-opus-4-8")
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "4096"))
POLL_SECS = int(os.environ.get("AGENT_POLL_SECS", "5"))
IDLE_LOG_EVERY = int(os.environ.get("AGENT_IDLE_LOG_EVERY", "120"))  # log a heartbeat every N idle polls

SYSTEM = (
    "You are the Command Centre cloud agent for Pete Ashcroft's businesses — a 24/7 Claude "
    "running on the always-on server. Your source of truth is the Command Centre (CC Supabase): "
    "knowledge in vault_notes, work in tasks, automations in crons, file index in drive_files, "
    "data homes in data_map. When you act, you write results back to the CC. Pete is based in "
    "Lanzarote and operates in the Atlantic/Canary timezone — reason about all dates and times in "
    "that timezone, never UTC. Be accurate and concise; if you are unsure, say so rather than "
    "guessing. British English."
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

def run_with_claude(prompt, model):
    body = {
        "model": model, "max_tokens": MAX_TOKENS,
        "system": f"{SYSTEM}\n\nThe current time is {now_canary()} (Pete's timezone, Atlantic/Canary).",
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode())
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    return text, resp.get("usage")

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
        text, usage = run_with_claude(prompt, model)
        complete(jid, text, usage, model)
        print(f"  ✓ job {jid[:8]} done ({(usage or {}).get('output_tokens','?')} out tok): {text[:90]!r}", flush=True)
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
