import json,urllib.request,urllib.error,os,re,sys,datetime
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC=f"{VAULT}/Library/processes/secrets"
k=json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL=k["url"]; SR=k["service_role_key"]; VAULT=VAULT
H={"apikey":SR,"Authorization":f"Bearer {SR}","Content-Type":"application/json","Prefer":"resolution=merge-duplicates,return=minimal"}
def post(rows):
    req=urllib.request.Request(f"{URL}/rest/v1/vault_notes?on_conflict=vault_path",
        data=json.dumps(rows).encode(),headers=H,method="POST")
    urllib.request.urlopen(req)
def fm_parse(text):
    """split frontmatter + body, light YAML parse for scalars + lists."""
    fm={}; body=text
    if text.startswith("---"):
        end=text.find("\n---",3)
        if end!=-1:
            raw=text[3:end].strip(); body=text[end+4:].lstrip("\n")
            key=None
            for line in raw.split("\n"):
                if re.match(r'^\s*-\s+',line) and key:   # block list item
                    fm.setdefault(key,[]); 
                    if isinstance(fm[key],list): fm[key].append(line.strip()[2:].strip().strip('"\''))
                    continue
                m=re.match(r'^([A-Za-z0-9_\-]+):\s*(.*)$',line)
                if m:
                    key=m.group(1); val=m.group(2).strip()
                    if val=="" : fm[key]=[]   # maybe block list follows
                    elif val.startswith("[") and val.endswith("]"):
                        fm[key]=[x.strip().strip('"\'') for x in val[1:-1].split(",") if x.strip()]
                    else: fm[key]=val.strip('"\'')
    return fm,body
PREFIX={"PA":"pa","SY":"sy","CD":"cd","OS":"os","EA":"ea","AT":"at"}
def entity_of(rel,fm):
    if fm.get("prefix"): return str(fm["prefix"]).lower()
    if fm.get("entity"): return str(fm["entity"]).lower()
    for part in rel.split(os.sep):
        m=re.match(r'^(PA|SY|CD|OS|EA|AT)-',part)
        if m: return PREFIX[m.group(1)]
    top=rel.split(os.sep)[0]
    return {"Businesses":"biz","Personal":"pa","Daily":"pa","Library":"lib"}.get(top,"")
def type_of(rel,fm):
    if fm.get("type"): return str(fm["type"])
    p=rel.lower()
    for k2,v in [("library/lessons","lesson"),("library/decisions","decision"),("library/audits","audit"),
                 ("library/meetings","meeting"),("library/processes","process"),("library/templates","template"),
                 ("daily/","daily"),("customers/","customer"),("suppliers/","supplier"),
                 ("businesses/","business"),("properties/","property"),("accreditations/","accreditation"),
                 ("projects/","project")]:
        if k2 in p: return v
    return "note"
def h1(body):
    m=re.search(r'^#\s+(.+)$',body,re.M); return m.group(1).strip() if m else None
LINK=re.compile(r'\[\[([^\]]+)\]\]')
def links_of(body):
    out=[]
    for m in LINK.findall(body):
        t=m.split("|")[0].split("#")[0].strip()
        if t: out.append(t)
    return sorted(set(out))
def row_for(path):
    rel=os.path.relpath(path,VAULT)
    try: text=open(path,encoding="utf-8").read()
    except: text=open(path,encoding="utf-8",errors="replace").read()
    fm,body=fm_parse(text)
    stem=os.path.splitext(os.path.basename(path))[0]
    tags=fm.get("tags") if isinstance(fm.get("tags"),list) else ([fm["tags"]] if fm.get("tags") else [])
    su=fm.get("updated") or fm.get("date") or None
    md=re.match(r'(\d{4}-\d{2}-\d{2})',str(su)) if su else None
    su=md.group(1) if md else datetime.datetime.fromtimestamp(os.path.getmtime(path),datetime.timezone.utc).strftime("%Y-%m-%d")
    return {
        "vault_path":rel, "slug":fm.get("slug") or stem, "type":type_of(rel,fm),
        "entity":entity_of(rel,fm), "title":fm.get("title") or h1(body) or stem,
        "body":body.replace("\x00","")[:200000], "frontmatter":fm, "tags":tags[:40], "links":links_of(body)[:60],
        "word_count":len(body.split()), "source_updated":str(su)
    }
def walk_md(roots):
    for r in roots:
        base=os.path.join(VAULT,r)
        if os.path.isfile(base) and base.endswith(".md"):   # allow ingesting a single file, not just a dir
            yield base; continue
        if not os.path.isdir(base): continue
        for dp,dn,fn in os.walk(base):
            dn[:]=[d for d in dn if not d.startswith(".") and d!="_archive"]
            for f in fn:
                if f.endswith(".md") and not f.startswith("."): yield os.path.join(dp,f)
roots=sys.argv[1:] or ["Library/lessons"]
batch=[]; n=0; fails=0
for p in walk_md(roots):
    try: batch.append(row_for(p))
    except Exception as e: fails+=1; continue
    if len(batch)>=100:
        try: post(batch); n+=len(batch)
        except urllib.error.HTTPError as e: print("POST err",e.code,e.read().decode()[:200]); fails+=len(batch)
        batch=[]; print(f"  ...{n} ingested",flush=True)
if batch:
    try: post(batch); n+=len(batch)
    except urllib.error.HTTPError as e: print("POST err",e.code,e.read().decode()[:300]); fails+=len(batch)
print(f"DONE: {n} notes ingested, {fails} failed  (roots={roots})")