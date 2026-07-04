#!/usr/bin/env python3
"""closeout-sweep.py -- the deterministic RECORD gate behind the /closeout skill.

It answers, for every git checkout this session touched: which commits did THIS session
make, which of those are not yet in the Work Log, and (optionally) records them. Ownership
is decided by session_attribution (the shared gitOperation test) -- so it NEVER logs a
parallel session's commits, which is the whole point (the 30-Jun/04-Jul today-bug).

  closeout-sweep.py                 dry run: JSON report of what WOULD be recorded + surfaced
  closeout-sweep.py --apply         also log this session's own unlogged commits (idempotent)
  closeout-sweep.py --human         pretty summary instead of JSON
  closeout-sweep.py --since DATE     window for the 'other sessions' unlogged surface (default: today)
  closeout-sweep.py --git-dir PATH  add a checkout to scan (repeatable); auto-discovers /tmp/pbs + /tmp/*

Owned commits are proven from THIS session's transcript; everything else is only surfaced,
never written. Auto-records are idempotent on work_log.source_ref, so re-running is safe.
"""
import os, sys, re, json, glob, subprocess, datetime

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import session_attribution as SA
import worklog_sha
import worklog  # safe to import: its side effects are under __main__ only


def _today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d")


def _is_git(d):
    return os.path.isdir(os.path.join(d, ".git")) or \
        subprocess.run(["git", "-C", d, "rev-parse", "--git-dir"],
                       capture_output=True, text=True).returncode == 0


def candidate_checkouts(extra):
    dirs = set()
    for d in ["/tmp/pbs"] + list(extra):
        if d and os.path.isdir(d):
            dirs.add(os.path.realpath(d))
    for g in glob.glob("/tmp/*/.git"):
        dirs.add(os.path.realpath(os.path.dirname(g)))
    cwd = os.getcwd()
    if _is_git(cwd):
        dirs.add(os.path.realpath(cwd))
    return sorted(d for d in dirs if _is_git(d))


