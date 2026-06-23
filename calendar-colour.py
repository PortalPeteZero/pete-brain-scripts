#!/usr/bin/env python3
"""calendar-colour.py — colour-code Pete's primary calendar events.

Scheme:
- Sygma work    → colourId 9  (Blueberry, deep blue)   — "Sygma = Blue"
- CD work       → colourId 5  (Banana, yellow)          — "CD = Yellow"
- Personal      → colourId 2  (Sage, soft green)        — "Personal = Sage"
- Travel        → colourId 6  (Tangerine, orange)       — "Travel = Orange"

Personal flipped 2026-05-24 from 11 (Tomato) to 2 (Sage) — Tomato and Tangerine were
visually too close. Sweep of existing Tomato-coloured events to Sage handled at flip time.

Anything that doesn't match any rule is left alone (no colour clobber).

Usage:
  python3 calendar-colour.py dry-run [--days-back N] [--days-ahead N]
  python3 calendar-colour.py apply   [--days-back N] [--days-ahead N]
  python3 calendar-colour.py apply-recent      # last-24h-only mode for the daily cron
  python3 calendar-colour.py classify "Title" "[loc]" "[organiser]" "[attendees]"  # debug single event
"""
import argparse, datetime, importlib.util, re, sys, json
from pathlib import Path

SCRIPTS = Path("/Users/peterashcroft/Second Brain/Library/processes/scripts")
spec = importlib.util.spec_from_file_location("c", SCRIPTS / "calendar-api.py")
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)

COLOURS = {
    "sygma":    "9",   # Blueberry (deep blue) — Sygma brand colour
    "cd":       "5",   # Banana (yellow) — Canary Detect
    "personal": "2",   # Sage (soft green) — personal/home stuff (was 11 Tomato until 2026-05-24)
    "travel":   "6",   # Tangerine (orange) — travel
}

# ---- Categorisation rules ----

TRAVEL_PATTERNS = [
    re.compile(r"\b(flight|fly to|flying to)\b", re.I),
    re.compile(r"\b(FR|BA|U2|EZY|RYR|VY|IB|TP|KL|LH|AF)\s*\d{2,4}\b"),
    re.compile(r"\b(check[ -]?in|check[ -]?out)\b", re.I),
    re.compile(r"\b(hotel|inn|lodge|resort|airbnb|booking\.com|hotels\.com|travelodge|premier inn|holiday inn|marriott|hilton|hyatt|ihg|best western|ibis|novotel|radisson|park inn)\b", re.I),
    re.compile(r"\b(car hire|car rental|hire car|enterprise|hertz|avis|sixt|europcar|budget rent)\b", re.I),
    re.compile(r"\barrive at .*(airport|terminal)\b", re.I),
    re.compile(r"\b(LHR|LGW|MAN|EDI|GLA|BHX|STN|LTN|ACE|TFS|LPA|FUE|MAD|BCN|AGP|PMI)\b"),
    re.compile(r"\b(airport|terminal\s*\d)\b", re.I),
]

# CD field & customer signals (Lanzarote leak / LeakGuard / pool world)
CD_KEYWORDS = [
    # Strong (slots 0-4): brand / product / strong activity keywords
    re.compile(r"\b(canary[\s-]?detect|leak[\s-]?guard|leakbusters|leak[\s-]?busters|leaky[\s-]?finders|pipebusters)\b", re.I),
    re.compile(r"\b(leak|leakguard|leak guard)\b", re.I),
    re.compile(r"\bcommunity survey\b", re.I),
    re.compile(r"\b(ecofinish|eco[\s-]finish)\b", re.I),
    re.compile(r"\bpool (job|cleaning|maintenance|construction|repair|coating|tiling)\b", re.I),
    # Slot 5-6: weak (location-only / multi-word match) — only checked AFTER personal keywords
    re.compile(r"\b(initial visit|repair|replace|battery|pump|capacitor)\b.*\b(client|comunidad|urbanisation|las margaritas|playa blanca|puerto del carmen|costa teguise|tias|yaiza|los mojones|las brenas|pdc)\b", re.I),
    re.compile(r"\b(playa blanca|puerto del carmen|costa teguise|tias|yaiza|las margaritas|las coloradas|las brenas|pdc)\b", re.I),
    # Slot 7: strong — Pete-to-fix patterns AND repair-pipe/pump/drain (Pete's CD field-work)
    re.compile(r"\bpete to (visit|repair|replace|fix|fit|install)\b", re.I),
    # Slot 8: strong — repair/replace/fit/install + pool/leak hardware
    re.compile(r"\b(repair|replace|fit|fix|install|service|complete|start)\b.*\b(pipe|pipework|pump|drain|tank|filter|skimmer|valve|capacitor|battery)\b", re.I),
]
CD_ORG_DOMAIN = "@canary-detect.com"

