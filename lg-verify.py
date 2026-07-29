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
import importlib.util as _ilu
import json, os, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
ENV = {**os.environ, "VAULT": VAULT}
FAILURES: list[str] = []
WARNINGS: list[str] = []

_k_spec = _ilu.spec_from_file_location("lg_known", f"{VAULT}/lg-known.py")
lg_known = _ilu.module_from_spec(_k_spec); _k_spec.loader.exec_module(lg_known)
KNOWN = lg_known.load()


def sql(q):
    r = subprocess.run(["python3", "/tmp/pbs/lg-sql.py", q], capture_output=True, text=True, env=ENV)
    out = r.stdout.strip(); i = out.find("[")
    if i < 0:
        raise SystemExit(f"SQL FAILED: {out[:300]}")
    return json.loads(out[i:])


def check(name, ok, detail, warn_only=False):
    # Already ruled on? Print the decision instead of the fault. lg-known.py explains why: without
    # somewhere to record a judgement, this file re-raised settled matters every single run.
    if not ok:
        why = lg_known.reason_for(KNOWN, "ALL", name)
        if why:
            print(f"  ----  {name}: {detail}")
            print(f"        DECIDED: {why}")
            return True
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

# ── COMMISSIONING COMPLETENESS ───────────────────────────────────────────────────────────────────
# Added 27 Jul 2026 after Pete found THREE things a "commissioned" logger was missing that nothing
# checked: the timezone (9 devices still on the factory Europe/Sofia), pulse input 2 left enabled
# (10 devices), and the map location (the two commissioned that day). Every one was invisible
# because the check asked "did my writes land?" instead of "is this device fully set up?".
print("\nCOMMISSIONING")
noloc = sql("""SELECT d.device_number FROM devices d
               WHERE d.property_id IS NOT NULL AND d.is_active
                 AND d.device_number NOT LIKE 'DEMO%'
                 AND (d.tl_latitude IS NULL OR d.tl_longitude IS NULL)""")
check("every installed meter has a map location", not noloc,
      f"{[d['device_number'] for d in noloc] or 'none'} without coordinates "
      f"(MapView, the device card and the Google Maps link all read these)")

# BOTH ENDS. ThingsLog owns the location (PUT /api/devices/{n}/location) and our devices.tl_latitude
# mirrors it -- the tl_ prefix means exactly what it says. Note the device DTO also carries
# latitude/longitude fields: they are vestigial, always null, and a PUT to them returns 200 and
# silently drops the value. Proven 27 Jul 2026 by writing one and reading it back.
tlloc = subprocess.run(["python3", "/tmp/pbs/thingslog-api.py", "get", "/api/devices/locations"],
                       capture_output=True, text=True, env=ENV).stdout
try:
    tlloc = json.loads(tlloc)
except Exception:
    tlloc = {}
installed = sql("""SELECT device_number, tl_latitude::float AS lat, tl_longitude::float AS lon
                   FROM devices WHERE property_id IS NOT NULL AND is_active
                     AND tl_output_index = 0 AND device_number NOT LIKE 'DEMO%'""")
notl = [d["device_number"] for d in installed
        if not (tlloc.get(d["device_number"], {}) or {}).get("latitude")]
check("every installed meter has a location AT THINGSLOG too", not notl and bool(tlloc),
      f"{notl or 'none'} unset at ThingsLog" if tlloc else "could not read /api/devices/locations",
      warn_only=not tlloc)

# BOTH AXES, and it must not pass on no data. Until 29 Jul 2026 this compared LATITUDE only, so a
# meter on the right latitude and the wrong longitude — a different island — was signed off.
# lg-commission.py fixed exactly this on 28 Jul and said so in its comment; this file never got it.
#
# Worse, the whole comparison was skipped when /api/devices/locations could not be read: `tlloc`
# came back {}, `drift` was therefore empty, and the check printed "none differ between the two
# systems". A pass built on nothing compared. `not checked` is not `clean`, so it now FAILS and
# says which it is. (`is None` rather than truthiness: latitude 0.0 is a real value here — it is
# how ThingsLog stores "never set" — and falsiness silently skipped those rows too.)
drift = []
for d in installed:
    t = tlloc.get(d["device_number"]) or {}
    pair = (d["lat"], d["lon"], t.get("latitude"), t.get("longitude"))
    if any(v is None for v in pair):
        continue                      # absent coordinates are the two checks above, not drift
    if abs(d["lat"] - t["latitude"]) > 1e-5 or abs(d["lon"] - t["longitude"]) > 1e-5:
        drift.append(d["device_number"])
