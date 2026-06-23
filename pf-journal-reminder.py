#!/usr/bin/env python3
"""pf-journal-reminder.py — daily 6pm nudge for Pete's PF framework journal practice.

Headless extraction of the old Cowork SKILL.md (the prompt only ran a deterministic script anyway).
Sends Pete a short reminder to open Cowork and do the 10-minute PF journal. Best-effort continuity:
surfaces yesterday's "one lesson for tomorrow" if the journal file is reachable (local vault on the
Mac, or Drive once a credentialled read is wired). On Railway the journal lives in My Drive and isn't
mounted, so continuity gracefully degrades to a plain nudge (logged) — the core nudge always fires.

Recipient is Pete only, so this is safe to fire ad-hoc. Voice: plain, no em dashes/semicolons
(voice-principles). Canonical practice doc: Library/processes/pf-journal.md.
"""
# CRON-META
# what: Daily 6pm reminder email to Pete for the PF framework journal practice
# why: Nudges Pete to open Cowork and do the 10-minute PF journal (carries yesterday's lesson when reachable)
# reads: schedule + yesterday's PF journal entry (best-effort)
# writes: reminder email to Pete
# entity: personal
# report:
# schedule: 10 18 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import os
import sys
import importlib.util
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")
PETE = "pete.ashcroft@sygma-solutions.com"


def _yesterday_lesson(yesterday):
    """Best-effort read of yesterday's 'one lesson for tomorrow'. Returns None if unreachable."""
    candidates = [
        os.path.join(VAULT, f"Personal/passion-fit/journal/{yesterday}.md"),
        os.path.expanduser(
            f"~/Library/CloudStorage/GoogleDrive-pete.ashcroft@sygma-solutions.com/My Drive/Passion Fit/journal/{yesterday}.md"
        ),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        text = open(path).read()
        marker = "## One lesson for tomorrow"
        if marker not in text:
            return None
        after = text.split(marker, 1)[1].strip()
        for cut in ("\n## ", "\n---"):
            i = after.find(cut)
            if i > 0:
                after = after[:i]
        return after.strip() or None
    return None


def main():
    tz = ZoneInfo("Atlantic/Canary")
    today = datetime.now(tz).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    continuity = _yesterday_lesson(yesterday)

    lines = [
        "Hey Pete,",
        "",
        '10 mins on the PF framework. Open Cowork when you are ready and say "journal".',
        "",
    ]
    if continuity:
        lines += ["Yesterday's lesson for today was:", "", f"  {continuity}", "", "How did that land?", ""]
    lines.append("See you in Cowork.")
    body = "\n".join(lines)

    spec = importlib.util.spec_from_file_location("gmail_api", os.path.join(VAULT, "Library/processes/scripts/gmail-api.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    g = mod.GmailAPI()
    result = g.send(to=PETE, subject="Journal time (10 mins)", body=body)
    print(f"pf-journal-reminder: SENT msg_id={result.get('id')} continuity={'yes' if continuity else 'no (degraded on cloud)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
