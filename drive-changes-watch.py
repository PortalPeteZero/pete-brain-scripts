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
            # None means ERRORED. This used to return [], which every caller then read as "no rows"
            # — one line that caused three separate silent failures (see load_folders, get_token and
            # the write path). A genuinely empty result is still [], so the two stay distinguishable.
            print("CC ERR", method, path, e.code, e.read().decode()[:200]); return None
        except Exception:
            if a < 4: time.sleep(2 * (a + 1)); continue
            raise

SHARED = {"Sygma Hub": "0APzpyHHfvUyIUk9PVA", "Canary Detect": "0AAcMZiTrK0txUk9PVA", "Sygma Private": "0AC_ioGo0GJ3tUk9PVA", "Ashcroft Family": "0ACX0xe254y5kUk9PVA", "One System": "0AGTfg0QwTS8kUk9PVA", "El Atico": "0AP-TBWWevTInUk9PVA",
          "Sygma Mala": "0ANYL9DOJQtmQUk9PVA", "Sygma Trainers": "0AP9_VgbvNGyEUk9PVA", "External Sygma Solutions": "0AOTm_FPU_iRmUk9PVA", "External Canary Detect": "0APjm9rgEA8PDUk9PVA",
          "Entities Private": "0APHr3b2NkrNNUk9PVA", "Passion Fit": "0AI3_VD66sPWyUk9PVA"}
DRIVES = list(SHARED.items()) + [("My Drive", None)]

def get_token(drive):
    r = cc("GET", f"drive_change_tokens?drive=eq.{urllib.parse.quote(drive)}&select=token")
    if r is None:
        # A FAILED read used to be indistinguishable from "no token yet", and the caller's response
        # to "no token yet" is to RE-BASELINE — which silently throws away every change since the
        # last good token. Raising instead means the drive is skipped and retried in 15 minutes with
        # its token intact.
        raise RuntimeError("could not read the change token — refusing to re-baseline off a failed read")
    return r[0]["token"] if r else None

def set_token(drive, t):
    if cc("POST", "drive_change_tokens?on_conflict=drive", [{"drive": drive, "token": t, "updated_at": "now()"}]) is None:
        raise RuntimeError("could not save the change token")

def start_token(did):
    p = {"supportsAllDrives": "true"}
    if did: p["driveId"] = did
    return gapi("/changes/startPageToken", p)["startPageToken"]

def load_folders(drive):
    """The drive's whole folder tree, paged 1000 at a time (PostgREST's cap).

    THE ROOT CAUSE of the recurring path drift, found 26 Jul 2026 by measuring instead of assuming.
    This used to page with `limit=1000&offset=N` and NO sort order. Postgres does not promise a
    stable order without ORDER BY, so offset paging re-delivered some rows and skipped others.
    Measured live on Sygma Hub: all 18,004 folder rows came back across 19 pages, but only 10,526
    were UNIQUE — 7,478 folders silently missing from the map, and a different subset each run.
    Adding the sort order returned 18,004 of 18,004.

    That is why this drift class kept coming back despite two earlier fixes (403 rows, then 158 in
    an afternoon, then 131 on 25 Jul): every previous fix treated the symptom in the path builder,
    while the map feeding it had been short on EVERY run.

    Now keyset-paged on drive_file_id (immune to rows shifting under a concurrent write, which
    offset paging is not), with a count check so a short map can never pass silently again. A
    failed page raises rather than reading as "end of data" — the drive is skipped and retried in
    15 minutes with its change token intact."""
    q = urllib.parse.quote(drive)
    want = cc("GET", f"drive_files?drive=eq.{q}&is_folder=eq.true&select=count")
    if want is None:
        raise RuntimeError("folder count FAILED — cannot tell a complete map from a short one")
    want = want[0]["count"]
    out = {}; last = ""
    while True:
        r = cc("GET", f"drive_files?drive=eq.{q}&is_folder=eq.true&select=drive_file_id,name,parent_id"
                      f"&order=drive_file_id.asc&limit=1000&drive_file_id=gt.{urllib.parse.quote(last)}")
        if r is None:
            raise RuntimeError("a folder-map page FAILED — a partial map would write truncated paths")
        for x in r: out[x["drive_file_id"]] = (x["name"], x["parent_id"])
        if len(r) < 1000: break
        last = r[-1]["drive_file_id"]
    if len(out) < want:
        # Usually transient (a folder deleted mid-read); self-heals next cycle. Never proceed on it.
        raise RuntimeError(f"folder map is SHORT: {len(out)} of {want} — refusing to build paths from it")
    return out

