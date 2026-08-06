#!/usr/bin/env python3
"""doc-command-check.py — do the commands our own docs tell us to run actually exist?

A process note that says "run `x.py --flag`" is an instruction a future session WILL follow. If the
script was renamed, or the flag was removed, nothing errors: argparse-less scripts swallow unknown
flags silently and do something else entirely. The session then reports work it never did.

Born 19 Jul 2026: [[pf-weekly-loop]] and [[pf-journal]] both told sessions to run
`garmin-daily-cc.py --publish-only` to publish the week/lesson pages. That script parses NO
arguments (its one job is Garmin -> garmin_daily), so the flag was ignored and a full Garmin pull
ran instead. The publish path had been removed; the docs were never updated. Silent for weeks.

What it checks, across every `vault_notes` body:
  1. every `python3 .../<script>.py` invocation names a script that EXISTS in the repo
  2. every `--flag` on that invocation appears somewhere in that script's source

Deliberately narrow to keep false positives near zero:
  - only real invocations (must be prefixed `python3`), so prose like "it used to say `x.py --foo`"
    in a correction note is ignored by design
  - a flag passes if its literal string appears anywhere in the script (argparse, sys.argv, docstring)

Report-only. Exits 0 clean / 2 with findings, so it can gate a session as well as feed drift-check.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/doc-command-check.py [--json]
"""
import time
import os, re, sys, json, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
CC_SQL = os.path.join(VAULT, "cc-sql.py")

# `python3 /tmp/pbs/foo.py --bar --baz` (optionally VAULT=… prefixed, any dir). The trailing capture
# stops at a backtick/newline so we only read flags belonging to THIS command.
CMD = re.compile(r"python3\s+[\"']?([\w./{}$-]*/)?([\w.-]+\.py)[\"']?([^`\n\r]*)")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+)")

# Flags the shell/python consume, not the script.
IGNORE_FLAGS = {"--help", "--version"}

# Generic stand-ins used in worked examples ("run `x.py --flag`"), not real scripts.
PLACEHOLDERS = {"x.py", "y.py", "foo.py", "bar.py", "script.py", "tool.py", "name.py", "thing.py"}

# Opt-out marker for notes that QUOTE a command historically (post-mortems, migration write-ups)
# rather than instruct anyone to run it. Put it anywhere in the note body.
EXEMPT = "<!-- doc-command-check: historical -->"

# Some notes are historical BY TYPE and always will be, so requiring a hand-added marker on each one
# just means the same false positive returns every time a plan is executed. A snapshot exists to
# record what things looked like before a change; a completed or scrapped plan is intent that has
# already happened or been abandoned. The brain skill's own plan-lifecycle rule says a finished plan
# must never be read as live state -- so a dead command inside one is the record working, not rot.
# (Added 27 Jul 2026: all 8 findings at the time were the retired people tools quoted inside exactly
# these two kinds of note -- a pre-consolidation snapshot and the EXECUTED consolidation plan.)
HISTORICAL_TYPES = {"snapshot"}
HISTORICAL_PLAN_STATUSES = {"completed", "executed", "scrapped", "done", "superseded"}


Q_FAILED = []          # a read that ERRORED is not a read that found nothing


def q(sql):
    """A failed read is recorded, never silently returned as an empty result.

    Same class as the entity-enrich-signoff fault fixed 6 Aug 2026: on a throttle or a network
    blip this returned [], the caller counted zero findings, and the verdict line said all clear
    having checked nothing. The stderr line alone is not enough -- nobody reads a cron's stderr
    when its report says everything is fine. Retries a throttled read first.
    """
    last = ""
    for attempt in range(4):
        r = subprocess.run([sys.executable, CC_SQL, sql], capture_output=True, text=True,
                           env={**os.environ, "VAULT": VAULT}, timeout=90)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and not out.startswith("ERROR"):
            try:
                d = json.loads(out)
                return d if isinstance(d, list) else []
            except Exception:
                last = f"unreadable reply: {out[:120]}"
                break
        last = ((r.stderr or "").strip() or out)[:200]   # cc-sql prints its errors to stdout
        if "429" in last or "Too Many Requests" in last or "Throttler" in last:
            time.sleep(2 * (attempt + 1))
            continue
        break
    sys.stderr.write(f"cc-sql error: {last}\n")
    Q_FAILED.append(last)
    return []


