#!/usr/bin/env python3
"""drive-maps.py — write/maintain an auto-updating front-door MAP.md in every kept Drive root.

Pete's "every drive has a map for Claude" idea, rolled out consistently. For each kept drive
it writes a `MAP.md` at the root whose auto-index (top-level folders + key files) is refreshed
in place inside a marker block, so any hand-written prose above/below the markers is preserved.

Design (per the 5 Jul drive-cleanup audit):
- SKIPS **Sygma Hub** — its README.md/MAP.md are managed by hub-reconcile.py; don't fight it.
- Uses a DISTINCT filename `MAP.md` — never touches an existing README.md.
- Lists each drive LIVE via the Drive API, so it works for drives NOT in the CC index
  (Petes Photo Archive, Social Media, Sygma Backup's).
"""
# CRON-META
# what: Writes/refreshes a MAP.md front-door index in every kept Google shared drive + My Drive
# why: every drive carries an at-a-glance map for Claude (Pete's design); listed live so the un-indexed drives are covered too
# reads: Google Drive API (SA) — top-level listing of each kept drive
# writes: MAP.md at each kept drive root (marker-block auto-index; preserves hand-written prose)
# entity: command-centre
# schedule: 30 5 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import sys, os, json, urllib.request, urllib.parse
import importlib.util
# Load drive-api.py from THIS script's own directory — portable across local (/tmp/pbs)
# and Railway (repo checkout); never hard-code /tmp/pbs (doesn't exist on Railway).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_spec = importlib.util.spec_from_file_location("da", os.path.join(_HERE, "drive-api.py"))
da = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(da)

BASE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
START, END = "<!-- DRIVE-MAP:AUTO START -->", "<!-- DRIVE-MAP:AUTO END -->"

# Kept drives to map (Sygma Hub deliberately excluded — hub-reconcile owns its map).
KEPT = {
    "Ashcroft Family": "0ACX0xe254y5kUk9PVA",
    "Canary Detect": "0AAcMZiTrK0txUk9PVA",
    "El Atico": "0AP-TBWWevTInUk9PVA",
    "Entities Private": "0APHr3b2NkrNNUk9PVA",
    "External Canary Detect": "0APjm9rgEA8PDUk9PVA",
    "External Sygma Solutions": "0AOTm_FPU_iRmUk9PVA",
    "One System": "0AGTfg0QwTS8kUk9PVA",
    "Petes Photo Archive": "0AJmtF_vpgSjyUk9PVA",
    "Social Media": "0AGOXT77ETxbyUk9PVA",
    "Sygma Backup's": "0AHrvLcZvxmbDUk9PVA",
    "Sygma Mala": "0ANYL9DOJQtmQUk9PVA",
    "Sygma Private": "0AC_ioGo0GJ3tUk9PVA",
    "Sygma Trainers": "0AP9_VgbvNGyEUk9PVA",
    "My Drive": "root",
}

def _get(url, tok):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})))

def root_children(drive_id, tok):
    """Live top-level listing of a drive (or My Drive when drive_id=='root')."""
    q = f"'{drive_id}' in parents and trashed=false"
    params = {"q": q, "fields": "nextPageToken,files(name,mimeType)", "pageSize": "1000",
              "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
    if drive_id != "root":
        params.update({"corpora": "drive", "driveId": drive_id})
    else:
        params.update({"corpora": "user"})
    out, page = [], None
    while True:
        p = dict(params)
        if page: p["pageToken"] = page
        d = _get(BASE + "?" + urllib.parse.urlencode(p), tok)
        out += d.get("files", []); page = d.get("nextPageToken")
        if not page: break
    return out

def build_index(name, kids):
    folders = sorted([k["name"] for k in kids if k["mimeType"] == FOLDER], key=str.lower)
    files = sorted([k["name"] for k in kids if k["mimeType"] != FOLDER and k["name"] not in ("MAP.md",)], key=str.lower)
    lines = [START, f"_Top level of the **{name}** drive — auto-updated by `drive-maps.py`. To find anything not listed, search Drive directly._", ""]
    for f in folders: lines.append(f"- 📁 {f}")
    for f in files: lines.append(f"- 📄 {f}")
    lines.append(END)
    return "\n".join(lines)

def splice(existing, block):
    if START in existing and END in existing:
        pre = existing.split(START)[0]
        post = existing.split(END, 1)[1]
        return pre + block + post
    # No markers (or new file): standard template with the block appended.
    header = f"# MAP — {{name}}\n\nFront-door index for this drive. The list below is auto-maintained; add any hand-written notes above or below the markers and they'll be preserved.\n\n"
    return header + block + "\n"

def find_map(drive_id, tok):
    q = f"name='MAP.md' and '{drive_id}' in parents and trashed=false"
    params = {"q": q, "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
    if drive_id != "root":
        params.update({"corpora": "drive", "driveId": drive_id})
    else:
        params.update({"corpora": "user"})
    fs = _get(BASE + "?" + urllib.parse.urlencode(params), tok).get("files", [])
    return fs[0]["id"] if fs else None

def upload_map(drive_id, name, tok, existing_id, content):
    data = content.encode("utf-8")
    if existing_id:
        url = f"{UPLOAD}/{existing_id}?uploadType=media&supportsAllDrives=true"
        req = urllib.request.Request(url, data=data, method="PATCH",
              headers={"Authorization": f"Bearer {tok}", "Content-Type": "text/markdown"})
        urllib.request.urlopen(req); return "updated"
    meta = {"name": "MAP.md", "parents": [drive_id], "mimeType": "text/markdown"}
    boundary = "----driveMapBoundary"
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: text/markdown\r\n\r\n").encode() + data + f"\r\n--{boundary}--".encode()
    url = f"{UPLOAD}?uploadType=multipart&supportsAllDrives=true"
    req = urllib.request.Request(url, data=body, method="POST",
          headers={"Authorization": f"Bearer {tok}", "Content-Type": f"multipart/related; boundary={boundary}"})
    urllib.request.urlopen(req); return "created"

def main():
    ok = fail = 0
    for name, did in KEPT.items():
        tok = da.get_token()  # per-drive refresh — cached w/ TTL, keeps long/cron runs alive
        try:
            kids = root_children(did, tok)
            block = build_index(name, kids)
            existing_id = find_map(did, tok)
            existing = ""
            if existing_id:
                existing = urllib.request.urlopen(urllib.request.Request(
                    f"{BASE}/{existing_id}?alt=media&supportsAllDrives=true",
                    headers={"Authorization": f"Bearer {tok}"})).read().decode("utf-8", "ignore")
            content = splice(existing, block).replace("{name}", name)
            action = upload_map(did, name, tok, existing_id, content)
            print(f"  {name:26} MAP.md {action} ({len([k for k in kids if k['mimeType']==FOLDER])} folders)")
            ok += 1
        except Exception as e:
            print(f"  {name:26} FAILED: {e!r}")
            fail += 1
    print(f"drive-maps: {ok} ok, {fail} failed")
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
