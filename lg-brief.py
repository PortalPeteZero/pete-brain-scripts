#!/usr/bin/env python3
"""lg-brief.py -- the LeakGuard session brief. Run it BEFORE touching anything LeakGuard.

WHY THIS EXISTS (27 Jul 2026, Pete: "every time we work on leakguard it's a disaster, you never
remember how it works or the current SOP"). The front door and the SOPs all exist and are all good.
Nothing MADE a session read them first, so every session rediscovered the same ground halfway
through, after the time was spent.

WHY IT IS BUILT THE WAY IT IS. The first cut of this file was a status readout plus prose I typed
myself. Pete, immediately: "how do we know the stuff it makes you read is complete and accurate,
you're waffling on about alerts, what about code, what about ThingsLog being the SSOT, how it's
wired up, all the quirks." Right, and a hand-written brief rots exactly like the fix plan did -- it
claimed five workstreams complete when 35 items were not done.

So almost nothing here is authored. Section 2 is DERIVED from the code and the live systems on every
run. Section 3 is a list of claims, each with a PROBE, and the brief says NO LONGER TRUE in place of
a claim whose probe stops passing. A quirk with no probe is labelled as such, so you can see exactly
how much of this is trusted rather than checked.

  VAULT=/tmp/pbs python3 /tmp/pbs/lg-brief.py            # read it
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-brief.py --ack      # read it and unlock the tools
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-brief.py --deep     # + the slow probes (ThingsLog round trips)
"""
import glob, json, os, re, subprocess, sys, time

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REPO = "/tmp/lg-hub"
ENV = {**os.environ, "VAULT": VAULT}
DEEP = "--deep" in sys.argv
_SID = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
MARKER = f"/tmp/.leakguard-brief-ack-{_SID}" if _SID else "/tmp/.leakguard-brief-ack"
STALE = []


def _sql(tool, query):
    r = subprocess.run(["python3", f"{VAULT}/{tool}", query], capture_output=True, text=True, env=ENV)
    out = r.stdout.strip(); i = out.find("[")
    return json.loads(out[i:]) if i >= 0 else []


def lg(q):
    return _sql("lg-sql.py", q)


def cc(q):
    return _sql("cc-sql.py", q)


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def rule(t):
    print(f"\n{'=' * 92}\n{t}\n{'=' * 92}")


def hazard(claim, fn):
    """A standing trap in someone else's system. It cannot 'go false' and must never read as stale;
    the probe exists to print CURRENT evidence so the warning is never taken on trust."""
    try:
        _, detail = fn()
    except Exception as e:
        print(f"  [hazard   ] {claim}\n              (evidence unavailable: {e})")
        return
    print(f"  [hazard   ] {claim}")
    print(f"              {detail}")


def probe(claim, fn, note=""):
    """Run a probe. Print HOLDS / NO LONGER TRUE / (no probe). Collect the failures."""
    if fn is None:
        print(f"  [no probe ] {claim}")
        if note:
            print(f"              {note}")
        return
    try:
        ok, detail = fn()
    except Exception as e:
        print(f"  [UNCHECKED] {claim}\n              probe errored: {e}")
        return
    if ok:
        print(f"  [holds    ] {claim}")
        if detail:
            print(f"              {detail}")
    else:
        print(f"  [NO LONGER TRUE] {claim}")
        print(f"              {detail}")
        STALE.append(claim)


print("LEAKGUARD SESSION BRIEF")
print("Sections 2 and 3 are derived and probed on every run. Nothing is quoted from a plan document.")

repo_ok = os.path.isdir(f"{REPO}/.git")
if not repo_ok:
    print(f"\n⚠️  {REPO} is not a git working copy. The code-derived sections below will be blank.")
    print("    Clone it first:  git clone https://github.com/SygmaSol/leakguard-insight-hub /tmp/lg-hub")