# Sygma signals (Britain training world)
SYGMA_KEYWORDS = [
    re.compile(r"\bsygma\b", re.I),
    re.compile(r"\bclancy\b", re.I),
    re.compile(r"\b(eusr|eus|proqual|citb|nrswa|hsg47|n[gd]uap|guidant geo|guideline geo)\b", re.I),
    re.compile(r"\b(training (planning|delivery|day|course)|ETP|approval|audit\b)\b", re.I),
    re.compile(r"\b(anglian|yorkshire|severn trent|thames water|wessex water|northumbrian water|scottish water|cadent|northern gas|sgn|wales \& west|gtcwater|thames|jvp utilities|imperium)\b", re.I),
    re.compile(r"\bwages sygma\b", re.I),
    re.compile(r"\bIntroduction to SYGMA\b", re.I),
    re.compile(r"\b(utility week|utility damages|key risk group|fuse energy|zero strike)\b", re.I),
    re.compile(r"\b(committee meeting|board meeting)\b", re.I),
]
SYGMA_ORG_DOMAINS = [
    "@sygma-solutions.com",
    "@theclancygroup.co.uk",
    "@anglianwater.co.uk",
    "@yorkshirewater.com",
    "@severntrent.co.uk",
    "@thameswater.co.uk",
    "@gtcwater.co.uk",
    "@cadentgas.com",
    "@northerngas.co.uk",
    "@sgn.co.uk",
    "@walesandwest.co.uk",
    "@wessexwater.co.uk",
    "@scottishwater.co.uk",
    "@northumbrianwater.co.uk",
    "@guidelinegeo.com",
]

# Personal patterns
# Patterns matched against TITLE ONLY (strict equality / start-of-title)
PERSONAL_TITLE_PATTERNS = [
    re.compile(r"^(sea swim|swim|swimming lesson|swimming|run|gym|workout|massage|cycling|tennis|padel|yoga|pilates|spa)$", re.I),
    re.compile(r"^(swim|run|gym|massage|spa)\s*$", re.I),
    re.compile(r"^spa\b", re.I),
]

# Patterns matched against title + location + description
PERSONAL_KEYWORDS = [
    re.compile(r"\b(sea swim|swimming lesson)\b", re.I),
    re.compile(r"\b(gym|workout|massage|yoga|pilates)\b", re.I),
    re.compile(r"\bscouts?\b", re.I),
    re.compile(r"\b(los claveles|olsens?|olsen|lavel|claveles)\b", re.I),
    re.compile(r"\b(freemason|lodge|masonic|brethren)\b", re.I),
    re.compile(r"\b(camello|atico|el atico)\b", re.I),
    re.compile(r"\bbirthday\b", re.I),
    re.compile(r"\bkids?\b.*\b(school|fiesta|holidays?|finish|out|off|home)\b", re.I),
    re.compile(r"\b(school (run|drop|pickup|fiesta|holiday|term))\b", re.I),
    re.compile(r"\b(summer|easter|christmas|half[- ]term)\s+(holidays?|break)\b", re.I),
    re.compile(r"\b(family|wedding|funeral|christening)\b", re.I),
    re.compile(r"\b(dentist|doctor|gp|optician|hospital|consultant|prescription|pharmacy)\b", re.I),
    re.compile(r"\b(seminar|sermon|service)\b", re.I),
]


def _organiser_email(event):
    return (event.get("organizer") or {}).get("email", "") or ""


def _attendee_emails(event):
    return [a.get("email", "") for a in (event.get("attendees") or [])]


