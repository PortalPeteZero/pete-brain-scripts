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

STD = {"pulse_coef": "0.001", "digits": "8", "fraction": "3"}   # 1 L/pulse, fleet standard
FACTORY = {"pulse_coef": "0.01", "digits": "7", "fraction": "2"}  # 10 L/pulse — never ship this


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


def apply_standard(tok, cid, n, initial_litres=None, hours=8, dry=False):
    cfg = _get(tok, cid, f"/api/devices/{n}/config")
    p = cfg["sensorConfigs"][0]["parameters"]
    before = {k: str(p.get(k)) for k in ("pulse_coef", "digits", "fraction", "initial_counter")}

    p.update(STD)
    if initial_litres is not None:
        p["initial_counter"] = str(int(initial_litres))
    cfg["recordPeriod"] = "MINUTES"
    cfg["every"] = 15
    cfg["countsThreshold"] = int(hours * 60 / 15)
    # deleteOldCounters is an ACTION flag, not a setting. Never let a whole-DTO PUT carry it true.
    cfg["deleteOldCounters"] = False

    want = {**STD, "initial_counter": str(int(initial_litres)) if initial_litres is not None
            else before["initial_counter"]}
    print(f"  {n}  {before}  ->  {want}  call-in {hours}h")
    if dry:
        print("     DRY RUN — nothing written")
        return True

    st, _ = _put(tok, cid, f"/api/devices/{n}/config", cfg)
    chk = _get(tok, cid, f"/api/devices/{n}/config")["sensorConfigs"][0]["parameters"]
    got = {k: str(chk.get(k)) for k in want}
    ok = got == want
    print(f"     PUT -> HTTP {st}; read-back {'VERIFIED' if ok else 'MISMATCH ' + json.dumps(got)}")
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
        print(f"Applying fleet standard (1 L/pulse, 8 digits, 3 dp) to {len(nums)} device(s)"
              f"{' [DRY RUN]' if dry else ''}\n")
        fails = [n for n in nums if not apply_standard(tok, cid, n, initial, hours, dry)]
        print(f"\n{len(nums)-len(fails)} ok, {len(fails)} failed"
              + (f": {', '.join(fails)}" if fails else ""))
        sys.exit(1 if fails else 0)

    print(__doc__); sys.exit(2)
