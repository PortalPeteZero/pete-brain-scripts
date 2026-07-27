#!/usr/bin/env python3
"""lg-brief.py -- the LeakGuard session brief. Run it BEFORE touching anything LeakGuard.

WHY THIS EXISTS (27 Jul 2026, Pete's words: "every time we work on leakguard it's a disaster,
you never remember how it works or the current SOP").

He is right, and the cause is not memory. The front door, the multi-output SOP and the
verify-before-claiming SOP all exist and are all good. Nothing makes a session read them before it
starts, so every session rediscovers the same ground halfway through, after the time is spent.
That day alone:
  * the front door was opened only when code was about to be written, hours in;
  * the fix plan was reported from instead of the live systems, and the plan was wrong;
  * thingslog-api.py was called with a device number where a path goes, and the resulting DNS
    error was reported to Pete as ThingsLog being unreachable.

So this is the process fix, not a note. `leakguard-context-gate.py` BLOCKS every LeakGuard tool
until `lg-brief.py --ack` has run in this session.

  VAULT=/tmp/pbs python3 /tmp/pbs/lg-brief.py          # read it
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-brief.py --ack    # read it and unlock the tools
"""
import json, os, subprocess, sys, time

VAULT = os.environ.get("VAULT", "/tmp/pbs")
ENV = {**os.environ, "VAULT": VAULT}
_SID = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
MARKER = f"/tmp/.leakguard-brief-ack-{_SID}" if _SID else "/tmp/.leakguard-brief-ack"


def cc(query):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", query], capture_output=True, text=True, env=ENV)
    out = r.stdout.strip(); i = out.find("[")
    return json.loads(out[i:]) if i >= 0 else []


def lg(query):
    r = subprocess.run(["python3", f"{VAULT}/lg-sql.py", query], capture_output=True, text=True, env=ENV)
    out = r.stdout.strip(); i = out.find("[")
    return json.loads(out[i:]) if i >= 0 else []


