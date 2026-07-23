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

    # --- P4 gate -1: SCHEMA + CAPTURE REHEARSAL, BEFORE THE IRREVERSIBLE SEND (23 Jul 2026) ---
    #     The Wheal Jane break: gates -> SEND -> capture. The capture failed on a missing
    #     `full_name`, so the customer had the email and the record was broken. The obvious fix
    #     ("dry-run te-log first") does NOT work on its own -- te-log's contact insert sits behind
    #     `if apply:`, so a dry run never reaches the fault and exits 0. So we validate the payload
    #     against the SHARED schema here, before anything leaves. Same schema te-log enforces, one
    #     declaration, no drift.
    if "--no-schema" not in args:
        try:
            S = _load("ee_payload_schema", f"{VAULT}/ee_payload_schema.py")
            ok_s, errs_s = S.validate(p)
            if not ok_s:
                print(f"\u26d4 ee-send REFUSING to send: payload fails the shared schema ({len(errs_s)}):")
                for e in errs_s:
                    print(f"   \u2717 {e}")
                print("   NOTHING WAS SENT. Build the payload with ee-payload.py, or fix the fields named above.")
                print("   (This check exists because on 23 Jul the email went out and the capture then failed.)")
                sys.exit(2)
            print("   \u25e6 schema: payload satisfies ee-draft-gate + ee-send + te-log")
        except SystemExit:
            raise
        except Exception as e:
            print(f"   \u26a0 schema check could not run ({e}) -- proceeding UNVALIDATED")

    # --- P3 gate 1: RETRIEVAL RECEIPT — no draft leaves without proof Step-1 retrieval happened ---
    refs = p.get("retrieval_refs") or a.get("retrieval_refs")
    if a.get("kind") in ("reply", "quote", "enquiry") and not refs:
        print("⛔ no retrieval receipt. Run Step 1 first:")
        print("   VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py semantic \"<course + scenario + people + location>\" --limit 6")
        print("   then put the note slugs you actually READ into the payload: \"retrieval_refs\": [\"slug1\", \"slug2\"]")
        sys.exit(2)

    # --- P4 gate 0: DRAFT GATE STAMP — no reply/quote is drafted/sent unless ee-draft-gate validated
    #     THIS exact draft_text (classification + must-haves + stage logic + precedents + shift-left
    #     lint) BEFORE it was presented to Pete. Built 21 Jul 2026 (Wheal Jane freelance-draft failure).
    #     Loud bypass only: --no-gate flag + payload "gate_override": "<why>", banked into lint_report. ---
    if a.get("kind") in ("reply", "quote"):
        dg = _load("eedraftgate", f"{VAULT}/ee-draft-gate.py")
        gate_ok, gate_msg = dg.verify_stamp(p)
        if gate_ok:
            a.setdefault("lint_report", {})
            print(f"   ◦ draft gate: {gate_msg}")
            a["draft_gate"] = {"stamped": True, "detail": gate_msg}
        elif "--no-gate" in args and (p.get("gate_override") or "").strip():
            print(f"   ◦ draft gate BYPASSED (--no-gate): {p['gate_override']}")
            a["draft_gate"] = {"stamped": False, "bypass_reason": p["gate_override"]}
        else:
            print(f"⛔ draft gate: {gate_msg}")
            print("   Run: VAULT=/tmp/pbs python3 /tmp/pbs/ee-draft-gate.py --in <payload>  (validates + stamps)")
            print("   Loud bypass: ee-send --no-gate with \"gate_override\": \"<why>\" in the payload.")
            sys.exit(2)

    # --- P3 gate 2: DRAFT-LINT — the banked rules, mechanically enforced (each block names its rule) ---
    lintmod = _load("eelint", f"{VAULT}/ee-lint.py")
    lint_ok, lint_report = lintmod.lint(body, p)
    if not lint_ok:
        print("⛔ lint BLOCKED — fix the draft, or override a rule with a reason (\"lint_overrides\": {\"<id>\": \"why\"}):")
        for f in lint_report["failures"]:
            print(f"   ✗ [{f['id']}] {f['reason']}" + (f"  → {f['detail']}" if f.get("detail") else ""))
        sys.exit(2)
    if lint_report.get("overridden"):
        for o in lint_report["overridden"]:
            print(f"   ◦ lint override [{o['id']}]: {o['overridden']}")
    a["retrieval_refs"] = refs
    a["lint_passed"] = True
    a["lint_report"] = lint_report
    if a.get("draft_gate"):
        a["lint_report"]["draft_gate"] = a.pop("draft_gate")   # rides the lint_report jsonb into the ledger

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
    a["final_text"] = body; p["activity"] = a
    p["message_id"] = sid or p.get("message_id")   # stable idempotency key for te-log's dedup — re-run safe even if the Gmail auto-pull later fails
    json.dump(p, open(inpath, "w"))
    print("  → capturing via te-log --apply …")
    r = subprocess.run(["python3", f"{VAULT}/te-log.py", "--in", inpath, "--apply", "--manifest", "/tmp/ee-live-manifest.jsonl"],
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        # the send is irreversible and already happened; make the half-recorded state LOUD so stdout
        # matches the exit code (the 'SENT' line above must never be mistaken for done — 22 Jul 2026)
        print(f"⛔ ee-send · SENT ({sid}) but CAPTURE FAILED — enquiry HALF-RECORDED (email delivered, record incomplete).")
        print(f"   Re-run the capture (idempotent, now retry-safe): VAULT=/tmp/pbs python3 /tmp/pbs/te-log.py --in {inpath} --apply")
        sys.exit(r.returncode)
    print(f"=== ee-send · DONE · sent + captured · {sid} ===")
    sys.exit(0)

if __name__ == "__main__":
    main()
