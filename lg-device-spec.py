#!/usr/bin/env python3
"""lg-device-spec.py — diff a logger's WHOLE ThingsLog config against the fleet specification.

WHY THIS EXISTS (27 Jul 2026).

`lg-device-config.py --standard` set the pulse coefficient, digits, fraction, initial counter and
call-in, read those five back, and printed VERIFIED. It was telling the truth about the fields it
knew about. Meanwhile nine loggers stayed on `Europe/Sofia` (ThingsLog's own home timezone, the
factory default) and ten had pulse input 2 enabled on factory settings with only one meter behind
them. Two of those were live customers. Pete found both by looking at the ThingsLog console:
"you didn't correct the time zone to Canary, it's still set to Sofia" and "you have the second
sensor turned on".

The failure is not that those two fields were forgotten. It is that a check written as
"did my writes land?" can only ever confirm the things somebody already thought of. Every field
nobody thought of stays at whatever the factory chose and is signed off by silence.

So this tool inverts it. It holds a specification for EVERY key in the config DTO and reports three
categories:

    WRONG        the value differs from the specification
    UNSPECIFIED  a key exists on the device that the specification says nothing about
                 <- this is the category that would have caught both of Pete's findings
    OK           matches

UNSPECIFIED is the point. A new firmware field, a setting ThingsLog adds, a slot nobody considered:
it shows up as "nobody has decided about this" rather than hiding behind a green tick. Deciding it
does not matter is fine — record it in IGNORED below, with a reason, and it stops appearing. What is
not fine is never being asked.

  VAULT=/tmp/pbs python3 /tmp/pbs/lg-device-spec.py <device>|--all [--json]
"""
import json, os, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
ENV = {**os.environ, "VAULT": VAULT}


def tl(path):
    out = subprocess.run(["python3", f"{VAULT}/thingslog-api.py", "get", path],
                         capture_output=True, text=True, env=ENV).stdout
    return json.loads(out) if out.strip().startswith(("{", "[")) else None


def cfg(n):
    out = subprocess.run(["python3", f"{VAULT}/thingslog-api.py", "config", n],
                         capture_output=True, text=True, env=ENV).stdout
    return json.loads(out) if out.strip().startswith("{") else None


def sql(q):
    # RAISES rather than returning []. A swallowed database failure used to leave crm_ports()
    # falling back to [0], so a two-meter logger was audited as single-port. It now also decides
    # the expected pulse coefficient and call-in, where an empty result would report a correct
    # 10 L/pulse device as WRONG — so silence here is not survivable.
    out = subprocess.run(["python3", f"{VAULT}/lg-sql.py", q],
                         capture_output=True, text=True, env=ENV).stdout
    i = out.find("[")
    if i < 0:
        raise SystemExit(f"lg-device-spec: SQL failed, refusing to guess — {out[:300]}")
    return json.loads(out[i:])


# ── THE SPECIFICATION ────────────────────────────────────────────────────────────────────────────
# A value, or a callable taking (device_number, live_config) and returning the expected value.
# EVERY top-level key of the config DTO must appear here or in IGNORED, or it is reported.
def _callin_reason(n):
    """The recorded justification for a non-standard call-in, if there is one."""
    rows = sql(f"SELECT callin_interval_reason AS r FROM devices "
               f"WHERE device_number = '{n}' AND tl_output_index = 0")
    return ((rows[0].get("r") if rows else None) or "").strip()


def _expected_callin(n, c):
    """32 quarter-hours = the 8h fleet call-in — UNLESS somebody wrote down why it is different.

    A deliberate interval and an abandoned diagnostic look identical in the config; the only thing
    that tells them apart is a recorded reason, which is why devices.callin_interval_reason exists.
    This tool did not read it, so on 29 Jul 2026 it was still reporting 04298215 as WRONG. That is
    Michelle Johnson, whose 4-hourly call-ins are a customer request relayed by Jane and confirmed
    by Pete on 28 Jul, and whose reason field ends "DO NOT put this back to 8 hours". She had
    already lost those call-ins once, on 27 Jul, to exactly this kind of tidy-up.

    A tool that keeps flagging a settled decision will eventually get it reverted by someone
    following the tool. lg-commission.py takes a recorded reason as making the setting CORRECT;
    this now does the same, and prints the reason so it stays visible rather than silently passing.
    """
    return c.get("countsThreshold") if _callin_reason(n) else 32


SPEC = {
    "timeZone": "Atlantic/Canary",
    "recordPeriod": "MINUTES",
    "every": 15,
    "countsThreshold": _expected_callin,
    # An ACTION flag, not a setting. It deletes ThingsLog's stored history for the device and a
    # whole-DTO PUT carrying `true` would fire it as a side effect.
    "deleteOldCounters": False,
}

IGNORED = {
    "configured": "device-reported: False simply means a change is queued for the next call-in",
    "date": "device-reported timestamp, not a setting",
    "sensorConfigs": "checked separately below, per slot",
    "missedTransmissionSeverity": "ThingsLog's own alerting; we use our connection watchdog instead",
    "packetAutoCorrection": "ThingsLog transport-level setting; no fleet position taken. If this "
                            "ever needs one, decide it here rather than leaving it to the factory",
}

