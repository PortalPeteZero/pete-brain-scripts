#!/usr/bin/env python3
"""
triage-action-classify.py -- v1.8 history pre-pass for inbox-triage.

For every thread in an inbox dump (JSON list of thread summaries with bodies),
emit a draft `Ask` classification from the fixed vocabulary:

    none       -- nothing in body asks Pete to do anything (handled / Pete already replied)
    info-only  -- pure FYI (newsletters, status, marketing)
    reply      -- thread asks/expects a reply from Pete
    decision   -- requires Pete to approve / reject / pay / sign off
    review     -- doc / quote / invoice / report needs Pete's eyeball
    rsvp       -- meeting / event needs accept / decline

The output is a DRAFT -- Claude reviews and corrects each classification
in-line before building the Step 5 ops table. Without this output (or
equivalent in-memory equivalent), Step 5 cannot run -- Step 6.0 refuses.

Heuristic rules (in order of precedence):

 1. Latest message FROM Pete (sent direction)        -> none
 2. Pete already replied AFTER latest external msg   -> none
 3. Auto-confirm subjects (receipt/UP/deployed/etc)  -> info-only
 4. Cold sales / marketing patterns                  -> info-only
 5. Calendar invites / RSVP requests                 -> rsvp
 6. Direct question to Pete (?, please, let me know) -> reply
 7. Internal-staff forwards with action language     -> decision/review
 8. Document shares (Google Doc, attached PDF/quote) -> review
 9. Recovery / status alerts ("UP", "succeeded")     -> info-only
10. Empty-body forwards from internal staff          -> reply

Caller passes Pete's email address via --pete (default pete.ashcroft@sygma-solutions.com).

Usage:
    python3 triage-action-classify.py /tmp/inbox-bodies.json > /tmp/triage-ask.json

Library:
    from triage_action_classify import classify_thread, classify_inbox
    drafts = classify_inbox(inbox_dump, pete_email="pete.ashcroft@sygma-solutions.com")
"""
from __future__ import annotations
import json
import re
import sys
from typing import Iterable

PETE_DEFAULT = "pete.ashcroft@sygma-solutions.com"

# Auto-confirm subject patterns -- info-only regardless of body
AUTO_CONFIRM_SUBJECT = re.compile(
    r"\b("
    r"payment\s+(received|confirmation|applied)|"
    r"your\s+(receipt|statement|order|invoice|card\s+statement)|"
    r"is\s+up\b|"
    r"deployed|"
    r"deployment\s+(succeeded|completed)|"
    r"workflow\s+(passed|succeeded)|"
    r"build\s+(succeeded|passed)|"
    r"welcome|"
    r"confirmation|"
    r"receipt"
    r")\b",
    re.I,
)

# Recovery alerts -- info-only
RECOVERY_BODY = re.compile(
    r"\b("
    r"is\s+up\s+again|"
    r"monitor\s+is\s+up|"
    r"recovered|"
    r"resolved|"
    r"all\s+systems\s+go|"
    r"deployment\s+succeeded"
    r")\b",
    re.I,
)

# Cold sales / marketing patterns
COLD_SALES = re.compile(
    r"\b("
    r"15\s*min(s|utes)?\s+to\s+(show|demo)|"
    r"growth\s+(specialist|hacking|hacker)|"
    r"we\s+help\s+companies\s+like\s+yours|"
    r"book\s+a\s+(call|demo)|"
    r"free\s+(trial|demo|consultation)|"
    r"calendly\.com|"
    r"unsubscribe.*marketing|"
    r"promote\s+your\s+business"
    r")\b",
    re.I,
)

# Newsletter signals
NEWSLETTER = re.compile(
    r"\b("
    r"daily\s+digest|"
    r"weekly\s+digest|"
    r"monthly\s+digest|"
    r"newsletter|"
    r"unsubscribe"
    r")\b",
    re.I,
)

# Calendar / RSVP
RSVP_SIGNAL = re.compile(
    r"\b("
    r"please\s+confirm\s+attendance|"
    r"menu\s+choice|"
    r"rsvp|"
    r"you('re|\s+are)\s+invited|"
    r"summons|"
    r"please\s+attend|"
    r"will\s+you\s+(be|attend)"
    r")\b",
    re.I,
)

