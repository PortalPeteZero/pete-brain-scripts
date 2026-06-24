#!/usr/bin/env python3
"""jotform-calendar-truth-builder.py
================================================================
Builds `Properties/Sygma Solutions Website/data/training-evaluations/
calendar-truth-cache.json` from the trainers' Google Calendars.

This is the canonical helper that replaces the one-off session-script
that originally produced the cache. Wired into
`jotform-training-eval-sync.py` to run as part of the Monday cron.

Cache shape (additive to the original; old fields preserved):

  {
    "generated_at_utc": "2026-05-31T19:45:00+00:00",
    "from_date":  "2026-05-01",
    "to_date":    "2026-06-30",
    "by_trainer_date": {
      "Trainer Name|YYYY-MM-DD": [
        {
          # Original fields (kept for backward compatibility)
          "event_summary": "...",
          "code": "C004",
          "customer": "...",
          "match_via": "alias-full" | "name-full" | "name-token" | "no-match",
          # NEW fields (2026-05-31 build)
          "start_iso":             "2026-05-15T09:00:00+01:00",  # null when all_day
          "all_day":               false,
          "duration_skipped_2day": false,                          # true for all_day events
          "calendar_event_id":     "abc123...",                    # for traceability
        }
      ]
    }
  }

Timezone discipline (per 2026-05-30 + 2026-05-31 lessons):
  - All datetimes parsed with explicit tzinfo.
  - Calendar `start.dateTime` carries an offset suffix (+01:00 / +00:00 / Z)
    which the parser respects. Auto-handles the GMT/BST switch.
  - Date keys (trainer|date) use Europe/London local date.

Course matching:
  - Reads `_course-map.yaml` for canonical names + aliases.
  - Match priority: alias-full > name-full > name-token > no-match.

Scope:
  - Default window: from 2026-05-01 (post-cutover) to today + 30 days.
  - Pre-2026-05-01 not pulled (two-era rule: pre-cutover = best-guess from
    delegate free-text, calendar-truth not reliable historically).

Created 2026-05-31 evening session.
"""

from __future__ import annotations
import importlib.util
import json
import re
import sys
import yaml
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

VAULT = Path("/tmp/pbs")
DATA_OUT = VAULT / "Properties/Sygma Solutions Website/data/training-evaluations/calendar-truth-cache.json"
COURSE_MAP = VAULT / "Businesses/sygma-solutions/training/courses/_course-map.yaml"
SCRIPTS = VAULT / "Library/processes/scripts"

UK_TZ = ZoneInfo("Europe/London")
# Cache scope: April 2026 onwards (gives missing-feedback detection back to April).
# The two-era rule (course-code accuracy) still kicks in at 2026-05-01 — the aggregator's
# duration computation respects that separately. Missing-feedback only needs the calendar
# event to exist; it doesn't depend on accurate course-code matching.
CACHE_START = date(2026, 4, 1)
CUTOVER = CACHE_START  # backward-compat alias for any external callers

# Trainer canonical name → calendar email
# (sourced from training-audit.py 2026-05-31; updated when roster changes)
TRAINERS = {
    "Andy Foster":       "andrew.foster@sygma-solutions.com",
    "Andy Bartholomew":  "andy.bartholomew@sygma-solutions.com",
    "Gareth Phillips":   "gareth.phillips@sygma-solutions.com",
    "Geoff Astley":      "geoff.astley@sygma-solutions.com",
    "Mark Pearce":       "mark.pearce@sygma-solutions.com",
    "Neal Sadd":         "neal.sadd@sygma-solutions.com",
    "Paul Baxter":       "paul.baxter@sygma-solutions.com",
    "Jim Ashcroft":      "jim.ashcroft@sygma-solutions.com",
    "Steve Mellor":      "steve.mellor@sygma-solutions.com",
    "Steve Scales":      "steve.scales@sygma-solutions.com",
}

# Words that, alone, are too generic to anchor a course match
STOPWORDS = {
    "and", "with", "the", "for", "of", "a", "an", "day", "days",
    "training", "course", "session", "test", "intro", "1", "2", "3", "4", "5",
}


