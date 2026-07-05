#!/usr/bin/env python3
"""dedupe-batched.py -- cross-drive dedupe for the 7 shared home drives.

Fast flat-scan (folders + files paginated) + in-memory path resolution
+ Courses protection (Sygma Hub Courses/ copies are NEVER trashed)
+ concurrent trashing.

Default = DRY RUN: writes a manifest (KEEP/TRASH per file), trashes NOTHING.
Pass --execute to actually trash the TRASH-marked files (recoverable in Drive trash).

Rule per md5 group (>1 identical file):
  - if any copy is under Hub Courses/  -> keep ALL Courses copies, trash only non-Courses copies
  - else                               -> keep the least-nested/shortest path, trash the rest
"""
import json, time, base64, urllib.request, urllib.parse, urllib.error, tempfile, os, subprocess, collections, sys
from concurrent.futures import ThreadPoolExecutor
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

V = VAULT
KEY = V + "/Library/processes/secrets/google-seo-service-account.json"
IMP = "pete.ashcroft@sygma-solutions.com"
SCOPE = "https://www.googleapis.com/auth/drive"
BASE = "https://www.googleapis.com/drive/v3"
MANIFEST = V + "/Projects/PA-Command-Centre/files/dedupe-manifest-2026-06-21.tsv"
creds = json.load(open(KEY)); _tc = {}

def tok():
    now = int(time.time())
    if _tc.get("exp", 0) > now + 60: return _tc["tok"]
    b = lambda d: base64.urlsafe_b64encode(d if isinstance(d, bytes) else d.encode()).decode().rstrip("=")
    ts = b(json.dumps({"alg": "RS256", "typ": "JWT"})) + "." + b(json.dumps({"iss": creds["client_email"], "sub": IMP, "scope": SCOPE, "aud": "https://oauth2.googleapis.com/token", "exp": now + 3600, "iat": now}))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f: f.write(creds["private_key"]); kf = f.name
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", kf, "-binary"], input=ts.encode(), capture_output=True).stdout; os.unlink(kf)
    r = urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": ts + "." + b(sig)}).encode()))
    t = json.loads(r.read())["access_token"]; _tc.update(tok=t, exp=now + 3600); return t

def api(p, params, method="GET", body=None):
    data = json.dumps(body).encode() if body else None
    for a in range(7):
        try:
            h = {"Authorization": f"Bearer {tok()}"}
            if data: h["Content-Type"] = "application/json"
            return json.loads(urllib.request.urlopen(urllib.request.Request(BASE + p + "?" + urllib.parse.urlencode(params), data=data, headers=h, method=method), timeout=120).read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503) and a < 6: time.sleep(2 * (a + 1)); continue
            raise
        except Exception:
            if a < 6: time.sleep(2 * (a + 1)); continue
            raise

DR = {"Sygma Hub": "0APzpyHHfvUyIUk9PVA", "Canary Detect": "0AAcMZiTrK0txUk9PVA", "Sygma Private": "0AC_ioGo0GJ3tUk9PVA", "Ashcroft Family": "0ACX0xe254y5kUk9PVA", "One System": "0AGTfg0QwTS8kUk9PVA", "El Atico": "0AP-TBWWevTInUk9PVA"}

def page(did, q, fields):
    out = []; pt = None
    while True:
        p = {"q": q, "corpora": "drive", "driveId": did, "includeItemsFromAllDrives": "true", "supportsAllDrives": "true", "fields": f"nextPageToken,files({fields})", "pageSize": 1000}
        if pt: p["pageToken"] = pt
        r = api("/files", p); out += r.get("files", []); pt = r.get("nextPageToken")
        if not pt: break
    return out

def scan(did):
    folders = page(did, "mimeType='application/vnd.google-apps.folder' and trashed=false", "id,name,parents")
    fmap = {f["id"]: (f["name"], (f.get("parents") or [None])[0]) for f in folders}
    files = page(did, "mimeType!='application/vnd.google-apps.folder' and trashed=false", "id,name,md5Checksum,size,parents")
    def path(fid):
        parts = []; cur = fid; seen = set()
        while cur in fmap and cur not in seen:
            seen.add(cur); nm, par = fmap[cur]; parts.append(nm); cur = par
        return "/".join(reversed(parts))
    out = []
    for f in files:
        par = (f.get("parents") or [None])[0]
        out.append((path(par), f["name"], f.get("md5Checksum"), f["id"], int(f.get("size") or 0)))
    return out

def trash(fid):
    api(f"/files/{fid}", {"supportsAllDrives": "true"}, method="PATCH", body={"trashed": True})

EXECUTE = "--execute" in sys.argv
rows = []; to_trash = []; TT = 0; TB = 0
for name, did in DR.items():
    try:
        files = scan(did)
    except Exception as e:
        print(f"{name}: SCAN FAILED {e}", flush=True); continue
    is_hub = (name == "Sygma Hub")
    def is_courses(p): return is_hub and (p == "Courses" or p.startswith("Courses/") or "/Courses/" in p)
    groups = collections.defaultdict(list)
    for path, nm, md5, fid, size in files:
        if md5: groups[md5].append((path + "/" + nm, fid, size))
    dt = 0; db = 0
    for md5, grp in groups.items():
        if len(grp) < 2: continue
        courses = [g for g in grp if is_courses(g[0])]
        if courses:
            keepset = set(g[1] for g in courses); tlist = [g for g in grp if g[1] not in keepset]
        else:
            keeper = min(grp, key=lambda g: (g[0].count("/"), len(g[0]))); tlist = [g for g in grp if g[1] != keeper[1]]
        tset = set(g[1] for g in tlist)
        for p, fid, size in grp:
            rows.append(f"{name}\t{'TRASH' if fid in tset else 'KEEP'}\t{size}\t{fid}\t{p}")
        for p, fid, size in tlist:
            dt += 1; db += size; to_trash.append((name, p, fid, size))
    print(f"{name}: {len(files)} files, {dt} dups to trash, {db//1024//1024}MB", flush=True)
    TT += dt; TB += db

open(MANIFEST, "w").write("drive\taction\tsize\tid\tpath\n" + "\n".join(rows) + "\n")
print(f"{'EXECUTE' if EXECUTE else 'DRY-RUN'}: {TT} files {'to trash' if not EXECUTE else 'trashing'}, {TB//1024//1024//1024} GB. Manifest: {MANIFEST}", flush=True)

if EXECUTE:
    fails = []
    def do(item):
        try: trash(item[2]); return None
        except Exception as e: return f"{item[0]}\t{item[2]}\t{e}"
    with ThreadPoolExecutor(max_workers=12) as ex:
        for r in ex.map(do, to_trash):
            if r: fails.append(r)
    print(f"EXECUTED: trashed {len(to_trash)-len(fails)}, {len(fails)} failed", flush=True)
    if fails: open(MANIFEST + ".fails", "w").write("\n".join(fails))