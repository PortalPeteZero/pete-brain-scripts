#!/usr/bin/env python3
"""entity-name-check.py — refuse to let a KNOWN wrong name for a person or company survive in the CC.

WHY THIS EXISTS
---------------
On 23 July 2026 a Plaud transcript rendered **Kier** (the contractor taking Clancy's IMRDS
contract on 30 October) as "Kears". Nobody caught it, so it propagated out of the transcript into
Roy Cotterill's contact record and two write-ups, where it sat as fact for two weeks until Pete
read it out loud on 6 August.

The damage is not the spelling. The SAME Clancy account already held Kier correctly on two other
contacts (contract "Kier (data-only)"), and Kier is a Sygma customer in its own right at
Customers/SY-Kier. One company, one account, two names -- so anyone searching for Kier found half
the picture and would never learn that the firm taking a 70m-turnover contract is one we already
have a data channel with.

A lesson note already existed about Plaud mangling PEOPLE's names. It did not stop this, because a
names-only sweep never looks at COMPANIES, and because a note is something you have to remember to
read. Pete on exactly this: "Don't make a memory -- FIX THE PROCESS."

WHY IT IS A LIST AND NOT CLEVER
-------------------------------
Two fuzzy versions of this were built and thrown away on 6 Aug 2026. Comparing every customer name
against every capitalised word produced **954** hits ("Lead" vs "Leak", "Steve" vs "Severn").
Filtering to distinctive names still produced **114** ("Sewerin" vs Severn -- and Sewerin is
not a competitor at all, it is the manufacturer whose leak-detection kit we USE, which is its own
lesson about inferring what a thing is from the folder it sits in).
Pete's standing rule is that a check which cries wolf is worse than no check, because people stop
reading it. Measured, failed, abandoned -- deliberately recorded here so nobody rebuilds it.

So this catches KNOWN wrong strings with certainty and does not pretend to catch unknown ones.
Every entry below is a real error that actually reached the record. When a new mis-transcription is
found, add it here in the same session and it can never come back.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/entity-name-check.py            # report, exit 0
  VAULT=/tmp/pbs python3 /tmp/pbs/entity-name-check.py --strict   # exit 1 if anything found
"""
import argparse, json, os, re, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# Text that records what was SAID, not what we assert. A verbatim transcript keeps its errors:
# editing one destroys the evidence of the mis-transcription. Never flagged, never fixed.
VERBATIM = ("/source/", "-transcript", "transcript-")

# Notes whose JOB is to name the wrong spelling in order to reject it.
EXEMPT_PATHS = (
    "library/lessons/lesson-transcript-names",
    "library/plans/rules-moved-snapshot",
)

# Machine output. A Plaud AI note is the tool's own words, the same class as a verbatim
# transcript: it records what the machine produced, and correcting it destroys the evidence that
# the machine gets these wrong. Detected by content, not path, because they live all over.
MACHINE_MARKERS = ("plaud", "meeting minutes ·", "ai note", "automatic transcription")

# How close the RIGHT spelling has to sit for a hit to be a note naming the error in order to
# reject it -- e.g. '"Sigma Solutions" = Sygma Solutions Ltd'. That is correct usage, not a fault.
NAMES_THE_ERROR_WINDOW = 120

# (wrong, right, note). Each one actually reached a record.
KNOWN_WRONG = [
    ("Kears",         "Kier",          "the contractor taking Clancy IMRDS, 30 Oct 2026. A customer in its own right: Customers/SY-Kier"),
    ("Josh Page",     "Josh Pope",     "Clancy operative, Wellmoor"),
    ("Tony Garlick",  "Tony Garlinge", "Clancy SLT, operational lead for the Sygma partnership"),
    ("Mark Kiri",     "Mark Keary",    "Clancy contact. NB Keary is NOT a misspelling of Kier"),
    ("Holly Low",     "Holly Lowe",    "Clancy contact"),
    ("Jimmy Elliott", "James Elliott", "Clancy Area Manager, IMR South"),
    ("DepoNet",       "Depotnet",      "Clancy's incident system"),
    ("Sigma",         "Sygma",         "us"),
]


def q(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    try:
        return json.loads(r.stdout)
    except Exception:
        return []


def exempt(path: str, body: str = "") -> bool:
    p = (path or "").lower()
    if any(v in p for v in VERBATIM) or any(e in p for e in EXEMPT_PATHS):
        return True
    return any(mk in (body or "").lower()[:1500] for mk in MACHINE_MARKERS)


def inside_quotes(body: str, m) -> bool:
    """A quoted string is a record of somebody else's text -- a calendar entry title, an email
    subject, a screen label -- not our assertion. clancy-vocab-check takes the same view of a
    banned phrase in quotes. Found on 6 Aug 2026 flagging '"Sigma Locator Demo"', which is what
    the calendar entry is actually called."""
    lo = max(0, m.start() - 60)
    before = body[lo:m.start()]
    after = body[m.end():m.end() + 60]
    for open_q, close_q in (('"', '"'), ("\u201c", "\u201d"), ("`", "`")):
        if open_q in before and close_q in after:
            # nothing closing the quote between the opener and the match
            if close_q not in before[before.rfind(open_q) + 1:]:
                return True
    return False


def names_the_error(body: str, m, right: str) -> bool:
    """True when the correct spelling sits right beside the wrong one, i.e. the note is
    naming the error deliberately rather than making it."""
    lo = max(0, m.start() - NAMES_THE_ERROR_WINDOW)
    hi = m.end() + NAMES_THE_ERROR_WINDOW
    return right.lower() in body[lo:hi].lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 if anything is found")
    a = ap.parse_args()

    print(f"entity-name-check — {len(KNOWN_WRONG)} known-wrong name(s)")
    hits = []

    for wrong, right, why in KNOWN_WRONG:
        pat = re.compile(r"\b" + re.escape(wrong) + r"\b", re.I)

        for r in q("SELECT customer, name, coalesce(role,'') r, coalesce(notes,'') n FROM account_people"):
            if pat.search(r["r"] + " " + r["n"]):
                hits.append((wrong, right, why, "account_people", f"{r['customer']} / {r['name']}"))

        rows = q("SELECT vault_path, body FROM vault_notes WHERE body ILIKE '%" + wrong.replace("'", "''") + "%'")
        for r in rows:
            body = r["body"] or ""
            if exempt(r["vault_path"], body):
                continue
            m = pat.search(body)
            if m and not names_the_error(body, m, right) and not inside_quotes(body, m):
                hits.append((wrong, right, why, "vault_notes", r["vault_path"]))

    if not hits:
        print("  0 known-wrong names anywhere they would be read as fact. ✓")
        return 0

    print(f"\n  {len(hits)} place(s) still carrying a name we know is wrong:\n")
    for wrong, right, why, where, subject in sorted(set(hits)):
        print(f"    \"{wrong}\" should be \"{right}\"")
        print(f"        {where}: {subject}")
        print(f"        ({why})")
    print("\n  Verbatim transcripts are exempt and must not be edited.")
    return 1 if a.strict else 0


if __name__ == "__main__":
    sys.exit(main())
