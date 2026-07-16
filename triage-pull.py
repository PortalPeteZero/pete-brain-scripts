#!/usr/bin/env python3
"""triage-pull.py — one call returns the in-scope inbox WITH metadata.

Kills the 80+ sequential get-thread calls a triage session used to make: the
caller runs THIS once and gets every thread's {id, from, subject, date,
labels, snippet, age_days} as JSON, ready to classify.

  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py                       # in:inbox, 100
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py "label:Replies" 50    # any query + limit
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py "in:inbox" 100 --full # + per-message enrichment
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py --round               # the READ-IN-FULL round (Step 1)
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py --threads <id[,id2]>  # strays/new arrivals -> SAME extractor

--round / --threads (the read-in-full extractor, 15-16 Jul 2026): every message body in full
(text/plain, HTML fallback, quoted history stripped) PLUS any text/calendar (.ics) invite — parsed
into a `📅 MEETING INVITE` banner + a `meeting_invite` flag + When/Where (handles attachment-only
Outlook/Teams invites). `--round` pages the whole inbox; `--threads` runs the identical extraction for
specific ids so a stray/new arrival mid-triage is NEVER judged off a bare get-thread. Writes a round
file `/tmp/triage-round-<session>.json`; the summary lists `meeting_invites` loudly.

--full (Triage Engine P5 — the auto-path guardrail inputs the bare pull cannot feed):
per thread also returns the LATEST message's real From/Reply-To addresses, the To/Cc
recipient lists, the automated-origin headers (Auto-Submitted, Precedence, List-Id), the
triggering message_id (the ledger's unique key), the Authentication-Results header (the
SPF/DKIM/DMARC gate), body text, attachment flag, and whether Pete has a prior outbound
on the thread. Every auto path (the offline runner, L2/L3/L4) MUST use --full.

Loads the canonical gmail-api.py by path (the file is hyphenated).
"""
import sys, os, json, datetime, importlib.util, re, base64, html as _html, uuid

def _load_gmail():
    spec = importlib.util.spec_from_file_location(
        "gmail_api", os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmail-api.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.GmailAPI()

def _load_lib():
    return importlib.import_module("triage_lib") if os.path.dirname(os.path.abspath(__file__)) in sys.path \
        else (sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) or importlib.import_module("triage_lib"))

def _hdr(msg, name):
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""

# ---------- read-in-full extraction (the root-cause fix) ----------
MSG_CAP = 15000            # per-message extracted-text ceiling; over -> truncated flag
THREAD_MSG_CAP = 15        # threads longer than this carry the newest N + history_summarised

def _strip_html(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"[ \t]+", " ", s)

def _strip_quoted(s):
    # drop quoted history so the session reads the NEW content, not the whole chain
    out = []
    for ln in s.splitlines():
        if re.match(r"\s*>", ln):
            continue
        if re.match(r"(?i)^\s*(on .+ wrote:|-{2,}\s*original message|from:\s|_{5,}|sent from my)", ln):
            break
        out.append(ln)
    return "\n".join(out).strip()

