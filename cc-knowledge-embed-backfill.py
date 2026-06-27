import json,urllib.request,urllib.error,time
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC=f"{VAULT}/Library/processes/secrets"; REF="zhexcaflgahdcbzvbyfq"
k=json.load(open(f"{SEC}/command-centre-supabase-keys.json")); URL=k["url"]; SR=k["service_role_key"]
tok=(os.environ.get("SUPABASE_TOKEN") or open(f"{SEC}/supabase-token").read().strip())
VKEY=(os.environ.get("VOYAGE_API_KEY") or open(f"{SEC}/voyage-api-key").read().strip())
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
HR={"apikey":SR,"Authorization":f"Bearer {SR}","Content-Type":"application/json"}
def _post_embed(texts):  # direct Voyage — the Supabase edge fn /functions/v1/embed 400s, so bypass it
    req=urllib.request.Request("https://api.voyageai.com/v1/embeddings",
        data=json.dumps({"input":texts,"model":"voyage-3.5-lite","input_type":"document","output_dimension":1024}).encode(),
        headers={"Authorization":f"Bearer {VKEY}","Content-Type":"application/json"},method="POST")
    return [d["embedding"] for d in json.loads(urllib.request.urlopen(req,timeout=180).read())["data"]]
def embed(texts):
    try: return _post_embed(texts)
    except urllib.error.HTTPError as e:
        if e.code in (429,500,502,503,504,546) and len(texts)>1:
            m=len(texts)//2; return embed(texts[:m])+embed(texts[m:])
        if e.code in (429,500,502,503,504,546): time.sleep(2); return _post_embed(texts)  # last-resort single retry
        raise
def get_null(limit=200):
    req=urllib.request.Request(f"{URL}/rest/v1/vault_notes?embedding=is.null&select=id,title,body&limit={limit}",headers=HR)
    return json.loads(urllib.request.urlopen(req).read())
def sql(q):
    req=urllib.request.Request(f"https://api.supabase.com/v1/projects/{REF}/database/query",data=json.dumps({"query":q}).encode(),headers={"Authorization":f"Bearer {tok}","Content-Type":"application/json","User-Agent":UA},method="POST")
    return urllib.request.urlopen(req,timeout=120).read()
def chunks(a,n):
    for i in range(0,len(a),n): yield a[i:i+n]
total=0
while True:
    notes=get_null(200)
    if not notes: break
    updates=[]
    for batch in chunks(notes,10):
        texts=[(((n.get("title") or "")+"\n"+(n.get("body") or ""))[:1500]) for n in batch]
        try: vecs=embed(texts)
        except Exception as e: print("embed fail",str(e)[:100]); continue
        for n,v in zip(batch,vecs):
            updates.append((n["id"],"["+",".join(f"{x:.6f}" for x in v)+"]"))
    if not updates: print("no progress — stopping"); break
    for ub in chunks(updates,50):
        vals=",".join(f"('{i}'::uuid,'{e}')" for i,e in ub)
        sql(f"update public.vault_notes v set embedding=d.e::vector from (values {vals}) d(id,e) where v.id=d.id;")
    total+=len(updates); print(f"  ...{total} embedded",flush=True)
print(f"DONE: {total} embeddings written")