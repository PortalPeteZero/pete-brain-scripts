#!/usr/bin/env python3
"""engine-manifest.py -- load the engine session-boot pack + acknowledge the contract gate.

THE point of this tool (born 10 Jul 2026, the Triage Engine's first live run): a session
guessed its own engine APIs from memory -- wrong helper name, wrong function, wrong payload
shape, on tools written the same day -- while the manifests built precisely to prevent that
sat unread. Pete: "don't make a memory, fix the process." So the process is now mechanical:
`engine-contract-gate.py` (a PreToolUse hook) BLOCKS execution of any engine tool until this
loader has run in the current session window.

  VAULT=/tmp/pbs python3 /tmp/pbs/engine-manifest.py --ack     # print pack + write the marker
  VAULT=/tmp/pbs python3 /tmp/pbs/engine-manifest.py           # status (marker age, gate state)

--ack prints: both manifests (triage-manifest + ee-manifest, from vault_notes) and the
usage-docstring HEAD of every engine tool on disk, then writes the marker the gate checks
(`/tmp/.engine-contract-ack`, fresh for 6 hours). Reading the pack IS the acknowledgement.
"""
import os, sys, time, glob

VAULT = os.environ.get("VAULT", "/tmp/pbs")
sys.path.insert(0, VAULT)
MARKER = "/tmp/.engine-contract-ack"
FRESH_SECS = 6 * 3600
TOOL_GLOBS = ["triage-*.py", "ee-*.py", "te-log.py"]


def marker_age():
    try:
        return time.time() - os.path.getmtime(MARKER)
    except OSError:
        return None


def status():
    age = marker_age()
    if age is None:
        print("engine-contract gate: LOCKED (no ack this session window)")
        print("run: VAULT=/tmp/pbs python3 /tmp/pbs/engine-manifest.py --ack")
        return 1
    if age > FRESH_SECS:
        print(f"engine-contract gate: STALE ack ({age/3600:.1f}h old > 6h)")
        print("run: VAULT=/tmp/pbs python3 /tmp/pbs/engine-manifest.py --ack")
        return 1
    print(f"engine-contract gate: OPEN (ack {age/60:.0f}m ago; fresh for {(FRESH_SECS-age)/3600:.1f}h more)")
    return 0


def ack():
    import importlib
    tl = importlib.import_module("triage_lib")
    print("=" * 78)
    print("ENGINE SESSION-BOOT PACK -- read this, then call tools. Contracts, not memory.")
    print("=" * 78)
    for slug in ("triage-manifest", "ee-manifest"):
        rows = tl.cc_sql(f"SELECT body FROM vault_notes WHERE slug='{slug}'")
        if rows:
            print(f"\n{'-'*30} [[{slug}]] {'-'*30}\n")
            print(rows[0]["body"])
        else:
            print(f"\n[warn] manifest note '{slug}' not found in vault_notes")
    print("\n" + "=" * 78)
    print("TOOL CONTRACTS (usage docstring head of every engine tool on disk)")
    print("=" * 78)
    for pat in TOOL_GLOBS:
        for path in sorted(glob.glob(os.path.join(VAULT, pat))):
            name = os.path.basename(path)
            try:
                head = open(path).read(4000)
                doc = head.split('"""')[1] if '"""' in head else "(no docstring)"
            except Exception as e:
                doc = f"(unreadable: {e})"
            print(f"\n### {name}\n{doc.strip()[:1200]}")
    with open(MARKER, "w") as f:
        f.write(str(time.time()))
    print("\n" + "=" * 78)
    print("ACK WRITTEN -- the engine-contract gate is open for 6h. Call tools per the")
    print("contracts above; do NOT call from memory.")
    return 0


if __name__ == "__main__":
    sys.exit(ack() if "--ack" in sys.argv else status())