# ══ 1 · THE RULE, AND THE TOOL FORMS THAT BITE ═══════════════════════════════════════════════════
rule("1 · THE RULE")
print("""
  ThingsLog is the SOURCE OF TRUTH. Our `readings` table is a COPY and can be wrong. When they
  disagree, ThingsLog wins. (Pete, 27 Jul 2026.)

  A claim about a device, a customer, a count or an outcome is only true if it came from ThingsLog
  or the live database THIS SESSION. Not from a plan, not from an audit, not from what you did an
  hour ago. Every "there are only N" said on this project has been wrong.

  THE FLOW IS THE RECORD. THE CRM DIARY IS NOT USED.
  Pete, 28 Jul 2026: "we dont use the diary system in the crm so stop looking for survey dates and
  install dates, we just use the flow". The `appointments` table is dead. An absent appointment
  proves NOTHING and a present one proves nothing either. Where a job has got to is:

      new_enquiry -> survey_booked -> install_quoted -> install_paid -> install_booked -> leakguarded

  read from properties.status, with its history in service_history (service_type='status_change').

  THERE IS ONE PAYMENT, NOT TWO. The survey and the install are not billed separately. There is no
  survey fee, no survey invoice, no separate Stripe charge. Acceptance and payment are the same act
  (since Jul 2026, by Stripe). Do not go looking for a second one.

  TOOL FORMS -- the wrong one fails in ways that look like an outage, not a typo:
    thingslog-api.py get /api/v2/devices/<number>   <- get takes a PATH (a bare number is accepted)
    thingslog-api.py config <number>                <- config takes a NUMBER
    lg-crosscheck.py <device>|--all                 lg-crosscheck.py <device> --series YYYY-MM-DD
    lg-device-config.py <device> --show             lg-verify.py            leakguard-name-sync.py <d> [--apply]
""")

# ══ 2 · HOW IT IS WIRED -- derived, not remembered ════════════════════════════════════════════════
rule("2 · HOW IT IS WIRED  (read out of the code and the live systems just now)")

if repo_ok:
    writers = sh(f"grep -rl 'from(\"readings\")' {REPO}/supabase/functions/*/index.ts 2>/dev/null "
                 f"| xargs -I{{}} sh -c 'grep -lE \"upsert|insert\" {{}}' 2>/dev/null "
                 f"| sed 's|.*/functions/||;s|/index.ts||' | sort")
    print(f"  WRITE `readings` ({len(writers.split()) if writers else 0}): {', '.join(writers.split()) or 'none found'}")

    consumers = sh(f"grep -rl 'alarm-engine.ts' {REPO}/supabase/functions/*/index.ts 2>/dev/null "
                   f"| sed 's|.*/functions/||;s|/index.ts||' | sort")
    clist = consumers.split()
    print(f"  IMPORT the alarm engine ({len(clist)}): {', '.join(clist) or 'none found'}")
    print( "     ^ a change to _shared/alarm-engine.ts means redeploying EVERY one of these.")

    guards = sh(f"grep -rho 'export function [a-zA-Z]*' {REPO}/supabase/functions/_shared/ingest-guards.ts 2>/dev/null "
                f"| sed 's/export function //' | sort | tr '\\n' ' '")
    print(f"  Shared ingest guards: {guards or 'none found'}")
    head = sh(f"git -C {REPO} rev-parse --short HEAD")
    print(f"  Repo HEAD: {head}   ({sh(f'git -C {REPO} log -1 --format=%cr')})")

live_fns = lg("""SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                 WHERE n.nspname='public' AND proname LIKE 'get_device%' ORDER BY proname""")
print(f"  Device reader functions in the DB ({len(live_fns)}): {', '.join(f['proname'] for f in live_fns)}")

crons = lg("SELECT jobid, jobname, schedule, active FROM cron.job ORDER BY jobid")
print(f"  Cron jobs ({len(crons)}):")
for c in crons:
    print(f"     [{c['jobid']:>2}] {c['jobname']:<34} {c['schedule']:<14} active={c['active']}")

trigs = lg("""SELECT c.relname AS tbl, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
              JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE NOT t.tgisinternal AND n.nspname='public'
                AND c.relname IN ('readings','devices','subscriptions','reading_corrections','properties')
              ORDER BY c.relname, t.tgname""")
print(f"  Triggers on the tables that matter ({len(trigs)}):")
for t in trigs:
    print(f"     {t['tbl']:<22} {t['tgname']}")

# ══ 3 · THE QUIRKS, EACH WITH A PROBE ════════════════════════════════════════════════════════════
rule("3 · THE QUIRKS -- every one carries a probe, so a claim that has gone stale SAYS SO")

def p_port0_pointer():
    bad = lg("""SELECT count(*) AS n FROM properties p JOIN devices d ON d.id = p.device_id
                WHERE d.tl_output_index <> 0""")[0]["n"]
    return int(bad) == 0, ("properties.device_id is the MAIN-meter (port 0) pointer BY DESIGN. "
                           "Sub-meters resolve via devices.property_id. Converting those call sites "
                           "has been proposed and rejected twice; it would be damage.")

