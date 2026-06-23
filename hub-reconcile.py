#!/usr/bin/env python3
"""
hub-reconcile.py -- keep Sygma Hub per-folder READMEs + the vault map current.

Whole-Hub, future-proof (walks the live Drive tree via the Changes API delta --
no hard-coded folder lists, so new/restructured folders are picked up
automatically). Auto-updates README auto-index blocks for every folder whose
contents changed, refreshes the live-structure block in hub-content-index.md,
and emails Pete a daily digest of what staff changed.

Modes:
  init    capture a startPageToken per Hub drive (baseline; processes nothing)
  run     pull the delta since last token, update READMEs + map, email digest
  status  print state

Auth: reuses drive-api.py's DWD service-account token (impersonates Pete).
Run from cron via Desktop Commander (nohup + log poll) -- not workspace bash.

Process doc: Library/processes/hub-maintenance.md
"""
# CRON-META
# what: Sygma Hub reconcile (per-folder READMEs + content index from the Drive Changes delta)
# why: keeps the Hub's auto-index READMEs + map current as staff add/move files; daily digest to Pete
# reads: Sygma Hub shared drive (Changes API delta), cron_state token
# writes: Hub folder READMEs (Drive) + hub-content-index.md (local, skipped on cloud) + Pete digest email + CC snapshot
# entity: sygma
# schedule: 30 17 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import importlib.util, json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))  # .../Second Brain
STATE_PATH = os.path.join(HERE, "..", "hub-reconcile-state.json")
HUB_INDEX = os.path.join(HERE, "..", "hub-content-index.md")

HUB_DRIVE_ID = "0APzpyHHfvUyIUk9PVA"           # Sygma Hub shared drive
DIGEST_TO = "pete.ashcroft@sygma-solutions.com"

AUTO_START = "<!-- HUB-INDEX:AUTO START (managed by hub-reconcile.py -- edit only via the markers) -->"
AUTO_END = "<!-- HUB-INDEX:AUTO END -->"
FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"

# --- auth via drive-api.py ---
_spec = importlib.util.spec_from_file_location("_drv", os.path.join(HERE, "drive-api.py"))
_drv = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_drv)

def _tok():
    return _drv.get_token()

def _req(method, url, body=None, ctype=None, raw=False):
    h = {"Authorization": f"Bearer {_tok()}"}
    data = None
    if body is not None:
        if ctype == "media" or raw:
            data = body if isinstance(body, bytes) else body.encode()
            h["Content-Type"] = ctype or "text/plain"
        else:
            data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req) as r:
        b = r.read()
        return b if raw else (json.loads(b) if b else {})

# --- state (durable in CC public.cron_state so the wiped cloud container keeps its Drive delta
#     tokens between runs; a lost token would re-scan the whole Hub or skip changes) ---
sys.path.insert(0, HERE)
from cron_state import get_state as _cs_get, set_state as _cs_set

def load_state():
    return _cs_get("hub-reconcile", "state", default={"tokens": {}, "last_run": None})

def save_state(s):
    _cs_set("hub-reconcile", "state", s)

# --- drive helpers ---
def start_token(drive_id):
    p = urllib.parse.urlencode({"driveId": drive_id, "supportsAllDrives": "true"})
    return _req("GET", f"{DRIVE}/changes/startPageToken?{p}")["startPageToken"]

def list_changes(drive_id, token):
    """Return (changes, new_start_token). Paginates."""
    changes, page = [], token
    new_start = token
    while True:
        p = urllib.parse.urlencode({
            "pageToken": page, "driveId": drive_id, "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true", "spaces": "drive",
            "includeRemoved": "true", "pageSize": "1000",
            "fields": "nextPageToken,newStartPageToken,changes(changeType,removed,fileId,"
                      "file(id,name,mimeType,trashed,parents,createdTime,modifiedTime,lastModifyingUser/displayName))",
        })
        r = _req("GET", f"{DRIVE}/changes?{p}")
        changes.extend(r.get("changes", []))
        if r.get("nextPageToken"):
            page = r["nextPageToken"]; continue
        new_start = r.get("newStartPageToken", page)
        break
    return changes, new_start

def children(folder_id):
    out, page = [], None
    while True:
        q = urllib.parse.quote(f"'{folder_id}' in parents and trashed=false")
        extra = f"&pageToken={page}" if page else ""
        url = (f"{DRIVE}/files?q={q}&fields=files(id,name,mimeType),nextPageToken"
               f"&pageSize=200&supportsAllDrives=true&includeItemsFromAllDrives=true{extra}")
        r = _req("GET", url)
        out.extend(r.get("files", []))
        page = r.get("nextPageToken")
        if not page:
            break
    return out

def get_meta(file_id, fields="id,name,mimeType,parents,trashed"):
    p = urllib.parse.urlencode({"fields": fields, "supportsAllDrives": "true"})
    return _req("GET", f"{DRIVE}/files/{file_id}?{p}")

