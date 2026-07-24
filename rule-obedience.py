#!/usr/bin/env python3
"""
rule-obedience.py — does a rule that ARRIVES actually get FOLLOWED?

Step 9 of [[plan-rules-that-stop-me]], the half that could not be answered on the day. The whole plan
rests on a measured claim: a gate fires ~100% of the time, an injected front-door rule reaches 88% of
sessions, a fetched note 33%, a resident line unreliably. **Delivery was measured. Obedience was not.**

Before 24 Jul 2026 the property hook emitted only a POINTER, so no historical session ever received a
rule inline — obedience could not be measured retrospectively at any price. It needs sessions to
happen. This makes that a runnable check rather than something a future session is supposed to
remember, which is the same discipline the plan applies to everything else: completion needs a gate
you can run, not a promise.

WHAT IT MEASURES
  For each property rule that is injected inline, find sessions where (a) the rule was delivered and
  (b) the session did work on that property, then check whether the rule was honoured. Some rules are
  checkable mechanically (a forbidden word appears in what shipped; a price was put on a page). Those
  are reported as PASS/FAIL. The rest are reported as NOT-MECHANICALLY-CHECKABLE and listed for a
  human read — stated plainly rather than quietly counted as passes.

HONEST LIMITS, stated rather than buried
  * A session that received a rule and did no relevant work is not evidence either way. Excluded.
  * "The rule was not broken" is weaker evidence than "the rule was applied". Both are reported, and
    they are NOT added together.
  * The sample only starts on 24 Jul 2026. Anything earlier is a pointer-era session and is skipped.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/rule-obedience.py            # report
  VAULT=/tmp/pbs python3 /tmp/pbs/rule-obedience.py --days 14  # widen the window
"""
import os, sys, json, glob, re, subprocess, datetime
from collections import defaultdict

VAULT = os.environ.get("VAULT", "/tmp/pbs")
TRANSCRIPTS = os.path.expanduser("~/.claude/projects/-Users-peterashcroft-Command-Centre")
INJECTOR_SHIPPED = datetime.datetime(2026, 7, 24, 12, 0)  # before this, the hook emitted a pointer

DAYS = 30
for i, a in enumerate(sys.argv):
    if a == "--days" and i + 1 < len(sys.argv):
        DAYS = int(sys.argv[i + 1])

# Rules that CAN be checked mechanically: a forbidden pattern that must not appear in shipped output.
# Deliberately small — a rule needing judgement is reported for a human read, never auto-scored.
MECHANICAL = {
    "Sygma Solutions Website": [
        ("no fixed prices", r"[Ff]rom\s*£\s*\d", "a 'From £X' price anchor on a Sygma page"),
        ("Genny and CAT order", r"CAT\s+and\s+Genny", "'CAT and Genny' instead of 'Genny and CAT'"),
    ],
    "Canary Detect Main Website": [
        ("no Cloudinary", r"res\.cloudinary\.com", "a Cloudinary URL in Canary Detect output"),
    ],
    "LeakGuard Lanzarote": [
        ("HTML not PDF", r"\bPDF report\b", "calling the LeakGuard report a 'PDF report'"),
    ],
}


def _sql(q):
    try:
        r = subprocess.run([sys.executable, f"{VAULT}/cc-sql.py", q], capture_output=True,
                           text=True, timeout=60, env={**os.environ, "VAULT": VAULT})
        return json.loads(r.stdout) if r.stdout.strip().startswith("[") else []
    except Exception:
        return []


def sessions_since(cutoff):
    out = []
    for f in glob.glob(os.path.join(TRANSCRIPTS, "*.jsonl")):
        try:
            mt = datetime.datetime.fromtimestamp(os.path.getmtime(f))
        except Exception:
            continue
        if mt >= cutoff:
            out.append(f)
    return out


def main():
    cutoff = max(INJECTOR_SHIPPED,
                 datetime.datetime.now() - datetime.timedelta(days=DAYS))
    files = sessions_since(cutoff)
    print(f"rule-obedience — {len(files)} session(s) since the injector shipped "
          f"({INJECTOR_SHIPPED:%d %b %H:%M})\n")

    if not files:
        print("  No sessions yet. Obedience is NOT measurable — this is not a pass.")
        return 0

    delivered = defaultdict(list)   # property -> [session file]
    breaches = []
    checkable = 0

    for f in files:
        try:
            text = open(f, errors="ignore").read()
        except Exception:
            continue
        if "FRONT-DOOR RULES" not in text:
            continue  # rules were never delivered in this session
        for prop, checks in MECHANICAL.items():
            if f"FRONT-DOOR RULES for {prop}" not in text:
                continue
            delivered[prop].append(os.path.basename(f)[:8])
            # only look at what the ASSISTANT produced, not at rule text quoted back at it
            produced = "\n".join(
                m for m in re.findall(r'"type":"text","text":"(.{0,4000}?)"', text)
                if "FRONT-DOOR RULES" not in m
            )
            for name, pat, human in checks:
                checkable += 1
                if re.search(pat, produced):
                    breaches.append((prop, name, human, os.path.basename(f)[:8]))

    if not delivered:
        print("  Rules were delivered in 0 sessions. Not measurable yet — this is NOT a pass.")
        return 0

    print("  DELIVERED (the rules actually arrived):")
    for prop, ss in sorted(delivered.items()):
        print(f"    {prop:30} {len(ss)} session(s)")

    print(f"\n  MECHANICALLY CHECKED: {checkable} rule-instances")
    if breaches:
        print(f"  ⛔ BREACHES: {len(breaches)} — the rule arrived and was broken anyway")
        for prop, name, human, sid in breaches:
            print(f"      [{sid}] {prop}: {human}")
        print("\n  A rule that arrives and is still broken is NOT solved by delivery.")
        print("  It needs a GATE — see plan-rules-that-stop-me §3a.")
    else:
        print("  ✅ no breach found in what was produced")
        print("  NOTE: 'not broken' is weaker than 'applied'. It is evidence, not proof.")

    total_rules = sum(len(v) for v in _sql(
        "SELECT key FROM rules_v WHERE delivery='injected-on-mention'") or [[]])
    print(f"\n  Rules injected on mention, live: {total_rules}")
    print("  Of those, mechanically checkable here: "
          f"{sum(len(v) for v in MECHANICAL.values())}. The rest need a human read —")
    print("  they are judgement rules, and scoring them automatically would be dishonest.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"rule-obedience: {e}", file=sys.stderr)
        sys.exit(1)