def main():
    as_json = "--json" in sys.argv
    rows = q("SELECT title, body, type, frontmatter->>'status' AS status "
             "FROM vault_notes WHERE body LIKE '%python3%'")
    findings = []          # dicts: kind, note, script, flag
    exempted = []          # notes carrying the historical opt-out marker
    seen = set()           # dedupe (note, script, flag) across repeated snippets

    for row in rows:
        title, body = row.get("title") or "?", row.get("body") or ""
        ntype = (row.get("type") or "").lower()
        nstatus = (row.get("status") or "").lower()
        # A note that RECOUNTS a command (a post-mortem quoting the job it killed) is not telling
        # anyone to run it. Those notes opt out explicitly rather than being rewritten into a lie.
        if EXEMPT in body:
            exempted.append(title)
            continue
        # ...and some are historical by their own type/status, with no marker needed.
        if ntype in HISTORICAL_TYPES or ("plan" in ntype and nstatus in HISTORICAL_PLAN_STATUSES):
            exempted.append(f"{title} [{ntype}{'/' + nstatus if nstatus else ''}]")
            continue
        for m in CMD.finditer(body):
            docdir, script, tail = (m.group(1) or ""), m.group(2), m.group(3)
            if script.lower() in PLACEHOLDERS or "<" in script or "{" in script:
                continue
            # Honour the sub-directory the doc actually names (helpers are mostly flat at the repo
            # root, but some live in a package dir e.g. account/). Resolving by basename alone both
            # hides a wrong path and invents one.
            subdir = ""
            if docdir:
                tail_dir = docdir.rstrip("/").split("/")[-1]
                # "..." is the docs' own abbreviation for the repo path, not a directory.
                if tail_dir and tail_dir not in ("pbs", "tmp", "...", ".", "..") and not tail_dir.startswith("$"):
                    subdir = tail_dir
            documented = os.path.join(VAULT, subdir, script) if subdir else os.path.join(VAULT, script)
            if not os.path.exists(documented):
                # Same script, different place? That is a wrong-path doc, not a dead reference.
                found_at = None
                for root, _dirs, files in os.walk(VAULT):
                    if "/.git" in root:
                        continue
                    if script in files:
                        found_at = os.path.relpath(os.path.join(root, script), VAULT)
                        break
                kind = "wrong-path" if found_at else "missing-script"
                key = (title, script, kind)
                if key not in seen:
                    seen.add(key)
                    findings.append({"kind": kind, "note": title, "script": script, "flag": None,
                                     "documented": os.path.relpath(documented, VAULT), "actual": found_at})
                if not found_at:
                    continue
                documented = os.path.join(VAULT, found_at)
            path = documented
            try:
                src = open(path, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for flag in FLAG.findall(tail):
                if flag in IGNORE_FLAGS or flag in src:
                    continue
                key = (title, script, flag)
                if key not in seen:
                    seen.add(key)
                    findings.append({"kind": "dead-flag", "note": title, "script": script, "flag": flag})

    if as_json:
        print(json.dumps({"findings": findings, "count": len(findings),
                          "notes_scanned": len(rows), "exempted": exempted,
                          "could_not_check": Q_FAILED}, indent=2))
    elif Q_FAILED:
        # Zero findings from a scan that could not read its notes is not a pass.
        print(f"doc-command-check: COULD NOT CHECK — {len(Q_FAILED)} database read(s) failed, so this "
              f"run scanned {len(rows)} note(s) and proves nothing. First error: {Q_FAILED[0][:140]}")
        print("Re-run it before treating the docs as clean.")
    elif not findings:
        print(f"doc-command-check: 0 findings — every documented command exists "
              f"({len(rows)} notes scanned, {len(exempted)} historical).")
    else:
        print(f"doc-command-check: {len(findings)} finding(s) across {len(rows)} notes scanned\n")
        for f in findings:
            if f["kind"] == "missing-script":
                print(f"  ⚠ MISSING SCRIPT  {f['script']:32s} referenced by note: {f['note']}")
            elif f["kind"] == "wrong-path":
                print(f"  ⚠ WRONG PATH      {f['script']:32s} doc says {f['documented']}, actually {f['actual']} — note: {f['note']}")
            else:
                print(f"  ⚠ DEAD FLAG       {f['script']} {f['flag']:20s} note: {f['note']}")
        print("\nFix the NOTE (the script is the source of truth), or restore the flag if the doc was right.")
    return 2 if (findings or Q_FAILED) else 0


if __name__ == "__main__":
    sys.exit(main())
