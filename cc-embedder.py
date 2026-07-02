#!/usr/bin/env python3
"""cc-embedder.py — the ONE embedder for the Command Centre semantic layer.

Owns `embedding` + `embedded_hash` for the three embedding tables (vault_notes, tasks, notes).
A row is DIRTY when `embedding IS NULL OR embedded_hash IS DISTINCT FROM md5(embed_input(...))`.
The embedded text is defined ONCE, in SQL, by `public.embed_input(a,b)` — this script SELECTs that
exact text (never rebuilds normalization in Python), embeds it, and writes `embedding` + `embedded_hash`
together with an OPTIMISTIC GUARD (`... WHERE md5(embed_input(row)) = <hash_at_read>`) so a row edited
mid-flight simply loses the write and is re-done on the next pass.

voyage-3.5-lite silently truncates at ~32k tokens (verified 2026-07-02), and embed_input windows to
100k chars (safely under that), so the stored hash always reflects exactly what was embedded — no
truncate-and-retry needed. Consolidates the retired cc-knowledge-embed-backfill.py / cc-tasks-embed.py /
cc-knowledge-voyage-setup.py (kept as thin shims). Usage: `cc-embedder.py [table ...]` (default: all).
"""
import json, urllib.request, urllib.error, time, os, sys

VAULT = os.environ.get("VAULT", os.path.dirname(os.path.abspath(__file__)))
SEC = f"{VAULT}/Library/processes/secrets"; REF = "zhexcaflgahdcbzvbyfq"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json")); URL = k["url"]; SR = k["service_role_key"]
tok = (os.environ.get("SUPABASE_TOKEN") or open(f"{SEC}/supabase-token").read()).strip()
VKEY = (os.environ.get("VOYAGE_API_KEY") or open(f"{SEC}/voyage-api-key").read()).strip()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

# table -> (colA, colB). embed_input(colA,colB) is the SQL single-source-of-truth for the embedded text.
TABLES = {"vault_notes": ("title", "body"), "tasks": ("name", "notes"), "notes": ("title", "body")}
MODEL = "voyage-3.5-lite"; DIM = 1024
REQ_CHAR_BUDGET = 180000   # cumulative chars per Voyage request (~45k tokens — safe margin)
EMBED_BATCH = 64           # max inputs per Voyage request
WRITE_CHUNK = 25           # rows per UPDATE (keeps the Management-API SQL statement small)

def mgmt_sql(q):
    req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "User-Agent": UA}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=180).read() or "[]")

def _voyage_once(texts):
    body = {"input": texts, "model": MODEL, "input_type": "document", "output_dimension": DIM}
    req = urllib.request.Request("https://api.voyageai.com/v1/embeddings",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {VKEY}", "Content-Type": "application/json"}, method="POST")
    return [d["embedding"] for d in json.loads(urllib.request.urlopen(req, timeout=300).read())["data"]]

def voyage(texts):
    try:
        return _voyage_once(texts)
    except urllib.error.HTTPError as e:
        if e.code in (429, 500, 502, 503, 504, 546) and len(texts) > 1:
            m = len(texts) // 2
            return voyage(texts[:m]) + voyage(texts[m:])
        if e.code in (429, 500, 502, 503, 504, 546):
            time.sleep(2); return _voyage_once(texts)
        raise

def dirty_rows(table, a, b, limit=500):
    ei = f"embed_input({a},{b})"
    q = (f"SELECT id::text AS id, {ei} AS t, md5({ei}) AS h FROM public.{table} "
         f"WHERE length({ei}) > 0 AND (embedding IS NULL OR embedded_hash IS DISTINCT FROM md5({ei})) "
         f"LIMIT {limit}")
    return mgmt_sql(q)

def _veclit(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

def write_rows(table, a, b, rows, vecs):
    """Write embedding+hash together, guarded so a row changed since read is skipped (re-done next pass).
    Returns the number of rows actually updated."""
    updated = 0
    for i in range(0, len(rows), WRITE_CHUNK):
        chunk_r = rows[i:i + WRITE_CHUNK]; chunk_v = vecs[i:i + WRITE_CHUNK]
        vals = ",".join(f"('{r['id']}'::uuid, '{_veclit(v)}'::vector, '{r['h']}')" for r, v in zip(chunk_r, chunk_v))
        q = (f"UPDATE public.{table} t SET embedding = d.e, embedded_hash = d.h "
             f"FROM (VALUES {vals}) d(id, e, h) "
             f"WHERE t.id = d.id AND md5(embed_input(t.{a}, t.{b})) = d.h "
             f"RETURNING t.id")
        updated += len(mgmt_sql(q) or [])
    return updated

def _embed_batches(rows):
    batch = []; chars = 0
    for r in rows:
        L = len(r["t"])
        if batch and (chars + L > REQ_CHAR_BUDGET or len(batch) >= EMBED_BATCH):
            yield batch; batch = []; chars = 0
        batch.append(r); chars += L
    if batch:
        yield batch

def run_table(table):
    a, b = TABLES[table]; total = 0
    while True:
        rows = dirty_rows(table, a, b, 500)
        if not rows:
            break
        wrote = 0
        for batch in _embed_batches(rows):
            try:
                vecs = voyage([r["t"] for r in batch])
            except Exception as e:
                print(f"  {table}: embed fail {str(e)[:140]}", flush=True); continue
            wrote += write_rows(table, a, b, batch, vecs)
        total += wrote
        print(f"  {table}: +{wrote} (total {total})", flush=True)
        if wrote == 0:   # nothing landed this pass (all lost the optimistic race, or embed failed) — stop
            break
    return total

def main():
    which = [t for t in sys.argv[1:]] or list(TABLES)
    grand = 0
    for t in which:
        if t not in TABLES:
            print(f"skip unknown table {t}", flush=True); continue
        grand += run_table(t)
    print(f"DONE: {grand} embeddings written")

if __name__ == "__main__":
    main()
