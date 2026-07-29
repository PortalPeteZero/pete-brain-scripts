#!/usr/bin/env python3
"""lg-known.py — the decisions you have already made, so the checks stop re-raising them.

WHY THIS EXISTS (29 Jul 2026). Pete: "its every fucking session, your flagging things we have
dealt with, i need to know why this keeps happening."

The cause was mechanical, not attitudinal. Every LeakGuard check derives its findings from live
state on each run, and there were exactly TWO places a decision could be recorded so a check would
stop repeating itself:

    audit_findings.acknowledged        (the daily audit's G-checks — works, 24 acknowledged)
    devices.callin_interval_reason     (one field, one check, one tool that read it)

Everywhere else — the commissioning audit, the fleet spec diff, the brief's reconciliation, the
truth check — there was NOWHERE for "Pete has seen this and it is fine" to live. So a settled
decision came back as a fresh finding every single session. Two live examples the day this was
written:

  * Three loggers each showed a 135-minute recording gap. That is the KNOWN, DOCUMENTED cost of
    moving them off Europe/Sofia on 27 Jul — two hours of timezone plus one 15-minute interval —
    written down in the commissioning SOP. It was still reported as an unexplained finding.
  * The decision to leave the established installs' monitoring boundaries alone (Pete, 28 Jul)
    existed only as a COMMENT INSIDE ONE TOOL'S SOURCE. No other tool could see it, and a comment
    is not something a check can read.

A row here is the answer, and it is deliberately expensive to write: a reason is required and must
be a real sentence, so the decision travels with its justification instead of being a silent mute.

  VAULT=/tmp/pbs python3 /tmp/pbs/lg-known.py list
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-known.py add <device|ALL> "<finding key>" "<why>" [--until YYYY-MM-DD]
  VAULT=/tmp/pbs python3 /tmp/pbs/lg-known.py remove <device|ALL> "<finding key>"

`finding key` is the check's own label, exactly as the tool prints it. The tools then print the
decision in place of the fault, so it stays VISIBLE — a suppressed finding you can no longer see
is just a different way of losing information.
"""
import json, os, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
ENV = {**os.environ, "VAULT": VAULT}


def _sql(q):
    out = subprocess.run(["python3", f"{VAULT}/lg-sql.py", q],
                         capture_output=True, text=True, env=ENV).stdout
    i = out.find("[")
    if i < 0:
        raise SystemExit(f"lg-known: SQL failed — {out[:300]}")
    return json.loads(out[i:])


def _q(s):
    return str(s).replace("'", "''")


def load():
    """{(scope, finding_key): reason} for every decision still in force.

    Expired rows are dropped here rather than deleted, so a time-boxed decision lapses on its own
    and the finding comes back — which is the point of putting a date on it.
    """
    rows = _sql("""SELECT scope, finding_key, reason, decided_by, decided_on
                   FROM known_findings
                   WHERE expires_on IS NULL OR expires_on >= CURRENT_DATE""")
    return {(r["scope"], r["finding_key"]):
            f"{r['reason']} ({r['decided_by']}, {r['decided_on']})" for r in rows}


def reason_for(known, scope, finding_key):
    """The recorded decision covering this finding, or None.

    Checks the specific scope (a device number) first, then ALL for a fleet-wide decision.
    """
    return known.get((str(scope), finding_key)) or known.get(("ALL", finding_key))


def main():
    args = sys.argv[1:]
    verb = args[0] if args else "list"

    if verb == "list":
        rows = _sql("""SELECT scope, finding_key, reason, decided_by, decided_on, expires_on
                       FROM known_findings ORDER BY scope, finding_key""")
        if not rows:
            print("No recorded decisions. Every finding will be raised on every run.")
            return 0
        print(f"{len(rows)} recorded decision(s) — these are NOT raised as faults:\n")
        for r in rows:
            lapsed = ""
            if r["expires_on"]:
                lapsed = f"  [until {r['expires_on']}]"
            print(f"  {r['scope']:<10} {r['finding_key']}{lapsed}")
            print(f"             {r['reason']}")
            print(f"             — {r['decided_by']}, {r['decided_on']}\n")
        return 0

    if verb == "add":
        if len(args) < 4:
            print(__doc__)
            return 2
        scope, key, reason = args[1], args[2], args[3]
        until = args[args.index("--until") + 1] if "--until" in args else None
        if len(reason.strip()) <= 10:
            print("REFUSED: give a real reason. A one-word mute is how a decision loses the thinking\n"
                  "behind it, and the next session cannot tell a judgement from a shrug.")
            return 2
        _sql(f"""INSERT INTO known_findings (scope, finding_key, reason, expires_on)
                 VALUES ('{_q(scope)}', '{_q(key)}', '{_q(reason)}',
                         {f"'{_q(until)}'" if until else 'NULL'})
                 ON CONFLICT (scope, finding_key) DO UPDATE
                   SET reason = EXCLUDED.reason, expires_on = EXCLUDED.expires_on,
                       decided_on = CURRENT_DATE""")
        print(f"Recorded: {scope} / {key}\n  {reason}")
        return 0

    if verb == "remove":
        if len(args) < 3:
            print(__doc__)
            return 2
        _sql(f"DELETE FROM known_findings WHERE scope='{_q(args[1])}' "
             f"AND finding_key='{_q(args[2])}'")
        print(f"Removed: {args[1]} / {args[2]} — it will be raised again from the next run.")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