def rule(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


print("LEAKGUARD SESSION BRIEF")
print("Everything below is pulled live. Nothing here is quoted from a plan document.")

# ── 1 · THE TRAPS, first, because these are what actually costs time ─────────────────────────────
rule("1 · READ THIS BEFORE YOU TOUCH A TOOL")
print("""
ThingsLog is the SOURCE OF TRUTH. Our `readings` table is a copy and can be wrong. When they
disagree, ThingsLog wins.

A claim about a device, a customer, a count or an outcome is only true if it came from ThingsLog or
the live database THIS SESSION. Not from a plan. Not from an audit. Not from what you did an hour
ago. Every "there are only N" said on this project has been wrong.

TOOL USAGE, exactly as written -- the wrong form fails in ways that look like an outage:
  lg-verify.py                          # the gate. Run it before claiming anything is done.
  lg-crosscheck.py <device>|--all       # ours vs ThingsLog
  lg-crosscheck.py <device> --series YYYY-MM-DD
  lg-device-config.py <device> --show   # what ThingsLog actually holds
  thingslog-api.py get /api/v2/devices/<number>    <-- get takes a PATH
  thingslog-api.py config <number>                 <-- config takes a NUMBER
  leakguard-name-sync.py <device> [--apply]        # writes BOTH ThingsLog and the CRM

DEPLOY RULES (binding, from the front door):
  bun, never npm. Commit as PortalPeteZero. Frontend auto-deploys on push to main; confirm READY
  with vercel-api.py deploy-for-sha <sha>. Edge functions deploy separately -- a commit is NOT a
  deploy. A change to _shared/alarm-engine.ts means redeploying ALL FOUR consumers:
  thingslog-webhook, thingslog-sync, connection-watchdog, demo-session.

DESIGN THAT LOOKS LIKE A BUG AND IS NOT:
  properties.device_id is the MAIN-meter (port 0) pointer BY DESIGN. Sub-meters resolve via
  devices.property_id. Converting those call sites would be damage -- it has been proposed and
  rejected twice.
""")

# ── 2 · LIVE STATE ──────────────────────────────────────────────────────────────────────────────
rule("2 · LIVE STATE, pulled just now")
try:
    fleet = lg("""SELECT count(*) AS device_rows, count(DISTINCT device_number) AS loggers,
                         count(*) FILTER (WHERE property_id IS NOT NULL) AS installed
                  FROM devices""")[0]
    print(f"  {fleet['loggers']} loggers, {fleet['device_rows']} meter rows, {fleet['installed']} installed")
except Exception as e:
    print(f"  (fleet query failed: {e})")

try:
    findings = lg("""SELECT check_id, count(*) AS n FROM audit_findings
                     WHERE NOT acknowledged GROUP BY check_id ORDER BY check_id""")
    summary = ", ".join("{} x{}".format(f["check_id"], f["n"]) for f in findings)
    print("  daily audit: " + (summary or "clean"))
except Exception as e:
    print(f"  (audit query failed: {e})")

try:
    alerts = lg("""SELECT a.alert_type, count(*) AS n FROM alert_logs a
                   WHERE a.status = 'pending' AND a.created_at > now() - interval '7 days'
                   GROUP BY 1 ORDER BY 2 DESC""")
    summary = ", ".join("{} x{}".format(a["alert_type"], a["n"]) for a in alerts)
    print("  pending alerts (7d): " + (summary or "none"))
except Exception as e:
    print(f"  (alert query failed: {e})")

# ── 3 · THE NOTES THAT MATTER ───────────────────────────────────────────────────────────────────
rule("3 · THE OPERATING NOTES (read the front door in full before code work)")
for path, why in [
    ("Projects/CD-LeakGuard/leakguard-crm-front-door.md", "the front door -- repos, deploys, model, state of play"),
    ("Projects/CD-LeakGuard/lg-sop-verify-before-claiming.md", "the one rule, and what has been established the hard way"),
    ("Projects/CD-LeakGuard/lg-fix-plan.md", "the plan -- INTENT, never live state"),
]:
    rows = cc(f"SELECT length(body) AS n, frontmatter->>'status' AS st FROM vault_notes WHERE vault_path='{path}'")
    if rows:
        st = f", status {rows[0]['st']}" if rows[0].get("st") else ""
        print(f"  {path}  ({rows[0]['n']} chars{st})")
        print(f"      {why}")
    else:
        print(f"  {path}  -- NOT FOUND")
print("""
  Read one in full with:
    VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py \\
      "SELECT body FROM vault_notes WHERE vault_path='Projects/CD-LeakGuard/leakguard-crm-front-door.md'"
""")

# ── 4 · KNOWN OPEN ──────────────────────────────────────────────────────────────────────────────
rule("4 · KNOWN OPEN -- do not 'discover' these again")
try:
    for d in lg("""SELECT d.device_number, d.tl_output_index AS port, c.full_name
                   FROM devices d JOIN properties p ON p.id = d.property_id
                   JOIN customers c ON c.id = p.customer_id
                   WHERE d.property_id IS NOT NULL AND d.is_active
                     AND (SELECT count(DISTINCT r.counter_m3) FROM readings r WHERE r.device_id = d.id) <= 1
                   ORDER BY d.device_number"""):
        print(f"  {d['device_number']} port {d['port']} ({d['full_name']}): has never registered a litre")
except Exception as e:
    print(f"  (dead-meter query failed: {e})")
try:
    for w in lg("""SELECT c.full_name FROM subscriptions s
                   JOIN properties p ON p.id = s.property_id JOIN customers c ON c.id = p.customer_id
                   WHERE s.status = 'active' AND p.device_id IS NULL AND c.full_name <> 'Jane Williams'"""):
        print(f"  {w['full_name']}: paying, no device installed")
except Exception as e:
    print(f"  (waiting query failed: {e})")
print("""  04259810 (Paul Kieser): 10 L/pulse, physical confirmation still open.
  04295016 (Ian Lawson): 10 L/pulse, old meter, confirmed correct by Pete.
  04299212 (Peter Reilly): ThingsLog holds NO history for 2026 and will not rebuild. Our table is
    the only record; readings/current is the only cross-check that works for this one device.
  Jane Williams is Pete's own staff on Pete's own card. A test account. Never nag about it.""")

rule("5 · NEXT STEP")
print("""  Run the gate before you claim anything is done:
      VAULT=/tmp/pbs python3 /tmp/pbs/lg-verify.py

  Unlock the LeakGuard tools for this session:
      VAULT=/tmp/pbs python3 /tmp/pbs/lg-brief.py --ack
""")

if "--ack" in sys.argv:
    with open(MARKER, "w") as f:
        f.write(str(time.time()))
    print(f"ACK recorded ({MARKER}). LeakGuard tools unlocked for this session.")
