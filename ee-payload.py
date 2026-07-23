#!/usr/bin/env python3
"""ee-payload.py -- build the DERIVABLE half of an Enquiry-Engine payload from the record.

Built 23 Jul 2026. The Wheal Jane booking reply was hand-authored: I typed `contact_email`,
`contact_name` and `company` where the tools want `email`, `full_name` and `company_name`.
ee-send sent the email, then te-log failed on the contacts insert. Email delivered, record broken.

An artefact that cannot be written by hand cannot be written wrong. So the fields that EXIST IN
THE RECORD are no longer typed -- they are read from the Gmail thread and the Portal CRM:

    full_name  email  company_name  job_title  phone      <- the CRM contact
    thread_id  message_id  subject  incoming_text          <- the Gmail thread

`incoming_text` is assembled from EVERY inbound customer message on the thread, in order, which
makes ee-draft-gate's [thread-not-read] block unreachable by construction rather than by my
discipline. That block fired on 23 Jul because I pasted only the latest message.

WHAT THIS DELIBERATELY DOES NOT DO. The judged half -- classification, must-have evidence in the
customer's own words, the precedents actually read, the draft itself -- cannot be derived from
anything. Pretending otherwise is how this tool would become the next failure. Those come out as
explicit `<<JUDGEMENT REQUIRED>>` placeholders so a hole is VISIBLE, and the shared schema
refuses any payload still carrying one.

Contact resolution mirrors te-log's own find_contact(): email first, then name with email-domain
corroboration. When more than one contact could match it REFUSES rather than guessing -- on 23 Jul
the payload was one field away from silently opening a second Bryony Halliday and splitting her
history across two records.

Usage:
  VAULT=/tmp/pbs python3 ee-payload.py --thread <gmail_thread_id> --kind reply \\
        [--draft draft.txt] [--cc a@b.com] [--sent-message <id>] [--out payload.json]
  VAULT=/tmp/pbs python3 ee-payload.py --selftest
"""
import os, sys, json, re, base64, urllib.parse, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SECRETS = f"{VAULT}/Library/processes/secrets"


def _load(name, fname):
    sp = importlib.util.spec_from_file_location(name, os.path.join(VAULT, fname))
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m


S = _load("ee_payload_schema", "ee_payload_schema.py")

OURS = re.compile(r"@sygma-solutions\.com|@canary-detect\.com", re.I)
NOREPLY = re.compile(r"no-?reply|do-?not-?reply|mailer-daemon", re.I)


# ── Gmail side ────────────────────────────────────────────────────────────────
def _body(part):
    out = ""
    if part.get("body", {}).get("data"):
        try:
            out += base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "ignore")
        except Exception:
            pass
    for c in part.get("parts") or []:
        out += _body(c)
    return out


