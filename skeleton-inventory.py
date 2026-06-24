import json, os, subprocess
VAULT="/Users/peterashcroft/Second Brain"; SC=f"{VAULT}/Library/processes/scripts"
man=json.load(open(f"{VAULT}/Library/processes/crons-manifest.json"))
cron_bn={os.path.basename(c.get("script_file","")) for c in man["crons"] if c.get("script_file")}
# helpers that crons also pull (also_sync) are cloud-side too
for c in man["crons"]:
    for a in (c.get("also_sync") or []): cron_bn.add(os.path.basename(a))
def gl(pat):
    r=subprocess.run(["grep","-rl","--include=*.py",pat,SC],capture_output=True,text=True)
    return {l for l in r.stdout.splitlines() if l}
hard=gl("/Users/peterashcroft/Second Brain")
env=gl('environ.get("VAULT"') | gl('getenv("VAULT"')
fix=[]; cloud=[]; ok=[]
for f in sorted(hard):
    b=os.path.basename(f)
    if f in env: ok.append(b)
    elif b in cron_bn: cloud.append(b)
    else: fix.append(b)
print(f"hardcoding .py: {len(hard)} | env-aware already: {len(ok)} | cloud-only cron/also scripts (stay on Railway): {len(cloud)} | LOCAL set to root-fix: {len(fix)}")
print(f"\nLOCAL set to make root-aware (I-1 worklist) [{len(fix)}]:")
for b in fix: print("  [ ]", b)
