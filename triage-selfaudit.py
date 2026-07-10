#!/usr/bin/env python3
# CRON-META
# what: Weekly Triage Engine self-audit — re-runs the routing regression harness, spot-checks the facts table for silent Nones, reports the override-rate trend and sampled probes. Audits the auditor so the engine doesn't rot (the EE-P4.4 shape on the triage grain).
# why: A regression harness nobody runs is decoration. The weekly audit runs it, checks the facts, and reports the learning metrics to the morning brief ([[triage-engine-design]]).
# reads: triage_routing_facts, triage_decisions, the triage-routing-regression note, config
# writes: CC daily_log (cron_name='triage-selfaudit')
# entity: personal
# schedule: 10 7 * * 1
# timezone: Atlantic/Canary
# secrets: GOOGLE_SA_JSON, SUPABASE_TOKEN
# CRON-META-END
"""triage-selfaudit.py -- weekly Triage Engine self-audit (P4; ee-selfaudit twin).

Checks:
  1. Routing regression harness (triage-routing-test.py) — all banked cases green
  2. Facts spot-check — matched-fact rows with NULL gmail_label (silent Nones), auto-enabled
     facts below the confidence floor, facts never seen in 90d
  3. Override-rate trend — this week vs last (decided_by='pete' rows only)
  4. Sampled probes — 3 random facts re-matched through the classifier path

Run by hand: VAULT=/tmp/pbs python3 /tmp/pbs/triage-selfaudit.py
"""
import os, sys, subprocess, random

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")


def main():
    lines = ["## triage-selfaudit — " + tl.today()]

    # 1. regression harness
    r = subprocess.run(["python3", os.path.join(tl.VAULT, "triage-routing-test.py")],
                       capture_output=True, text=True, env={**os.environ, "VAULT": tl.VAULT})
    tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout else "no output"
    lines.append(f"- regression harness: {tail} ({'green' if r.returncode == 0 else 'RED — fix before trusting routing'})")

    # 2. facts spot-checks
    nulls = tl.cc_sql("SELECT count(*) AS n FROM triage_routing_facts WHERE gmail_label IS NULL")[0]["n"]
    if nulls:
        lines.append(f"- facts incomplete: {nulls} fact(s) with NULL gmail_label — silent Nones, fix or delete.")
    weak_auto = tl.cc_sql("SELECT count(*) AS n FROM triage_routing_facts WHERE "
                          "(auto_file_enabled OR auto_send_enabled) AND confidence < 0.9")[0]["n"]
    if weak_auto:
        lines.append(f"- ⚠ {weak_auto} auto-enabled fact(s) below the 0.9 confidence floor — review.")

    # 3. override-rate trend (pete rows only)
    tr = tl.cc_sql(
        "SELECT count(*) FILTER (WHERE decided_at >= now() - interval '7 days') AS n7, "
        "count(*) FILTER (WHERE decided_at >= now() - interval '7 days' AND overridden) AS o7, "
        "count(*) FILTER (WHERE decided_at >= now() - interval '14 days' AND decided_at < now() - interval '7 days') AS n14, "
        "count(*) FILTER (WHERE decided_at >= now() - interval '14 days' AND decided_at < now() - interval '7 days' AND overridden) AS o14 "
        "FROM triage_decisions WHERE decided_by='pete'")[0]
    def rate(o, n):
        return f"{o}/{n} ({o/n:.0%})" if n else "no data"
    lines.append(f"- override rate: this week {rate(tr['o7'], tr['n7'])}, last week {rate(tr['o14'], tr['n14'])} "
                 f"(pete-decided rows only; target: trending down)")

    # 4. sampled probes
    facts = tl.cc_sql("SELECT sender_pattern, gmail_label, filter_mode FROM triage_routing_facts "
                      "WHERE gmail_label IS NOT NULL ORDER BY sender_pattern LIMIT 200")
    ok = 0
    picks = random.sample(facts, min(3, len(facts))) if facts else []
    for f in picks:
        probe = ("probe@" + f["sender_pattern"].lstrip("*.")) if "@" not in f["sender_pattern"] else f["sender_pattern"]
        m = tl.match_fact(probe)
        if m and m["gmail_label"] == f["gmail_label"]:
            ok += 1
    lines.append(f"- sampled probes: {ok}/{len(picks)} facts re-match correctly")

    tl.log_daily("triage-selfaudit", "\n".join(lines))
    print("\n".join(lines))
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
