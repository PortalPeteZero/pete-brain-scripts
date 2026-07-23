#!/usr/bin/env python3
"""ee_payload_schema.py -- the ONE declaration of what an Enquiry-Engine payload must contain.

Built 23 Jul 2026 after the Wheal Jane booking reply: the payload was hand-authored, carried
`contact_email`/`contact_name`/`company` where the tools want `email`/`full_name`/`company_name`,
and nothing validated it as an object. `ee-send` sent the email, THEN `te-log` failed on the
contacts insert (23502, full_name NOT NULL). Email delivered, record broken.

Why a shared module and not a check inside each tool: three tools consume this payload
(ee-draft-gate, ee-send, te-log) and each was discovering its own missing field one round trip
at a time. Two implementations of the same schema would drift, and a drifting schema is exactly
the fault this exists to prevent. So it is declared once, here, and imported.

THE PAYLOAD HAS TWO HALVES -- this distinction is load-bearing:

  DERIVABLE  built by code from the Gmail thread + the Portal CRM. Never typed by hand.
             (contact fields, thread_id, message_id, incoming_text, subject, cc)
             `ee-payload.py` emits these. This is the half that broke.

  JUDGED     cannot be derived from anything: the classification, the must-have evidence in
             the customer's own words, the precedents actually read, and the draft itself.
             These stay human. The schema can only insist they are PRESENT and well-shaped;
             `ee-draft-gate` is what judges whether they are RIGHT.

A missing judged field is emitted by the builder as an explicit null placeholder, so a hole is
visible rather than an absent key -- and `require()` refuses a payload with a placeholder left in.

Usage:
    import ee_payload_schema as S
    ok, errors = S.validate(payload)          # every consumer's requirements for this kind
    ok, errors = S.validate(payload, for_tool="te-log")
    S.DERIVABLE / S.JUDGED                    # the field split, for the builder
"""

# ── the field table ──────────────────────────────────────────────────────────────
# name: (half, required_for_kinds | None = optional, consumer that fails without it, why)
# "*" = every kind. A tuple of kinds = only those.

FIELDS = {
    # --- derivable: contact identity (resolved from the CRM, never typed) -------------
    "full_name":    ("derivable", "*",                "te-log",
                     "contacts.full_name is NOT NULL -- a missing one is the 23502 that broke 23 Jul"),
    "email":        ("derivable", "*",                "ee-send/te-log",
                     "ee-send refuses without a recipient; te-log matches the CRM contact on it"),
    "company_name": ("derivable", None,               "te-log",
                     "written to the contact on create; absent is survivable, wrong is not"),
    "job_title":    ("derivable", None,               "te-log", "contact enrichment"),
    "phone":        ("derivable", None,               "te-log", "contact enrichment"),
    "mobile":       ("derivable", None,               "te-log", "contact enrichment"),

    # --- derivable: the thread ------------------------------------------------------
    "thread_id":    ("derivable", "*",                "ee-send/te-log",
                     "threads the reply; without it ee-send sends an orphan and te-log cannot file"),
    "message_id":   ("derivable", "*",                "te-log",
                     "THE idempotency key -- a null message_id duplicates the touch on every re-run"),
    "incoming_text":("derivable", ("reply", "quote"), "ee-draft-gate",
                     "must represent EVERY inbound customer message, not just the latest"),
    "subject":      ("derivable", None,               "ee-send", "falls back to a generic subject"),
    "cc":           ("derivable", None,               "ee-send", "optional copy list"),
    "source":       ("derivable", None,               "te-log", "defaults to web-enquiry"),

    # --- judged: never derivable ----------------------------------------------------
    "activity":         ("judged", "*",                "all three", "carries kind + draft_text"),
    "classification":   ("judged", ("reply", "quote"), "ee-draft-gate",
                         "course_code, scenario, stage, and each must-have with the customer's words"),
    "retrieval_refs":   ("judged", ("reply", "quote", "enquiry"), "ee-send/ee-draft-gate",
                         ">=2 precedents actually read, each with a takeaway"),
    "tags":             ("judged", None,               "te-log", "course cluster"),
    "knowledge":        ("judged", None,               "te-log", "the distilled lesson"),
}

