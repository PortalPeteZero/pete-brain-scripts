#!/usr/bin/env python3
"""cc-knowledge-voyage-setup.py — embed vault_notes via Voyage AI (free-tier paced, resumable).

The embedding column is already vector(1024) + match_notes(vector(1024)) exists (first run did
the re-spec). This embeds rows where embedding is null, writing per-batch so a kill/resume just
picks up the remaining nulls. Paced for Voyage's free tier (~3 RPM / 10K TPM): batch 16, text
truncated to ~1200 chars, ~30s between requests. Builds the hnsw index when the corpus is full.
Re-run any time (e.g. after new notes) — it only touches null-embedding rows.
"""
import json, urllib.request, urllib.error, time
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"; REF = "zhexcaflgahdcbzvbyfq"
VKEY = open(f"{SEC}/voyage-api-key").read().strip()
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json")); URL = k["url"]; SR = k["service_role_key"]
tok = open(f"{SEC}/supabase-token").read().strip()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
MODEL = "voyage-3.5-lite"; DIM = 1024; BATCH = 40; TRUNC = 8000; PACE = 0
HR = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}

def sql(q):
    r = urllib.request.Request(f"https://api.supabase.com/v1/projects/{REF}/database/query", data=json.dumps({"query": q}).encode(), headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "User-Agent": UA}, method="POST")
    return urllib.request.urlopen(r, timeout=120).read()

def voyage(texts):
    for a in range(8):
        try:
            r = urllib.request.Request("https://api.voyageai.com/v1/embeddings",
                data=json.dumps({"input": texts, "model": MODEL, "input_type": "document", "output_dimension": DIM}).encode(),
                headers={"Authorization": f"Bearer {VKEY}", "Content-Type": "application/json"}, method="POST")
            return [row["embedding"] for row in json.loads(urllib.request.urlopen(r, timeout=120).read())["data"]]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < 7: time.sleep(30 * (a + 1)); continue
            raise

def get_null(limit=200):
    r = urllib.request.Request(f"{URL}/rest/v1/vault_notes?embedding=is.null&select=id,title,body&limit={limit}", headers=HR)
    return json.loads(urllib.request.urlopen(r).read())

def chunks(a, n):
    for i in range(0, len(a), n): yield a[i:i + n]

total = 0
while True:
    notes = get_null(200)
    if not notes: break
    for batch in chunks(notes, BATCH):
        texts = [(((n.get("title") or "") + "\n" + (n.get("body") or ""))[:TRUNC]) for n in batch]
        vecs = voyage(texts)
        vals = ",".join("('" + n["id"] + "'::uuid,'[" + ",".join(f"{x:.6f}" for x in v) + "]')" for n, v in zip(batch, vecs))
        sql(f"update public.vault_notes v set embedding=d.e::vector from (values {vals}) d(id,e) where v.id=d.id;")
        total += len(batch); print(f"   ...{total} embedded", flush=True)
        time.sleep(PACE)
print("building hnsw index…", flush=True)
sql("create index if not exists vault_notes_emb on public.vault_notes using hnsw (embedding vector_cosine_ops);")
print(f"DONE: {total} notes embedded with {MODEL}. Semantic search ready.", flush=True)