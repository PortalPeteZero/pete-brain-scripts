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
import importlib.util as _ilu
import json, math, os, re, subprocess, sys, types, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
ENV = {**os.environ, "VAULT": VAULT}
TZ = "Atlantic/Canary"
GAPS: list[str] = []

_k_spec = _ilu.spec_from_file_location("lg_known", f"{VAULT}/lg-known.py")
lg_known = _ilu.module_from_spec(_k_spec); _k_spec.loader.exec_module(lg_known)
KNOWN = lg_known.load()


def sql(q):
    out = subprocess.run(["python3", f"{VAULT}/lg-sql.py", q], capture_output=True, text=True, env=ENV).stdout
    i = out.find("[")
    if i < 0:
        raise SystemExit(f"SQL FAILED: {out[:300]}")
    return json.loads(out[i:])


# The leakguard-name-sync module, which owns the naming convention (format_name +
# name_matches_address). Set by tl_session(); audit() reads it rather than keeping a second,
# subtly different copy of "does this name identify this property".
NS = None


def tl_session():
    global NS
    src = open(f"{VAULT}/leakguard-name-sync.py").read().replace('if __name__ == "__main__":\n    main()', "")
    mod = types.ModuleType("ns")
    exec(compile(src, "ns", "exec"), mod.__dict__)
    NS = mod
    m, _c, base, tok, cid = mod._tl()
    return mod, m, base, tok, cid


def tl_get(m, base, tok, path):
    return m._get(base, tok, path)


# Which device the current run is auditing, so the closing summary can name it. Without this the
# fleet sweep printed 31 anonymous lines — "monitoring boundary set: None" twenty times over with
# nothing to say whose. A summary you have to scroll back through is not a summary.
CURRENT = {"num": "", "who": ""}


