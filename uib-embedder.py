#!/usr/bin/env python3
"""uib-embedder.py — give Agent Hertz eyes on published content.

Every block Hertz answers from is retrieved by vector similarity, so a published block with a
null `embedding` is live on the site and invisible to the assistant. `uib-content-check.py`
fails on exactly that, and it is a real defect rather than a tidiness one: a reader can see a
video the assistant will swear does not exist.

There was no embedder anywhere. Not in the Bureau repo, not in pete-brain-scripts, not in
`public.crons` — the CC has half a dozen (`cc-embedder` and friends) but those write
`vault_notes` in a different database entirely. Content was being published faster than anything
was indexing it. Written 6 Aug 2026 after the gate went red on 69 blocks from seven videos
published that morning.

Model must match what Hertz asks with: voyage-3.5-lite. A corpus embedded with one model and
queried with another retrieves noise, and nothing would look broken.

    VAULT=/tmp/pbs python3 /tmp/pbs/uib-embedder.py            # dry run, shows the backlog
    VAULT=/tmp/pbs python3 /tmp/pbs/uib-embedder.py --apply    # embed everything outstanding
"""
import argparse, hashlib, json, os, subprocess, sys, time, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REF = "xekedjpotwhhstpwganq"
MODEL = "voyage-3.5-lite"
BATCH = 32


def q(sql):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"},
        method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=180).read())


def voyage_key():
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py",
                        "SELECT value FROM secrets WHERE name='voyage-api-key'"],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    v = json.loads(r.stdout)[0]["value"]
    return json.loads(v)["api_key"] if v.strip().startswith("{") else v.strip()


def embed(key, texts):
    """input_type=document, because these are the things being searched, not the search."""
    req = urllib.request.Request(
        "https://api.voyageai.com/v1/embeddings",
        data=json.dumps({"model": MODEL, "input": texts, "input_type": "document"}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    for attempt in range(4):
        try:
            return [d["embedding"] for d in json.loads(urllib.request.urlopen(req, timeout=120).read())["data"]]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"voyage {e.code}: {e.read().decode()[:200]}")
    raise RuntimeError("voyage: retries exhausted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    rows = q("""select b.id, b.text
                from resource_block b join resource r on r.id = b.resource_id
                where b.embedding is null
                  and r.status in ('published','review')
                  and coalesce(btrim(b.text),'') <> ''
                order by b.id""")
    print(f"uib-embedder: {len(rows)} block(s) outstanding")
    if not rows:
        return 0
    if not a.apply:
        print("  dry run. Re-run with --apply to embed them.")
        return 0

    key = voyage_key()
    done = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        vecs = embed(key, [c["text"] for c in chunk])
        # One statement per batch, so a mid-run failure leaves the rest untouched rather
        # than half-written.
        sets = []
        for c, v in zip(chunk, vecs):
            h = hashlib.sha256(c["text"].encode()).hexdigest()
            sets.append(f"('{c['id']}'::uuid, '{json.dumps(v)}'::vector, '{h}')")
        q("update resource_block b set embedding = v.emb, embedded_hash = v.h "
          "from (values " + ",".join(sets) + ") as v(id, emb, h) where b.id = v.id")
        done += len(chunk)
        print(f"  {done}/{len(rows)}")

    left = q("""select count(*) n from resource_block b join resource r on r.id=b.resource_id
                where b.embedding is null and r.status in ('published','review')
                  and coalesce(btrim(b.text),'') <> ''""")[0]["n"]
    print(f"done. {left} still unembedded.")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
