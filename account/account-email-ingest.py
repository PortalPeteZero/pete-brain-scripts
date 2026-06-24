#!/usr/bin/env python3
"""account-email-ingest — pull new Clancy mail into the account_* store.

Two nets (the 16 Jun sweep proved the label alone misses internal threads):
  A) label:Customers/SY-Clancy
  B) internal 'clancy from:sygma-solutions.com' minus briefing/Asana/calendar noise
Deterministic, conservative — never guesses a deliverable or action:
  - new Clancy / alliance email addresses  -> account_people (provenance noted)
  - substantive attachments (pdf/doc/xls/ppt) -> account_documents (Gmail link)
  - threads where Clancy sent the last message -> reply-owed flag (account_state)
Cursor in account_state.email_cursor; idempotent (skips contacts/docs already held).

Cron: daily 07:10 (com.peterashcroft.account-email-ingest).
Usage: account-email-ingest.py [--days N] [--dry-run]
"""
# CRON-META
# what: Pull new Clancy mail into the Command Centre account_* store (contacts, docs, reply-owed)
# why: Sweeps Clancy mail into the account store so the Clancy cockpit stays current without manual filing
# reads: Gmail (Clancy label + internal Sygma net)
# writes: CC account_people / account_documents / account_state
# entity: customers
# report:
# schedule: 10 7 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import os
import sys
import re
import datetime
import importlib.util

# Co-located helpers: account_store.py is a sibling; gmail-api.py is one level up on the Mac
# (scripts/) but flat alongside on Railway (/app). Resolve from __file__, never a $VAULT path.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import account_store as store

_gmail = os.path.join(_HERE, "gmail-api.py")
if not os.path.exists(_gmail):
    _gmail = os.path.join(os.path.dirname(_HERE), "gmail-api.py")
_spec = importlib.util.spec_from_file_location("gmail_api", _gmail)
gmail_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gmail_api)

C = "clancy"
DRY = "--dry-run" in sys.argv
CLANCY_DOMAINS = ("theclancygroup.co.uk", "anglianwater.co.uk")
DOC_EXT = {"pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt"}
ADDR_RE = re.compile(r'(?:"?([^"<>@]+?)"?\s*)?<?([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})>?')


def parse_addrs(val):
    out = []
    for m in ADDR_RE.finditer(val or ""):
        name = (m.group(1) or "").strip().strip('"').strip()
        out.append((name, m.group(2).lower()))
    return out


def headers_of(msg):
    return {h["name"].lower(): h["value"] for h in (msg.get("payload", {}).get("headers") or [])}


def walk_parts(part, out):
    if part.get("filename") and (part.get("body", {}) or {}).get("attachmentId"):
        out.append(part["filename"])
    for p in part.get("parts", []) or []:
        walk_parts(p, out)


def main():
    days = None
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
    if days is None:
        last = store.get_state(C, "email_cursor")
        if last:
            try:
                days = max(1, (datetime.date.today() - datetime.date.fromisoformat(last[:10])).days + 1)
            except Exception:
                days = 30
        else:
            days = 30

    g = gmail_api.GmailAPI()
    netA = f"label:Customers/SY-Clancy newer_than:{days}d"
    netB = ('clancy from:sygma-solutions.com newer_than:%dd '
            '-subject:briefing -subject:"week ahead" -from:asana -from:calendar-notification') % days
    threadsA = g.search_threads(netA, max_results=40)
    threadsB = g.search_threads(netB, max_results=25)

    existing_emails = {(p.get("email") or "").lower() for p in
                       store.select("account_people", f"customer=eq.{C}&select=email") if p.get("email")}
    existing_docs = {(d.get("url") or "") for d in
                     store.select("account_documents", f"customer=eq.{C}&select=url") if d.get("url")}

    new_contacts, new_docs, reply_owed, seen = {}, [], [], set()
    for tlist, is_label in [(threadsA, True), (threadsB, False)]:
        for t in tlist:
            tid = t["id"]
            if tid in seen:
                continue
            seen.add(tid)
            td = g.get_thread(tid, fmt="full")
            msgs = td.get("messages", [])
            link = f"https://mail.google.com/mail/u/0/#all/{tid}"
            for m in msgs:
                h = headers_of(m)
                for field in ("from", "to", "cc"):
                    for name, addr in parse_addrs(h.get(field)):
                        dom = addr.split("@")[-1]
                        if any(dom == d or dom.endswith("." + d) for d in CLANCY_DOMAINS) \
                                and addr not in existing_emails and addr not in new_contacts:
                            new_contacts[addr] = {"customer": C,
                                                  "name": name or addr.split("@")[0].replace(".", " ").title(),
                                                  "side": ("alliance" if "anglian" in dom else "clancy"),
                                                  "email": addr, "notes": "auto-added by email-ingest"}
                files = []
                walk_parts(m.get("payload", {}), files)
                for fn in files:
                    ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                    if ext in DOC_EXT and link not in existing_docs:
                        new_docs.append({"customer": C, "title": fn, "type": ext.upper(),
                                         "url": link, "status": "from email"})
                        existing_docs.add(link)
            if is_label and msgs:
                lfrom = " ".join(a for _, a in parse_addrs(headers_of(msgs[-1]).get("from")))
                if any(d in lfrom for d in CLANCY_DOMAINS):
                    reply_owed.append(headers_of(msgs[-1]).get("subject", "(no subject)")[:70])

    ins_c = ins_d = 0
    if not DRY:
        if new_contacts:
            store.insert("account_people", list(new_contacts.values()))
            ins_c = len(new_contacts)
        uniq, seent = [], set()
        for d in new_docs:
            k = (d["title"], d["url"])
            if k not in seent:
                seent.add(k)
                uniq.append(d)
        if uniq:
            store.insert("account_documents", uniq)
            ins_d = len(uniq)
        store.set_state(C, "email_cursor", datetime.date.today().isoformat())
        store.set_state(C, "last_email_ingest", datetime.datetime.now(datetime.timezone.utc).isoformat())
        store.set_state(C, "reply_owed", f"{len(reply_owed)}" + (": " + " | ".join(reply_owed[:5]) if reply_owed else ""))
    print(f"account-email-ingest {'(DRY) ' if DRY else ''}{C}: threads A={len(threadsA)} B={len(threadsB)} "
          f"| new contacts {len(new_contacts)} | new docs {ins_d if not DRY else len(new_docs)} | reply-owed {len(reply_owed)}")
    if not DRY:
        store.daily_note_line(f"account-email-ingest: +{ins_c} contacts, +{ins_d} docs, {len(reply_owed)} reply-owed (Clancy mail sweep)")
        store.refresh_state_of_play(C)


if __name__ == "__main__":
    main()
