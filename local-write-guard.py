#!/usr/bin/env python3
"""
local-write-guard.py — Phase 1 of the nothing-to-local guardrails (plan
plan-nothing-to-local-guardrails-2026-07-06). A Claude Code **PreToolUse** hook that stops the
model persisting KNOWLEDGE / data to permanent LOCAL disk, at the point of action, across EVERY
file-writing tool — the durable fix for failure-class F1.

Two tiers (plan Phase 1):

  Tier 1 — the memory dir (`~/.claude/**/memory/**`) is CONDUCT-ONLY.
     BLOCK knowledge-shaped writes there: `project_*`, `reference_*`, credential/store-shaped
     content, and FACT-shaped `user_*` (health / service / hardware facts).
     PASS conduct/identity: `MEMORY.md`, `feedback_*`, and conduct-shaped `user_*` (preferences /
     how-to-work). The `user_*` split is by CONTENT-shape, not prefix.

  Tier 2 — outside the memory dir, default-block outside /tmp.
     SILENT pass: `/tmp/**`, `/private/tmp/**` (+ macOS temp + $TMPDIR), the ONE permanent CC key,
     and the Google Drive mount roots (Drive is a cloud home; vault-writer writes there).
     PASS-with-reminder: the auto-loaded CONFIG files (kernel CLAUDE.md, both settings.json,
     `~/.config/pete-cc/*.cache.md`, keybindings) and `~/Downloads` / `~/Desktop`.
     BLOCK everything else with "route it to a cloud home".

Coverage: `Write`, `Edit`, `NotebookEdit`; Desktop-Commander `write_file` / `edit_block` /
`create_directory` / `move_file` / `write_pdf`; PowerPoint `export_pdf` / `save_presentation`;
and `Bash` (heuristic parse of `>` `>>` `tee` `cp` `mv` `install` `python -c open(...,'w')`). The
REPL vector (DC `start_process` / `interact_with_process`) is scanned the same heuristic way.

HONESTY NOTES (documented, not hidden):
  • This governs the MODEL'S tool calls only — not subprocesses a Bash line spawns, not crons, not
    the boot kernel. A shell `python3 - <<'PY'` heredoc that writes a file is NOT reliably caught.
  • Bash/REPL parsing is heuristic. To avoid bricking the session it is deliberately CONSERVATIVE:
    it only BLOCKS when it can extract a concrete write-target path that resolves to a blocked
    location; anything ambiguous is ALLOWED (surfaced-not-guaranteed).
  • The computer-use GUI "Save As" path cannot be inspected here — surfaced-not-guaranteed.
  • FAIL-OPEN on any internal error: a guard bug must never block legitimate work.

Exit contract (Claude Code PreToolUse): exit 2 + stderr message ⇒ BLOCK the tool call; exit 0 ⇒
allow (a reminder is printed to stderr but does not block).
"""
import sys, json, os, re, glob

HOME = os.path.expanduser("~")


def _abs(p):
    """Best-effort absolute, symlink-resolved path. Never raises."""
    try:
        return os.path.realpath(os.path.expanduser(os.path.expandvars(p)))
    except Exception:
        try:
            return os.path.abspath(os.path.expanduser(p))
        except Exception:
            return p or ""


# ---- location predicates -----------------------------------------------------------------------

def _is_memory_dir(path):
    """True for a path inside any `.claude/**/memory/**` tree (conduct-only home)."""
    parts = path.split(os.sep)
    return ".claude" in parts and "memory" in parts and parts.index("memory") > parts.index(".claude")


# Temp roots — silent pass (ephemeral work is fine anywhere under here).
_TMP_ROOTS = [
    "/tmp", "/private/tmp", "/private/var/folders", "/var/folders",
    _abs(os.environ.get("TMPDIR", "")) if os.environ.get("TMPDIR") else None,
]
_TMP_ROOTS = [r for r in _TMP_ROOTS if r]

# The ONE permanent local key — silent pass.
_CC_KEY = _abs("~/.config/pete-secrets/command-centre-supabase-keys.json")

# Google Drive mount roots — cloud home, silent pass.
_DRIVE_GLOBS = [
    _abs("~/Library/CloudStorage/GoogleDrive-*"),
    _abs("~/My Drive"),
    _abs("~/Google Drive"),
]

# Auto-loaded CONFIG files — pass WITH a reminder (editing them is legitimate but noteworthy).
_CONFIG_FILES = {
    _abs("~/Command Centre/CLAUDE.md"),
    _abs("~/.claude/settings.json"),
    _abs("~/.claude/settings.local.json"),
    _abs("~/Command Centre/.claude/settings.json"),
    _abs("~/Command Centre/.claude/settings.local.json"),
    _abs("~/.claude/keybindings.json"),
}
_CONFIG_DIR_GLOBS = [_abs("~/.config/pete-cc/*.cache.md")]