# Per-slot parameters for an ENABLED pulse input. initial_counter is per-device (the meter face at
# install) so it is required to exist rather than to equal anything.
SENSOR_SPEC = {
    # Read from the CRM's own record of what meter is fitted, rather than a hardcoded pair of
    # device numbers. The same list used to be duplicated in lg-verify.py, lg-device-spec.py and
    # lg-device-config.py, and duplication is precisely why they drifted apart: on 29 Jul 2026
    # lg-device-config had no list at all and would have reset both of these to 1 L/pulse.
    "pulse_coef": lambda n: _crm_coef(n),
    # REPORTED, NOT ASSERTED. `digits` is the size of the PHYSICAL meter register, not a fleet
    # setting: 15 installed meters run 8 and 8 run 9, and every one of those agrees with ThingsLog
    # to the litre. lg-commission.py stopped asserting it on 28 Jul for that reason. This file kept
    # demanding "8" and so, on 29 Jul, 9 of the 10 faults it reported were not faults — a tool that
    # is wrong nine times out of ten is one you learn to ignore.
    "digits": "<any>",
    "fraction": "3",
    "sensor_type": "water_meter",
    "units_type": "CUBIC_METER",
    "initial_counter": "<any>",
}


def crm_ports(n):
    rows = sql(f"SELECT tl_output_index FROM devices WHERE device_number = '{n}' ORDER BY 1")
    return sorted(int(r["tl_output_index"]) for r in rows) or [0]


def _crm_coef(n):
    """The pulse coefficient this logger's meter should be on, per the CRM's litres_per_pulse.

    Unfitted stock has no meter to describe, so it falls to the fleet default of 1 L/pulse.
    """
    rows = sql(f"SELECT litres_per_pulse AS lpp FROM devices "
               f"WHERE device_number = '{n}' AND tl_output_index = 0")
    lpp = rows[0].get("lpp") if rows else None
    return f"{float(lpp) / 1000:g}" if lpp is not None else "0.001"


def check(n):
    c = cfg(n)
    if not c:
        return [("UNREADABLE", "config", "", "")]
    out = []
    for k, v in c.items():
        if k in IGNORED:
            continue
        if k not in SPEC:
            out.append(("UNSPECIFIED", k, repr(v), "no fleet position has ever been taken on this key"))
            continue
        want = SPEC[k](n, c) if callable(SPEC[k]) else SPEC[k]
        if v != want:
            out.append(("WRONG", k, repr(v), f"expected {want!r}"))
        elif k == "countsThreshold" and v != 32:
            # Passing only because a reason is on file. Say so — a settled decision should be
            # visible in the report, not silently absent from it.
            out.append(("BY DESIGN", k, repr(v),
                        f"off-standard on purpose: {_callin_reason(n)[:150]}"))

    ports = crm_ports(n)
    for i, sc in enumerate(c.get("sensorConfigs", [])):
        enabled = bool(sc.get("enabled"))
        should = i in ports
        if enabled != should:
            out.append(("WRONG", f"sensorConfigs[{i}].enabled", repr(enabled),
                        f"the CRM has meter ports {ports}, so this must be {should}"))
        if not enabled:
            continue
        p = sc.get("parameters") or {}
        for k, v in SENSOR_SPEC.items():
            want = v(n) if callable(v) else v
            got = p.get(k)
            if want == "<any>":
                if got in (None, ""):
                    out.append(("WRONG", f"sensorConfigs[{i}].{k}", repr(got), "must be set at install"))
            elif got != want:
                out.append(("WRONG", f"sensorConfigs[{i}].{k}", repr(got), f"expected {want!r}"))
        for k in p:
            if k not in SENSOR_SPEC:
                out.append(("UNSPECIFIED", f"sensorConfigs[{i}].{k}", repr(p[k]),
                            "no fleet position has ever been taken on this parameter"))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if "--all" in sys.argv or not args:
        devs, page = [], 0
        while True:
            d = tl(f"/api/v2/devices?page={page}&size=100")
            devs += d.get("content", [])
            if d.get("last", True) or page > 20:
                break
            page += 1
        nums = sorted(x["number"] for x in devs)
    else:
        nums = args

    report, wrong, unspec = {}, 0, 0
    for n in nums:
        f = check(n)
        report[n] = f
        wrong += sum(1 for x in f if x[0] == "WRONG")
        unspec += sum(1 for x in f if x[0] == "UNSPECIFIED")

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Fleet specification check — {len(nums)} logger(s)\n")
        for n, f in report.items():
            bad = [x for x in f if x[0] == "WRONG"]
            un = [x for x in f if x[0] == "UNSPECIFIED"]
            byd = [x for x in f if x[0] == "BY DESIGN"]
            if not bad and not un and not byd:
                print(f"  {n}  matches the specification")
                continue
            print(f"  {n}")
            for _, k, got, why in bad:
                print(f"     WRONG        {k} = {got}   ({why})")
            for _, k, got, why in byd:
                print(f"     BY DESIGN    {k} = {got}   ({why})")
            for _, k, got, why in un:
                print(f"     UNSPECIFIED  {k} = {got}   ({why})")
        print(f"\n{wrong} field(s) wrong, {unspec} field(s) nobody has decided about.")
        if unspec:
            print("An UNSPECIFIED field is not necessarily a fault. It is a field that has never been")
            print("considered, which is exactly how the whole fleet sat on Europe/Sofia. Decide it and")
            print("record the decision in SPEC or IGNORED.")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
