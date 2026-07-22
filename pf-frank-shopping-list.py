#!/usr/bin/env python3
"""pf-frank-shopping-list.py — Frank's P3 learning loop.

Reads the ANONYMISED frank_qa_log (question + answer + marker + hour-rounded timestamp; NO identity)
and clusters the `declined` + `weak` questions into the weekly drop shopping list: what Frank
couldn't answer well becomes the next PF material to ingest. Read-only over the portal DB.

    VAULT=/tmp/pbs python3 /tmp/pbs/pf-frank-shopping-list.py [--days 7]
"""
import os, sys, json, time, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.join(VAULT, "Library/processes/secrets")
DAYS = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 7

_pk = json.load(open(f"{SEC}/passion-fit-supabase-keys.json"))
since = time.strftime("%Y-%m-%dT%H:00:00", time.gmtime(time.time() - DAYS * 86400))
req = urllib.request.Request(
    f"{_pk['project_url']}/rest/v1/frank_qa_log"
    f"?select=question,marker,created_at&marker=in.(declined,weak)&created_at=gte.{since}"
    f"&order=created_at.desc",
    headers={"apikey": _pk["service_role_key"], "Authorization": f"Bearer {_pk['service_role_key']}"})
rows = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())

print(f"Frank shopping list — last {DAYS} days: {len(rows)} weak/declined exchanges\n")
if not rows:
    print("Nothing to shop for — Frank answered everything in scope. (Or he's not live yet.)")
    sys.exit(0)
for marker in ("declined", "weak"):
    hits = [r for r in rows if r["marker"] == marker]
    if hits:
        label = "OUT OF SCOPE (declined)" if marker == "declined" else "WEAK (thumbs-down)"
        print(f"## {label} — {len(hits)}")
        for r in hits[:40]:
            print(f"  · {r['question'][:140]}")
        print()
print("→ Cluster the WEAK ones into concepts that need richer material; the DECLINED ones show "
      "where athletes expect Frank to go next. Feed both into the weekly pf-ingest drop.")
