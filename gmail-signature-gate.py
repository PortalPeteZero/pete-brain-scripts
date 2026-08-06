#!/usr/bin/env python3
"""gmail-signature-gate.py -- prove that mail to a NO_SIGNATURE_DOMAINS recipient never goes out
carrying Pete's HTML signature, on EVERY send path, without sending anything.

    VAULT=/tmp/pbs python3 /tmp/pbs/gmail-signature-gate.py

Why this exists: M Group's Mimecast blocks signed mail from us with `554 Host network not allowed`
(measured 6 Aug 2026: 5 of 16 signed sends blocked, 0 of 4 unsigned). The fix lives in gmail-api.py
so that ee-send, triage, reply_thread and a bare CLI send all inherit it. This gate is what proves
the fix is still wired, because the failure is silent: a signed message just bounces later.

Done = FAILURES: 0.
"""
import base64
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gmail_api_impl", os.path.join(HERE, "gmail-api.py"))
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


class FakeAPI(gm.GmailAPI):
    """A GmailAPI that captures the outgoing message instead of posting it."""

    def __init__(self):
        self.user = gm.DEFAULT_USER
        self.creds = {}
        self.scope = gm.SCOPE
        self._token, self._token_exp = "stub", 1 << 62
        self.captured = None

    def _signature_html(self, from_=None):
        return '<table><tr><td>Pete Ashcroft</td></tr><tr><td>Managing Director</td></tr>' \
               '<tr><td><a href="https://heyzine.com/flip-book/b9cb182d69.html">flipbook</a></td></tr></table>'

    def _call(self, method, path, body=None, query=None):
        self.captured = body
        return {"id": "stub", "threadId": "stub"}


def raw_text(body_obj):
    """Decode the outgoing message INCLUDING its MIME parts. The signature is appended as HTML and
    lands in a base64-encoded part, so a flat search of the outer blob silently finds nothing and
    every 'is it signed?' assertion passes for the wrong reason. Caught by this gate 6 Aug 2026."""
    import email
    raw = (body_obj or {}).get("raw") or ((body_obj or {}).get("message") or {}).get("raw") or ""
    outer = base64.urlsafe_b64decode(raw + "==" * 3).decode("utf-8", "replace")
    chunks = [outer]
    try:
        msg = email.message_from_string(outer)
        for part in msg.walk():
            payload = part.get_payload(decode=True)
            if payload:
                chunks.append(payload.decode("utf-8", "replace"))
    except Exception:
        pass
    return "\n".join(chunks)


def signed(text):
    return ("Managing Director" in text) or ("heyzine" in text)


MG = "Emma.Jones@mgroupltd.com"
SAFE = "tony.garlinge@theclancygroup.co.uk"

print("1. domain matcher")
check("plain address matches", gm._blocked_signature_domains(MG) == ["mgroupltd.com"])
check("case insensitive", gm._blocked_signature_domains("EMMA.JONES@MGROUPLTD.COM") == ["mgroupltd.com"])
check("display-name form matches",
      gm._blocked_signature_domains('"Jones, Emma" <Emma.Jones@mgroupltd.com>') == ["mgroupltd.com"])
check("multi-recipient string finds the blocked one",
      gm._blocked_signature_domains(f"{SAFE}, {MG}") == ["mgroupltd.com"])
check("list form works", gm._blocked_signature_domains([SAFE, "a@morrisonus.com"]) == ["morrisonus.com"])
check("all three domains covered",
      gm._blocked_signature_domains("a@mgroupltd.com, b@mgroupservices.com, c@morrisonus.com")
      == ["mgroupltd.com", "mgroupservices.com", "morrisonus.com"])
check("unrelated recipient is not matched", gm._blocked_signature_domains(SAFE) == [])
check("lookalike domain is not matched", gm._blocked_signature_domains("x@notmgroupltd.com.evil.com") == [])
check("None is safe", gm._blocked_signature_domains(None, None) == [])

print("2. send()")
g = FakeAPI()
g.send(SAFE, "s", "body")
check("normal recipient still gets the signature", signed(raw_text(g.captured)))
g = FakeAPI()
g.send(MG, "s", "body")
check("M Group recipient gets NO signature", not signed(raw_text(g.captured)))
g = FakeAPI()
g.send(SAFE, "s", "body", cc=MG)
check("blocked address in Cc alone suppresses", not signed(raw_text(g.captured)))
g = FakeAPI()
g.send(SAFE, "s", "body", bcc=MG)
check("blocked address in Bcc alone suppresses", not signed(raw_text(g.captured)))
g = FakeAPI()
g.send(MG, "s", "<p>body</p>", html=True)
check("html body to M Group also unsigned", not signed(raw_text(g.captured)))

print("3. create_draft()")
g = FakeAPI()
g.create_draft(SAFE, "s", "body")
check("normal draft still signed", signed(raw_text(g.captured)))
g = FakeAPI()
g.create_draft(MG, "s", "body")
check("M Group draft unsigned", not signed(raw_text(g.captured)))

print("4. reply_thread() inherits (it funnels through send/create_draft)")


class ThreadAPI(FakeAPI):
    def get_thread(self, tid, fmt="full"):
        return {"messages": [{"payload": {"headers": [
            {"name": "From", "value": f'"Jones, Emma" <{MG}>'},
            {"name": "Subject", "value": "EUSR Cat 2"},
            {"name": "Message-ID", "value": "<abc@mail>"},
        ]}}]}

    def list_send_as(self):
        return [{"sendAsEmail": gm.DEFAULT_USER}]


g = ThreadAPI()
g.reply_thread("t1", "body")
check("threaded reply to M Group unsigned", not signed(raw_text(g.captured)))
g = ThreadAPI()
g.reply_thread("t1", "body", as_draft=True)
check("threaded reply DRAFT to M Group unsigned", not signed(raw_text(g.captured)))

print("5. the gate never turns a signature ON")
g = FakeAPI()
g.send(SAFE, "s", "body", signature=False)
check("signature=False stays off for a normal recipient", not signed(raw_text(g.captured)))

print("6. no other repo file builds its own send that would bypass this")
bypass = []
for fn in sorted(os.listdir(HERE)):
    if not fn.endswith(".py") or fn in ("gmail-api.py", os.path.basename(__file__)):
        continue
    try:
        src = open(os.path.join(HERE, fn), encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    if re.search(r"""_call\(\s*['"]POST['"]\s*,\s*['"]/(messages/send|drafts)['"]""", src) or "smtplib" in src:
        bypass.append(fn)
# remittance-to-xero.py posts its own raw, but only ever to the fixed Xero bills inbox and it
# attaches no signature at all, so it cannot reach a blocked domain. Reviewed 6 Aug 2026.
KNOWN_SAFE = {"remittance-to-xero.py"}
unexpected = [f for f in bypass if f not in KNOWN_SAFE]
check("no unreviewed file posts its own message", not unexpected, f"-> {unexpected}")

print()
print(f"FAILURES: {len(FAILURES)}")
if FAILURES:
    for f in FAILURES:
        print("  -", f)
sys.exit(1 if FAILURES else 0)
