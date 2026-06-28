#!/usr/bin/env python3
"""cc-knowledge-ttl-cull.py — on-demand, SAFE cull of aged machine-history notes.

DELIBERATELY on-demand (dry-run by default), NOT an unattended cron: an auto-deleter running
forever on the knowledge base is a future-incident vector, and the real prevention is the
ingest-skip in cc-knowledge-ingest.py (these types can no longer be ingested). This is the
belt-and-braces broom you run by hand when residual machine history has aged out.

Only ever touches narrow, unambiguous machine-history types, older than TTL, that are NOT
load-bearing (no inbound links, not kernel-referenced). Always back up first
(cc-knowledge-backup.py). Dry-run by default; pass --apply to delete.

    VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-ttl-cull.py            # dry-run
    VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-ttl-cull.py --apply    # delete
"""
import json, urllib.request, re, sys, os

VAULT = os.environ.get("VAULT", "/tmp/pbs")
APPLY = "--apply" in sys.argv
TTL_DAYS = 180
HISTORY = ("email-extract", "drift-check", "run-log", "session-log", "session-report")
TOK = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()


def q(sql):
    req = urllib.request.Request("https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": "M"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=90).read().decode())


# Protect-set is EXPLICIT and DECOUPLED from the orientation map (map-md is now a generated
# artifact via cc-orientation-map-sync.py — its wording must NOT drive deletion). Sources:
#   (1) kernel refs scraped from claude-md ONLY (stable operating instructions);
#   (2) an explicit allowlist in config 'protected-slugs' (seeded from what map-md used to protect).
# Link-target notes are already excluded by the candidate query below.
ktext = q("SELECT value FROM config WHERE key='claude-md'")[0]["value"]
ref = set()
for m in re.findall(r"\[\[([^\]]+)\]\]", ktext): t = m.split("|")[0].split("#")[0].strip(); ref |= {t, t.split("/")[-1]}
for m in re.findall(r"`([^`]+)`", ktext): t = m.strip(); ref |= {t, t.split("/")[-1]}
_ps = q("SELECT value FROM config WHERE key='protected-slugs'")
if _ps and _ps[0].get("value"):
    for t in json.loads(_ps[0]["value"]): ref |= {t, t.split("/")[-1]}

types_sql = ",".join(f"'{t}'" for t in HISTORY)
cand = q(f"""SELECT n.id, n.slug, n.type, n.source_updated FROM vault_notes n
  WHERE n.type IN ({types_sql})
    AND n.source_updated < (now() - interval '{TTL_DAYS} days')
    AND NOT EXISTS (SELECT 1 FROM note_links l WHERE l.dst_id = n.id)""")
cull = [c for c in cand if not (c["slug"] and (c["slug"] in ref or c["slug"].split("/")[-1] in ref))]

from collections import Counter
print(f"cc-knowledge-ttl-cull (TTL {TTL_DAYS}d, types {HISTORY}) — {len(cull)} cullable")
for t, n in Counter(c["type"] for c in cull).most_common():
    print(f"   {n:4} {t}")
if cull and APPLY:
    ids = ",".join(f"'{c['id']}'" for c in cull)
    q(f"DELETE FROM vault_notes WHERE id IN ({ids})")
    summary = ", ".join(f"{n} {t}" for t, n in Counter(c['type'] for c in cull).most_common())
    q(f"INSERT INTO daily_log(date,cron_name,content) VALUES (current_date,'ttl-cull',$${len(cull)} aged history notes culled: {summary}$$)")
    print(f"  APPLIED — deleted {len(cull)}, logged to daily_log")
elif not APPLY:
    print("  (dry-run; pass --apply to delete)")
