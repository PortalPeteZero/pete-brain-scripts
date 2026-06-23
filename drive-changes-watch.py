#!/usr/bin/env python3
"""drive-changes-watch.py -- CONTINUOUS CAPTURE.

Polls the Google Drive Changes API per drive (7 home shared drives + My Drive) since the
last saved page-token, and applies every add / move / rename / trash to the CC `drive_files`
index — so the index stays live as Pete OR staff add or move files, web or synced-local.
Saves the new token each run. First run per drive just initialises the token (baseline =
the full index already built by drive-files-index.py).

Run on a ~15-min cron. Idempotent. Everything is upsert/delete on drive_files.

Cloud-ready (Business OS): uses the Drive Changes API + CC tables only — no local Drive mount, no
local state files — so it runs headless on Railway. $VAULT/env-aware for the SA key + CC keys.

# CRON-META
# what: Continuous Drive capture — polls the Drive Changes API per drive, applies adds/moves/renames/trashes to the CC drive_files index
# why: keeps the ~150k-file index live so 'where is X' stays current as Pete/staff add or move files (web or synced)
# reads: Google Drive Changes API (SA); CC drive_change_tokens (page-token state) + drive_files (folder map)
# writes: CC drive_files (upsert/delete) + drive_change_tokens (new page token)
# entity: command-centre
# schedule: */15 * * * *
# timezone: Atlantic/Canary
# CRON-META-END
"""
import json, time, base64, urllib.request, urllib.parse, urllib.error, tempfile, os, subprocess

V = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")
KEY = V + "/Library/processes/secrets/google-seo-service-account.json"
IMP = "pete.ashcroft@sygma-solutions.com"; SCOPE = "https://www.googleapis.com/auth/drive"; BASE = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
CCURL = os.environ.get("CC_SUPABASE_URL"); SR = os.environ.get("CC_SUPABASE_SERVICE_KEY")
if not (CCURL and SR):
    ck = json.load(open(V + "/Library/processes/secrets/command-centre-supabase-keys.json"))
    CCURL = ck["url"]; SR = ck["service_role_key"]
CCH = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}
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

def gapi(path, params):
    for a in range(6):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(params), headers={"Authorization": f"Bearer {tok()}"}), timeout=90).read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503) and a < 5: time.sleep(2 * (a + 1)); continue
            raise
        except Exception:
            if a < 5: time.sleep(2 * (a + 1)); continue
            raise

def cc(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = dict(CCH)
    if method == "POST": h["Prefer"] = "resolution=merge-duplicates"
    for a in range(5):
        try:
            r = urllib.request.urlopen(urllib.request.Request(f"{CCURL}/rest/v1/{path}", data=data, headers=h, method=method), timeout=60)
            t = r.read().decode(); return json.loads(t) if t.strip() else []
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < 4: time.sleep(2 * (a + 1)); continue
            print("CC ERR", method, path, e.code, e.read().decode()[:200]); return []
        except Exception:
            if a < 4: time.sleep(2 * (a + 1)); continue
            raise

SHARED = {"Sygma Hub": "0APzpyHHfvUyIUk9PVA", "Canary Detect": "0AAcMZiTrK0txUk9PVA", "Sygma Private": "0AC_ioGo0GJ3tUk9PVA", "CD Private": "0AFilU9XoRsf_Uk9PVA", "Ashcroft Family": "0ACX0xe254y5kUk9PVA", "One System": "0AGTfg0QwTS8kUk9PVA", "El Atico": "0AP-TBWWevTInUk9PVA",
          "Sygma Mala": "0ANYL9DOJQtmQUk9PVA", "Sygma Trainers": "0AP9_VgbvNGyEUk9PVA", "External Sygma Solutions": "0AOTm_FPU_iRmUk9PVA", "External Canary Detect": "0APjm9rgEA8PDUk9PVA"}
DRIVES = list(SHARED.items()) + [("My Drive", None)]

def get_token(drive):
    r = cc("GET", f"drive_change_tokens?drive=eq.{urllib.parse.quote(drive)}&select=token")
    return r[0]["token"] if r else None

def set_token(drive, t):
    cc("POST", "drive_change_tokens?on_conflict=drive", [{"drive": drive, "token": t, "updated_at": "now()"}])

def start_token(did):
    p = {"supportsAllDrives": "true"}
    if did: p["driveId"] = did
    return gapi("/changes/startPageToken", p)["startPageToken"]

def load_folders(drive):
    out = {}; off = 0
    while True:
        r = cc("GET", f"drive_files?drive=eq.{urllib.parse.quote(drive)}&is_folder=eq.true&select=drive_file_id,name,parent_id&limit=1000&offset={off}")
        for x in r: out[x["drive_file_id"]] = (x["name"], x["parent_id"])
        if len(r) < 1000: break
        off += 1000
    return out

def path_of(fmap, fid):
    parts = []; cur = fid; seen = set()
    while cur in fmap and cur not in seen:
        seen.add(cur); nm, par = fmap[cur]; parts.append(nm); cur = par
    return "/".join(reversed(parts))

total_up = total_del = 0
for drive, did in DRIVES:
    t = get_token(drive)
    if not t:
        set_token(drive, start_token(did)); print(f"{drive}: token initialised (baseline)", flush=True); continue
    fmap = load_folders(drive)
    upserts = []; deletes = []
    pt = t
    while pt:
        params = {"pageToken": pt, "fields": "nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,parents,mimeType,size,modifiedTime,trashed))", "pageSize": 1000, "includeRemoved": "true", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if did: params["driveId"] = did; params["corpora"] = "drive"
        r = gapi("/changes", params)
        for chg in r.get("changes", []):
            fid = chg.get("fileId")
            if not fid: continue  # drive-level change, no file
            f = chg.get("file")
            if chg.get("removed") or (f and f.get("trashed")):
                deletes.append(fid)
            elif f:
                par = (f.get("parents") or [None])[0]
                isf = f["mimeType"] == FOLDER
                if isf: fmap[fid] = (f["name"], par)
                pp = path_of(fmap, par)
                upserts.append({"drive_file_id": fid, "name": f["name"], "path": (pp + "/" + f["name"]) if pp else f["name"], "drive": drive, "entity": drive, "mime": "folder" if isf else f.get("mimeType"), "size": int(f["size"]) if f.get("size") else None, "modified_time": f.get("modifiedTime"), "is_folder": isf, "parent_id": par})
        if r.get("newStartPageToken"):
            set_token(drive, r["newStartPageToken"]); pt = None
        else:
            pt = r.get("nextPageToken")
    # de-dup: a file both changed+removed in window -> delete wins
    delset = set(deletes)
    upserts = [u for u in upserts if u["drive_file_id"] not in delset]
    for i in range(0, len(upserts), 500):
        cc("POST", "drive_files?on_conflict=drive_file_id", upserts[i:i + 500])
    for fid in deletes:
        cc("DELETE", f"drive_files?drive_file_id=eq.{fid}")
    total_up += len(upserts); total_del += len(deletes)
    print(f"{drive}: {len(upserts)} upserts, {len(deletes)} deletes", flush=True)
print(f"DONE: {total_up} upserts, {total_del} deletes across all drives", flush=True)