def extract_message(msg):
    """Return (text, flags) for ONE message. flags in {body_absent, body_empty_after_strip,
    truncated, extraction_failed}. HTML-only mail falls back to stripped HTML -- never a
    silent empty body (the 4k/text-plain-only defect)."""
    flags = set()
    try:
        plain, html_parts, has_body_part = [], [], False
        def walk(part):
            nonlocal has_body_part
            mt = part.get("mimeType", "")
            data = part.get("body", {}).get("data")
            if mt.startswith("text/plain") and data:
                has_body_part = True
                plain.append(base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace"))
            elif mt.startswith("text/html") and data:
                has_body_part = True
                html_parts.append(base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace"))
            for p in part.get("parts", []) or []:
                walk(p)
        walk(msg.get("payload", {}))
        text = "\n".join(plain).strip()
        if not text and html_parts:
            text = _strip_html("\n".join(html_parts)).strip()
        text = _strip_quoted(text)
        if not has_body_part:
            flags.add("body_absent")
        elif not text:
            flags.add("body_empty_after_strip")
        if len(text) > MSG_CAP:
            text = text[:MSG_CAP]; flags.add("truncated")
        return text, flags
    except Exception:
        return "", {"extraction_failed"}

def _attachments(msg):
    out = []
    def walk(part):
        if part.get("filename"):
            out.append({"name": part["filename"], "mime": part.get("mimeType", ""),
                        "size": part.get("body", {}).get("size", 0)})
        for p in part.get("parts", []) or []:
            walk(p)
    walk(msg.get("payload", {}))
    return out

def _ics_unfold(raw):
    out = []
    for ln in raw.splitlines():
        if ln[:1] in (" ", "\t") and out:
            out[-1] += ln[1:]
        else:
            out.append(ln)
    return out

def _calendar_invite(msg, g):
    """If the message carries a text/calendar (.ics) part, parse the meeting details.
    Returns {summary, when, tzid, location} or None. Handles inline data AND an
    attachment-only .ics (the Outlook/Teams shape — data lives behind an attachmentId).
    This is what stops a meeting invite being judged off the text/plain snippet
    ('call data will be shared shortly') and filed as info-only (16 Jul 2026)."""
    cal = {"p": None}
    def walk(p):
        if cal["p"] is not None:
            return
        mt = p.get("mimeType", "")
        fn = (p.get("filename") or "").lower()
        if mt.startswith("text/calendar") or fn.endswith(".ics"):
            cal["p"] = p; return
        for sp in p.get("parts", []) or []:
            walk(sp)
    walk(msg.get("payload", {}))
    part = cal["p"]
    if not part:
        return None
    body = part.get("body", {})
    data = body.get("data")
    if not data and body.get("attachmentId"):
        try:
            att = g._call("GET", f"/messages/{msg['id']}/attachments/{body['attachmentId']}")
            data = att.get("data")
        except Exception:
            return {"summary": None, "when": None, "tzid": None, "location": None, "unparsed": True}
    if not data:
        return None
    try:
        raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
    except Exception:
        return {"unparsed": True}
    lines = _ics_unfold(raw)
    def _val(prefix):
        for l in lines:
            if l.startswith(prefix) and ":" in l:
                return l.split(":", 1)[1].strip()
        return None
    dtstart = tzid = None
    for l in lines:
        if l.startswith("DTSTART") and ":" in l:
            v = l.split(":", 1)[1].strip()
            if v[:2] == "20":  # a real event date, not the 16010101 VTIMEZONE-rule anchors
                dtstart = v
                mm = re.search(r"TZID=([^:;]+)", l); tzid = mm.group(1) if mm else None
                break
    when = None
    if dtstart:
        mm = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})", dtstart)
        if mm:
            when = f"{mm.group(1)}-{mm.group(2)}-{mm.group(3)} {mm.group(4)}:{mm.group(5)}"
    return {"summary": _val("SUMMARY"), "when": when, "tzid": tzid, "location": _val("LOCATION")}

def _process_thread(t, g, tl, team, today):
    """Full read-in-full extraction for ONE thread: every message body PLUS any
    text/calendar (.ics) invite surfaced as a loud MEETING INVITE banner + a
    `meeting_invite` flag. The single unit both build_round and build_threads use, so a
    stray/new arrival gets the IDENTICAL treatment as an in-round thread. Returns dict|None."""
    full = g.get_thread(t["id"])
    msgs = full.get("messages", [])
    if not msgs:
        return None
    kept = msgs[-THREAD_MSG_CAP:]
    history_summarised = len(msgs) > THREAD_MSG_CAP
    thread_flags = set()
    emsgs = []
    for m in kept:
        text, flags = extract_message(m)
        cal = _calendar_invite(m, g)
        if cal:
            flags.add("meeting_invite")
            text = ("📅 MEETING INVITE -- When: %s (tz: %s) -- Where: %s -- %s"
                    % (cal.get("when") or "?", cal.get("tzid") or "?",
                       cal.get("location") or "?", cal.get("summary") or "")).strip() + "\n\n" + text
        thread_flags |= flags
        emsgs.append({
            "from": _hdr(m, "From"), "to": _hdr(m, "To"), "cc": _hdr(m, "Cc"),
            "reply_to": _hdr(m, "Reply-To"), "date": _hdr(m, "Date"),
            "subject": _hdr(m, "Subject"), "body": text,
            "attachments": _attachments(m), "flags": sorted(flags), "calendar": cal,
        })
    last = kept[-1]
    facts = tl.compute_thread_facts(emsgs, team)
    ts = int(last.get("internalDate", 0)) / 1000
    d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date() if ts else None
    return {
        "id": t["id"],
        "newest_message_id": last.get("id"),
        "from": _hdr(last, "From").split("<")[0].strip().strip('"'),
        "subject": _hdr(last, "Subject"),
        "date": d.isoformat() if d else None,
        "age_days": (today - d).days if d else None,
        "labels": last.get("labelIds", []),
        "snippet": (last.get("snippet", "") or "")[:160],
        "msgs": len(msgs), "kept_msgs": len(kept),
        "facts": facts,
        "flags": sorted(thread_flags),
        "history_summarised": history_summarised,
        "messages": emsgs,
    }

def _write_round(threads_list, query, exhausted):
    session_id = str(uuid.uuid4())
    round_obj = {
        "session_id": session_id,
        "query": query,
        "pulled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pagination_exhausted": exhausted,
        "thread_count": len(threads_list),
        "threads": threads_list,
    }
    path = f"/tmp/triage-round-{session_id}.json"
    with open(path, "w") as f:
        json.dump(round_obj, f, ensure_ascii=False, indent=2)
    round_obj["round_file"] = path
    return round_obj

