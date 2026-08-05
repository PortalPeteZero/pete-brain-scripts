#!/usr/bin/env python3
"""pf-quote-gate.py — mechanical quotation-integrity gate for seminar summaries.

Step 4a of the pf-seminars skill says every direct quote must exist in the
transcript as one continuous passage. That was a reasoning check, and reasoning
checks converge on summaries that merely READ well: nine summaries that passed
repeated adversarial audits still had 71% of their quoted spans fail this gate.

This is the runnable version. For each <key>-summary.md it finds the matching
<key>-transcript.txt, extracts every quoted span, and proves each appears
verbatim in the tape. A span may be joined ONLY with an ellipsis, and each side
of the join is checked separately.

  pf-quote-gate.py <dir> [<dir>...]        # check every summary in those dirs
  pf-quote-gate.py <summary.md>            # check one summary
  pf-quote-gate.py <dir> --quiet           # counts only

Exit 0 only when the counter prints 0. Anything else is not done.

NOTE ON RESIDUE: these are ASR transcripts. Removing a stutter ("as I we want"
-> "as I want") is accepted practice and WILL fail this gate. So a non-zero
count is a list to adjudicate by hand, not automatically a list of defects.
Zero is the only result that needs no adjudication.

WHAT THIS GATE DOES NOT PROVE — do not overclaim a zero:
 1. It proves the WORDS are verbatim, not that a span sits inside ONE speaker's
    turn. Speaker labels are stripped from the haystack, so a quote welded across
    a turn boundary still passes. Turn continuity has to be checked separately.
 2. Spans under MIN_WORDS are skipped, so short quoted fragments are invisible.
 3. Attribution, sequence, causation, invented specifics and stripped hedges
    OUTSIDE quote marks are all out of scope. This is a quotation check only,
    and it is the floor of Step 4a, not the whole of it.
"""
import glob, os, re, sys, unicodedata

MIN_WORDS = 6
CURLY_RE = re.compile(r'“([^“”]+)”')
# Three transcript formats are in play: bracketed [MM:SS] (Plaud exports), BARE
# MM:SS on its own line (some Google Recorder pulls), and none at all. A bare
# stamp left in the haystack silently breaks continuity mid-quote and reads as a
# weld — so strip it, but ONLY line-anchored, or a race split ("2:45") goes too.
TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")
BARE_TS_RE = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?\s*$", re.M)
# Labels appear as "Speaker 3:", "None:" (export never identified the speaker) or
# "?:". Strip all of them, or the literal token survives mid-sentence and reads as
# a weld — one transcript carried "None:" 198 times, once per chunk boundary.
SPEAKER_RE = re.compile(r"^\s*(?:Speaker \d+|None|\?):", re.M)
# Some Plaud pulls carry the browser console injected INTO the speech, splitting
# words mid-sentence: "talking out of [11] [13:56:18] [LOG] (https://…) turn here"
LOGLINE_RE = re.compile(r"\[\d+\]\s*\[\d{2}:\d{2}:\d{2}\]\s*\[[A-Z]+\]\s*(?:\([^)]*\))?")
# Speech has no apostrophes; the transcriber picks a form and the summariser picks
# another. Expand on both sides so "I'm" and "I am" are the same utterance.
CONTRACTIONS = ((" i m ", " i am "), (" don t ", " do not "), (" didn t ", " did not "),
                (" won t ", " will not "), (" can t ", " cannot "), (" isn t ", " is not "),
                (" wasn t ", " was not "), (" haven t ", " have not "), (" hasn t ", " has not "),
                (" wouldn t ", " would not "), (" couldn t ", " could not "),
                (" shouldn t ", " should not "), (" doesn t ", " does not "),
                (" aren t ", " are not "), (" weren t ", " were not "), (" it s ", " it is "),
                (" that s ", " that is "), (" there s ", " there is "), (" you re ", " you are "),
                (" we re ", " we are "), (" they re ", " they are "), (" i ve ", " i have "),
                (" you ve ", " you have "), (" we ve ", " we have "), (" i ll ", " i will "),
                (" you ll ", " you will "), (" i d ", " i would "))
BLOCK_START_RE = re.compile(r"(#{1,6} |[-*+] |\d+\. |\|)")