def classify(event):
    """Return one of 'sygma', 'cd', 'personal', 'travel', or None.

    Priority order:
      1. Travel (most specific patterns — flights, hotels, car hire)
      2. Strong CD signals (CD organiser domain, leakguard/canary-detect title keywords, Pete-to-fix patterns)
      3. Strong Sygma signals (Sygma-orbit external organiser domain, Sygma/Clancy/training keywords)
      4. Personal keywords (sea swim, scouts, los claveles, camello, freemason, birthdays, family, medical, …)
      5. Weak Sygma (@sygma-solutions.com in attendees AND multi-attendee — internal meetings)
      6. Weak CD (Lanzarote location-only — only triggers if nothing above matched)
    """
    title = event.get("summary", "") or ""
    location = event.get("location", "") or ""
    description = event.get("description", "") or ""
    org = _organiser_email(event).lower()
    attendees = _attendee_emails(event)
    attendees_lower = " ".join(attendees).lower()
    haystack = f"{title} | {location} | {description}".strip()
    title_loc = f"{title} | {location}".strip()

    # 1. Travel — match title+location only (not description, which often mentions hotel/airport
    # for events that aren't themselves travel — e.g. a conference at a hotel)
    for pat in TRAVEL_PATTERNS:
        if pat.search(title_loc):
            return "travel"

    # 2. Strong CD: explicit CD organiser domain, OR strong-leak/Canary keywords (slots 0-4, 7, 8)
    if CD_ORG_DOMAIN in (org + " " + attendees_lower):
        return "cd"
    strong_cd_indices = [0, 1, 2, 3, 4, 7, 8]  # canary-detect/leakguard/leak/community survey/ecofinish/pool, Pete-to-fix, repair-pipe
    for i in strong_cd_indices:
        if CD_KEYWORDS[i].search(haystack):
            return "cd"

    # 3. Strong Sygma: external Sygma-orbit organiser, OR Sygma/Clancy/training keywords
    for d in SYGMA_ORG_DOMAINS:
        if d == "@sygma-solutions.com":
            continue  # internal — handled later as weak signal
        if d in (org + " " + attendees_lower):
            return "sygma"
    for pat in SYGMA_KEYWORDS:
        if pat.search(haystack):
            return "sygma"

    # 4. Personal — first the title-strict patterns (Swim, Run, Spa, etc.), then the broader keywords
    title_stripped = title.strip()
    for pat in PERSONAL_TITLE_PATTERNS:
        if pat.search(title_stripped):
            return "personal"
    for pat in PERSONAL_KEYWORDS:
        if pat.search(haystack):
            return "personal"

    # 5. Weak Sygma: internal @sygma-solutions.com in attendees AND multi-attendee
    if "@sygma-solutions.com" in (org + " " + attendees_lower):
        non_pete = [a for a in attendees if a.lower() and "pete.ashcroft" not in a.lower()]
        if non_pete:
            return "sygma"

    # 6. Weak CD: Lanzarote location-only patterns (last resort, slots 5-6)
    for i in (5, 6):
        if CD_KEYWORDS[i].search(haystack):
            return "cd"

    # 7. No match
    return None


def fetch_window(api, days_back, days_ahead, today=None):
    today = today or datetime.date.today()
    start = (today - datetime.timedelta(days=days_back)).isoformat() + "T00:00:00Z"
    end = (today + datetime.timedelta(days=days_ahead)).isoformat() + "T23:59:59Z"
    return api.list_events("primary", time_min=start, time_max=end, max_results=2500)


def fetch_recent(api, hours=36):
    """For the daily cron: walk a 2-day-back / 365-day-ahead window.

    The cron fires once a day; uncoloured events anywhere in that window
    will get classified. Already-coloured events are skipped (idempotent).
    """
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=2)).isoformat() + "T00:00:00Z"
    end = (today + datetime.timedelta(days=365)).isoformat() + "T23:59:59Z"
    return api.list_events("primary", time_min=start, time_max=end, max_results=2500)


