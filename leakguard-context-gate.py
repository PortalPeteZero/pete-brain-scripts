#!/usr/bin/env python3
"""leakguard-context-gate.py -- PreToolUse hook: no LeakGuard tool runs, and no LeakGuard code is
edited, until the session has read the LeakGuard brief.

BORN 27 Jul 2026. Pete: "every time we work on leakguard it's a disaster, you never remember how it
works or the current SOP, it's a fucking disgrace."

He is right and it is not a memory problem. The front door, the multi-output SOP and the
verify-before-claiming SOP all exist and are all good. Nothing MAKES a session read them first, so
every session rediscovers the same ground halfway through, after the time is already spent. In that
one session:
  * the front door was opened only when code was about to be written, hours in;
  * status was reported from the fix plan instead of the live systems, and the plan was wrong -- it
    claimed five workstreams complete when 35 items were not done;
  * thingslog-api.py was called with a device number where a path belongs, and the resulting DNS
    error was reported to Pete as ThingsLog being unreachable;
  * a paginated endpoint was read as a whole fleet, three times over, in tools that had been in use
    for weeks.

Modelled on engine-contract-gate.py, which fixed the identical class of failure for the Triage and
Enquiry engines on 10 Jul.

Scope -- deliberately narrow:
  * Only EXECUTION blocks: a python invocation of a LeakGuard tool, or a write into /tmp/lg-hub.
    READING anything always passes -- reading is the remedy, never the offence.
  * lg-brief.py itself always passes (it IS the unlock).
  * Read-only queries through cc-sql.py / lg-sql.py pass, so a session can look before it leaps.
  * Everything non-LeakGuard passes untouched.
  * FAIL-OPEN on any internal error. A guard bug must never brick a session.

Exit contract (PreToolUse): exit 2 + stderr => BLOCK; exit 0 => allow.
"""
import json, os, re, subprocess, sys, time

VAULT = os.environ.get("VAULT", "/tmp/pbs")

_SID = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
MARKER = f"/tmp/.leakguard-brief-ack-{_SID}" if _SID else "/tmp/.leakguard-brief-ack"
FRESH_SECS = 6 * 3600

# An EXECUTION of a LeakGuard tool. `head`/`cat`/`grep`/`sed` of the same file does not match,
# because there is no python token in front of it.
# Anchored the same way as _DEVICE_WRITE_RE below (29 Jul 2026) — see the note there. Grepping a
# document that quotes one of these commands is reading, not running.
_LG_EXEC_RE = re.compile(
    r"(?:^|[\n;&|]\s*)(?:\w+=\S*\s+)*python3?\s+(?:[^\n;|&]*?/)?"
    r"(lg-(?:verify|crosscheck|device-config|sql)"
    r"|leakguard-(?:name-sync)"
    r"|thingslog-api)\.py\b"
)

# A WRITE into the LeakGuard repo working copy. Reading it is fine.
# NARROW ON PURPOSE. The first cut matched `/tmp/lg-hub[^\n]*?(?:>|>>)`, so a plain
# `cd /tmp/lg-hub 2>/dev/null && git rev-parse` was read as a write and blocked — a pure read,
# refused. Caught within a minute of the gate going live, on the very command written to inspect the
# repo. A guard that blocks reading teaches people to work around it, which is worse than no guard,
# so this now names the write verbs rather than guessing from punctuation.
_LG_REPO_WRITE_RE = re.compile(
    r"git\s+(?:commit|push|add|checkout|reset|merge|rebase)\b[^\n]*?/tmp/lg-hub"
    r"|cd\s+/tmp/lg-hub[^\n]*?git\s+(?:commit|push|add|checkout|reset|merge|rebase)\b"
    r"|(?:\btee\b|\bcp\b|\bmv\b|\brm\b)[^\n]*?/tmp/lg-hub"
    r"|(?:^|[;&|]\s*)[^\n]*?>{1,2}\s*/tmp/lg-hub"
    r"|bunx\s+supabase\s+functions\s+deploy"
)

_UNLOCK_RE = re.compile(r"lg-brief\.py")

