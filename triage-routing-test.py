#!/usr/bin/env python3
"""triage-routing-test.py -- the Triage Engine routing regression harness (P1).

The triage twin of ee-alias-test.py: replays every banked routing case against the LIVE
triage_routing_facts table + the facts-first classifier, and prints N/N pass. Cases live in
the vault note `triage-routing-regression` inside a fenced block labelled
`json triage-routing-cases` (the same fence mechanism as the lint rules -- the note is the
ONE home; a corrected misroute appends a case THERE at the moment of correction).

Case shapes:
  positive: {"sender": "x@dom", "expect_label": "...", "expect_mode": "A|B",
             "note": "why this case exists"}
  negative: {"sender": "x@dom", "expect_no_auto": true, "note": "..."}
            (a digest-undo's negative assertion: this sender must NOT auto-act --
             passes while the matched fact has auto_file_enabled=false AND
             auto_send_enabled=false, or no fact exists)

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-routing-test.py            # replay all cases
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-routing-test.py --demo     # P1 gate: live proof
       facts move with the DB (flip a scratch fact, show the classification change, revert)
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-routing-test.py --add '<json case>'
       append a case to the note at the moment of correction

Exit codes: 0 all pass · 1 failures · 2 harness error.
"""
import os, sys, json, re

VAULT = os.environ.get("VAULT", "/tmp/pbs")
sys.path.insert(0, VAULT)
import importlib
tl = importlib.import_module("triage_lib")

NOTE_PATH = "Projects/PA-Command-Centre/triage-routing-regression.md"
FENCE = "json triage-routing-cases"


def load_cases():
    """v6: cases live in the triage_cases table (payload jsonb, verbatim), NOT the vault-note
    fence. Mechanical replay is ROUTING cases only -- content cases run through --acceptance."""
    rows = tl.cc_sql("SELECT payload FROM triage_cases WHERE active AND type='routing' ORDER BY created_at")
    return [r["payload"] for r in rows], None


def run_case(c):
    # --- ask-classification cases (10 Jul 2026: corrections are not only about
    # fact routing — the classifier's ask and the lint's patterns regress too) ---
    if c.get("expect_ask"):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tac", os.path.join(VAULT, "triage-action-classify.py"))
        tac = importlib.util.module_from_spec(spec); spec.loader.exec_module(tac)
        thread = {"thread_id": "regression-case", "subject": c.get("subject", ""),
                  "from_last": c.get("from_last") or c["sender"],
                  "last_body": c.get("body_sample", ""),
                  "prior_pete_outbound": bool(c.get("prior_pete_outbound")),
                  "msgs": c.get("msgs", 2)}
        got = tac.classify_thread(thread)["ask_classification"]
        if got != c["expect_ask"]:
            return False, f"ask={got} (expect {c['expect_ask']})"
        if not c.get("expect_lint_clean"):
            return True, f"ask={got} (expect {c['expect_ask']})"
        # fall through: a case may assert BOTH the ask and lint cleanliness
    # --- lint-clean cases: the named rules must NOT fire on the sample ---
    if c.get("expect_lint_clean"):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tlint", os.path.join(VAULT, "triage-lint.py"))
        tlint = importlib.util.module_from_spec(spec); spec.loader.exec_module(tlint)
        ok, report = tlint.lint({"level": "L2", "thread_id": "regression-case",
                                 "subject": c.get("subject", ""), "body_text": c.get("body_sample", ""),
                                 "ask": c.get("ask", "info-only"), "verb": "Clear", "label": None})
        fired = [f["rule"] for f in report.get("failures", [])]
        bad = [r for r in c["expect_lint_clean"] if r in fired]
        return (not bad), (f"rules fired: {fired}" if bad else f"clean of {c['expect_lint_clean']} (fired: {fired})")
    fact = tl.match_fact(c["sender"])
    if c.get("expect_no_auto"):
        if fact is None:
            return True, "no fact (auto impossible)"
        if not fact.get("auto_file_enabled") and not fact.get("auto_send_enabled"):
            return True, "fact matched, both auto flags FALSE"
        return False, f"AUTO ENABLED on {fact['sender_pattern']} — negative case violated"
    if fact is None:
        return False, "no fact matched"
    ok = (fact.get("gmail_label") == c.get("expect_label")
          and fact.get("filter_mode") == c.get("expect_mode"))
    return ok, f"matched {fact['sender_pattern']} → {fact.get('gmail_label')}/{fact.get('filter_mode')}"


