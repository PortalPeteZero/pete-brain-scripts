#!/usr/bin/env python3
"""lg-device-config.py — set a LeakGuard logger's sensor config at ThingsLog, verified by read-back.

The pulse settings live in a NESTED block (sensorConfigs[0].parameters), so `thingslog-api.py
set-config` cannot reach them — that verb only writes top-level fields. This does the full
read-mutate-PUT-verify cycle.

FLEET STANDARD: pulse_coef 0.001 (1 litre per pulse), 8 digits, 3 decimals.
Factory default on a new spare is 0.01 / 7 / 2 — i.e. 10 L per pulse, which records consumption
TEN TIMES too high. Nine of ten spares were sitting on the factory default as of 27 Jul 2026.

FAILS CLOSED. ThingsLog returns HTTP 200 for fields it silently drops, so a 200 proves nothing.
This re-reads the config afterwards and exits 1 on any mismatch. Never assume the write landed.

⚠️ initial_counter is ADDITIVE to the pulse accumulator, not a replacement. The reported counter is
   (pulses + initial_counter) x pulse_coef. Verified 27 Jul 2026: device 04302516 carries
   initial_counter 574 and reported exactly 0.574 when its pulse count was zero; 04160611 carries 29
   and reported 0.029. So on a logger that has been bench-run, set the meter-face value AND clear the
   accumulated pulses, or the counter reads high by the bench amount for ever.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-device-config.py <device> --show
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-device-config.py <device> --standard [--initial-litres N] [--hours 8]
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-device-config.py --all-spares --standard --dry-run
"""
import json, os, ssl, subprocess, sys, urllib.request, urllib.error

BASE = "https://iot.thingslog.com:4443"
_ctx = ssl.create_default_context()
ENV = {**os.environ, "VAULT": os.environ.get("VAULT", "/tmp/pbs")}

TZ = "Atlantic/Canary"
STD = {"pulse_coef": "0.001", "digits": "8", "fraction": "3"}   # defaults for UNFITTED stock only
FACTORY = {"pulse_coef": "0.01", "digits": "7", "fraction": "2"}  # 10 L/pulse — never ship this

# ── WHY `STD` IS NOT APPLIED WHOLESALE (29 Jul 2026) ─────────────────────────────────────────────
# `p.update(STD)` used to be unconditional, so --standard forced 1 L/pulse and an 8-digit register
# onto whatever device it was pointed at. Measured against ThingsLog on 29 Jul: that would have
# misconfigured 9 of the 23 installed loggers.
#
#   * TWO are legitimately on 10 L/pulse — 04259810 (Paul Kieser) and 04295016 (Ian Lawson, which
#     Pete confirmed himself). Forcing 0.001 makes every reading read TEN TIMES TOO LOW: the exact
#     mirror of the factory-default fault this tool was written to prevent.
#   * EIGHT legitimately run a 9-digit register. `digits` is the size of the PHYSICAL meter
#     register, not a fleet setting. lg-commission.py stopped asserting it on 28 Jul for that
#     reason; this tool went on WRITING it.
#
# And the commissioning SOP names this command as step 1, so the damage was one keystroke away.
#
# The fix is not another hardcoded list of exceptions — three other tools already keep one, and
# that duplication is itself why they drift apart. The CRM already records what each meter is
# (`devices.litres_per_pulse`), so that is what gets written. A device we know nothing about (an
# unfitted spare) still gets the fleet defaults, because for new stock there is no meter to
# contradict them.
FLEET_ALWAYS = {"fraction": "3"}   # genuinely fleet-wide: scales every reading, 3 across the fleet


def _sql(q):
    r = subprocess.run(["python3", "/tmp/pbs/lg-sql.py", q], capture_output=True, text=True, env=ENV)
    out = r.stdout.strip(); i = out.find("[")
    if i < 0:
        raise SystemExit(f"SQL failed: {out[:300]}")
    return json.loads(out[i:])


def _creds():
    raw = subprocess.run(["python3", "/tmp/pbs/cc-sql.py",
                          "SELECT value FROM secrets WHERE name='thingslog-login.json'"],
                         capture_output=True, text=True, env=ENV).stdout
    return json.loads(json.loads(raw)[0]["value"])


def _login(c):
    body = json.dumps({"username": c["username"], "password": c["password"]}).encode()
    req = urllib.request.Request(c.get("base_url", BASE) + "/login", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, context=_ctx, timeout=30) as r:
        return r.headers.get("Authorization").replace("Bearer ", "")


