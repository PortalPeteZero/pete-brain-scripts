#!/usr/bin/env python3
"""
git-commit-atomic-guard.py — PreToolUse hook: a `git commit` must be its OWN Bash call.

WHY THIS EXISTS
  Nothing links a commit to a session except the harness stamp
  `toolUseResult.gitOperation.commit.sha`, which is only written when a Bash call's action is
  UNAMBIGUOUSLY a git commit. Chain it — `git add … && git commit … && git push` — and the stamp is
  never written, so `session_attribution.py` reports "0 owned commits" and the closeout record gate is
  blind. This bit two sessions running (20 + 21 Jul 2026) despite a written lesson (2026-07-17). A
  written rule was not enough; this makes it mechanical.

WHAT IT BLOCKS
  A Bash command where a `git commit` is chained to any other command via `&&`, `||`, `;`, `|`, or a
  newline. The fix is trivial and is printed on block: run the commit as its own call, stage in a
  separate call (or with `git commit -a`), push in a separate call, and use `git -C <dir> commit`
  instead of `cd <dir> && git commit`.

WHAT IT ALLOWS (no false positives — this is the whole design constraint)
  • A standalone `git commit …`, including `git -C /path commit …` and `git commit -a …`.
  • A commit message that itself contains `&&` / `;` / a heredoc body — the operator lives inside a
    quoted string or heredoc, which is masked out before the structural check (same technique as
    local-write-guard.py). `git commit -m "$(cat <<'EOF' … EOF)"` is a SINGLE command → allowed.
  • Any command with no `git commit` in it at all.

EXIT CONTRACT (Claude Code PreToolUse): exit 2 + stderr ⇒ BLOCK; exit 0 ⇒ allow.
FAIL-OPEN: any internal error ⇒ exit 0. A guard bug must never brick a session.
"""
import sys, json, re

# Heredoc bodies → masked to a single space. A heredoc is stdin data, never shell structure.
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?^\s*\2\s*$", re.S | re.M)
_SQUOTE_RE = re.compile(r"'[^']*'")
_DQUOTE_RE = re.compile(r'"(?:[^"\\]|\\.)*"')

# Command-joining operators (structural, only meaningful once quotes/heredocs are masked away).
_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")

# A git-commit invocation: the word `git` followed (same segment) by the `commit` subcommand.
# `commit-tree` / `commit-graph` are different subcommands — require a word boundary after `commit`.
_GIT_COMMIT_RE = re.compile(r"\bgit\b(?:\s+-C\s+\S+|\s+-[^\s]+)*\s+commit\b")


def _mask(cmd: str) -> str:
    """Blank out heredoc bodies and quoted strings so only real shell structure remains."""
    masked = _HEREDOC_RE.sub(" ", cmd)
    masked = _SQUOTE_RE.sub(" ", masked)
    masked = _DQUOTE_RE.sub(" ", masked)
    return masked


def is_chained_commit(cmd: str) -> bool:
    if not cmd:
        return False
    masked = _mask(cmd)
    if not _GIT_COMMIT_RE.search(masked):
        return False  # no real (unquoted) git-commit invocation → nothing to guard
    segments = [s for s in _SPLIT_RE.split(masked) if s.strip()]
    if len(segments) <= 1:
        return False  # a lone git commit — exactly what we want
    # More than one command AND one of them is a git commit → the stamp-breaking pattern.
    return any(_GIT_COMMIT_RE.search(s) for s in segments)


_MSG = """BLOCKED: run `git commit` as its OWN Bash call — do not chain it with && / || / ; / | / newline.

Chaining a commit breaks the harness stamp (toolUseResult.gitOperation.commit.sha), so the commit
becomes invisible to session_attribution.py and the closeout record gate reports "0 owned commits".
This has silently bitten two sessions running.

Fix — split into separate Bash calls:
  1. stage:   git add -A            (or use `git commit -a` to stage+commit tracked files in one)
  2. commit:  git commit -m "..."   ← its own call, nothing chained; use `git -C <dir> commit` not `cd <dir> && ...`
  3. push:    git push
The commit message may still contain && or a heredoc — only the SHELL chaining is blocked, not text."""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # can't parse → fail open
    try:
        tool = payload.get("tool_name") or payload.get("tool") or ""
        if tool != "Bash" and not tool.endswith(("start_process", "interact_with_process")):
            return 0
        cmd = (payload.get("tool_input") or {}).get("command") \
            or (payload.get("tool_input") or {}).get("input") or ""
        if is_chained_commit(cmd):
            sys.stderr.write(_MSG + "\n")
            return 2
    except Exception:
        return 0  # any guard bug → allow, never brick
    return 0


if __name__ == "__main__":
    sys.exit(main())
