#!/usr/bin/env python3
"""
finance-ledger-publish.py — publish a per-entity finance ledger to its Command Centre surface.

Reads a `finance-ledger.md` (the dynamic record the `finance this` verb appends to), parses its
three load-bearing sections (`## Deadlines` / `## Latest decision` / `## Recent filings`), and
publishes them to `reports.snapshots` under the ledger's `cc_report_key`. So the entity's CC
dashboard (e.g. Ashcroft Finance) reflects a new deadline / decision / filing with **no code
deploy** (household-finance-system plan, Phase 4). Re-publishing overwrites the day's snapshot.

Usage:  python3 finance-ledger-publish.py [path/to/finance-ledger.md]
        (default: Personal/family/Finance/finance-ledger.md — the Ashcroft Finance ledger)
"""
import os, re, sys, datetime
from importlib.machinery import SourceFileLoader
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
DEFAULT = os.path.join(VAULT, "Personal/family/Finance/finance-ledger.md")
cc_publish = SourceFileLoader(
    "cc_publish", os.path.join(VAULT, "Library/processes/scripts/cc_publish.py")).load_module()

# Header text -> payload key. Headers are load-bearing; keep them exact in the ledger.
SECTIONS = {"Deadlines": "deadlines", "Latest decision": "decision", "Recent filings": "filings"}


def clean(line):
    s = re.sub(r"^[-*]\s+", "", line.strip())                 # bullet marker
    s = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", s)        # [[target|label]] -> label
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)                 # [[target]] -> target
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)                  # **bold** -> bold
    return s.strip()


def parse(path):
    txt = open(path, encoding="utf-8").read()
    fm = {}
    m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
    if m:
        for ln in m.group(1).splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                fm[k.strip()] = v.strip()
    out = {v: [] for v in SECTIONS.values()}
    cur = None
    for ln in txt.splitlines():
        h = re.match(r"^##\s+(.+)", ln)
        if h:
            cur = SECTIONS.get(h.group(1).strip())
            continue
        if cur and ln.lstrip().startswith(("-", "*")):
            c = clean(ln)
            if c:
                out[cur].append(c)
    return fm, out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    if not os.path.exists(path):
        print(f"finance-ledger-publish: no ledger at {path}", file=sys.stderr)
        return 2
    fm, sections = parse(path)
    key = fm.get("cc_report_key")
    if not key:
        print(f"finance-ledger-publish: {path} has no `cc_report_key` in frontmatter — nothing to publish", file=sys.stderr)
        return 2
    updated = fm.get("updated") or datetime.date.today().isoformat()
    payload = {"title": "Latest from the ledger", "updated": updated, **sections}
    period = datetime.date.today().isoformat()
    ok = cc_publish.publish(key, period, payload)
    print(f"finance-ledger-publish: {'published' if ok else 'FAILED'} {key} → reports.snapshots "
          f"({len(sections['deadlines'])} deadlines · {len(sections['decision'])} decision · {len(sections['filings'])} filings)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())