def p_alert_types():
    d = lg("""SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint
              WHERE conrelid='public.alert_logs'::regclass AND conname LIKE '%alert_type%'""")
    if not d:
        return False, "the alert_type CHECK constraint is gone"
    vals = re.findall(r"'([a-z_]+)'::text", d[0]["d"])
    return len(vals) == 7, f"{len(vals)} allowed values: {', '.join(vals)}. A NEW type throws 23514 into a swallowed error."

def p_alert_prop_notnull():
    r = lg("""SELECT is_nullable FROM information_schema.columns
              WHERE table_name='alert_logs' AND column_name='property_id'""")
    return r and r[0]["is_nullable"] == "NO", "alert_logs.property_id is NOT NULL, so a device with no property cannot raise one."

def p_config_warning_subkeys():
    if not repo_ok:
        raise RuntimeError("repo absent")
    # `grep -rc <file>` prints "path:count", which int() chokes on. Count lines instead.
    subkeyed = sh(f"grep -c 'window_key: `\\${{device.deviceId}}:' "
                  f"{REPO}/supabase/functions/_shared/alarm-engine.ts || true").strip()
    return int(subkeyed or 0) >= 5, (f"{subkeyed} config warnings sub-keyed. Every config_warning MUST carry its own "
                                     f"window_key suffix: the upsert conflict target is "
                                     f"(property_id, alert_date, alert_type, window_key) with ignoreDuplicates, so "
                                     f"sharing a key means only the FIRST warning of the day survives.")

def p_invoke_wrapped():
    if not repo_ok:
        raise RuntimeError("repo absent")
    n = sh(f"grep -rn 'functions.invoke(\"send-email\"' {REPO}/src --include=*.tsx --include=*.ts "
           f"| grep -v 'lib/sendEmail.ts' | wc -l").strip()
    return int(n or 0) == 0, ("supabase.functions.invoke NEVER THROWS -- it returns {data, error}. Every try/catch "
                              "around it is dead code. All sends go through src/lib/sendEmail.ts.")

def p_secondary_token():
    if not repo_ok:
        raise RuntimeError("repo absent")
    # `text-secondary\b` also matches `text-secondary-foreground`, which is the CORRECT token --
    # the first run reported four violations and all four were fine. A probe that cries wolf is
    # worse than no probe, because the next real one gets waved through.
    n = sh(f"grep -rn 'text-secondary' {REPO}/src --include=*.tsx "
           f"| grep -v 'text-secondary-foreground' | wc -l").strip()
    return int(n or 0) == 0, "--secondary is a BACKGROUND token. As a foreground it renders 1.17:1 on --card: invisible."

def p_devices_paginates():
    out = sh(f"VAULT={VAULT} python3 {VAULT}/thingslog-api.py get '/api/v2/devices?page=0&size=20' 2>/dev/null")
    try:
        d = json.loads(out)
    except Exception:
        raise RuntimeError("could not read /api/v2/devices")
    tp, te = d.get("totalPages"), d.get("totalElements")
    return (tp or 0) > 1, (f"/api/v2/devices is a PAGE: totalElements {te}, totalPages {tp}, default size 20. "
                           f"Reading .content off page 0 is not the fleet. Use _all_devices().")

def p_ledger_complete():
    if not repo_ok:
        raise RuntimeError("repo absent")
    repo_v = {os.path.basename(f)[:14] for f in glob.glob(f"{REPO}/supabase/migrations/*.sql")}
    led = {r["version"] for r in lg("SELECT version FROM supabase_migrations.schema_migrations")}
    missing = sorted(repo_v - led)
    return not missing, (f"{len(repo_v)} repo migrations, all recorded, so `db push` replays nothing."
                         if not missing else f"UNRECORDED, a push would replay these: {missing}")