def repo_slug(git_dir):
    r = subprocess.run(["git", "-C", git_dir, "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    url = r.stdout.strip()
    m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else os.path.basename(git_dir)


def contains_sha(git_dir, sha):
    return subprocess.run(["git", "-C", git_dir, "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True, text=True).returncode == 0


def commits_since(git_dir, since):
    out = subprocess.run(["git", "-C", git_dir, "log", f"--since={since} 00:00:00",
                          "--pretty=%H\t%s", "--no-merges"], capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [l.split("\t", 1) for l in out.stdout.strip().splitlines() if "\t" in l]


def logged_token_set(git_dir):
    res = worklog.ccq("SELECT COALESCE(source_ref,'') AS s, COALESCE(detail,'') AS d FROM work_log")
    text = " ".join(((r.get("s") or "") + " " + (r.get("d") or "")) for r in (res or []))
    return worklog_sha.logged_tokens(text, git_dir)


def log_commit(repo, git_dir, full_sha, subject, owned_note):
    """Append one work_log row for a proven-own commit. area=dev, outcome=unknown (a raw
    main-session commit the per-ship hooks missed); idempotent on source_ref."""
    short = full_sha[:9]
    cmd = ["python3", os.path.join(os.environ.get("VAULT", "/tmp/pbs"), "worklog.py"),
           "--area", "dev",
           "--title", (subject or f"commit {short}")[:180],
           "--evidence", f"commit {short}: {subject}"[:400],
           "--outcome", "unknown",
           "--link", f"https://github.com/{repo}/commit/{full_sha}",
           "--source-ref", f"git:{repo}@{full_sha}"]
    r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ})
    return {"sha": short, "subject": subject, "ok": r.returncode == 0,
            "out": (r.stdout or r.stderr).strip()[:200]}


def run(apply_mode, since, extra_dirs):
    sid, main, is_sub, why = SA.resolve_transcript()
    owned, unattributed, notes = SA.owned_commit_shas()
    report = {
        "session_id": sid, "main_transcript": main, "is_subagent": is_sub,
        "owned_shas": sorted(owned), "unattributed_subruns": unattributed,
        "attribution_notes": notes, "applied": apply_mode, "since": since,
        "repos": [], "warnings": list(notes), "ownership_verifiable": True,
    }
    # When ownership CANNOT be verified (a sub-run, or no session transcript -- e.g. a non
    # Claude-Code runtime like Cowork, where the gitOperation-stamped transcript may be absent),
    # this gate did NOT check anything. It must fail LOUD: never report REMAINING 0 / exit 0,
    # which would be a false all-clear. The auto-attribution requires the Claude Code interactive
    # transcript; elsewhere the caller must fall back to recall-based per-ship logging.
    if is_sub:
        report["ownership_verifiable"] = False
        report["warnings"].insert(0, "REFUSING: this is a sub-run, not the main session. No records written.")
        return report
    if not main:
        report["ownership_verifiable"] = False
        report["warnings"].insert(0, "COULD NOT VERIFY OWNERSHIP: no session transcript resolved (not a Claude "
                                     "Code interactive session, or CLAUDE_CODE_SESSION_ID unset). NOTHING was "
                                     "checked or recorded -- this is NOT a clean pass. Fall back to logging each "
                                     "ship by hand with worklog.py.")
        return report

    checkouts = candidate_checkouts(extra_dirs)
    # Map each owned SHA to the checkout(s) that contain it. gitOperation gives us a short
    # (often 7-char) SHA with NO repo, so we resolve by membership. If a short SHA resolves
    # in MORE THAN ONE checkout (a cross-repo prefix collision), we must NOT auto-place it in
    # all of them -- that could log a foreign commit. Ambiguous ones are surfaced, not placed.
    dirs_for = {s: [d for d in checkouts if contains_sha(d, s)] for s in owned}
    owned_by_dir = {}
    ambiguous = []
    for s, ds in dirs_for.items():
        if len(ds) == 1:
            owned_by_dir.setdefault(ds[0], set()).add(s)
        elif len(ds) > 1:
            ambiguous.append((s, ds))
    unplaced = sorted(s for s, ds in dirs_for.items() if not ds)
    if unplaced:
        report["warnings"].append(
            f"{len(unplaced)} owned commit(s) not found in any scanned checkout ({', '.join(unplaced)}); "
            "their repo isn't on disk here, so they can't be reconciled. Surface, don't assume logged.")
    for s, ds in ambiguous:
        report["warnings"].append(
            f"owned SHA {s} resolves in MULTIPLE checkouts ({', '.join(os.path.basename(x) for x in ds)}) "
            "-- a short-SHA prefix collision across repos. Surfaced, NOT auto-logged; confirm its repo.")

    for d, owned_here in sorted(owned_by_dir.items()):
        repo = repo_slug(d)
        tokens = logged_token_set(d)
        todays = commits_since(d, since)
        unlogged = [(full, subj) for full, subj in todays if not worklog_sha.is_present(full, tokens)]
        mine_unlogged = [(f, s) for f, s in unlogged if SA.owns(f, owned_here)]
        others_unlogged = [(f, s) for f, s in unlogged if not SA.owns(f, owned_here)]
        recorded = []
        if apply_mode and mine_unlogged:
            for full, subj in mine_unlogged:
                recorded.append(log_commit(repo, d, full, subj, "owned"))
        report["repos"].append({
            "repo": repo, "git_dir": d,
            "owned_here": sorted(owned_here),
            "mine_unlogged": [{"sha": f[:9], "subject": s} for f, s in mine_unlogged],
            "others_unlogged": [{"sha": f[:9], "subject": s} for f, s in others_unlogged],
            "recorded": recorded,
        })
        if others_unlogged:
            report["warnings"].append(
                f"{repo}: {len(others_unlogged)} unlogged commit(s) NOT auto-attributed to this session. "
                "Most are other live sessions' work (correctly left alone) -- but a commit YOU made in a "
                "way the transcript didn't stamp (e.g. `git commit -q`, or a commit buried in a compound "
                "shell command) also lands here. Confirm ownership before logging any; never auto-log these.")
        failed = [x for x in recorded if not x.get("ok")]
        if failed:
            report["warnings"].append(
                f"{repo}: {len(failed)} work_log write(s) FAILED ({', '.join(x['sha'] for x in failed)}) "
                "-- these commits are NOT recorded and still count as unlogged. "
                + "; ".join(x.get("out", "") for x in failed)[:220])
    return report


def _remaining(report):
    """Owned commits still NOT in the work log after this run: mine_unlogged minus the writes
    that ACTUALLY succeeded. A FAILED work_log write must never count as recorded -- otherwise
    the gate would report 'clean' while a commit silently went unlogged (the exact failure the
    skill exists to prevent)."""
    return sum(len(rp["mine_unlogged"]) - len([x for x in rp["recorded"] if x.get("ok")])
               for rp in report["repos"])


def _human(r):
    print(f"CLOSEOUT SWEEP -- session {r['session_id'][:8]}  (since {r['since']}, "
          f"{'APPLIED' if r['applied'] else 'dry-run'})")
    print(f"owned commits this session: {', '.join(s[:9] for s in r['owned_shas']) or '(none)'}")
    for w in r["warnings"]:
        print(f"  ! {w}")
    if not r.get("ownership_verifiable", True):
        print("\nOWNERSHIP UNVERIFIABLE — nothing checked or recorded. NOT a clean pass.")
        return
    if not r["repos"]:
        print("no touched checkouts with owned commits found.")
    for rp in r["repos"]:
        print(f"\n[{rp['repo']}]  ({rp['git_dir']})")
        if rp["mine_unlogged"]:
            for c in rp["mine_unlogged"]:
                print(f"   MINE, unlogged: {c['sha']}  {c['subject'][:70]}")
        else:
            print("   mine: all logged ✓")
        for c in rp["others_unlogged"]:
            print(f"   UNATTRIBUTED, unlogged (surface — confirm before logging): {c['sha']}  {c['subject'][:70]}")
        for rec in rp["recorded"]:
            if rec["ok"]:
                print(f"   -> recorded {rec['sha']}: ok")
            else:
                print(f"   -> WRITE FAILED {rec['sha']}: {rec['out']}  (still unlogged)")
    # the runnable-gate one-liner (a failed write is NOT recorded -- see _remaining)
    print(f"\nUNLOGGED-OWNED REMAINING: {_remaining(r)}")


def main():
    args = sys.argv[1:]
    apply_mode = "--apply" in args
    human = "--human" in args
    since = _today()
    extra = []
    i = 0
    argv = [a for a in args if a not in ("--apply", "--human")]
    while i < len(argv):
        if argv[i] in ("--since", "--git-dir"):
            if i + 1 >= len(argv):
                sys.exit(f"closeout-sweep: {argv[i]} needs a value "
                         f"(e.g. {argv[i]} {'YYYY-MM-DD' if argv[i]=='--since' else '/path/to/checkout'})")
            if argv[i] == "--since":
                since = argv[i + 1]
            else:
                extra.append(argv[i + 1])
            i += 2
        else:
            i += 1
    rep = run(apply_mode, since, extra)
    if human:
        _human(rep)
    else:
        print(json.dumps(rep, indent=2))
    # exit 3 = ownership UNVERIFIABLE (no transcript / sub-run) -- gate could not run, NOT a clean
    # pass; a false REMAINING-0 here would be the worst outcome. exit 2 = ran, but an owned commit
    # stayed unlogged (incl. a failed write, see _remaining). exit 0 = clean.
    if not rep.get("ownership_verifiable", True):
        sys.exit(3)
    if apply_mode:
        sys.exit(2 if _remaining(rep) else 0)


if __name__ == "__main__":
    main()