# lg-sql.py is on the tool list because a WRITE through it is a live database change. A plain SELECT
# is how you find things out, so it is allowed through unread -- the point of the gate is to stop
# blind ACTION, not blind curiosity.
_LG_SQL_RE = re.compile(r"python3?\s+(?:[^\n;|&]*?/)?lg-sql\.py\b")
_MUTATING_SQL_RE = re.compile(
    r"\b(insert\s+into|update\s+\w|delete\s+from|drop\s+|alter\s+|create\s+|truncate\s+|grant\s+|revoke\s+)",
    re.I,
)

MESSAGE = """⛔ LEAKGUARD CONTEXT GATE — the session brief has not been read.

This is not a formality. Every LeakGuard session so far has rediscovered the same ground halfway
through, after the time was spent, because the front door and the SOP exist and nothing made anyone
open them first. Pete's words, 27 Jul 2026: "every time we work on leakguard it's a disaster, you
never remember how it works or the current SOP."

Run this, read it, and the tools unlock for this session:

    VAULT=/tmp/pbs python3 /tmp/pbs/lg-brief.py --ack

It pulls the live fleet state, the outstanding audit findings, the pending alerts and the known-open
list, and it names the notes you must read in full before writing code. Nothing in it is quoted from
a plan document.

It also RECONCILES THINGSLOG AGAINST THE CRM — counters, pulse rate, map location, device name —
and it will not unlock anything if ThingsLog could not be reached. That is deliberate. Pete, 28 Jul
2026: "half of the problem is you always rely on our CRM and never check ThingsLog." Reading the
copy is one command; reading the record takes a login, so the copy wins and gets quoted. Every
correction that day came from exactly that. If ThingsLog is genuinely down, say so and do not act on
the CRM alone.

READING is never blocked. Look at anything you like — cat, grep, a SELECT through lg-sql.py — this
gate only stops ACTION taken before the context was loaded."""


def fresh():
    """Recent AND proving ThingsLog was actually read.

    The marker used to be a bare timestamp, so "briefed" meant "ran a command", not "saw the system
    of record". Pete, 28 Jul 2026: "I need a gate to make you read ThingsLog as well as the CRM."
    lg-brief.py now writes {"thingslog_reached": true} only after reconciling the two live, and
    refuses to write anything at all when ThingsLog cannot be reached.

    A pre-JSON marker left over from an older session is treated as NOT briefed — it cannot prove
    the reconciliation happened, and assuming in the gate's favour is how the gate becomes theatre.
    """
    try:
        if (time.time() - os.path.getmtime(MARKER)) >= FRESH_SECS:
            return False
        with open(MARKER) as fh:
            mk = json.load(fh)
        # Two independent claims, both required. ThingsLog was actually read (not just our copy),
        # and the SOPs were PRINTED IN FULL into the session rather than pointed at. Pete, 28 Jul
        # 2026: "can we gate it so future you must read all" — a list of filenames is a pointer, and
        # pointers are what has failed here every time.
        return mk.get("thingslog_reached") is True and bool(mk.get("sops_read"))
    except (OSError, ValueError):
        return False


# ── THE LIVE-CUSTOMER WRITE GUARD ────────────────────────────────────────────────────────────────
# Pete, 28 Jul 2026: "you changed michelle johns without my go ahead and now you have fucked up her
# call ins which she relies on."
#
# He was right. Michelle had called in six times a day for eleven days because she had asked for it.
# I swept her onto the fleet standard alongside two genuine diagnostics, without asking, and she lost
# exactly the three call-ins she needed. Recording a reason on her interval fixed HER. This fixes the
# class: no write reaches a device that belongs to a paying customer unless the command says why.
#
# It is not a permission system — there is nobody to ask at 2am. It is a forcing function: you cannot
# make the change without writing down who asked for it, and the reason is kept, so the next session
# finds a decision instead of an anomaly to tidy away.

