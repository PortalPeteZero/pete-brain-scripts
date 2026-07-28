#!/usr/bin/env python3
"""
truncation-guard.py — PreToolUse hook: read-contract tools must be run WHOLE, never truncated.

WHY THIS EXISTS (28 Jul 2026)
  people.py exists so a person lookup asks all four stores at once; its contract is that the
  caller READS the whole answer. On 28 Jul a session ran `people.py find "Will" | head -25`,
  which silently cut the output before the phone-contacts store, then reported "checked all four
  stores" to Pete. The exact class triage-read.py was built to kill one day earlier (`[:600]`
  body slices) — but that gate only covers triage rounds. This one covers the pipe.

  Pete: "took 2 days to build this and it still doesn't work" — the tool worked; the pipe broke
  the contract. A rule saying "don't truncate" is words. This refuses the command.

WHAT IT BLOCKS
  A Bash command that pipes a READ-CONTRACT tool into a truncating/filtering consumer
  (head, tail, grep, sed, awk, cut, wc) — directly or anywhere downstream in the same pipeline
  segment. Read-contract tools (the full-output-or-nothing set):
    people.py · whereis.py · triage-read.py · engine-manifest.py · ee-facts.py · ee-signoff.py
    · entity-enrich-signoff.py · triage-signoff.py · gate-report.py · connection-parity.py
  Also blocked: redirecting these tools' stdout to a file (> / >> / tee) — a file you may never
  read is the same hole (the triage-read known-limit, made refusable here).

WHAT IT ALLOWS (design constraint: no false positives on real work)
  • Running the tool bare, with any of its own flags.
  • Truncating anything NOT on the list (build logs, curl, git log …) — head/tail stay legal.
  • The tool name appearing inside a quoted string or heredoc (e.g. a commit message mentioning
    people.py) — quotes/heredocs are masked before the structural check.

EXIT CONTRACT (Claude Code PreToolUse): exit 2 + stderr ⇒ BLOCK; exit 0 ⇒ allow.
FAIL-OPEN: any internal error ⇒ exit 0. A guard bug must never brick a session.
"""
import sys, json, re

_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?^\s*\2\s*$", re.S | re.M)
_SQUOTE_RE = re.compile(r"'[^']*'")
_DQUOTE_RE = re.compile(r'"(?:[^"\\]|\\.)*"')

READ_CONTRACT = (
    "people.py", "whereis.py", "triage-read.py", "engine-manifest.py", "ee-facts.py",
    "ee-signoff.py", "entity-enrich-signoff.py", "triage-signoff.py", "gate-report.py",
    "connection-parity.py",
)
TRUNCATORS = re.compile(r"^\s*(head|tail|grep|egrep|fgrep|sed|awk|cut|wc|tee)\b")
REDIRECT = re.compile(r"(?<![0-9&])>\s*\S")


def main():
    try:
        payload = json.load(sys.stdin)
        cmd = (payload.get("tool_input") or {}).get("command") or ""
        if not cmd:
            sys.exit(0)
        masked = _HEREDOC_RE.sub(" ", cmd)
        masked = _SQUOTE_RE.sub("''", masked)
        masked = _DQUOTE_RE.sub('""', masked)
        # examine each command chain segment; within a segment, pipeline stages
        for segment in re.split(r"&&|\|\||;|\n", masked):
            stages = segment.split("|")
            hit = None
            for i, stage in enumerate(stages):
                if any(t in stage for t in READ_CONTRACT):
                    tool = next(t for t in READ_CONTRACT if t in stage)
                    # blocked if ANY downstream stage truncates/filters
                    for later in stages[i + 1:]:
                        if TRUNCATORS.match(later.strip()):
                            hit = (tool, later.strip().split()[0])
                            break
                    if not hit and REDIRECT.search(stage):
                        hit = (tool, "redirect-to-file")
                if hit:
                    break
            if hit:
                tool, how = hit
                sys.stderr.write(
                    f"BLOCKED by truncation-guard: {tool} is a READ-CONTRACT tool — its output must "
                    f"be read WHOLE, and this command feeds it through `{how}`.\n"
                    f"Run it bare and read everything it prints. If the output is long, the harness "
                    f"persists it to a file automatically — read THAT file in full.\n"
                    f"Why: on 28 Jul 2026 `people.py find | head -25` silently cut the phone-contacts "
                    f"store and the session reported 'checked all four stores'. The pipe, not the "
                    f"tool, broke the contract — so the pipe is what gets refused.\n"
                )
                sys.exit(2)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail-open


if __name__ == "__main__":
    main()
