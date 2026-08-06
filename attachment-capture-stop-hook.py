#!/usr/bin/env python3
"""attachment-capture-stop-hook.py — REFUSE to end a turn telling Pete a document is still
awaited from him when he sent images to this very session.

WHY THIS EXISTS. On 3 Aug 2026 Pete sent four photographs in one message: "this is my
daughter's boyfriend … I need a proper identity set up for him in the CC". The session
filed three (NIE cert front, NIE cert back, travel cert), read the passport number off the
fourth, wrote PW0207219 with its issue and expiry into `family_id` — and never saved the
passport image. It then recorded "passport scan STILL PENDING from Pete — waiting on Pete
to drop the file". He had already dropped it. Two later sessions repeated that back to him
as fact before anyone checked, and it took him swearing at the screen to get it looked at.

WHAT IT REFUSES, AND WHY THIS CONDITION. The harm was not the missing file on its own — it
was the false claim built on top of it, which sent Pete looking for something he had
already provided. That claim is cheap to detect and almost never a legitimate thing to say
in a session Pete has just dropped images into. So the gate fires on:

    this session received image attachments  AND  the reply claims a document is still
    outstanding from Pete

and it makes you check those images before you say it. If the document really is
outstanding, the override takes two seconds and records why.

WHAT IT DELIBERATELY DOES NOT DO. It does not try to prove each attachment was filed.
That was the first design and it does not work: the transcript carries no link between
"image 4 of this message" and a filename written later, so every text-based attempt either
missed the real failure or counted unrelated documents as saves. Measured on the founding
transcript, the partial-save version scored 4 images against 8 "saves" — glob patterns and
other threads' PDFs among them — and would never have fired. A gate that cannot be trusted
to fire correctly is worse than none, so that half was cut rather than shipped.

The listing of what Pete actually sent (count, sizes, timestamps, his words) IS included in
the refusal, so the session can settle the question immediately.

DELIBERATELY NARROW:
  * silent in any session with no user image attachments — most sessions.
  * silent unless the reply makes an "awaiting it from Pete" claim.
  * reads THIS session's transcript only, via session_attribution — never file mtimes in
    the shared /tmp/pbs clone, which is what made an earlier stop-hook fire for every
    other session on the machine.
  * fails OPEN on any error. A close must never break because the check broke.

Override, when the document genuinely is still outstanding:
  VAULT=/tmp/pbs python3 /tmp/pbs/gate_override.py grant attachment-capture-stop-hook --reason "<why>"

CLI (for auditing, and for testing against a known transcript):
  python3 attachment-capture-stop-hook.py --transcript <path.jsonl> [--json]

The gate key is the SCRIPT BASENAME, because that is what gate-report.py derives from the
wiring in settings.json.
"""
import json
import os
import re
import subprocess
import sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# "I am still waiting on Pete for a document." Kept tight and document-flavoured: a bare
# "waiting on you" about a decision is not this, and must not be caught.
_DOC = (r"scan|photo|photograph|image|document|passport|licence|license|cert\w*|"
        r"statement|invoice|receipt|file")

# EVERY branch must tie the missing thing TO PETE. Without that, the gate matched
# `echo "any file still missing from Drive?"`, "local copy MISSING", and "this plan held
# that copy pending a CRM feature" — 4 false fires against 1 real one. "Something is
# outstanding" is ordinary; "Pete still owes us this document" is the claim that was false.
AWAITING = re.compile(
    r"(?:"
    r"(?:" + _DOC + r")[^.\n]{0,40}?\b(?:pending|outstanding|awaited|missing|"
    r"not (?:yet )?(?:provided|received|supplied|sent))\b[^.\n]{0,20}\bfrom\s+(?:pete|you)\b"
    # "still needed from Pete" needs a DOCUMENT in view — otherwise it catches a plan's
    # "## Decisions still needed from Pete", which is a heading, not a lost attachment.
    r"|(?:" + _DOC + r")[^.\n]{0,40}?\b(?:pending|outstanding|awaited|still needed)\s+"
    r"from\s+(?:pete|you)\b"
    r"|\b(?:waiting|await\w*)\s+(?:on|for)\s+(?:pete|you)\b[^.\n]{0,60}?(?:" + _DOC + r")"
    r"|\b(?:waiting|await\w*)\s+(?:on|for)\s+(?:pete|you)\s+to\s+(?:send|drop|share|provide|upload)"
    # The negation is REQUIRED. Written `(?:not\s+)?` it also matched "Pete HAS SENT" —
    # the exact opposite claim — and fired on a page that was correctly recording what he
    # had already provided.
    r"|\b(?:pete|you)\s+(?:(?:has|have)\s+not|hasn't|haven't|never)\s+"
    r"(?:yet\s+)?(?:sent|provided|shared|dropped|supplied)\b"
    r")", re.I)