# Internal-staff forward action signals
INTERNAL_FORWARD_ACTION = re.compile(
    r"\b("
    r"needs?\s+paying|"
    r"this\s+needs|"
    r"please\s+(check|review|approve|pay|action)|"
    r"can\s+you\s+(check|look\s+at|approve)|"
    r"asap|"
    r"urgent|"
    # payment-escalation vocabulary (10 Jul 2026 first live run: the ProQual
    # 'Overdue Account' forward drafted info-only; a forwarded supplier
    # escalation is a decision even with no comment from the forwarder)
    r"overdue|"
    r"escalation\s+process|"
    r"must\s+be\s+settled|"
    r"final\s+reminder|"
    r"breach\s+of\s+[\w'’]+\s*(payment\s+)?terms|"
    r"outstanding\s+balance"
    r")\b",
    re.I,
)

# Document share / review signals
DOC_REVIEW = re.compile(
    r"\b("
    r"shared\s+(a\s+)?document|"
    r"shared\s+with\s+you|"
    r"please\s+find\s+attached|"
    r"see\s+attached|"
    r"quote\s+(attached|enclosed)|"
    r"invoice\s+(attached|enclosed)"
    r")\b",
    re.I,
)

# Direct question signals
DIRECT_QUESTION = re.compile(
    r"("
    r"\?\s*$|"  # ends with ?
    r"\bplease\s+(let\s+me\s+know|advise|confirm|reply|respond)\b|"
    r"\bcan\s+you\s+(let\s+me\s+know|tell\s+me|confirm)\b|"
    r"\bcould\s+you\s+(let\s+me\s+know|tell\s+me|confirm)\b|"
    r"\bwhat'?s\s+your\s+(view|take|opinion|thinking)\b|"
    r"\bwhat\s+do\s+you\s+think\b|"
    r"\bare\s+you\s+(able|free|ok|happy)\b|"
    r"\bdo\s+you\s+(want|need|have)\b"
    r")",
    re.I,
)


def is_pete(addr: str, pete_email: str) -> bool:
    """Cheap match on pete's email anywhere in the From header."""
    if not addr:
        return False
    return pete_email.lower() in addr.lower()


def latest_external_after_pete_reply(messages: list[dict], pete_email: str) -> bool:
    """
    Return True if there is an external message LATER than Pete's most recent
    sent message in the thread. False if Pete's last message is the latest
    (i.e. Pete has already replied).
    """
    if not messages:
        return True  # default conservative
    # Walk in chronological order
    last_pete_idx = -1
    for i, m in enumerate(messages):
        from_header = m.get("from", "") or m.get("from_last", "")
        if is_pete(from_header, pete_email):
            last_pete_idx = i
    if last_pete_idx == -1:
        return True  # Pete never replied -- external is latest
    # Is there any external msg after Pete's last reply?
    for m in messages[last_pete_idx + 1 :]:
        from_header = m.get("from", "") or m.get("from_last", "")
        if not is_pete(from_header, pete_email):
            return True
    return False