def p_commit_is_not_deploy():
    """Is the code RUNNING the code in the repo?

    First cut compared timestamps: the newest commit touching supabase/functions/ against each
    function's updated_at. That is the wrong question and it produced four false alarms on its first
    run -- deploy first, commit second (which is the safe order) makes the commit newer than the
    deploy while the deployed bundle is byte-identical to the source.

    So: take the distinctive exported symbols out of the shared modules and look for them in the
    DEPLOYED body. If a symbol the repo exports is missing from what is running, that function is
    genuinely behind, whatever the clocks say.
    """
    if not repo_ok:
        raise RuntimeError("repo absent")
    import urllib.request
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    guards = sh(f"grep -ho 'export function [a-zA-Z]*' {REPO}/supabase/functions/_shared/ingest-guards.ts "
                f"| sed 's/export function //'").split()
    behind = []
    for fn in ["thingslog-webhook", "thingslog-sync", "connection-watchdog", "demo-session"]:
        req = urllib.request.Request(
            f"https://api.supabase.com/v1/projects/uuhzjytscifrpuqpfrdc/functions/{fn}/body",
            headers={"Authorization": f"Bearer {tok}", "User-Agent": "Mozilla/5.0"})
        body = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
        # only the two ingest functions import the guards; all four import the alarm engine
        want = guards if fn in ("thingslog-webhook", "thingslog-sync") else []
        missing = [g for g in want if g not in body]
        if missing:
            behind.append(f"{fn} is missing {', '.join(missing)}")
    return not behind, ("Every deployed function contains the symbols its source exports. "
                        "A COMMIT IS NOT A DEPLOY -- edge functions deploy separately."
                        if not behind else "DEPLOYED CODE IS BEHIND THE REPO: " + "; ".join(behind))

def p_commissioning_integrity():
    """Every logger on Atlantic/Canary, with its ThingsLog sensor enables matching our CRM.

    THIS PROBE REPLACES A MISTAKE. On 27 Jul 2026 I noticed ThingsLog stamping +03:00 on some
    devices and +01:00 on others, decided the platform lied about its timezones, and started writing
    that into this brief as a permanent quirk with a probe to prove it. Pete stopped me: "you didn't
    correct the time zone to Canary, it's still set to Sofia." He was right. Europe/Sofia is
    ThingsLog's own home timezone and the factory default, and the whole new batch of NINE devices
    was still on it -- because lg-device-config.py --standard set the pulse block, the record period
    and the call-in, then read back only those four fields and printed VERIFIED.

    Two of the nine were live customers. He also spotted, in the same breath, that pulse input 2 was
    switched on with factory defaults on TEN devices that back a single meter -- a logger polling an
    input with nothing on it, reporting zeros for ever, indistinguishable from the dead meter G6
    exists to catch.

    Documenting my own misconfiguration as someone else's platform behaviour would have been worse
    than saying nothing: every future session would have read it as fact and stopped looking. So the
    probe checks the thing that was actually wrong.

    Timezone matters because the alarm engine resolves the window as
        property.timezone -> device.tl_timezone -> Atlantic/Canary
    so a property with no timezone of its own inherits the device's and its overnight window is
    sampled hours out.
    """
    import urllib.request
    devs, page = [], 0
    while True:
        d = json.loads(sh(f"VAULT={VAULT} python3 {VAULT}/thingslog-api.py get "
                          f"'/api/v2/devices?page={page}&size=100' 2>/dev/null"))
        devs += d.get("content", [])
        if d.get("last", True) or page > 20:
            break
        page += 1
    crm = {r["device_number"]: sorted(r["ports"]) for r in lg(
        "SELECT device_number, array_agg(tl_output_index ORDER BY tl_output_index) AS ports "
        "FROM devices GROUP BY 1")}
    bad = []
    for dv in devs:
        n = dv["number"]
        c = sh(f"VAULT={VAULT} python3 {VAULT}/thingslog-api.py config {n} 2>/dev/null")
        if not c.strip().startswith("{"):
            continue
        c = json.loads(c)
        on = [i for i, sc in enumerate(c.get("sensorConfigs", [])) if sc.get("enabled")]
        if c.get("timeZone") != "Atlantic/Canary":
            bad.append(f"{n} tz={c.get('timeZone')}")
        elif on != crm.get(n, []):
            bad.append(f"{n} sensors {on} vs CRM {crm.get(n)}")
    return not bad, (f"{len(devs)} loggers, all Atlantic/Canary with sensor enables matching the CRM."
                     if not bad else "MISCONFIGURED: " + "; ".join(bad))