# Writing into something that outlives the session: the CC tables, the knowledge store, a
# task. This is what turns a wrong sentence into a wrong record.
RECORDING = re.compile(
    r"(daily_log|INSERT\s+INTO|UPDATE\s+\w+\s+SET|cc-save\.py|cc-knowledge-ingest\.py|"
    r"worklog\.py|public\.tasks|\btasks\b\s*\(|cc-sql\.py)", re.I)

# Prefilter substrings — every word AWAITING can hinge on, lowercased. Keep in step with it.
CLAIM_MARKERS = ("pending", "awaited", "outstanding", "waiting", "await",
                 "not provided", "not received", "not supplied", "not sent", "to follow",
                 "missing", "you send", "you drop", "you share", "you provide",
                 "drop the", "send the")

# The hook's own refusal text lands in the transcript and quotes these very words. Left
# unguarded, one block arms the gate permanently against the session trying to clear it.
SELF = ("BLOCKED by attachment-capture", "attachment-capture-stop-hook")


def _resolve_transcript():
    try:
        sys.path.insert(0, VAULT)
        import session_attribution as SA
        _sid, main, is_sub, why = SA.resolve_transcript()
    except Exception as e:
        return None, f"session_attribution unavailable ({e})"
    if is_sub:
        return None, "subagent transcript — the main session owns the close"
    if not main or not os.path.exists(main):
        return None, f"no main transcript ({why})"
    return main, ""


def scan(path):
    """(images, claims, error) for THIS session.

    images: one entry per user-attached image — timestamp, byte size, and the words that
            came with it, so the refusal can name exactly what Pete sent.
    claims: assistant sentences asserting a document is still awaited from Pete.
    """
    images, claims = [], []
    try:
        with open(path, "r", errors="ignore") as fh:
            for line in fh:
                if any(s in line for s in SELF):
                    continue
                # Cheap prefilter so a 100 MB transcript is not JSON-parsed line by line.
                # It MUST admit every line a later check could match. Twice while building
                # this gate the prefilter silently ate the very line the gate exists to
                # catch — first the shell copy, then the tool_use carrying the false
                # "STILL PENDING" claim, which has no "text" key at all. Keep it loose.
                low = line.lower()
                if '"image"' not in line and not any(m in low for m in CLAIM_MARKERS):
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                role = msg.get("role")
                ts = rec.get("timestamp")

                if rec.get("type") == "user" and role == "user":
                    said = _first_text(content)
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "image":
                            data = (b.get("source") or {}).get("data") or ""
                            images.append({"ts": ts, "bytes": (len(data) * 3) // 4,
                                           "said": said})
                elif role == "assistant":
                    for b in content:
                        # ONLY claims being WRITTEN INTO A DURABLE RECORD count.
                        #
                        # Measured across all 113 transcripts, matching the claim anywhere
                        # in assistant output fired on 14 sessions and was WRONG on 13:
                        # "send me the photos and I'll add that section", "if you drop that
                        # crop into Downloads", "the invoice is still outstanding", "a phone
                        # that would not send the photos". Asking Pete for something he has
                        # not sent is normal and correct; so is saying an invoice is unpaid.
                        # A 93% false-positive gate gets switched off in a day.
                        #
                        # The harm on 3 Aug was not the sentence — it was that the sentence
                        # went into daily_log, outlived the session, and was read back as
                        # fact by two later ones. So: a claim recorded, not a claim spoken.
                        if not isinstance(b, dict) or b.get("type") != "tool_use":
                            continue
                        blob = json.dumps(b.get("input") or "")
                        if not RECORDING.search(blob):
                            continue
                        for sent in re.split(r"(?<=[.!?\n])\s+|\\n", blob):
                            if AWAITING.search(sent):
                                s = re.sub(r"\s+", " ", sent).strip()
                                if s and s not in claims:
                                    claims.append(s[:200])
    except OSError as e:
        return None, None, str(e)
    return images, claims, ""


