#!/usr/bin/env python3
"""triage-pull.py — one call returns the in-scope inbox WITH metadata.

Kills the 80+ sequential get-thread calls a triage session used to make: the
caller runs THIS once and gets every thread's {id, from, subject, date,
labels, snippet, age_days} as JSON, ready to classify.

  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py                       # in:inbox, 100
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py "label:Replies" 50    # any query + limit
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py "in:inbox" 100 --full # + per-message enrichment

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

def build_round(query="in:inbox"):
    """The read-in-full round: page EVERY in-scope thread, extract EVERY message,
    compute team/pete facts, mint a session_id, write the round file. Returns the round dict."""
    g = _load_gmail()
    tl = _load_lib()
    team = tl.team_emails()
    threads, exhausted = g.search_threads_all(query)
    today = datetime.date.today()
    out_threads = []
    for t in threads:
        full = g.get_thread(t["id"])
        msgs = full.get("messages", [])
        if not msgs:
            continue
        kept = msgs[-THREAD_MSG_CAP:]
        history_summarised = len(msgs) > THREAD_MSG_CAP
        thread_flags = set()
        emsgs = []
        for m in kept:
            text, flags = extract_message(m)
            thread_flags |= flags
            emsgs.append({
                "from": _hdr(m, "From"), "to": _hdr(m, "To"), "cc": _hdr(m, "Cc"),
                "reply_to": _hdr(m, "Reply-To"), "date": _hdr(m, "Date"),
                "subject": _hdr(m, "Subject"), "body": text,
                "attachments": _attachments(m), "flags": sorted(flags),
            })
        last = kept[-1]
        facts = tl.compute_thread_facts(emsgs, team)
        ts = int(last.get("internalDate", 0)) / 1000
        d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date() if ts else None
        out_threads.append({
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
        })
    session_id = str(uuid.uuid4())
    round_obj = {
        "session_id": session_id,
        "query": query,
        "pulled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pagination_exhausted": exhausted,
        "thread_count": len(out_threads),
        "threads": out_threads,
    }
    path = f"/tmp/triage-round-{session_id}.json"
    with open(path, "w") as f:
        json.dump(round_obj, f, ensure_ascii=False, indent=2)
    round_obj["round_file"] = path
    return round_obj

def main():
    if "--round" in sys.argv:
        q = next((a for a in sys.argv[1:] if not a.startswith("--")), "in:inbox")
        r = build_round(q)
        # summary to stdout; full data in the round file
        blocked = [t["id"] for t in r["threads"] if "extraction_failed" in t["flags"]]
        print(json.dumps({
            "session_id": r["session_id"], "round_file": r["round_file"],
            "thread_count": r["thread_count"], "pagination_exhausted": r["pagination_exhausted"],
            "extraction_failed": blocked,
            "needs_team_note": sum(1 for t in r["threads"] if t["facts"]["team_replied_since"]),
        }, indent=2))
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
