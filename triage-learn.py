#!/usr/bin/env python3
"""triage-learn.py -- the Triage Engine learning aggregator (P5).

Aggregates the decisions ledger per sender/fact and hardens the routing facts:
  - N consistent PETE confirmations (decided_by='pete', not overridden, same final label)
    -> raise the fact's confidence toward 1.0 and refresh decisions_count / last_seen
  - an override spike -> LOWER confidence + flag the fact in the report (never rewrite the
    routing on one bad week -- one override flags, never rewrites)
  - uncovered senders seen >= 3 times in 30d -> PROPOSE a new fact + Mode-A/B filter
    (proposal only -- Pete confirms; filters are never created silently)

NEVER touches auto_file_enabled / auto_send_enabled -- learn may PROPOSE enablement in its
report; only Pete flips those flags. Accuracy counts use decided_by='pete' rows ONLY
(cron-auto rows are volume, never accuracy evidence).

Confidence model (simple, monotone, data-honest):
  confidence = pete_confirms / (pete_confirms + overrides), floored at 0 when no history.

Usage: VAULT=/tmp/pbs python3 /tmp/pbs/triage-learn.py [--apply]
       (dry-run by default -- prints what it would change; --apply writes)
"""
import os, sys

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")

MIN_CONFIRMS = 3
NEW_SENDER_THRESHOLD = 3


def main():
    apply = "--apply" in sys.argv
    report = [f"## triage-learn — {tl.today()} ({'apply' if apply else 'dry-run'})"]

    # per-fact aggregates (pete rows only)
    stats = tl.cc_sql(
        "SELECT fact_id, count(*) AS n, count(*) FILTER (WHERE overridden) AS ov, max(decided_at) AS last "
        "FROM triage_decisions WHERE decided_by='pete' AND fact_id IS NOT NULL GROUP BY fact_id")
    for s in stats:
        n, ov = s["n"], s["ov"]
        conf = round((n - ov) / n, 3) if n else 0
        line = f"fact {s['fact_id'][:8]}…: {n} pete decisions, {ov} overrides → confidence {conf}"
        if ov and ov / n > 0.2:
            line += "  ⚠ override spike — review this fact's routing"
        report.append("- " + line)
        if apply and n >= MIN_CONFIRMS:
            tl.cc_sql("UPDATE triage_routing_facts SET confidence=%s, decisions_count=%d, "
                      "overrides_count=%d, last_seen='%s', source='learned', updated_at=now() "
                      "WHERE id='%s'" % (conf, n, ov, s["last"], s["fact_id"]))

    # uncovered senders
    unc = tl.cc_sql(
        "SELECT sender, count(*) AS n FROM triage_decisions WHERE fact_id IS NULL AND sender IS NOT NULL "
        "AND decided_at >= now() - interval '30 days' GROUP BY sender HAVING count(*) >= %d "
        "ORDER BY count(*) DESC LIMIT 10" % NEW_SENDER_THRESHOLD)
    for u in unc:
        dom = u["sender"].split("@")[-1]
        report.append(f"- PROPOSAL: {u['n']}x from uncovered sender {u['sender']} (30d) — "
                      f"create a fact + Mode-A/B filter for {dom}? (Pete confirms; never silent)")

    if len(report) == 1:
        report.append("no ledger history yet — nothing to learn from.")
    print("\n".join(report))
    if apply:
        tl.log_daily("triage-learn", "\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
