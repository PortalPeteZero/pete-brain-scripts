"""ee-draft-gate.py — the MECHANICAL pre-draft gate for Enquiry Engine replies (built 21 Jul 2026).

The recurring failure (Pete, 21 Jul 2026 — Wheal Jane): a reply drafted straight into chat from one
skimmed precedent, engine contract unread, classification skipped, price quoted on an enquiry whose
course type + headcount were still open. A banner in the skill is words that get skipped; THIS tool
makes the draft code-produced, exactly as triage-ops-table did for the triage table:

  NO EE reply/quote draft may be PRESENTED to Pete unless this gate has validated it.
  ee-send REFUSES to draft/send a reply/quote whose draft_text carries no valid gate stamp.

What it verifies (each check names itself; override with "gate_overrides": {"<id>": "why"}):
  [sent-history]     it re-runs the Sent/domain reconciliation LIVE (gmail-api) — the Emma Greeves
                     duplicate guard. Prior threads beyond the enquiry's own = BLOCK until reconciled.
  [facts-match]      it re-resolves the course from the customer's own words via ee-facts and blocks
                     a claimed course_code that doesn't match the index resolution.
  [must-haves]       location / course_type / headcount each declared present|ambiguous|missing WITH
                     the customer's words as evidence; 'present'/'ambiguous' evidence must actually
                     appear in incoming_text. No evidence = BLOCK.
  [stage-logic]      the balance rule (workflow-design, validated 2026-07-07) derived, not vibed:
                     all three present → ready-to-quote; course+mode known, headcount missing →
                     quote-with-qualifier allowed (fixed day rate) OR qualify-first; course_type
                     ambiguous or location missing → qualify-first, NO price.
  [stage-draft-fit]  qualify-first drafts: zero £ figures, agenda link included (Pete rule 21 Jul),
                     every missing/ambiguous must-have actually asked about. quote drafts: agenda
                     link + cert routes present, £ figures left to ee-lint's SSOT cross-check.
  [lint]             the FULL ee-lint runs here at draft time (shift-left) — a rule banked via
                     ee-learn blocks at the draft, not at send. This is the learning surface: every
                     correction Pete banks tightens the NEXT draft automatically.
  [retrieval]        ≥2 precedent refs, each existing in vault_notes, each with a takeaway line —
                     the runnable form of "name the worked replies you read in full".

On PASS it prints the banked doc-rules (the knowledge the draft must honour), the classification
block, and the draft — the presentation IS the gate output — and writes the stamp
/tmp/ee-draft-gate-<key>.json (sha256 of draft_text). ee-send verifies the stamp hash; a changed
draft needs a re-run. Bypass exists but is loud: ee-send --no-gate with a payload
"gate_override" reason, banked into the ledger's lint_report.

Payload = the te-log/ee-send payload PLUS:
  "incoming_text": "<the customer's enquiry, verbatim>",
  "classification": {"course_code": "C001", "scenario": "private-onsite",
                     "stage": "qualify-first" | "ready-to-quote",
                     "balance_call": "quote-with-qualifier" (optional),
                     "must_haves": {"location":  {"status": "present",  "evidence": "at our offices in Cornwall"},
                                    "course_type": {"status": "ambiguous", "evidence": "CAT and Genny training"},
                                    "headcount": {"status": "missing"}}},
  "retrieval_refs": [{"slug": "...", "takeaway": "what this precedent settled"}, ...]
  (activity.draft_text = the draft, verbatim — the draft of record)

Usage:
  VAULT=/tmp/pbs python3 ee-draft-gate.py --in payload.json          # validate + stamp + print
  VAULT=/tmp/pbs python3 ee-draft-gate.py --selftest                 # run the built-in regression fixtures
"""
import os, sys, json, re, hashlib, subprocess, importlib.util, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")

def _load(fname, modname):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(VAULT, fname))
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    except SystemExit:
        pass
    return m

def _cc(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       env={**os.environ, "VAULT": VAULT}, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"cc-sql failed: {r.stderr[:200]}")
    return json.loads(r.stdout) if r.stdout.strip() else []

def _lit(s):
    return "'" + str(s).replace("'", "''") + "'"

