#!/usr/bin/env python3
"""
xhale-context-hook.py — the un-ignorable delivery of the Xhale operating rules.

A Claude Code UserPromptSubmit hook. When Pete's prompt mentions Xhale, Loren, physio, Passion Fit
or the coaching loop, it injects the handful of rules that MUST NOT be missed — above all: an entry
written into Xhale that also exists in Google Calendar has to be LINKED IN THE SAME ACTION.

WHY A HOOK AND NOT A NOTE. Pete, 6 Aug 2026: "Whenever I mention the word Xhale or Physio or things
like that, you then need to pull up that process, and you need to know to add this matching event
tag." A note only works if the session remembers to fetch it — measured at roughly a third of the
time. The harness runs this one every prompt, so it cannot be skipped. That is the same reasoning
that produced property-context-hook.py, and the same reasoning behind Pete's standing instruction:
"Don't make a memory - FIX THE PROCESS."

THE FAILURE IT EXISTS TO STOP. On 6 Aug 2026 the xhale_gcal_link table was built and then, forty
minutes later, a physio write-up went into Xhale without a link row — because the rule lived in a
design conversation rather than in the writing step. Unlinked, the next calendar sync would have
seen a physio event in the diary, found nothing it recognised in Xhale, and created a duplicate,
which is a notification to Loren that cannot be unsent.

The rules are embedded rather than fetched: a per-prompt hook must never wait on a network, and
these particular ones are stable. Everything else lives in [[xhale-operating-sop]], which the
injection points at. FAIL-OPEN: any error → exit 0, inject nothing, never block the prompt.

Wire in settings.json under hooks.UserPromptSubmit.
"""
import json
import re
import sys

# Deliberately specific. "Loren", "Xhale" and "physio" are unambiguous in Pete's world; generic
# words like "coach" or "swim" would fire on half his day and train him to ignore the injection.
TRIGGERS = re.compile(
    r"\b(xhale|trainxhale|loren|physio|physiotherap\w*|passion\s*fit|pf\s*journal"
    r"|coach'?s?\s+comment\w*|session\s+feedback)\b", re.I)

NOTE = """[xhale hook — the operating rules, because this prompt mentions the coaching loop]
Full detail: [[xhale-operating-sop]] · what the API can do: [[xhale-api-capability-map]] · plain
English: [[xhale-how-it-works]] · credentials: [[xhale-api-configuration]]. Helper: xhale-api.py.

⚠ LINK ON WRITE — the one that gets forgotten. Putting a diary entry into Xhale that ALSO exists in
Pete's Google Calendar? Then in ONE action: create the Xhale entry, find its calendar event, and
write the xhale_gcal_link row (xhale_session_id, gcal_event_id, on_date, title, origin='gcal').
It is NOT done until the link exists. Unlinked, the next sync cannot tell the two apart and creates
a duplicate — a notification to Loren that cannot be unsent. Stats lines need no link; they have no
calendar counterpart.

⚠ EVERY WRITE NOTIFIES LOREN. Get it right first time. Show Pete the exact text before writing.
Never post a partial or a placeholder. Read first — reads are free and notify nobody.

⚠ NO TIME FIELD. A session has a date and an integer `order`, nothing else. Put the time in the
title, Pete's way: "1pm - 2pm Physio", "Seminar 5.30pm". Daily stats go at order -1 (top of day);
order 0 is silently coerced to LAST; equal order breaks by oldest id, so order 1 loses to anything
Loren planned earlier.

⚠ subtitle is READ-ONLY in every state and fails silently with a 200. brief_description does not
render on a training session. Diary (discipline 17) is the only shape whose title we control.

⚠ Loren's replies are in the per-session messages[] AND a separate direct thread (contact 3280),
with ZERO overlap. coach_comments is always empty — she has never used it.

⚠ DELETE is a soft delete; tombstones stay in list responses with deleted=true. Filter them.

Stats format, locked by Pete: Sleep 79, 6h42 | RHR 52 | HRV 59
garmin_daily.sleep_hours is DECIMAL — 6.7 means 6h42, NOT 6h07. Convert it."""


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        prompt = (json.loads(raw).get("prompt") or "")
        if not TRIGGERS.search(prompt):
            return
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": NOTE}}))
    except Exception:
        return          # fail open, always


if __name__ == "__main__":
    main()
    sys.exit(0)