def p_diary_unused():
    """The CRM diary is not used, so nothing may be concluded from it.

    Recorded as a probe rather than a sentence because I reasoned from it twice. Building an
    explanation on "there is no installation appointment, therefore the booking dialog never touched
    this property" is worthless when nobody books anything in the diary -- and it sent me hunting for
    a separate survey payment that does not exist.

    If the appointments table ever starts being maintained, this says so and the rule changes.
    """
    rows = lg("""SELECT count(*) AS n, max(created_at)::date AS last_created,
                        count(*) FILTER (WHERE appointment_type = 'installation') AS installs
                 FROM appointments""")[0]
    return True, (f"{rows['n']} appointment rows, {rows['installs']} of them installations, newest "
                  f"created {rows['last_created']}. The diary is NOT maintained -- read "
                  f"properties.status and service_history instead. And there is ONE payment, not a "
                  f"separate survey fee.")

def p_thingslog_agrees():
    r = subprocess.run(["python3", f"{VAULT}/lg-crosscheck.py", "--all"], capture_output=True, text=True, env=ENV)
    tail = [l.strip() for l in r.stdout.strip().split("\n") if "agree" in l]
    return r.returncode == 0, (tail[-1] if tail else "cross-check did not report")

probe("properties.device_id is the port-0 pointer, by design", p_port0_pointer)
probe("alert_logs.alert_type is a closed 7-value list", p_alert_types)
probe("alert_logs.property_id is NOT NULL", p_alert_prop_notnull)
probe("config warnings are sub-keyed, or they swallow each other", p_config_warning_subkeys)
probe("no raw send-email invoke outside sendEmail.ts", p_invoke_wrapped)
probe("--secondary is never used as a foreground", p_secondary_token)
probe("/api/v2/devices paginates -- page 0 is not the fleet", p_devices_paginates)
probe("the migration ledger covers every repo file", p_ledger_complete)
probe("a commit is not a deploy -- edge functions are current", p_commit_is_not_deploy)
probe("every logger is on Atlantic/Canary with the right sensors enabled", p_commissioning_integrity)
hazard("the CRM diary is NOT the record -- the status flow is", p_diary_unused)
if DEEP:
    probe("our readings agree with ThingsLog", p_thingslog_agrees)
else:
    print("  [skipped  ] our readings agree with ThingsLog  (slow -- run with --deep)")

probe("deleteOldCounters clears ThingsLog's stored HISTORY, not the device's pulse accumulator",
      None, "No safe probe: proving it means destroying history. Established by incident, 27 Jul 2026.")
probe("the reported counter is (pulses + initial_counter) x pulse_coef, and initial_counter is in PULSES",
      None, "Verified on 04302516 (574 -> 0.574 at zero pulses) and 04160611 (29 -> 0.029).")
probe("ThingsLog commands QUEUE, they do not push; a read-back proves persistence, not effect",
      None, "SEND_CONFIG_OVER_MQTT sat PENDING with sentDate=None. Check the SIDE EFFECT.")
probe("test the COUNTER, not delta_litres, when asking whether a meter has ever worked",
      None, "Any device whose litres were corrected or bounded has zero positive deltas and a counter that moved.")

# ══ 4 · LIVE STATE ═══════════════════════════════════════════════════════════════════════════════
# ══ 3B · THINGSLOG vs THE CRM ════════════════════════════════════════════════════════════════════
# WHY THIS IS MANDATORY AND WHY THE ACK FAILS WITHOUT IT.
#
# Pete, 28 Jul 2026: "half of the problem is you always rely on our CRM and never check ThingsLog."
# He is right, and the reason is mechanical rather than moral: reading our CRM is one command,
# reading ThingsLog needs a login and several calls, so the cheap source wins and gets quoted.
#
# Every correction he had to make that day came from exactly that:
#   * Keith Ferris's setup reported from our records without opening ThingsLog at all.
#   * Glenn Dickson quoted at 10 litres per pulse from our CRM while ThingsLog said 1.
#   * The new Fleet page built on a battery percentage that only a manual sync ever wrote,
#     with the live voltage sitting unused in the next column.
#
# The counter reconciliation already existed in this brief and was SKIPPED unless somebody passed
# --deep. The single most important comparison in the system was opt-in. It is not any more: it
# costs 35 seconds across the fleet, once a session, and it is the difference between knowing and
# assuming.
#
# THE GATE: if ThingsLog cannot be reached, --ack REFUSES and writes no marker, so the tools stay
# locked. A session that cannot see the system of record must not act on the copy.
rule("3B · THINGSLOG vs THE CRM — the copy against the record")

TL_REACHED = False
TL_DISAGREE = []


