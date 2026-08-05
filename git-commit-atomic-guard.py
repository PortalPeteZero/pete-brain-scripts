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
import sys, json, re, os

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


_AUTHOR_GATED_REPOS = ("leakguard", "command-centre")

_AUTHOR_MSG = (
    "BLOCKED — this repo's Vercel project has COMMIT-AUTHOR VERIFICATION on.\n"
    "  A push whose commit author is not a recognised GitHub user is accepted by git and then\n"
    "  SILENTLY NEVER BUILT (readyStateReason: 'could not associate the committer with a GitHub\n"
    "  user', seatBlock: COMMIT_AUTHOR_REQUIRED). The app just looks unchanged, so the failure is\n"
    "  invisible until someone checks the deploy.\n"
    "  Commit as the verified author instead:\n"
    "    git -c user.name=PortalPeteZero -c user.email=portalpetezero@users.noreply.github.com commit …\n"
    "  Then confirm the deploy actually reached READY before calling it shipped."
)


def unverified_author_commit(cmd: str):
    """A commit into an author-verified repo without an explicit verified author identity.

    Replaces the resident rule `feedback_leakguard_vercel_commit_author` (plan step 4). The harm is
    that it fails SILENTLY: the push succeeds and the deploy never runs, so nothing surfaces the
    mistake. Deliberately narrow — it only fires on a `git commit` that (a) names an author-gated
    repo in the command or runs inside its checkout, and (b) sets no `user.email`/`--author`.
    """
    import re as _re
    masked = _mask(cmd)  # quotes + heredocs are DATA, never the repo being committed to
    if not _GIT_COMMIT_RE.search(masked):
        return None
    low = masked.lower()
    # already carries an explicit identity → fine
    if "user.email=" in low or "--author" in low:
        return None

    # Judge the REPO TARGET only — the `git -C <path>` argument, else the working directory.
    # An earlier draft searched the whole command string and fired on a legitimate script whose
    # unrelated file path happened to contain "Command-Centre". A gate that blocks real work is
    # worse than no gate (Pete, 22 Jul), so the match is now scoped to the actual target.
    m = _re.search(r"-C\s+(\S+)", masked)
    if m:
        target = m.group(1).lower()
    else:
        try:
            target = os.getcwd().lower()
        except Exception:
            return None
    if any(r in target for r in _AUTHOR_GATED_REPOS):
        return _AUTHOR_MSG
    return None


_QUIET_RE = re.compile(r"\bgit\b[^|;&]*\bcommit\b[^|;&]*(?:\s-q\b|\s--quiet\b|\s-[a-zA-Z]*q[a-zA-Z]*\b)")

_QUIET_MSG = """BLOCKED: do not commit with -q / --quiet.

A quiet commit is NOT stamped with toolUseResult.gitOperation.commit.sha, so the harness cannot
attribute it to this session. session_attribution.py then reports "0 owned commits" and the closeout
record gate is blind — the commit silently never reaches the Work Log.

This is the exact failure the rule was written from (verified live 2026-07-04). The guard used to
block only CHAINED commits and had no -q check at all, so the headline case walked straight through.

Fix: drop the flag.
  git commit -m "..."        ← plain, its own call
If you genuinely need it quiet, log the commit yourself afterwards:
  VAULT=/tmp/pbs python3 /tmp/pbs/worklog.py --source-ref "git:<owner>/<repo>@<sha>" ..."""


def quiet_commit(cmd: str) -> bool:
    """A `git commit -q` / `--quiet`, which silently breaks session attribution.

    Added 24 Jul 2026. An audit of feedback_closeout_attribution_commit_normally found this guard
    enforced only ONE of the rule's four clauses: chained commits. The rule's HEADLINE clause — that
    -q is not stamped — had zero code, and a live piped test confirmed `git commit -q` was allowed.
    """
    return bool(_QUIET_RE.search(_mask(cmd)))


# --- shared-clone protection (added 28 Jul 2026) -------------------------------------------------
# /tmp/pbs is ONE checkout shared by every concurrent session (the boot kernel materialises it).
# Two sessions doing git operations there race each other's index: on 28 Jul 2026 a fleet-work
# session committed while a LeakGuard session had a file staged — the commit wrapped the other
# session's work under the wrong message, then a recovery reset un-staged the other session's file
# mid-flow. Nothing was lost, but only by luck and hand-unpicking. Session-local git work belongs in
# a session-local clone; /tmp/pbs is a runtime artefact.
_SHARED_CLONE_RE = re.compile(r"(?:/private)?/tmp/pbs(?:/|\s|$|['\"])")

# Subcommands that mutate the index, worktree branch state, or history. Read-only git
# (status/log/show/diff/fetch/grep) and `pull` (tool refresh) stay allowed.
_GIT_MUTATE_RE = re.compile(
    r"\bgit\b(?:\s+-C\s+(\S+)|\s+-[^\sC]\S*)*\s+"
    r"(add|commit|reset|restore|stash|rebase|merge|cherry-pick|am|rm|mv|checkout|switch|push|clean)\b"
)


def shared_clone_mutation(cmd: str):
    """A git mutation whose target repo is the SHARED /tmp/pbs checkout."""
    masked = _mask(cmd)
    m = _GIT_MUTATE_RE.search(masked)
    if not m:
        return None
    # Judge the repo TARGET: the -C argument if given, else `cd /tmp/pbs`-style in the same command,
    # else the working directory. (Same target-scoping principle as unverified_author_commit.)
    target = m.group(1) or ""
    if not target:
        cd = re.search(r"(?:^|[\s(])cd\s+(\S+)", masked)
        if cd:
            target = cd.group(1)
    if not target:
        try:
            target = os.getcwd()
        except Exception:
            return None
    if _SHARED_CLONE_RE.match(target) or _SHARED_CLONE_RE.match(target + " "):
        return _SHARED_MSG
    return None


