#!/usr/bin/env python3
"""
finance-ledger-publish.py — publish a per-entity finance ledger to its Command Centre surface.

Reads the CC `public.finance_ledger` table (the record the `finance this` verb INSERTs into —
converted from the old finance-ledger.md Drive file 2026-07-03, per Pete: "yes, make it a table")
and publishes the three load-bearing sections (deadlines / latest decision / recent filings) to
`reports.snapshots` under each ledger's `cc_report_key`. The entity's CC dashboard (e.g. Ashcroft
Finance, /m/ashcroft-finance "Latest from the ledger" panel) reflects a new entry with no code
deploy. Re-publishing overwrites the day's snapshot.

Adding an entry (the `finance this` verb, see skills/finance-filing):
  INSERT INTO finance_ledger (entity, cc_report_key, kind, entry, entry_date)
  VALUES ('personal', 'ashcroft-finance', 'deadline'|'decision'|'filing',
          'YYYY-MM-DD — what happened — [[wikilink]]', 'YYYY-MM-DD');
then run this script. Retire an old line by setting archived_at (never delete — it's a ledger).

Usage:  VAULT=/tmp/pbs python3 finance-ledger-publish.py [cc_report_key]
        (default: publish every distinct active cc_report_key in the table)
"""
import json, os, re, sys, datetime, urllib.request, urllib.parse
from importlib.machinery import SourceFileLoader

VAULT = os.environ.get("VAULT", "/tmp/pbs")
_pub_path = os.path.join(VAULT, "cc_publish.py")
if not os.path.exists(_pub_path):
    _pub_path = os.path.join(VAULT, "Library/processes/scripts/cc_publish.py")
cc_publish = SourceFileLoader("cc_publish", _pub_path).load_module()

KINDS = {"deadline": "deadlines", "decision": "decision", "filing": "filings"}


def _cc_rest(path):
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        d = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))
        url, key = d["url"], d["service_role_key"]
    req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/{path}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def clean(s):
    s = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", s)        # [[target|label]] -> label
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)                 # [[target]] -> target
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)                  # **bold** -> bold
    return s.strip()


def publish_key(key, rows):
    sections = {"deadlines": [], "decision": [], "filings": []}
    latest = None   # when the ledger last CHANGED (created_at), not a future deadline date
    for r in rows:
        sections[KINDS[r["kind"]]].append(clean(r["entry"]))
        c = (r.get("created_at") or "")[:10]
        if c and (latest is None or c > latest):
            latest = c
    payload = {"title": "Latest from the ledger",
               "updated": latest or datetime.date.today().isoformat(), **sections}
    ok = cc_publish.publish(key, datetime.date.today().isoformat(), payload)
    print(f"finance-ledger-publish: {'published' if ok else 'FAILED'} {key} → reports.snapshots "
          f"({len(sections['deadlines'])} deadlines · {len(sections['decision'])} decision · {len(sections['filings'])} filings)")
    return ok


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    q = "finance_ledger?select=cc_report_key,kind,entry,entry_date,created_at&archived_at=is.null&order=entry_date.desc.nullslast"
    if only:
        q += f"&cc_report_key=eq.{urllib.parse.quote(only)}"
    rows = _cc_rest(q)
    if not rows:
        print("finance-ledger-publish: no active ledger rows — nothing to publish", file=sys.stderr)
        return 2
    by_key = {}
    for r in rows:
        by_key.setdefault(r["cc_report_key"], []).append(r)
    ok = all(publish_key(k, v) for k, v in by_key.items())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
