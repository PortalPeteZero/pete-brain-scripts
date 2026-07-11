#!/usr/bin/env python3
"""ee-lint.py — the EE draft-lint (hardening plan P3): the banked rules made mechanical.

Every check that blocks NAMES the rule it enforces. The extensible banned-pattern rules live in
a machine-readable block inside the workflow-design note (## Lint rules), so a corrected mistake
becomes a permanent check by ADDING A RULE THERE — never a lesson note, never a memory.

A block is not a hard wall: a rule can be overridden per-send with an explicit reason in the
payload — `"lint_overrides": {"<rule-id>": "why this is legitimately fine here"}` — which is
banked in the ledger's lint_report. (E.g. internal-names is fine when the customer already knows
the person from the thread; say so.)

Usage (library):   from ee-lint import lint;  ok, report = lint(body, payload)
Usage (CLI):       VAULT=/tmp/pbs python3 ee-lint.py --in payload.json   (lints activity.final_text)
"""
import os, sys, json, re, subprocess, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")

def _cc(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    try:
        return json.loads(r.stdout.strip())
    except Exception:
        return []

def _doc_rules():
    """Pull the machine-readable rules block out of workflow-design (```json ee-lint-rules ... ```)."""
    rows = _cc("SELECT body FROM vault_notes WHERE slug='workflow-design'")
    if not rows:
        return []
    m = re.search(r"```json ee-lint-rules\n(.*?)\n```", rows[0]["body"], re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except Exception:
        return [{"id": "rules-block-unparseable", "pattern": ".^", "reason": "the ee-lint-rules JSON in workflow-design failed to parse — fix it", "always_fail": True}]

def _allowed_prices():
    """£ amounts DERIVED LIVE from the EE price SSOT tables — CC public.ee_rates (standard)
    + CC public.ee_customer_rates (per-customer/per-thread specials) — never hardcoded here. Every
    amount becomes a base, plus VAT (×1.2) and small multiples/sums (per-head cert sums,
    multi-day, small groups). A price change in the DB propagates with zero code change.
    Returns None if the SSOT can't be read → price cross-check becomes a no-op rather than
    blocking every send on a transient DB blip. (Repointed Portal-DB → CC ee_rates 2026-07-11.)"""
    import importlib.util as _u
    try:
        _s = _u.spec_from_file_location("_ef", f"{VAULT}/ee-facts.py")
        _ef = _u.module_from_spec(_s)
        try: _s.loader.exec_module(_ef)
        except SystemExit: pass
        pb = _ef.price_book()  # {item_key: {amount,...}} live from CC ee_rates
        base = {int(round(v["amount"])) for v in pb.values() if v.get("amount") is not None}
        ov = _ef.cc_q("SELECT DISTINCT rate FROM ee_customer_rates")
        base |= {int(round(float(r["rate"]))) for r in ov}
    except Exception:
        return None
    if not base:
        return None
    allowed = set(base)
    for b in base:
        allowed.add(int(round(b * 1.2)))          # inc-VAT twin
        for k in range(2, 13):                     # multi-day / multi-head (cap 12)
            allowed.add(b * k)
    # sums of a delivery/list base + n cert/reg fees (n up to cap) — real composite quote totals
    small = {b for b in base if b <= 40}           # cert/reg fees
    biggish = {b for b in base if b > 40}          # delivery/list rates
    for d in biggish:
        for c in small:
            for n in range(1, 13):
                allowed.add(d + c * n)
                allowed.add(int(round((d + c * n) * 1.2)))
    return allowed

def _staff_names():
    """Current staff first names, LIVE from hub.staff_directory (Active + Subcontractor) — never
    hardcoded (repointed 2026-07-10). First token of full_name ∪ first token of preferred_name."""
    try:
        import importlib.util as _u
        _s = _u.spec_from_file_location("_ef", f"{VAULT}/ee-facts.py")
        _ef = _u.module_from_spec(_s)
        try: _s.loader.exec_module(_ef)
        except SystemExit: pass
        rows = _ef.portal_q("SELECT full_name, preferred_name FROM hub.staff_directory "
                            "WHERE employment_status IN ('Active','Subcontractor')")
        out = set()
        for r in rows:
            for f in (r.get("full_name"), r.get("preferred_name")):
                if f and f.split():
                    out.add(f.split()[0])
        return out
    except Exception:
        return set()

def lint(body, payload=None):
    """Returns (passed: bool, report: dict). report['failures'] = [{id, reason, detail}]."""
    p = payload or {}
    a = p.get("activity", {}) if isinstance(p, dict) else {}
    overrides = p.get("lint_overrides") or {}
    fails, notes = [], []

    def fail(rid, reason, detail=""):
        if rid in overrides:
            notes.append({"id": rid, "overridden": overrides[rid], "detail": detail})
        else:
            fails.append({"id": rid, "reason": reason, "detail": detail})

    text = body or ""
    recipients = " ".join([str(p.get("email") or ""), str(p.get("cc") or ""), str(a.get("cc") or "")]).lower()
    internal_on_thread = "@sygma-solutions.com" in recipients

    # 1. staff name dropped on a customer who doesn't know them — WARN (Mode B reviews), names LIVE
    #    from the directory. Intent (Pete 10 Jul): block ONLY the bare unattributed case; introduced
    #    ("my colleague Jim") + customer-already-named pass. WARN not hard-block → no false-block on a
    #    customer whose own colleague shares a staff first name.
    staff = _staff_names()
    if staff and not internal_on_thread:
        recip_first = ((str(p.get("full_name") or "").split() or [""])[0]).lower()
        incoming = str(p.get("incoming_text") or a.get("incoming_text") or "")
        incoming_clean = re.sub(r"(?m)^\s*>.*$", "", incoming)                       # drop quoted lines
        incoming_clean = re.split(r"(?i)(?:^|\n)\s*(?:from:|on .+ wrote:|-----\s*original)", incoming_clean)[0]  # drop forwarded tail/sig
        for nm in staff:
            low = nm.lower()
            if low in (recip_first, "pete", "michaela"):      # recipient's own name / the author(s)
                continue
            if not re.search(rf"\b{re.escape(nm)}\b", text):
                continue
            attributed = (re.search(rf"\b(?:my|our)\s+(?:colleague\s+)?{re.escape(nm)}\b", text, re.I)
                          or re.search(rf"\bcolleague\s+{re.escape(nm)}\b", text, re.I)
                          or re.search(rf"\b{re.escape(nm)}\b\s+from\s+(?:bookings|the office|our team|your team|your side|your end)", text, re.I))
            known = re.search(rf"\b{re.escape(nm)}\b", incoming_clean or "")
            if not attributed and not known:
                notes.append({"id": "internal-name-warn",
                              "overridden": f"WARN: draft names staff '{nm}' to a customer who may not know them — "
                                            f"introduce them ('my colleague {nm}') or confirm it's expected (Mode-B judgement, not a block)"})

    # 2. holding email (banked 9 Jul: 'why would you send an email saying you would confirm and go back')
    if re.search(r"\b(i(?:'| wi)ll (?:confirm|check)[^.\n]{0,40}(?:and |then )?(?:come back|get back|revert)|let me confirm and)", text, re.I):
        fail("holding-email", "never send a holding 'I'll confirm and come back' email — answer or ask, in one email (workflow-design; ex ee-lesson-no-holding-emails)")

    # 3. price cross-check vs the SSOT notes (derived live; None = SSOT unreadable, skip)
    allowed_prices = _allowed_prices()
    if allowed_prices is not None:
        for m in re.finditer(r"£\s?([\d,]+)", text):
            try:
                v = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if v not in allowed_prices:
                fail("price-not-in-ssot", "every £ figure must derive from ee_rates / ee_customer_rates (base rate, cert fee, or a simple multiple)", f"£{v}")

    # 4. availability claims need a live seat check
    if re.search(r"\b(\w+|\d+)\s+(seat|place)s?\s+(left|remaining|available)\b", text, re.I) and not p.get("availability_checked"):
        fail("availability-unverified", "seat/place counts must be re-checked live (ee-public-dates --dry) before quoting — then set \"availability_checked\": true; NULL places-left is never quoted")

    # 5. links: no relative agenda paths, no bare URLs outside markdown
    if re.search(r"(?<!sygma-solutions\.com)/agendas/[a-z0-9-]+", text) and "sygma-solutions.com/agendas" not in text:
        fail("relative-agenda-link", "agenda links must be absolute (https://sygma-solutions.com/agendas/...) — relative paths break in email")
    for m in re.finditer(r"(?<!\()https?://\S+", text):
        if not re.search(r"\]\(" + re.escape(m.group(0)[:20]), text):
            fail("bare-url", "wrap links as worded markdown links [text](url) — never paste a bare URL", m.group(0)[:60])

    # 6. voice: no em-dashes; no sign-off name (the Gmail signature carries Pete's name)
    if "—" in text or re.search(r"\s--\s", text):
        fail("em-dash", "no em-dashes in Pete's voice — commas/colons/full stops (voice-principles)")
    if re.search(r"\b(Best|Regards|Cheers|Thanks),?\s*\n+\s*Pete\b", text):
        fail("signoff-name", "no sign-off name — the signature carries 'Pete Ashcroft'; end with 'Many thanks'")

    # 7. read-back gate: the email must end with a concrete ask/next step
    paras = [q.strip() for q in text.split("\n\n") if q.strip()]
    tail = [q for q in paras if not re.fullmatch(r"(many thanks|thanks|thank you)[.!]?", q.strip().lower())]
    last = tail[-1] if tail else ""
    if a.get("kind") in ("reply", "quote", "enquiry") and last:
        if "?" not in last and not re.search(r"\b(reply|confirm|send|let me know|tell me|choose|pick|book)\b", last, re.I):
            fail("no-ask-at-end", "the final paragraph must give the customer a concrete next step or question (read-back gate)")

    # 8. doc-driven rules (the regression list — grows with every corrected mistake)
    for r in _doc_rules():
        if r.get("always_fail") or re.search(r.get("pattern", ".^"), text, re.I):
            fail(r.get("id", "doc-rule"), r.get("reason", "banked rule"), r.get("id", ""))

    report = {"failures": fails, "overridden": notes, "checked": True}
    return (not fails), report

def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__); sys.exit(0)
    inpath = None
    for i, x in enumerate(sys.argv[1:]):
        if x == "--in":
            inpath = sys.argv[1:][i + 1]
    p = json.loads(open(inpath).read()) if inpath else json.loads(sys.stdin.read())
    a = p.get("activity", {})
    body = a.get("final_text") or a.get("draft_text") or a.get("body") or ""
    ok, report = lint(body, p)
    if ok:
        print("✅ lint PASS" + (f" ({len(report['overridden'])} overridden with reasons)" if report["overridden"] else ""))
        sys.exit(0)
    print("⛔ lint BLOCKED — fix the draft or override each rule with a reason (lint_overrides):")
    for f in report["failures"]:
        print(f"   ✗ [{f['id']}] {f['reason']}" + (f"  → {f['detail']}" if f.get("detail") else ""))
    sys.exit(2)

if __name__ == "__main__":
    main()
