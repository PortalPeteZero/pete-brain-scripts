#!/usr/bin/env python3
"""leakguard-context-gate.py -- PreToolUse hook: no LeakGuard tool runs, and no LeakGuard code is
edited, until the session has read the LeakGuard brief.

BORN 27 Jul 2026. Pete: "every time we work on leakguard it's a disaster, you never remember how it
works or the current SOP, it's a fucking disgrace."

He is right and it is not a memory problem. The front door, the multi-output SOP and the
verify-before-claiming SOP all exist and are all good. Nothing MAKES a session read them first, so
every session rediscovers the same ground halfway through, after the time is already spent. In that
one session:
  * the front door was opened only when code was about to be written, hours in;
  * status was reported from the fix plan instead of the live systems, and the plan was wrong -- it
    claimed five workstreams complete when 35 items were not done;
  * thingslog-api.py was called with a device number where a path belongs, and the resulting DNS
    error was reported to Pete as ThingsLog being unreachable;
  * a paginated endpoint was read as a whole fleet, three times over, in tools that had been in use
    for weeks.

Modelled on engine-contract-gate.py, which fixed the identical class of failure for the Triage and
Enquiry engines on 10 Jul.

Scope -- deliberately narrow:
  * Only EXECUTION blocks: a python invocation of a LeakGuard tool, or a write into /tmp/lg-hub.
    READING anything always passes -- reading is the remedy, never the offence.
  * lg-brief.py itself always passes (it IS the unlock).
  * Read-only queries through cc-sql.py / lg-sql.py pass, so a session can look before it leaps.
  * Everything non-LeakGuard passes untouched.
  * FAIL-OPEN on any internal error. A guard bug must never brick a session.

Exit contract (PreToolUse): exit 2 + stderr => BLOCK; exit 0 => allow.
"""
import json, os, re, sys, time

_SID = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
MARKER = f"/tmp/.leakguard-brief-ack-{_SID}" if _SID else "/tmp/.leakguard-brief-ack"
FRESH_SECS = 6 * 3600

# An EXECUTION of a LeakGuard tool. `head`/`cat`/`grep`/`sed` of the same file does not match,
# because there is no python token in front of it.
_LG_EXEC_RE = re.compile(
    r"python3?\s+(?:[^\n;|&]*?/)?"
    r"(lg-(?:verify|crosscheck|device-config|sql)"
    r"|leakguard-(?:name-sync)"
    r"|thingslog-api)\.py\b"
)

# A WRITE into the LeakGuard repo working copy. Reading it is fine.
# NARROW ON PURPOSE. The first cut matched `/tmp/lg-hub[^\n]*?(?:>|>>)`, so a plain
# `cd /tmp/lg-hub 2>/dev/null && git rev-parse` was read as a write and blocked — a pure read,
# refused. Caught within a minute of the gate going live, on the very command written to inspect the
# repo. A guard that blocks reading teaches people to work around it, which is worse than no guard,
# so this now names the write verbs rather than guessing from punctuation.
_LG_REPO_WRITE_RE = re.compile(
    r"git\s+(?:commit|push|add|checkout|reset|merge|rebase)\b[^\n]*?/tmp/lg-hub"
    r"|cd\s+/tmp/lg-hub[^\n]*?git\s+(?:commit|push|add|checkout|reset|merge|rebase)\b"
    r"|(?:\btee\b|\bcp\b|\bmv\b|\brm\b)[^\n]*?/tmp/lg-hub"
    r"|(?:^|[;&|]\s*)[^\n]*?>{1,2}\s*/tmp/lg-hub"
    r"|bunx\s+supabase\s+functions\s+deploy"
)

_UNLOCK_RE = re.compile(r"lg-brief\.py")

