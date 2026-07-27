#!/usr/bin/env python3
"""outbound-approval.py -- nothing goes out under Pete's name that he has not seen.

Pete, 27 Jul 2026: "you keep adding shit to my emails i havent asked for, if your doing
this you need a approval." He was right, with receipts: he asked for a vehicle to be added
to the fleet policy, and the email that went to his insurance broker also contained a
question about an employee's age, a request for documents, and later an assertion about that
employee -- none of which he said. He only ever saw the third email, and that one was fine.

The failure mode is composing and sending in one motion, so the fix removes that motion:
`gmail-api.py` send/reply_thread refuse a body with no approval row, and an approval is keyed
to the SHA-256 of the exact body. Change a character after approval and the hash changes and
it refuses again -- so "approved" can never drift from "what was sent".

This is the same shape as ee-send refusing an unstamped enquiry draft, generalised to all mail.

Flow:
  1. Draft the body and SHOW IT TO PETE IN FULL. Do not send.
  2. He says send.
  3. VAULT=/tmp/pbs python3 outbound-approval.py --approve <body-file> --to X --subject Y
  4. Send. The gate finds the row, marks it used, and lets it through.

An approval is single-use: `used_at` is stamped on send, so the same text cannot be
re-sent later without a fresh approval.

Usage:
  outbound-approval.py --approve <file> [--to ADDR] [--subject S] [--note N]
  outbound-approval.py --check <file>          # is this body approved and unused?
  outbound-approval.py --list                  # recent approvals
"""
import os, sys, json, hashlib, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")


def cc_sql(sql):
    out = subprocess.run(["python3", os.path.join(VAULT, "cc-sql.py"), sql],
                         capture_output=True, text=True,
                         env={**os.environ, "VAULT": VAULT}).stdout
    try:
        return json.loads(out)
    except Exception:
        raise RuntimeError(f"cc-sql failed: {out[:300]}")


def q(s):
    return "null" if s is None else "'" + str(s).replace("'", "''") + "'"


def digest(body):
    """Hash the body with whitespace normalised, so a stray trailing newline between the
    text Pete read and the text sent does not cause a spurious refusal. Any change to the
    WORDS still changes the hash, which is the point."""
    return hashlib.sha256(" ".join((body or "").split()).encode()).hexdigest()


def approve(body, to=None, subject=None, note=None):
    h = digest(body)
    preview = " ".join(body.split())[:400]
    cc_sql(f"""INSERT INTO public.outbound_approvals (body_sha256,to_addr,subject,preview,session_note)
               VALUES ({q(h)},{q(to)},{q(subject)},{q(preview)},{q(note)})
               ON CONFLICT (body_sha256) DO UPDATE
                 SET approved_at = now(), used_at = NULL,
                     to_addr = excluded.to_addr, subject = excluded.subject""")
    print(f"approved: {h[:16]}…  ({len(body.split())} words)")
    return h


def is_approved(body):
    """(ok, reason). Used by gmail-api before any send."""
    h = digest(body)
    try:
        rows = cc_sql(f"SELECT used_at FROM public.outbound_approvals WHERE body_sha256={q(h)}")
    except Exception as e:
        return True, f"gate unavailable ({e})"   # fail open: never strand a send on a broken check
    if not rows:
        return False, "this exact text has not been approved"
    if rows[0].get("used_at"):
        return False, "this text was already sent once; approve it again to resend"
    return True, ""


def mark_used(body):
    try:
        cc_sql(f"UPDATE public.outbound_approvals SET used_at=now() WHERE body_sha256={q(digest(body))}")
    except Exception:
        pass


if __name__ == "__main__":
    a = sys.argv
    if "--approve" in a:
        path = a[a.index("--approve") + 1]
        get = lambda f: a[a.index(f) + 1] if f in a else None
        approve(open(path).read(), get("--to"), get("--subject"), get("--note"))
    elif "--check" in a:
        ok, why = is_approved(open(a[a.index("--check") + 1]).read())
        print("APPROVED" if ok else f"NOT APPROVED — {why}")
        sys.exit(0 if ok else 1)
    elif "--list" in a:
        for r in cc_sql("SELECT left(body_sha256,12) AS h, to_addr, subject, approved_at, used_at "
                        "FROM public.outbound_approvals ORDER BY approved_at DESC LIMIT 20"):
            print(f"  {r['h']}  {r['approved_at'][:19]}  used={bool(r['used_at'])}  "
                  f"{(r['to_addr'] or '')[:32]}  {(r['subject'] or '')[:44]}")
    else:
        print(__doc__)
