#!/usr/bin/env python3
"""transcript-clean.py -- fix what YouTube's auto-captions get wrong about Sygma's kit.

YouTube ASR is fine on ordinary English and consistently wrong on this trade's vocabulary.
Every substitution below was found in a REAL Sygma transcript and confirmed, not guessed --
"Genny" comes back as "Jenny" or even "journey"; "voltstick" as "vault stick"; "sonde depth"
as "snowed depth" (confirmed by Pete, 4 Aug 2026).

This exists because auto-captions are how a video becomes answerable by Genny, and an
uncorrected transcript teaches the wrong words to anyone -- and anything -- reading it.

Usage:
  transcript-clean.py in.srt out.srt          # keeps SRT timings
  transcript-clean.py in.srt out.txt --prose  # one clean block of prose
  transcript-clean.py in.srt --report         # show what WOULD change, write nothing

Add a fix ONLY when you have seen it in a real transcript. A speculative substitution that
fires on correct text is worse than the error it was meant to catch.
"""
import os, re, sys

# (pattern, replacement, why) -- ordered; longer/more specific first
FIXES = [
    # ── the transmitter. The single most common error, and the most damaging. ──
    (r"\buse our journey to trace\b", "use our Genny to trace",
     "ASR heard 'Genny' as 'journey' (webinar, 4 Aug 2026)"),
    (r"\bJenny's\b",   "Genny's",   "transmitter name"),
    (r"\bjennies\b",   "Gennies",   "transmitter name"),
    (r"\bJennies\b",   "Gennies",   "transmitter name"),
    (r"\bJenny 4\b",   "Genny4",    "Radiodetection product name"),
    (r"\bjenny\b",     "Genny",     "transmitter name"),
    (r"\bJenny\b",     "Genny",     "transmitter name"),

    # ── other kit ──
    (r"\bvault stick\b", "voltstick", "the instrument is a voltstick"),
    (r"\bvolt stick\b",  "voltstick", "one word"),
    (r"\bCAT 4\b",       "CAT4",      "Radiodetection product name"),
    (r"\bsnowed depth\b", "sonde depth",
     "'snowed' is not a thing; sonde mode is -- CONFIRMED by Pete, 4 Aug 2026"),

    # ── street lighting wiring, checked against Sygma's own slide wording ──
    (r"\bTED off system\b", "tee'd off system", "slide reads 'Tee'd off'"),
    (r"\bTED system\b",     "tee'd off system", "slide reads 'Tee'd off'"),
    (r"\bT system\b",       "tee'd off system", "slide reads 'Tee'd off'"),
    (r"\bloopin loop out\b", "loop-in loop-out", "slide reads 'Loop-in'"),
    (r"\bloop in loop out\b", "loop-in loop-out", "slide reads 'Loop-in'"),

    # ── run-together words and spellings ──
    (r"\bTjunction\b",   "T junction",  "ASR ran the words together"),
    (r"\bboiler flu\b",  "boiler flue", "spelling"),
    (r"\bblowoff\b",     "blow-off",    "spelling"),

    # ── the locator. Lower-case 'cat' is the animal; upper-case is the instrument. ──
    (r"\bthe cat\b",  "the CAT",  "CAT is an instrument, not an animal"),
    (r"\byour cat\b", "your CAT", "CAT is an instrument, not an animal"),
    (r"\bthe cats\b", "the CATs", "CAT is an instrument, not an animal"),
]

# Body copy is ALWAYS "Genny and CAT" -- the name states the method: connect the Genny first.
# Flagged, never auto-swapped: reversing a speaker's actual words is not a transcription fix.
WRONG_ORDER = re.compile(r"C\.?A\.?T\.?\s*(?:&|and)\s*Genny", re.I)


def clean(text):
    log = []
    for pat, rep, why in FIXES:
        n = len(re.findall(pat, text))
        if n:
            text = re.sub(pat, rep, text)
            log.append((pat, rep, why, n))
    return text, log


def srt_blocks(raw):
    for b in raw.split("\n\n"):
        L = b.strip().splitlines()
        if len(L) >= 3:
            yield L[0], L[1], " ".join(L[2:]).strip()


def main():
    args = [a for a in sys.argv[1:]]
    prose = "--prose" in args
    report = "--report" in args
    args = [a for a in args if not a.startswith("--")]
    if not args:
        print(__doc__); sys.exit(2)
    src = args[0]
    if not os.path.exists(src):
        print(f"No such file: {src}", file=sys.stderr); sys.exit(2)
    raw = open(src, encoding="utf-8", errors="replace").read()

    blocks = list(srt_blocks(raw))
    if not blocks:                                   # plain text in, plain text out
        body, log = clean(raw)
        out_text = body
    elif prose:
        body, log = clean(re.sub(r"\s+", " ", " ".join(t for _, _, t in blocks)))
        out_text = body
    else:
        log, parts = [], []
        for n, ts, t in blocks:
            t2, l = clean(t)
            log += l
            parts.append(f"{n}\n{ts}\n{t2}")
        out_text = "\n\n".join(parts) + "\n"

    total = {}
    for pat, rep, why, c in log:
        total[(pat, rep, why)] = total.get((pat, rep, why), 0) + c

    print(f"transcript-clean — {src}")
    if total:
        for (pat, rep, why), c in sorted(total.items(), key=lambda x: -x[1]):
            print(f"  {c:>3}x  {pat:24s} -> {rep:18s}  ({why})")
    else:
        print("  nothing to correct.")

    wrong = WRONG_ORDER.findall(out_text)
    if wrong:
        print(f"\n  ! {len(wrong)} x wrong naming order in the body ({wrong[0]!r}). "
              f"Body copy is always 'Genny and CAT'. NOT auto-changed — the speaker said it, "
              f"so fix it in the source or note it, do not silently rewrite them.")

    if report:
        print("\n  --report: nothing written."); sys.exit(0)
    if len(args) < 2:
        print("\nUsage: transcript-clean.py in.srt out.srt [--prose]", file=sys.stderr); sys.exit(2)
    open(args[1], "w").write(out_text)
    print(f"\n  wrote {args[1]} ({len(out_text.split()):,} words)")


if __name__ == "__main__":
    main()
