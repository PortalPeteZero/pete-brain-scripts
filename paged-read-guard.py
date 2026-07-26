#!/usr/bin/env python3
"""paged-read-guard.py — refuse a paginated read that has no stable sort order.

WHY THIS EXISTS
  Paging with `limit`+`offset` (or a Range header) and NO `ORDER BY` silently loses rows. Postgres
  promises no stable order without one, so consecutive pages are free to re-deliver some rows and
  skip others. Nothing errors; the read just comes back short.

  Measured on CC `drive_files`, 26 Jul 2026: the Drive watcher's folder-map read pulled all 18,004
  rows across 19 pages but assembled only 10,526 UNIQUE — ~7,500 folders missing, a different
  subset each run. It stored truncated file paths as fact. That drift was repaired BY HAND three
  times (403 rows, then 158 in one afternoon, then 131) because each earlier fix hardened the code
  CONSUMING the short read instead of the read itself.

  Instability scales with page count, so a two-page read almost always looks correct in review.
  That is exactly why this needs a mechanical gate rather than care and attention.
  See [[paged-reads-need-a-stable-order]].

WHAT IT FLAGS
  A line constructing a paged read (`offset=` as a query param or kwarg, or a `"Range"` header)
  with no `order=` / `orderby=` / `order_by=` anywhere in its statement window.

DECLARING A LEGITIMATE CASE
  Put `# paged-read-guard: ok <reason>` on the line (or the one above). Use it only where the read
  genuinely cannot go short — a single non-looping call, or a deliberately-sampled read.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/paged-read-guard.py            # report; exit 1 if any found
  VAULT=/tmp/pbs python3 /tmp/pbs/paged-read-guard.py --json
"""
import ast, json, os, re, sys
from pathlib import Path

VAULT = os.environ.get("VAULT", "/tmp/pbs")
ROOT = Path(VAULT)

# A paged read under construction: `offset=` as a param/kwarg (NOT the assignment `offset = 0`),
# or a PostgREST Range header.
PAGED = re.compile(r"offset=|[\"']Range[\"']\s*:")
ORDERED = re.compile(r"order=|orderby=|order_by=|\.order\(")
# `for offset in range(7)` and friends are day offsets, not pagination.
DAY_OFFSET = re.compile(r"for\s+offset\s+in\s+range")
WAIVER = re.compile(r"#\s*paged-read-guard:\s*ok")
WINDOW = 3          # lines either side, so a call split across lines still sees its own order=


def _prose_lines(src):
    """Line numbers occupied by a docstring or bare string expression. Prose that DESCRIBES this bug
    (including this file's own header) must never trip the guard, or it cries wolf and gets ignored.
    Uses the real parser, not a quote-counting heuristic. An unparseable file yields nothing, so it
    is judged on its raw lines rather than skipped — better a false positive than a blind spot."""
    out = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out


def scan(path):
    try:
        src = path.read_text(errors="replace")
    except Exception:
        return []
    lines = src.splitlines()
    prose = _prose_lines(src)
    out = []
    for i, line in enumerate(lines):
        if (i + 1) in prose:
            continue
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("print("):
            continue
        if not PAGED.search(line) or DAY_OFFSET.search(line):
            continue
        lo, hi = max(0, i - WINDOW), min(len(lines), i + WINDOW + 1)
        window = "\n".join(lines[lo:hi])
        if ORDERED.search(window) or WAIVER.search(window):
            continue
        out.append({"file": str(path.relative_to(ROOT)), "line": i + 1, "code": line.strip()[:150]})
    return out


def main():
    as_json = "--json" in sys.argv
    hits = []
    for p in sorted(ROOT.rglob("*.py")):
        if any(part in (".git", "__pycache__", "node_modules") for part in p.parts):
            continue
        hits += scan(p)

    if as_json:
        print(json.dumps({"gaps": len(hits), "gap_types": (["unordered-paged-read"] if hits else []),
                          "findings": [{"rule": "unordered-paged-read", "subject": f"{h['file']}:{h['line']}",
                                        "detail": h["code"], "severity": "high"} for h in hits]}, indent=1))
    else:
        if not hits:
            print("paged-read-guard: clean — every paged read specifies a sort order.")
        else:
            print(f"=== paged-read-guard — {len(hits)} unordered paged read(s) ===")
            for h in hits:
                print(f"  {h['file']}:{h['line']}\n      {h['code']}")
            print("\n  A paged read with no ORDER BY silently loses rows. Add an order (prefer keyset:\n"
                  "  order=<col>.asc & <col>=gt.<last>), or declare it with `# paged-read-guard: ok <reason>`.")
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()
