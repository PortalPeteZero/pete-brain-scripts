#!/usr/bin/env python3
"""ee-send.py — the ONE sanctioned path for an Enquiry-Engine reply. Formatting is not optional.

Every EE reply is rendered by ee-html.to_html → a CLEAN, SIMPLE email (Pete 2026-07-07: no navy banner,
no cards; readable paragraphs, underlined worded links, bold figures) and, by default, DRAFTED into
Gmail for Pete to review (Mode B).
On Pete's OK, --apply sends + captures via te-log. Recipient is always the enquiry's own `email`
(never derived from the thread — that's what mis-addressed web-form replies back to info@).

Takes the SAME payload JSON as te-log. Body = activity.final_text or draft_text.

Usage:
  VAULT=/tmp/pbs python3 ee-send.py --in payload.json            # DEFAULT: create the formatted draft in Gmail
  VAULT=/tmp/pbs python3 ee-send.py --in payload.json --apply    # send the formatted email + capture (after sign-off)
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
        print("⛔ payload has no `email` — recipient must be the enquiry's own address."); sys.exit(2)
    if to.lower().endswith("@sygma-solutions.com"):
        print(f"⛔ refusing: recipient {to} is one of OUR addresses, not the customer."); sys.exit(2)
    body = a.get("final_text") or a.get("draft_text") or a.get("body")
    if not body:
        print("⛔ no reply body in the payload."); sys.exit(2)
    cc = p.get("cc") or a.get("cc")
    thread_id = p.get("thread_id")

    h = _load("eeh", f"{VAULT}/ee-html.py")
    gm = _load("gm", f"{VAULT}/gmail-api.py")
    html = h.to_html(body)                # ← always the house-style formatted HTML
    g = gm.GmailAPI()

    if not apply:
        # DEFAULT — draft it for review (Mode B), formatted, in Gmail
        d = g.reply_thread(thread_id, body, as_draft=True, to=to, cc=cc, html=html) if thread_id \
            else g.create_draft(to, a.get("subject") or "Your training enquiry", body, cc=cc, html=html)
        did = d.get("id") if isinstance(d, dict) else None
        print(f"=== ee-send · DRAFTED (formatted) for review · to {to}{' · cc '+cc if cc else ''} · draft {did} ===")
        print("  Review in Gmail Drafts. When Pete signs off: ee-send --in <payload> --apply (sends + captures).")
        return

    # --apply — send the formatted email, then capture
    res = g.reply_thread(thread_id, body, as_draft=False, to=to, cc=cc, html=html) if thread_id \
        else g.send(to, a.get("subject") or "Your training enquiry", body, cc=cc, html=html)
    sid = res.get("id") if isinstance(res, dict) else None
    sm = g.get_message(sid); sh = {x["name"].lower(): x["value"] for x in sm.get("payload", {}).get("headers", [])}
    # ee-html.py (clean template, Pete 2026-07-07) always wraps the body in color:#1a1a2e and uses #003366 links.
    # Check the HTML WE rendered (`html`) — the sent body is base64 in the payload, so a marker never appears there
    # (which is why the old #1B2340-in-payload check ALWAYS failed and sys.exit(3)'d before te-log).
    # And NEVER abort the capture on a miss — aborting is the bug that skipped te-log on every clean send.
    formatted = ("#1a1a2e" in html) or ("#003366" in html)
    print(f"=== ee-send · SENT · {sid} · To {sh.get('to')} · formatted={formatted} ===")
    if not formatted:
        print("  ⚠ note: ee-html didn't produce the clean-template markers — worth a glance at the render. Capture still proceeding.")
    a["final_text"] = body; p["activity"] = a; json.dump(p, open(inpath, "w"))
    print("  → capturing via te-log --apply …")
    r = subprocess.run(["python3", f"{VAULT}/te-log.py", "--in", inpath, "--apply", "--manifest", "/tmp/ee-live-manifest.jsonl"],
                       env={**os.environ, "VAULT": VAULT})
    sys.exit(r.returncode)

if __name__ == "__main__":
    main()