def _norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

CAP_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
             8: "eight", 9: "nine", 10: "ten", 12: "twelve", 16: "sixteen"}

_RULES_CACHE = None
def _doc_rules():
    """All kind='doc' rules WITH their enforcement columns (scenarios/applies_when/require_pattern/
    fail_hint). Cached per process; a stubbed _RULES_CACHE makes the selftest deterministic offline."""
    global _RULES_CACHE
    if _RULES_CACHE is None:
        try:
            _RULES_CACHE = _cc("SELECT name, body, scenarios, applies_when, require_pattern, fail_hint "
                               "FROM ee_rules WHERE kind='doc' ORDER BY name")
        except Exception:
            _RULES_CACHE = []
    return _RULES_CACHE

def _rules_for(scenario, stage):
    """Doc-rules whose scenarios apply to this scenario/stage. Untagged or 'all' always apply."""
    tags = set(t for t in (scenario, stage) if t)
    out = []
    for r in _doc_rules():
        scen = r.get("scenarios") or []
        if (not scen) or ("all" in scen) or (tags & set(scen)):
            out.append(r)
    return out

def _headcount_n(mh):
    m = re.search(r"\d+", ((mh.get("headcount") or {}).get("evidence") or ""))
    return int(m.group(0)) if m else None

_THREAD_STOP = {"regards", "kindly", "thanks", "email", "phone", "mobile", "website", "submission",
                "confidential", "attachments", "addressee", "unauthorised", "notify", "sender",
                "delete", "please", "sygma", "solutions", "hello", "morning", "afternoon"}

def _plain_body(m):
    """Plain-text body of one Gmail message, quoted history stripped — so a later reply quoting an
    earlier message can't make that earlier message look 'read'."""
    import base64
    parts = []
    def walk(pt):
        mt = pt.get("mimeType", "") or ""
        data = (pt.get("body") or {}).get("data")
        if data and mt.startswith("text/"):
            try:
                txt = base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
                if mt == "text/html":
                    txt = re.sub(r"<[^>]+>", " ", txt)
                parts.append((mt, txt))
            except Exception:
                pass
        for sub in (pt.get("parts") or []):
            walk(sub)
    walk(m.get("payload", {}) or {})
    plain = [t for mt, t in parts if mt == "text/plain"]
    txt = plain[0] if plain else (parts[0][1] if parts else "")
    return re.split(r"\nOn .{0,90}wrote:|\n>{1,}\s|\n-{2,}\s*Original Message", txt)[0]

