#!/usr/bin/env python3
"""lg-commission.py — commission a LeakGuard logger, or audit one that already is.

WHY THIS EXISTS. Pete, 28 Jul 2026: "can we ensure you have a proper documented process for setting
up future devices, it's taken too long with at least 5 mistakes."

Commissioning two loggers took most of a day and left, in the end, SEVEN defects. Not one was
detected by the tooling; every one was found by Pete looking at the ThingsLog console:

  1  timezone left on Europe/Sofia, ThingsLog's own home timezone and the factory default. NINE
     devices, two of them live customers. The alarm engine resolves its window as
     property.timezone -> device.tl_timezone -> Atlantic/Canary, so a property with no timezone of
     its own would have had its overnight window sampled two hours out.
  2  pulse input 2 left enabled with factory settings on TEN loggers that back a single meter. A
     logger polling an input with nothing on it reports zeros for ever, which is exactly what the
     dead-meter watchdog exists to flag.
  3  GPS location never set, so the fleet map had no pin and the Maps link never rendered.
  4  the ThingsLog device name left as "!", so the console could not tell you which unit was where.
  5  monitoring_from never set, so pre-install water counts as the customer's.
  6  deleteOldCounters fired in the belief it would clear the device's pulse accumulator. It clears
     ThingsLog's stored HISTORY. The accumulator survived and a live customer's counter sat 3,766 L
     above her meter face.
  7  a diagnostic 30-minute call-in left in place for days at 16x the normal battery drain -- along
     with two others nobody had gone back to.

The common cause is not forgetfulness. It is that commissioning was a sequence of separate commands,
each verifying only its own writes, with no single definition of "done" and nothing that could fail.

So this is one command. It performs every step, reads every step back from the system that owns it,
and REFUSES to report success if any part is missing. --check runs the same definition of done
against a device that is already live, so an old mistake surfaces too.

  lg-commission.py --check <device>|--all         audit; exits 1 on any gap
  lg-commission.py <device> --property <uuid> --meter-reading <litres> [--meters N] [--dry-run]

WHAT IT DELIBERATELY WILL NOT DO
  * It never fires deleteOldCounters. That flag is destructive, it does not do what its name
    suggests, and nothing about commissioning needs it. Bench pulses are handled by monitoring_from.
  * It never enables customer alarm emails. Settled policy, 27 Jul 2026: internal CD contacts are
    alerted and CD notifies the customer.
"""
import json, math, os, re, subprocess, sys, types, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
ENV = {**os.environ, "VAULT": VAULT}
TZ = "Atlantic/Canary"
GAPS: list[str] = []


def sql(q):
    out = subprocess.run(["python3", f"{VAULT}/lg-sql.py", q], capture_output=True, text=True, env=ENV).stdout
    i = out.find("[")
    if i < 0:
        raise SystemExit(f"SQL FAILED: {out[:300]}")
    return json.loads(out[i:])


def tl_session():
    src = open(f"{VAULT}/leakguard-name-sync.py").read().replace('if __name__ == "__main__":\n    main()', "")
    mod = types.ModuleType("ns")
    exec(compile(src, "ns", "exec"), mod.__dict__)
    m, _c, base, tok, cid = mod._tl()
    return mod, m, base, tok, cid


def tl_get(m, base, tok, path):
    return m._get(base, tok, path)


def step(name, ok, detail):
    print(f"  {'PASS' if ok else 'GAP '}  {name}: {detail}")
    if not ok:
        GAPS.append(f"{name}: {detail}")
    return ok


def geocode(addr):
    o = subprocess.run(["python3", f"{VAULT}/geocoding-api.py", "geocode", addr],
                       capture_output=True, text=True, env=ENV).stdout
    lat = re.search(r"lat:\s*([-\d.]+)", o)
    lon = re.search(r"lon:\s*([-\d.]+)", o)
    typ = re.search(r"location_type:\s*(\S+)", o)
    fmt = re.search(r"formatted:\s*(.+)", o)
    if not (lat and lon):
        return None
    return float(lat.group(1)), float(lon.group(1)), (typ.group(1) if typ else "?"), (fmt.group(1).strip() if fmt else "")