def folder_name(folder_id):
    try:
        return get_meta(folder_id, "name").get("name", folder_id)
    except Exception:
        return folder_id

# --- README auto-block upsert ---
def render_block(folder_id):
    kids = sorted(children(folder_id), key=lambda f: (f["mimeType"] != FOLDER_MIME, f["name"].lower()))
    lines = [AUTO_START, f"_Contents (auto-updated {datetime.now():%Y-%m-%d}):_", ""]
    if not kids:
        lines.append("- _(empty)_")
    for f in kids:
        if f["name"] == "README.md":
            continue
        icon = "\U0001F4C1" if f["mimeType"] == FOLDER_MIME else "\U0001F4C4"
        lines.append(f"- {icon} {f['name']}")
    lines.append(AUTO_END)
    return "\n".join(lines)

def find_readme(folder_id):
    q = urllib.parse.quote(f"'{folder_id}' in parents and name='README.md' and trashed=false")
    url = f"{DRIVE}/files?q={q}&fields=files(id,name)&supportsAllDrives=true&includeItemsFromAllDrives=true"
    fs = _req("GET", url).get("files", [])
    return fs[0]["id"] if fs else None

def read_file(file_id):
    return _req("GET", f"{DRIVE}/files/{file_id}?alt=media&supportsAllDrives=true", raw=True).decode("utf-8", "replace")

def update_file_content(file_id, content):
    _req("PATCH", f"{UPLOAD}/files/{file_id}?uploadType=media&supportsAllDrives=true",
         body=content, ctype="text/markdown")

def create_readme(folder_id, content):
    # multipart create
    boundary = "----hubReconcile"
    meta = {"name": "README.md", "parents": [folder_id], "mimeType": "text/markdown"}
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: text/markdown\r\n\r\n"
            f"{content}\r\n--{boundary}--").encode()
    _req("POST", f"{UPLOAD}/files?uploadType=multipart&supportsAllDrives=true",
         body=body, ctype=f"multipart/related; boundary={boundary}", raw=True)

def upsert_readme(folder_id):
    block = render_block(folder_id)
    rid = find_readme(folder_id)
    if rid:
        cur = read_file(rid)
        if AUTO_START in cur and AUTO_END in cur:
            pre = cur.split(AUTO_START)[0]
            post = cur.split(AUTO_END, 1)[1]
            new = pre + block + post
        else:
            new = cur.rstrip() + "\n\n" + block + "\n"
        if new != cur:
            update_file_content(rid, new)
        return "updated"
    else:
        name = folder_name(folder_id)
        header = f"# {name}\n\n_Auto-indexed by hub-reconcile. See [[hub-maintenance]]._\n\n"
        create_readme(folder_id, header + block + "\n")
        return "created"

# --- hub-content-index change log ---
def append_index_log(summary_lines):
    if not os.path.exists(HUB_INDEX):
        return
    txt = open(HUB_INDEX).read()
    marker = "## Change log (auto -- hub-reconcile)"
    entry = f"\n### {datetime.now():%Y-%m-%d}\n" + "\n".join(summary_lines) + "\n"
    if marker in txt:
        txt = txt.replace(marker, marker + entry, 1)
    else:
        txt = txt.rstrip() + f"\n\n{marker}\n_Newest first. Daily delta of staff Hub changes._\n{entry}"
    open(HUB_INDEX, "w").write(txt)

# --- digest email ---
def send_digest(subject, html):
    try:
        gspec = importlib.util.spec_from_file_location("_gm", os.path.join(HERE, "gmail-api.py"))
        gm = importlib.util.module_from_spec(gspec); gspec.loader.exec_module(gm)
        gm.GmailAPI().send(DIGEST_TO, subject, html, html=True)
        return True
    except Exception as e:
        print("digest send failed:", e); return False