def gate(p, live=True):
    """Returns (passed: bool, report: dict). report['failures'] = [{id, reason, detail}]."""
    a = p.get("activity", {}) or {}
    overrides = p.get("gate_overrides") or {}
    fails, notes, info = [], [], []

    def fail(rid, reason, detail=""):
        if rid in overrides:
            notes.append({"id": rid, "overridden": overrides[rid], "detail": detail})
        else:
            fails.append({"id": rid, "reason": reason, "detail": detail})

    draft = a.get("draft_text") or ""
    incoming = p.get("incoming_text") or ""
    cls = p.get("classification") or {}
    email = (p.get("email") or "").strip().lower()

    # 0. structural
    if not draft.strip():
        fail("no-draft", "activity.draft_text is empty — there is nothing to validate")
    if not incoming.strip():
        fail("no-incoming", "incoming_text (the customer's enquiry, verbatim) is required — the gate checks evidence against it")
    if not cls:
        fail("no-classification", "classification block missing — course_code / scenario / stage / must_haves")
    if fails:
        return False, {"failures": fails, "overridden": notes, "info": info}

    # 1. sent-history — re-run LIVE, never trust a claim (Emma Greeves guard)
    if live and email:
        try:
            g = _load("gmail-api.py", "gmapi").GmailAPI()
            domain = email.split("@")[-1]
            hits = {t["id"] for t in (g.search_threads(f"to:{email} in:sent", 10) or [])}
            hits |= {t["id"] for t in (g.search_threads(f"{domain} in:sent", 10) or [])}
            hits.discard(p.get("thread_id"))
            if hits:
                fail("sent-history",
                     "prior Sent conversation(s) exist with this customer/domain — reconcile the full history "
                     "before drafting (a fresh reply may duplicate an answer already given)",
                     ", ".join(sorted(hits)))
            else:
                info.append("sent-history: 0 prior Sent threads for %s / @%s (checked live)" % (email, domain))
        except Exception as e:
            fail("sent-history", f"could not verify Sent history live ({e}) — check it by hand and override with the evidence")

    # 2. facts-match — re-resolve the course from the customer's own words
    course_code = (cls.get("course_code") or "").strip()
    resolved = None
    if live and course_code:
        try:
            ef = _load("ee-facts.py", "eefacts")
            fn = getattr(ef, "lookup", None) or getattr(ef, "resolve", None)
            resolved = fn(incoming) if fn else None
            rc = (resolved or {}).get("code") if isinstance(resolved, dict) else None
            if rc and rc != course_code:
                fail("facts-match", f"classification claims {course_code} but ee-facts resolves the customer's words to {rc}", rc)
            elif rc:
                info.append(f"facts-match: ee-facts resolves to {rc} (agrees)")
            else:
                info.append("facts-match: ee-facts returned no resolution — course_code taken on the payload's word")
        except Exception as e:
            info.append(f"facts-match: could not re-resolve ({e}) — course_code taken on the payload's word")

    # 3. must-haves with evidence
    mh = cls.get("must_haves") or {}
    statuses = {}
    for key in ("location", "course_type", "headcount"):
        d = mh.get(key) or {}
        st = d.get("status")
        statuses[key] = st
        if st not in ("present", "ambiguous", "missing"):
            fail("must-haves", f"must_haves.{key}.status must be present|ambiguous|missing (got {st!r})")
            continue
        if st in ("present", "ambiguous"):
            ev = d.get("evidence") or ""
            if not ev.strip():
                fail("must-haves", f"must_haves.{key} is '{st}' but carries no evidence — quote the customer's words")
            elif _norm(ev) not in _norm(incoming):
                fail("must-haves", f"must_haves.{key} evidence {ev!r} does not appear in incoming_text — evidence must be the customer's own words")

    # 3b. FULL-THREAD READ — the gate PULLS the live thread and proves EVERY inbound customer message is
    #     represented in incoming_text. Before this, incoming_text was whatever the drafter pasted, so a
    #     session could skim the latest message and pass clean (Pete, 23 Jul 2026). A pull failure is a loud
    #     INFO, never a block — an API blip must not block every draft (the _allowed_prices lesson).
    if live and p.get("thread_id"):
        try:
            g2 = _load("gmail-api.py", "gmapi2").GmailAPI()
            th = g2.get_thread(p["thread_id"])
            msgs = th.get("messages", []) if isinstance(th, dict) else (th or [])
            inbound, total = [], len(msgs)
            for m in msgs:
                hdrs = {h["name"].lower(): h["value"] for h in (m.get("payload", {}) or {}).get("headers", [])}
                frm = (hdrs.get("from") or "").lower()
                body = _plain_body(m)
                # a website contact-form submission arrives FROM info@sygma-solutions.com but IS the
                # customer's own words — count it as inbound, or a first-contact enquiry escapes the check
                is_form = "contact form submission" in (body or "").lower()
                if ("sygma-solutions.com" in frm or "sygma.ie" in frm) and not is_form:
                    continue
                if is_form:
                    # a form body is mostly field metadata (Name/Company/Phone/Enquiry Type); compare on the
                    # customer's actual Message, else a legitimate incoming_text scores low on boilerplate
                    body = re.split(r"(?i)\bmessage", body)[-1] or body   # form renders as "MessageHi we are…" (no trailing boundary)
                if body and len(_norm(body)) >= 40:
                    inbound.append((hdrs.get("from", "?"), body))
            missing = []
            ninc = _norm(incoming)
            # word-overlap, not a verbatim run: an omitted message scores ~0, while a legitimately
            # assembled incoming_text scores high even if boilerplate/metadata was trimmed.
            for idx, (frm, body) in enumerate(inbound, 1):
                toks = [w for w in re.findall(r"[a-z]{5,}", _norm(body)) if w not in _THREAD_STOP]
                seen = list(dict.fromkeys(toks))[:16]
                if len(seen) < 4:
                    continue
                hit = sum(1 for w in seen if w in ninc)
                if hit / len(seen) < 0.4:
                    missing.append(f"#{idx} from {frm[:40]} (only {hit}/{len(seen)} of its words appear)")
            if missing:
                fail("thread-not-read",
                     f"incoming_text does not represent every inbound message on this thread "
                     f"({len(inbound)} customer message(s) of {total} total) — READ THE WHOLE THREAD and include "
                     f"each customer message, not just the latest", "; ".join(missing))
            else:
                info.append(f"thread-read: all {len(inbound)} inbound message(s) of {total} represented in incoming_text")
        except Exception as e:
            info.append(f"thread-read: could NOT verify the full thread live ({e}) — read it by hand before trusting this draft")

    # 3c. CRM-FIRST — pull the contact's prior CRM activity BEFORE drafting and make the drafter account for
    #     it. te-log reads the CRM at capture, which is after the send; this brings it forward to draft time.
    if live and email:
        try:
            ef2 = _load("ee-facts.py", "eefacts2")
            # match the exact address OR anyone at the same company domain — a customer's CRM record is
            # often under a different mailbox than the person emailing (Bryony writes from bhalliday@,
            # her CRM contact is consultancy@), and an exact-match-only check passes silently on zero.
            dom = email.split("@")[-1]
            rows = ef2.portal_q(
                "SELECT a.subject, a.activity_type, a.occurred_at FROM contact_activities a "
                "JOIN contacts c ON c.id = a.contact_id WHERE lower(c.email) = %s OR lower(c.email) LIKE %s "
                "ORDER BY a.occurred_at DESC LIMIT 20" % (_lit(email), _lit("%@" + dom)))
            n = len(rows or [])
            declared = (cls.get("crm_reviewed") or {}).get("activity_count") if isinstance(cls.get("crm_reviewed"), dict) else cls.get("crm_reviewed")
            for r in (rows or [])[:6]:
                info.append(f"crm: [{r.get('activity_type')}] {str(r.get('subject'))[:60]} ({str(r.get('occurred_at'))[:10]})")
            if n and declared is None:
                fail("crm-not-checked",
                     f"this contact has {n} prior CRM activity(ies) — read them and declare "
                     f"classification.crm_reviewed = {{\"activity_count\": {n}}} before drafting")
            elif n and isinstance(declared, int) and declared != n:
                fail("crm-not-checked",
                     f"classification.crm_reviewed.activity_count={declared} but the CRM holds {n} — re-read the contact's history")
            else:
                info.append(f"crm-first: {n} prior activity(ies) reviewed for {email}")
        except Exception as e:
            info.append(f"crm-first: could NOT read the CRM live ({e}) — check the contact by hand before trusting this draft")

    # 4. stage-logic — the balance rule, derived
    stage = cls.get("stage")
    balance = cls.get("balance_call")
    if stage not in ("qualify-first", "ready-to-quote"):
        fail("stage-logic", f"classification.stage must be qualify-first|ready-to-quote (got {stage!r})")
    else:
        course_ok = statuses.get("course_type") == "present"
        loc_ok = statuses.get("location") == "present"
        head_ok = statuses.get("headcount") == "present"
        if stage == "ready-to-quote":
            if not (course_ok and loc_ok):
                fail("stage-logic", "ready-to-quote requires course_type AND location/mode present — a soft must-have means qualify-first (no price)")
            elif not head_ok and balance != "quote-with-qualifier":
                fail("stage-logic", "headcount missing: quoting is allowed ONLY as balance_call='quote-with-qualifier' "
                                    "(fixed day rate + ask roughly how many) — declare it or go qualify-first")

    # 5. stage-draft-fit
    pounds = re.findall(r"£\s?[\d,]+", draft)
    agenda_in = "sygma-solutions.com/agendas" in draft
    if stage == "qualify-first":
        if pounds:
            fail("stage-draft-fit", "qualify-first reply must carry NO price — a £ figure belongs in the quote that follows their answer", ", ".join(pounds))
        if not agenda_in:
            fail("stage-draft-fit", "send the course agenda WITH the qualifying questions (Pete rule, 21 Jul 2026) — link the resolved course's agenda")
        asks = {"headcount": r"how many|numbers|team size|group size",
                "location": r"where|which site|address|location|postcode",
                "course_type": r"certif|\bcert\b|route|steer|which course|what course"}
        # the keyword must sit in a paragraph that actually ASKS (carries a '?') — a mention in
        # prose (e.g. 'course agenda' in the link line) is not a question (audit fix, 21 Jul 2026)
        ask_paras = " ".join(q for q in draft.split("\n\n") if "?" in q)
        for key, st in statuses.items():
            if st in ("missing", "ambiguous") and not re.search(asks[key], ask_paras, re.I):
                fail("stage-draft-fit", f"{key} is {st} but no question in the draft asks about it — one clarification round must gather EVERYTHING missing")
    if stage == "ready-to-quote":
        if not agenda_in:
            fail("stage-draft-fit", "a quote goes out with the course agenda link")
        if not pounds:
            fail("stage-draft-fit", "ready-to-quote draft carries no £ figure — either quote it or reclassify qualify-first")

    # 6. full lint, shifted left (the learning surface — rules banked via ee-learn bite HERE)
    try:
        lint = _load("ee-lint.py", "eelint").lint
        lp = dict(p)
        lp.setdefault("lint_overrides", p.get("lint_overrides") or {})
        ok, rep = lint(draft, lp)
        if not ok:
            for f in rep["failures"]:
                fail("lint:" + f["id"], f["reason"], f.get("detail", ""))
        for o in rep.get("overridden", []):
            notes.append({"id": "lint:" + o["id"], "overridden": o.get("overridden", ""), "detail": o.get("detail", "")})
    except Exception as e:
        fail("lint", f"could not run ee-lint at draft time ({e}) — fix the environment, the gate does not pass blind")

    # 7. retrieval — named precedents, existing, each with a takeaway
    refs = p.get("retrieval_refs") or []
    norm_refs = []
    for r in refs:
        if isinstance(r, str):
            norm_refs.append({"slug": r, "takeaway": ""})
        elif isinstance(r, dict):
            norm_refs.append({"slug": r.get("slug") or "", "takeaway": r.get("takeaway") or ""})
    if len(norm_refs) < 2:
        fail("retrieval", "at least 2 worked precedents must be read IN FULL before drafting — name them in retrieval_refs")
    elif live:
        for r in norm_refs:
            if len(r["takeaway"].strip()) < 15:
                fail("retrieval", f"retrieval ref {r['slug']!r} takeaway is empty/trivial — a real line on what it settled proves it was read")
            slug = r["slug"].replace("'", "")
            try:
                rows = _cc("SELECT 1 FROM vault_notes WHERE slug=%s OR vault_path ILIKE %s LIMIT 1"
                           % (_lit(slug), _lit("%" + slug.split("/")[-1].replace(".md", "") + "%")))
                if not rows:
                    fail("retrieval", f"retrieval ref {r['slug']!r} does not resolve to a vault note — name the real precedent")
            except Exception as e:
                info.append(f"retrieval: existence check skipped for {r['slug']!r} ({e})")

    # 8. required-rules — banked rules must be HONOURED in the draft, not merely printed (fail-closed).
    #    Each rule carries its own require_pattern (the presence signal) + scenarios + applies_when,
    #    so a new rule becomes enforceable by editing the DB, no code change (the CLAUDE 'change a
    #    rule in the DB' promise, given teeth). This is the fix for the 22 Jul Wheal Jane run where
    #    the gate printed onsite_fill_upsell as 'honoured' while enforcing nothing.
    scenario = cls.get("scenario")
    cap = cls.get("cap") or (resolved.get("cap") if isinstance(resolved, dict) else None) or 8
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = 8
    headn = _headcount_n(mh)
    is_quote = (stage == "ready-to-quote") or (balance == "quote-with-qualifier")

    def _cond_ok(cond):
        if not cond:
            return True
        if cond == "is_quote":
            return is_quote
        if cond == "ready_to_quote":
            return stage == "ready-to-quote"
        if cond == "headcount_lt_cap":
            return bool(is_quote and cap and headn and headn < cap)
        if cond == "headcount_gt_cap":
            return bool(is_quote and cap and headn and headn > cap)
        return False  # unknown / typo'd applies_when -> SKIP the rule (never enforce on an unrecognised
                      # condition; returning True here silently enforced it unconditionally — bug fix 23 Jul 2026)

    for r in _rules_for(scenario, stage):
        pat = r.get("require_pattern")
        if not pat or not _cond_ok(r.get("applies_when")):
            continue
        eff = pat.replace("{cap}", str(cap)).replace("{cap_word}", CAP_WORDS.get(cap, str(cap)))
        try:
            if not re.search(eff, draft):
                fail("rule:" + r["name"], r.get("fail_hint") or ("draft does not honour banked rule " + r["name"]))
        except re.error:
            info.append("rule:%s has an invalid require_pattern — skipped" % r["name"])

    return (not fails), {"failures": fails, "overridden": notes, "info": info}

