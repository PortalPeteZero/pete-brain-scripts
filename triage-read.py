#!/usr/bin/env python3
"""triage-read.py — the ONLY sanctioned way to read a triage round, and the thing that makes
judging-from-a-slice mechanically impossible.

WHY THIS EXISTS (27 Jul 2026)
  SKILL.md has said "READ its messages[].body in full" since 15 Jul 2026. On 27 Jul a round of 70
  threads was judged off a hand-rolled `print(body[:600])`. Gareth Phillips' training-bookings audit
  is 8,485 characters carrying six recommendations that need Pete's decision; 700 of them were read.
  Andrew Weston's backlink email carries an explicit ask (create an account on the new dashboard)
  that was dropped.

  Every gate in the triage chain until now checked the OUTPUT -- is there a judgment for each thread,
  does the ask match the verb, does the label resolve. NONE could see the INPUT. A skill file saying
  "read it all" cannot refuse a `[:600]`, and Pete has the measurement to prove rules-without-teeth
  do not hold (see [[2026-07-27-gmail-filter-overlap-needs-a-gate-not-a-rule]]).

HOW IT BITES
  * There is NO truncation parameter. You cannot ask this tool for a slice, so there is no `[:600]`
    to write. It prints every message of every thread, whole.
  * It writes a READ RECEIPT (/tmp/triage-read-<session_id>.json) holding a sha256 of the exact
    full text it printed, per thread.
  * `triage-ops-table.py` REFUSES any thread whose full-body hash is not in that receipt. Not warns
    -- refuses, the same way it already refuses an unresolvable label.
  * The receipt is written by the tool, from what it actually emitted. It is not a self-certification
    checkbox, which would be worth nothing given the judge and the judged are the same model.

  Paging exists (--from/--to) because a 70-thread round does not fit one screen, but paging cannot
  skip: the receipt only ever records threads this tool has printed IN FULL, and the ops-table gate
  demands every round thread be present. To get a table you must page through the lot.

Usage:
  VAULT=/tmp/pbs python3 triage-read.py <round_file>                 # every thread, whole
  VAULT=/tmp/pbs python3 triage-read.py <round_file> --from 1 --to 12
  VAULT=/tmp/pbs python3 triage-read.py <round_file> --receipt       # show receipt coverage
"""
import hashlib, json, os, re, sys


def normalise(text):
    """Collapse FORMATTING noise only. No word is ever removed.

    HTML marketing mail arrives as a few hundred words wrapped in thousands of blank lines and
    trailing spaces (the NCL cruise survey: 2,471 characters, roughly 200 of them content). Printing
    that verbatim buries the real mail and burns the reader's attention, which is the same failure
    truncation causes, arrived at from the other direction.

    This strips trailing whitespace and collapses runs of blank lines to one. It is applied BEFORE
    hashing, so what gets printed is exactly what gets hashed -- the receipt stays honest. Anything
    that removed words would have to be rejected on that basis alone.
    """
    text = re.sub(r"[ \t]+(\n)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"

ARGS = sys.argv[1:]
if not ARGS or ARGS[0].startswith("--"):
    print(__doc__)
    sys.exit(2)

ROUND_FILE = ARGS[0]


def _arg(flag, default=None):
    if flag in ARGS:
        try:
            return int(ARGS[ARGS.index(flag) + 1])
        except (IndexError, ValueError):
            return default
    return default


def thread_full_text(t):
    """The COMPLETE text of a thread: every message, whole. This is what gets hashed, so the
    receipt can only be satisfied by having emitted all of it."""
    parts = []
    for m in t.get("messages", []):
        parts.append(f"--- FROM {m.get('from','?')} | TO {m.get('to','?')} | {m.get('date','?')}\n")
        parts.append(normalise(m.get("body") or "") + "\n")
        for a in (m.get("attachments") or []):
            parts.append(f"[attachment: {a.get('filename')} {a.get('size')} bytes]\n")
    return "".join(parts)


def receipt_path(session_id):
    return f"/tmp/triage-read-{session_id}.json"


def load_receipt(session_id):
    p = receipt_path(session_id)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            pass
    return {"session_id": session_id, "threads": {}}


def main():
    rnd = json.load(open(ROUND_FILE))
    session_id = rnd.get("session_id", "unknown")
    threads = rnd.get("threads", [])
    receipt = load_receipt(session_id)

    if "--receipt" in ARGS:
        seen = set(receipt["threads"])
        missing = [(i, t) for i, t in enumerate(threads, 1) if t["id"] not in seen]
        print(f"read receipt {receipt_path(session_id)}")
        print(f"  round threads : {len(threads)}")
        print(f"  read in full  : {len(threads) - len(missing)}")
        if missing:
            print(f"  STILL UNREAD  : {len(missing)}")
            for i, t in missing:
                print(f"    #{i} {t.get('subject','')[:70]}")
            return 1
        print("  0 unread — the ops-table gate will accept this round")
        return 0

    lo = _arg("--from", 1)
    hi = _arg("--to", len(threads))

    for i, t in enumerate(threads, 1):
        if i < lo or i > hi:
            continue
        full = thread_full_text(t)
        f = t.get("facts", {})
        print("=" * 100)
        print(f"#{i} | {t.get('subject','(no subject)')}")
        print(f"    thread_id={t['id']} | from={t.get('from','?')}")
        print(f"    facts: last_direction={f.get('last_direction')} "
              f"pete_replied_since={f.get('pete_replied_since')} "
              f"team_replied_since={f.get('team_replied_since')} msgs={f.get('msg_count')}")
        for flag in ("partial_content", "truncated", "body_absent",
                     "body_empty_after_strip", "meeting_invite"):
            if t.get(flag):
                print(f"    !! FLAG {flag}={t.get(flag)}")
        print(f"    [{len(full)} chars, printed whole]")
        print("-" * 100)
        print(full)
        receipt["threads"][t["id"]] = {
            "sha256": hashlib.sha256(full.encode("utf-8")).hexdigest(),
            "chars": len(full),
            "subject": t.get("subject", ""),
        }

    json.dump(receipt, open(receipt_path(session_id), "w"), indent=1)
    unread = [t for t in threads if t["id"] not in receipt["threads"]]
    print("=" * 100)
    print(f"receipt: {len(receipt['threads'])}/{len(threads)} thread(s) read in full "
          f"-> {receipt_path(session_id)}")
    if unread:
        print(f"{len(unread)} STILL UNREAD — the ops-table gate will refuse until these are read:")
        for i, t in enumerate(threads, 1):
            if t in unread:
                print(f"  #{i} {t.get('subject','')[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
