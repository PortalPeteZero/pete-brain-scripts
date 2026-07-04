#!/usr/bin/env python3
"""worklog_sha.py -- the ONE SHA-matching helper shared by `worklog.py reconcile`
(discovery: which commits aren't logged) and the closeout skill (aligning reconcile's
full SHAs against the gitOperation-owned set). Factored out of reconcile() so the two
can never drift into disagreeing about what "SHA X is present in text Y" means.

  sha_tokens(text)                 -> set of [0-9a-f]{7,40} tokens found in free text
  expand_ranges(text, git_dir)     -> extra SHAs from `A..B` ranges resolved in a repo
  logged_tokens(text, git_dir)     -> sha_tokens ∪ expand_ranges  (the full logged set)
  is_present(full_sha, tokens)     -> full_sha starts with any token (prefix match)

Matching is deliberately prefix-based: a short 7-char token in a work_log note matches
the full 40-char commit SHA. All lowercased.
"""
import re, subprocess

_TOKEN_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])")
_RANGE_RE = re.compile(r"([0-9a-f]{7,40})\.\.([0-9a-f]{7,40})")


def sha_tokens(text):
    """Every SHA-like token in free text (source_ref + detail of the work log)."""
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 7}


def expand_ranges(text, git_dir):
    """`A..B` in a logged ref covers every commit between A and B; resolve them in the
    repo so one range/feature entry accounts for all its commits."""
    out = set()
    for a, b in _RANGE_RE.findall((text or "").lower()):
        try:
            rl = subprocess.run(["git", "-C", git_dir, "rev-list", f"{a}^..{b}"],
                                capture_output=True, text=True)
        except OSError:
            continue
        if rl.returncode == 0:
            out.update(x for x in rl.stdout.split() if len(x) >= 7)
    return out


def logged_tokens(text, git_dir):
    return sha_tokens(text) | expand_ranges(text, git_dir)


def is_present(full_sha, tokens):
    """True if this full SHA is referenced by any token (token is a prefix of it, or
    -- for an already-short full -- vice-versa)."""
    f = (full_sha or "").lower()
    for t in tokens:
        t = (t or "").lower()
        if t and (f.startswith(t) or t.startswith(f)):
            return True
    return False
