import json,urllib.request,urllib.error,os,re,datetime
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")
SEC=f"{VAULT}/Library/processes/secrets"
k=json.load(open(f"{SEC}/command-centre-supabase-keys.json")); URL=k["url"]; SR=k["service_role_key"]
MEM="/Users/peterashcroft/.claude/projects/-Users-peterashcroft-Second-Brain/memory"
H={"apikey":SR,"Authorization":f"Bearer {SR}","Content-Type":"application/json","Prefer":"resolution=merge-duplicates,return=minimal"}
def fm_parse(text):
    fm={}; body=text
    if text.startswith("---"):
        end=text.find("\n---",3)
        if end!=-1:
            raw=text[3:end].strip(); body=text[end+4:].lstrip("\n"); key=None
            for line in raw.split("\n"):
                if re.match(r'^\s*-\s+',line) and key and isinstance(fm.get(key),list): fm[key].append(line.strip()[2:].strip().strip('"\'')); continue
                m=re.match(r'^([A-Za-z0-9_\-]+):\s*(.*)$',line)
                if m:
                    key=m.group(1); val=m.group(2).strip()
                    if val=="": fm[key]=[]
                    elif val.startswith("[") and val.endswith("]"): fm[key]=[x.strip().strip('"\'') for x in val[1:-1].split(",") if x.strip()]
                    else: fm[key]=val.strip('"\'')
    return fm,body
LINK=re.compile(r'\[\[([^\]]+)\]\]')
rows=[]
if not os.path.isdir(MEM): print("memory dir not found:",MEM); raise SystemExit
for f in sorted(os.listdir(MEM)):
    if not f.endswith(".md"): continue
    text=open(os.path.join(MEM,f),encoding="utf-8",errors="replace").read()
    fm,body=fm_parse(text); stem=os.path.splitext(f)[0]
    links=sorted(set(m.split("|")[0].split("#")[0].strip() for m in LINK.findall(body) if m.strip()))
    rows.append({"vault_path":f"_memory/{f}","slug":fm.get("name") or stem,"type":"memory",
        "entity":"pa","title":fm.get("name") or stem,"body":body[:200000],
        "frontmatter":fm,"tags":[],"links":links[:60],"word_count":len(body.split()),
        "source_updated":datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(MEM,f)),datetime.timezone.utc).strftime("%Y-%m-%d")})
req=urllib.request.Request(f"{URL}/rest/v1/vault_notes?on_conflict=vault_path",data=json.dumps(rows).encode(),headers=H,method="POST")
try: urllib.request.urlopen(req); print(f"✅ {len(rows)} memory entries ingested")
except urllib.error.HTTPError as e: print("err",e.code,e.read().decode()[:300])