def _first_text(content):
    for b in content:
        if isinstance(b, dict) and b.get("type") == "text":
            return re.sub(r"\s+", " ", (b.get("text") or "")).strip()[:200]
    return ""


def overridden():
    try:
        r = subprocess.run([sys.executable, os.path.join(VAULT, "gate_override.py"),
                            "check", "attachment-capture-stop-hook"],
                           capture_output=True, text=True,
                           env={**os.environ, "VAULT": VAULT}, timeout=60)
        return r.returncode == 0 and "granted" in (r.stdout or "").lower()
    except Exception:
        return False


def main():
    argv = sys.argv[1:]
    as_json = "--json" in argv
    if "--transcript" in argv:
        path, why = argv[argv.index("--transcript") + 1], ""
    else:
        path, why = _resolve_transcript()
    if not path:
        if as_json:
            print(json.dumps({"checked": False, "why": why}))
        sys.exit(0)                                # fail open — never block on a broken check

    images, claims, err = scan(path)
    if images is None:
        sys.stderr.write(f"[attachment-capture] could not read the transcript ({err}) — "
                         f"NOT confirming every attachment was filed.\n")
        sys.exit(0)

    if as_json:
        print(json.dumps({"checked": True, "images": len(images),
                          "claims": claims, "detail": images[:20]}, indent=1))
    if not images or not claims:
        sys.exit(0)
    if overridden():
        sys.exit(0)

    drops = {}
    for im in images:
        drops.setdefault((im["ts"], im["said"]), []).append(im["bytes"])

    out = [f"BLOCKED by attachment-capture: you are about to say a document is still "
           f"outstanding from Pete,\nbut he sent {len(images)} image(s) to THIS session.\n\n",
           "  What you said:\n"]
    for c in claims[:4]:
        out.append(f"    \"{c}\"\n")
    out.append("\n  What Pete actually sent:\n")
    for (ts, said), sizes in list(drops.items())[:8]:
        out.append(f"    {ts} — {len(sizes)} image(s), {', '.join(f'{b:,}b' for b in sizes)}\n")
        if said:
            out.append(f"      with: \"{said}\"\n")
    out.append(
        "\n  CHECK THOSE IMAGES BEFORE YOU SAY IT. Read each one back out of this transcript's\n"
        "  image blocks and confirm none of them is the document you are calling outstanding.\n"
        "  If one of them is it, file it — Drive, and the gated bucket where the page reads its\n"
        "  scans from — and correct the claim.\n\n"
        "  This is the exact failure the gate exists for: 3 Aug 2026, Jared's passport. Four\n"
        "  photographs sent, three filed, the passport read for its number and then lost, and\n"
        "  recorded as 'STILL PENDING from Pete'. He had already sent it, and two later sessions\n"
        "  repeated it back to him as fact.\n\n"
        f"  If it genuinely is still outstanding:\n"
        f"    VAULT={VAULT} python3 {VAULT}/gate_override.py grant attachment-capture-stop-hook "
        f"--reason \"<why>\"\n")
    sys.stderr.write("".join(out))
    sys.exit(2)


if __name__ == "__main__":
    main()