def step(name, ok, detail):
    # A finding you have already ruled on is printed as a DECISION, not re-raised as a gap. See
    # lg-known.py for why this exists — before it, a settled call had nowhere to live except a
    # comment in somebody's source, so every session met it again as if it were new.
    if not ok:
        why = lg_known.reason_for(KNOWN, CURRENT["num"], name)
        if why:
            print(f"  ----  {name}: {detail}")
            print(f"        DECIDED: {why}")
            return True
    print(f"  {'PASS' if ok else 'GAP '}  {name}: {detail}")
    if not ok:
        GAPS.append(f"{CURRENT['num']} {CURRENT['who']}".strip() + f" — {name}: {detail}")
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
    CURRENT["num"], CURRENT["who"] = num, ""
    rows = sql(f"""SELECT d.id, d.device_number, d.tl_output_index, d.property_id, d.is_active,
                          d.monitoring_from, d.install_date, d.litres_per_pulse, d.subscription_tier,
                          d.callin_interval_reason,
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
    CURRENT["who"] = who
    print(f"       {who}, {main['address_line1']} {main['house_number'] or ''}, {main['city'] or ''}")

    cfg = json.loads(subprocess.run(["python3", f"{VAULT}/thingslog-api.py", "config", num],
                                    capture_output=True, text=True, env=ENV).stdout or "{}")
    p0 = (cfg.get("sensorConfigs") or [{}])[0].get("parameters", {})
    ports = sorted(int(r["tl_output_index"]) for r in rows)
    on = [i for i, sc in enumerate(cfg.get("sensorConfigs", [])) if sc.get("enabled")]

    step("ThingsLog timezone is Canary", cfg.get("timeZone") == TZ, str(cfg.get("timeZone")))
    step("pulse inputs enabled match the meters we have", on == ports, f"ThingsLog {on}, CRM {ports}")
    # The pulse COEFFICIENT used to be printed here and compared to nothing, and the CRM's
    # litres_per_pulse was selected in the query above and never read. Glenn Dickson passed this
    # check clean on 28 Jul 2026 with ThingsLog on 1 L/pulse and our own record saying 10 — a tenfold
    # disagreement on a live customer, invisible because the assertion only looked at digits and
    # decimals. Both halves are now tested, and the two systems are compared to each other.
    coef = p0.get("pulse_coef")
    try:
        tl_lpp = float(coef) * 1000 if coef is not None else None
    except (TypeError, ValueError):
        tl_lpp = None
    crm_lpp = float(main["litres_per_pulse"]) if main["litres_per_pulse"] is not None else None
    # DECIMALS are asserted; DIGITS are only reported.
    #
    # The original check demanded digits == "8" and failed a third of the fleet. Measured 28 Jul
    # 2026: 15 installed devices run 8 digits and 8 run 9, and every one of those 8 has a counter
    # that agrees with ThingsLog to the litre. Digits is the size of the physical meter register,
    # not a fleet setting, so requiring one value manufactures eight failures that are not faults.
    # `fraction` is different: it scales the counter, it is 3 on every device in the fleet, and a
    # wrong value would misread every reading.
    step("decimal places are the fleet standard", p0.get("fraction") == "3",
         f"fraction {p0.get('fraction')} (meter register digits: {p0.get('digits')}, "
         f"reported not asserted — the fleet runs both 8 and 9)")
    step("our litres-per-pulse matches ThingsLog's coefficient",
         tl_lpp is not None and crm_lpp is not None and abs(tl_lpp - crm_lpp) < 1e-6,
         f"ThingsLog coef {coef} = {tl_lpp} L/pulse, CRM {crm_lpp} L/pulse"
         + ("" if tl_lpp == crm_lpp else "  — every reading is scaled by this"))
    # A deliberate interval and an abandoned diagnostic look IDENTICAL in the config. The only thing
    # that tells them apart is a reason, and until 28 Jul 2026 there was nowhere to write one.
    #
    # Michelle Johnson called in six times a day for at least eleven days because she had asked for
    # it. I swept her onto the 8-hour standard along with two real diagnostics, without asking, and
    # she lost the three call-ins she relies on. Restoring her was not the fix: THIS CHECK would have
    # gone on saying "a shortened interval is a DIAGNOSTIC and must be put back" for ever, so the
    # next session would have reverted her again and been right to, following the tool.
    #
    # So: a documented reason makes a non-standard interval CORRECT, and the reason is printed. No
    # reason means it is still treated as something somebody forgot to put back.
    ct = cfg.get("countsThreshold")
    hours = (ct * 15 / 60) if ct else None
    why = (main.get("callin_interval_reason") or "").strip()
    if ct == 32:
        step("call-in is the 8h standard", True, "8.0h")
    elif why:
        step("call-in is off-standard ON PURPOSE", True,
             f"{hours}h — {why[:160]}{'…' if len(why) > 160 else ''}")
    else:
        step("call-in is the 8h standard", False,
             f"{hours or '?'}h with NO recorded reason — treated as a diagnostic left running. If it "
             f"is deliberate, say so in devices.callin_interval_reason rather than reverting it.")
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
    # This used to assert only that the name was not blank or a placeholder, under the label
    # "is the address" — so a logger named after the WRONG property passed. Now it actually looks
    # for the street and the town in the name. Kept loose on purpose: the convention is
    # "Town - Street - Number - Villa name", and the villa name and separators vary.
    # Shared with lg-brief.py via leakguard-name-sync, which owns the naming convention. The
    # substring test that used to live here failed on any address carrying its house number inline
    # and raised a permanent false gap on 04326710 (Gillian Guidi), whose name is exactly right.
    name_matches = NS.name_matches_address(nm, main["address_line1"], main["city"],
                                           main["house_number"])
    step("ThingsLog device name is the address", name_matches,
         f"{nm!r} vs {main['address_line1']}, {main['city']}")

    loc = locmap.get(num) or {}
    tl_lat, tl_lon = loc.get("latitude"), loc.get("longitude")
    step("GPS is set at ThingsLog", bool(tl_lat) and bool(tl_lon), f"{tl_lat},{tl_lon}")
    # Longitude was fetched and never compared, so a device on the right latitude and the wrong
    # longitude — i.e. the wrong island — passed. Both are compared now.
    gps_ok = all(v is not None for v in (main["lat"], main["lon"], tl_lat, tl_lon)) \
        and abs(main["lat"] - tl_lat) < 1e-5 and abs(main["lon"] - tl_lon) < 1e-5
    step("GPS mirrored into the CRM and agreeing, both axes", gps_ok,
         f"ThingsLog {tl_lat},{tl_lon} vs CRM {main['lat']},{main['lon']}")

    # Compared to ThingsLog's OWN value, not to the constant. The old version tested the CRM against
    # TZ and called it "mirrors ThingsLog", which is only true because the check above pins
    # ThingsLog to TZ as well — it would not have caught the two drifting apart.
    step("CRM timezone mirrors ThingsLog",
         main["tl_timezone"] == cfg.get("timeZone") and main["tl_timezone"] == TZ,
         f"CRM {main['tl_timezone']} vs ThingsLog {cfg.get('timeZone')}")
    step("property carries its own timezone", main["prop_tz"] == TZ,
         f"{main['prop_tz']} — the engine reads this FIRST, so it is the real safety net")
    step("install date recorded", bool(main["install_date"]), str(main["install_date"]))
    # Only asserted on a RECENT install. `recent` is set above from the install date.
    #
    # Blank means "count everything from the first reading", which is what every device did before
    # this field existed. Measured across the fleet on 28 Jul 2026: of the 24 installed meters only
    # two carry any pre-install water at all — Szilard Zsovak 393 L and Michelle Johnson 307 L,
    # both under 1.2% of what they have used since, neither enough to move a bill, a threshold or an
    # alarm. Pete's call, same day: leave them. Failing all 24 for a setting that costs nothing on 22
    # of them just buries the gaps that do matter.
    #
    # It is still asserted where it is cheap and where it bites: a device fitted in the last
    # fortnight, which may have been sitting on the bench being tested first.
    # And asserted only where it CHANGES SOMETHING. The boundary's whole job is to stop water
    # recorded before the fit being booked to the customer, so on a device with no pre-install water
    # there is nothing for it to exclude and an empty field is not a defect. Asserting on the empty
    # FIELD rather than on the actual litres raised all three of the 27 Jul installs on 29 Jul —
    # Dickson and Ferris had no readings at all before their fit, and Guidi had 249 readings that
    # between them registered zero litres (a logger recording on the bench with nothing flowing).
    # Three gaps, not one litre of consequence between them. That is the noise that gets a real
    # finding ignored, so the check now measures the thing it cares about.
    if recent:
        pre = sql(f"""SELECT COALESCE(SUM(r.delta_litres), 0) AS litres, count(*) AS n
                      FROM readings r
                      WHERE r.device_id = '{main['id']}'
                        AND r.reading_time < '{main['install_date']}'::timestamptz""")
        pre_l = float(pre[0]["litres"] or 0)
        pre_n = int(pre[0]["n"] or 0)
        if pre_l > 0:
            step("monitoring boundary set", bool(main["monitoring_from"]),
                 f"{main['monitoring_from'] or 'not set'} — {pre_l:,.0f} L recorded before the fit "
                 f"would be booked to the customer without it")
        else:
            print(f"  ----  monitoring boundary: {main['monitoring_from'] or 'not set'} "
                  f"(nothing to exclude — {pre_n} reading(s) before the fit, 0 L)")
    else:
        print(f"  ----  monitoring boundary: {main['monitoring_from'] or 'not set'} "
              f"(established install — informational, see the note in the source)")

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

    # ── IS IT ACTUALLY WORKING ───────────────────────────────────────────────────────────────────
    # Everything above checks how the device is SET UP. None of it noticed that Glenn Dickson had
    # stopped talking (28 Jul 2026: passed clean while six hours past his call-in), that a counter
    # could disagree with ThingsLog, or that two hours of recording had gone missing. A device can
    # be perfectly configured and still not be watching the property.

    step("the device is switched on", bool(main["is_active"]),
         "is_active — an inactive device stops being monitored")

    live = sql(f"""SELECT tl_last_transmission AS last, tl_next_transmission AS next,
                          tl_transmission_interval_minutes AS mins,
                          round(extract(epoch FROM (now() - tl_next_transmission)) / 60) AS late
                     FROM devices WHERE id = '{main['id']}'""")[0]
    # One interval of slack: an 8-hour logger routinely drifts a few minutes and the whole fleet
    # runs 480-minute intervals, so anything inside its own interval is jitter, not a fault.
    slack = int(live["mins"] or 480)
    late = float(live["late"]) if live["late"] is not None else None
    if late is None:
        when = ", never transmitted"
    elif late <= 0:
        when = f", not due for another {abs(int(late))} min"
    else:
        when = f", {int(late)} MIN LATE (one interval of slack = {slack})"
    step("transmitting on schedule", bool(live["last"]) and late is not None and late <= slack,
         f"last {live['last']}, due {live['next']}{when}")

    for r in rows:
        pid, idx = r["id"], r["tl_output_index"]
        # Our stored counter against ThingsLog's, the same comparison lg-crosscheck.py makes. It was
        # never part of the definition of done, so a device could be storing something different
        # from the system of record and still be called commissioned.
        # /readings/current returns a LIST of sensors; the counter is the `reading` field of the one
        # whose sensorIndex is this port. Parsing it any other way silently yields 0.0 and reports a
        # false failure, which is worse than no check — caught before this shipped. Same shape
        # lg-crosscheck.py uses.
        ours = sql(f"""SELECT counter_m3, reading_time FROM readings WHERE device_id = '{pid}'
                        ORDER BY reading_time DESC LIMIT 1""")
        tlc = tl_get(m, base, tok, f"/api/v2/devices/{num}/readings/current")
        tl_counter = None
        if isinstance(tlc, list):
            for s in tlc:
                if s.get("sensorIndex") == idx and s.get("reading") is not None:
                    tl_counter = float(s["reading"])
                    break
        our_counter = float(ours[0]["counter_m3"]) if ours else None
        agree = (our_counter is not None and tl_counter is not None
                 and abs(our_counter - tl_counter) * 1000 < 1)   # within a litre
        step(f"port {idx}: our counter agrees with ThingsLog", agree,
             f"ours {our_counter} vs ThingsLog {tl_counter}"
             + ("" if agree else "  — the CRM is a copy; ThingsLog is the record"))

        # A gap means the logger stopped recording. Correcting a device's TIMEZONE costs a gap the
        # size of the shift plus one interval — 27 Jul 2026, three devices moved off Europe/Sofia
        # each lost 2h15m, Sofia to Canary being exactly two hours. Do that before a device is
        # fitted, not after. Anything in the last week is worth knowing about.
        g = sql(f"""SELECT count(*) AS n, max(round(dt_seconds/60.0)) AS worst FROM readings
                     WHERE device_id = '{pid}' AND dt_seconds > 900
                       AND reading_time > now() - interval '7 days'""")[0]
        step(f"port {idx}: no recording gaps in the last week", int(g["n"] or 0) == 0,
             f"{g['n']} gap(s)" + (f", longest {g['worst']} min" if g["n"] else ""))

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
            # `AND is_active` used to be here, which meant switching a device OFF removed it from
            # the audit instead of failing it — exactly backwards for a device that has stopped
            # being monitored. Inactive devices are now included and fail the "switched on" step.
            nums = [r["device_number"] for r in sql(
                "SELECT DISTINCT device_number FROM devices WHERE property_id IS NOT NULL "
                "AND device_number NOT LIKE 'DEMO%' ORDER BY 1")]
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