def path_of(fmap, fid, root):
    """Path of `fid` relative to its drive root, or None if the chain does not REACH that root.

    None is the whole point: it means WE DO NOT KNOW where this item is, so the caller must not
    write a path. The old version walked "while cur in fmap" and returned however far it got, which
    made a truncated path indistinguishable from a correct one. Now the root is the required anchor,
    so an incomplete map, a missing ancestor, a cycle, or an item with no ancestry at all can only
    ever produce None — never a plausible-looking wrong answer."""
    parts = []; cur = fid; seen = set()
    while True:
        if cur == root: return "/".join(reversed(parts))
        if cur is None or cur in seen or cur not in fmap: return None
        seen.add(cur); nm, par = fmap[cur]; parts.append(nm); cur = par

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
    # The anchor every path is measured from. A shared drive's root id IS its driveId; for My Drive
    # we ask Drive rather than hardcoding an id, so the check can never be quietly wrong.
    root = did or gapi("/files/root", {"fields": "id"})["id"]
    fmap = load_folders(drive)
    raw = []; newtok = None
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
            # Held, NOT saved yet. Saving it here meant the token advanced BEFORE the upserts and
            # deletes below were applied, so a failed write lost that window's changes permanently —
            # the feed never re-delivers them. It is now saved only once the work has landed.
            newtok = r["newStartPageToken"]; pt = None
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
        # Walk up fetching any parent still unknown (a folder created since the last full index), so
        # a brand-new file still gets its full path. Stop at the root — it is the anchor, not a gap.
        cur = par; guard = 0
        while cur and cur != root and cur not in fmap and guard < 50:
            got = fetch_folder(cur)
            if not got: break        # unresolvable; path_of() below returns None and we write nothing
            fmap[cur] = got; cur = got[1]; guard += 1
        isf = f.get("mimeType") == FOLDER
        # ONE authority on "do we know where this is". Previously a truncated walk produced a
        # plausible path that was then stored as fact — the recurring drift (18 Jul: 403 rows, then
        # 158 in an afternoon; 25 Jul: 131 more). A path we cannot anchor is not written at all.
        pp = path_of(fmap, par, root)
        if pp is None:
            # Not indexable: either the ancestry is genuinely outside this drive (Pete's 25 Jul rule,
            # enforced the same way in drive-files-index.py's anchored() — e.g. his files inside a
            # folder someone else shared with him) or we transiently could not resolve it. Either way
            # we know nothing to write, and writing a row with no path would recreate exactly the
            # orphan class the prune pass and the locator invariant were built to eliminate.
            _unresolved.append(f["name"]); continue
        fp = (pp + "/" + f["name"]) if pp else f["name"]
        if "_backups" in fp.split("/"): continue   # cold-backup folders are hidden from the file index (Pete, 2026-06-26)
        upserts.append({"drive_file_id": fid, "name": f["name"], "path": fp, "drive": drive, "entity": drive, "mime": "folder" if isf else f.get("mimeType"), "size": int(f["size"]) if f.get("size") else None, "modified_time": f.get("modifiedTime"), "is_folder": isf, "parent_id": par})
    # de-dup: a file both changed+removed in window -> delete wins
    delset = set(deletes)
    upserts = [u for u in upserts if u["drive_file_id"] not in delset]
    # Writes first, token second. A failed write now raises, so the token is NOT advanced and the
    # same window is simply re-delivered next run (every write here is idempotent).
    for i in range(0, len(upserts), 500):
        if cc("POST", "drive_files?on_conflict=drive_file_id", upserts[i:i + 500]) is None:
            raise RuntimeError("upsert FAILED — token not advanced, so this window retries next run")
    for fid in deletes:
        if cc("DELETE", f"drive_files?drive_file_id=eq.{fid}") is None:
            raise RuntimeError("delete FAILED — token not advanced, so this window retries next run")
    if newtok:
        set_token(drive, newtok)
    if _unresolved:
        print(f"  !! {len(_unresolved)} item(s) could not be anchored to the {drive} root — NOT indexed "
              f"rather than filed at a guessed path: {', '.join(_unresolved[:5])}"
              + (f" (+{len(_unresolved)-5} more)" if len(_unresolved) > 5 else "")
              + ". Expected for items whose ancestry sits outside this drive; if it is unexpected, "
                "run drive-files-index.py to reconcile.")
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
