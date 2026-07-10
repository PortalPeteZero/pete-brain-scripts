#!/usr/bin/env python3
"""triage-lint.py -- the Triage Engine enforcement lint (P3; the ee-lint twin).

Gates every triage action -- and EVERY auto action (L2 auto-file, L3 auto-draft, L4 auto-send,
acting-sync mutations). Every check that blocks NAMES the rule it enforces. The rules live in
the machine-readable fence `json triage-lint-rules` inside the email-workflow note -- extracted
BY FENCE LABEL (the exact ee-lint mechanism). A corrected mistake becomes a rule row THERE.

A block is a gate, not a wall: in an INTERACTIVE session a rule may be overridden with a written
reason -- `"lint_overrides": {"<rule-id>": "why this is fine here"}` -- banked to the decision
row's lint_report. On AUTO paths there are no overrides: any block queues the item for Pete.

Checks (by action level):
  all auto      : per-thread-verify, enquiry-routes-to-ee (content, BEFORE mutation, regardless
                  of fact confidence), content-anomaly-veto
  L2 auto-file  : fact matched + auto_file_enabled (Pete-flipped) + confidence floor + the vetoes
  L3 auto-draft : basis-receipt (non-empty basis_refs) + draft_voice rules
  L4 auto-send  : EVERY never_auto_send class + history floor (>=20 pete decisions, conf >= 0.95,
                  override < 2%) + basis-receipt + record-before-send handled by the caller

Library:  from triage_lint import lint;  ok, report = lint(action_dict)
CLI:      VAULT=/tmp/pbs python3 /tmp/pbs/triage-lint.py --in action.json
          VAULT=/tmp/pbs python3 /tmp/pbs/triage-lint.py --demo      # P3 gate

Action dict (superset; supply what the level needs):
{ "level": "interactive|L2|L3|L4|sync",
  "thread_id": "...", "message_id": "...", "sender": "who@dom",
  "subject": "...", "body_text": "...", "ask": "none|info-only|reply|...",
  "verb": "File", "label": "Receipts",
  "headers": {"to": [...], "cc": [...], "auto_submitted": "...", "precedence": "...",
               "list_id": "...", "authentication_results": "...", "reply_to": "..."},
  "has_attachment": false, "prior_pete_outbound": false,
  "basis_refs": [...], "draft_text": "...",
  "lint_overrides": {"rule-id": "reason"} }
"""
import os, sys, json, re

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")

PETE = "pete.ashcroft@sygma-solutions.com"

# L4 history floor (Open decision 3 defaults -- tunable in the fence/config later)
L4_MIN_DECISIONS = 20
L4_MIN_CONFIDENCE = 0.95
L4_MAX_OVERRIDE_RATE = 0.02
L2_CONFIDENCE_FLOOR = 0.90


_RULES_CACHE = None

def _rules():
    global _RULES_CACHE
    if _RULES_CACHE is None:
        _RULES_CACHE = {r["id"]: r for r in tl.load_lint_rules()}
    return _RULES_CACHE


def _pat_hit(rule, text):
    for p in rule.get("patterns", []):
        if re.search(p, text or "", re.I):
            return p
    return None