# EVERY alternative below is anchored to a PYTHON INVOCATION, exactly like _LG_EXEC_RE above.
#
# It was not, until 29 Jul 2026: the patterns matched the bare filename anywhere in the command, so
# `cat lg-commission.py`, `grep -n "geocode" lg-commission.py`, `wc -l lg-device-config.py`,
# `git log -- lg-commission.py` and even grepping a DOCUMENT that quotes a `--apply` command were
# all refused as live-customer writes. Measured that day: 8 of 8 plain reads blocked, 0 of 3 real
# writes missed — the guard was catching everything it should and a great deal it should not.
#
# That is the THIRD time this exact class has been fixed in this one file (see _LG_EXEC_RE's
# comment, and the /tmp/lg-hub note below). The rule, stated once: a guard keys on the ACT, never on
# the NAME of the thing acted upon. Reading is never the offence — it is the remedy.
# A python invocation must stand at the START of the command or of a new segment (after ; && ||),
# optionally behind VAR=value assignments. Without that anchor the text merely has to APPEAR, so
# `grep -rn 'python3 leakguard-name-sync.py --apply' skills/` — searching the docs for a command —
# reads as running it. Quoted text is data; only a command position is a command.
_SEG = r"(?:^|[\n;&|]\s*)"
_ENV = r"(?:\w+=\S*\s+)*"
_PY = _SEG + _ENV + r"python3?\s+(?:[^\n;|&]*?/)?"
_DEVICE_WRITE_RE = re.compile(
    # --show and --dry-run write nothing. --dry-run was refused until 29 Jul 2026, which meant the
    # one safe way to preview a config change was harder than making it.
    _PY + r"lg-device-config\.py\b(?![^\n;|&]*(?:--show|--dry-run))"
    r"|" + _PY + r"thingslog-api\.py\s+set-transmission\b"
    r"|" + _PY + r"thingslog-api\.py\s+set-config\b"
    # delete-counters DELETES a device's entire stored history at ThingsLog — it wiped 3,718 live
    # readings on 26 Jul 2026. It carries its own --i-mean-it interlock but was NOT on this list, so
    # the most destructive verb in the toolset was the one nobody had to justify or have recorded.
    r"|" + _PY + r"thingslog-api\.py\s+delete-counters\b"
    # `commands <device> <TYPE>` queues a command (RESET, SEND_CONFIG_OVER_MQTT...) onto a live
    # logger. Listing the queue takes no type and stays free.
    r"|" + _PY + r"thingslog-api\.py\s+commands\s+\S+\s+\S"
    r"|" + _PY + r"leakguard-name-sync\.py\b[^\n;|&]*--apply"
    r"|" + _PY + r"lg-commission\.py\b(?![^\n;|&]*--check)"           # the commissioning WRITE path

    # ── THE BYPASS, closed 31 Jul 2026 ────────────────────────────────────────────────────────
    # Everything above names a SUBCOMMAND. So the guard only ever saw writes that went through a
    # tool's front door, and any other route to the same API was invisible. Three real ones:
    #   - 30 Jul: a session cleared `replacementNumber` on a live customer's logger by importing
    #     thingslog-api in an inline python block. It reached work_log but NOT device_change_log,
    #     which is the record that exists for exactly this. Pete found the gap on 31 Jul.
    #   - 31 Jul: this session renamed 04299212 and set 04327014's location the same way. Both
    #     touched a customer device; neither was guarded or logged.
    #   - a raw `curl -X PUT` at the API needs no helper at all.
    # Enumerating more subcommands would leave the same shape of hole, so these two match on the
    # ACT of writing rather than on a tool name.
    #
    # Deliberately keyed to a mutating CALL (`_put(`, `_post(`, `method="PUT"`, the rename
    # function) and never to a path alone: `m._get(base, tok, f"/api/devices/{n}/config")` is a
    # READ, and reading must stay free — that is the whole design of this gate.
    # Two LOOKAHEADS rather than a sequence, for two reasons learned while testing this:
    #   - they must span newlines, because the import and the write sit on later lines of a
    #     `python3 - <<'PY'` heredoc (a same-line pattern caught none of the three real cases);
    #   - they must not assume ORDER, or a block that calls the write before naming the helper
    #     slips through on a technicality.
    r"|" + _SEG + _ENV + r"python3?\s(?=[\s\S]*?thingslog-api\.py)"
          r"(?=[\s\S]*?(?:_put\(|_post\(|_delete\(|"
          r"method\s*=\s*[\"'](?:PUT|POST|PATCH|DELETE)[\"']))"
    r"|" + _SEG + _ENV + r"python3?\s(?=[\s\S]*?leakguard-name-sync\.py)"
          r"(?=[\s\S]*?set_name_thingslog\()"
    # curl straight at the API, no helper involved.
    r"|" + _SEG + r"curl\b[^\n]*?-X\s*(?:PUT|POST|PATCH|DELETE)[^\n]*?"
          r"(?:thingslog|/api/devices/|/api/v2/devices/)"
)
# A ThingsLog logger number: eight digits, standing on its own.
_DEVNUM_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_REASON_RE = re.compile(r"--reason[=\s]+(\"[^\"]+\"|'[^']+'|\S+)")
# Tables where a row IS a customer's monitoring setup.
#
# The table name must stand in a SQL POSITION — right after UPDATE / INSERT INTO / DELETE FROM.
# It used to match the bare word anywhere in the command, which is the same "quoted text is data"
# flaw the tool patterns above were anchored to avoid, just on the SQL branch instead.
# Caught 31 Jul 2026: a backfill INTO device_change_log was refused because the word `devices`
# appeared inside the URL string "/api/v2/devices/04327014" in a REASON being written down. Three
# innocent fragments of one compound command — an unrelated lg-sql SELECT, an INSERT, and that
# URL — added up to a block on a write that touched no device at all.
# Note this deliberately does NOT read `device_change_log` or `readings` as customer tables: the
# log is the record OF changes, not a device setting, and `\bdevices\b` cannot match inside
# `device_change_log` anyway.
_CUSTOMER_TABLE_RE = re.compile(
    r"\b(?:UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+"
    r"(?:public\.)?(devices|alarm_no_use_windows|device_alarm_config|alarm_contacts)\b", re.I)


