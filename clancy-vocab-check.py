#!/usr/bin/env python3
"""clancy-vocab-check.py — refuse to publish a Clancy page that names Depotnet wrongly.

WHY THIS EXISTS
---------------
Depotnet's incident tabs are, verified on screen 1 Aug 2026 (incident 119372):

    Incident Details | Questions | Report | Outstanding Actions | Closed Actions |
    Witnesses | Injuries

There is no "investigation form" and no "investigation section". Both were invented, and the
invented name is what did the damage: once the Report tab is called "the investigation form", an
empty one reads as "no investigation was done" — a claim about whether people did their jobs,
drawn from a system that only knows whether a field is filled. Someone may have investigated
thoroughly and never written it up.

Pete, 1 Aug 2026: "your framing of having no investigation done is wrong then, there isnt an
investigation form". Earlier the same week, on the same class of error: "a tick box not ticked
might nnot mean it wasnt investigated ... you have to qualify and accurate".

So this is a vocabulary gate, deliberately narrow. It does not police tone. It catches two things
only: names for Depotnet objects that Depotnet does not use, and absence-claims the data cannot
support. A banned phrase inside curly quotes is ALLOWED, because that is how the pages name a
wrong phrasing in order to reject it.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-vocab-check.py <file.html>
  ... | VAULT=/tmp/pbs python3 /tmp/pbs/clancy-vocab-check.py -     # stdin

Exit 0 = clean. Exit 1 = something must be reworded (offending lines printed with context).
"""
import sys, re, html as H

# (pattern, what to say instead). Matched case-insensitively against the page's visible text.
BANNED = [
    (r"investigation form",
     'Depotnet has no "investigation form". The tab is called Report.'),
    (r"investigation section",
     'Depotnet has no "investigation section". The tab is called Report.'),
    (r"no investigation (was |had |ever )?(been )?(done|carried out|undertaken)",
     'The system cannot tell us this. Say the Report is empty.'),
    (r"(has|have|carry|carries|carried) no investigation",
     'The system cannot tell us this. Say the Report is empty / has not been filled in.'),
    (r"(was|were) not investigated",
     'The system cannot tell us this. Say the Report is empty.'),
    (r"nobody investigated",
     'The system cannot tell us this. Say the Report is empty.'),
    (r"failed to investigate",
     'The system cannot tell us this. Say the Report is empty.'),
]

# Two ways a banned phrase is legitimately on the page, both of which must pass:
#   QUOTED  — named in order to be rejected: 'It does not say a damage "has no investigation"'.
#   NEGATED — the caveat itself: "An empty Report does not mean nobody investigated."
# Anything else is the phrase being asserted, which is the thing this gate exists to stop.
QUOTED = re.compile(r"[“”\"']\s*[^“”\"']{0,80}$")
NEGATED = re.compile(
    r"\b(?:do(?:es)?\s+not\s+mean|is\s+not\s+(?:evidence|proof)|never\s+(?:says?|claims?)|"
    r"not\s+the\s+same\s+as|cannot\s+tell\s+us|no\s+way\s+to\s+say)\b[^.]{0,60}$", re.I)


def visible_text(raw):
    """Strip tags and unescape, but keep newlines so an offender can be located."""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return H.unescape(t)


def check(raw, label="page"):
    text = visible_text(raw)
    flat = re.sub(r"\s+", " ", text)
    bad = []
    for pat, fix in BANNED:
        for m in re.finditer(pat, flat, re.I):
            before = flat[max(0, m.start() - 90):m.start()]
            if QUOTED.search(before) or NEGATED.search(before):
                continue                      # named or negated, not asserted — allowed
            bad.append((m.group(0), fix,
                        flat[max(0, m.start() - 110):m.end() + 110].strip()))
    if not bad:
        print(f"vocab: {label} clean — {len(flat):,} chars checked, "
              f"{len(BANNED)} rules")
        return 0
    print(f"\nVOCAB CHECK FAILED — {len(bad)} phrase(s) in {label}\n")
    for phrase, fix, ctx in bad:
        print(f'  "{phrase}"')
        print(f"     {fix}")
        print(f"     ...{ctx}...\n")
    print("Reword and re-run. Depotnet's own tabs: Incident Details | Questions | Report |")
    print("Outstanding Actions | Closed Actions | Witnesses | Injuries")
    return 1


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    raw = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
    return check(raw, "stdin" if src == "-" else src)


if __name__ == "__main__":
    sys.exit(main())