# summariser normalisations that are accepted practice, applied to both sides
SPELLING = (("behaviour", "behavior"), ("organisation", "organization"),
            ("realise", "realize"), ("recognise", "recognize"),
            ("prioritise", "prioritize"), ("individualise", "individualize"),
            ("apologise", "apologize"), ("summarise", "summarize"))


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("—", " "), ("–", " ")):
        s = s.replace(a, b)
    s = s.lower()
    for a, b in SPELLING:
        s = s.replace(a, b)
    # British -our -> -or (humour, colour, favour, honour, labour, neighbour…):
    # the summaries are en-GB, the ASR output is en-US. Speech has no spelling.
    s = re.sub(r"\b(hum|col|fav|hon|lab|neighb|endeav|rum|val|arm|vig)our", r"\1or", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Collapse ASR stutters ("and and", "if if", "i i") on BOTH sides. Dropping a
    # stutter from a quotation is accepted practice, so leaving them in makes the
    # gate flag correct work and trains the reader to ignore it.
    # Contractions FIRST, then stutters. The other order leaves "we're we're" as
    # "we are we re" — the pair is no longer adjacent-identical, so it survives the
    # collapse and reads as a defect.
    s = f" {s} "
    for a, b in CONTRACTIONS:
        s = s.replace(a, b)
    s = s.strip()
    # Single-token stutters only ("and and", "i i"). A two-token rule was tried and
    # reverted: it collapses genuine repetition ("round round round") asymmetrically
    # and cost more than it fixed. On a GATE, over-normalising hides defects, and a
    # false negative is far more expensive than a span adjudicated by hand.
    prev = None
    while prev != s:
        prev, s = s, re.sub(r"\b(\w+) \1\b", r"\1", s)
    return s


def transcript_for(summary_path):
    d, base = os.path.dirname(summary_path), os.path.basename(summary_path)
    key = re.sub(r"-summary(-updated)?\.md$", "", base)
    for cand in (f"{d}/{key}-transcript.txt", f"{d}/{key}-COMPLETE-transcript.txt"):
        if os.path.exists(cand):
            return cand
    return None


def transcript_text(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    raw = LOGLINE_RE.sub(" ", raw)
    raw = BARE_TS_RE.sub(" ", raw)
    raw = TIMESTAMP_RE.sub(" ", raw)
    raw = SPEAKER_RE.sub(" ", raw)
    return norm(raw)


def blocks(path):
    """Reassemble logical blocks. Markdown hard-wraps quotations across physical
    lines; splitting on newline shreds them. A run of '>' lines is one quote, and
    a list item / heading / table row always starts its own block (joining them
    straddles emphasis markers and manufactures nonsense spans)."""
    out, cur, in_bq = [], [], False

    def flush():
        nonlocal cur, in_bq
        if cur:
            out.append((in_bq, " ".join(cur)))
        cur, in_bq = [], False

    for raw in open(path, encoding="utf-8"):
        stripped = raw.rstrip("\n").lstrip()
        if not stripped:
            flush(); continue
        bq = stripped.startswith(">")
        if bq != in_bq or (BLOCK_START_RE.match(stripped) and cur):
            flush()
        in_bq = bq
        cur.append(stripped.lstrip("> ").strip() if bq else stripped)
    flush()
    return out


def quoted_fragments(text):
    """Straight quotes must be paired POSITIONALLY. Scanning for any '"..."'
    pair matches the prose BETWEEN two quotes whenever a line carries more than
    one, which manufactures failures that look exactly like real ones."""
    frags = [m.group(1) for m in CURLY_RE.finditer(text)]
    parts = CURLY_RE.sub(" ", text).split('"')
    if len(parts) > 2:
        frags += parts[1::2]          # indices 1,3,5… are inside quotes
    return frags


def spans(path):
    """A blockquote is a quotation claim only when it carries quote marks. The
    house style uses `> *"…"*` for verbatim speech and `> **bold**` for the
    summariser's own pull-out; treating the latter as speech fails every one."""
    out = []
    for _is_bq, text in blocks(path):
        out += [f for f in quoted_fragments(text) if len(f.split()) >= MIN_WORDS]
    return out


def check(summary_path):
    t = transcript_for(summary_path)
    if not t:
        return None, []
    hay, bad, total = transcript_text(t), [], 0
    for span in spans(summary_path):
        for part in re.split(r"\s*(?:\.\.\.|…)\s*", span):
            n = norm(part)
            if len(n.split()) < MIN_WORDS:
                continue
            total += 1
            if n not in hay:
                bad.append(part.strip())
    return total, bad


def main(argv):
    quiet = "--quiet" in argv
    args = [a for a in argv if not a.startswith("--")] or ["."]
    targets = []
    for a in args:
        targets += sorted(glob.glob(f"{a}/*summary*.md")) if os.path.isdir(a) else [a]
    if not targets:
        print("no summaries found"); return 1

    grand_total = grand_bad = 0
    missing = []
    for s in targets:
        total, bad = check(s)
        if total is None:
            missing.append(os.path.basename(s)); continue
        grand_total += total; grand_bad += len(bad)
        if bad and not quiet:
            print(f"\n{os.path.basename(s)}  ({len(bad)}/{total} unverified)")
            for b in bad:
                print(f"   - {b[:150]}")
    if missing:
        print(f"\nNO TRANSCRIPT FOUND: {missing}")
    print(f"\nchecked {grand_total} quoted spans across {len(targets)-len(missing)} summaries")
    print(f"GATE: {grand_bad} unverified spans")
    return 1 if grand_bad or missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
