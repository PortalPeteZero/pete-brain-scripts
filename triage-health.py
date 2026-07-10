#!/usr/bin/env python3
"""triage-health.py -- Pete's ONE command for the Triage Engine (P5; the ee-health twin).

Prints the five goals as five plain-English lines, each ✅/❌, then an overall verdict.
Mirrored on the /m/triage-engine cockpit.

Usage: VAULT=/tmp/pbs python3 /tmp/pbs/triage-health.py
"""
import os, sys, subprocess, re

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")

TOOLS = ["triage-sync.py", "triage-action-classify.py", "triage-routing-test.py",
         "triage-log.py", "triage-lint.py", "triage-reconcile.py", "triage-selfaudit.py",
         "triage-signoff.py", "triage-learn.py", "triage-health.py", "triage-engine-run.py",
         "triage-pull.py", "triage-validator.py"]


def main():
    checks = []

    # 1. Everything in the database
    facts = tl.cc_sql("SELECT count(*) AS n FROM triage_routing_facts")[0]["n"]
    r = subprocess.run(["python3", os.path.join(tl.VAULT, "triage-routing-test.py")],
                       capture_output=True, text=True, env={**os.environ, "VAULT": tl.VAULT})
    m = re.search(r"regression: (\d+)/(\d+) pass", r.stdout or "")
    reg = f"{m.group(1)}/{m.group(2)}" if m else "unreadable"
    reg_ok = bool(m) and m.group(1) == m.group(2)
    tmpl = tl.cc_sql("SELECT count(*) AS n FROM triage_templates")[0]["n"]
    ok1 = facts > 0 and reg_ok and tmpl > 0
    checks.append((ok1, f"1. Everything in the database — routing facts: {facts}; "
                        f"regression tests: {reg} pass; templates in DB: {tmpl}"))

    # 2. Knows where everything is
    manifest = tl.cc_sql("SELECT count(*) AS n FROM vault_notes WHERE slug='triage-manifest'")[0]["n"]
    missing_tools = [t for t in TOOLS if not os.path.exists(os.path.join(tl.VAULT, t))]
    ok2 = manifest > 0 and not missing_tools
    checks.append((ok2, f"2. Knows where everything is — manifest note: "
                        f"{'present' if manifest else 'MISSING'}; tools on disk: "
                        f"{len(TOOLS) - len(missing_tools)}/{len(TOOLS)}"
                        + (f" (missing {missing_tools})" if missing_tools else "")))

    # 3. Uses & follows it
    bad = tl.cc_sql("SELECT count(*) AS n FROM triage_decisions WHERE "
                    "(send_status IS NOT NULL AND (basis_refs IS NULL OR cardinality(basis_refs)=0)) OR "
                    "(decided_by='cron-auto' AND lint_passed IS NOT TRUE)")[0]["n"]
    ok3 = bad == 0
    checks.append((ok3, f"3. Uses & follows it — auto actions missing basis-receipt or lint-pass: {bad}"))

    # 4. Every touch keeps the systems in sync
    rec = tl.cc_sql("SELECT content FROM daily_log WHERE cron_name='triage-reconcile' "
                    "ORDER BY date DESC, content LIMIT 1")
    if rec:
        first = rec[0]["content"].splitlines()
        drift_free = any("zero drift" in l.lower() for l in first)
        summary = "zero drift" if drift_free else "drift lines present — see the morning brief"
    else:
        drift_free, summary = None, "no reconcile run yet (cron runs nightly 06:50)"
    stuck = tl.cc_sql("SELECT count(*) AS n FROM triage_decisions WHERE apply_status='applying' OR send_status='sending'")[0]["n"]
    ok4 = (drift_free is not False) and stuck == 0
    checks.append((ok4, f"4. Every touch, all systems — last reconcile: {summary}; stuck rows now: {stuck}"))

    # 5. Constantly learns
    tr = tl.cc_sql(
        "SELECT count(*) FILTER (WHERE decided_at >= now() - interval '7 days') AS n7, "
        "count(*) FILTER (WHERE decided_at >= now() - interval '7 days' AND overridden) AS o7 "
        "FROM triage_decisions WHERE decided_by='pete'")[0]
    rate = f"{tr['o7']}/{tr['n7']} overridden this week" if tr["n7"] else "no decision history yet (engine is new)"
    ok5 = reg_ok
    checks.append((ok5, f"5. Constantly learns — override rate: {rate}; regression harness: "
                        f"{'green' if reg_ok else 'RED'}"))

    all_ok = all(c[0] for c in checks)
    for ok, line in checks:
        print(("✅ " if ok else "❌ ") + line)
    print()
    auto = tl.get_config("triage-auto-mode", "off")
    sync_mode = tl.get_config("triage-sync-mode", "report")
    print(f"kill switch: triage-auto-mode={auto} · sync mode: {sync_mode} · "
          f"auto-enabled facts: {tl.cc_sql('SELECT count(*) AS n FROM triage_routing_facts WHERE auto_file_enabled OR auto_send_enabled')[0]['n']} "
          f"(Pete flips these, never the engine)")
    print("ALL SYSTEMS GREEN" if all_ok else "ATTENTION NEEDED — see ❌ lines")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