def _load_calendar_api():
    spec = importlib.util.spec_from_file_location(
        "calendar_api", SCRIPTS / "calendar-api.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CalendarAPI()


def _load_courses() -> list[dict]:
    """Return list of course dicts with normalised match-keys."""
    raw = yaml.safe_load(COURSE_MAP.read_text())
    out = []
    for c in raw.get("courses", []):
        if c.get("status") == "retired":
            continue
        code = c.get("code")
        name = c.get("name", "")
        aliases = [a.get("name", "") for a in (c.get("aliases") or []) if a.get("name")]
        # Build normalised match candidates: lowercase, alphanumeric+spaces only
        def norm(s):
            return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", s.lower())).strip()
        out.append({
            "code": code,
            "name": name,
            "name_norm": norm(name),
            "aliases_norm": [norm(a) for a in aliases if a],
        })
    return out


def _match_course(event_summary: str, courses: list[dict]) -> tuple[str | None, str]:
    """Return (course_code, match_via). match_via is 'alias-full', 'name-full',
    'name-token', or 'no-match'."""
    if not event_summary:
        return None, "no-match"
    summary_norm = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", event_summary.lower())).strip()

    # 1. Try alias substring (longest alias wins)
    best = (0, None)
    for c in courses:
        for a in c["aliases_norm"]:
            if a and a in summary_norm and len(a) > best[0]:
                best = (len(a), c["code"])
    if best[1]:
        return best[1], "alias-full"

    # 2. Try canonical name substring (longest name wins)
    best = (0, None)
    for c in courses:
        if c["name_norm"] and c["name_norm"] in summary_norm and len(c["name_norm"]) > best[0]:
            best = (len(c["name_norm"]), c["code"])
    if best[1]:
        return best[1], "name-full"

    # 3. Token-bag overlap: count non-stopword tokens of course name present in summary
    summary_tokens = set(summary_norm.split()) - STOPWORDS
    best = (0, None)
    for c in courses:
        c_tokens = set(c["name_norm"].split()) - STOPWORDS
        if not c_tokens: continue
        overlap = c_tokens & summary_tokens
        # Require >= 60% of course name tokens to be present, min 2 tokens
        if len(c_tokens) >= 2 and len(overlap) / len(c_tokens) >= 0.6 and len(overlap) >= 2:
            score = len(overlap)
            if score > best[0]:
                best = (score, c["code"])
    if best[1]:
        return best[1], "name-token"

    return None, "no-match"


def _extract_customer(event_summary: str) -> str | None:
    """Heuristic: assume customer name is the prefix before the first ' - '
    or the substring ending with 'Ltd'/'Limited'."""
    if not event_summary:
        return None
    # Try Ltd / Limited prefix
    m = re.match(r"^(.+?(?:\sLtd|\sLimited|\sPLC))(?:\s|$|,|-)", event_summary, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Try first " - " split (e.g. "Customer Name - Course Title")
    if " - " in event_summary:
        first = event_summary.split(" - ")[0].strip()
        # Reject if it starts with a digit ("1 Day GPR training") — that's a course descriptor
        if not re.match(r"^\d", first):
            return first
    return None


def _parse_event_start(ev: dict) -> tuple[str | None, bool]:
    """Return (start_iso_with_tz, is_all_day). start_iso is None when all_day."""
    start = ev.get("start", {})
    if "dateTime" in start:
        # Timed event — RFC3339 with offset suffix
        dt_str = start["dateTime"]
        # Validate parseable
        try:
            datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except ValueError:
            return None, False
        return dt_str, False
    elif "date" in start:
        # All-day event
        return None, True
    return None, False


def _uk_dates_from_event(ev: dict) -> list[date]:
    """The trainer-local dates the event covers (Mon-Sun grain, inclusive of every
    day from start to end). Returns [] if can't parse.

    For all-day events: Google Calendar end.date is EXCLUSIVE, so a 2-day
    event has start.date = Day1, end.date = Day3 → returns [Day1, Day2].

    For timed events: returns every UK-local calendar date the span crosses,
    so a Wed 07:30 → Thu 14:00 event returns [Wed, Thu]."""
    start = ev.get("start", {})
    end = ev.get("end", {})
    try:
        if "dateTime" in start:
            sdt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone(UK_TZ)
            edt_str = end.get("dateTime") or start["dateTime"]
            edt = datetime.fromisoformat(edt_str.replace("Z", "+00:00")).astimezone(UK_TZ)
            sd, ed = sdt.date(), edt.date()
        elif "date" in start:
            sd = date.fromisoformat(start["date"])
            ed_str = end.get("date") or start["date"]
            ed = date.fromisoformat(ed_str) - timedelta(days=1)  # end.date is exclusive for all-day
        else:
            return []
    except (ValueError, KeyError):
        return []
    if ed < sd:
        ed = sd
    out = []
    d = sd
    while d <= ed:
        out.append(d)
        d += timedelta(days=1)
    return out


def build(from_date: date | None = None, to_date: date | None = None, verbose: bool = True) -> dict:
    """Pull all trainer calendars + build the truth cache."""
    if from_date is None:
        from_date = CACHE_START
    if from_date < CACHE_START:
        if verbose:
            print(f"  Warning: from_date {from_date} is before cache-start {CACHE_START}; clamping")
        from_date = CACHE_START
    if to_date is None:
        to_date = date.today()  # past + today only; future events are zero-value for missing-feedback + duration
    if to_date > date.today():
        if verbose:
            print(f"  Warning: to_date {to_date} is in the future; clamping to today.")
        to_date = date.today()

    courses = _load_courses()
    cal = _load_calendar_api()

    if verbose:
        print(f"Building calendar-truth cache from {from_date} to {to_date}")
        print(f"  Trainers: {len(TRAINERS)}  Courses: {len(courses)}")

    time_min = datetime.combine(from_date, datetime.min.time()).replace(tzinfo=UK_TZ).isoformat()
    time_max = datetime.combine(to_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=UK_TZ).isoformat()

    by_trainer_date: dict[str, list[dict]] = {}
    total_events = 0
    total_matched = 0
    skipped_2day = 0

    for trainer_name, trainer_email in TRAINERS.items():
        try:
            events = cal.list_events(
                calendar_id=trainer_email,
                time_min=time_min,
                time_max=time_max,
                max_results=2500,
            )
        except Exception as e:
            if verbose:
                print(f"  WARN {trainer_name}: {e}")
            continue

        # First pass: collect candidate entries per trainer-date with filtering
        candidates_by_key: dict[str, list[dict]] = {}
        for ev in events:
            summary = ev.get("summary", "") or ""
            if not summary.strip():
                continue
            lower = summary.lower().strip()
            # Skip obvious non-training events + admin notes that contaminate trainer-date cache
            if any(kw in lower for kw in [
                "holiday", "annual leave", "day off", "doctor", "dentist",
                "nights worked away", "lunch", "travel ",
                "mandatory teams meeting", "teams prep", "prep with",
                "meet up between", "team meeting", "discuss ",
                "please read description", "internal -", "internal-",
                "sigma locator demo", "kier doc", "office", "training" if lower == "training" else "__none__",
            ]):
                continue
            # Hotel / accommodation patterns (often all-day, adjacent to training)
            if lower.startswith("stay at ") or lower.startswith("hotel ") or " hotel " in lower:
                continue
            # Single-word generic summaries
            if len(lower.split()) <= 2 and not any(c.isdigit() for c in lower):
                continue
            # Things starting with "Please " are usually instructions, not deliveries
            if lower.startswith("please "):
                continue

            uk_dates = _uk_dates_from_event(ev)
            uk_dates = [d for d in uk_dates if CUTOVER <= d <= to_date]
            if not uk_dates:
                continue

            code, match_via = _match_course(summary, courses)
            customer = _extract_customer(summary)
            start_iso, all_day = _parse_event_start(ev)
            # Multi-day events (whether all-day or timed-spanning-midnight) are
            # 2-day courses by Pete's convention — duration not computable per
            # delivery (feedback all lands on Day 2). Single-day timed events
            # are the only case where duration is computable.
            is_multi_day = len(uk_dates) > 1
            duration_skipped = all_day or is_multi_day

            for day_idx, uk_d in enumerate(uk_dates):
                entry = {
                    "event_summary": summary,
                    "code": code,
                    "customer": customer,
                    "match_via": match_via,
                    # Only the FIRST day carries the real start_iso (for any single-day
                    # duration math); secondary days have null to avoid double-counting.
                    "start_iso": start_iso if day_idx == 0 else None,
                    "all_day": all_day,
                    "duration_skipped_2day": duration_skipped,
                    "calendar_event_id": ev.get("id"),
                    "is_multi_day": is_multi_day,
                    "day_of": day_idx + 1,             # 1, 2, ...
                    "day_total": len(uk_dates),         # 2, 3, ...
                }
                key = f"{trainer_name}|{uk_d.isoformat()}"
                candidates_by_key.setdefault(key, []).append(entry)

        # Second pass: ONE event per trainer-date (Pete corrected 2026-05-31:
        # trainers never deliver multiple courses on the same day). Pick the best
        # candidate and discard the rest.
        def _score(entry: dict) -> tuple:
            # Higher tuple wins. Prefer: matched code > timed (not all-day) >
            # customer extracted > longest summary
            has_code = 1 if entry["code"] else 0
            is_timed = 0 if entry["all_day"] else 1
            has_customer = 1 if entry["customer"] else 0
            summary_len = len(entry["event_summary"])
            return (has_code, is_timed, has_customer, summary_len)

        for key, entries in candidates_by_key.items():
            best = max(entries, key=_score)
            by_trainer_date[key] = [best]
            total_events += 1
            if best["code"]:
                total_matched += 1
            if best["all_day"]:
                skipped_2day += 1

        # Always populate the empty-day slots between events for the trainer
        # (matches the existing cache shape so missing-feedback detector can scan)
        if events:
            d_iter = from_date
            while d_iter <= to_date:
                k = f"{trainer_name}|{d_iter.isoformat()}"
                by_trainer_date.setdefault(k, [])
                d_iter += timedelta(days=1)

    out = {
        "generated_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds"),
        "from_date": from_date.isoformat(),
        "to_date":   to_date.isoformat(),
        "stats": {
            "trainers": len(TRAINERS),
            "events_in_window": total_events,
            "events_matched_to_course": total_matched,
            "events_all_day_skipped_for_duration": skipped_2day,
        },
        "by_trainer_date": by_trainer_date,
    }

    # === INVARIANT SELF-CHECKS — fail loudly if violated ===
    # These are non-negotiable. The Monday cron MUST refuse to write a cache
    # that violates these and surface a P2 Asana for Pete's attention.
    invariant_violations = []

    # Invariant 1: at most one event per trainer-date
    # (Pete-correction lesson: Library/lessons/2026-05-31-truth-builder-one-event-per-trainer-date.md)
    multi = {k: v for k, v in by_trainer_date.items() if len(v) > 1}
    if multi:
        invariant_violations.append(
            f"ONE-EVENT-PER-TRAINER-DATE invariant violated: {len(multi)} trainer-dates have multiple events. "
            f"Examples: {list(multi.keys())[:3]}. The truth-builder's second-pass best-match selection is broken."
        )

    # Invariant 2: no future-dated entries
    # (date keys are Europe/London local; comparing to UTC today is fine — only fires on
    # genuinely future entries, edge of midnight tolerated)
    today = date.today()
    future_keys = [k for k, evs in by_trainer_date.items() if evs and date.fromisoformat(k.split("|", 1)[1]) > today]
    if future_keys:
        invariant_violations.append(
            f"NO-FUTURE-DATES invariant violated: {len(future_keys)} trainer-dates are in the future. "
            f"Examples: {future_keys[:3]}. Truth-builder scope should be past + today only."
        )

    # Invariant 3: every Day-1 event has tz-aware start_iso when not all_day.
    # (Day-2+ entries of multi-day events have start_iso=null by design.)
    for k, evs in by_trainer_date.items():
        for ev in evs:
            if ev.get("all_day"): continue
            if ev.get("day_of", 1) != 1: continue  # Day-2+ allowed to have null
            s = ev.get("start_iso")
            if not s or ("+" not in s and not s.endswith("Z")):
                invariant_violations.append(
                    f"TZ-AWARE-START-ISO invariant violated for {k}: start_iso={s!r} lacks offset. "
                    "All timed Day-1 event starts must carry an explicit timezone."
                )
                break

    out["invariant_violations"] = invariant_violations

    if verbose:
        print(f"  Total events in window: {total_events}")
        print(f"  Matched to course: {total_matched}")
        print(f"  All-day (2-day course, duration skipped): {skipped_2day}")
        print(f"  Trainer-date keys: {len(by_trainer_date)}")
        if invariant_violations:
            print(f"  ⚠ INVARIANT VIOLATIONS ({len(invariant_violations)}):")
            for v in invariant_violations:
                print(f"     - {v}")
        else:
            print(f"  ✓ All invariants pass.")

    return out


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="from_date", help="YYYY-MM-DD (default: 2026-05-01)")
    p.add_argument("--to", dest="to_date", help="YYYY-MM-DD (default: today + 30d)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    fd = date.fromisoformat(args.from_date) if args.from_date else None
    td = date.fromisoformat(args.to_date) if args.to_date else None

    cache = build(from_date=fd, to_date=td, verbose=not args.quiet)

    if args.dry_run:
        print(json.dumps(cache, indent=2)[:2000])
        print("...")
        print("[dry-run; not written]")
        return

    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(json.dumps(cache, indent=1))
    if not args.quiet:
        print(f"Wrote {DATA_OUT}")


if __name__ == "__main__":
    main()
