#!/usr/bin/env python3
"""
capability-probe-guard.py — a Claude Code **PreToolUse** hook (Bash) that stops me
answering a CAPABILITY question ("can I do X / do I have access to Y / is the CLI
available") from a LOCAL BINARY PROBE instead of the Command Centre's own locator.

WHY THIS EXISTS (the failure it fixes, 20 Jul 2026):
  Pete asked me to deploy a Supabase edge function. I ran `which supabase`, saw
  nothing useful, and told him it "wasn't available here" — leaving it a closeout
  open item. It WAS available: the account token + `npx supabase` were right there,
  and the CC locator (`whereis.py` / the connections capability registry) says so
  plainly. The moment I actually ran `whereis "supabase"` it answered immediately.
  Root cause: I treated a capability question as a local-environment question and
  checked a MIRROR (`which`, the per-project keys file) instead of the SOURCE OF
  TRUTH (the locator). Pete: a reminder/memory is not a fix — it must be mechanical.

WHAT IT DOES:
  When a Bash call is a BARE capability probe — the whole command is essentially
  `which X`, `command -v X`, or `type X` — this guard BLOCKS it (exit 2) and hands
  back what the locator actually says about X, plus the standing rule. So I can
  never again read a blank `which` as "no capability": the misleading result is
  replaced, at the point of action, by the authoritative answer.

  A bare probe is the exact signature of the mistake. `which`/`command -v` buried
  inside a larger script line (a conditional, a var assignment) is real logic, not a
  capability judgement, so it is LEFT ALONE — this keeps false positives near zero.

HONESTY NOTES (documented, not hidden):
  • Covers the model's own Bash `which/command -v/type` probes only. It does NOT
    catch a probe run inside a Desktop-Commander REPL, a heredoc, or a subprocess,
    and it does not read my mind if I skip the probe and just assert from memory —
    for that, the standing SSOT-FIRST / WHERE-IS-FIRST UserPromptSubmit guard still
    carries the rule every turn. This closes the specific hole that one walked past.
  • `--version` is deliberately NOT treated as a probe: legitimate version/compat
    checks use it constantly, and the failure was `which`, not `--version`.
  • FAIL-OPEN on any internal error: a guard bug must never block real work.

Exit contract (Claude Code PreToolUse): exit 2 + stderr ⇒ BLOCK the tool call and
feed stderr back to the model; exit 0 ⇒ allow.
"""
import sys, os, re, json, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
WHEREIS = os.path.join(VAULT, "whereis.py")

# The whole command is nothing but a binary-existence probe. Anchored ^...$ so a
# `which` inside a bigger pipeline / conditional / assignment is NOT matched.
_PROBE = re.compile(
    r"""^\s*
        (?:which|type)\s+(?:-\w+\s+)*(?P<a>[A-Za-z0-9_./+-]+)   # which X / type -p X
      |
        ^\s*command\s+-[vV]\s+(?P<b>[A-Za-z0-9_./+-]+)          # command -v X
    \s*$""",
    re.VERBOSE,
)


def _probed_tool(command: str):
    """Return the tool name iff `command` is a BARE capability probe, else None."""
    if not command or "\n" in command.strip():
        return None  # multi-line ⇒ a script, not a bare probe
    m = _PROBE.match(command)
    if not m:
        return None
    return m.group("a") or m.group("b")


def _locator_says(tool: str) -> str:
    """Best-effort: what the CC locator records for `tool`. Never raises/hangs."""
    if not os.path.exists(WHEREIS):
        return "(locator not materialised — run the boot kernel, then `whereis`)"
    try:
        r = subprocess.run(
            [sys.executable, WHEREIS, tool],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "VAULT": VAULT},
        )
        out = (r.stdout or "").strip()
        if not out:
            return "(locator returned nothing for this term — try a broader term)"
        # trim to keep the injected block readable
        lines = [ln for ln in out.splitlines() if ln.strip()]
        return "\n".join(lines[:38])
    except Exception:
        return "(locator lookup did not complete — run `whereis` yourself)"


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # unreadable payload ⇒ fail open

    if data.get("tool_name") not in (None, "", "Bash"):
        return 0
    command = (data.get("tool_input") or {}).get("command", "") or ""

    try:
        tool = _probed_tool(command)
    except Exception:
        return 0  # regex bug ⇒ fail open, never brick the session
    if not tool:
        return 0  # not a bare probe ⇒ allow silently

    answer = _locator_says(tool)
    sys.stderr.write(
        "⛔ CAPABILITY PROBE BLOCKED — you are checking the LOCAL machine to decide "
        f"whether a capability exists (`{command.strip()}`).\n\n"
        "A missing local binary does NOT mean the capability is absent. 'Can I do X / "
        "do I have access to Y / is the CLI available' is a CAPABILITY question, and "
        "the ONLY authority on it is the Command Centre locator — `whereis.py` + the "
        "connections capability registry in [[connections]] — never a local poke. "
        "This is the exact check that made you tell Pete a Supabase edge-function "
        "deploy 'wasn't available' when it was.\n\n"
        f"What the locator records for '{tool}':\n{answer}\n\n"
        f"Read that, and for the full record run:  VAULT={VAULT} python3 {WHEREIS} \"{tool}\"\n"
        "Only conclude a capability is absent AFTER the locator says so."
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
