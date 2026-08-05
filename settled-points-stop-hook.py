#!/usr/bin/env python3
"""settled-points-stop-hook.py — refuse to end a turn that re-opens something Pete has settled.

Pete, 5 Aug 2026: "the 1pm and 3pm thing we have been through 3 times now" ... and when I wrote
it into a scratchpad note instead of building a gate: **"you did that last time but it didnt stop
you"**.

He is right, and the failure is exactly the one he named earlier the same day about lessons: a
note is something I have to remember to read, so it stops nothing. A ruling only holds if
something mechanical enforces it.

WHAT IT DOES
  Reads THIS turn's final reply out of the transcript, matches it against the active rows in
  `public.settled_points`, and blocks with the ruling if a settled point has been raised again.

WHY THE REGISTER IS A TABLE, NOT A FILE
  Same reason as everything else here: a file in /tmp is gone next session and a note in a vault
  doc is only as good as my remembering to read it. The table is queried by the hook on every
  turn end, so a ruling Pete gives once keeps working without either of us doing anything.

  Adding one:
    scope    the thing it applies to ("clancy-153523", or "global")
    subject  short name of the point
    ruling   what was decided and why, in Pete's terms — this is what gets printed back at me
    patterns phrases that indicate I am raising it again

FALSE POSITIVES ARE THE REAL RISK
  A gate that fires when Pete himself asks about a settled point, or that blocks a factual
  restatement he requested, is worse than no gate — it trains both of us to override it. So:

  * If the USER's own last message mentions the point, the hook stays quiet. He is allowed to
    re-open anything; the rule is about ME volunteering it.
  * A pattern must hit as a WHOLE WORD, and short numeric patterns ("1pm") need two or more
    distinct patterns from the same row before it fires, so a passing mention of a time in an
    unrelated context does not trip it.
  * Quoting a source document verbatim is not raising a point. A line inside quotation marks is
    excluded before matching.

Install as a Stop hook. Exit 0 = nothing settled was re-opened. Exit 2 = blocked, with the ruling.
"""
import json, os, re, sys, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"


def register():
    k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
    H = {"apikey": k["service_role_key"], "Authorization": f"Bearer {k['service_role_key']}"}
    req = urllib.request.Request(
        k["url"] + "/rest/v1/settled_points?select=*&active=is.true&order=id.asc&limit=500",
        headers=H)
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


def read_transcript(path):
    """(my final reply this turn, the user's last message)."""
    replies, users = [], []
    try:
        with open(path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                msg = e.get("message") or {}
                role, content = msg.get("role"), msg.get("content")
                if role == "assistant" and isinstance(content, list):
                    txt = "".join(c.get("text", "") for c in content if c.get("type") == "text")
                    if txt.strip():
                        replies.append(txt)
                elif role == "user":
                    if isinstance(content, str):
                        users.append(content)
                    elif isinstance(content, list):
                        users.append(" ".join(c.get("text", "") for c in content
                                              if isinstance(c, dict) and c.get("type") == "text"))
    except Exception:
        return "", ""
    return (replies[-1] if replies else ""), (users[-1] if users else "")


def strip_quotes(text):
    """Quoting a document is not raising a point."""
    return re.sub(r'["“”][^"“”]{6,600}["“”]', " ", text or "")


def hits(text, patterns):
    found = []
    for p in patterns or []:
        if re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", text, re.I):
            found.append(p)
    return found


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)                                  # fail open
    reply, user = read_transcript(payload.get("transcript_path", ""))
    if not reply.strip():
        sys.exit(0)
    try:
        rows = register()
    except Exception:
        sys.exit(0)                                  # cannot read the register -> never block

    body = strip_quotes(reply)
    fired = []
    for r in rows:
        got = hits(body, r["patterns"])
        if not got:
            continue
        # Pete raising it himself is always allowed.
        if hits(user, r["patterns"]):
            continue
        # A single short numeric pattern is too weak on its own.
        if len(got) == 1 and len(got[0]) <= 5:
            continue
        fired.append((r, got))

    if not fired:
        sys.exit(0)

    out = ["BLOCKED by settled-points: you have re-opened something Pete has already settled.", ""]
    for r, got in fired:
        out.append(f"  [{r['scope']}] {r['subject']}  "
                   f"(settled {r['settled_on']} by {r['settled_by']}, raised {r['times_raised']}x)")
        out.append(f"      RULING: {r['ruling']}")
        out.append(f"      you said: {', '.join(got)}")
        out.append("")
    out += ["Take it out of the reply and finish the turn. If you genuinely believe the ruling is",
            "wrong, say so in one line and ask him -- do not restate the point as if it were new.",
            "",
            'Pete, 5 Aug 2026, on writing this into a note instead of a gate: "you did that last',
            'time but it didnt stop you."']
    sys.stderr.write("\n".join(out) + "\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
