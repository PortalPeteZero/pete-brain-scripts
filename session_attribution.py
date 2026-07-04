#!/usr/bin/env python3
"""session_attribution.py -- the ONE ownership test shared by every end-of-session
reconcile-writer (brain Compress Step 7c AND the closeout skill), so whichever runs
first, in any order, only ever logs ITS OWN commits.

WHY THIS EXISTS
  worklog.py reconcile is whole-repo and has NO session concept -- it lists every
  commit in a repo that isn't in work_log, regardless of who made it. A writer that
  "logs every commit reconcile flags" therefore grabs OTHER live sessions' commits
  (the 30-Jun / 04-Jul "grabbed 6 other-session commits" bug). The only signal that
  unambiguously ties a commit to THIS session is the session's own transcript: the
  harness stamps `toolUseResult.gitOperation.commit.sha` on a commit a tool call in
  THIS transcript actually created. That structured field -- never stdout text -- is
  the ownership test. (Verified: on a 54 MB / 5089-line transcript it yields exactly
  the session's real commits with zero false positives, where a stdout hex-grep of the
  same calls yielded 40+ false positives from `git status` / `ls-remote` / push ranges.)

WHAT IT DOES
  owned_commit_shas() -> (shas:set[str], unattributed:int, notes:list[str])
     the set of short/long commit SHAs THIS session created, read ONLY from
     gitOperation.commit.sha across the main transcript + any of this session's
     subagent/workflow transcripts. `unattributed` + `notes` carry anything it could
     not parse or safely attribute -- surfaced loudly, never silently dropped.

  resolve_transcript() -> (session_id, main_jsonl|None, is_subagent, why)
     locates this session's top-level transcript and decides main-vs-subagent from the
     TRANSCRIPT PATH, not from an env var.

     IMPORTANT (build-time finding, 2026-07-04): CLAUDE_CODE_CHILD_SESSION is NOT a
     reliable "am I a subagent" flag -- in the claude-desktop / local-agent-mode
     entrypoint it is set to "1" even in the genuine top-level interactive session.
     Using it as a hard bail would break the main path. So the reliable discriminator
     is: the MAIN session's transcript is a TOP-LEVEL <session_id>.jsonl in a project
     dir; a spawned agent's transcript lives under a `subagents/` path segment.

  is_top_level_session() -> (ok:bool, why:str)
     convenience guard for callers: True when we resolved a top-level main transcript.

CLI
  python3 session_attribution.py            # human summary of this session's owned SHAs
  python3 session_attribution.py --json     # {shas, unattributed, notes, session_id, main}

SAFETY NOTE
  Even if a caller's guard were bypassed, the writes it gates stay safe: they are
  idempotent on work_log.source_ref (ON CONFLICT DO NOTHING) and scoped to SHAs proven
  present in THIS transcript's gitOperation. The guard is defence-in-depth, not the
  sole safety.
"""
import os, sys, json, glob, time

PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")

# Streaming budget so a 100 MB+ transcript can't hang a close routine.
_MAX_BYTES_PER_FILE = 400 * 1024 * 1024   # 400 MB hard ceiling per file
_MAX_SECONDS = 25                          # wall-clock budget across all files


def _sid():
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or ""