_SHARED_MSG = """BLOCKED: git mutations in /tmp/pbs are forbidden — it is ONE checkout SHARED by every live session.
Staging, committing or resetting here races any parallel session's git state (two sessions collided
exactly this way on 28 Jul 2026: one session's commit swallowed the other's staged file).

Do your git work in a session-local clone instead:
  PAT=$(cat "/tmp/pbs/Library/processes/secrets/github-pat")
  git clone "https://${PAT}@github.com/PortalPeteZero/pete-brain-scripts.git" /tmp/pbs-work-<yourtask>
  cp /tmp/pbs/<your-file> /tmp/pbs-work-<yourtask>/   # bring your edited file across
  # then add / commit / push THERE (commit still as its own Bash call)

Reading (git log/show/diff/status) and running tools from /tmp/pbs is fine — only mutations are blocked.
Your edited file in /tmp/pbs keeps working for THIS session; the next boot pulls the pushed version."""


# --- shared-clone FILE writes (added 5 Aug 2026) --------------------------------------------------
# The git-mutation block above stops `git add/commit/reset` in /tmp/pbs, but nothing stopped a plain
# `cp my-tool.py /tmp/pbs/`. That leaves the file MODIFIED/UNTRACKED in the one checkout every live
# session shares, so it surfaces in their `git status` as dirt they cannot attribute or safely
# revert. On 5 Aug 2026 a Passion Fit session left `skills/pf-seminars.skill` and its SKILL.md
# modified there; two other sessions reported it to Pete as "a mess left behind", and one logged
# "pf-seminars.skill sits modified/uncommitted in /tmp/pbs - NOT this session's".
#
# Deliberately narrow, because a gate that blocks real work is worse than no gate (Pete, 22 Jul):
# it fires ONLY on a copy/move/redirect/tee whose DESTINATION is inside /tmp/pbs. Reading from
# /tmp/pbs, running its tools, and the boot kernel (a python script, not a shell copy) are untouched.
_IN_PBS_RE = re.compile(r"^(?:/private)?/tmp/pbs(?:/|$)")
_COPY_CMD_RE = re.compile(r"(?:^|[\s(])(?:cp|mv|rsync|install|ln)\b")
# `> path`, `>> path`, `tee [-flags] path` — here the path IS the destination.
_REDIRECT_RE = re.compile(r"(?:>>?\s*|\btee\b(?:\s+-\S+)*\s+)((?:/private)?/tmp/pbs(?:/\S*)?)")

_SHARED_WRITE_MSG = """BLOCKED: do not write files into /tmp/pbs — it is ONE checkout SHARED by every live session.

A copy into /tmp/pbs leaves the file modified/untracked in every other session's `git status`, as dirt
they cannot attribute or safely revert. That happened on 5 Aug 2026 and two sessions reported it to
Pete as a mess left behind.

What to do instead:
  • Just RUN the tool from your own clone by path — it does not need to live in /tmp/pbs:
      VAULT=/tmp/pbs python3 /tmp/pbs-work-<yourtask>/<tool>.py …
    (VAULT=/tmp/pbs still resolves the secrets; only the CODE comes from your clone.)
  • Committing? Do it in the session-local clone, push, and let the next boot pull it.

Also watch the case no command-line guard can see: a GENERATOR run out of /tmp/pbs (e.g.
`python3 /tmp/pbs/package-skill.py`) writes into ITS OWN directory, not your cwd. Run it from your
clone. Before you close, confirm none of the dirt is yours:  git -C /tmp/pbs status --short"""


def shared_clone_write(cmd: str):
    """A shell copy/move/redirect whose DESTINATION is inside the shared /tmp/pbs checkout.

    Judges the destination only. `cp /tmp/pbs/tool.py /tmp/mine/` copies OUT and is fine — an
    earlier draft matched the path anywhere after `cp` and would have blocked it, which is the
    false-positive class this guard's whole design forbids.
    """
    masked = _mask(cmd)  # quoted text is DATA — a path inside a string is not a redirect
    for seg in _SPLIT_RE.split(masked):
        seg = seg.strip()
        if not seg:
            continue
        if _REDIRECT_RE.search(seg):
            return _SHARED_WRITE_MSG
        if _COPY_CMD_RE.search(seg):
            # destination of cp/mv/rsync/ln is the LAST non-flag token
            args = [t for t in seg.split() if not t.startswith("-")]
            if len(args) >= 3 and _IN_PBS_RE.match(args[-1]):
                return _SHARED_WRITE_MSG
    return None


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
        shared = shared_clone_mutation(cmd)
        if shared:
            sys.stderr.write(shared + "\n")
            return 2
        shared_w = shared_clone_write(cmd)
        if shared_w:
            sys.stderr.write(shared_w + "\n")
            return 2
        if is_chained_commit(cmd):
            sys.stderr.write(_MSG + "\n")
            return 2
        if quiet_commit(cmd):
            sys.stderr.write(_QUIET_MSG + "\n")
            return 2
        why = unverified_author_commit(cmd)
        if why:
            sys.stderr.write(why + "\n")
            return 2
    except Exception:
        return 0  # any guard bug → allow, never brick
    return 0


if __name__ == "__main__":
    sys.exit(main())