DERIVABLE = tuple(k for k, v in FIELDS.items() if v[0] == "derivable")
JUDGED    = tuple(k for k, v in FIELDS.items() if v[0] == "judged")

# a judged field the builder could not fill is emitted as this, so the hole is visible
PLACEHOLDER = "<<JUDGEMENT REQUIRED>>"

# kinds that carry a draft of record
DRAFT_KINDS = ("reply", "quote")
VALID_KINDS = ("enquiry", "reply", "quote", "chase", "handoff", "correction", "note",
               "call", "meeting", "booked", "won", "lost", "scrub")


def _kind(payload):
    return ((payload.get("activity") or {}).get("kind") or "").strip()


def _required(spec_kinds, kind):
    if spec_kinds is None:
        return False
    return spec_kinds == "*" or kind in spec_kinds


def validate(payload, for_tool=None):
    """(ok, errors). Each error names the field, the consumer that would fail, and why.

    for_tool narrows to one consumer's requirements; default checks all three, which is what
    you want before an irreversible send.
    """
    errs = []
    if not isinstance(payload, dict):
        return False, ["payload is not an object"]

    kind = _kind(payload)
    if not kind:
        return False, ["activity.kind is missing -- every consumer branches on it"]
    if kind not in VALID_KINDS:
        errs.append(f"activity.kind '{kind}' is not one of {VALID_KINDS}")

    for name, (half, kinds, consumer, why) in FIELDS.items():
        if for_tool and for_tool not in consumer:
            continue
        val = payload.get(name)
        needed = _required(kinds, kind)

        if needed and (val is None or val == "" or val == []):
            errs.append(f"[{name}] missing -- {consumer} fails without it: {why}")
            continue
        if val == PLACEHOLDER or (isinstance(val, str) and PLACEHOLDER in val):
            errs.append(f"[{name}] still holds the {PLACEHOLDER} placeholder -- a judgement was never made")

    # --- activity internals -------------------------------------------------------
    a = payload.get("activity") or {}
    if kind in DRAFT_KINDS:
        if not (a.get("draft_text") or a.get("final_text")):
            errs.append("[activity.draft_text] missing -- it IS the edit-free/edited signal "
                        "ee-signoff blocks on; a top-level draft_text is silently ignored")
        if payload.get("draft_text") and not a.get("draft_text"):
            errs.append("[draft_text] is at the TOP LEVEL -- te-log reads activity.draft_text only, "
                        "so this one is silently dropped (the 20 Jul miss)")

    # --- classification internals -------------------------------------------------
    if _required(FIELDS["classification"][1], kind):
        c = payload.get("classification")
        if c is not None and not isinstance(c, dict):
            errs.append("[classification] must be an object, not %s" % type(c).__name__)
            c = {}
        c = c or {}
        for k in ("course_code", "scenario", "stage"):
            if not c.get(k):
                errs.append(f"[classification.{k}] missing -- ee-draft-gate derives the stage logic from it")
        mh = c.get("must_haves")
        if mh is not None and not isinstance(mh, dict):
            errs.append("[classification.must_haves] must be an object, not %s" % type(mh).__name__)
            mh = {}
        mh = mh or {}
        for k in ("location", "course_type", "headcount"):
            if k not in mh:
                errs.append(f"[classification.must_haves.{k}] missing -- each must-have is declared "
                            "present|ambiguous|missing")
            elif not isinstance(mh[k], dict):
                errs.append(f"[classification.must_haves.{k}] must be an object")
            else:
                st = (mh[k] or {}).get("status")
                if st in ("present", "ambiguous") and not (mh[k] or {}).get("evidence"):
                    errs.append(f"[classification.must_haves.{k}] status '{st}' with no evidence -- "
                                "the customer's own words are required")

    # --- retrieval ----------------------------------------------------------------
    if _required(FIELDS["retrieval_refs"][1], kind):
        refs = payload.get("retrieval_refs") or []
        if not isinstance(refs, list):
            errs.append("[retrieval_refs] must be a list")
            refs = []
        if len(refs) < 2:
            errs.append(f"[retrieval_refs] {len(refs)} supplied, >=2 required -- the runnable form of "
                        "'name the worked replies you actually read'")

    # --- the alias trap that caused 23 Jul ---------------------------------------
    for wrong, right in (("contact_email", "email"), ("contact_name", "full_name"),
                         ("company", "company_name"), ("name", "full_name")):
        if payload.get(wrong) and not payload.get(right):
            errs.append(f"[{wrong}] is not a field any consumer reads -- it is '{right}'. "
                        "This exact alias broke the 23 Jul capture AFTER the email had gone.")

    return (not errs), errs