def run(mode, days_back, days_ahead, recent_hours=None, force=False):
    """If force=True, also re-classify already-coloured events (use for scheme migration)."""
    api = c.CalendarAPI()
    if mode == "apply-recent":
        events = fetch_recent(api, hours=recent_hours or 36)
    else:
        events = fetch_window(api, days_back, days_ahead)

    counts = {"sygma": 0, "cd": 0, "personal": 0, "travel": 0, "unmatched": 0,
              "skipped_already_coloured": 0, "skipped_already_correct": 0}
    decisions = []
    for e in events:
        existing_colour = e.get("colorId")
        if existing_colour and not force:
            counts["skipped_already_coloured"] += 1
            continue
        cat = classify(e)
        if cat is None:
            counts["unmatched"] += 1
            decisions.append((e, None))
            continue
        # In force mode, only patch if the existing colour differs from target
        if existing_colour and existing_colour == COLOURS[cat]:
            counts["skipped_already_correct"] += 1
            continue
        counts[cat] += 1
        decisions.append((e, cat))

    print("=" * 60)
    print(f"Calendar colour run: mode={mode} window=-{days_back}d → +{days_ahead}d")
    print("=" * 60)
    for k in ("sygma", "cd", "personal", "travel", "unmatched", "skipped_already_coloured", "skipped_already_correct"):
        print(f"  {k:30s}: {counts[k]}")
    print()

    if mode == "dry-run":
        print("Sample of CLASSIFIED events (first 50):")
        for e, cat in decisions:
            if cat is None: continue
            t = e.get("summary", "(no title)")[:55]
            d = (e.get("start", {}).get("dateTime") or e.get("start", {}).get("date") or "")[:10]
            print(f"  [{cat:8}] {d} | {t}")
        print()
        print("Sample of UNMATCHED events (first 30 — these will keep current colour):")
        n = 0
        for e, cat in decisions:
            if cat is not None: continue
            if n >= 30: break
            t = e.get("summary", "(no title)")[:55]
            d = (e.get("start", {}).get("dateTime") or e.get("start", {}).get("date") or "")[:10]
            org = _organiser_email(e)[:30]
            print(f"  [-] {d} | {t} | org:{org}")
            n += 1
        return counts

    # Apply mode — actually mutate events
    import time
    applied = 0
    skipped_birthday = 0
    errors = []
    for e, cat in decisions:
        if cat is None: continue
        # Google blocks colour mutations on auto-imported birthday events
        if e.get("eventType") == "birthday":
            skipped_birthday += 1
            continue
        target_colour = COLOURS[cat]
        for attempt in (1, 2, 3):
            try:
                api.update_event(e["id"], "primary", colorId=target_colour)
                applied += 1
                time.sleep(0.15)  # 150ms gap = ~6.5 req/s, well under Calendar's 500-req/100s/user limit
                break
            except Exception as ex:
                msg = str(ex)
                if "rateLimitExceeded" in msg or "HTTP 403" in msg:
                    backoff = 2 ** attempt  # 2, 4, 8 seconds
                    time.sleep(backoff)
                    continue
                if "eventTypeRestriction" in msg or "birthday" in msg:
                    skipped_birthday += 1
                    break
                # Some auto-imported fromGmail events return bare HTTP 400 with no reason
                # but are also blocked from colour mutation. Skip if so.
                if "HTTP 400" in msg and e.get("eventType") in ("fromGmail", "birthday"):
                    skipped_birthday += 1
                    break
                # Other error — record and continue
                errors.append((e.get("summary", "")[:40], msg[:200]))
                break
        else:
            # All 3 retries exhausted on rate-limit
            errors.append((e.get("summary", "")[:40], "rate-limit exhausted after 3 retries"))
    print(f"Applied colours to {applied} events.")
    if skipped_birthday:
        print(f"Skipped {skipped_birthday} auto-imported birthday events (Google blocks colour mutation on these).")
    if errors:
        print(f"Errors ({len(errors)}):")
        for t, err in errors[:15]:
            print(f"  - {t}: {err}")
    return counts


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ("dry-run", "apply"):
        sp = sub.add_parser(cmd)
        sp.add_argument("--days-back", type=int, default=30)
        sp.add_argument("--days-ahead", type=int, default=180)
        sp.add_argument("--force", action="store_true",
                        help="Re-classify already-coloured events (for scheme migrations)")
    sp_recent = sub.add_parser("apply-recent")
    sp_recent.add_argument("--hours", type=int, default=36)
    sp_classify = sub.add_parser("classify")
    sp_classify.add_argument("title")
    sp_classify.add_argument("--location", default="")
    sp_classify.add_argument("--organiser", default="")
    args = p.parse_args()

    if args.cmd == "classify":
        e = {"summary": args.title, "location": args.location, "organizer": {"email": args.organiser}}
        print(classify(e))
        return
    if args.cmd == "apply-recent":
        run("apply-recent", 2, 365, recent_hours=args.hours)
        return
    run(args.cmd, args.days_back, args.days_ahead, force=getattr(args, "force", False))


if __name__ == "__main__":
    main()
