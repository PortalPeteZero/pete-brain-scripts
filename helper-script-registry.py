#!/usr/bin/env python3
"""
helper-script-registry.py -- walk Library/processes/scripts/ for *-api.py / *-api.sh
helpers and regenerate the auto-generated table in [[external-service-routing]].

Parses each helper's top docstring (or top comment for shell scripts), pulls the
first non-blank descriptive line as the helper's scope, and writes the table
between AUTOGEN markers in `Library/processes/external-service-routing.md`.

This is the future-proof mechanism: adding a new `xyz-api.py` to scripts/ + a
descriptive docstring is enough. Next registry run picks it up automatically.
No skill edits, no CLAUDE.md edits, no memory edits required.

Usage:
  python3 helper-script-registry.py            # regenerate the table in-place
  python3 helper-script-registry.py --check    # report drift, exit 1 if stale
  python3 helper-script-registry.py --print    # print the would-be table to stdout
"""
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE
ROUTING_DOC = HERE.parent / "external-service-routing.md"

BEGIN_MARK = "<!-- BEGIN HELPER-SCRIPT-REGISTRY AUTOGEN -->"
END_MARK = "<!-- END HELPER-SCRIPT-REGISTRY AUTOGEN -->"


def extract_scope(path: Path) -> str:
    """Pull the helper's one-line scope description.

    Convention: first non-blank line of the top-of-file docstring (Python) or
    top comment block (shell) that ISN'T the filename itself or auth boilerplate.
    Fallback: returns "(scope undocumented)" if nothing parseable.
    """
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return "(unreadable)"

    if path.suffix == ".py":
        # Python: find the first triple-quoted docstring after the shebang/encoding
        m = re.search(r'^"""(.*?)"""', text, re.DOTALL | re.MULTILINE)
        if not m:
            m = re.search(r"^'''(.*?)'''", text, re.DOTALL | re.MULTILINE)
        if not m:
            return "(no docstring)"
        body = m.group(1).strip()
    elif path.suffix == ".sh":
        # Shell: read top comment block after shebang
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#!"):
                continue
            if stripped.startswith("#"):
                lines.append(stripped.lstrip("#").strip())
            elif lines and not stripped:
                lines.append("")
            elif lines:
                break
        body = "\n".join(lines).strip()
    else:
        return "(unsupported file type)"

    if not body:
        return "(empty header)"

    # Find first meaningful descriptive line. Skip lines that:
    # - repeat the filename
    # - are pure Auth: / Scope: / Usage: prefixes
    # - are blank
    fname = path.name
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(fname) or line.endswith(f"-- {fname}"):
            # "drive-api.py -- Google Drive API helper" — strip the prefix
            after = line.split("--", 1)
            if len(after) == 2:
                return after[1].strip()
            continue
        if re.match(r"^(Auth|Scope|Usage|Pattern|Service account|Requires)\s*:", line):
            continue
        return line

    return "(scope unclear)"


def discover_helpers():
    """Return list of (path, scope) tuples for every *-api.{py,sh} in scripts/."""
    helpers = []
    for path in sorted(SCRIPTS_DIR.iterdir()):
        if not path.is_file():
            continue
        if not (path.name.endswith("-api.py") or path.name.endswith("-api.sh")):
            continue
        helpers.append((path, extract_scope(path)))
    return helpers


def build_table(helpers):
    """Return the markdown table block (between AUTOGEN markers)."""
    rows = [
        "| Service domain | Helper script | Scope |",
        "| --- | --- | --- |",
    ]
    for path, scope in helpers:
        # Derive service domain from filename (`gmail-api.py` -> `Gmail`)
        domain = path.stem.replace("-api", "").replace("-", " ").title()
        rel = f"`{path.name}`"  # flat repo-root layout (post-cutover); pulled to /tmp/pbs
        rows.append(f"| **{domain}** | {rel} | {scope} |")
    return "\n".join(rows)


# The routing doc lives in vault_notes (Library/ is git-ignored + absent on Railway). Regen is
# DB-backed one-command via cc_note_sync — no local-file skeleton, no /tmp workaround.
ROUTING_VAULT_PATH = "Library/processes/external-service-routing.md"


def regenerate(check_only=False, print_only=False):
    from cc_note_sync import fetch_body, write_body, splice_block
    helpers = discover_helpers()
    table = build_table(helpers)

    auto_block = "\n".join(
        [
            BEGIN_MARK,
            f"<!-- Auto-generated from {len(helpers)} *-api.{{py,sh}} helpers by helper-script-registry.py. -->",
            f"<!-- CADENCE: regenerated on every connection-updater ritual run; P6-stale iff stated count != live count. count={len(helpers)} -->",
            f"<!-- Do not edit by hand -- run `VAULT=/tmp/pbs python3 /tmp/pbs/helper-script-registry.py` to refresh (writes vault_notes). -->",
            "",
            table,
            "",
            END_MARK,
        ]
    )

    if print_only:
        print(auto_block)
        return 0

    current = fetch_body(ROUTING_VAULT_PATH)
    if current is None:
        print(f"ERROR: note {ROUTING_VAULT_PATH} not found in vault_notes.", file=sys.stderr)
        return 2
    if BEGIN_MARK not in current or END_MARK not in current:
        print(f"ERROR: {ROUTING_VAULT_PATH} is missing AUTOGEN markers.", file=sys.stderr)
        return 2

    new_doc = splice_block(current, BEGIN_MARK, END_MARK, auto_block)

    if check_only:
        if new_doc != current:
            print(f"DRIFT: external-service-routing.md table is stale -- {len(helpers)} helpers detected but doc differs.")
            return 1
        print(f"OK: external-service-routing.md table matches {len(helpers)} helpers.")
        return 0

    if new_doc == current:
        print(f"NO-OP: external-service-routing.md already up to date ({len(helpers)} helpers).")
        return 0

    write_body(ROUTING_VAULT_PATH, new_doc)
    print(f"OK: regenerated table in external-service-routing.md ({len(helpers)} helpers) -> vault_notes.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    check_only = "--check" in args
    print_only = "--print" in args
    sys.exit(regenerate(check_only=check_only, print_only=print_only))