# --- modes ---
def do_init():
    s = load_state()
    s["tokens"][HUB_DRIVE_ID] = start_token(HUB_DRIVE_ID)
    s["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(s)
    print(f"init: baseline token captured for {HUB_DRIVE_ID}")

def do_run():
    s = load_state()
    token = s.get("tokens", {}).get(HUB_DRIVE_ID)
    if not token:
        print("no token -- running init first"); do_init(); return
    prev_run = s.get("last_run")  # window start -- a folder counts as NEW only if created after this
    changes, new_token = list_changes(HUB_DRIVE_ID, token)
    # collapse to latest state per fileId
    latest = {}
    for c in changes:
        if not c.get("fileId"):
            continue  # changeType "drive" entries (drive rename/theme) have no fileId -- not relevant
        latest[c["fileId"]] = c
    added, edited, moved, removed, new_folders, touched_folders = [], [], [], [], [], []
    readme_self, acl_only = [], []   # 2026-06-12 noise fix: self-refreshes + ACL/move-only surfacings tracked separately
    affected_folders = set()
    for fid, c in latest.items():
        f = c.get("file") or {}
        nm = f.get("name", fid)
        if c.get("removed") or f.get("trashed"):
            removed.append(nm); continue
        parents = f.get("parents") or []
        for p in parents:
            affected_folders.add(p)
        who = (f.get("lastModifyingUser") or {}).get("displayName", "?")
        rec = f"{nm}  ({who})"
        if f.get("mimeType") == FOLDER_MIME:
            ctime = f.get("createdTime")
            if ctime and (not prev_run or ctime > prev_run):
                new_folders.append(nm)          # genuinely created since the last run
            else:
                touched_folders.append(nm)      # existing folder surfaced by a move/share/structure change -- NOT new
        else:
            # --- 2026-06-12 noise fix (root cause of the 314/186-change digests) ---
            # (1) README.md files are owned + auto-refreshed by THIS script; yesterday's
            #     upserts surface in today's changes feed as edits. Count them separately,
            #     never in the human digest list.
            if nm == "README.md":
                readme_self.append(rec); continue
            # (2) A file surfaced by the changes feed whose content did NOT change in the
            #     window (modifiedTime <= window start) was an ACL / share / move-only
            #     bump (e.g. the 11 Jun HR limited-access sweep) -- not a content edit.
            mtime = f.get("modifiedTime")
            if mtime and prev_run and mtime <= prev_run:
                acl_only.append(rec); continue
            edited.append(rec)
    # upsert READMEs for affected folders
    readme_results = {}
    for folder in affected_folders:
        try:
            readme_results[folder] = upsert_readme(folder)
        except Exception as e:
            readme_results[folder] = f"ERR {e}"
    # log + digest
    today = f"{datetime.now():%Y-%m-%d}"
    log_lines = [
        f"- Files changed: {len(edited)} | New folders: {len(new_folders)} | Existing folders touched (move/share/structure): {len(touched_folders)} | Removed: {len(removed)} | READMEs touched: {len(readme_results)} | Noise excluded: {len(readme_self)} README self-refreshes + {len(acl_only)} ACL/move-only",
    ]
    if new_folders:
        log_lines.append("- **New folders (review for the map):** " + ", ".join(sorted(new_folders)))
    if removed:
        log_lines.append("- **Removed/trashed:** " + ", ".join(sorted(removed)[:30]))
    append_index_log(log_lines)
    # email
    def ul(items):
        return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>" if items else "<p>none</p>"
    html = (f"<h2>Sygma Hub daily reconcile - {today}</h2>"
            f"<p>{len(edited)} files changed, {len(new_folders)} new folders, "
            f"{len(touched_folders)} existing folders touched (moved/shared/restructured, not new), "
            f"{len(removed)} removed. {len(readme_results)} folder READMEs refreshed automatically.</p>"
            f"<h3>New folders (may need a line in the map)</h3>{ul(sorted(new_folders))}"
            f"<h3>Removed / trashed</h3>{ul(sorted(removed)[:50])}"
            f"<h3>Files added / edited</h3>{ul(sorted(edited)[:120])}"
            f"<p style='color:#888'>Noise excluded from this digest: {len(readme_self)} README auto-refreshes (this script's own writes) "
            f"+ {len(acl_only)} ACL/share/move-only surfacings (no content change).</p>"
            f"<p style='color:#888'>READMEs + hub-content-index updated automatically. "
            f"Protocol: Library/processes/hub-maintenance.md</p>")
    if edited or new_folders or removed:
        send_digest(f"Sygma Hub reconcile - {today} - {len(edited)} changes", html)
    else:
        print(f"no human changes since last run -- no digest sent "
              f"(excluded noise: {len(readme_self)} README self-refreshes, {len(acl_only)} ACL-only)")
    # Command Centre: publish the daily Hub-activity snapshot (additive; the digest email above is unchanged). Non-fatal.
    try:
        import importlib.util as _il
        _spec = _il.spec_from_file_location("cc_publish", os.path.join(HERE, "cc_publish.py"))
        _cc = _il.module_from_spec(_spec); _spec.loader.exec_module(_cc)
        _subj = f"Sygma Hub reconcile - {today} - {len(edited)} changes"
        ok = _cc.publish("hub-activity", today, {"subject": _subj, "html": html})
        print(f"  CC: hub-activity snapshot {'published' if ok else 'FAILED'} ({today})")
    except Exception as _e:
        print(f"  CC PUBLISH FAILED: {_e}")
    s["tokens"][HUB_DRIVE_ID] = new_token
    s["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(s)
    print(f"run complete: {len(latest)} changed items, {len(readme_results)} READMEs touched, token advanced")

def do_status():
    s = load_state()
    print(json.dumps(s, indent=2))

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"init": do_init, "run": do_run, "status": do_status}.get(mode, do_run)()
