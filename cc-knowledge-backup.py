#!/usr/bin/env python3
"""cc-knowledge-backup.py — cold backup of the CC knowledge base before any destructive op.

Paginated export of ALL of vault_notes (every column incl the pgvector `embedding`) + the full
note_links table → one JSON on Drive (My Drive/Command Centre/_backups/). Then re-reads the file
and checks row counts == live + embeddings present (round-trip proof). Exit 1 if it can't verify.

Use this FIRST, every time, before culling/deleting notes. There is no pg_dump/psql here — this
paginated REST export is the supported backup path.

    VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-backup.py [YYYY-MM-DD]
"""
import json, urllib.request, os, sys, time

VAULT = os.environ.get("VAULT", "/tmp/pbs")
TOK = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
REF = "zhexcaflgahdcbzvbyfq"
URL = f"https://api.supabase.com/v1/projects/{REF}/database/query"
HDR = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json",
       "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
STAMP = sys.argv[1] if len(sys.argv) > 1 else "manual"
COLS = ("id, slug, vault_path, type, entity, title, body, frontmatter, tags, links, word_count, "
        "source_updated, created_at, updated_at, embedding::text AS embedding, entity_slug, "
        "client_slug, drive_file_id, section")


def q(sql, tries=4):
    for a in range(tries):
        try:
            req = urllib.request.Request(URL, data=json.dumps({"query": sql}).encode(), headers=HDR, method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
        except Exception:
            if a == tries - 1: raise
            time.sleep(2 * (a + 1))


def export():
    notes, last = [], ""
    while True:
        page = q(f"SELECT {COLS} FROM vault_notes WHERE id::text > '{last}' ORDER BY id::text LIMIT 200")
        if not page: break
        notes.extend(page); last = page[-1]["id"]
    links = q("SELECT src_id, dst_target, dst_id FROM note_links ORDER BY src_id, dst_target")
    return notes, links


def main():
    notes, links = export()
    live_n = q("SELECT count(*) c FROM vault_notes")[0]["c"]
    live_l = q("SELECT count(*) c FROM note_links")[0]["c"]
    out = {"_manifest": {"created": STAMP, "vault_notes": len(notes), "note_links": len(links),
                          "embeddings_present": sum(1 for n in notes if n.get("embedding")),
                          "note": "embedding as pgvector text; fts omitted (regenerable from body)."},
           "vault_notes": notes, "note_links": links}
    dest_dir = os.path.expanduser("~/Library/CloudStorage/GoogleDrive-pete.ashcroft@sygma-solutions.com/My Drive/Command Centre/_backups")
    os.makedirs(dest_dir, exist_ok=True)
    dest = f"{dest_dir}/cc-knowledge-backup-{STAMP}.json"
    json.dump(out, open(dest, "w"), ensure_ascii=False)
    # round-trip verify: counts must match (hard); every embedding that EXISTS must be well-formed.
    # (notes just-ingested and awaiting embed-backfill are allowed — reported, not failed.)
    b = json.load(open(dest))
    present = [n for n in b["vault_notes"] if n.get("embedding")]
    emb_ok = all(isinstance(n["embedding"], str) and n["embedding"].startswith("[") for n in present)
    missing = len(b["vault_notes"]) - len(present)
    ok = (len(b["vault_notes"]) == live_n and len(b["note_links"]) == live_l and emb_ok)
    print(f"backup → {dest}  ({os.path.getsize(dest)/1_048_576:.1f} MB)")
    print(f"  notes {len(b['vault_notes'])}/{live_n}  links {len(b['note_links'])}/{live_l}  "
          f"embeddings present {len(present)} (awaiting backfill: {missing})  -> {'VERIFIED ✓' if ok else 'VERIFY FAILED ✗'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
