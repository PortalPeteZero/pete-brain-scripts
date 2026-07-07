#!/usr/bin/env python3
"""ee-send.py — the ONE sanctioned way to send an Enquiry-Engine reply. Makes HTML automatic.

WHY THIS EXISTS: plain-text sends recurred for weeks because the default gmail path sends plain and a
"remember to format HTML" reminder never fired at the send moment. This helper removes the choice:
it ALWAYS formats the reply as HTML (ee-html), ALWAYS verifies the recipient is the customer, sends on
the thread, then runs the te-log capture. Using it = HTML + verified recipient + capture, for free.

Takes the SAME payload JSON as te-log. Recipient = the enquiry's own `email` (never derived from the
thread — that's what mis-addressed web-form replies back to info@). Body = activity.final_text or draft_text.

Usage:
  VAULT=/tmp/pbs python3 ee-send.py --in payload.json            # dry-run: shows recipient + HTML preview len
  VAULT=/tmp/pbs python3 ee-send.py --in payload.json --apply    # HTML-send + te-log capture (the real thing)
"""
import os, sys, json, subprocess, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")

def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    inpath = next((args[i+1] for i, a in enumerate(args) if a == "--in" and i+1 < len(args)), None)
    if not inpath:
        print("usage: ee-send.py --in payload.json [--apply]"); sys.exit(2)
    p = json.load(open(inpath))
    a = p.get("activity", {})
    to = p.get("email")
    if not to:
        print("⛔ payload has no `email` — cannot send safely (recipient must be the enquiry's own address)."); sys.exit(2)
    # ⛔ recipient gate — never send to one of our own addresses
    if to.lower().endswith("@sygma-solutions.com"):
        print(f"⛔ refusing to send: recipient {to} is one of OUR addresses, not the customer."); sys.exit(2)
    body = a.get("final_text") or a.get("draft_text") or a.get("body")
    if not body:
        print("⛔ no reply body in the payload (activity.final_text / draft_text)."); sys.exit(2)
    subject = a.get("subject") or "Your training enquiry"
    thread_id = p.get("thread_id")
    cc = p.get("cc") or a.get("cc")

    h = _load("eeh", f"{VAULT}/ee-html.py")
    gm = _load("gm", f"{VAULT}/gmail-api.py")
    html = h.to_html(body)               # ← HTML is not optional here
    g = gm.GmailAPI()

    print(f"=== ee-send · to {to}{' · cc '+cc if cc else ''} · {'APPLY' if apply else 'DRY-RUN'} ===")
    print(f"  subject: {subject}")
    print(f"  HTML body: {len(html)} chars (formatted) · thread {thread_id or '(fresh)'}")
    if not apply:
        print("  [dry] no send. Re-run with --apply."); return

    # send HTML (pass the HTML string as the `html` arg → forces HTML body). Signature auto-appended.
    if thread_id:
        res = g.reply_thread(thread_id, body, as_draft=False, to=to, cc=cc, html=html)
    else:
        res = g.create_draft  # never fresh-send without a subject/thread here; guard
        res = g.send(to, subject, body, cc=cc, html=html)
    sid = res.get("id") if isinstance(res, dict) else None
    # verify what actually went, and that it's HTML
    sm = g.get_message(sid); sh = {x["name"].lower(): x["value"] for x in sm.get("payload", {}).get("headers", [])}
    is_html = "text/html" in json.dumps(sm.get("payload", {}))
    print(f"  SENT {sid} · To {sh.get('to')} · html={is_html}")
    if not is_html:
        print("  ⛔ WARNING: sent message is NOT HTML — investigate before capture."); sys.exit(3)
    # stamp the sent text as final, then capture via te-log
    a["final_text"] = body; p["activity"] = a
    json.dump(p, open(inpath, "w"))
    print("  → capturing via te-log --apply …")
    r = subprocess.run(["python3", f"{VAULT}/te-log.py", "--in", inpath, "--apply", "--manifest", "/tmp/ee-live-manifest.jsonl"],
                       env={**os.environ, "VAULT": VAULT})
    sys.exit(r.returncode)

if __name__ == "__main__":
    main()
