#!/usr/bin/env python3
"""
drive-api.py -- Google Drive API helper
Auth: service account JWT + DWD (impersonates pete.ashcroft@sygma-solutions.com)
Scope: https://www.googleapis.com/auth/drive
Usage:
  python3 drive-api.py drives                          # list shared drives
  python3 drive-api.py ls [FOLDER_ID]                  # list files in folder (root if omitted)
  python3 drive-api.py search QUERY                    # full-text search across Drive
  python3 drive-api.py get FILE_ID /local/path         # download file
  python3 drive-api.py upload /local/file FOLDER_ID [NAME]  # upload file
  python3 drive-api.py create-folder NAME PARENT_ID    # create folder (shared-drive aware)
  python3 drive-api.py move FILE_ID DEST_FOLDER_ID     # move file
  python3 drive-api.py info FILE_ID                    # get file metadata
  python3 drive-api.py whoami                          # show auth info

  # Sygma Hub build extensions (added 2026-04-27):
  python3 drive-api.py copy SRC_ID DEST_FOLDER_ID [NEW_NAME]   # server-side file copy
  python3 drive-api.py create-shortcut NAME PARENT_ID TARGET_ID  # Drive shortcut to TARGET_ID
  python3 drive-api.py find-by-name NAME [PARENT_ID]   # exact-name search across all drives
  python3 drive-api.py set-props FILE_ID KEY=VAL [KEY=VAL...]  # set hidden appProperties
  python3 drive-api.py get-props FILE_ID               # read appProperties on a file
  python3 drive-api.py find-by-props KEY=VAL [KEY=VAL...]  # find files matching all KEY=VAL
  python3 drive-api.py info-modified FILE_ID           # slim metadata incl modifiedTime + appProperties
  python3 drive-api.py upload-text PATH FOLDER_ID NAME [--mime TYPE]  # upload from a string-content file
  python3 drive-api.py trash FILE_ID                   # send file/folder to trash (recoverable 30 days)
  python3 drive-api.py untrash FILE_ID                 # restore from trash
  python3 drive-api.py share FILE_ID EMAIL [role] [--notify] [--message TEXT]  # grant reader|commenter|writer (default writer, silent)
  python3 drive-api.py list-permissions FILE_ID        # list who has access
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys

KEY = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware on Railway (bootstrap materialises the key)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
IMPERSONATE = "pete.ashcroft@sygma-solutions.com"
SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"

with open(KEY) as f:
    creds = json.load(f)

_token_cache = {}

def get_token():
    now = int(time.time())
    if _token_cache.get("exp", 0) > now + 60:
        return _token_cache["tok"]
    def b64u(d):
        if isinstance(d, str): d = d.encode()
        return base64.urlsafe_b64encode(d).decode().rstrip("=")
    h = b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
    c = b64u(json.dumps({
        "iss": creds["client_email"], "sub": IMPERSONATE, "scope": SCOPE,
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600, "iat": now,
    }))
    ts = f"{h}.{c}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(creds["private_key"]); kf = f.name
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", kf, "-binary"],
                         input=ts.encode(), capture_output=True).stdout
    os.unlink(kf)
    jwt = f"{ts}.{b64u(sig)}"
    r = urllib.request.Request("https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        }).encode())
    tok = json.loads(urllib.request.urlopen(r).read())["access_token"]
    _token_cache["tok"] = tok
    _token_cache["exp"] = now + 3600
    return tok

def api(method, path, params=None, body=None, base=DRIVE_BASE):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {get_token()}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

def list_drives():
    resp = api("GET", "/drives", {"pageSize": 50})
    drives = resp.get("drives", [])
    if not drives:
        print("No shared drives found.")
        return
    print(f"{'ID':<35} {'Name'}")
    print("-" * 60)
    for d in drives:
        print(f"{d['id']:<35} {d['name']}")

def ls(folder_id=None):
    # Shared-drive-aware: works for My Drive folders AND shared drive root or sub-folders.
    # If folder_id is a shared-drive-root id (starts with 0A), use 'in parents' the same way.
    # Auto-paginates -- folders with >1000 children list completely without pageToken
    # juggling on the caller side. (Was a 100-item silent cap before 2 May 2026 -- bit
    # us when listing the April Tom-jobs folder which had 103 subfolders.)
    base_params = {
        "pageSize": 1000,
        "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,parents)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "corpora": "allDrives",
    }
    if folder_id:
        base_params["q"] = f"'{folder_id}' in parents and trashed=false"
    else:
        base_params["q"] = "'root' in parents and trashed=false"

    files = []
    page_token = None
    while True:
        params = dict(base_params)
        if page_token:
            params["pageToken"] = page_token
        resp = api("GET", "/files", params)
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not files:
        print("No files found.")
        return
    print(f"{'TYPE':<6} {'SIZE':>10}  {'MODIFIED':<20} {'ID':<35} {'Name'}")
    print("-" * 100)
    for f in files:
        mtype = "DIR" if "folder" in f.get("mimeType","") else "FILE"
        size = f.get("size", "-")
        if isinstance(size, str) and size.isdigit():
            size = f"{int(size):,}"
        mod = f.get("modifiedTime","")[:10]
        print(f"{mtype:<6} {size:>10}  {mod:<20} {f['id']:<35} {f['name']}")

def search(query):
    params = {
        "q": f"fullText contains '{query}' and trashed=false",
        "pageSize": 30,
        "fields": "files(id,name,mimeType,modifiedTime)",
    }
    resp = api("GET", "/files", params)
    files = resp.get("files", [])
    if not files:
        print(f"No results for: {query}")
        return
    print(f"Found {len(files)} result(s) for: {query}\n")
    for f in files:
        mtype = "DIR" if "folder" in f.get("mimeType","") else "FILE"
        print(f"  [{mtype}] {f['name']}")
        print(f"         ID: {f['id']}")
        print()

def get_file(file_id, local_path):
    # Get metadata first to determine export vs download.
    # supportsAllDrives=true is required for files inside shared drives.
    meta = api("GET", f"/files/{file_id}", {"fields": "name,mimeType", "supportsAllDrives": "true"})
    mime = meta.get("mimeType","")
    name = meta.get("name","file")
    print(f"Downloading: {name} ({mime})")

    # Google Docs types need export
    export_map = {
        "application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
        "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
        "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    }
    if mime in export_map:
        export_mime, ext = export_map[mime]
        url = f"{DRIVE_BASE}/files/{file_id}/export?mimeType={urllib.parse.quote(export_mime)}&supportsAllDrives=true"
        if not local_path.endswith(ext):
            local_path += ext
    else:
        url = f"{DRIVE_BASE}/files/{file_id}?alt=media&supportsAllDrives=true"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"})
    with urllib.request.urlopen(req) as r, open(local_path, "wb") as out:
        out.write(r.read())
    print(f"Saved to: {local_path}")

def upload_file(local_path, folder_id, name=None):
    if not os.path.exists(local_path):
        print(f"File not found: {local_path}", file=sys.stderr); sys.exit(1)
    name = name or os.path.basename(local_path)
    meta = {"name": name, "parents": [folder_id]}
    # Multipart upload
    boundary = "----DriveAPIBoundary"
    with open(local_path, "rb") as f:
        file_data = f.read()
    body = (
        f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode() +
        json.dumps(meta).encode() + b"\r\n" +
        f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode() +
        file_data + f"\r\n--{boundary}--".encode()
    )
    # supportsAllDrives=true is REQUIRED for shared-drive parents (otherwise HTTP 404).
    # No-op for My Drive parents.
    req = urllib.request.Request(
        UPLOAD_BASE + "/files?uploadType=multipart&supportsAllDrives=true&fields=id,name,parents",
        data=body,
        headers={
            "Authorization": f"Bearer {get_token()}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method="POST"
    )
    result = json.loads(urllib.request.urlopen(req).read())
    print(f"Uploaded: {result['name']} (ID: {result['id']})")
    return result

def create_folder(name, parent_id):
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    # supportsAllDrives=true makes this work for both My Drive AND shared drive parents.
    # No-op for My Drive callers; required for shared drives.
    params = {"supportsAllDrives": "true", "fields": "id,name,parents,driveId"}
    result = api("POST", "/files", params=params, body=body)
    print(f"Created folder: {result['name']} (ID: {result['id']})")
    return result

# -----------------------------------------------------------------------------
# Sygma Hub build extensions (added 2026-04-27)
# -----------------------------------------------------------------------------

def copy_file(src_id, dest_folder_id, new_name=None):
    """Server-side file copy via files.copy. No local download/upload roundtrip."""
    body = {"parents": [dest_folder_id]}
    if new_name:
        body["name"] = new_name
    params = {"supportsAllDrives": "true",
              "fields": "id,name,parents,mimeType,size,driveId"}
    result = api("POST", f"/files/{src_id}/copy", params=params, body=body)
    print(f"Copied: {result['name']} (ID: {result['id']})  → folder {dest_folder_id}")
    return result

def create_shortcut(name, parent_id, target_id):
    """Create a Drive shortcut named `name` in `parent_id` pointing at `target_id`."""
    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.shortcut",
        "parents": [parent_id],
        "shortcutDetails": {"targetId": target_id},
    }
    params = {"supportsAllDrives": "true",
              "fields": "id,name,parents,shortcutDetails,driveId"}
    result = api("POST", "/files", params=params, body=body)
    print(f"Created shortcut: {result['name']} (ID: {result['id']})  → {target_id}")
    return result

def find_by_name(name, parent_id=None):
    """Find files by exact name across all drives. Optionally scoped to a parent folder."""
    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{safe_name}' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    params = {
        "q": q,
        "pageSize": 100,
        "fields": "files(id,name,mimeType,size,modifiedTime,parents,driveId)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "corpora": "allDrives",
    }
    resp = api("GET", "/files", params=params)
    files = resp.get("files", [])
    if not files:
        print(f"No files found with exact name: {name}")
        return []
    print(f"Found {len(files)} match(es) for: {name}")
    print(f"{'ID':<35} {'SIZE':>10}  {'MOD':<11} {'DRIVE':<22} {'NAME'}")
    print("-" * 100)
    for f in files:
        size = f.get("size", "-")
        if isinstance(size, str) and size.isdigit():
            size = f"{int(size):,}"
        mod = f.get("modifiedTime", "")[:10]
        drv = f.get("driveId", "my-drive")
        print(f"{f['id']:<35} {str(size):>10}  {mod:<11} {drv:<22} {f['name']}")
    return files

def set_props(file_id, kvs):
    """Set/update appProperties (hidden per-app metadata) on a file.
    `kvs` is a dict; pass value=None to delete a single key. Uses PATCH.
    appProperties merge with existing — values not in kvs are preserved.
    """
    body = {"appProperties": {k: (v if v is not None else None) for k, v in kvs.items()}}
    params = {"supportsAllDrives": "true",
              "fields": "id,name,appProperties"}
    result = api("PATCH", f"/files/{file_id}", params=params, body=body)
    print(f"Updated appProperties on {result['name']} ({result['id']}):")
    for k, v in (result.get("appProperties") or {}).items():
        print(f"  {k} = {v}")
    return result

def get_props(file_id):
    """Read appProperties + name + id of a file."""
    params = {"supportsAllDrives": "true",
              "fields": "id,name,appProperties,modifiedTime,parents,driveId"}
    result = api("GET", f"/files/{file_id}", params=params)
    print(f"{result['name']} ({result['id']})")
    print(f"  modifiedTime: {result.get('modifiedTime', '')}")
    print(f"  driveId: {result.get('driveId', 'my-drive')}")
    print(f"  parents: {result.get('parents', [])}")
    props = result.get("appProperties") or {}
    if not props:
        print("  appProperties: (none)")
    else:
        print("  appProperties:")
        for k, v in props.items():
            print(f"    {k} = {v}")
    return result

def find_by_props(kvs, max_results=200):
    """Find files matching ALL provided appProperty key=value pairs.
    `kvs` is a dict of str->str. Returns list of matching file metadata dicts."""
    parts = []
    for k, v in kvs.items():
        sk = k.replace("\\", "\\\\").replace("'", "\\'")
        sv = v.replace("\\", "\\\\").replace("'", "\\'")
        parts.append(f"appProperties has {{ key='{sk}' and value='{sv}' }}")
    q = " and ".join(parts) + " and trashed = false"
    params = {
        "q": q,
        "pageSize": max_results,
        "fields": "files(id,name,mimeType,size,modifiedTime,parents,driveId,appProperties)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "corpora": "allDrives",
    }
    resp = api("GET", "/files", params=params)
    files = resp.get("files", [])
    print(f"Found {len(files)} file(s) matching {kvs}")
    for f in files:
        print(f"  {f['id']}  {f['name']}  (drive={f.get('driveId','my')})")
    return files

def info_modified(file_id):
    """Slim metadata incl modifiedTime + appProperties — used by stale-edit detector."""
    fields = "id,name,modifiedTime,createdTime,appProperties,mimeType,size,parents,driveId,trashed"
    params = {"supportsAllDrives": "true", "fields": fields}
    result = api("GET", f"/files/{file_id}", params=params)
    print(json.dumps(result, indent=2))
    return result

def trash_file(file_id):
    """Send file or folder to trash (recoverable for 30 days). Folders cascade to children."""
    body = {"trashed": True}
    params = {"supportsAllDrives": "true", "fields": "id,name,trashed"}
    result = api("PATCH", f"/files/{file_id}", params=params, body=body)
    print(f"Trashed: {result['name']} ({result['id']}) — recoverable for 30 days")
    return result

def untrash_file(file_id):
    """Restore a trashed file or folder."""
    body = {"trashed": False}
    params = {"supportsAllDrives": "true", "fields": "id,name,trashed"}
    result = api("PATCH", f"/files/{file_id}", params=params, body=body)
    print(f"Restored: {result['name']} ({result['id']})")
    return result

def upload_text(content_or_path, folder_id, name, mime_type="text/markdown"):
    """Upload a small text/markdown file. `content_or_path` is either a string of content
    OR a local path that starts with / (auto-detected). Used by Phase A skeleton script
    to upload the README/MAP/CLAUDE markdown files we author in vault."""
    # Auto-detect: if it looks like an absolute path AND exists on disk, read from disk;
    # otherwise treat as literal content string.
    if isinstance(content_or_path, str) and content_or_path.startswith("/") and os.path.exists(content_or_path):
        with open(content_or_path, "rb") as f:
            data = f.read()
    else:
        data = content_or_path.encode("utf-8") if isinstance(content_or_path, str) else content_or_path
    meta = {"name": name, "parents": [folder_id], "mimeType": mime_type}
    boundary = "----DriveAPIBoundaryTxt"
    body = (
        f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode() +
        json.dumps(meta).encode() + b"\r\n" +
        f"--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n".encode() +
        data + f"\r\n--{boundary}--".encode()
    )
    req = urllib.request.Request(
        UPLOAD_BASE + "/files?uploadType=multipart&supportsAllDrives=true",
        data=body,
        headers={
            "Authorization": f"Bearer {get_token()}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method="POST",
    )
    try:
        result = json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    print(f"Uploaded text: {result['name']} (ID: {result['id']})")
    return result

def rename_file(file_id, new_name):
    """Rename a file/folder in place. Drive links are by ID, so a rename never breaks a link or
    a recorded drive_folder_id — but the drive_files index shows the OLD path until the next
    drive-changes-watch run."""
    before = api("GET", f"/files/{file_id}", {"fields": "name,mimeType", "supportsAllDrives": "true"})
    old_name = before.get("name")
    result = api("PATCH", f"/files/{file_id}",
                 body={"name": new_name},
                 params={"fields": "id,name", "supportsAllDrives": "true"})
    print(f"Renamed: '{old_name}' → '{result['name']}' ({file_id})")
    if before.get("mimeType") == "application/vnd.google-apps.folder":
        # drive_files.path is a denormalised string built from the parent chain. Renaming a FOLDER
        # updates only that folder's own row on the next changes-watch; every descendant keeps the
        # OLD path forever, because the descendants themselves never changed so are never re-upserted.
        # Verified 18 Jul 2026: renaming SY-Portal-Development left 19 child rows on the stale path.
        print("\n  ⚠ FOLDER rename — descendants in drive_files keep the OLD path and will NOT self-heal.")
        print("    Repair the index now (replace <OLD>/<NEW> with the full paths):")
        print("      UPDATE drive_files SET path = '<NEW>' || substring(path from length('<OLD>') + 1)")
        print("      WHERE path LIKE '<OLD>%';")


def move_file(file_id, dest_folder_id):
    # Get current parents (supportsAllDrives required for shared-drive files).
    meta = api("GET", f"/files/{file_id}", {"fields": "parents,name", "supportsAllDrives": "true"})
    old_parents = ",".join(meta.get("parents", []))
    params = {
        "addParents": dest_folder_id,
        "removeParents": old_parents,
        "fields": "id,name,parents",
        "supportsAllDrives": "true",
    }
    result = api("PATCH", f"/files/{file_id}", params=params)
    print(f"Moved: {result['name']} → {dest_folder_id}")

def info(file_id):
    fields = "id,name,mimeType,size,createdTime,modifiedTime,parents,webViewLink,owners"
    meta = api("GET", f"/files/{file_id}", {"fields": fields, "supportsAllDrives": "true"})
    for k, v in meta.items():
        print(f"  {k}: {v}")

def share(file_id, email, role="writer", notify=False, message=None):
    """Grant a user permission on a file/folder. role: reader|commenter|writer.
    notify=False shares silently (no email) -- the covering email carries the link.
    Works for shared-drive files (supportsAllDrives). External-sharing must be
    allowed by the shared drive's settings for a third-party email to stick."""
    body = {"type": "user", "role": role, "emailAddress": email}
    params = {"supportsAllDrives": "true",
              "sendNotificationEmail": "true" if notify else "false",
              "fields": "id,role,emailAddress"}
    if notify and message:
        params["emailMessage"] = message
    r = api("POST", f"/files/{file_id}/permissions", params, body)
    print(f"Shared {file_id} with {email} as {role} (notify={notify})")
    return r