def classify_thread(thread: dict, pete_email: str = PETE_DEFAULT) -> dict:
    """
    Classify a single thread dict. Expected keys (best effort -- missing keys
    fall back gracefully):

        thread_id / tid       -- Gmail thread id
        subject               -- thread subject
        from_first / from_last
        last_body             -- body text of latest message
        first_body            -- body text of first message
        msgs / msg_count      -- message count
        user_labels           -- list of user-applied label names
        messages              -- optional full message list with from + body per msg
    """
    tid = thread.get("thread_id") or thread.get("tid", "")
    subject = thread.get("subject", "") or ""
    from_first = thread.get("from_first", "") or ""
    from_last = thread.get("from_last", "") or ""
    last_body = thread.get("last_body", "") or ""
    first_body = thread.get("first_body", "") or ""
    msg_count = thread.get("msgs", thread.get("msg_count", 1))
    user_labels = thread.get("user_labels", []) or []

    has_actions = "Replies" in user_labels or "Actions" in user_labels  # transition-safe: tray renamed Actions→Replies 2026-06-25
    has_delegated = "Delegated" in user_labels
    has_linked_task = thread.get("has_linked_asana_task", False)

    # Direction of latest message
    latest_direction = "external"
    if is_pete(from_last, pete_email):
        latest_direction = "pete-sent"
    elif "michaela.ashcroft" in from_last.lower() or "@sygma-solutions.com" in from_last.lower():
        # Internal staff (could be a forward to Pete)
        latest_direction = "internal-forward"

    # If full message list is provided, do a proper Pete-replied-since check
    pete_replied_since = False
    if "messages" in thread and isinstance(thread["messages"], list):
        # If Pete's latest is more recent than the latest external -> Pete replied
        pete_replied_since = not latest_external_after_pete_reply(
            thread["messages"], pete_email
        )
    elif latest_direction == "pete-sent":
        # Heuristic without full list: latest from Pete -> Pete is the last to act
        pete_replied_since = True

    # Detect open question in latest external message
    open_question = False
    body_for_question = last_body
    if latest_direction == "pete-sent" and first_body:
        # Pete's reply is latest -- check the first message for the original ask context
        body_for_question = first_body
    if DIRECT_QUESTION.search(body_for_question or ""):
        open_question = True

    # ----- Classification cascade (highest precedence first) -----

    # Rule 1: latest is Pete-sent -> none (ball on the other side)
    if latest_direction == "pete-sent":
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "none",
                      "Pete sent the latest message in the thread -- ball is on the other side.")

    # Rule 2: Pete already replied after latest external -> none
    if pete_replied_since:
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "none",
                      "Pete has already replied since the latest external message.")

    # Rule 3: auto-confirm subjects -> info-only
    if AUTO_CONFIRM_SUBJECT.search(subject):
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "info-only",
                      f"Auto-confirm subject pattern: '{subject[:60]}'")

    # Rule 9: recovery alerts (also info-only)
    if RECOVERY_BODY.search(subject) or RECOVERY_BODY.search(last_body[:500]):
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "info-only",
                      "Recovery / status alert (system says 'I'm fine now') -- not an ask.")

    # Rule 5: calendar / RSVP signals
    if RSVP_SIGNAL.search(subject) or RSVP_SIGNAL.search(last_body[:1500]):
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "rsvp",
                      "RSVP / attendance signal in subject or body.")

    # Rule 4: cold sales / marketing
    if COLD_SALES.search(last_body[:1500]) or NEWSLETTER.search(last_body[:1500]):
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "info-only",
                      "Cold sales / newsletter pattern in body.")

    # Rule 11 (10 Jul 2026, first live run): conversation in flight — the latest
    # message is external ON A THREAD WHERE PETE HAS A PRIOR OUTBOUND (he is
    # mid-conversation and the counterparty just continued it). Tom Delaney's
    # enquiry reply carried no "?" and fell to the info-only default. Guard:
    # never fires on auto-replies/OOO (subject) — those are noise, not turns.
    prior_pete_outbound = bool(thread.get("prior_pete_outbound"))
    if not prior_pete_outbound and isinstance(thread.get("messages"), list) and len(thread["messages"]) > 1:
        prior_pete_outbound = any(is_pete(m.get("from", ""), pete_email)
                                  for m in thread["messages"][:-1])
    if (latest_direction == "external" and prior_pete_outbound
            and not re.search(r"out of office|automatic reply|auto[- ]?reply", subject, re.I)):
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "reply",
                      "Conversation in flight: counterparty replied on a thread Pete has already written on — ball is Pete's.")

    # Rule 7: internal-staff forwards with action language
    if latest_direction == "internal-forward" and INTERNAL_FORWARD_ACTION.search(last_body[:1500]):
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "decision",
                      "Internal staff forward with action language ('needs paying' / 'please review').")

    # Rule 10: empty-body forward from internal staff -> reply (ask the forwarder)
    if latest_direction == "internal-forward" and len((last_body or "").strip()) < 100:
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "reply",
                      "Internal staff forward with essentially empty body -- ask the forwarder what was meant.")

    # Rule 8: document share / review signals
    if DOC_REVIEW.search(last_body[:2000]) or DOC_REVIEW.search(subject):
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "review",
                      "Document share / attached quote / attached invoice -- needs Pete's eyeball.")

    # Rule 6: direct question to Pete
    if open_question:
        return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                      has_actions, has_linked_task,
                      "reply",
                      "Direct question / explicit ask in latest message body.")

    # Default: info-only (we leaned conservative on action; Pete corrected v1.7-and-earlier
    # for over-classifying; v1.8 default is info-only when no signal triggers).
    return _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
                  has_actions, has_linked_task,
                  "info-only",
                  "No action signal triggered -- defaulting to info-only.")


import re as _re
# Action/Task verb split (2026-06-06): signals that the action happens OUTSIDE
# the email (pay / process / portal / build) → suggest the Asana-only `Task`
# verb. Everything actionable without these signals defaults to `Reply`
# (tray). Claude reviews — this is a hint, not a decision.
_TASK_SHAPED = _re.compile(
    r"\b(invoice|statement of account|statement from|remittance|payment due|"
    r"pay(ment)? (is )?(now )?(due|outstanding)|amount (due|outstanding)|"
    r"presupuesto|factura|cert(ificate)?s? (batch|issue|upload)|upload.*sharepoint|"
    r"jotform|spreadsheet|master sheet|portal upload)\b", _re.I)


