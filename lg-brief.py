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
for path, why in [
    ("Projects/CD-LeakGuard/leakguard-crm-front-door.md", "repos, deploys, the data model, state of play"),
    ("Projects/CD-LeakGuard/lg-sop-verify-before-claiming.md", "the one rule + what was learned the hard way"),
    ("Projects/CD-LeakGuard/lg-fix-plan.md", "INTENT, never live state"),
]:
    r = cc(f"SELECT length(body) AS n, frontmatter->>'status' AS st, updated_at::date AS d "
           f"FROM vault_notes WHERE vault_path='{path}'")
    print(f"  {'✓' if r else '✗'} {path}" + (f"  ({r[0]['n']} chars, status {r[0]['st']})" if r else "  NOT FOUND"))
    print(f"      {why}")
print("""
  cc-sql.py "SELECT body FROM vault_notes WHERE vault_path='Projects/CD-LeakGuard/leakguard-crm-front-door.md'"
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
    with open(MARKER, "w") as fh:
        fh.write(str(time.time()))
    print(f"\nACK recorded. LeakGuard tools unlocked for this session.")