def lint(action):
    """Returns (ok, report). report = {"passed": bool, "failures": [{"rule","reason","detail"}],
    "overridden": {...}} -- bank the report to the decision row's lint_report."""
    rules = _rules()
    level = action.get("level", "interactive")
    auto = level in ("L2", "L3", "L4", "sync")
    failures = []
    overrides = action.get("lint_overrides") or {}

    def fail(rule_id, detail=""):
        r = rules.get(rule_id, {})
        if not auto and rule_id in overrides and (overrides[rule_id] or "").strip():
            return  # interactive override with a written reason -- banked, not blocking
        failures.append({"rule": rule_id, "reason": r.get("reason", rule_id), "detail": detail})

    blob = f"{action.get('subject','')}\n{action.get('body_text','')}"

    # --- all auto paths ---
    if auto:
        if not action.get("thread_id"):
            fail("per-thread-verify", "no thread_id — batch/search-set actions are forbidden")
        hit = _pat_hit(rules.get("enquiry-routes-to-ee", {}), blob)
        if hit:
            fail("enquiry-routes-to-ee", f"enquiry content matched: {hit[:60]}")
        hit = _pat_hit(rules.get("content-anomaly-veto", {}), blob)
        ask = action.get("ask")
        if hit or (ask and ask not in ("none", "info-only")):
            fail("content-anomaly-veto", hit[:60] if hit else f"ask={ask} beyond none/info-only")

    # --- L2 auto-file ---
    if level == "L2":
        fact = tl.match_fact(action.get("sender") or "")
        if not fact:
            fail("nas-new-sender", "no matched fact — auto-file impossible")
        else:
            if not fact.get("auto_file_enabled"):
                fail("nas-new-sender", "auto_file_enabled is FALSE — Pete has not flipped this fact")
            if float(fact.get("confidence") or 0) < L2_CONFIDENCE_FLOOR:
                fail("nas-new-sender", f"confidence {fact.get('confidence')} below L2 floor {L2_CONFIDENCE_FLOOR}")

    # --- L3 / L4 basis receipt ---
    if level in ("L3", "L4"):
        if not action.get("basis_refs"):
            fail("basis-receipt", "no banked basis_refs — refused before drafting")

    # --- L4 never-auto-send classes ---
    if level == "L4":
        h = action.get("headers") or {}
        fact = tl.match_fact(action.get("sender") or "")
        if not fact or not fact.get("auto_send_enabled"):
            fail("nas-new-sender", "no fact or auto_send_enabled FALSE")
        else:
            hist = tl.cc_sql(
                "SELECT count(*) AS n, count(*) FILTER (WHERE overridden) AS ov FROM triage_decisions "
                f"WHERE fact_id='{fact['id']}' AND decided_by='pete'")
            n, ov = (hist[0]["n"], hist[0]["ov"]) if hist else (0, 0)
            if n < L4_MIN_DECISIONS:
                fail("nas-new-sender", f"history floor: {n} pete decisions < {L4_MIN_DECISIONS}")
            elif n and ov / n > L4_MAX_OVERRIDE_RATE:
                fail("nas-new-sender", f"override rate {ov}/{n} above {L4_MAX_OVERRIDE_RATE:.0%}")
            if float(fact.get("confidence") or 0) < L4_MIN_CONFIDENCE:
                fail("nas-new-sender", f"confidence below L4 floor {L4_MIN_CONFIDENCE}")
        lbl = action.get("label") or ""
        r = rules.get("nas-finance", {})
        if any(lbl.startswith(p) for p in r.get("label_prefixes", [])) or _pat_hit(r, blob):
            fail("nas-finance")
        r = rules.get("nas-commercial", {})
        if any(lbl.startswith(p) for p in r.get("label_prefixes", [])):
            fail("nas-commercial")
        if action.get("has_attachment"):
            fail("nas-attachment")
        tos = [t.lower() for t in (h.get("to") or [])]
        if not any(PETE in t for t in tos):
            fail("nas-not-direct-to", f"To: {tos[:3]}")
        auto_sub = (h.get("auto_submitted") or "no").lower()
        prec = (h.get("precedence") or "").lower()
        local = (action.get("sender") or "@").split("@")[0].lower()
        r = rules.get("nas-automated-origin", {})
        if (auto_sub not in ("", "no") or prec in ("bulk", "list", "junk") or h.get("list_id")
                or any(local.startswith(lp) for lp in r.get("local_parts", []))):
            fail("nas-automated-origin", f"auto_submitted={auto_sub} precedence={prec} local={local}")
        draft = action.get("draft_text") or ""
        if _pat_hit(rules.get("nas-figure-date-commitment", {}), draft):
            fail("nas-figure-date-commitment", "draft carries a figure/date/commitment")
        if action.get("prior_pete_outbound"):
            fail("nas-prior-human-outbound")
        ar = (h.get("authentication_results") or "").lower()
        dom = (action.get("sender") or "@").split("@")[-1].lower()
        dmarc_ok = ("dmarc=pass" in ar and (f"header.from={dom}" in ar or dom in ar)
                    and "spf=pass" in ar and "dkim=pass" in ar)
        if not dmarc_ok:
            fail("nas-dmarc", f"Authentication-Results not a clean pass for {dom}")
        rt = (h.get("reply_to") or "").lower()
        if rt and dom not in rt:
            fail("nas-dmarc", f"divergent Reply-To {rt[:40]} — auto path replies to the whitelisted address only")

    # --- draft voice (L3/L4 + any drafted text) ---
    if action.get("draft_text"):
        for rid in ("voice-no-em-dash", "voice-no-corny-preamble"):
            hit = _pat_hit(rules.get(rid, {}), action["draft_text"])
            if hit:
                fail(rid, repr(hit[:30]))

    banked_overrides = {k: v for k, v in overrides.items() if k in rules}
    report = {"passed": not failures, "failures": failures, "overridden": banked_overrides,
              "level": level}
    return (not failures), report


