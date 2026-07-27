#!/usr/bin/env python3
"""lg-verify.py — the LeakGuard truth check. Run it BEFORE claiming anything is done.

WHY THIS EXISTS (27 Jul 2026). A full day of work produced a repeating failure: predict what a change
would do, then report the prediction as the outcome without going back to the system. Concretely:
  - Fired deleteOldCounters to clear a logger's bench pulses, reported it handled. It cleared
    ThingsLog's HISTORY, not the device's accumulator. A live customer's counter is 3,766 L out.
  - Claimed a monitoring boundary would exclude already-stored litres. It would not.
  - Claimed no cross-check was possible for a device. readings/current worked fine.
  - Said "there are only N" — 2 customers, 4 tier writers, 2 report sites, 8 alerts, 7 reader
    functions. WRONG EVERY TIME. Real answers: 4, 6, 3, 52, 8.
  - Was one step from converting 15 code sites that are correct by design, because the audit said so
    and the SOP said the opposite.

THE RULE THIS ENFORCES:
  A claim about a device, a customer or a count is only true if it came from ThingsLog or the live
  database THIS RUN. Not from a plan, not from an audit, not from what you did an hour ago.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-verify.py            # full check, exit 1 on any failure
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-verify.py --device 04299212
"""
import json, os, subprocess, sys

ENV = {**os.environ, "VAULT": os.environ.get("VAULT", "/tmp/pbs")}
FAILURES: list[str] = []
WARNINGS: list[str] = []


def sql(q):
    r = subprocess.run(["python3", "/tmp/pbs/lg-sql.py", q], capture_output=True, text=True, env=ENV)
    out = r.stdout.strip(); i = out.find("[")
    if i < 0:
        raise SystemExit(f"SQL FAILED: {out[:300]}")
    return json.loads(out[i:])


def check(name, ok, detail, warn_only=False):
    tag = "OK  " if ok else ("WARN" if warn_only else "FAIL")
    print(f"  {tag}  {name}: {detail}")
    if not ok:
        (WARNINGS if warn_only else FAILURES).append(f"{name}: {detail}")
    return ok


print("LeakGuard verification — every number below came from the live systems just now\n")

# ── INGEST INTEGRITY ─────────────────────────────────────────────────────────────────────────────
print("INGEST")
n = sql("SELECT count(*) AS n FROM readings WHERE reading_time > now()")[0]["n"]
check("no future-dated readings", int(n) == 0,
      f"{n} rows dated in the future (one poisons that device's delta baseline for ever)")

g4 = sql("""SELECT count(*) AS tx, count(*) FILTER (WHERE raw_payload ? 'rejected_future') AS g4
            FROM transmission_log WHERE received_at > now() - interval '2 hours'""")[0]
tx, g4n = int(g4["tx"]), int(g4["g4"])
check("G4 guard is running", tx == 0 or g4n == tx,
      f"{g4n}/{tx} transmissions in the last 2h carry the guard field" + (" (no traffic yet)" if tx == 0 else ""),
      warn_only=(tx == 0))

neg = sql("SELECT count(*) AS n FROM readings WHERE counter_m3 < 0")[0]["n"]
check("no negative counters stored", int(neg) == 0, f"{neg} rows")

# Silent data loss: entries delivered but not stored. One dropped entry per payload is normal
# overlap; a large ratio is the baseline-poisoning signature.
drop = sql("""SELECT count(*) AS n FROM transmission_log
              WHERE (raw_payload->>'entries_in_payload')::int > readings_count * 5
                AND received_at > now() - interval '48 hours'""")[0]["n"]
check("no silent payload loss (48h)", int(drop) == 0,
      f"{drop} transmissions stored under a fifth of what was delivered")

# ── THINGSLOG IS THE TRUTH ───────────────────────────────────────────────────────────────────────
print("\nTHINGSLOG vs OUR DATA")
r = subprocess.run(["python3", "/tmp/pbs/lg-crosscheck.py", "--all"],
                   capture_output=True, text=True, env=ENV)
tail = [l for l in r.stdout.strip().split("\n") if "agree" in l]
check("every installed device agrees with ThingsLog", r.returncode == 0,
      tail[-1].strip() if tail else "cross-check did not report")

# ── METERING CONFIG ──────────────────────────────────────────────────────────────────────────────
print("\nMETERING")
odd = sql("""SELECT device_number, litres_per_pulse FROM devices
             WHERE property_id IS NOT NULL AND litres_per_pulse IS DISTINCT FROM 1
             ORDER BY device_number""")
known = {"04295016", "04259810"}   # Lawson: old meter, confirmed by Pete. Kieser: physical check open.
unexpected = [d["device_number"] for d in odd if d["device_number"] not in known]
check("no unexpected pulse rates on installed devices", not unexpected,
      f"{[d['device_number'] for d in odd]} non-standard; unexpected: {unexpected or 'none'}")