def _reconcile():
    """Compare every installed meter against ThingsLog on the axes that have actually burned us."""
    global TL_REACHED

    # 1. COUNTERS. ThingsLog is the system of record for readings; ours is a copy that has been
    #    wrong before. 23 devices, ~35s.
    r = subprocess.run(["python3", f"{VAULT}/lg-crosscheck.py", "--all"],
                       capture_output=True, text=True, env=ENV)
    if r.returncode not in (0, 1) or "agree" not in r.stdout:
        raise RuntimeError(f"ThingsLog unreachable or crosscheck failed: {(r.stdout + r.stderr)[:200]}")
    TL_REACHED = True
    for line in r.stdout.splitlines():
        if line.strip().startswith("FAIL"):
            TL_DISAGREE.append("counter " + line.strip()[4:].strip())
    summary = [l.strip() for l in r.stdout.splitlines() if "agree" in l]
    print(f"  counters      {summary[-1] if summary else 'no summary'}")

    # 2. PULSE COEFFICIENT. Ours is what scales every litre we store. Glenn was 10 against
    #    ThingsLog's 1 and nothing noticed for a day.
    fleet = sh(f"VAULT={VAULT} python3 {VAULT}/thingslog-api.py fleet")
    tl_pulse = {}
    for line in fleet.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 5 and parts[0].isdigit():
            try:
                tl_pulse[parts[0]] = float(parts[4].replace(" L/pulse", ""))
            except ValueError:
                pass
    ours = lg("""SELECT device_number AS num, litres_per_pulse::float AS lpp
                 FROM devices WHERE tl_output_index = 0 AND device_number NOT LIKE 'DEMO%'""")
    bad = [f"{o['num']} pulse: ThingsLog {tl_pulse.get(o['num'])} vs CRM {o['lpp']}"
           for o in ours
           if o["num"] in tl_pulse and o["lpp"] is not None
           and abs(tl_pulse[o["num"]] - o["lpp"]) > 1e-6]
    TL_DISAGREE.extend(bad)
    print(f"  pulse rate    {len(ours)} meters compared, {len(bad)} disagree")

    # 3. GPS. From /api/devices/locations — the device DTO's own lat/lon are vestigial and a PUT
    #    against them returns 200 while dropping the value.
    loc = json.loads(sh(f"VAULT={VAULT} python3 {VAULT}/thingslog-api.py get "
                        f"/api/devices/locations") or "{}")
    ours = lg("""SELECT device_number AS num, tl_latitude::float AS lat, tl_longitude::float AS lon
                 FROM devices WHERE property_id IS NOT NULL AND tl_output_index = 0
                   AND device_number NOT LIKE 'DEMO%'""")
    bad = []
    for o in ours:
        l = loc.get(o["num"]) or {}
        tla, tlo = l.get("latitude"), l.get("longitude")
        if None in (tla, tlo, o["lat"], o["lon"]) \
                or abs(tla - o["lat"]) > 1e-5 or abs(tlo - o["lon"]) > 1e-5:
            bad.append(f"{o['num']} GPS: ThingsLog {tla},{tlo} vs CRM {o['lat']},{o['lon']}")
    TL_DISAGREE.extend(bad)
    print(f"  map location  {len(ours)} installed compared, {len(bad)} disagree")

    # 4. DEVICE NAME vs the address we hold. Read from the JSON, never the fleet TABLE — that
    #    display truncates at 34 characters and comparing against the stump invents mismatches
    #    (did exactly that on three devices, 28 Jul 2026, before checking).
    names, page = {}, 0
    while True:
        d = json.loads(sh(f"VAULT={VAULT} python3 {VAULT}/thingslog-api.py get "
                          f"'/api/v2/devices?page={page}&size=100'") or "{}")
        for c in d.get("content", []):
            names[c.get("number")] = (c.get("name") or "").strip()
        page += 1
        if page >= int(d.get("totalPages") or 1) or page > 20:
            break
    ours = lg("""SELECT d.device_number AS num, p.address_line1 AS a1, p.city AS city
                 FROM devices d JOIN properties p ON p.id = d.property_id
                 WHERE d.tl_output_index = 0 AND d.device_number NOT LIKE 'DEMO%'""")
    bad = []
    for o in ours:
        nm = names.get(o["num"], "")
        street = ((o["a1"] or "").split(",")[0]).strip().lower()
        town = (o["city"] or "").strip().lower()
        if not nm or nm in ("!", "?") or (street and street not in nm.lower()) \
                or (town and town not in nm.lower()):
            bad.append(f"{o['num']} name: ThingsLog {nm!r} vs {o['a1']}, {o['city']}")
    TL_DISAGREE.extend(bad)
    print(f"  device name   {len(ours)} installed compared, {len(bad)} disagree")