def demo():
    print("P3 GATE DEMO — triage-lint")
    # 1) a seeded misroute: L2 auto-file on a sender with no fact -> blocks naming its rule
    a1 = {"level": "L2", "thread_id": "t-demo", "sender": "boss@never-seen-corp.com",
          "subject": "Monthly newsletter", "body_text": "hello", "ask": "info-only"}
    ok1, r1 = lint(a1)
    print(f"\n1. L2 auto-file, uncovered sender → {'BLOCKED' if not ok1 else 'passed?!'}")
    for f in r1["failures"]:
        print(f"   ✗ rule [{f['rule']}]: {f['reason'][:80]} ({f['detail'][:60]})")
    # 2) enquiry-in-triage: auto path sees enquiry content -> blocks
    a2 = {"level": "L2", "thread_id": "t-demo2", "sender": "alerts@md.getsentry.com",
          "subject": "CAT and Genny training", "ask": "info-only",
          "body_text": "Hi, we are interested in a Genny and CAT training course — can you quote for 6 people and share dates?"}
    ok2, r2 = lint(a2)
    blocked_enq = any(f["rule"] == "enquiry-routes-to-ee" for f in r2["failures"])
    print(f"\n2. enquiry content on a HIGH-confidence noise sender → {'BLOCKED' if blocked_enq else 'MISSED'}")
    for f in r2["failures"]:
        print(f"   ✗ rule [{f['rule']}]: {f['reason'][:80]}")
    # 3) interactive override with a written reason -> banked, not blocking; lands in lint_report
    a3 = {"level": "interactive", "thread_id": "t-demo3", "sender": "x@y.com",
          "draft_text": "I'll be honest, the schedule slipped.",
          "lint_overrides": {"voice-no-corny-preamble": "quoting the customer's own phrasing back"}}
    ok3, r3 = lint(a3)
    print(f"\n3. interactive draft with an overridden voice rule → {'passes' if ok3 else 'blocked'}; "
          f"override banked: {list(r3['overridden'].keys())}")
    # bank to a scratch ledger row to demonstrate the lint_report landing
    tl.cc_sql("DELETE FROM triage_decisions WHERE message_id='p3-demo-msg-001'")
    tl.cc_sql("INSERT INTO triage_decisions (thread_id, message_id, sender, decided_by, lint_passed, lint_report) "
              "VALUES ('t-demo3','p3-demo-msg-001','x@y.com','pete', %s, '%s'::jsonb)"
              % ("true" if ok3 else "false", tl.esc(json.dumps(r3))))
    row = tl.cc_sql("SELECT lint_passed, lint_report->'overridden' AS ov FROM triage_decisions "
                    "WHERE message_id='p3-demo-msg-001'")
    print(f"   ledger row: lint_passed={row[0]['lint_passed']}, overridden keys banked={row[0]['ov']}")
    tl.cc_sql("DELETE FROM triage_decisions WHERE message_id='p3-demo-msg-001'")
    # 4) L4 never-send classes: CC-only + automated origin + no DMARC -> blocks
    a4 = {"level": "L4", "thread_id": "t-demo4", "sender": "noreply@md.getsentry.com",
          "subject": "hi", "body_text": "ok", "ask": "none", "basis_refs": ["tmpl:ack-basic"],
          "headers": {"to": ["someone-else@corp.com"], "cc": [PETE], "auto_submitted": "auto-generated",
                       "authentication_results": "spf=fail"}}
    ok4, r4 = lint(a4)
    hit_rules = {f["rule"] for f in r4["failures"]}
    need = {"nas-not-direct-to", "nas-automated-origin", "nas-dmarc"}
    print(f"\n4. L4 send, CC-only + automated origin + DMARC fail → BLOCKED on {sorted(hit_rules & need)}")
    verdict = (not ok1) and blocked_enq and ok3 and need.issubset(hit_rules)
    print(f"\nP3 GATE: {'PASS — misroute blocks named, enquiry blocks, override banked, L4 classes enforced' if verdict else 'FAIL'}")
    return 0 if verdict else 1


def main():
    if "--demo" in sys.argv:
        return demo()
    if "--in" not in sys.argv:
        print(__doc__); return 2
    action = json.load(open(sys.argv[sys.argv.index("--in") + 1]))
    ok, report = lint(action)
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
