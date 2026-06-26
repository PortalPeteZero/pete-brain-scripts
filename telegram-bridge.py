#!/usr/bin/env python3
"""telegram-bridge.py — Business OS Part G: the Telegram <-> Command Centre bridge.

A long-running worker (a Railway service, deployed like the cc-agent). One poll loop, two jobs:
  INTAKE  — long-poll Telegram getUpdates; for each message from the ALLOWED user ONLY
            (telegram-allowed-userid; every other sender is rejected), enqueue a
            public.agent_jobs row (kind='telegram') for the always-on cc-agent to run.
  DELIVER — find finished telegram jobs not yet delivered, send the result back to the
            chat via the Bot API sendMessage, and stamp delivered_at.

The cc-agent (cc-agent-worker.py) is UNCHANGED — this bridge is purely an I/O adapter on the
agent_jobs queue, so the proven agent keeps doing the reasoning + tool-use.

SAFETY: inbound text is UNTRUSTED input. The cc-agent keeps its outbound send-gate
(AGENT_EMAIL_LIVE=0 -> any email routes to Pete only), so an injected Telegram message cannot
weaponise an autonomous send. The allow-list of one (Pete's user-id) is the first gate.

Creds (env-first, like the rest of the cloud fleet):
  CC_SUPABASE_URL + CC_SUPABASE_SERVICE_KEY  -> the CC (and used to fetch the telegram secrets)
  telegram-bot-token + telegram-allowed-userid -> read from the CC secrets table at startup
    (override with TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_USERID env if ever needed)

Run: python telegram-bridge.py   (Railway startCommand; restartPolicyType=ON_FAILURE; no schedule)
Pure stdlib — no pip deps (matches cc-agent).
"""
# CRON-META
# what: Persistent bridge relaying @pete_command_centre_bot messages to/from the 24/7 cc-agent.
# why: Gives Pete the Telegram (phone) chat surface into the Command Centre.
# reads: Telegram Bot API + cc-agent
# writes: Telegram replies (relays to/from cc-agent)
# host: service
# timezone: Atlantic/Canary
# CRON-META-END
import os, sys, json, time, urllib.request, urllib.error, urllib.parse
from pathlib import Path

VAULT = os.environ.get("VAULT", "/tmp/pbs")


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


def cc_secret(name):
    s, rows = rest("GET", f"secrets?select=value&name=eq.{urllib.parse.quote(name)}")
    if s == 200 and rows:
        return rows[0]["value"]
    raise SystemExit(f"telegram-bridge: secret '{name}' not in CC (HTTP {s}) — cannot start")


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or cc_secret("telegram-bot-token")
ALLOWED = str(os.environ.get("TELEGRAM_ALLOWED_USERID") or cc_secret("telegram-allowed-userid")).strip()
API = f"https://api.telegram.org/bot{TOKEN}"
POLL_TIMEOUT = int(os.environ.get("TG_POLL_TIMEOUT", "10"))   # long-poll seconds (also caps reply latency)
TG_MAX = 3900                                                  # Telegram hard limit is 4096/message; chunk under it


def tg(method, **params):
    body = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send(chat_id, text):
    text = (text or "(no reply)").strip() or "(no reply)"
    for i in range(0, len(text), TG_MAX):
        tg("sendMessage", chat_id=chat_id, text=text[i:i + TG_MAX])


def enqueue(text, chat_id, msg):
    prompt = ("[Message from Pete via Telegram — reply concisely, it's going to his phone]\n\n" + text)
    ctx = {"channel": "telegram", "chat_id": chat_id,
           "tg_message_id": msg.get("message_id"),
           "from": (msg.get("from") or {}).get("username")}
    s, out = rest("POST", "agent_jobs",
                  [{"kind": "telegram", "source": "telegram", "prompt": prompt,
                    "status": "pending", "context": ctx}],
                  prefer="return=representation")
    if s in (200, 201) and isinstance(out, list) and out:
        return out[0]["id"]
    print(f"  ! enqueue failed {s}: {str(out)[:200]}", flush=True)
    return None


def handle_update(u):
    msg = u.get("message") or u.get("edited_message")
    if not msg:
        return
    frm = msg.get("from") or {}
    uid = str(frm.get("id"))
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()

    if uid != ALLOWED:                      # allow-list of one — reject everyone else
        if chat_id:
            tg("sendMessage", chat_id=chat_id,
               text="Sorry — this is a private assistant and you're not on its allow-list.")
        print(f"  x rejected message from uid={uid} (not on allow-list)", flush=True)
        return

    if not text:
        send(chat_id, "Send me text — I can capture a note/task, answer from the Command Centre, or give you a briefing.")
        return

    if text.lower() in ("/start", "/help"):
        send(chat_id, "Command Centre bridge online. Just type — I'll route it to your 24/7 agent "
                      "(notes, tasks, brain queries, briefings). Replies usually land in ~10-20s.")
        return

    jid = enqueue(text, chat_id, msg)
    if jid:
        tg("sendMessage", chat_id=chat_id, text="\U0001F44D on it…")
        print(f"  > enqueued agent_job {jid[:8]} from Telegram (uid {uid})", flush=True)
    else:
        send(chat_id, "⚠ couldn't queue that just now — try again in a moment.")


def deliver_done():
    s, rows = rest("GET", "agent_jobs?kind=eq.telegram&status=in.(done,error)&delivered_at=is.null"
                          "&select=id,status,result,error,context&order=created_at.asc&limit=10")
    if s != 200 or not rows:
        return
    for j in rows:
        ctx = j.get("context") or {}
        chat_id = ctx.get("chat_id")
        if chat_id:
            out = j.get("result") if j.get("status") == "done" else f"⚠ error: {(j.get('error') or '')[:600]}"
            send(chat_id, out)
            print(f"  < delivered job {j['id'][:8]} -> telegram {chat_id}", flush=True)
        rest("PATCH", f"agent_jobs?id=eq.{j['id']}", body={"delivered_at": "now()"}, prefer="return=minimal")


def main():
    print(f"telegram-bridge up — allow-list uid={ALLOWED}, cc={CC_URL[:34]}…", flush=True)
    tg("deleteWebhook")                     # ensure long-poll mode (webhook + getUpdates are exclusive)
    me = tg("getMe")
    print("  getMe:", json.dumps(me.get("result", me))[:160], flush=True)
    offset = None
    polls = 0
    while True:
        params = {"timeout": POLL_TIMEOUT}
        if offset is not None:
            params["offset"] = offset
        resp = tg("getUpdates", **params)
        if resp.get("ok"):
            for u in resp.get("result", []):
                offset = u["update_id"] + 1
                try:
                    handle_update(u)
                except Exception as e:
                    print(f"  ! handle error: {e}", flush=True)
        else:
            print("  getUpdates not ok:", str(resp.get("error") or resp)[:160], flush=True)
            time.sleep(3)
        try:
            deliver_done()                  # runs after every poll cycle (<= POLL_TIMEOUT latency)
        except Exception as e:
            print(f"  ! deliver error: {e}", flush=True)
        polls += 1
        if polls % 60 == 0:
            print(f"  . alive ({polls} polls)", flush=True)


if __name__ == "__main__":
    main()