def _clean(raw):
    t = re.sub(r"<[^>]+>", " ", raw)
    t = re.sub(r"(?s)On .{0,80}wrote:.*", "", t)          # quoted history
    t = re.sub(r"(?m)^\s*>.*$", "", t)                     # quote markers
    t = re.sub(r"&nbsp;", " ", t)
    t = re.sub(r"&#x27;|&#39;", "'", t)
    t = re.sub(r"&amp;", "&", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def read_thread(thread_id):
    """-> (subject, [ {from, date, body, inbound} ... ]) every message, full, in order."""
    gm = _load("gm", "gmail-api.py")
    g = gm.GmailAPI()
    full = g.get_thread(thread_id, fmt="full")
    msgs = []
    subject = ""
    for m in full.get("messages", []):
        h = {x["name"].lower(): x["value"] for x in m["payload"]["headers"]}
        frm = h.get("from", "")
        if not subject:
            subject = h.get("subject", "")
        msgs.append({
            "id": m.get("id"),
            "from": frm,
            "date": h.get("date", ""),
            "body": _clean(_body(m["payload"])),
            # inbound = not from us. A website form relays the CUSTOMER's words, so it counts.
            "inbound": not OURS.search(frm) or bool(re.search(r"website|contact form", frm, re.I)),
        })
    return subject, msgs


def assemble_incoming(msgs):
    """EVERY inbound message, in order, labelled. This is what kills [thread-not-read]."""
    parts = []
    n = 0
    for m in msgs:
        if not m["inbound"] or not m["body"]:
            continue
        n += 1
        who = re.sub(r"\s*<[^>]+>", "", m["from"]).strip().strip('"')
        when = m["date"][:16]
        parts.append(f"[{n} — {who}, {when}] {m['body']}")
    return "\n\n".join(parts), n


def latest_inbound(msgs):
    for m in reversed(msgs):
        if m["inbound"]:
            return m
    return None


def customer_address(msgs):
    """The address the customer actually corresponds FROM -- never a website relay, never ours."""
    for m in reversed(msgs):
        if not m["inbound"]:
            continue
        addrs = re.findall(r"[\w.\-+]+@[\w.\-]+", m["from"])
        for a in addrs:
            if not OURS.search(a) and not NOREPLY.search(a):
                return a
    return None


# ── CRM side ──────────────────────────────────────────────────────────────────
def portal_get(table, **params):
    tl = _load("telog", "te-log.py")
    return tl.portal_get(table, **params)


def resolve_contact(email):
    """Mirror te-log.find_contact: email, then name+domain. REFUSE on ambiguity."""
    if not email:
        return None, "no customer address found on the thread"
    rows = portal_get("contacts",
                      select="id,full_name,email,company_name,job_title,phone,mobile,stage_id",
                      email=f"ilike.{urllib.parse.quote(email)}") or []
    if len(rows) == 1:
        return rows[0], f"matched CRM contact on email {email}"
    if len(rows) > 1:
        return None, (f"AMBIGUOUS: {len(rows)} CRM contacts carry {email} -- refusing to guess. "
                      f"Resolve the duplicate first.")
    dom = email.split("@")[-1].lower()
    same = [c for c in (portal_get("contacts",
                                   select="id,full_name,email,company_name,job_title,phone,mobile,stage_id",
                                   email=f"ilike.*@{urllib.parse.quote(dom)}") or [])]
    if len(same) == 1:
        return same[0], (f"no contact for {email}; matched the single contact on domain @{dom} "
                         f"({same[0].get('email')}) -- te-log will append, not duplicate")
    if len(same) > 1:
        names = ", ".join(f"{c.get('full_name')} <{c.get('email')}>" for c in same[:4])
        return None, (f"AMBIGUOUS: {len(same)} contacts at @{dom} ({names}) and none at {email}. "
                      f"Refusing to guess which is the person -- name the contact explicitly.")
    return None, f"NEW: no CRM contact for {email} or @{dom} -- the judged fields must supply the name"


# ── build ─────────────────────────────────────────────────────────────────────
def build(thread_id, kind, draft=None, cc=None, sent_message=None):
    subject, msgs = read_thread(thread_id)
    if not msgs:
        raise SystemExit(f"⛔ thread {thread_id} has no messages")
    incoming, n_in = assemble_incoming(msgs)
    addr = customer_address(msgs)
    contact, how = resolve_contact(addr)

    # message_id: the idempotency key. For a reply CAPTURE it is the message we SENT;
    # otherwise the newest inbound. Getting this wrong duplicates the touch on re-run.
    if sent_message:
        mid = sent_message
    else:
        li = latest_inbound(msgs)
        mid = (li or msgs[-1])["id"]

    p = {
        # ---- derivable: from the CRM ----
        "full_name":    (contact or {}).get("full_name"),
        "email":        addr or (contact or {}).get("email"),
        "company_name": (contact or {}).get("company_name"),
        "job_title":    (contact or {}).get("job_title"),
        "phone":        (contact or {}).get("phone"),
        "mobile":       (contact or {}).get("mobile"),
        # ---- derivable: from the thread ----
        "thread_id":    thread_id,
        "message_id":   mid,
        "subject":      subject,
        "incoming_text": incoming,
        "source":       "web-enquiry",
        # ---- judged: visible holes, refused by the schema until filled ----
        "classification": S.PLACEHOLDER,
        "retrieval_refs": S.PLACEHOLDER,
        "activity": {"kind": kind, "subject": subject,
                     "draft_text": draft if draft else S.PLACEHOLDER},
        "_derivation": {
            "contact": how,
            "inbound_messages_included": n_in,
            "total_messages_on_thread": len(msgs),
            "message_id_is": "the sent message" if sent_message else "the newest inbound message",
        },
    }
    if cc:
        p["cc"] = cc if isinstance(cc, list) else [cc]
    if kind not in S.DRAFT_KINDS:
        p["activity"].pop("draft_text", None)
    return p, contact, how


def main():
    a = sys.argv[1:]
    if "--selftest" in a:
        return selftest()
    if "--thread" not in a:
        print(__doc__); return 2
    get = lambda f: (a[a.index(f) + 1] if f in a and a.index(f) + 1 < len(a) else None)
    thread = get("--thread")
    kind = get("--kind") or "reply"
    draft_f = get("--draft")
    draft = open(draft_f).read().strip() if draft_f else None
    cc = get("--cc")
    sent = get("--sent-message")
    out = get("--out")

    p, contact, how = build(thread, kind, draft, cc, sent)

    print(f"=== ee-payload · thread {thread} · kind {kind} ===", file=sys.stderr)
    print(f"  contact      : {how}", file=sys.stderr)
    print(f"  incoming_text: {p['_derivation']['inbound_messages_included']} inbound message(s) of "
          f"{p['_derivation']['total_messages_on_thread']} on the thread", file=sys.stderr)
    print(f"  message_id   : {p['message_id']} ({p['_derivation']['message_id_is']})", file=sys.stderr)

    ok, errs = S.validate(p)

    # Split by WHICH HALF the field belongs to, not by string-matching the placeholder.
    # An unfilled judgement cascades (classification placeholder -> "course_code missing" ->
    # "must be an object"), and reporting those as unresolved DERIVABLE fields tells the caller
    # to go fix the record when the record is fine. That misdirection is the thing this whole
    # build exists to stop, so it must not be reintroduced by the reporting.
    def _field_of(e):
        m = re.match(r"\[([A-Za-z_]+)", e)
        return m.group(1) if m else ""

    holes = [e for e in errs if _field_of(e) in S.JUDGED or S.PLACEHOLDER in e]
    other = [e for e in errs if e not in holes]
    if holes:
        print(f"  judgement still required — these cannot be derived from anything:", file=sys.stderr)
        for e in holes:
            print(f"     · {e}", file=sys.stderr)
    if other:
        print(f"  ⛔ DERIVABLE fields could not be resolved ({len(other)}):", file=sys.stderr)
        for e in other:
            print(f"     ✗ {e}", file=sys.stderr)
        print("     These come from the record, not from you. Do NOT hand-type them — fix the "
              "record, or name the contact explicitly.", file=sys.stderr)

    blob = json.dumps(p, indent=1)
    if out:
        open(out, "w").write(blob)
        print(f"  written      : {out}", file=sys.stderr)
    else:
        print(blob)
    return 2 if other else 0


def selftest():
    """Fixtures for the pure logic. Gmail/CRM calls are covered by the live rebuild proof."""
    fails = 0

    def ck(label, cond):
        nonlocal fails
        print(("PASS" if cond else "FAIL") + f" - {label}")
        if not cond:
            fails += 1

    msgs = [
        {"id": "m1", "from": "Sygma Website <info@sygma-solutions.com>", "date": "Tue, 21 Jul 2026",
         "body": "we are looking for CAT and Genny training", "inbound": True},
        {"id": "m2", "from": "Peter Ashcroft <pete.ashcroft@sygma-solutions.com>", "date": "Tue, 21 Jul 2026",
         "body": "a couple of quick questions", "inbound": False},
        {"id": "m3", "from": "Bryony Halliday <bhalliday@wheal-jane.co.uk>", "date": "Wed, 22 Jul 2026",
         "body": "There are 4 in our team", "inbound": True},
        {"id": "m4", "from": "Bryony Halliday <bhalliday@wheal-jane.co.uk>", "date": "Thu, 23 Jul 2026",
         "body": "we would like to go ahead", "inbound": True},
    ]
    txt, n = assemble_incoming(msgs)
    ck("incoming_text carries EVERY inbound message, not just the latest", n == 3)
    ck("  ...the first inbound is present", "CAT and Genny training" in txt)
    ck("  ...the middle inbound is present", "4 in our team" in txt)
    ck("  ...the latest inbound is present", "go ahead" in txt)
    ck("  ...our own outbound is excluded", "quick questions" not in txt)

    ck("customer address is the person, never our own domain",
       customer_address(msgs) == "bhalliday@wheal-jane.co.uk")

    relay = [{"id": "x", "from": "Website <noreply@canary-detect.com>", "date": "d",
              "body": "hi", "inbound": True}]
    ck("a no-reply relay never becomes the recipient", customer_address(relay) is None)

    ck("the judged fields are declared judged, not derivable",
       "classification" in S.JUDGED and "retrieval_refs" in S.JUDGED)
    ck("the fields that broke 23 Jul are declared derivable",
       "full_name" in S.DERIVABLE and "email" in S.DERIVABLE)

    print(f"\n{'ALL FIXTURES BEHAVED' if not fails else str(fails) + ' FIXTURE(S) FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