# lg-sql.py is on the tool list because a WRITE through it is a live database change. A plain SELECT
# is how you find things out, so it is allowed through unread -- the point of the gate is to stop
# blind ACTION, not blind curiosity.
_LG_SQL_RE = re.compile(r"python3?\s+(?:[^\n;|&]*?/)?lg-sql\.py\b")
_MUTATING_SQL_RE = re.compile(
    r"\b(insert\s+into|update\s+\w|delete\s+from|drop\s+|alter\s+|create\s+|truncate\s+|grant\s+|revoke\s+)",
    re.I,
)

MESSAGE = """⛔ LEAKGUARD CONTEXT GATE — the session brief has not been read.

This is not a formality. Every LeakGuard session so far has rediscovered the same ground halfway
through, after the time was spent, because the front door and the SOP exist and nothing made anyone
open them first. Pete's words, 27 Jul 2026: "every time we work on leakguard it's a disaster, you
never remember how it works or the current SOP."

Run this, read it, and the tools unlock for this session:

    VAULT=/tmp/pbs python3 /tmp/pbs/lg-brief.py --ack

It pulls the live fleet state, the outstanding audit findings, the pending alerts and the known-open
list, and it names the notes you must read in full before writing code. Nothing in it is quoted from
a plan document.

It also RECONCILES THINGSLOG AGAINST THE CRM — counters, pulse rate, map location, device name —
and it will not unlock anything if ThingsLog could not be reached. That is deliberate. Pete, 28 Jul
2026: "half of the problem is you always rely on our CRM and never check ThingsLog." Reading the
copy is one command; reading the record takes a login, so the copy wins and gets quoted. Every
correction that day came from exactly that. If ThingsLog is genuinely down, say so and do not act on
the CRM alone.

READING is never blocked. Look at anything you like — cat, grep, a SELECT through lg-sql.py — this
gate only stops ACTION taken before the context was loaded."""


def fresh():
    """Recent AND proving ThingsLog was actually read.

    The marker used to be a bare timestamp, so "briefed" meant "ran a command", not "saw the system
    of record". Pete, 28 Jul 2026: "I need a gate to make you read ThingsLog as well as the CRM."
    lg-brief.py now writes {"thingslog_reached": true} only after reconciling the two live, and
    refuses to write anything at all when ThingsLog cannot be reached.

    A pre-JSON marker left over from an older session is treated as NOT briefed — it cannot prove
    the reconciliation happened, and assuming in the gate's favour is how the gate becomes theatre.
    """
    try:
        if (time.time() - os.path.getmtime(MARKER)) >= FRESH_SECS:
            return False
        with open(MARKER) as fh:
            return json.load(fh).get("thingslog_reached") is True
    except (OSError, ValueError):
        return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        tool = payload.get("tool_name") or payload.get("tool") or ""
        ti = payload.get("tool_input") or {}

        if tool == "Bash":
            cmd = ti.get("command") or ""
        elif tool in ("Write", "Edit", "NotebookEdit"):
            fp = ti.get("file_path") or ""
            cmd = fp if "/tmp/lg-hub" in fp else ""
            if cmd and fresh():
                return 0
            if cmd:
                sys.stderr.write(MESSAGE + f"\n\n(blocked: editing {fp})\n")
                return 2
            return 0
        else:
            return 0

        if _UNLOCK_RE.search(cmd):
            return 0
        if fresh():
            return 0

        hit = None
        if _LG_SQL_RE.search(cmd):
            # a SELECT is fine; a write is not
            if _MUTATING_SQL_RE.search(cmd):
                hit = "a database change through lg-sql.py"
        if not hit and _LG_EXEC_RE.search(cmd):
            m = _LG_EXEC_RE.search(cmd)
            if not (m.group(1) == "lg-sql" and not _MUTATING_SQL_RE.search(cmd)):
                hit = f"{m.group(1)}.py"
        if not hit and _LG_REPO_WRITE_RE.search(cmd):
            hit = "a write to the LeakGuard repo or an edge-function deploy"

        if hit:
            sys.stderr.write(MESSAGE + f"\n\n(blocked: {hit})\n")
            return 2
        return 0
    except Exception:
        return 0  # fail open, always


if __name__ == "__main__":
    sys.exit(main())