def resolve_transcript():
    """Return (session_id, main_jsonl_path|None, is_subagent, why).

    main_jsonl_path is the TOP-LEVEL <sid>.jsonl (never one under subagents/).
    is_subagent is True only when we can positively see we're running under a
    subagents/ path -- the path is the reliable signal, not CLAUDE_CODE_CHILD_SESSION
    (which is set even in the claude-desktop main session)."""
    sid = _sid()
    if not sid:
        return (sid, None, False, "no CLAUDE_CODE_SESSION_ID in env")
    # All transcripts named for this session id, anywhere under the projects tree.
    hits = glob.glob(os.path.join(PROJECTS_ROOT, "*", f"{sid}.jsonl"))
    hits += glob.glob(os.path.join(PROJECTS_ROOT, "*", "subagents", "**", f"{sid}.jsonl"),
                      recursive=True)
    top = [h for h in hits if f"{os.sep}subagents{os.sep}" not in h]
    sub = [h for h in hits if f"{os.sep}subagents{os.sep}" in h]
    if top:
        # Prefer the largest top-level match (the live one) if several projects collide.
        top.sort(key=lambda p: os.path.getsize(p) if os.path.exists(p) else 0, reverse=True)
        return (sid, top[0], False, "resolved top-level main transcript")
    if sub:
        return (sid, None, True, "transcript resolves under subagents/ -- this is a sub-run")
    return (sid, None, False, f"no transcript file found for session {sid}")


def is_top_level_session():
    sid, main, is_sub, why = resolve_transcript()
    if is_sub:
        return (False, why)
    if not main:
        return (False, why)
    return (True, why)


def _sibling_subagent_files(main_jsonl, sid):
    """Subagent + workflow transcripts that belong to THIS session.

    They live beside the main transcript under subagents/. The dir is shared across the
    project's sessions, so we only claim a file whose content references our session id
    as parent -- otherwise we'd inherit another session's sub-run commits. Files we
    cannot confirm are returned separately so the caller can surface them (never silently
    claim OR silently drop)."""
    proj = os.path.dirname(main_jsonl)
    subdir = os.path.join(proj, "subagents")
    cand = []
    if os.path.isdir(subdir):
        cand += glob.glob(os.path.join(subdir, "agent-*.jsonl"))
        cand += glob.glob(os.path.join(subdir, "workflows", "wf_*", "agent-*.jsonl"))
        cand += glob.glob(os.path.join(subdir, "**", "*.jsonl"), recursive=True)
    cand = sorted(set(os.path.realpath(c) for c in cand))
    mine, unknown = [], []
    for c in cand:
        claimed = False
        try:
            with open(c, "r", encoding="utf-8", errors="replace") as fh:
                for _ in range(80):                 # a child's session linkage is in its first records
                    line = fh.readline()
                    if not line:
                        break
                    if not sid or sid not in line:  # cheap pre-filter before json.loads
                        continue
                    try:
                        o = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    # Claim ONLY on a STRUCTURED session-linkage field equal to sid -- never a
                    # bare substring. A foreign transcript that merely mentions our sid as data
                    # (in a message, a path, a prompt) must NOT be claimed. In real subagent
                    # transcripts the top-level `sessionId` equals the PARENT session id (the
                    # child's own identity lives in `agentId`), so sessionId==sid is the tie.
                    if any(str(o.get(k)) == sid for k in ("sessionId", "parentSessionId", "parentUuid")):
                        claimed = True
                        break
        except OSError:
            unknown.append(c); continue
        (mine if claimed else unknown).append(c)
    return mine, unknown


def _shas_from_file(path, deadline):
    """Stream one transcript, pulling gitOperation.commit.sha only. The `"gitOperation"
    not in line` fast-path means only the handful of commit lines ever get json.loaded,
    so even a 100 MB+ file is cheap. Returns (shas, ok, note, unparsed) -- `unparsed`
    counts lines that CARRIED the gitOperation token but would not JSON-parse (a truncated
    or corrupt commit record). The caller surfaces that count; it is never dropped."""
    shas = set()
    unparsed = 0
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return (shas, False, f"cannot stat {os.path.basename(path)}: {e}", unparsed)
    if size > _MAX_BYTES_PER_FILE:
        return (shas, False, f"{os.path.basename(path)} is {size//1048576} MB (> budget) -- not fully scanned", unparsed)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if time.time() > deadline:
                    return (shas, False, f"time budget hit while scanning {os.path.basename(path)}", unparsed)
                if "gitOperation" not in line:
                    continue
                try:
                    o = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    unparsed += 1          # a gitOperation line we couldn't read -- surface, don't drop
                    continue
                tur = o.get("toolUseResult")
                if not isinstance(tur, dict):
                    continue
                go = tur.get("gitOperation")
                if not isinstance(go, dict):
                    continue
                c = go.get("commit")
                if isinstance(c, dict) and c.get("sha"):
                    shas.add(str(c["sha"]))
    except OSError as e:
        return (shas, False, f"read error on {os.path.basename(path)}: {e}", unparsed)
    return (shas, True, "", unparsed)