def _lg(query):
    """Query the LeakGuard DB. Raises on any failure — the caller must NOT swallow it."""
    out = subprocess.run(["python3", f"{VAULT}/lg-sql.py", query],
                         capture_output=True, text=True, timeout=60,
                         env={**os.environ, "VAULT": VAULT}).stdout
    i = out.find("[")
    if i < 0:
        raise RuntimeError(f"lg-sql returned no result: {out[:200]}")
    return json.loads(out[i:])


def live_customer_write(cmd):
    """(blocked_message, None) if this must be refused, else (None, [rows]) for the devices touched.

    FAILS CLOSED, unlike the rest of this gate. The general rule here is that a guard bug must never
    brick a session, so everything else returns 0 on error. This one is the exception on purpose: if
    we cannot find out whether a device belongs to a customer, we must not write to it. Guessing in
    our own favour is exactly how Michelle's setting got changed.
    """
    if not (_DEVICE_WRITE_RE.search(cmd) or
            (_LG_SQL_RE.search(cmd) and _MUTATING_SQL_RE.search(cmd)
             and _CUSTOMER_TABLE_RE.search(cmd))):
        return None, None

    reason = _REASON_RE.search(cmd)
    targets_all = re.search(r"set-transmission\s+all\b", cmd) is not None
    nums = sorted(set(_DEVNUM_RE.findall(cmd)))

    try:
        if targets_all or not nums:
            rows = _lg("""SELECT d.device_number AS num, c.full_name AS who
                          FROM devices d JOIN properties p ON p.id = d.property_id
                          JOIN customers c ON c.id = p.customer_id
                          WHERE d.tl_output_index = 0""")
            scope = "EVERY installed device" if targets_all else "one or more customer devices"
        else:
            inlist = ",".join(f"'{n}'" for n in nums)
            rows = _lg(f"""SELECT d.device_number AS num, c.full_name AS who
                           FROM devices d JOIN properties p ON p.id = d.property_id
                           JOIN customers c ON c.id = p.customer_id
                           WHERE d.device_number IN ({inlist})""")
            scope = ", ".join(f"{r['num']} ({r['who']})" for r in rows)
    except Exception as e:
        return (f"⛔ LEAKGUARD WRITE GUARD — cannot verify who this device belongs to.\n\n"
                f"The lookup failed: {e}\n\n"
                f"This guard fails CLOSED. A write to a device that might be a live customer's is "
                f"refused when we cannot check, because guessing in our own favour is precisely how "
                f"Michelle Johnson lost her call-ins on 27 Jul 2026."), None

    if not rows:            # spare stock on the shelf — nobody is relying on it
        return None, []
    if reason:
        return None, rows

    return (f"⛔ LEAKGUARD WRITE GUARD — this changes a LIVE customer's logger.\n\n"
            f"Affected: {scope}\n\n"
            f"Somebody is relying on this device's settings, and you have not said who asked for "
            f"the change or why. Michelle Johnson had asked for six call-ins a day; they were "
            f"'standardised' away on 27 Jul 2026 without a word written down, and she lost the "
            f"three she needed.\n\n"
            f"Add a reason and the change goes through and is recorded:\n\n"
            f"    <your command> --reason \"Jane relayed Michelle's request for 6am/2pm/10pm\"\n\n"
            f"If nobody has asked for it, do not make it. A device that merely looks non-standard "
            f"is not a fault — check WHY it is set that way first (devices.callin_interval_reason, "
            f"device_change_log)."), None


