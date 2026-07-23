#!/usr/bin/env python3
"""engine-contract-gate.py -- PreToolUse hook: no engine tool runs until the session
has loaded the engine manifests (the session-boot pack).

Born 10 Jul 2026 (Triage Engine first live run): a session guessed its own engine APIs
from memory -- wrong helper name, wrong function, wrong payload shapes -- while the
manifests built to prevent exactly that went unread. Pete's instruction: "don't make a
memory, fix the process." This hook IS the process fix: it mechanically BLOCKS the
EXECUTION of any engine tool (triage-*.py, ee-*.py, te-log.py) until
`engine-manifest.py --ack` has run in the current session window (marker fresh < 6h).

Scope -- deliberately narrow:
  * Only EXECUTION blocks: a python invocation of an engine tool. Reading contracts
    (head/cat/grep/sed of the same files) always passes -- reading is the remedy.
  * engine-manifest.py itself always passes (it IS the unlock).
  * Everything non-engine passes untouched.
  * FAIL-OPEN on any internal error -- a guard bug must never brick the session.

Exit contract (PreToolUse): exit 2 + stderr => BLOCK; exit 0 => allow.
"""
import os, sys, json, re, time

# 23 Jul 2026: this was a single global file, so a PARALLEL session's --ack opened the gate for a
# session that had read nothing -- the gate reported a compliance it never checked. Keyed to the
# session now; engine-manifest.py --ack writes the same path.
_SID = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
MARKER = f"/tmp/.engine-contract-ack-{_SID}" if _SID else "/tmp/.engine-contract-ack"
FRESH_SECS = 6 * 3600

# an EXECUTION of an engine tool: a python interpreter token followed (same command) by an
# engine tool filename. head/cat/grep/ls of the file do not match (no python token).
_ENGINE_EXEC_RE = re.compile(
    r"python3?\s+(?:[^\n;|&]*?/)?"
    r"(triage-(?:pull|action-classify|log|lint|routing-test|sync|reconcile|selfaudit|signoff|health|learn|engine-run|validator|ops-table)"
    r"|ee-(?:facts|send|signoff|lint|reconcile|selfaudit|health|alias-test|public-dates|backfill|html|index-gen"
    r"|draft-gate|learn|payload)"
    r"|te-log)\.py\b")
# 23 Jul 2026: ee-draft-gate, ee-learn and triage-ops-table were absent from this list, so the
# whole draft-building path -- the part built on 21 Jul after the LAST Wheal Jane failure -- ran
# unguarded. ee-payload is added on creation rather than after it bites.
_UNLOCK_RE = re.compile(r"engine-manifest\.py")


def fresh():
    try:
        return (time.time() - os.path.getmtime(MARKER)) < FRESH_SECS
    except OSError:
        return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        tool = payload.get("tool_name") or payload.get("tool") or ""
        ti = payload.get("tool_input") or {}
        if tool != "Bash" and not tool.endswith(("start_process", "interact_with_process")):
            return 0
        cmd = ti.get("command") or ti.get("input") or ""
        if not isinstance(cmd, str):
            return 0
        if _UNLOCK_RE.search(cmd):
            return 0
        m = _ENGINE_EXEC_RE.search(cmd)
        if not m:
            return 0
        if fresh():
            return 0
        sys.stderr.write(
            "BLOCKED by engine-contract-gate: executing an engine tool (%s.py) before loading "
            "the session-boot pack.\nRun FIRST:  VAULT=/tmp/pbs python3 /tmp/pbs/engine-manifest.py --ack\n"
            "It prints both manifests + every tool's usage contract and opens the gate for 6h. "
            "Call tools per the printed contracts -- never from memory. (Pete, 10 Jul 2026: "
            "'don't make a memory, fix the process.')\n" % m.group(1))
        return 2
    except Exception:
        return 0  # fail-open


if __name__ == "__main__":
    sys.exit(main())