# ── CUSTOMERS ────────────────────────────────────────────────────────────────────────────────────
print("\nCUSTOMERS")
tier = sql("""SELECT c.full_name, d.subscription_tier AS device_tier, pl.tier AS paid_tier
              FROM properties p JOIN devices d ON d.id = p.device_id
              JOIN customers c ON c.id = p.customer_id
              JOIN subscriptions s ON s.property_id = p.id JOIN plans pl ON pl.id = s.plan_id
              WHERE s.status IN ('active','grandfathered')""")
mismatch = [t for t in tier if t["device_tier"] != t["paid_tier"]
            and not (t["paid_tier"] == "founder" and t["device_tier"] in ("plus", "founder"))]
check("everyone gets the tier they pay for", not mismatch,
      f"{len(tier)} checked, {len(mismatch)} mismatched" + (f" -> {[m['full_name'] for m in mismatch]}" if mismatch else ""))

# Jane Williams is Pete's own staff on Pete's own card - a test account, not a customer waiting for
# an install and not a refund question. Settled 27 Jul 2026: "It's my card, all right? Just stop.
# Take it off the nag list." Excluded permanently.
waiting = sql("""SELECT c.full_name FROM subscriptions s JOIN properties p ON p.id = s.property_id
                 JOIN customers c ON c.id = p.customer_id
                 WHERE s.status = 'active' AND p.device_id IS NULL
                   AND c.full_name <> 'Jane Williams'""")
check("no paid customer waiting for a device", not waiting,
      f"{len(waiting)} waiting: {[w['full_name'] for w in waiting] or 'none'}", warn_only=True)

# ── THINGS THAT REACH NOBODY ─────────────────────────────────────────────────────────────────────
print("\nUNSEEN BY ANYONE")
iss = sql("SELECT count(*) AS n FROM issue_reports WHERE status = 'open'")[0]["n"]
check("no unanswered customer issue reports", int(iss) == 0, f"{iss} open", warn_only=True)

cw = sql("""SELECT count(*) AS n FROM alert_logs
            WHERE alert_type = 'config_warning' AND status = 'pending'""")[0]["n"]
check("no pending config warnings", int(cw) == 0, f"{cw} pending (dead meters, missing thresholds)", warn_only=True)

# Test the COUNTER, not delta_litres: a device whose litres were zeroed or bounded (a commissioning
# boundary, a correction) has no positive deltas but a counter that plainly moved. Same bug this
# script found in G6 itself.
dead = sql("""SELECT d.device_number, d.tl_output_index FROM devices d
              WHERE d.property_id IS NOT NULL AND d.is_active
                AND EXISTS (SELECT 1 FROM readings r WHERE r.device_id = d.id)
                AND (SELECT count(DISTINCT r.counter_m3) FROM readings r WHERE r.device_id = d.id) <= 1""")
check("no meter that has never registered a litre", not dead,
      f"{[(d['device_number'], d['tl_output_index']) for d in dead] or 'none'}")

# ── DAILY AUDIT (D6) ─────────────────────────────────────────────────────────────────────────────
print("\nDAILY AUDIT")
af = sql("""SELECT check_id, count(*) AS n FROM audit_findings
            WHERE NOT acknowledged GROUP BY check_id ORDER BY check_id""")
if not af:
    check("audit findings outstanding", True, "none")
else:
    for a in af:
        # G1/G3 are the historical corruption still awaiting correction (Workstream B), not new faults.
        check(f"audit {a['check_id']}", False, f"{a['n']} unacknowledged", warn_only=True)
last = sql("SELECT max(run_at)::text AS t FROM audit_findings")[0]["t"]
check("audit has run", last is not None, f"most recent finding recorded {last}", warn_only=True)

# ── ALARM COVER ──────────────────────────────────────────────────────────────────────────────────
print("\nALARM COVER")
nocover = sql("""SELECT d.device_number FROM devices d
                 WHERE d.property_id IS NOT NULL AND d.is_active
                   AND NOT EXISTS (SELECT 1 FROM alarm_no_use_windows w WHERE w.device_id = d.id)""")
check("every live meter has an overnight window", not nocover,
      f"{[d['device_number'] for d in nocover] or 'none'} without one")

failopen = sql("""SELECT count(*) AS n FROM device_alarm_config
                  WHERE high_use_alarm_enabled AND coalesce(high_use_threshold, 0) = 0""")[0]["n"]
check("no alarm switched on with no threshold", int(failopen) == 0, f"{failopen} would never fire")

print()
if FAILURES:
    print(f"FAILED ({len(FAILURES)}):")
    for f in FAILURES:
        print(f"  - {f}")
if WARNINGS:
    print(f"Needs attention ({len(WARNINGS)}):")
    for w in WARNINGS:
        print(f"  - {w}")
if not FAILURES and not WARNINGS:
    print("All checks clean.")
sys.exit(1 if FAILURES else 0)
