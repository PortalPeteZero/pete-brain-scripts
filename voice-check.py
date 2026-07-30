#!/usr/bin/env python3
"""voice-check.py — catch the four things that make a Pete-authored document read wrong.

Run this on ANY document Pete will put in front of someone else: a proposal page, a document, a
deck's text, an outbound write-up. It is a linter for point of view, not for grammar.

The four checks, all learned the hard way on the Passion Fit tiered-membership proposal
(30 Jul 2026), where Pete flagged every one of them separately:

  1. THIRD PERSON      "Pete's version", "Pete is not sure", "not Pete's to choose".
                       It is his document. It should say I / my / me.
  2. MEETING NARRATION "this came up at the end of the conversation", "Loren took to it
                       immediately", "Loren's response was". That is minutes of a meeting the
                       reader was not at, and it reads as borrowing someone's endorsement.
                       Quoting somebody to make a substantive point is fine; narrating their
                       reaction is not, so this only flags reaction-narration patterns.
  3. TELLING, NOT      "should" where "could" belongs. Pete's words: "i need some humility here,
     OFFERING          i am not the expert telling them what to do, this is just my idea."
  4. DASHES            Em dashes and double hyphens, which are banned in anything outbound.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/voice-check.py <file.html|file.md> [--author Pete] [--quiet]

Exit codes: 0 = clean, 1 = findings, 2 = bad usage.
Findings are advisory. Judgement still applies: a byline, a status line, or a deliberate quote
will trip these, which is why every hit is printed with its context rather than just counted.
"""
import argparse
import re
import sys
from pathlib import Path

NARRATION = [
    (r"came up (?:at the end|in the (?:call|meeting|conversation))", "narrates when it was said"),
    (r"took to it (?:immediately|straight away)", "narrates someone's reaction"),
    (r"\b\w+'s response(?: was)?\b", "narrates someone's reaction"),
    (r"\b\w+ did not (?:rush to )?disagree", "narrates someone's reaction"),
    (r"\b\w+ agreed (?:immediately|straight away)", "narrates someone's reaction"),
    (r"in the conversation\b", "refers to the source meeting"),
    (r"raised this with \w+", "refers to the source meeting"),
    (r"\bshould expect (?:him|her|them) to\b", "briefs the reader about a third party"),
    (r"when \w+ takes (?:it|this) to \w+", "treats the reader as someone to be handled"),
]

BANNED_DASH = [("—", "em dash"), ("–", "en dash"), ("--", "double hyphen")]


def visible_text(raw: str, is_html: bool) -> str:
    if not is_html:
        return raw
    body = raw[raw.find("<body"):] if "<body" in raw else raw
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S)
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    return re.sub(r"<[^>]+>", " ", body)


def context(text: str, start: int, end: int, pad: int = 70) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - pad):end + pad]).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--author", default="Pete",
                    help="the name that should never appear in third person (default: Pete)")
    ap.add_argument("--quiet", action="store_true", help="counts only, no context lines")
    a = ap.parse_args()

    p = Path(a.path)
    if not p.exists():
        print(f"voice-check: no such file {p}", file=sys.stderr)
        return 2

    raw = p.read_text(encoding="utf-8", errors="replace")
    text = visible_text(raw, p.suffix.lower() in (".html", ".htm"))
    findings = []

    for m in re.finditer(rf"\b{re.escape(a.author)}\b", text):
        findings.append(("THIRD PERSON", f"{a.author} named in the body",
                         context(text, m.start(), m.end())))

    for pat, why in NARRATION:
        for m in re.finditer(pat, text, re.I):
            findings.append(("NARRATION", why, context(text, m.start(), m.end())))

    for m in re.finditer(r"\bshould\b", text, re.I):
        findings.append(("TELLING", "'should' — would 'could' be fairer?",
                         context(text, m.start(), m.end())))

    for ch, name in BANNED_DASH:
        for m in re.finditer(re.escape(ch), text):
            findings.append(("DASH", name, context(text, m.start(), m.end(), 45)))

    if not findings:
        print(f"voice-check: {p.name} — clean on all four checks.")
        return 0

    order = ["THIRD PERSON", "NARRATION", "TELLING", "DASH"]
    print(f"voice-check: {p.name} — {len(findings)} thing(s) to look at\n")
    for kind in order:
        rows = [f for f in findings if f[0] == kind]
        if not rows:
            continue
        print(f"── {kind} ({len(rows)}) ──")
        for _, why, ctx in rows:
            print(f"   • {why}")
            if not a.quiet:
                print(f"     …{ctx}…")
        print()
    print("None of these are automatic errors. A byline, a status line or a deliberate quote will")
    print("trip them. Read each one and decide.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