def record_change(cmd, rows, session_id):
    """Best-effort audit row. Never blocks the change it is describing."""
    try:
        reason = _REASON_RE.search(cmd)
        reason = reason.group(1).strip("\"'") if reason else ""
        for r in (rows or [{"num": None, "who": None}]):
            _lg("INSERT INTO device_change_log (device_number, customer_name, command, reason, "
                "session_id, tool) VALUES (" +
                (f"'{r['num']}'" if r.get("num") else "NULL") + "," +
                (f"'{(r.get('who') or '').replace(chr(39), chr(39) * 2)}'" if r.get("who") else "NULL") +
                f",'{cmd[:900].replace(chr(39), chr(39) * 2)}'"
                f",'{reason[:500].replace(chr(39), chr(39) * 2)}'"
                f",'{session_id[:80]}','leakguard-context-gate')")
    except Exception:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        tool = payload.get("tool_name") or payload.get("tool") or ""
        ti = payload.get("tool_input") or {}

        if tool == "Bash":
            cmd = ti.get("command") or ""
        elif tool in ("Write", "Edit", "NotebookEdit"):
            fp = ti.get("file_path") or ""
            cmd = fp if "/tmp/lg-hub" in fp else ""
            if cmd and fresh():
                return 0
            if cmd:
                sys.stderr.write(MESSAGE + f"\n\n(blocked: editing {fp})\n")
                return 2
            return 0
        else:
            return 0

        if _UNLOCK_RE.search(cmd):
            return 0

        # The write guard runs whether or not the brief has been read. Being briefed says you know
        # how the system works; it does not say anybody asked for this change.
        try:
            refusal, rows = live_customer_write(cmd)
        except Exception as e:
            sys.stderr.write(f"⛔ LEAKGUARD WRITE GUARD errored and refuses to guess: {e}\n")
            return 2
        if refusal:
            sys.stderr.write(refusal + "\n")
            return 2
        if rows:
            record_change(cmd, rows, _SID)

        if fresh():
            return 0

        hit = None
        if _LG_SQL_RE.search(cmd):
            # a SELECT is fine; a write is not
            if _MUTATING_SQL_RE.search(cmd):
                hit = "a database change through lg-sql.py"
        if not hit and _LG_EXEC_RE.search(cmd):
            m = _LG_EXEC_RE.search(cmd)
            if not (m.group(1) == "lg-sql" and not _MUTATING_SQL_RE.search(cmd)):
                hit = f"{m.group(1)}.py"
        if not hit and _LG_REPO_WRITE_RE.search(cmd):
            hit = "a write to the LeakGuard repo or an edge-function deploy"

        if hit:
            sys.stderr.write(MESSAGE + f"\n\n(blocked: {hit})\n")
            return 2
        return 0
    except Exception:
        return 0  # fail open, always


if __name__ == "__main__":
    sys.exit(main())