def list_permissions(file_id):
    r = api("GET", f"/files/{file_id}/permissions",
            {"supportsAllDrives": "true", "fields": "permissions(id,type,role,emailAddress,displayName)"})
    for p in r.get("permissions", []):
        print(f"  {p.get('role'):<10} {p.get('type'):<7} {p.get('emailAddress') or p.get('displayName','')}")
    return r

def whoami():
    about = api("GET", "/about", {"fields": "user,storageQuota"})
    print(f"Authenticated as: {about['user']['displayName']} ({about['user']['emailAddress']})")
    q = about.get("storageQuota", {})
    if q:
        used = int(q.get("usage", 0)) // 1024 // 1024
        total = int(q.get("limit", 0)) // 1024 // 1024
        print(f"Storage: {used}MB used of {total}MB")

def delete_drive(drive_id):
    """Permanently delete a Shared Drive (with its trashed contents). Requires organizer + domain admin.
    IRREVERSIBLE — only call on a drive you've confirmed empty of live content."""
    url = DRIVE_BASE + f"/drives/{drive_id}?useDomainAdminAccess=true&allowItemDeletion=true"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"}, method="DELETE")
    with urllib.request.urlopen(req) as r:
        code = r.status
    print(f"Deleted shared drive {drive_id} (HTTP {code})")
    return code


