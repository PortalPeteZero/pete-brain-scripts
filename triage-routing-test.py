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

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")

NOTE_PATH = "Projects/PA-Command-Centre/triage-routing-regression.md"
FENCE = "json triage-routing-cases"


def load_cases():
    rows = tl.cc_sql(f"SELECT body FROM vault_notes WHERE vault_path='{NOTE_PATH}'")
    if not rows:
        raise RuntimeError(f"regression note not found at {NOTE_PATH}")
    m = re.search(r"```" + re.escape(FENCE) + r"\s*\n(.*?)```", rows[0]["body"], re.S)
    if not m:
        raise RuntimeError(f"no `{FENCE}` fence in the note")
    return json.loads(m.group(1)), rows[0]["body"]


def run_case(c):
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
    case = json.loads(case_json)
    cases, body = load_cases()
    if any(c.get("sender") == case.get("sender") and
           c.get("expect_label") == case.get("expect_label") and
           bool(c.get("expect_no_auto")) == bool(case.get("expect_no_auto")) for c in cases):
        print("case already banked — no-op")
        return 0
    cases.append(case)
    new_fence = "```" + FENCE + "\n" + json.dumps(cases, indent=2) + "\n```"
    new_body = re.sub(r"```" + re.escape(FENCE) + r"\s*\n.*?```", new_fence, body, flags=re.S)
    tl.cc_sql("UPDATE vault_notes SET body=$trtbody$" + new_body +
              "$trtbody$, updated_at=now() WHERE vault_path='" + NOTE_PATH + "'")
    print(f"banked case for {case.get('sender')} — {len(cases)} cases total")
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