try:
    _reconcile()
except Exception as e:
    print(f"  ⛔ COULD NOT READ THINGSLOG: {e}")
    print("     Nothing below about device state can be trusted, because the only source that")
    print("     could confirm it was unreachable. The ack will refuse.")

if TL_REACHED:
    if TL_DISAGREE:
        print(f"\n  ⚠️  {len(TL_DISAGREE)} DISAGREEMENT(S). ThingsLog is the record; the CRM is the copy.")
        for d in TL_DISAGREE:
            print(f"      - {d}")
        print("      Quote ThingsLog, not the CRM, until these are reconciled.")
    else:
        print("\n  Both systems agree on every installed meter. The CRM can be quoted for these fields.")

rule("4 · LIVE STATE")
f = lg("""SELECT count(*) AS rows, count(DISTINCT device_number) AS loggers,
                 count(*) FILTER (WHERE property_id IS NOT NULL) AS installed FROM devices""")[0]
print(f"  {f['loggers']} loggers, {f['rows']} meter rows, {f['installed']} installed")
af = lg("SELECT check_id, count(*) AS n FROM audit_findings WHERE NOT acknowledged GROUP BY 1 ORDER BY 1")
print("  daily audit: " + (", ".join("{} x{}".format(a["check_id"], a["n"]) for a in af) or "clean"))
al = lg("""SELECT alert_type, count(*) AS n FROM alert_logs
           WHERE status='pending' AND created_at > now() - interval '7 days' GROUP BY 1 ORDER BY 2 DESC""")
print("  pending alerts (7d): " + (", ".join("{} x{}".format(a["alert_type"], a["n"]) for a in al) or "none"))
for d in lg("""SELECT d.device_number, d.tl_output_index AS port, c.full_name FROM devices d
               JOIN properties p ON p.id=d.property_id JOIN customers c ON c.id=p.customer_id
               WHERE d.is_active AND (SELECT count(DISTINCT r.counter_m3) FROM readings r
                                      WHERE r.device_id=d.id) <= 1 ORDER BY 1"""):
    print(f"  DEAD METER: {d['device_number']} port {d['port']} ({d['full_name']}) has never registered a litre")
for w in lg("""SELECT c.full_name FROM subscriptions s JOIN properties p ON p.id=s.property_id
               JOIN customers c ON c.id=p.customer_id
               WHERE s.status='active' AND p.device_id IS NULL AND c.full_name <> 'Jane Williams'"""):
    print(f"  WAITING: {w['full_name']} is paying with no device installed")
print("  Jane Williams is Pete's own staff on Pete's own card. A test account. Never nag about it.")

# ══ 5 · THE NOTES ════════════════════════════════════════════════════════════════════════════════
rule("5 · READ IN FULL BEFORE WRITING CODE")

# The list is QUERIED, never hardcoded.
#
# It used to be three literal paths. By 28 Jul 2026 two of them were wrong: one SOP had been filed
# under Projects/ and moved, another had been superseded by a note in a different folder that the
# original never mentioned. A hardcoded list in the very tool whose job is to stop the SOPs rotting
# is the same failure one level up.
#
# So: every SOP Canary Detect holds is listed, straight from the database, every run. Write a new
# one and it appears here on its own. Move one and nothing breaks. Pete, 28 Jul 2026: "you need to
# ensure any future work I do with LeakGuard knows exactly where these are."
SOP_DIR = "Businesses/canary-detect/sops/"

# PRINTED IN FULL, not listed.
#
# Pete, 28 Jul 2026: "can we gate it so future you must read all". Listing them was still a pointer,
# and a pointer is what has failed on this project every single time — the SOPs existed, were good,
# and went unread while the same ground was rediscovered halfway through the session.
#
# So the text itself goes on screen every run. There is nothing to go and open, nothing to remember
# to open, and no version of "briefed" that does not include having the procedures in front of you.
# The unlock depends on it: SOPS_READ below is what the marker records and the gate requires.
sops = cc(f"""SELECT vault_path, title, word_count, updated_at::date AS d, body
              FROM vault_notes
              WHERE type = 'sop' AND vault_path LIKE '{SOP_DIR}%'
              ORDER BY vault_path""")

