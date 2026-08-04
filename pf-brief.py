#!/usr/bin/env python3
"""pf-brief.py — the Passion Fit pre-read, printed IN FULL. Running it IS the read.

BORN 4 Aug 2026. That evening's journal walk opened by asking Pete how his race went — a race
whose full debrief he and Claude had written together three days earlier, with Loren's reply
already attached, sitting in health_feedback. The session had "checked" the table by pulling
one-line headlines (`string_agg(headline)`) and treated the skim as having read the record.
Pete: "you literally wrote the race feedback with me ... how can you make such a silly mistake?
what system failed?"

The answer: no system. The pf-journal process note carries a STOP block saying load everything
first, but prose in a note is a conduct rule, and conduct rules lose to token thrift. Every
other repeat-failure surface here got a mechanical ritual that prints the record itself
(lg-brief.py, engine-manifest.py). This is the journal's.

WHAT IT PRINTS — the last 31 days, every body IN FULL, nothing summarised:
  1. health_journal   — every entry body (the practice's own record)
  2. health_feedback  — every session entry, EVERY field: Pete's feedback text, intro, splits,
                        Loren's replies. The exact data the 4 Aug skim reduced to headlines.
  3. health_weekly    — every weekly note in full (both the feedback half and the plan half)
  4. garmin_daily     — the raw rows (numbers are the source here, not a summary of it)

CONTRACT
  • Run it BARE. It is on truncation-guard's read-contract list: piping it to head/grep or
    redirecting it to a file is refused. Skimming the anti-skim tool is the first bypass anyone
    would reach for.
  • The ack marker (/tmp/.pf-brief-ack-<session>) is written ONLY after every query succeeded
    and the full print completed. pf-journal-gate.py refuses to file a journal, weekly note or
    feedback entry without a fresh marker. If the CC cannot be reached there is nothing to
    pre-read from, so nothing unlocks — say so instead of walking blind.
"""
import json
import os
import subprocess
import sys
import time

VAULT = os.environ.get("VAULT", "/tmp/pbs")
_SID = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
MARKER = f"/tmp/.pf-brief-ack-{_SID}" if _SID else "/tmp/.pf-brief-ack"
DAYS = 31

# Fields whose whole value is Pete's (or Loren's) words — printed first, labelled, in full.
_PROSE_KEYS = ("headline", "intro_block", "splits_block", "feedback_text", "body_note",
               "conditions_note", "nutrition_pre", "nutrition_during", "activity_name")


def _sql(query):
    """One query through cc-sql.py. Raises on any failure — the caller must not swallow it."""
    out = subprocess.run(
        ["python3", f"{VAULT}/cc-sql.py", query],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "VAULT": VAULT},
    )
    i = (out.stdout or "").find("[")
    if out.returncode != 0 or i < 0:
        raise RuntimeError(f"cc-sql failed: {(out.stdout or out.stderr)[:300]}")
    return json.loads(out.stdout[i:])


def _hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _feedback_entry(e):
    """Print EVERY field of a feedback entry. Iterating the dict, not a hand-kept field list,
    so a field added later cannot silently vanish from the brief."""
    for k in _PROSE_KEYS:
        v = e.get(k)
        if v:
            print(f"--- {k} ---")
            print(v)
    lr = e.get("loren_reply")
    if lr:
        print("--- loren_reply ---")
        if isinstance(lr, dict):
            print(f"({lr.get('coach', 'Loren')}, {lr.get('replied_at', '?')})")
            print(lr.get("text", ""))
        else:
            print(lr)
    rest = {k: v for k, v in e.items()
            if k not in _PROSE_KEYS and k != "loren_reply" and v not in (None, "", [])}
    if rest:
        print("--- other fields ---")
        print(json.dumps(rest, ensure_ascii=False, indent=1, sort_keys=True))


def main():
    counts = {}

    _hr(f"PF BRIEF — the record in full, last {DAYS} days — generated for session {_SID or '(no id)'}")
    print("Read ALL of it. The walk may not open with a question the record already answers.")

    _hr("1/4 · health_journal — every entry, full body")
    rows = _sql(f"SELECT date, body FROM health_journal "
                f"WHERE date >= current_date - {DAYS} ORDER BY date")
    counts["journal_rows"] = len(rows)
    if not rows:
        print("(no journal entries in range)")
    for r in rows:
        _hr(f"JOURNAL {r['date']}")
        print(r["body"])

    _hr("2/4 · health_feedback — every session entry, every field")
    rows = _sql(f"SELECT date, payload FROM health_feedback "
                f"WHERE date >= current_date - {DAYS} ORDER BY date")
    counts["feedback_rows"] = len(rows)
    if not rows:
        print("(no feedback entries in range)")
    for r in rows:
        entries = (r.get("payload") or {}).get("entries") or []
        for n, e in enumerate(entries, 1):
            _hr(f"FEEDBACK {r['date']}" + (f" · entry {n}/{len(entries)}" if len(entries) > 1 else ""))
            _feedback_entry(e)

    _hr("3/4 · health_weekly — every weekly note in full (feedback half AND plan half)")
    rows = _sql(f"SELECT iso_week, body FROM health_weekly "
                f"WHERE to_date(iso_week || '-1', 'IYYY-\"W\"IW-ID') >= current_date - {DAYS + 7} "
                f"ORDER BY iso_week")
    counts["weekly_rows"] = len(rows)
    if not rows:
        print("(no weekly notes in range)")
    for r in rows:
        _hr(f"WEEKLY {r['iso_week']}")
        print(r["body"])

    _hr("4/4 · garmin_daily — raw rows")
    rows = _sql(f"SELECT date, sleep_score, sleep_hours, hrv, resting_hr, steps, stress_avg, "
                f"body_battery_high, readiness, readiness_label, updated_at FROM garmin_daily "
                f"WHERE date >= current_date - {DAYS} ORDER BY date")
    counts["garmin_rows"] = len(rows)
    hdr = ["date", "sleep_score", "sleep_hours", "hrv", "resting_hr", "steps",
           "stress_avg", "body_battery_high", "readiness", "readiness_label"]
    print("  ".join(hdr))
    for r in rows:
        print("  ".join(str(r.get(k)) for k in hdr))

    marker = {**counts, "ts": time.time(), "days": DAYS}
    with open(MARKER, "w") as fh:
        json.dump(marker, fh)
    _hr("BRIEF COMPLETE — ack written")
    print(f"{MARKER}: {json.dumps(counts)}")
    print("Filing to health_journal / health_weekly / health_feedback is now unlocked "
          "for 6 hours in this session.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Fail CLOSED: no ack on any failure. Walking the journal without the record is the
        # offence this tool exists to stop, so a broken pre-read must not unlock anything.
        sys.stderr.write(f"pf-brief FAILED — no ack written: {e}\n")
        sys.exit(1)
