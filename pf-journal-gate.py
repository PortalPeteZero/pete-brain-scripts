#!/usr/bin/env python3
"""pf-journal-gate.py — PreToolUse hook: nothing is FILED to the Passion Fit record until the
session has read the record in full through pf-brief.py.

BORN 4 Aug 2026. The journal walk that night opened by asking Pete how his race went. The full
race debrief — written WITH Pete, Loren's reply attached — had been in health_feedback since
1 Aug. The session had pulled one-line headlines and called that reading. Pete: "you literally
wrote the race feedback with me ... what system failed?" The honest answer was: none exists.
The pf-journal process note's STOP block ("load ALL context before you ask Pete a single
question") is prose, and prose loses to token thrift. Modelled on leakguard-context-gate.py and
engine-contract-gate.py, which killed the identical class for their surfaces.

Scope — deliberately narrow, same design rules as the LeakGuard gate:
  * Only FILING blocks: a mutating statement against health_journal, health_weekly or
    health_feedback (through cc-sql.py or any python invocation), or a mutating REST call at
    those tables. The filing act is the one moment every journal/weekly/feedback session must
    pass through, so it is where the gate lives.
  * READING always passes — SELECTs, the dashboard, anything. Reading is the remedy, never the
    offence.
  * pf-brief.py itself always passes (it IS the unlock, and truncation-guard separately refuses
    to let it be piped or redirected — the brief cannot be skimmed either).
  * The ack must be fresh (6h) AND carry the row counts pf-brief.py writes only after a
    complete, successful print. A bare touch of the marker file proves nothing.
  * FAIL-OPEN on any internal error. A guard bug must never brick a session.

Exit contract (PreToolUse): exit 2 + stderr => BLOCK; exit 0 => allow.
"""
import json
import os
import re
import sys
import time

_SID = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
MARKER = f"/tmp/.pf-brief-ack-{_SID}" if _SID else "/tmp/.pf-brief-ack"
FRESH_SECS = 6 * 3600

_UNLOCK_RE = re.compile(r"pf-brief\.py")

# The three tables that ARE the Passion Fit record. The name must stand in a SQL POSITION
# (after INSERT INTO / UPDATE / DELETE FROM), not merely appear — quoted text is data, and a
# command that greps a document mentioning "insert into health_journal" is reading, not filing.
# (Rule learned three times over in leakguard-context-gate.py: key on the ACT, not the name.)
_PF_SQL_WRITE_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
    r"(?:public\.)?health_(?:journal|weekly|feedback)\b", re.I)

# A python invocation at the start of the command or a segment — the same anchor the LeakGuard
# gate uses, so SQL quoted inside a commit message or a doc never matches on its own.
_SEG = r"(?:^|[\n;&|]\s*)"
_ENV = r"(?:\w+=\S*\s+)*"
_PY_RE = re.compile(_SEG + _ENV + r"python3?\b")

# The no-helper bypass: curl (or requests in a heredoc) straight at PostgREST. A mutating verb
# aimed at one of the three tables is filing, whatever the transport.
_PF_REST_WRITE_RE = re.compile(
    r"curl\b[^\n]*?-X\s*(?:POST|PATCH|PUT|DELETE)[^\n]*?/rest/v1/health_(?:journal|weekly|feedback)\b"
    r"|" + _SEG + _ENV + r"python3?\s(?=[\s\S]*?/rest/v1/health_(?:journal|weekly|feedback)\b)"
    r"(?=[\s\S]*?\.(?:post|patch|put|delete)\()", re.I)

MESSAGE = """⛔ PF JOURNAL GATE — the record has not been read this session.

On 4 Aug 2026 a journal walk opened by asking Pete how his race went. His full debrief had been
in health_feedback for three days, written with this assistant, Loren's reply attached. The
session had skimmed headlines and called it reading. Pete: "you literally wrote the race
feedback with me."

Nothing is filed to the Passion Fit record until the whole record has been in front of you.
Run this, bare (truncation-guard will refuse a pipe), read all of it, and filing unlocks:

    VAULT=/tmp/pbs python3 /tmp/pbs/pf-brief.py

It prints the last month IN FULL: every journal body, every feedback entry field including
Loren's replies, every weekly note (feedback half and plan half), and the raw Garmin rows.

READING is never blocked — SELECT anything you like. This gate only stops FILING before the
record was loaded, because a journal written off a skim is how tonight's failure happened."""


def fresh():
    """Recent AND carrying the counts pf-brief.py writes only after a complete print.
    An empty or older-format marker is treated as NOT briefed — assuming in the gate's favour
    is how a gate becomes theatre (lg gate, 28 Jul 2026)."""
    try:
        if (time.time() - os.path.getmtime(MARKER)) >= FRESH_SECS:
            return False
        with open(MARKER) as fh:
            mk = json.load(fh)
        return all(k in mk for k in
                   ("journal_rows", "feedback_rows", "weekly_rows", "garmin_rows"))
    except (OSError, ValueError):
        return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        if (payload.get("tool_name") or payload.get("tool") or "") != "Bash":
            return 0
        cmd = (payload.get("tool_input") or {}).get("command") or ""

        if _UNLOCK_RE.search(cmd):
            return 0
        if fresh():
            return 0

        hit = None
        if _PF_SQL_WRITE_RE.search(cmd) and _PY_RE.search(cmd):
            hit = "a mutating statement against the Passion Fit tables"
        elif _PF_REST_WRITE_RE.search(cmd):
            hit = "a mutating REST call at the Passion Fit tables"

        if hit:
            sys.stderr.write(MESSAGE + f"\n\n(blocked: {hit})\n")
            return 2
        return 0
    except Exception:
        return 0  # fail open, always


if __name__ == "__main__":
    sys.exit(main())