def _suggest_verb(ask, subject, body):
    if ask in ("reply", "rsvp"):
        return "action"
    if ask in ("decision", "review"):
        blob = f"{subject}\n{(body or '')[:2000]}"
        return "task" if _TASK_SHAPED.search(blob) else "action"
    return None  # none / info-only — no task-bearing verb


def _result(tid, msg_count, latest_direction, pete_replied_since, open_question,
            has_actions, has_linked_task, ask, reason, subject="", last_body=""):
    return {
        "thread_id": tid,
        "msg_count": msg_count,
        "latest_direction": latest_direction,
        "pete_replied_since_last_external": pete_replied_since,
        "open_question_in_latest": open_question,
        "has_actions_label": has_actions,
        "has_linked_asana_task": has_linked_task,
        "ask_classification": ask,
        "ask_reason": reason,
        "suggested_verb": _suggest_verb(ask, subject, last_body),
    }


def classify_inbox(inbox_dump: Iterable[dict], pete_email: str = PETE_DEFAULT,
                   use_facts: bool = True) -> list[dict]:
    out = []
    for t in inbox_dump:
        r = classify_thread(t, pete_email=pete_email)
        # Verb hint post-pass (2026-06-06 Action/Task split): recompute with the
        # thread's actual subject + latest body so decision/review hints see text.
        msgs = t.get("messages") or []
        subject = (msgs[-1].get("subject") if msgs else "") or ""
        last_body = (msgs[-1].get("body") if msgs else "") or ""
        r["suggested_verb"] = _suggest_verb(r.get("ask_classification"), subject, last_body)
        if use_facts:
            sender = ""
            for msg in msgs:
                frm = msg.get("from") or msg.get("from_addr") or ""
                m = re.search(r"[\w.+-]+@[\w.-]+", frm)
                if m and not is_pete(m.group(0), pete_email):
                    sender = m.group(0).lower()
            r.update(facts_route(sender))
        out.append(r)
    return out


# ---------- Triage Engine P1: facts-first routing layer ----------
#
# FACTS ROUTE, CONTENT CLASSIFIES. The triage_routing_facts table supplies the
# ROUTING proposal (label / verb / project / priority) for a known sender; the
# ASK always comes from the content heuristics above and is never short-circuited
# by a fact -- the content-anomaly veto (triage-lint) depends on the ask being
# computed for every message regardless of sender-fact confidence.
# Ambiguity guard: no fact, or a matched fact with NULL routing columns
# ("facts incomplete" -- the EE silent-Nones lesson) => propose, never assert.

def facts_route(sender_addr: str) -> dict:
    base = {"sender": sender_addr or None, "fact_id": None, "fact_confidence": None,
            "routing_source": "uncovered", "facts_incomplete": False,
            "fact_label": None, "fact_mode": None, "fact_verb": None,
            "fact_project": None, "fact_priority": None}
    if not sender_addr:
        return base
    try:
        import importlib, os as _os, sys as _sys
        _sys.path.insert(0, _os.environ.get("VAULT", "/tmp/pbs"))
        tl = importlib.import_module("triage_lib")
        fact = tl.match_fact(sender_addr)
    except Exception:
        return base  # DB unreachable -- heuristics-only, honestly labelled uncovered
    if not fact:
        return base
    base.update({
        "fact_id": fact["id"], "fact_confidence": float(fact.get("confidence") or 0),
        "routing_source": "fact",
        "fact_label": fact.get("gmail_label"), "fact_mode": fact.get("filter_mode"),
        "fact_verb": fact.get("default_verb"), "fact_project": fact.get("default_project_slug"),
        "fact_priority": fact.get("default_priority"),
    })
    if not fact.get("gmail_label"):
        # matched row with NULL routing columns: LOUD, never a silent None
        base["facts_incomplete"] = True
        base["routing_source"] = "fact-incomplete"
    return base


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    pete_email = PETE_DEFAULT
    args = sys.argv[1:]
    if "--pete" in args:
        i = args.index("--pete")
        pete_email = args[i + 1]
        args = args[:i] + args[i + 2 :]
    inbox_path = args[0]
    with open(inbox_path) as f:
        inbox = json.load(f)
    drafts = classify_inbox(inbox, pete_email=pete_email)
    json.dump(drafts, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
