#!/usr/bin/env python3
"""agent-cron-enqueue.py — the bridge that turns an AGENTIC cron into a scheduled agent job.

An agentic cron is NOT a self-contained .py — it's a PROMPT that needs a reasoning Claude agent
(calls Gmail/Calendar/Odoo/CC tools, decides content, renders output). railway-bootstrap runs a
headless .py and CANNOT host a Claude agent. So an agentic cron migrates by becoming a row in
public.agent_jobs that the always-on cloud agent (cc-agent) claims and runs through its tool-loop.

This ONE script is deployed as N Railway cron services (one per agentic cron), each with a distinct
CRON_KEY env. On its schedule it:
  1. looks up the cron's prompt in public.agent_cron_prompts (by CRON_KEY),
  2. inserts a pending agent_jobs row (kind='cron', source=CRON_KEY, prompt, model),
  3. the cloud agent (cc-agent) picks it up within its poll interval and runs it.

Idempotent: skips if a pending/running job for this source already exists (no pile-up if the agent
is slow/down). Output lands wherever the prompt writes it (a CC table, a report, an email) — agentic
crons are verified by RESULT (H8), not code-drift. The firm send-gate lives in the agent
(AGENT_EMAIL_LIVE=0 → routes outbound to Pete) until each cron's recipients are verified.

Env (set by railway-deploy.py): CRON_KEY + CC_SUPABASE_URL + CC_SUPABASE_SERVICE_KEY.
Local test: agent-cron-enqueue.py <cron-key>   (reads CC keys from the secrets file if env unset).
"""
# NOTE: this is a SHARED launcher — its CRON-META is intentionally generic. Per-cron descriptive
# metadata (what/why/reads/writes/schedule) stays manifest-driven for agentic crons (railway-deploy
# keeps the existing manifest fields; agentic crons are verified by RESULT, not code-drift — see H8).
import os, sys, json, urllib.request, urllib.error
from pathlib import Path

VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")


def _cc():
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        d = json.load(open(Path(VAULT) / "Library/processes/secrets/command-centre-supabase-keys.json"))
        url, key = d["url"], d["service_role_key"]
    return url.rstrip("/"), key


CC_URL, CC_KEY = _cc()
_H = {"apikey": CC_KEY, "Authorization": "Bearer " + CC_KEY, "Content-Type": "application/json"}


def rest(method, path, body=None, prefer=None):
    h = dict(_H)
    if prefer:
        h["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{CC_URL}/rest/v1/{path}", data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            t = r.read().decode()
            return r.status, (json.loads(t) if t.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    key = os.environ.get("CRON_KEY") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not key:
        print("agent-cron-enqueue: no CRON_KEY env / argv — nothing to do", file=sys.stderr)
        return 2
    rows = rest("GET", f"agent_cron_prompts?cron_key=eq.{key}&select=prompt,model,enabled")[1] or []
    if not rows:
        print(f"agent-cron-enqueue: no agent_cron_prompts row for '{key}' — skip", file=sys.stderr)
        return 2
    row = rows[0]
    if not row.get("enabled"):
        print(f"agent-cron-enqueue: '{key}' disabled — skip")
        return 0
    # idempotency: never stack jobs for the same cron (the agent may be mid-run or down)
    pend = rest("GET", f"agent_jobs?source=eq.{key}&status=in.(pending,running)&select=id")[1] or []
    if pend:
        print(f"agent-cron-enqueue: '{key}' already has {len(pend)} pending/running job(s) — skip")
        return 0
    body = {"kind": "cron", "source": key, "prompt": row["prompt"], "status": "pending"}
    if row.get("model"):
        body["model"] = row["model"]
    s, out = rest("POST", "agent_jobs", [body], prefer="return=representation")
    if s not in (200, 201):
        print(f"agent-cron-enqueue: insert failed {s}: {out}", file=sys.stderr)
        return 1
    jid = (out[0]["id"] if isinstance(out, list) and out else "?")
    print(f"agent-cron-enqueue: '{key}' → agent_jobs {jid} (pending) — cloud agent will run it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
