import os,json,urllib.request
SEC=os.path.join(os.environ.get("VAULT","/tmp/pbs"),"Library/processes/secrets")
pk=json.load(open(f"{SEC}/passion-fit-supabase-keys.json"))
PURL,PKEY=pk["project_url"],pk["service_role_key"]
def portal(p):
    req=urllib.request.Request(f"{PURL}/rest/v1/{p}",headers={"apikey":PKEY,"Authorization":f"Bearer {PKEY}"})
    return json.loads(urllib.request.urlopen(req,timeout=90).read().decode())
for slug in ["the-pie-of-potential","potential-tom-verbatim"]:
    for _ in range(3):
        try:
            rows=portal(f"frank_knowledge?select=slug,title,body,concepts&slug=eq.{slug}");break
        except Exception as e:
            rows=None
    print("="*80);print("SLUG:",slug)
    if rows:
        for r in rows: print("TITLE:",r["title"]);print(r["body"])
    else: print("(fetch failed)")
