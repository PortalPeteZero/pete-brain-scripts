#!/usr/bin/env python3
"""triage-pull.py — one call returns the in-scope inbox WITH metadata.

Kills the 80+ sequential get-thread calls a triage session used to make: the
caller runs THIS once and gets every thread's {id, from, subject, date,
labels, snippet, age_days} as JSON, ready to classify.

  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py                       # in:inbox, 100
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-pull.py "label:Actions" 50    # any query + limit

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
    query = sys.argv[1] if len(sys.argv) > 1 else "in:inbox"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100
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
        rows.append({
            "id": t["id"],
            "from": frm,
            "subject": _hdr(last, "Subject"),
            "date": d.isoformat() if d else None,
            "age_days": (today - d).days if d else None,
            "labels": last.get("labelIds", []),
            "snippet": (last.get("snippet", "") or "")[:160],
            "msgs": len(msgs),
        })
    print(json.dumps(rows, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
