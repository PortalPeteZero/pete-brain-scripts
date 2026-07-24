#!/usr/bin/env python3
"""
gate-report.py — publish the LOCAL hook wiring into the CC `gates` registry.

Step 0b of [[plan-rules-that-stop-me]]. The problem it closes: every hook-type gate is wired in a
settings.json on Pete's Mac, but `cc-locator-audit` runs on **Railway** and cannot read local disk.
So without this, the daily audit can never tell whether a registered gate is actually wired, and
"what stops me doing X?" is unanswerable from anywhere but this machine.

WHAT IT DOES
  1. Reads EVERY settings source (round-4 audit finding H3a: the inventory had been reading one file
     and missing a live blocking hook in another):
       ~/.claude/settings.json                    user settings
       ~/.claude/settings.local.json              user local overrides
       <cwd>/.claude/settings.json                PROJECT settings  ← the one that was missed
       <cwd>/.claude/settings.local.json          project local overrides
  2. Derives a stable key per hook — the script basename (`local-write-guard`), or for an inline
     hook with no script file, `inline:<event>:<sha1 of the command, 8 chars>`.
  3. Reconciles against `public.gates`: stamps `last_reported_at` on every gate it observed, and
     reports anything wired-but-unregistered or registered-but-not-wired.

WHAT IT DELIBERATELY DOES NOT DO
  * It does NOT auto-insert unknown hooks. A gate row carries judgement — what it refuses, its
    exceptions, its override path, which rule it replaces — and none of that can be derived from a
    shell command string. Unregistered hooks are REPORTED for a human to register properly.
  * It does not touch DB-object or lint-script gates: those are visible from the cloud already, and
    `is_called` for a lint script is a code fact, not a wiring fact.

STALENESS (the audit's rule): `last_reported_at` is how the cloud tells "not published this session"
from "gate removed". A gate whose `last_reported_at` is older than ~48h is stale, NOT proven-gone —
`cc-locator-audit` must say so in those words rather than implying the gate has been deleted.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/gate-report.py            # report + stamp
  VAULT=/tmp/pbs python3 /tmp/pbs/gate-report.py --dry-run  # report only, no writes

FAIL-OPEN: never raises into the boot path. A reporting failure must not stop a session starting.
"""
import os, sys, json, glob, hashlib, subprocess, re

VAULT = os.environ.get("VAULT", "/tmp/pbs")
HOME = os.path.expanduser("~")
DRY = "--dry-run" in sys.argv


def _sql(q):
    """Run a query through cc-sql.py. Returns [] on any failure (fail-open)."""
    try:
        r = subprocess.run([sys.executable, f"{VAULT}/cc-sql.py", q],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "VAULT": VAULT})
        return json.loads(r.stdout) if r.stdout.strip().startswith("[") else []
    except Exception:
        return []


def _q(s):
    return (s or "").replace("'", "''")


def settings_files():
    """Every settings source, in precedence order (later overrides earlier)."""
    cwd = os.getcwd()
    return [p for p in [
        f"{HOME}/.claude/settings.json",
        f"{HOME}/.claude/settings.local.json",
        f"{cwd}/.claude/settings.json",
        f"{cwd}/.claude/settings.local.json",
    ] if os.path.exists(p)]


def key_for(command, event):
    """Stable identifier for a hook.

    The script basename where the command actually EXECUTES one — matched on `python3 <path>.py`,
    not on a bare mention. That distinction matters: the SSOT-FIRST hook is an inline `printf` whose
    injected TEXT names `whereis.py`, and a looser match keyed it as the `whereis` gate, inventing a
    hook that does not exist. Anything with no executed script gets a digest of its own command.
    """
    cmd = (command or "").strip()
    # A text-injection hook (`printf`/`echo` of a JSON payload) is ALWAYS inline, even when the text
    # it injects quotes a script name. The SSOT-FIRST hook injects an instruction containing
    # "python3 /tmp/pbs/whereis.py" — matching on that invented a `whereis` gate that does not exist.
    if not re.match(r"^(printf|echo)\b", cmd):
        m = re.findall(r"python3?\s+\S*?([A-Za-z0-9_\-]+)\.py", cmd)
        if m:
            return m[-1]  # the `if [ -f X ]; then python3 X` wrapper names it twice — the run is last
    digest = hashlib.sha1(cmd.encode()).hexdigest()[:8]
    return f"inline:{event.lower()}:{digest}"


def observed():
    """{key: {event, source, command}} across every settings file."""
    seen = {}
    for path in settings_files():
        try:
            data = json.load(open(path))
        except Exception:
            continue
        for event, entries in (data.get("hooks") or {}).items():
            for entry in entries or []:
                for hook in entry.get("hooks") or []:
                    cmd = hook.get("command", "")
                    if not cmd:
                        continue
                    seen[key_for(cmd, event)] = {
                        "event": event,
                        "source": path.replace(HOME, "~"),
                        "command": cmd[:200],
                        "matcher": entry.get("matcher", ""),
                    }
    return seen


def main():
    obs = observed()
    rows = _sql("SELECT key, kind, status, wired_in FROM gates WHERE kind LIKE '%hook%'")
    registered = {r["key"]: r for r in rows}

    matched = sorted(set(obs) & set(registered))
    unregistered = sorted(set(obs) - set(registered))
    missing = sorted(set(registered) - set(obs))

    print(f"gate-report — {len(settings_files())} settings file(s), {len(obs)} hook(s) wired, "
          f"{len(registered)} hook-gate(s) registered")
    for k in matched:
        print(f"  ✅ {k:28} {obs[k]['event']:20} {obs[k]['source']}")
    for k in unregistered:
        print(f"  ⚠  UNREGISTERED: {k:20} {obs[k]['event']:20} {obs[k]['source']}")
        print(f"       → register it in public.gates (what it refuses, exceptions, override path, owner)")
    for k in missing:
        print(f"  ⚠  REGISTERED BUT NOT WIRED: {k}  (registered as: {registered[k].get('wired_in')})")
        print(f"       → either the gate was removed, or this session did not read its settings file")

    if matched and not DRY:
        keys = ",".join(f"'{_q(k)}'" for k in matched)
        _sql(f"UPDATE gates SET last_reported_at = now(), updated_at = now() WHERE key IN ({keys})")
        print(f"  stamped last_reported_at on {len(matched)} gate(s)")
    elif DRY:
        print("  (dry run — nothing written)")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"gate-report: {e} (fail-open — boot continues)", file=sys.stderr)
        sys.exit(0)
