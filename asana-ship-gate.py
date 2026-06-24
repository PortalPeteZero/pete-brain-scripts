#!/usr/bin/env python3
"""
asana-ship-gate.py — Stop hook. Deterministic close-at-ship enforcement.

The piece that makes the WRITE reliable instead of hopeful: it doesn't depend on
the model remembering to close a task — the harness runs this every time Claude
finishes a turn, and it won't let the session sign off silently if this session
shipped task-linked work whose Asana task is still open.

FAST + SILENT by design. It only does anything when it finds an Asana gid that
this session explicitly tied to shipped work:
  - a gid (121\\d{13}) in a recent git commit message across Pete's repos, OR
  - a `SHIPPED: <gid>` marker in TODAY's daily note (the fallback a session writes
    when it ships non-commit work — a cron, an email, a file — and can't close
    the task in the same breath).
For each such gid it makes ONE Asana GET; if the task is still OPEN it surfaces it
(exit 2, fed back to the model) so it gets closed before sign-off. No gids found
=> exits 0 before any network call (the common case — zero friction). Already-
surfaced gids are remembered per-session so it nudges once, never nags.

Registered as a Stop hook in .claude/settings.json. System design:
[[Library/decisions/2026-06-14-asana-reconciliation-system]].
"""
import sys, os, json, re, subprocess, urllib.request, datetime
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
GID_RE = re.compile(r"\b(121\d{13})\b")
SHIPPED_RE = re.compile(r"SHIPPED:?\s*(121\d{13})", re.I)
REPOS = [os.path.expanduser(p) for p in (
    "~/code/command-centre", "~/code/sygma-platform", "~/code/passion-fit")]


def read_stdin():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def recent_commit_gids():
    gids = set()
    for repo in REPOS:
        if not os.path.isdir(os.path.join(repo, ".git")):
            continue
        try:
            r = subprocess.run(
                ["git", "-C", repo, "log", "--since=1 day ago", "--format=%B"],
                capture_output=True, text=True, timeout=8)
            gids.update(GID_RE.findall(r.stdout))
        except Exception:
            pass
    return gids


def shipped_marker_gids():
    p = f"{VAULT}/Daily/{datetime.date.today().isoformat()}.md"
    if not os.path.isfile(p):
        return set()
    try:
        return set(SHIPPED_RE.findall(open(p, encoding="utf-8").read()))
    except Exception:
        return set()


def asana_open(gid, pat):
    """(is_open, name). On any error returns (False, '') — never block on a hiccup."""
    try:
        req = urllib.request.Request(
            f"https://app.asana.com/api/1.0/tasks/{gid}?opt_fields=completed,name",
            headers={"Authorization": f"Bearer {pat}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            t = json.loads(r.read())["data"]
        return (not t.get("completed")), t.get("name", "")
    except Exception:
        return False, ""


def main():
    data = read_stdin()
    if data.get("stop_hook_active"):
        sys.exit(0)                       # already in a stop continuation — don't loop

    gids = recent_commit_gids() | shipped_marker_gids()
    if not gids:
        sys.exit(0)                       # fast path — nothing ship-referenced, zero cost

    sid = data.get("session_id", "nosession")
    ackpath = f"/tmp/asana-ship-gate-{sid}.json"
    acked = set()
    if os.path.isfile(ackpath):
        try:
            acked = set(json.load(open(ackpath)))
        except Exception:
            pass

    pat_path = f"{VAULT}/Library/processes/secrets/asana-pat"
    if not os.path.isfile(pat_path):
        sys.exit(0)
    pat = open(pat_path).read().strip()

    surface = []
    for gid in sorted(gids - acked):
        is_open, name = asana_open(gid, pat)
        if is_open:
            surface.append((gid, name))

    if not surface:
        sys.exit(0)

    try:                                  # nudge once, never nag
        json.dump(sorted(acked | {g for g, _ in surface}), open(ackpath, "w"))
    except Exception:
        pass

    lines = "\n".join(f"  - {gid}  {name[:60]}" for gid, name in surface)
    print("Close-at-ship check: this session shipped work referencing these Asana "
          "tasks, but they are still OPEN:\n" + lines +
          "\n\nClose each now if the work is done — `python3 Library/processes/scripts/"
          "asana-reconcile.py --ship <gid> --apply-auto`, or close in Asana with an "
          "audit comment. If one is intentionally left open (e.g. awaiting Pete's "
          "call), say so briefly and carry on.", file=sys.stderr)
    sys.exit(2)                           # fed to the model; it acts, then stops (acked = no re-nag)


if __name__ == "__main__":
    main()