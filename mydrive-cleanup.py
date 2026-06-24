#!/usr/bin/env python3
"""mydrive-cleanup.py -- find (and optionally trash) empty folders + .DS_Store junk in My Drive.

Uses the Drive API (corpora=user) so it doesn't depend on the local sync state.
Default = dry-run report. Pass --execute to trash empties + .DS_Store (recoverable 30 days).
"""
import json, time, base64, urllib.request, urllib.parse, urllib.error, tempfile, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

V = VAULT
KEY = V + "/Library/processes/secrets/google-seo-service-account.json"
IMP = "pete.ashcroft@sygma-solutions.com"; SCOPE = "https://www.googleapis.com/auth/drive"; BASE = "https://www.googleapis.com/drive/v3"
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
            return json.loads(urllib.request.urlopen(urllib.request.Request(BASE + p + "?" + urllib.parse.urlencode(params), data=data, headers=h, method=method), timeout=90).read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503) and a < 6: time.sleep(2 * (a + 1)); continue
            raise
        except Exception:
            if a < 6: time.sleep(2 * (a + 1)); continue
            raise

def page(q, fields):
    out = []; pt = None
    while True:
        p = {"q": q, "corpora": "user", "spaces": "drive", "fields": f"nextPageToken,files({fields})", "pageSize": 1000}
        if pt: p["pageToken"] = pt
        r = api("/files", p); out += r.get("files", []); pt = r.get("nextPageToken")
        if not pt: break
    return out

folders = page("mimeType='application/vnd.google-apps.folder' and trashed=false", "id,name,parents")
files = page("mimeType!='application/vnd.google-apps.folder' and trashed=false", "id,name,parents")
fmap = {f["id"]: (f.get("parents") or [None])[0] for f in folders}
fname = {f["id"]: f["name"] for f in folders}

nonempty = set()
for f in files:
    cur = (f.get("parents") or [None])[0]; seen = set()
    while cur in fmap and cur not in seen:
        seen.add(cur); nonempty.add(cur); cur = fmap[cur]
empty = [fid for fid in fmap if fid not in nonempty]
dsstore = [f for f in files if f["name"] == ".DS_Store"]

def path(fid):
    parts = []; cur = fid; seen = set()
    while cur in fmap and cur not in seen:
        seen.add(cur); parts.append(fname[cur]); cur = fmap[cur]
    return "/".join(reversed(parts))

# top-level empties only (parent not itself empty) -> trashing these removes their empty subtrees
empty_set = set(empty)
top_empty = [fid for fid in empty if fmap.get(fid) not in empty_set]
print(f"My Drive: {len(folders)} folders, {len(files)} files", flush=True)
print(f"EMPTY folders: {len(empty)} ({len(top_empty)} top-level)", flush=True)
print(f".DS_Store junk: {len(dsstore)}", flush=True)
for fid in sorted(top_empty, key=path)[:50]: print("  empty:", path(fid), flush=True)

if "--execute" in sys.argv:
    def trash(fid):
        try:
            urllib.request.urlopen(urllib.request.Request(BASE + f"/files/{fid}", data=json.dumps({"trashed": True}).encode(), headers={"Authorization": f"Bearer {tok()}", "Content-Type": "application/json"}, method="PATCH"), timeout=60); return True
        except Exception: return False
    targets = top_empty + [f["id"] for f in dsstore]
    done = sum(1 for r in ThreadPoolExecutor(max_workers=10).map(trash, targets) if r)
    print(f"TRASHED {done}/{len(targets)} (top-level empty folders + .DS_Store)", flush=True)