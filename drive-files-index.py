#!/usr/bin/env python3
"""drive-files-index.py -- scan the home drives + My Drive and upsert every
folder + file into the CC `drive_files` table (the file-index / where-is-everything).

Flat-scan per drive (fast) + in-memory path resolution, then batched upsert
(on_conflict=drive_file_id) into CC Supabase via the REST API. Idempotent —
re-run any time for a full refresh; the Changes-API watcher keeps it current between runs.
"""
import json, time, base64, urllib.request, urllib.parse, urllib.error, tempfile, os, subprocess, sys
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

V = VAULT
KEY = V + "/Library/processes/secrets/google-seo-service-account.json"
IMP = "pete.ashcroft@sygma-solutions.com"; SCOPE = "https://www.googleapis.com/auth/drive"; BASE = "https://www.googleapis.com/drive/v3"
ck = json.load(open(V + "/Library/processes/secrets/command-centre-supabase-keys.json"))
CCURL = ck["url"]; SR = ck["service_role_key"]
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

def api(params):
    for a in range(7):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/files?" + urllib.parse.urlencode(params), headers={"Authorization": f"Bearer {tok()}"}), timeout=120).read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503) and a < 6: time.sleep(2 * (a + 1)); continue
            raise
        except Exception:
            if a < 6: time.sleep(2 * (a + 1)); continue
            raise

def page(params):
    out = []; pt = None
    while True:
        q = dict(params)
        if pt: q["pageToken"] = pt
        r = api(q); out += r.get("files", []); pt = r.get("nextPageToken")
        if not pt: break
    return out

SHARED = {"Sygma Hub": "0APzpyHHfvUyIUk9PVA", "Canary Detect": "0AAcMZiTrK0txUk9PVA", "Sygma Private": "0AC_ioGo0GJ3tUk9PVA", "Ashcroft Family": "0ACX0xe254y5kUk9PVA", "One System": "0AGTfg0QwTS8kUk9PVA", "El Atico": "0AP-TBWWevTInUk9PVA",
          "Sygma Mala": "0ANYL9DOJQtmQUk9PVA", "Sygma Trainers": "0AP9_VgbvNGyEUk9PVA", "External Sygma Solutions": "0AOTm_FPU_iRmUk9PVA", "External Canary Detect": "0APjm9rgEA8PDUk9PVA",
          "Entities Private": "0APHr3b2NkrNNUk9PVA"}
FFIELDS = "nextPageToken,files(id,name,parents,driveId)"
XFIELDS = "nextPageToken,files(id,name,parents,mimeType,size,modifiedTime,driveId)"

def build(drive, folders, files):
    fmap = {f["id"]: (f["name"], (f.get("parents") or [None])[0]) for f in folders}
    def path(fid):
        parts = []; cur = fid; seen = set()
        while cur in fmap and cur not in seen:
            seen.add(cur); nm, par = fmap[cur]; parts.append(nm); cur = par
        return "/".join(reversed(parts))
    rows = []
    for f in folders:
        rows.append({"drive_file_id": f["id"], "name": f["name"], "path": path(f["id"]), "drive": drive, "entity": drive, "mime": "folder", "size": None, "modified_time": None, "is_folder": True, "parent_id": (f.get("parents") or [None])[0]})
    for f in files:
        par = (f.get("parents") or [None])[0]
        pp = path(par)
        rows.append({"drive_file_id": f["id"], "name": f["name"], "path": (pp + "/" + f["name"]) if pp else f["name"], "drive": drive, "entity": drive, "mime": f.get("mimeType"), "size": int(f["size"]) if f.get("size") else None, "modified_time": f.get("modifiedTime"), "is_folder": False, "parent_id": par})
    # cold-backup folders are hidden from the file index (Pete, 2026-06-26)
    return [r for r in rows if "_backups" not in (r["path"] or "").split("/")]

def scan_shared(name, did):
    common = {"corpora": "drive", "driveId": did, "includeItemsFromAllDrives": "true", "supportsAllDrives": "true", "pageSize": 1000}
    folders = page({**common, "q": "mimeType='application/vnd.google-apps.folder' and trashed=false", "fields": FFIELDS})
    files = page({**common, "q": "mimeType!='application/vnd.google-apps.folder' and trashed=false", "fields": XFIELDS})
    return build(name, folders, files)

def scan_mydrive():
    common = {"corpora": "user", "spaces": "drive", "supportsAllDrives": "true", "pageSize": 1000}
    # `'me' in owners` keeps My Drive to the files Pete OWNS. Without it, corpora=user also returns
    # every "Shared with me" item, which then gets indexed + relabelled 'My Drive' — clutter that
    # doesn't match Pete's real My Drive. (My-Drive ownership fix, 2026-06-25.)
    folders = page({**common, "q": "mimeType='application/vnd.google-apps.folder' and trashed=false and 'me' in owners", "fields": FFIELDS})
    files = page({**common, "q": "mimeType!='application/vnd.google-apps.folder' and trashed=false and 'me' in owners", "fields": XFIELDS})
    # A user-OWNED item that physically lives in a shared drive surfaces in this corpora=user pass too.
    # Its own scan_shared() pass already captures it with the correct drive + full path, so drop it here
    # — otherwise build() relabels it 'My Drive' (the original index-corruption bug; same guard as
    # drive-changes-watch.py: `if did is None and f.get("driveId"): continue`).
    folders = [f for f in folders if not f.get("driveId")]
    files = [f for f in files if not f.get("driveId")]
    return build("My Drive", folders, files)

def upsert(rows):
    H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    done = 0
    for i in range(0, len(rows), 500):
        b = rows[i:i + 500]
        req = urllib.request.Request(f"{CCURL}/rest/v1/drive_files?on_conflict=drive_file_id", data=json.dumps(b).encode(), headers=H, method="POST")
        for a in range(5):
            try:
                urllib.request.urlopen(req, timeout=120); done += len(b); break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and a < 4: time.sleep(2 * (a + 1)); continue
                print("UPSERT ERR", e.code, e.read().decode()[:300], flush=True); break
            except Exception:
                if a < 4: time.sleep(2 * (a + 1)); continue
                raise
    return done

all_rows = []
for name, did in SHARED.items():
    try:
        r = scan_shared(name, did); print(f"{name}: {len(r)} rows", flush=True); all_rows += r
    except Exception as e:
        print(f"{name}: SCAN FAILED {e}", flush=True)
try:
    r = scan_mydrive(); print(f"My Drive: {len(r)} rows", flush=True); all_rows += r
except Exception as e:
    print(f"My Drive: SCAN FAILED {e}", flush=True)
print(f"TOTAL {len(all_rows)} rows -> upserting to CC drive_files", flush=True)
n = upsert(all_rows)
print(f"DONE: upserted {n} rows", flush=True)