def owned_commit_shas():
    """(shas:set[str], unattributed:int, notes:list[str]).

    shas = commit SHAs THIS session created (from gitOperation.commit.sha). notes carries
    every reason coverage was incomplete; unattributed counts anything we could not confirm
    or parse (sub-run transcripts we can't tie to us + unparseable gitOperation lines).
    Callers MUST surface notes/unattributed -- no silent caps."""
    sid, main, is_sub, why = resolve_transcript()
    notes = []
    if is_sub:
        notes.append("SUB-RUN: " + why + " -- closeout/record is a main-session action; not attributing here.")
        return (set(), 0, notes)
    if not main:
        notes.append("NO TRANSCRIPT: " + why + " -- cannot prove commit ownership; nothing auto-recorded.")
        return (set(), 0, notes)

    deadline = time.time() + _MAX_SECONDS
    shas = set()
    unattributed = 0
    s, ok, note, unp = _shas_from_file(main, deadline)
    shas |= s
    if not ok and note:
        notes.append("MAIN TRANSCRIPT: " + note)
    if unp:
        unattributed += unp
        notes.append(f"MAIN TRANSCRIPT: {unp} gitOperation line(s) were unparseable (truncated/corrupt) "
                     "-- surfaced as unattributed, NOT silently dropped.")

    mine_subs, unknown_subs = _sibling_subagent_files(main, sid)
    for f in mine_subs:
        s, ok, note, unp = _shas_from_file(f, deadline)
        shas |= s
        if not ok and note:
            notes.append("SUBAGENT: " + note)
        if unp:
            unattributed += unp
            notes.append(f"SUBAGENT {os.path.basename(f)}: {unp} unparseable gitOperation line(s) "
                         "-- surfaced as unattributed, not dropped.")
    if unknown_subs:
        unattributed += len(unknown_subs)
        notes.append(f"{len(unknown_subs)} sub-run transcript(s) in this project could NOT be structurally "
                     "confirmed as this session's (sessionId did not match) -- their commits, if any, are "
                     "NOT claimed. Surface, don't assume.")
    return (shas, unattributed, notes)


def owns(full_sha, owned):
    """A repo's full SHA belongs to this session if any owned token is a prefix of it
    (gitOperation SHAs may be abbreviated to 7 chars; repo SHAs are full 40)."""
    f = (full_sha or "").lower()
    if not f.strip():          # an empty/None SHA must never be claimed by any token
        return False
    for t in owned:
        t = (t or "").lower()
        if t and (f.startswith(t) or t.startswith(f)):
            return True
    return False


def _main():
    as_json = "--json" in sys.argv
    sid, main, is_sub, why = resolve_transcript()
    shas, unattributed, notes = owned_commit_shas()
    if as_json:
        print(json.dumps({
            "session_id": sid, "main_transcript": main, "is_subagent": is_sub,
            "resolve_why": why, "owned_shas": sorted(shas),
            "unattributed": unattributed, "notes": notes,
        }, indent=2))
        return
    print(f"session:  {sid}")
    print(f"main:     {main or '(none)'}  [{why}]")
    print(f"subagent: {is_sub}")
    print(f"owned commit SHAs ({len(shas)}): {', '.join(sorted(shas)) or '(none)'}")
    if unattributed:
        print(f"unattributed sub-runs: {unattributed}")
    for n in notes:
        print(f"  ! {n}")


if __name__ == "__main__":
    _main()