def _find_child(name, parent_id, folders_only=False):
    esc = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{esc}' and '{parent_id}' in parents and trashed=false"
    if folders_only:
        q += " and mimeType='application/vnd.google-apps.folder'"
    r = api("GET", "/files", {"q": q, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
                              "fields": "files(id,name,mimeType)"})
    fs = r.get("files", [])
    return fs[0]["id"] if fs else None


def ensure_path(drive_id, *parts):
    """Resolve nested folders under a shared-drive root (drive_id), creating any that are missing.
    Returns the leaf folder id. Use to target a Drive home from a script without hardcoding folder ids."""
    parent = drive_id
    for p in parts:
        fid = _find_child(p, parent, folders_only=True)
        if not fid:
            fid = create_folder(p, parent)["id"]
        parent = fid
    return parent


def upsert_file(local_path, folder_id, name=None):
    """Upload local_path into folder_id, REPLACING any existing same-named file (update content in place,
    so re-runs don't create duplicates). Falls back to a fresh upload if none exists."""
    if not os.path.exists(local_path):
        print(f"File not found: {local_path}", file=sys.stderr); sys.exit(1)
    name = name or os.path.basename(local_path)
    existing = _find_child(name, folder_id)
    if not existing:
        return upload_file(local_path, folder_id, name)
    with open(local_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(
        UPLOAD_BASE + f"/files/{existing}?uploadType=media&supportsAllDrives=true&fields=id,name",
        data=data, headers={"Authorization": f"Bearer {get_token()}", "Content-Type": "application/octet-stream"},
        method="PATCH")
    result = json.loads(urllib.request.urlopen(req).read())
    print(f"Updated: {result['name']} (ID: {result['id']})")
    return result


def create_drive(name):
    """Create a new Shared Drive named `name`. Idempotent: returns the existing drive if the name already
    matches (Drive lets you make same-named drives, so we guard). Needs the delegated user to be allowed to
    create shared drives (full auth/drive scope). drives.create requires a client-generated requestId."""
    import uuid
    for d in api("GET", "/drives", {"pageSize": 100}).get("drives", []):
        if d.get("name") == name:
            print(f"Shared drive already exists: {name} (ID: {d['id']})")
            return d
    result = api("POST", "/drives", {"requestId": str(uuid.uuid4())}, {"name": name})
    print(f"Created shared drive: {result.get('name')} (ID: {result.get('id')})")
    return result


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "drives":
        list_drives()
    elif cmd == "ls":
        ls(args[1] if len(args) > 1 else None)
    elif cmd == "search":
        if len(args) < 2: print("Usage: drive-api.py search QUERY"); sys.exit(1)
        search(args[1])
    elif cmd == "get":
        if len(args) < 3: print("Usage: drive-api.py get FILE_ID /local/path"); sys.exit(1)
        get_file(args[1], args[2])
    elif cmd == "upload":
        if len(args) < 3: print("Usage: drive-api.py upload /local/file FOLDER_ID [NAME]"); sys.exit(1)
        upload_file(args[1], args[2], args[3] if len(args) > 3 else None)
    elif cmd == "create-folder":
        if len(args) < 3: print("Usage: drive-api.py create-folder NAME PARENT_ID"); sys.exit(1)
        create_folder(args[1], args[2])
    elif cmd == "move":
        if len(args) < 3: print("Usage: drive-api.py move FILE_ID DEST_FOLDER_ID"); sys.exit(1)
        move_file(args[1], args[2])
    elif cmd == "rename":
        if len(args) < 3: print("Usage: drive-api.py rename FILE_ID NEW_NAME"); sys.exit(1)
        rename_file(args[1], args[2])
    elif cmd == "info":
        if len(args) < 2: print("Usage: drive-api.py info FILE_ID"); sys.exit(1)
        info(args[1])
    elif cmd == "whoami":
        whoami()
    elif cmd == "create-drive":
        if len(args) < 2: print("Usage: drive-api.py create-drive NAME"); sys.exit(1)
        create_drive(args[1])
    elif cmd == "upsert-file":
        if len(args) < 3: print("Usage: drive-api.py upsert-file /local/file FOLDER_ID [NAME]"); sys.exit(1)
        upsert_file(args[1], args[2], args[3] if len(args) > 3 else None)
    elif cmd == "delete-drive":
        if len(args) < 2: print("Usage: drive-api.py delete-drive DRIVE_ID"); sys.exit(1)
        delete_drive(args[1])
    # ----- Sygma Hub build extensions -----
    elif cmd == "copy":
        if len(args) < 3: print("Usage: drive-api.py copy SRC_ID DEST_FOLDER_ID [NEW_NAME]"); sys.exit(1)
        copy_file(args[1], args[2], args[3] if len(args) > 3 else None)
    elif cmd == "create-shortcut":
        if len(args) < 4: print("Usage: drive-api.py create-shortcut NAME PARENT_ID TARGET_ID"); sys.exit(1)
        create_shortcut(args[1], args[2], args[3])
    elif cmd == "find-by-name":
        if len(args) < 2: print("Usage: drive-api.py find-by-name NAME [PARENT_ID]"); sys.exit(1)
        find_by_name(args[1], args[2] if len(args) > 2 else None)
    elif cmd == "set-props":
        if len(args) < 3:
            print("Usage: drive-api.py set-props FILE_ID KEY=VAL [KEY=VAL...]"); sys.exit(1)
        kvs = {}
        for kv in args[2:]:
            if "=" not in kv:
                print(f"Bad KEY=VALUE: {kv}"); sys.exit(1)
            k, v = kv.split("=", 1)
            kvs[k] = v
        set_props(args[1], kvs)
    elif cmd == "get-props":
        if len(args) < 2: print("Usage: drive-api.py get-props FILE_ID"); sys.exit(1)
        get_props(args[1])
    elif cmd == "find-by-props":
        if len(args) < 2:
            print("Usage: drive-api.py find-by-props KEY=VAL [KEY=VAL...]"); sys.exit(1)
        kvs = {}
        for kv in args[1:]:
            if "=" not in kv:
                print(f"Bad KEY=VALUE: {kv}"); sys.exit(1)
            k, v = kv.split("=", 1)
            kvs[k] = v
        find_by_props(kvs)
    elif cmd == "info-modified":
        if len(args) < 2: print("Usage: drive-api.py info-modified FILE_ID"); sys.exit(1)
        info_modified(args[1])
    elif cmd == "upload-text":
        if len(args) < 4:
            print("Usage: drive-api.py upload-text PATH_OR_CONTENT FOLDER_ID NAME [MIME]"); sys.exit(1)
        mime = args[4] if len(args) > 4 else "text/markdown"
        upload_text(args[1], args[2], args[3], mime)
    elif cmd == "trash":
        if len(args) < 2: print("Usage: drive-api.py trash FILE_ID"); sys.exit(1)
        trash_file(args[1])
    elif cmd == "untrash":
        if len(args) < 2: print("Usage: drive-api.py untrash FILE_ID"); sys.exit(1)
        untrash_file(args[1])
    elif cmd == "share":
        if len(args) < 3: print("Usage: drive-api.py share FILE_ID EMAIL [role] [--notify] [--message TEXT]"); sys.exit(1)
        role = args[3] if len(args) > 3 and not args[3].startswith("--") else "writer"
        notify = "--notify" in args
        msg = None
        if "--message" in args:
            mi = args.index("--message")
            msg = args[mi + 1] if mi + 1 < len(args) else None
        share(args[1], args[2], role=role, notify=notify, message=msg)
    elif cmd == "list-permissions":
        if len(args) < 2: print("Usage: drive-api.py list-permissions FILE_ID"); sys.exit(1)
        list_permissions(args[1])
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