def banked_rules():
    try:
        return _cc("SELECT name, body FROM ee_rules WHERE kind='doc' ORDER BY name")
    except Exception:
        return []

def stamp_key(p):
    return p.get("thread_id") or hashlib.sha256((p.get("email") or "unknown").encode()).hexdigest()[:16]

def write_stamp(p):
    a = p.get("activity", {}) or {}
    key = stamp_key(p)
    stamp = {"key": key, "thread_id": p.get("thread_id"), "email": p.get("email"),
             "draft_sha256": hashlib.sha256((a.get("draft_text") or "").encode()).hexdigest(),
             "stage": (p.get("classification") or {}).get("stage"),
             "stamped_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    path = f"/tmp/ee-draft-gate-{key}.json"
    json.dump(stamp, open(path, "w"), indent=1)
    return path, stamp

def verify_stamp(p):
    """For ee-send: (ok, message). Valid = stamp file exists for this thread/email AND its
    draft_sha256 matches sha256(activity.draft_text). A changed draft needs a gate re-run."""
    a = p.get("activity", {}) or {}
    path = f"/tmp/ee-draft-gate-{stamp_key(p)}.json"
    if not os.path.exists(path):
        return False, f"no gate stamp at {path} — run ee-draft-gate.py --in <payload> first"
    try:
        s = json.load(open(path))
    except Exception as e:
        return False, f"gate stamp unreadable ({e})"
    want = hashlib.sha256((a.get("draft_text") or "").encode()).hexdigest()
    if s.get("draft_sha256") != want:
        return False, "gate stamp is for a DIFFERENT draft_text — the draft changed since validation; re-run ee-draft-gate"
    return True, f"gate stamp valid (stage {s.get('stage')}, stamped {s.get('stamped_at')})"

SELFTEST_BASE = {
    "full_name": "Bryony Halliday", "email": "selftest@example.invalid",
    "company_name": "Wheal Jane Consultancy", "thread_id": None,
    "incoming_text": "Hi we are looking for CAT and Genny training for my team of geotechnical and mining engineers. If possible at our offices in Cornwall.",
    "classification": {"course_code": "C001", "scenario": "private-onsite", "stage": "qualify-first",
                       "must_haves": {"location": {"status": "present", "evidence": "at our offices in Cornwall"},
                                      "course_type": {"status": "ambiguous", "evidence": "CAT and Genny training"},
                                      "headcount": {"status": "missing"}}},
    "retrieval_refs": [{"slug": "a", "takeaway": "validated cert-route listing shape"}, {"slug": "b", "takeaway": "open-course alternative for 1-2 people"}],
}
GOOD_DRAFT = ("Hi Bryony,\n\nThanks for getting in touch through our website.\n\n"
              "I've linked the [Genny and CAT course agenda](https://sygma-solutions.com/agendas/hsg47-utility-detection-and-avoidance) here.\n\n"
              "On certification there are a couple of routes, in-house certified or an accredited card, EUSR Cat 1 or ProQual. Happy to give you a steer.\n\n"
              "How many are in the team, and any preference on the certification route?\n\n"
              "Send those over and I'll come back with the full picture on price and dates.\n\nMany thanks")
BAD_DRAFT = ("Hi Bryony,\n\nIt is £965 + VAT per day, up to 8 delegates.\n\n"
             "How many are in the team? Let me know.\n\nMany thanks")

def selftest():
    import copy
    global _RULES_CACHE
    # deterministic offline rule set — mirrors the live enforceable ee_rules
    _RULES_CACHE = [
        {"name": "onsite_fill_upsell", "body": "sub-cap on-site: invite adding more to make up the numbers",
         "scenarios": ["private-onsite"], "applies_when": "headcount_lt_cap",
         "require_pattern": r"(?is)(make (up )?the numbers|add (a few )?more|anybody else|fill the day)",
         "fail_hint": "add more to make up the numbers"},
        {"name": "onsite_cap_statement", "body": "state up-to-cap",
         "scenarios": ["private-onsite"], "applies_when": "is_quote",
         "require_pattern": r"(?is)up to ({cap_word}|{cap})", "fail_hint": "state up to {cap}"},
        {"name": "cert_recommendation", "body": "recommend in-house, CITB, accredited only if client insists",
         "scenarios": ["private-onsite", "open-course"], "applies_when": "is_quote",
         "require_pattern": r"(?is)(?=.*in.?house)(?=.*(citb|only needed|insist|sufficient))",
         "fail_hint": "recommend the in-house cert"},
        {"name": "booking_cta_close", "body": "close with a booking CTA, not a date question",
         "scenarios": ["private-onsite", "open-course", "ready-to-quote"], "applies_when": "ready_to_quote",
         "require_pattern": r"(?is)(get (this |it )?booked|book you in|shall i (get|book)|would you like)",
         "fail_hint": "close with a booking CTA"},
    ]
    ok_count = 0
    p = copy.deepcopy(SELFTEST_BASE); p["activity"] = {"kind": "reply", "draft_text": GOOD_DRAFT}
    passed, rep = gate(p, live=False)
    print(("PASS" if passed else "FAIL"), "- good qualify-first draft should pass:", [f["id"] for f in rep["failures"]])
    ok_count += passed
    p = copy.deepcopy(SELFTEST_BASE); p["activity"] = {"kind": "reply", "draft_text": BAD_DRAFT}
    passed, rep = gate(p, live=False)
    ids = [f["id"] for f in rep["failures"]]
    want = (not passed) and any("stage-draft-fit" == i for i in ids)
    print(("PASS" if want else "FAIL"), "- priced qualify-first draft must block:", ids)
    ok_count += want
    p = copy.deepcopy(SELFTEST_BASE)
    p["classification"]["stage"] = "ready-to-quote"
    p["activity"] = {"kind": "reply", "draft_text": BAD_DRAFT}
    passed, rep = gate(p, live=False)
    ids = [f["id"] for f in rep["failures"]]
    want = (not passed) and any(i == "stage-logic" for i in ids)
    print(("PASS" if want else "FAIL"), "- quote with ambiguous course_type must block:", ids)
    ok_count += want
    p = copy.deepcopy(SELFTEST_BASE)
    p["retrieval_refs"] = []
    p["activity"] = {"kind": "reply", "draft_text": GOOD_DRAFT}
    passed, rep = gate(p, live=False)
    want = (not passed) and any(f["id"] == "retrieval" for f in rep["failures"])
    print(("PASS" if want else "FAIL"), "- no precedents named must block:", [f["id"] for f in rep["failures"]])
    ok_count += want
    p = copy.deepcopy(SELFTEST_BASE)
    p["activity"] = {"kind": "reply", "draft_text": (
        "Hi Bryony,\n\nThanks for getting in touch.\n\n"
        "I've linked the [Genny and CAT course agenda](https://sygma-solutions.com/agendas/hsg47-utility-detection-and-avoidance) here.\n\n"
        "How many are in the team?\n\nSend that over and I'll come back with the full picture.\n\nMany thanks")}
    passed, rep = gate(p, live=False)
    want = (not passed) and any(f["id"] == "stage-draft-fit" for f in rep["failures"])
    print(("PASS" if want else "FAIL"), "- ambiguous course_type never asked about must block:", [f["id"] for f in rep["failures"]])
    ok_count += want
    # fixture 6: a sub-cap on-site QUOTE that omits the fill-the-day upsell must BLOCK (the 22 Jul fix)
    p = copy.deepcopy(SELFTEST_BASE)
    p["classification"]["stage"] = "ready-to-quote"
    p["classification"]["cap"] = 8
    p["classification"]["must_haves"]["course_type"] = {"status": "present", "evidence": "CAT and Genny training"}
    p["classification"]["must_haves"]["headcount"] = {"status": "present", "evidence": "There are 4 in our team"}
    p["incoming_text"] = SELFTEST_BASE["incoming_text"] + " There are 4 in our team."
    p["activity"] = {"kind": "quote", "draft_text": (
        "Hi Bryony,\n\nThanks for those details.\n\n"
        "The in-house certificate is the right fit and it is CITB approved, so it is all your team will need.\n\n"
        "We deliver at your offices. The cost is £965 plus VAT for the day, up to eight people.\n\n"
        "You can see the [Genny and CAT course agenda](https://sygma-solutions.com/agendas/hsg47-utility-detection-and-avoidance).\n\n"
        "Shall I get this booked in for you?\n\nMany thanks")}
    passed, rep = gate(p, live=False)
    ids = [f["id"] for f in rep["failures"]]
    want = (not passed) and any(i == "rule:onsite_fill_upsell" for i in ids)
    print(("PASS" if want else "FAIL"), "- sub-8 on-site quote missing the fill-upsell must block:", ids)
    ok_count += want
    print(f"{ok_count}/6 fixtures behaved")
    return 0 if ok_count == 6 else 1

def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    inpath = None
    for i, x in enumerate(sys.argv[1:]):
        if x == "--in":
            inpath = sys.argv[1:][i + 1]
    if not inpath:
        print(__doc__); sys.exit(2)
    p = json.load(open(inpath))
    passed, rep = gate(p)
    for line in rep.get("info", []):
        print("   ◦", line)
    if not passed:
        print("⛔ DRAFT GATE BLOCKED — fix the payload/draft, or override a check with \"gate_overrides\": {\"<id>\": \"why\"}:")
        for f in rep["failures"]:
            print(f"   ✗ [{f['id']}] {f['reason']}" + (f"  → {f['detail']}" if f.get("detail") else ""))
        sys.exit(2)
    for o in rep.get("overridden", []):
        print(f"   ◦ override [{o['id']}]: {o['overridden']}")
    path, stamp = write_stamp(p)
    cls = p.get("classification") or {}
    mh = cls.get("must_haves") or {}
    print("\n=== EE DRAFT GATE — PASSED · stamp %s ===" % path)
    print("Course %s · %s · stage %s%s" % (cls.get("course_code"), cls.get("scenario"), cls.get("stage"),
          " (%s)" % cls.get("balance_call") if cls.get("balance_call") else ""))
    for k in ("location", "course_type", "headcount"):
        d = mh.get(k) or {}
        print("  %-12s %-9s %s" % (k, d.get("status", "?"), ('"%s"' % d.get("evidence")) if d.get("evidence") else ""))
    rules = _rules_for(cls.get("scenario"), cls.get("stage"))
    if rules:
        print("\nRules for this scenario (%s · %s) — the draft was CHECKED against the enforced ones:"
              % (cls.get("scenario"), cls.get("stage")))
        for r in rules:
            tag = "ENFORCED" if r.get("require_pattern") else "advisory"
            print("  · [%s] %s" % (tag, r["name"]))
            body = (r.get("body") or "").strip()
            if body:
                print("      " + body.replace("\n", "\n      "))
    print("\n--- DRAFT (of record — present THIS to Pete verbatim) ---\n")
    print((p.get("activity") or {}).get("draft_text", ""))
    sys.exit(0)

if __name__ == "__main__":
    main()