def replay():
    cases, _ = load_cases()
    passed = 0
    for c in cases:
        ok, why = run_case(c)
        mark = "✓" if ok else "✗"
        print(f"  {mark} {c['sender']:42} {why}")
        if ok:
            passed += 1
    print(f"\ntriage routing regression: {passed}/{len(cases)} pass")
    return 0 if passed == len(cases) else 1


def add_case(case_json):
    """Bank a routing case into triage_cases. Dedupe key = sender + expect_label + expect_no_auto
    (a corrected mistake can re-bank with a different expectation; identical is a no-op)."""
    case = json.loads(case_json)
    pj = "'" + tl.esc(json.dumps(case)) + "'::jsonb"
    sender = case.get("sender")
    dup = tl.cc_sql(
        "SELECT id FROM triage_cases WHERE type='routing' AND active"
        f" AND payload->>'sender' IS NOT DISTINCT FROM {'NULL' if sender is None else chr(39)+tl.esc(sender)+chr(39)}"
        f" AND payload->>'expect_label' IS NOT DISTINCT FROM "
        f"{'NULL' if case.get('expect_label') is None else chr(39)+tl.esc(case['expect_label'])+chr(39)}"
        f" AND (payload->>'expect_no_auto' IS NOT DISTINCT FROM {'true' if case.get('expect_no_auto') else 'NULL'})")
    if dup:
        tl.cc_sql(f"UPDATE triage_cases SET payload={pj} WHERE id='{dup[0]['id']}'")
        print(f"case updated for {sender}")
        return 0
    tl.cc_sql(f"INSERT INTO triage_cases (type, sender, payload) VALUES ('routing', "
              f"{'NULL' if sender is None else chr(39)+tl.esc(sender)+chr(39)}, {pj})")
    print(f"banked routing case for {sender}")
    return 0


def demo():
    """P1 gate: live proof the classification moves with the DB, no code change."""
    print("P1 GATE DEMO — facts move with the DB")
    scratch = "p1-demo-scratch.example.com"
    tl.cc_sql(f"DELETE FROM triage_routing_facts WHERE sender_pattern='{scratch}'")
    fact = tl.match_fact(f"bot@{scratch}")
    print(f"  1. no fact for {scratch}: match = {fact}")
    ok1 = fact is None
    tl.cc_sql("INSERT INTO triage_routing_facts (sender_pattern, gmail_label, filter_mode, source) "
              f"VALUES ('{scratch}', 'Newsletters', 'B', 'manual')")
    fact = tl.match_fact(f"bot@{scratch}")
    print(f"  2. fact row inserted → match = {fact['gmail_label']}/{fact['filter_mode']} (source {fact['source']})")
    ok2 = fact is not None and fact["gmail_label"] == "Newsletters"
    tl.cc_sql(f"UPDATE triage_routing_facts SET gmail_label='Receipts' WHERE sender_pattern='{scratch}'")
    fact = tl.match_fact(f"bot@{scratch}")
    print(f"  3. fact row EDITED (no code change) → match = {fact['gmail_label']}/{fact['filter_mode']}")
    ok3 = fact is not None and fact["gmail_label"] == "Receipts"
    tl.cc_sql(f"DELETE FROM triage_routing_facts WHERE sender_pattern='{scratch}'")
    fact = tl.match_fact(f"bot@{scratch}")
    print(f"  4. scratch row reverted (deleted): match = {fact}")
    ok4 = fact is None
    print("\n  replaying the full banked regression set:")
    reg = replay()
    verdict = ok1 and ok2 and ok3 and ok4 and reg == 0
    print(f"\nP1 GATE: {'PASS — classification follows the DB and the regression set is green' if verdict else 'FAIL'}")
    return 0 if verdict else 1


def main():
    if "--demo" in sys.argv:
        return demo()
    if "--add" in sys.argv:
        return add_case(sys.argv[sys.argv.index("--add") + 1])
    return replay()


if __name__ == "__main__":
    sys.exit(main())