def build_round(query="in:inbox"):
    """The read-in-full round: page EVERY in-scope thread, extract EVERY message + any
    calendar invite, compute team/pete facts, mint a session_id, write the round file."""
    g = _load_gmail()
    tl = _load_lib()
    team = tl.team_emails()
    threads, exhausted = g.search_threads_all(query)
    today = datetime.date.today()
    out_threads = [pt for t in threads if (pt := _process_thread(t, g, tl, team, today))]
    return _write_round(out_threads, query, exhausted)

def build_threads(ids):
    """Same read-in-full extraction (incl. .ics invites) for SPECIFIC thread ids -- the
    path for strays / new arrivals that appear mid-triage, so they are NEVER judged off a
    bare get-thread. Returns a round dict."""
    g = _load_gmail()
    tl = _load_lib()
    team = tl.team_emails()
    today = datetime.date.today()
    out = [pt for tid in ids if (pt := _process_thread({"id": tid}, g, tl, team, today))]
    return _write_round(out, "threads:" + ",".join(ids), True)

def _round_summary(r):
    return {
        "session_id": r["session_id"], "round_file": r["round_file"],
        "thread_count": r["thread_count"], "pagination_exhausted": r.get("pagination_exhausted"),
        "extraction_failed": [t["id"] for t in r["threads"] if "extraction_failed" in t["flags"]],
        "needs_team_note": sum(1 for t in r["threads"] if t["facts"]["team_replied_since"]),
        "meeting_invites": [{"id": t["id"], "subject": t["subject"]}
                            for t in r["threads"] if "meeting_invite" in t["flags"]],
    }

def main():
    if "--thread" in sys.argv or "--threads" in sys.argv:
        # strays / new arrivals mid-triage go through the SAME extractor (never bare get-thread)
        ids = []
        for i, a in enumerate(sys.argv):
            if a in ("--thread", "--threads") and i + 1 < len(sys.argv):
                ids += [x for x in sys.argv[i + 1].split(",") if x]
        print(json.dumps(_round_summary(build_threads(ids)), indent=2))
        return
    if "--round" in sys.argv:
        q = next((a for a in sys.argv[1:] if not a.startswith("--")), "in:inbox")
        print(json.dumps(_round_summary(build_round(q)), indent=2))
        return
    args = [a for a in sys.argv[1:] if a != "--full"]
    full_mode = "--full" in sys.argv
    query = args[0] if len(args) > 0 else "in:inbox"
    limit = int(args[1]) if len(args) > 1 else 100
    g = _load_gmail()
    today = datetime.date.today()
    rows = []
    for t in g.search_threads(query, max_results=limit):
        full = g.get_thread(t["id"])
        msgs = full.get("messages", [])
        if not msgs:
            continue
        last = msgs[-1]
        ts = int(last.get("internalDate", 0)) / 1000
        d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date() if ts else None
        frm = _hdr(last, "From").split("<")[0].strip().strip('"')
        row = {
            "id": t["id"],
            "from": frm,
            "subject": _hdr(last, "Subject"),
            "date": d.isoformat() if d else None,
            "age_days": (today - d).days if d else None,
            "labels": last.get("labelIds", []),
            "snippet": (last.get("snippet", "") or "")[:160],
            "msgs": len(msgs),
        }
        if full_mode:
            import re as _re, base64 as _b64
            raw_from = _hdr(last, "From")
            m = _re.search(r"[\w.+-]+@[\w.-]+", raw_from)
            pete = "pete.ashcroft@sygma-solutions.com"
            def _addrs(v):
                return [a.lower() for a in _re.findall(r"[\w.+-]+@[\w.-]+", v or "")]
            def _body_text(msg):
                def walk(part):
                    if part.get("mimeType", "").startswith("text/plain"):
                        data = part.get("body", {}).get("data")
                        if data:
                            return _b64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
                    for p in part.get("parts", []) or []:
                        t = walk(p)
                        if t:
                            return t
                    return ""
                return walk(msg.get("payload", {}))[:4000]
            def _has_attachment(msg):
                def walk(part):
                    if part.get("filename"):
                        return True
                    return any(walk(p) for p in part.get("parts", []) or [])
                return walk(msg.get("payload", {}))
            row.update({
                "sender_addr": (m.group(0).lower() if m else None),
                "reply_to": _hdr(last, "Reply-To"),
                "to": _addrs(_hdr(last, "To")),
                "cc": _addrs(_hdr(last, "Cc")),
                "message_id": _hdr(last, "Message-ID") or last.get("id"),
                "auto_submitted": _hdr(last, "Auto-Submitted"),
                "precedence": _hdr(last, "Precedence"),
                "list_id": _hdr(last, "List-Id"),
                "authentication_results": _hdr(last, "Authentication-Results"),
                "body_text": _body_text(last),
                "has_attachment": _has_attachment(last),
                "prior_pete_outbound": any(
                    pete in _hdr(msg2, "From").lower() for msg2 in msgs[:-1]),
            })
        rows.append(row)
    print(json.dumps(rows, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