check("ThingsLog and our CRM agree on where each meter is, BOTH axes",
      bool(tlloc) and not drift,
      f"{len(installed)} compared, {drift or 'none'} differ between the two systems" if tlloc
      else "NOT CHECKED — /api/devices/locations could not be read, so nothing was compared. "
           "This is not a clean result.")

badtz = sql("""SELECT device_number FROM devices
               WHERE is_active AND tl_timezone IS DISTINCT FROM 'Atlantic/Canary'""")
check("every logger is on Canary time", not badtz,
      f"{[d['device_number'] for d in badtz] or 'none'} on another timezone. The alarm engine "
      f"resolves property.timezone -> device.tl_timezone -> Atlantic/Canary, so a property with no "
      f"timezone of its own inherits this and its overnight window is sampled hours out.")

# THE SETTLED POLICY, recorded here so it stops being re-litigated. Pete, 27 Jul 2026: "the system is
# set up at the minute that these are turned off and only internal CD people get alerts and then we
# notify customer." So D19 is DECIDED, not open. What matters now is that it stays that way: an
# alarm email going straight to a customer would breach it.
oncust = sql("""SELECT device_number FROM devices
                WHERE property_id IS NOT NULL AND is_active AND send_alarms_to_customer""")
check("customer alarm emails stay OFF (settled policy: CD is alerted, CD notifies)", not oncust,
      f"{[d['device_number'] for d in oncust] or 'none'} would email the customer directly")

# ── THE MONEY LADDER ─────────────────────────────────────────────────────────────────────────────
# The property ladder is install_quoted -> install_paid -> install_booked -> leakguarded, and
# statusConfig.tsx says so in as many words. Found 28 Jul 2026: SIX properties sat at install_booked
# with no subscription row of any kind, quotes going back to April, because booking an installation
# appointment flipped the status with no payment check at all. An engineer's day booked against work
# nobody has bought.
print("\nTHE MONEY LADDER")
unpaid = sql("""SELECT c.full_name, p.address_line1 FROM properties p
                LEFT JOIN customers c ON c.id = p.customer_id
                WHERE p.status = 'install_booked'
                  AND NOT EXISTS (SELECT 1 FROM subscriptions s
                                  WHERE s.property_id = p.id
                                    AND s.status IN ('active','grandfathered','pending_payment'))""")
check("no install booked against an unpaid quote", not unpaid,
      f"{[(u['full_name'], u['address_line1']) for u in unpaid] or 'none'}", warn_only=True)

freeloader = sql("""SELECT c.full_name, p.address_line1 FROM properties p
                    LEFT JOIN customers c ON c.id = p.customer_id
                    WHERE p.status IN ('leakguarded','live') AND p.device_id IS NOT NULL
                      AND p.address_line1 NOT ILIKE '%Ejemplo%'
                      AND NOT EXISTS (SELECT 1 FROM subscriptions s
                                      WHERE s.property_id = p.id
                                        AND s.status IN ('active','grandfathered'))""")
check("no property being monitored for free", not freeloader,
      f"{[(f['full_name'], f['address_line1']) for f in freeloader] or 'none'}")

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

# THIS CHECK EXISTS BECAUSE THE ONE ABOVE PASSED WHILE THE FLEET HAD NO COVER (27 Jul 2026).
# "Switched on with no threshold" is the exotic failure. The ordinary one is the alarm being OFF,
# and on that date 24 of the 25 config rows were off with a zero threshold - the only enabled row in
# the whole system was the demo property. The gate reported OK. It was asking whether the alarm was
# broken, never whether it was there. Cost: 40,439 L over three days on 04290813 with nobody told.
nocover = sql("""SELECT d.device_number, d.tl_output_index AS port
                 FROM devices d
                 LEFT JOIN device_alarm_config c ON c.device_id = d.id
                 WHERE d.property_id IS NOT NULL AND d.is_active
                   AND d.device_number NOT LIKE 'DEMO%'
                   AND (c.device_id IS NULL
                        OR NOT c.high_use_alarm_enabled
                        OR coalesce(c.high_use_threshold, 0) = 0)""")
check("every live meter has a working high-use alarm", not nocover,
      f"{[(d['device_number'], d['port']) for d in nocover] or 'none'} without one")

# The window must cover the whole day. end_hour 23 reads as "all day" but the engine's test is
# half-open (hour < endHour), so a burst between 11pm and midnight was never counted.
halfday = sql("""SELECT count(*) AS n FROM device_alarm_config
                 WHERE high_use_alarm_enabled
                   AND coalesce(high_use_start_hour, 0) = 0
                   AND coalesce(high_use_end_hour, 24) < 24""")[0]["n"]
check("no high-use window that silently drops the last hour", int(halfday) == 0,
      f"{halfday} rows end before midnight")

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
