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

V = os.environ.get("VAULT", "/tmp/pbs")
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

SHARED = {"Sygma Hub": "0APzpyHHfvUyIUk9PVA", "Canary Detect": "0AAcMZiTrK0txUk9PVA", "Sygma Private": "0AC_ioGo0GJ3tUk9PVA", "Ashcroft Family": "0ACX0xe254y5kUk9PVA", "One System": "0AGTfg0QwTS8kUk9PVA", "El Atico": "0AP-TBWWevTInUk9PVA",
          "Sygma Mala": "0ANYL9DOJQtmQUk9PVA", "Sygma Trainers": "0AP9_VgbvNGyEUk9PVA", "External Sygma Solutions": "0AOTm_FPU_iRmUk9PVA", "External Canary Detect": "0APjm9rgEA8PDUk9PVA",
          "Entities Private": "0APHr3b2NkrNNUk9PVA"}
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

def fetch_folder(fid):
    """Resolve ONE parent folder not already known locally -- a folder created since the last full
    index -- so a new file still gets its full correct path. Returns None for a drive root (a folder
    with no parent), which stops the walk and keeps paths root-relative (identical convention to
    drive-files-index.py, so the watcher never disagrees with the full scan)."""
    try:
        m = gapi(f"/files/{fid}", {"fields": "id,name,parents", "supportsAllDrives": "true"})
        par = (m.get("parents") or [None])[0]
        return (m.get("name"), par) if par else None
    except Exception:
        return None

def process_drive(drive, did):
    """One drive's change feed → (upserts, deletes). Errors propagate to the caller so a single
    drive's failure can't starve the rest; an expired change token (410/404) is re-baselined there."""
    t = get_token(drive)
    if not t:
        set_token(drive, start_token(did)); print(f"{drive}: token initialised (baseline)", flush=True); return 0, 0
    fmap = load_folders(drive)
    raw = []
    pt = t
    while pt:
        params = {"pageToken": pt, "fields": "nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,parents,mimeType,size,modifiedTime,trashed,driveId,ownedByMe))", "pageSize": 1000, "includeRemoved": "true", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if did: params["driveId"] = did; params["corpora"] = "drive"
        r = gapi("/changes", params)
        for chg in r.get("changes", []):
            fid = chg.get("fileId")
            if not fid: continue  # drive-level change, no file
            raw.append((fid, bool(chg.get("removed")), chg.get("file")))
        if r.get("newStartPageToken"):
            set_token(drive, r["newStartPageToken"]); pt = None
        else:
            pt = r.get("nextPageToken")
    # Pass 1 -- register EVERY changed folder into the map before resolving any path. Changes arrive
    # in no guaranteed parent-first order, so a new file can be seen before its own new parent folder.
    for fid, removed, f in raw:
        if f and not removed and not f.get("trashed") and f.get("mimeType") == FOLDER:
            fmap[fid] = (f["name"], (f.get("parents") or [None])[0])
    # Pass 2 -- build upserts/deletes with full paths, fetching any parent still unknown (a new
    # folder absent from this change batch) so brand-new files never land at a truncated/root path.
    upserts = []; deletes = []; _unresolved = []
    for fid, removed, f in raw:
        if removed or (f and f.get("trashed")):
            deletes.append(fid); continue
        if not f: continue
        # On the My Drive (user-corpus) pass, skip two kinds of item so neither gets relabelled
        # 'My Drive': (1) shared-DRIVE files (driveId set) — their own per-drive pass already upserts
        # them with the correct drive + full path; (2) "Shared with me" files Pete doesn't own
        # (ownedByMe false) — clutter that isn't in his real My Drive. Mirrors the `'me' in owners`
        # filter in drive-files-index.py. (Without (1): the original index-corruption bug; without the
        # false-deletes that includeItemsFromAllDrives=false caused.)
        if did is None and (f.get("driveId") or not f.get("ownedByMe")): continue
        par = (f.get("parents") or [None])[0]
        cur = par; guard = 0; chain_ok = True
        while cur and cur not in fmap and guard < 50:
            got = fetch_folder(cur)
            if not got:
                # ROOT CAUSE of the recurring path drift (18 Jul 2026). This used to break and
                # then store the HALF-BUILT path as if it were fact, so a transient parent fetch
                # failure permanently recorded a file at the wrong location — e.g.
                # 'Customers and Suppliers/Customers/README.md' saved as 'Customers/README.md'.
                # It cost 403 rows once and 158 more in a single afternoon.
                # An unresolvable chain means WE DO NOT KNOW the path — so do not write one.
                # parent_id is still correct, so drive-path-rebuild.py reconstructs it from the
                # tree, and the daily locator check reports it meanwhile. Never assert a guess.
                chain_ok = False
                break
            fmap[cur] = got; cur = got[1]; guard += 1
        isf = f.get("mimeType") == FOLDER
        pp = path_of(fmap, par)
        fp = (pp + "/" + f["name"]) if pp else f["name"]
        if "_backups" in fp.split("/"): continue   # cold-backup folders are hidden from the file index (Pete, 2026-06-26)
        row = {"drive_file_id": fid, "name": f["name"], "path": fp, "drive": drive, "entity": drive, "mime": "folder" if isf else f.get("mimeType"), "size": int(f["size"]) if f.get("size") else None, "modified_time": f.get("modifiedTime"), "is_folder": isf, "parent_id": par}
        if not chain_ok:
            row.pop("path")          # leave any existing path untouched rather than overwrite it with a guess
            _unresolved.append(f["name"])
        upserts.append(row)
    # de-dup: a file both changed+removed in window -> delete wins
    delset = set(deletes)
    upserts = [u for u in upserts if u["drive_file_id"] not in delset]
    for i in range(0, len(upserts), 500):
        cc("POST", "drive_files?on_conflict=drive_file_id", upserts[i:i + 500])
    for fid in deletes:
        cc("DELETE", f"drive_files?drive_file_id=eq.{fid}")
    if _unresolved:
        print(f"  !! {len(_unresolved)} file(s) had an UNRESOLVABLE parent chain — path left unwritten "
              f"rather than guessed: {', '.join(_unresolved[:5])}"
              + (f" (+{len(_unresolved)-5} more)" if len(_unresolved) > 5 else "")
              + ". Run drive-path-rebuild.py --apply to fill them from the tree.")
    print(f"{drive}: {len(upserts)} upserts, {len(deletes)} deletes", flush=True)
    return len(upserts), len(deletes)

total_up = total_del = 0
for drive, did in DRIVES:
    try:
        up, dl = process_drive(drive, did)
        total_up += up; total_del += dl
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):   # expired/invalid change token → re-baseline so it self-heals next run
            try:
                set_token(drive, start_token(did))
                print(f"{drive}: change token expired ({e.code}) → re-baselined; resumes next run", flush=True)
            except Exception as e2:
                print(f"{drive}: re-baseline FAILED after {e.code}: {e2}", flush=True)
        else:
            print(f"{drive}: HTTP {e.code} — skipped this run, other drives continue", flush=True)
    except Exception as e:
        print(f"{drive}: ERROR {e} — skipped this run, other drives continue", flush=True)
print(f"DONE: {total_up} upserts, {total_del} deletes across all drives", flush=True)