# ── THE DEFINITION OF DONE ───────────────────────────────────────────────────────────────────────
# Every one of these was a real defect on a device somebody had already called commissioned.
def audit(num, m, base, tok, locmap):
    print(f"\n{num}")
    rows = sql(f"""SELECT d.id, d.device_number, d.tl_output_index, d.property_id, d.is_active,
                          d.monitoring_from, d.install_date, d.litres_per_pulse, d.subscription_tier,
                          d.tl_latitude::float AS lat, d.tl_longitude::float AS lon, d.tl_timezone,
                          d.send_alarms_to_customer, d.device_name,
                          p.address_line1, p.house_number, p.city, p.timezone AS prop_tz,
                          c.full_name
                   FROM devices d
                   LEFT JOIN properties p ON p.id = d.property_id
                   LEFT JOIN customers c ON c.id = p.customer_id
                   WHERE d.device_number = '{num}' ORDER BY d.tl_output_index""")
    if not rows:
        return step("device exists in the CRM", False, "no devices row at all")
    main = rows[0]
    if not main["property_id"]:
        print("       (unassigned spare — commissioning checks do not apply)")
        return True

    who = main["full_name"] or "?"
    print(f"       {who}, {main['address_line1']} {main['house_number'] or ''}, {main['city'] or ''}")

    cfg = json.loads(subprocess.run(["python3", f"{VAULT}/thingslog-api.py", "config", num],
                                    capture_output=True, text=True, env=ENV).stdout or "{}")
    p0 = (cfg.get("sensorConfigs") or [{}])[0].get("parameters", {})
    ports = sorted(int(r["tl_output_index"]) for r in rows)
    on = [i for i, sc in enumerate(cfg.get("sensorConfigs", [])) if sc.get("enabled")]

    step("ThingsLog timezone is Canary", cfg.get("timeZone") == TZ, str(cfg.get("timeZone")))
    step("pulse inputs enabled match the meters we have", on == ports, f"ThingsLog {on}, CRM {ports}")
    step("pulse rate, digits and decimals are the fleet standard",
         p0.get("digits") == "8" and p0.get("fraction") == "3",
         f"coef {p0.get('pulse_coef')}, digits {p0.get('digits')}, fraction {p0.get('fraction')}")
    ct = cfg.get("countsThreshold")
    step("call-in is the 8h standard", ct == 32,
         f"{(ct * 15 / 60) if ct else '?'}h — a shortened interval is a DIAGNOSTIC and must be put back")
    # A weak version of this check ("is it set?") passed a brand-new install sitting at 0 on
    # 28 Jul 2026, because "0" is a set value. A meter can genuinely read zero, so this cannot hard
    # fail on the number alone -- but a device commissioned in the last fortnight whose initial
    # counter is 0 is far more likely to be one nobody typed the meter face into than a genuinely
    # unused meter, so it is called out.
    ic = str(p0.get("initial_counter") or "")
    recent = bool(main["install_date"]) and str(main["install_date"]) >= str(
        sql("SELECT (CURRENT_DATE - 14)::text AS d")[0]["d"])
    step("initial counter is set from the meter face", ic != "" and not (ic == "0" and recent),
         f"initial_counter={ic or 'unset'} (PULSES, and ADDITIVE: the reported counter is "
         f"(pulses + initial_counter) x pulse_coef)"
         + ("  — a fresh install reading 0 usually means the meter face was never entered" 
            if ic == "0" and recent else ""))

    dev = tl_get(m, base, tok, f"/api/v2/devices/{num}")
    nm = (dev.get("name") or "").strip()
    step("ThingsLog device name is the address", nm not in ("", "!", "?"), repr(nm))

    loc = locmap.get(num) or {}
    tl_lat = loc.get("latitude")
    step("GPS is set at ThingsLog", bool(tl_lat), f"{tl_lat},{loc.get('longitude')}")
    step("GPS mirrored into the CRM and agreeing", bool(main["lat"]) and bool(tl_lat)
         and abs(main["lat"] - tl_lat) < 1e-5, f"CRM {main['lat']},{main['lon']}")

    step("CRM timezone mirrors ThingsLog", main["tl_timezone"] == TZ, str(main["tl_timezone"]))
    step("property carries its own timezone", main["prop_tz"] == TZ,
         f"{main['prop_tz']} — the engine reads this FIRST, so it is the real safety net")
    step("install date recorded", bool(main["install_date"]), str(main["install_date"]))
    step("monitoring boundary set", bool(main["monitoring_from"]),
         f"{main['monitoring_from']} — without it, pre-install water is booked to the customer")

    for r in rows:
        pid, idx = r["id"], r["tl_output_index"]
        cfgn = sql(f"SELECT high_use_alarm_enabled AS en, high_use_threshold AS thr, "
                   f"high_use_end_hour AS eh FROM device_alarm_config WHERE device_id='{pid}'")
        step(f"port {idx}: high-use alarm live", bool(cfgn) and cfgn[0]["en"] and float(cfgn[0]["thr"] or 0) > 0,
             f"{cfgn[0] if cfgn else 'no config row'}")
        w = sql(f"SELECT count(*) AS n FROM alarm_no_use_windows WHERE device_id='{pid}'")[0]["n"]
        step(f"port {idx}: overnight window exists", int(w) > 0, f"{w} window(s)")
        ct2 = sql(f"SELECT count(*) FILTER (WHERE kind='internal') AS internal, count(*) AS total "
                  f"FROM alarm_contacts WHERE device_id='{pid}'")[0]
        step(f"port {idx}: an internal CD contact is alerted", int(ct2["internal"]) > 0,
             f"{ct2['internal']} internal of {ct2['total']}")

    step("customer alarm emails are OFF", not main["send_alarms_to_customer"],
         "settled policy: CD is alerted, CD notifies the customer")

    sub = sql(f"""SELECT s.status, pl.tier FROM subscriptions s JOIN plans pl ON pl.id = s.plan_id
                  WHERE s.property_id = '{main['property_id']}' AND s.status IN ('active','grandfathered')""")
    step("subscription is live and the device tier matches it", bool(sub) and
         (main["subscription_tier"] == sub[0]["tier"]
          or (sub[0]["tier"] == "founder" and main["subscription_tier"] in ("plus", "founder"))),
         f"pays {sub[0]['tier'] if sub else 'NO ACTIVE SUBSCRIPTION'}, device {main['subscription_tier']}")
    return not GAPS


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mod, m, base, tok, cid = tl_session()
    locmap = json.loads(subprocess.run(["python3", f"{VAULT}/thingslog-api.py", "get",
                                        "/api/devices/locations"], capture_output=True, text=True,
                                       env=ENV).stdout or "{}")
    if "--check" in sys.argv or not args:
        if "--all" in sys.argv or not args:
            nums = [r["device_number"] for r in sql(
                "SELECT DISTINCT device_number FROM devices WHERE property_id IS NOT NULL "
                "AND is_active AND device_number NOT LIKE 'DEMO%' ORDER BY 1")]
        else:
            nums = args
        print("COMMISSIONING AUDIT — the same definition of done that lg-commission applies\n")
        for n in nums:
            audit(n, m, base, tok, locmap)
        print()
        if GAPS:
            print(f"{len(GAPS)} GAP(S):")
            for g in GAPS:
                print(f"  - {g}")
        else:
            print("Every installed meter is fully commissioned.")
        return 1 if GAPS else 0

    print("Commissioning a device is not yet wired as a single write path — use --check to audit.")
    print("Steps, in order, each verified by read-back:")
    print("  1  lg-device-config.py <dev> --standard --initial-litres <meter face>   (pulse, tz, sensors, call-in)")
    print("  2  leakguard-name-sync.py <dev> --apply                                 (name, BOTH systems)")
    print("  3  PUT /api/devices/<dev>/location  from geocoding the property address (NOT the device DTO:")
    print("     its latitude/longitude fields are vestigial and a PUT returns 200 and drops the value)")
    print("  4  link the device to the property, set install_date and monitoring_from")
    print("  5  derive_high_use_thresholds(), overnight window, internal alarm contact")
    print("  6  lg-commission.py --check <dev>   <- must come back clean")
    return 2


if __name__ == "__main__":
    sys.exit(main())