def _get(tok, cid, path):
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": "Bearer " + tok, "Accept": "application/json",
        "X-Company-Id": str(cid), "User-Agent": "curl/8"})
    with urllib.request.urlopen(req, context=_ctx, timeout=40) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def _put(tok, cid, path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="PUT",
                                 headers={"Authorization": "Bearer " + tok, "Accept": "application/json",
                                          "Content-Type": "application/json", "X-Company-Id": str(cid),
                                          "User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, context=_ctx, timeout=40) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


def show(tok, cid, n):
    cfg = _get(tok, cid, f"/api/devices/{n}/config")
    p = cfg["sensorConfigs"][0]["parameters"]
    lpp = round(float(p.get("pulse_coef", 0)) * 1000, 3) if p.get("pulse_coef") else "?"
    factory = all(str(p.get(k)) == v for k, v in FACTORY.items())
    std = all(str(p.get(k)) == v for k, v in STD.items())
    hrs = (cfg.get("countsThreshold") or 0) * (cfg.get("every") or 0) / 60
    print(f"  {n}  pulse_coef={p.get('pulse_coef')} ({lpp} L/pulse)  digits={p.get('digits')} "
          f"fraction={p.get('fraction')}  initial={p.get('initial_counter')}  "
          f"call-in={hrs:g}h  configured={cfg.get('configured')}"
          f"{'   <-- FACTORY DEFAULT, 10x WRONG' if factory else '   [fleet standard]' if std else '   <-- NON-STANDARD'}")
    return cfg


def _crm_ports(n):
    """Which meter ports this logger actually has, according to our CRM."""
    rows = _sql(f"SELECT tl_output_index FROM devices WHERE device_number = '{n}' ORDER BY 1")
    return sorted(int(r["tl_output_index"]) for r in rows) or [0]


def _crm_meter(n):
    """What the CRM says this logger's MAIN meter is: (fitted?, litres_per_pulse, who).

    The CRM is the authority on what meter is physically on the end of the wire — the same
    authority this tool already trusts for how many ports are enabled. `fitted` is False for
    unassigned shelf stock, which is the only case where fleet defaults may be imposed blind.
    """
    rows = _sql(f"""SELECT d.litres_per_pulse, d.property_id, c.full_name AS who
                    FROM devices d
                    LEFT JOIN properties p ON p.id = d.property_id
                    LEFT JOIN customers c ON c.id = p.customer_id
                    WHERE d.device_number = '{n}' AND d.tl_output_index = 0""")
    if not rows:
        return False, None, None
    r = rows[0]
    lpp = float(r["litres_per_pulse"]) if r["litres_per_pulse"] is not None else None
    return bool(r["property_id"]), lpp, r["who"]


def apply_standard(tok, cid, n, initial_litres=None, hours=8, dry=False):
    cfg = _get(tok, cid, f"/api/devices/{n}/config")
    p = cfg["sensorConfigs"][0]["parameters"]
    before = {k: str(p.get(k)) for k in ("pulse_coef", "digits", "fraction", "initial_counter")}

    # ── TIMEZONE ────────────────────────────────────────────────────────────────────────────────
    # Added 27 Jul 2026, after Pete: "you didn't correct the time zone to Canary, it's still set to
    # Sofia." He was right, and it was not one device: the whole new batch of NINE was on
    # Europe/Sofia, ThingsLog's own home timezone and the factory default. This function set the
    # pulse block, the record period and the call-in, called it "fleet standard", and never looked
    # at the timezone. Two of the nine were live customers by then.
    #
    # It matters because the alarm engine resolves the window timezone as
    #     property.timezone -> device.tl_timezone -> Atlantic/Canary
    # so a property with no timezone of its own inherits the device's, and an overnight window would
    # be sampled two hours out. It also makes every ThingsLog timestamp for that device read in a
    # timezone nobody involved lives in.
    tz_before = cfg.get("timeZone")
    cfg["timeZone"] = TZ

    # ── SENSOR ENABLES ──────────────────────────────────────────────────────────────────────────
    # Same discovery, same day: TEN devices had pulse input 2 switched on with factory defaults
    # (0.01 coef, 7 digits) while the CRM knew of a single meter. A logger polling an input with
    # nothing on it reports zeros for ever, which is indistinguishable from the genuinely dead meter
    # G6 exists to catch.
    #
    # The CRM is the authority on how many meters a logger backs -- one devices row per port. So the
    # enables are aligned to it rather than to a flag someone remembers to pass.
    ports = _crm_ports(n)
    enables_before = [i for i, sc in enumerate(cfg.get("sensorConfigs", [])) if sc.get("enabled")]
    for i, sc in enumerate(cfg.get("sensorConfigs", [])):
        sc["enabled"] = i in ports

    # ── WHAT THIS METER ACTUALLY IS ─────────────────────────────────────────────────────────────
    # See the note beside STD. A FITTED meter's pulse rate and register size are facts about the
    # hardware; only unfitted stock gets fleet defaults imposed on it.
    fitted, crm_lpp, who = _crm_meter(n)
    if fitted:
        if crm_lpp is None:
            raise SystemExit(
                f"  {n}: REFUSED — this logger is fitted at {who or 'a customer property'} but the "
                f"CRM has no litres_per_pulse for it. Writing a guessed pulse rate scales every "
                f"reading that meter ever produces. Set devices.litres_per_pulse first.")
        target = {"pulse_coef": f"{crm_lpp / 1000:g}",
                  "digits": str(p.get("digits")),      # the meter's register, not ours to change
                  **FLEET_ALWAYS}
    else:
        target = dict(STD)

    p.update(target)
    if initial_litres is not None:
        p["initial_counter"] = str(int(initial_litres))
    cfg["recordPeriod"] = "MINUTES"
    cfg["every"] = 15
    cfg["countsThreshold"] = int(hours * 60 / 15)
    # deleteOldCounters is an ACTION flag, not a setting. Never let a whole-DTO PUT carry it true.
    cfg["deleteOldCounters"] = False

    want = {**target, "initial_counter": str(int(initial_litres)) if initial_litres is not None
            else before["initial_counter"]}
    print(f"  {n}  {before}  ->  {want}  call-in {hours}h")
    if fitted:
        print(f"     fitted at {who or '?'} — pulse rate {crm_lpp:g} L/pulse taken from the CRM, "
              f"register digits {p.get('digits')} left as the meter has them")
    else:
        print(f"     unfitted stock — fleet defaults applied ({STD['pulse_coef']} = 1 L/pulse, "
              f"{STD['digits']} digits)")
    if tz_before != TZ:
        print(f"     timezone   {tz_before} -> {TZ}")
    if enables_before != ports:
        print(f"     sensors on {enables_before} -> {ports}   (from the CRM's meter rows)")
    if dry:
        print("     DRY RUN — nothing written")
        return True

    st, _ = _put(tok, cid, f"/api/devices/{n}/config", cfg)

    # READ BACK EVERYTHING WE WROTE, not just the part we remembered to check. The old read-back
    # verified the four pulse parameters and printed VERIFIED while the timezone and the second
    # sensor were both wrong -- a gate that checks the wrong fields is worse than none, because it
    # signs off the mistake.
    back = _get(tok, cid, f"/api/devices/{n}/config")
    got = {k: str(back["sensorConfigs"][0]["parameters"].get(k)) for k in want}
    tz_ok = back.get("timeZone") == TZ
    en_ok = [i for i, sc in enumerate(back.get("sensorConfigs", [])) if sc.get("enabled")] == ports
    ok = got == want and tz_ok and en_ok
    detail = "VERIFIED" if ok else "MISMATCH " + json.dumps(
        {"params": got, "timeZone": back.get("timeZone"),
         "sensors_on": [i for i, sc in enumerate(back.get("sensorConfigs", [])) if sc.get("enabled")]})
    print(f"     PUT -> HTTP {st}; read-back {detail}")
    return ok


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(2)
    c = _creds(); tok = _login(c); cid = c.get("company_id")
    dry = "--dry-run" in args
    hours = float(args[args.index("--hours") + 1]) if "--hours" in args else 8
    initial = int(args[args.index("--initial-litres") + 1]) if "--initial-litres" in args else None

    if "--all-spares" in args:
        nums = [d["device_number"] for d in _sql(
            "SELECT device_number FROM devices WHERE property_id IS NULL AND is_active "
            "AND device_number <> 'DEMO0001' ORDER BY device_number")]
    else:
        nums = [args[0]]

    if "--show" in args:
        print(f"ThingsLog sensor config — {len(nums)} device(s)\n")
        for n in nums:
            show(tok, cid, n)
        sys.exit(0)

    if "--standard" in args:
        print(f"Applying the standard to {len(nums)} device(s){' [DRY RUN]' if dry else ''}\n"
              f"  fitted meters  : pulse rate from the CRM, register digits left alone, "
              f"fraction 3, {hours:g}h call-in, Canary time, sensor enables from the CRM\n"
              f"  unfitted stock : fleet defaults ({STD['pulse_coef']} = 1 L/pulse, "
              f"{STD['digits']} digits, 3 dp)\n")
        fails = [n for n in nums if not apply_standard(tok, cid, n, initial, hours, dry)]
        print(f"\n{len(nums)-len(fails)} ok, {len(fails)} failed"
              + (f": {', '.join(fails)}" if fails else ""))
        sys.exit(1 if fails else 0)

    print(__doc__); sys.exit(2)