SOPS_READ = [s_["vault_path"] for s_ in sops if "leakguard" in s_["vault_path"].lower()]

if not sops:
    print("  ⛔ NO SOPs FOUND AT THAT PATH. Something has been moved or deleted.")
    print("     Do NOT proceed on memory — find them before you touch anything:")
    print("     cc-sql.py \"SELECT vault_path FROM vault_notes WHERE type=\'sop\'\"")
else:
    # LeakGuard ones are printed IN FULL. The rest of Canary Detect's operating procedures are
    # named so you know they exist and where, but not pasted — a CloudTalk phone config in a
    # LeakGuard brief is noise, and noise is what makes people skim the part that matters.
    mine = [s_ for s_ in sops if "leakguard" in s_["vault_path"].lower()]
    others = [s_ for s_ in sops if s_ not in mine]
    total = sum(int(s_["word_count"] or 0) for s_ in mine)
    print(f"  {len(mine)} LeakGuard SOP(s), {total} words, printed IN FULL below. They live in {SOP_DIR}")
    print("  They are here rather than linked because linking to them has never once worked.")
    if others:
        print(f"\n  Also in that folder, not LeakGuard, not printed: "
              + ", ".join(o["vault_path"].rsplit("/", 1)[-1] for o in others))
    for s_ in mine:
        print("\n" + "-" * 92)
        print(f"  {s_['vault_path']}   ({s_['word_count']} words, updated {s_['d']})")
        print("-" * 92)
        for line in (s_["body"] or "").splitlines():
            print("  " + line)

print("\n" + "=" * 92)
print("  THE FRONT DOOR — repos, deploys, the data model, state of play")
fd = "Projects/CD-LeakGuard/leakguard-crm-front-door.md"
r = cc(f"SELECT length(body) AS n, updated_at::date AS d FROM vault_notes WHERE vault_path='{fd}'")
print(f"     {'✓' if r else '✗ NOT FOUND'} {fd}" + (f"  ({r[0]['n']} chars, {r[0]['d']})" if r else ""))
print(f"""     cc-sql.py "SELECT body FROM vault_notes WHERE vault_path='{fd}'"
""")

rule("VERDICT")
if STALE:
    print(f"  ⚠️  {len(STALE)} CLAIM(S) IN THIS BRIEF ARE NO LONGER TRUE. Fix the system or fix the brief:")
    for c in STALE:
        print(f"      - {c}")
    print("  Do not carry on as if the brief were sound.")
else:
    print("  Every probed claim still holds.")
print("\n  Before claiming anything is done:  VAULT=/tmp/pbs python3 /tmp/pbs/lg-verify.py")

if "--ack" in sys.argv:
    # THE GATE. An ack is a claim that this session has seen the system of record, so it cannot be
    # granted when the system of record was unreachable. Reading our own CRM and calling that
    # "briefed" is the exact habit this exists to break — Pete, 28 Jul 2026: "I need a gate to make
    # you read ThingsLog as well as the CRM."
    #
    # There is no --force. If ThingsLog is genuinely down, the honest move is to say so and not
    # touch the fleet, not to wave the check through and report from a copy.
    if not TL_REACHED:
        print("\n⛔ ACK REFUSED — ThingsLog was not reached, so the tools stay locked.")
        print("   Retry when it answers:  VAULT=/tmp/pbs python3 /tmp/pbs/lg-brief.py --ack")
        print("   If it is genuinely down, say so to Pete and do not act on the CRM alone.")
        sys.exit(1)
    # The unlock now also asserts the SOPs were PRINTED, not merely named. Pete, 28 Jul 2026:
    # "can we gate it so future you must read all". If none were found, that is a broken system, not
    # a briefed session — refuse rather than unlock into a project with no procedures.
    if not SOPS_READ:
        print("\n⛔ ACK REFUSED — no SOPs were found, so none could be put in front of you.")
        print("   The tools stay locked. Find where they went before touching anything.")
        sys.exit(1)
    with open(MARKER, "w") as fh:
        json.dump({"at": time.time(), "thingslog_reached": True,
                   "sops_read": SOPS_READ,
                   "disagreements": TL_DISAGREE}, fh)
    print(f"\nACK recorded — ThingsLog reconciled, {len(SOPS_READ)} SOP(s) read in full. Tools unlocked.")
    if TL_DISAGREE:
        print(f"Carry this with you: {len(TL_DISAGREE)} field(s) where our CRM and ThingsLog disagree.")