# Downloads / Desktop — pass with a light reminder.
_REMIND_ROOTS = [_abs("~/Downloads"), _abs("~/Desktop")]


def _under(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _matches_any_glob(path, patterns):
    for pat in patterns:
        # match the pattern itself OR anything under a directory it expands to
        for base in glob.glob(pat) or [pat]:
            if _under(path, base):
                return True
        # also treat the glob's literal head as a prefix (dir may not exist yet, and the `*` can fall
        # mid-segment as in GoogleDrive-<email>) — plain startswith, no separator required
        prefix = pat.split("*")[0]
        if prefix and path.startswith(prefix):
            return True
    return False


# ---- content-shape classifiers (Tier 1) --------------------------------------------------------

_CRED_RE = re.compile(
    r"(sk-[A-Za-z0-9]{12}|ghp_[A-Za-z0-9]{20}|sbp_[A-Za-z0-9]{15}|vcp_[A-Za-z0-9]{15}"
    r"|AKIA[0-9A-Z]{12}|-----BEGIN |xox[baprs]-|eyJ[A-Za-z0-9_-]{15})")
_CRED_KV_RE = re.compile(r"(?i)(api[_-]?key|secret|service_role|token|password|passcode|bearer)\s*[:=]\s*\S{10,}")

# conduct = how Pete likes me to work / how to address him / tone-style-voice preferences
_CONDUCT_RE = re.compile(
    r"(?i)\b(prefer|prefers|preferred|likes?|wants?|dislikes?|always|never|avoid|"
    r"call me|address me|tone|style|voice|how (i|pete) (like|work)|when .+ (then|do)|"
    r"don'?t|do not)\b")
# fact = health / service / hardware / credential-adjacent knowledge (belongs in vault_notes)
_FACT_RE = re.compile(
    r"(?i)\b(garmin|hrm|whoop|device|serial|firmware|sensor|heart.?rate|hrv|vo2|"
    r"endpoint|api|host|port|ref|supabase|railway|vercel|dosage|mg\b|units|"
    r"blood|glucose|metric|readings?)\b")


def _tier1_decision(base, content):
    """Return ('block'|'remind'|'allow', reason) for a write INTO the memory dir."""
    c = content or ""
    # credential-shaped content is always knowledge/secret — never a conduct note
    if _CRED_RE.search(c) or _CRED_KV_RE.search(c):
        return ("block", "credential/secret-shaped content in the conduct-memory dir")
    if base == "MEMORY.md" or base.startswith("feedback_"):
        return ("allow", "")
    if base.startswith("project_") or base.startswith("reference_"):
        return ("block", f"'{base}' is KNOWLEDGE (project_/reference_), not conduct")
    if base.startswith("user_"):
        is_conduct = bool(_CONDUCT_RE.search(c))
        is_fact = bool(_FACT_RE.search(c))
        if is_fact and not is_conduct:
            return ("block", f"'{base}' reads as a FACT (health/service/hardware), which is knowledge")
        if is_conduct:
            return ("allow", "")
        # ambiguous user_ note → allow but flag
        return ("remind", f"'{base}': if this is a fact (health/service/hardware) it belongs in vault_notes, "
                          "not conduct memory")
    # any other shape landing in the memory dir → treat as knowledge, flag it
    return ("remind", f"'{base}' is an unusual file for the conduct-memory dir — knowledge belongs in the CC")


def _tier2_decision(path):
    """Return ('block'|'remind'|'allow', reason) for a write OUTSIDE the memory dir."""
    if any(_under(path, r) for r in _TMP_ROOTS):
        return ("allow", "")
    if path == _CC_KEY:
        return ("allow", "")
    if _matches_any_glob(path, _DRIVE_GLOBS):
        return ("allow", "")
    if path in _CONFIG_FILES or _matches_any_glob(path, _CONFIG_DIR_GLOBS):
        return ("remind", "editing an auto-loaded config file — remember the real rules live in the CC "
                          "(config.claude-md), not this file")
    if any(_under(path, r) for r in _REMIND_ROOTS):
        return ("remind", "writing to Downloads/Desktop — fine for a transient artefact or a .skill to "
                          "reinstall, but durable data/knowledge belongs in a cloud home")
    return ("block", "writing a permanent file to local disk — route it to a cloud home "
                     "(knowledge → vault_notes, files → Drive, tasks → public.tasks, code → pete-brain-scripts)")


# ---- per-tool target extraction ----------------------------------------------------------------

# (path, content) candidate extraction. content may be None when unknown.
_PATH_KEYS = ("file_path", "path", "notebook_path", "output_path", "output", "target", "destination")
_CONTENT_KEYS = ("content", "new_string", "text", "data")


def _candidates_from_tool(tool_name, ti):
    """Yield (path, content) pairs a write-tool call would touch."""
    out = []
    content = ""
    for k in _CONTENT_KEYS:
        if isinstance(ti.get(k), str) and ti.get(k):
            content = ti[k]
            break

    # command-based tools FIRST (start_process/interact_with_process also start with the DC prefix, so
    # they must be matched here before the DC path-key branch below, or their command string is missed)
    if tool_name == "Bash" or tool_name in (
            "mcp__Desktop_Commander__start_process", "mcp__Desktop_Commander__interact_with_process"):
        cmd = ti.get("command") or ti.get("input") or ""
        if isinstance(cmd, str):
            out.extend((p, None) for p in _write_targets_from_shell(cmd))
        return out

    if tool_name in ("Write", "Edit", "NotebookEdit") or tool_name.startswith("mcp__Desktop_Commander__") \
            or tool_name.startswith("mcp__PowerPoint"):
        for k in _PATH_KEYS:
            v = ti.get(k)
            if isinstance(v, str) and v.strip():
                out.append((v, content))
        return out

    # unknown tool that still carries a path key — be safe, inspect it
    for k in _PATH_KEYS:
        v = ti.get(k)
        if isinstance(v, str) and v.strip():
            out.append((v, content))
    return out


# heuristic shell write-target extraction (conservative — misses are allowed, plan-documented).
# CONSERVATISM RULE: a false positive (blocking a legit command) is far worse than a miss, so a
# candidate is only kept when it is unmistakably a FILESYSTEM PATH (has a separator / ~ / ./ , or is
# a bare filename WITH an extension). This drops SQL comparisons (`WHERE ts > '2026-07-05'`), numeric
# tests (`[ $x > 5 ]`), and package args (`pip install foo bar`) — none of which are path-like.
_REDIR_RE = re.compile(r"(?<![0-9&])>>?\s*([^\s;|&><]+)")          # > file / >> file (not 2>&1, >&2)
_TEE_RE = re.compile(r"\btee\s+(?:-a\s+)?([^\s;|&><]+)")
_CPMV_RE = re.compile(r"\b(?:cp|mv)\s+(?:-[A-Za-z]+\s+)*.*?\s([^\s;|&><]+)\s*(?:;|\||&|$)")  # NOT install/rsync
_PYOPEN_RE = re.compile(r"""open\(\s*['"]([^'"]+)['"]\s*,\s*['"][aw]""")
_IGNORE_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr", "&1", "&2"}
_PATHLIKE_RE = re.compile(r"^(?:~|\.\.?/|/)|/|^[\w][\w .-]*\.[A-Za-z0-9]{1,6}$")


def _pathlike(t):
    """True only when t is unmistakably a filesystem path (see CONSERVATISM RULE)."""
    return bool(_PATHLIKE_RE.search(t))


def _write_targets_from_shell(cmd):
    targets = []
    for rx in (_REDIR_RE, _TEE_RE, _CPMV_RE, _PYOPEN_RE):
        for m in rx.finditer(cmd):
            t = m.group(1).strip().strip('"\'')
            # unexpanded shell var / command-substitution ($VAR, ${VAR}, $(...), `...`) — the guard
            # can't know where it resolves, so per the CONSERVATISM RULE it must ALLOW, not block.
            if "$" in t or "`" in t:
                continue
            if t and t not in _IGNORE_TARGETS and not t.startswith("/dev/") and _pathlike(t):
                targets.append(t)
    return targets


# ---- decision engine ---------------------------------------------------------------------------

def classify(tool_name, tool_input):
    """Return a list of (action, path, reason). action ∈ {block, remind, allow}."""
    decisions = []
    for raw_path, content in _candidates_from_tool(tool_name, tool_input or {}):
        path = _abs(raw_path)
        if not path:
            continue
        if _is_memory_dir(path):
            act, why = _tier1_decision(os.path.basename(path), content if content is not None else "")
        else:
            act, why = _tier2_decision(path)
        decisions.append((act, path, why))
    return decisions


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # can't parse → fail open
    try:
        tool_name = payload.get("tool_name") or payload.get("tool") or ""
        tool_input = payload.get("tool_input") or {}
        decisions = classify(tool_name, tool_input)
    except Exception:
        return 0  # guard bug → fail open, never brick the session

    blocks = [(p, why) for a, p, why in decisions if a == "block"]
    reminds = [(p, why) for a, p, why in decisions if a == "remind"]

    if blocks:
        msg = ["BLOCKED by local-write-guard — nothing permanent to local disk (F1):"]
        for p, why in blocks:
            msg.append(f"  • {p}\n    ↳ {why}")
        msg.append("If this really is ephemeral, write it under /tmp. Conduct memory "
                   "(feedback_/MEMORY.md/conduct user_) is the only local knowledge allowed.")
        sys.stderr.write("\n".join(msg) + "\n")
        return 2

    if reminds:
        for p, why in reminds:
            sys.stderr.write(f"[local-write-guard note] {p}\n    ↳ {why}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