def require(payload, for_tool=None):
    """validate() or raise ValueError with every failure listed. For call sites that must not proceed."""
    ok, errs = validate(payload, for_tool=for_tool)
    if not ok:
        raise ValueError("payload schema check FAILED (%d):\n  - %s" % (len(errs), "\n  - ".join(errs)))
    return True


def selftest():
    """Regression fixtures. Each asserts a fault this schema exists to catch."""
    fails = 0

    def check(label, payload, must_mention):
        nonlocal fails
        ok, errs = validate(payload)
        blob = " ".join(errs)
        hit = (must_mention is None and ok) or (must_mention and must_mention in blob)
        print(("PASS" if hit else "FAIL") + f" - {label}" + ("" if hit else f"  -> got {errs}"))
        if not hit:
            fails += 1

    good = {
        "full_name": "Bryony Halliday", "email": "b@wheal-jane.co.uk",
        "company_name": "Wheal Jane Consultancy",
        "thread_id": "T1", "message_id": "M1",
        "incoming_text": "we would like to go ahead",
        "classification": {"course_code": "C001", "scenario": "private-onsite", "stage": "ready-to-quote",
                           "must_haves": {"location": {"status": "present", "evidence": "Cornwall"},
                                          "course_type": {"status": "present", "evidence": "in house"},
                                          "headcount": {"status": "present", "evidence": "4 in our team"}}},
        "retrieval_refs": [{"slug": "a", "takeaway": "x"}, {"slug": "b", "takeaway": "y"}],
        "activity": {"kind": "reply", "draft_text": "Hi Bryony"},
    }
    check("a complete reply payload validates", good, None)

    # THE 23 JUL FAILURE -- this is the fixture that matters
    bad = {k: v for k, v in good.items() if k != "full_name"}
    check("missing full_name is caught BEFORE the send (the 23 Jul break)", bad, "[full_name] missing")

    alias = {k: v for k, v in good.items() if k != "email"}
    alias["contact_email"] = "b@wheal-jane.co.uk"
    check("the contact_email alias is named and corrected", alias, "it is 'email'")

    nomsg = {k: v for k, v in good.items() if k != "message_id"}
    check("missing message_id (the idempotency key) is caught", nomsg, "[message_id] missing")

    toplevel = {k: v for k, v in good.items()}
    toplevel["activity"] = {"kind": "reply"}
    toplevel["draft_text"] = "Hi Bryony"
    check("a top-level draft_text is caught as silently dropped", toplevel, "TOP LEVEL")

    noev = json_copy(good)
    noev["classification"]["must_haves"]["location"] = {"status": "present"}
    check("a must-have claimed present with no evidence is caught", noev, "no evidence")

    oneref = json_copy(good)
    oneref["retrieval_refs"] = [{"slug": "a", "takeaway": "x"}]
    check("fewer than two precedents is caught", oneref, ">=2 required")

    ph = json_copy(good)
    ph["classification"] = PLACEHOLDER
    check("an unfilled judgement placeholder is caught", ph, "placeholder")

    print(f"\n{'ALL FIXTURES BEHAVED' if not fails else str(fails) + ' FIXTURE(S) FAILED'}")
    return 1 if fails else 0


def json_copy(o):
    import json as _j
    return _j.loads(_j.dumps(o))


if __name__ == "__main__":
    import sys
    sys.exit(selftest() if "--selftest" in sys.argv else print(__doc__) or 0)
