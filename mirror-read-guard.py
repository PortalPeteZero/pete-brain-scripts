#!/usr/bin/env python3
"""mirror-read-guard.py -- refuse a read of a MIRROR table when a source of truth exists.

Pete, 27 Jul 2026, after I told him a trainer's diary was unavailable: "Why does this keep
happening you know how to access calendars and it's not through the cc, stop everything now."

What happened: I ran `cc-sql "SELECT ... FROM calendar_events"`, got an empty result, and
reported "no trainer diary data available to me". `calendar-api.py` had every trainer's
calendar, including the one I said I couldn't see. Pete then sent a rebuke to an employee on
a premise I had wrongly told him was uncheckable.

`data_map` ALREADY said it, in these words:
    Calendar / schedule -> Google Calendar (source of truth) + CC mirror public.calendar_events
    Staff directory (CC bot mirror) -- NOT A ROSTER, do not build lists from it
and the SSOT-FIRST instruction fires on EVERY user message. Both were read past all day. So
the fix cannot be more text -- it has to refuse.

The empty result is the specific trap: a mirror that is stale or partial returns [] rather
than an error, and [] reads as "there is no data" instead of "you asked the wrong system".

Mirror list is DERIVED from data_map at runtime, so a new mirror protects itself as soon as
someone records it -- no list here to go stale.

Usage:
  VAULT=/tmp/pbs python3 mirror-read-guard.py --check "SELECT * FROM calendar_events"
  VAULT=/tmp/pbs python3 mirror-read-guard.py --list      # what is currently guarded
  # library: check_sql(sql) -> (allowed, message)
"""
import os, re, sys, json, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# Where a mirror's real source lives. Derived from data_map where possible; these are the
# fallbacks for the three the system has explicitly called mirrors, so the guard still works
# if data_map is unreachable.
FALLBACK = {
    "calendar_events": (
        "Google Calendar",
        'VAULT=/tmp/pbs python3 /tmp/pbs/calendar-api.py events <person>@sygma-solutions.com <from> <to>\n'
        '  (every staff calendar is listed by: calendar-api.py calendars)'),
    "staff_directory": (
        "the Sygma Platform hub.staff_directory (contact-card tier only in the CC)",
        'VAULT=/tmp/pbs python3 /tmp/pbs/people.py find "<name>"'),
    "google_contacts": (
        "Google Contacts",
        'VAULT=/tmp/pbs python3 /tmp/pbs/people.py find "<name or number>"'),
}


def cc_sql(sql):
    out = subprocess.run(["python3", os.path.join(VAULT, "cc-sql.py"), sql],
                         capture_output=True, text=True,
                         env={**os.environ, "VAULT": VAULT, "MIRROR_GUARD_OFF": "1"}).stdout
    try:
        return json.loads(out)
    except Exception:
        return []


def mirrors():
    """Tables data_map itself calls a mirror, plus the fallbacks."""
    found = dict(FALLBACK)
    try:
        rows = cc_sql(
            "SELECT domain, home, access FROM data_map "
            "WHERE home ILIKE '%mirror%' OR access ILIKE '%mirror%' OR notes ILIKE '%mirror%'")
        for r in rows:
            blob = f"{r.get('home') or ''} {r.get('access') or ''}"
            # The word "mirror" must sit right ALONGSIDE the table name. A loose match caught
            # public.secrets, public.contacts and public.trainers -- all SOURCES that merely
            # appear in a row that also mentions a mirror. Measured 27 Jul 2026: 3 false
            # positives to 3 true ones, i.e. a coin toss, which is worse than no guard because
            # it would block legitimate reads and be switched off within a week.
            for t in re.findall(r"mirror[^.]{0,30}?(?:public\.)?(\w+)|(?:public\.)?(\w+)[^.]{0,20}?mirror",
                                blob, re.I):
                t = (t[0] or t[1]) if isinstance(t, tuple) else t
                if not t or t in found or t.lower() in ("cc", "the", "a", "local", "bot", "public"):
                    continue
                src = (r.get("home") or "").split("+")[0].strip()
                found[t] = (src or r.get("domain", "the source system"),
                            (r.get("access") or "").split("·")[0].strip())
    except Exception:
        pass
    return found


TABLE_RE = re.compile(r"\b(?:from|join)\s+(?:public\.)?([a-zA-Z_][\w]*)", re.I)


def check_sql(sql):
    """(allowed, message). Only ever refuses a READ of a known mirror."""
    if not re.match(r"\s*(select|with)\b", sql or "", re.I):
        return True, ""                    # writes/DDL are not this guard's business
    m = mirrors()
    hit = [t for t in {t.lower() for t in TABLE_RE.findall(sql)} if t in m]
    if not hit:
        return True, ""
    t = hit[0]
    source, how = m[t]
    return False, (
        f"BLOCKED by mirror-read-guard: `{t}` is a MIRROR, not the source of truth.\n"
        f"  The source is: {source}\n"
        f"  Read it with:\n    {how}\n"
        f"  A mirror can be stale or partial and returns an EMPTY RESULT rather than an error,\n"
        f"  so [] here means 'wrong system', NOT 'no data exists'. Do not report an empty mirror\n"
        f"  as an absence of fact.\n"
        f"  If you genuinely want the mirror (bulk analysis, comparing mirror vs source),\n"
        f"  set MIRROR_GUARD_OFF=1 for that one call and say in your answer that you used a mirror."
    )


if __name__ == "__main__":
    if "--list" in sys.argv:
        for t, (src, how) in sorted(mirrors().items()):
            print(f"  {t:<22} -> {src}")
            print(f"  {'':<22}    {how}")
        sys.exit(0)
    if "--check" in sys.argv:
        ok, msg = check_sql(sys.argv[sys.argv.index("--check") + 1])
        print("ALLOW" if ok else msg)
        sys.exit(0 if ok else 2)
    print(__doc__)
