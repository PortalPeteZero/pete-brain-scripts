#!/usr/bin/env python3
"""ee-health.py — Pete's one command for the Enquiry Engine (hardening plan P5.6).

Prints FIVE plain-English lines, one per goal — each 0/green when the goal holds:
  1. Everything in the database
  2. The engine knows where everything is
  3. The model uses & follows it
  4. Every touch point keeps the 3 systems updated
  5. The engine constantly learns

Run:  VAULT=/tmp/pbs python3 /tmp/pbs/ee-health.py
(Also rendered on the cockpit /m/enquiry-engine Brain tab.)
"""
import os, sys, json, subprocess, datetime as dt, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")

def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__); sys.exit(0)
    tl = _load("telog", f"{VAULT}/te-log.py")
    ok_all = True

    # 1. Everything in the DB
    ef = open(f"{VAULT}/ee-facts.py").read()
    fic = ("MODEL = {" in ef) or ("SUPPORTING = {" in ef)
    ar = subprocess.run(["python3", f"{VAULT}/ee-alias-test.py"], capture_output=True, text=True,
                        env={**os.environ, "VAULT": VAULT})
    alias_ok = ar.returncode == 0
    alias_line = (ar.stdout or "").strip().split("\n")[-1].strip("= ").strip()
    g1 = (not fic) and alias_ok
    print(("✅" if g1 else "🔴") + f" 1. Everything in the database — facts hardcoded in tools: {'0' if not fic else 'FOUND'}; {alias_line}")
    ok_all &= g1

    # 2. Knows where everything is
    man = tl.cc_sql("SELECT count(*) n FROM vault_notes WHERE slug='ee-manifest'")[0]["n"]
    helps = all(subprocess.run(["python3", f"{VAULT}/{t}", "--help"], capture_output=True, text=True,
                               env={**os.environ, "VAULT": VAULT}).returncode == 0
                for t in ("te-log.py", "ee-facts.py", "ee-lint.py", "ee-alias-test.py", "ee-health.py"))
    g2 = man == 1 and helps
    print(("✅" if g2 else "🔴") + f" 2. Knows where everything is — manifest note: {'present' if man else 'MISSING'}; every EE tool answers --help: {'yes' if helps else 'NO'}")
    ok_all &= g2

    # 3. Uses & follows it (since P3 shipped)
    disc = tl.cc_sql("SELECT count(*) n FROM enquiry_touches WHERE kind IN ('reply','quote') AND source='live' "
                     "AND occurred_at > '2026-07-10T12:00:00Z' AND (retrieval_refs IS NULL OR cardinality(retrieval_refs)=0 OR lint_passed IS NOT TRUE)")[0]["n"]
    g3 = disc == 0
    print(("✅" if g3 else "🔴") + f" 3. Uses & follows it — sends missing retrieval-receipt or lint-pass: {disc}")
    ok_all &= g3

    # 4. Every touch, 3 systems — last nightly reconcile
    rec = tl.cc_sql("SELECT content, date FROM daily_log WHERE cron_name='ee-reconcile' ORDER BY created_at DESC LIMIT 1")
    if rec:
        head = rec[0]["content"].split("\n")[0]
        drift_zero = "zero drift" in head.lower()
        print(("✅" if drift_zero else "🟡") + f" 4. Every touch, 3 systems — last reconcile ({rec[0]['date']}): {head.replace('## ', '')}")
        # drift is a report, not a failure of the engine's own writes — yellow, not red
    else:
        print("🔴 4. Every touch, 3 systems — the nightly reconciler has never run")
        ok_all = False

    # 5. Constantly learns
    wk = tl.cc_sql("SELECT count(*) FILTER (WHERE edited IS FALSE) ef, count(*) FILTER (WHERE edited IS NOT NULL) tot "
                   "FROM enquiry_touches WHERE kind IN ('reply','quote') AND source='live' AND occurred_at > now() - interval '7 days'")[0]
    pct = (100 * wk["ef"] // wk["tot"]) if wk["tot"] else None
    sa = tl.cc_sql("SELECT content FROM daily_log WHERE cron_name='ee-selfaudit' ORDER BY created_at DESC LIMIT 1")
    sa_green = bool(sa) and "ALL GREEN" in sa[0]["content"]
    print(("✅" if sa_green else "🟡") + f" 5. Constantly learns — edit-free this week: {str(pct) + '%' if pct is not None else 'no sends yet'} ({wk['ef']}/{wk['tot']}); weekly self-audit: {'green' if sa_green else 'not yet green/run'}")

    print("\n" + ("ALL SYSTEMS GREEN" if ok_all else "attention needed on the red lines above"))
    sys.exit(0 if ok_all else 1)

if __name__ == "__main__":
    main()
