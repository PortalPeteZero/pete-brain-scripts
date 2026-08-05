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
"""
import glob, os, re, sys, unicodedata

MIN_WORDS = 6
CURLY_RE = re.compile(r'“([^“”]+)”')
TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")
SPEAKER_RE = re.compile(r"^\s*Speaker \d+:", re.M)
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
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def transcript_for(summary_path):
    d, base = os.path.dirname(summary_path), os.path.basename(summary_path)
    key = re.sub(r"-summary(-updated)?\.md$", "", base)
    for cand in (f"{d}/{key}-transcript.txt", f"{d}/{key}-COMPLETE-transcript.txt"):
        if os.path.exists(cand):
            return cand
    return None


def transcript_text(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
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
    out = []
    for is_bq, text in blocks(path):
        if is_bq:
            body = re.sub(r"[*_`]", "", text).strip().strip('"“”')
            body = re.split(r"\s+—\s+[A-Z][a-z]+\s*$", body)[0]   # drop "— Pete" tail
            if len(body.split()) >= MIN_WORDS:
                out.append(body)
            continue
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
