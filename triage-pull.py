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
import sys, os, json, datetime, importlib.util

def _load_gmail():
    spec = importlib.util.spec_from_file_location(
        "gmail_api", os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmail-api.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.GmailAPI()

def _hdr(msg, name):
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""

def main():
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
