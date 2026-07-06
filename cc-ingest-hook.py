#!/usr/bin/env python3
"""PostToolUse hook (Write|Edit): auto-push any knowledge .md written under /tmp/pbs into the CC.

Why this exists (2 Jul 2026): authored knowledge kept sitting un-ingested in /tmp until Pete
challenged it — "sorry, I'll upload it" is not a fix, and a memory rule is only guidance.
This hook makes write+push atomic at the HARNESS level: the moment a knowledge file is written
or edited, the harness runs the ingest — no model discipline involved. A failed push comes back
as a blocking error the session cannot ignore.

Registered in "~/Command Centre/.claude/settings.json" (PostToolUse, matcher Write|Edit).
No-ops for anything outside /tmp/pbs, non-.md files, or an un-booted session.
"""
import json
import os
import subprocess
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    fp = (payload.get("tool_input") or {}).get("file_path") or ""
    if not fp:
        return 0
    fp = os.path.realpath(fp)
    root = os.path.realpath(os.environ.get("VAULT", "/tmp/pbs"))
    if not fp.startswith(root + os.sep) or not fp.endswith(".md"):
        return 0  # not a CC knowledge file — nothing to do
    ingest = os.path.join(root, "cc-knowledge-ingest.py")
    if not os.path.isfile(ingest) or not os.path.isfile(fp):
        return 0  # session not booted (or file already gone) — nothing we can do

    last = ""
    for attempt in range(3):  # ingest sees transient DNS/SSL blips; retry before alarming
        try:
            r = subprocess.run(
                ["python3", ingest, fp],
                cwd=root, env=dict(os.environ, VAULT=root),
                capture_output=True, text=True, timeout=60,
            )
            last = ((r.stdout or "") + (r.stderr or "")).strip()
            if r.returncode == 0 and ", 0 failed" in last:
                rel = os.path.relpath(fp, root)
                # F3: a lifecycle note (session-plan) is skipped by the BULK ingest but BELONGS in
                # vault_notes. cc-knowledge-ingest flags it with "PERSIST-ELIGIBLE:"; fall through to
                # cc-save.py so it actually persists. This NEVER fires for SKILL.md / scaffolding —
                # those skip with no marker — so edited skills still stay out of the KB.
                if "PERSIST-ELIGIBLE:" in last:
                    save = os.path.join(root, "cc-save.py")
                    s = subprocess.run(
                        ["python3", save, fp], cwd=root, env=dict(os.environ, VAULT=root),
                        capture_output=True, text=True, timeout=60,
                    )
                    if s.returncode == 0 and "SAVED:" in (s.stdout or ""):
                        print(f"CC auto-push OK: {rel} persisted via cc-save (lifecycle note).")
                        return 0
                    last = f"cc-save fall-through failed: {((s.stdout or '')+(s.stderr or '')).strip()[:300]}"
                    continue  # retry the whole loop; after 3, surface as a blocking failure below
                if "0 notes ingested" in last:
                    print(f"CC auto-push: {rel} skipped as ephemeral scaffolding (by design — not a vault doc).")
                else:
                    print(f"CC auto-push OK: {rel} is now in vault_notes.")
                return 0
        except Exception as e:  # timeout, missing python, etc.
            last = f"{type(e).__name__}: {e}"
    print(
        f"CC AUTO-PUSH FAILED for {fp} after 3 attempts — the CC copy is STALE. "
        f"Re-run: VAULT={root} python3 {ingest} '{fp}'  (last output: {last[:300]})",
        file=sys.stderr,
    )
    return 2  # blocking: surface the failure to the session so it must retry


if __name__ == "__main__":
    sys.exit(main())
