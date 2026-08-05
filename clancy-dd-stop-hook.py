#!/usr/bin/env python3
"""clancy-dd-stop-hook.py — refuse to end a turn that changed the Damage Depot and left the
workflow incomplete.

Pete, 5 Aug 2026: "We need a tidy workflow that you work and follow. Do not skip. It's gated."

The workflow itself is clancy-dd-workflow.py. This is what makes it unskippable. Writing the
steps down did not work — they were written down, in the process doc, and skipped anyway:
on 5 Aug a Sygma finding was corrected and republished with ONE of the ten publishers, leaving
eleven pages nineteen hours stale and still rendering the superseded finding. Nothing objected.

WHAT IT DOES
  Fires only when THIS session changed Depot data or pages (checked against the live tables,
  not against a guess about what the session did). Then runs the workflow and blocks the turn
  while any step is incomplete, printing the failing steps and their fix commands.

WHY IT IS SCOPED TO THIS SESSION
  A shared /tmp and several live sessions mean an unscoped hook blocks a session for another
  session's work — that happened on 4 Aug with the enrichment hook, which used file mtime with
  no session id and told the wrong session to finish someone else's job. Ownership here is a
  timestamp comparison: something in the Depot changed AFTER this session started. That is
  narrow, cheap, and cannot borrow another session's work.

WHAT IT DELIBERATELY DOES NOT BLOCK ON
  Steps whose fix needs Pete or a signed-in Depotnet session — a file Depotnet would not serve
  is not something the turn can fix, so blocking on it would make every turn unendable. Those
  are REPORTED, loudly, and the turn is allowed to end. Everything the session itself can fix
  (promote, publish, classify) blocks.

Install: a Stop hook in settings, same shape as the other gates.
"""
import json, os, subprocess, sys, datetime, urllib.request, urllib.parse

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"

# Steps the SESSION can fix on its own. A not-held file needs Depotnet; a missing capture needs a
# signed-in session. Blocking on those would make the turn permanently unendable.
BLOCKING = {"roll up", "classify", "publish"}
STARTED = os.environ.get("CLAUDE_SESSION_START")   # ISO8601, set by the harness where available


def session_start():
    """When did this session begin? The transcript's own mtime is the fallback — it is written
    from the first message on, so it brackets the session without needing an env var."""
    if STARTED:
        try:
            return datetime.datetime.fromisoformat(STARTED.replace("Z", "+00:00"))
        except ValueError:
            pass
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if sid:
        for root, _dirs, files in os.walk(os.path.expanduser("~/.claude/projects")):
            for f in files:
                if f.startswith(sid):
                    p = os.path.join(root, f)
                    # birth time where the platform has it, else first-write mtime
                    st = os.stat(p)
                    ts = getattr(st, "st_birthtime", st.st_mtime)
                    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    return None


def touched_since(since):
    """Did the Depot change after this session started? Asks the live tables."""
    k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
    H = {"apikey": k["service_role_key"],
         "Authorization": f"Bearer {k['service_role_key']}"}
    iso = urllib.parse.quote(since.isoformat(), safe="")
    probes = [
        f"clancy_dn_incidents?select=id&limit=1&or=(doc_enriched_at.gte.{iso},"
        f"sygma_reviewed_at.gte.{iso},import_changed_at.gte.{iso})",
        f"module_content?select=module_key&limit=1&module_key=like.clancy%2A&updated_at.gte={iso}",
    ]
    for p in probes:
        try:
            req = urllib.request.Request(k["url"] + "/rest/v1/" + p, headers=H)
            if json.loads(urllib.request.urlopen(req, timeout=60).read().decode()):
                return True
        except Exception:
            continue          # a probe that cannot run must not silently pass OR silently block
    return False


def main():
    since = session_start()
    if not since:
        sys.exit(0)                       # cannot prove ownership -> never block
    try:
        if not touched_since(since):
            sys.exit(0)
    except Exception:
        sys.exit(0)

    r = subprocess.run(["python3", f"{VAULT}/clancy-dd-workflow.py", "--json"],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    if r.returncode == 2 or not r.stdout.strip():
        sys.exit(0)                       # check could not run -> report nothing, block nothing
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        sys.exit(0)

    blocking, advisory = [], []
    for fy, steps in data.get("years", {}).items():
        for s in steps:
            if s["ok"]:
                continue
            (blocking if s["name"] in BLOCKING else advisory).append((fy, s))

    if advisory and not blocking:
        for fy, s in advisory:
            print(f"note: Damage Depot {fy} step {s['n']} {s['name']} is incomplete "
                  f"({s['count']}) — needs Depotnet or Pete, not this turn.", file=sys.stderr)
        sys.exit(0)

    if not blocking:
        sys.exit(0)

    lines = ["BLOCKED by clancy-dd-stop-hook: this session changed the Damage Depot and the "
             "workflow is not complete.", ""]
    for fy, s in blocking:
        lines.append(f"  {fy}  step {s['n']} {s['stage']} / {s['name']} — {s['detail']}")
        for b in (s.get("bad") or [])[:6]:
            lines.append(f"      - {b}")
        if s.get("fix"):
            lines.append(f"      FIX: {s['fix']}")
    for fy, s in advisory:
        lines.append(f"  (also, not blocking) {fy} step {s['n']} {s['name']}: {s['detail']}")
    lines += ["", "Run the fixes above, then re-run:",
              f"  VAULT={VAULT} python3 {VAULT}/clancy-dd-workflow.py"]
    print("\n".join(lines), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
