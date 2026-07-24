#!/usr/bin/env python3
"""
gate_override.py — the override channel for HOOK-type gates, and the review that keeps them honest.

Step 0c of [[plan-rules-that-stop-me]]. **No blocking gate may ship without this.**

THE PROBLEM IT SOLVES
  `ee-lint` has a working override (`lint_overrides` in its payload: a rule id plus why it is
  legitimately fine here) — but that only works because its CALLER controls a JSON payload. A
  PreToolUse hook receives just `tool_name` + `tool_input`. There is nowhere to attach "why this is
  fine", so all four existing guards have NO override at all (verified 24 Jul: zero override
  handling across local-write-guard, engine-contract-gate, capability-probe-guard,
  git-commit-atomic-guard). A gate you cannot legitimately bypass will eventually block real work —
  this plan already recorded local-write-guard firing a false positive on a relative path.

  And Pete's own worry, in his words: *"could that rule stop us building something?"* Yes. This
  session watched an architecture decision reverse twice in one evening; a gate encoding the morning
  version would have blocked the evening one.

THE MECHANISM — a filesystem side-channel, because the hook contract has no room for one
  A session declares an override BEFORE the action, then takes it:

      VAULT=/tmp/pbs python3 /tmp/pbs/gate_override.py grant local-write-guard \
          --reason "restoring 3 rules from the snapshot; the target IS the conduct store" --uses 3

  The grant writes /tmp/pbs/.overrides/<gate>.json — deliberately in /tmp, so it dies with the
  session and can never become a standing exemption. A gate calls `check(gate_key)`; if a grant is
  live it decrements a use, logs to public.gate_overrides, and returns the reason for the record.

  Grants are SINGLE-GATE and USE-CAPPED. There is no "disable all gates".

THE REVIEW HALF — the part that stops this becoming a rubber stamp
  Every taken override is a row in public.gate_overrides. `review` reports gates by override rate:
  **a gate overridden repeatedly is a wrong gate** — either its exceptions are incomplete or it
  should never have been fail-closed. That is the safety valve §3e demands and nothing in the system
  had. Run it in closeout, or when a gate feels like it is in the way.

      VAULT=/tmp/pbs python3 /tmp/pbs/gate_override.py review --days 30

FAIL-OPEN throughout: if this file is broken, gates must behave as if no override exists (i.e. they
block normally) — never as if everything is overridden.
"""
import os, sys, json, time, subprocess, argparse

VAULT = os.environ.get("VAULT", "/tmp/pbs")
ODIR = os.path.join(VAULT, ".overrides")
MAX_TTL = 3600  # a grant is dead after an hour, whatever it says


def _sql(q):
    try:
        r = subprocess.run([sys.executable, f"{VAULT}/cc-sql.py", q],
                           capture_output=True, text=True, timeout=45,
                           env={**os.environ, "VAULT": VAULT})
        return json.loads(r.stdout) if r.stdout.strip().startswith("[") else []
    except Exception:
        return []


def _q(s):
    return (s or "").replace("'", "''")


def _path(gate):
    return os.path.join(ODIR, f"{gate.replace('/', '_')}.json")


def grant(gate, reason, uses=1, ttl=900):
    """Declare an override for ONE gate, before the action. Requires a real reason."""
    if not reason or len(reason.strip()) < 15:
        print("gate_override: refused — a reason of at least 15 characters is required. "
              "'because I need to' is not a reason; say what makes this case legitimate.",
              file=sys.stderr)
        return 2
    os.makedirs(ODIR, exist_ok=True)
    rec = {"gate": gate, "reason": reason.strip(), "uses": max(1, int(uses)),
           "expires": time.time() + min(int(ttl), MAX_TTL)}
    with open(_path(gate), "w") as fh:
        json.dump(rec, fh)
    print(f"gate_override: granted for {gate} — {rec['uses']} use(s), "
          f"{int(min(int(ttl), MAX_TTL)/60)} min. Reason recorded: {reason.strip()[:90]}")
    return 0


def check(gate, context=""):
    """Called BY a gate. Returns the reason string if an override is live, else None.

    Consumes one use and logs the take. Fail-open means: on any error, return None — the gate
    blocks normally. Never the other way round.
    """
    try:
        p = _path(gate)
        if not os.path.exists(p):
            return None
        rec = json.load(open(p))
        if rec.get("expires", 0) < time.time() or rec.get("uses", 0) < 1:
            os.remove(p)
            return None
        rec["uses"] -= 1
        if rec["uses"] < 1:
            os.remove(p)
        else:
            json.dump(rec, open(p, "w"))
        _sql("INSERT INTO gate_overrides (gate_key, reason, context) VALUES "
             f"('{_q(gate)}', '{_q(rec.get('reason',''))}', '{_q(context)[:400]}')")
        return rec.get("reason", "")
    except Exception:
        return None  # fail-open means the GATE still blocks


def review(days=30):
    """A gate overridden repeatedly is a wrong gate. This is the report that says which."""
    rows = _sql(f"""
        SELECT g.key, g.kind, g.status,
               count(o.id) FILTER (WHERE o.taken_at > now() - interval '{int(days)} days') AS overrides,
               max(o.taken_at) AS last_override
        FROM gates g LEFT JOIN gate_overrides o ON o.gate_key = g.key
        WHERE g.status = 'live' GROUP BY g.key, g.kind, g.status ORDER BY overrides DESC, g.key""")
    if not rows:
        print("gate_override review: no gates registered, or the registry is unreachable.")
        return 0
    print(f"gate override review — last {days} days\n")
    flagged = 0
    for r in rows:
        n = int(r.get("overrides") or 0)
        if n >= 3:
            flag = "⚠ OVERRIDDEN REPEATEDLY — its exceptions are wrong, or it should not be fail-closed"
            flagged += 1
        elif n:
            flag = "used occasionally — check the reasons read legitimately"
        else:
            flag = ""
        print(f"  {r['key']:34} {n:>3} override(s)  {flag}")
    print(f"\n{flagged} gate(s) need attention." if flagged
          else "\nNo gate is being overridden habitually.")
    if flagged:
        print("Fix the gate — widen its exceptions or demote it to a warning. Do not normalise the override.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Override channel + review for hook-type gates.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grant", help="declare an override for one gate, before the action")
    g.add_argument("gate")
    g.add_argument("--reason", required=True, help="why THIS case is legitimate (15+ chars)")
    g.add_argument("--uses", type=int, default=1)
    g.add_argument("--ttl", type=int, default=900, help="seconds, capped at 3600")
    c = sub.add_parser("check", help="(for gates) is an override live?")
    c.add_argument("gate")
    c.add_argument("--context", default="")
    r = sub.add_parser("review", help="which gates are being overridden habitually")
    r.add_argument("--days", type=int, default=30)
    a = ap.parse_args()

    if a.cmd == "grant":
        return grant(a.gate, a.reason, a.uses, a.ttl)
    if a.cmd == "check":
        reason = check(a.gate, a.context)
        if reason:
            print(reason)
            return 0
        return 1
    return review(a.days)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"gate_override: {e}", file=sys.stderr)
        sys.exit(1)
