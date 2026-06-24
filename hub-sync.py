#!/usr/bin/env python3
"""
hub-sync.py -- Selective sync between Sygma Hub shared drive and Pete's vault.

Pull-only by default. Push only on explicit command. Never auto-syncs.

Reads the registry at Library/processes/hub-sync-registry.md to know which Hub
folders mirror to which vault folders. Tracks sync state in
Library/processes/hub-sync-state.json (per-mapping last_pulled timestamp + per-file
md5 + Hub modifiedTime).

Reuses the service-account auth machinery from drive-api.py.

Usage:
  python3 hub-sync.py status                    # show all mappings + drift summary
  python3 hub-sync.py pull                      # pull everything in registry
  python3 hub-sync.py pull <vault-path>         # pull a specific mapping (substring match)
  python3 hub-sync.py push <vault-file-or-dir>  # push a specific file/folder back to Hub (explicit)
  python3 hub-sync.py registry                  # show parsed registry
  python3 hub-sync.py clean <vault-path>        # wipe a local mirror folder + reset its state (use after a bad pull)
"""

import json, os, sys, re, hashlib, time, urllib.request, urllib.parse, urllib.error, base64, subprocess, tempfile, shutil
from pathlib import Path

# Resolve vault root (this script lives at Library/processes/scripts/)
SCRIPT_DIR = Path(__file__).resolve().parent
VAULT_ROOT = SCRIPT_DIR.parent.parent.parent
REGISTRY_PATH = VAULT_ROOT / "Library" / "processes" / "hub-sync-registry.md"
STATE_PATH = VAULT_ROOT / "Library" / "processes" / "hub-sync-state.json"
KEY_PATH = SCRIPT_DIR.parent / "secrets" / "google-seo-service-account.json"
IMPERSONATE = "pete.ashcroft@sygma-solutions.com"
SCOPE = "https://www.googleapis.com/auth/drive"

DRIVE_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"

_token = None
_token_exp = 0


def get_token():
    global _token, _token_exp
    now = int(time.time())
    if _token and _token_exp > now + 60:
        return _token
    with open(KEY_PATH) as f:
        creds = json.load(f)

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
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as kf:
        kf.write(creds["private_key"])
        kf_name = kf.name
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", kf_name, "-binary"],
                         input=ts.encode(), capture_output=True).stdout
    os.unlink(kf_name)
    jwt = f"{ts}.{b64u(sig)}"
    req = urllib.request.Request("https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        }).encode())
    _token = json.loads(urllib.request.urlopen(req).read())["access_token"]
    _token_exp = now + 3600
    return _token


def api(method, path, params=None, body=None, base=DRIVE_BASE, raw=False):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
        else:
            data = body
    headers = {"Authorization": f"Bearer {get_token()}"}
    if data and not raw:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        if raw:
            return resp
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode()[:300]
        raise RuntimeError(f"HTTP {e.code}: {msg}")


# Drive MIME -> export/native handling
GOOGLE_MIMES = {
    "application/vnd.google-apps.document": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "application/vnd.google-apps.spreadsheet": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "application/vnd.google-apps.presentation": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "application/vnd.google-apps.drawing": ("png", "image/png"),
    "application/vnd.google-apps.script": ("json", "application/vnd.google-apps.script+json"),
}


def parse_registry():
    """Parse the markdown registry, return list of mappings."""
    text = REGISTRY_PATH.read_text()
    mappings = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("| # | Hub source path"):
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table:
            if not line.strip().startswith("|"):
                in_table = False
                continue
            # Parse row: | # | path | id | dest | notes |
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if len(parts) < 4 or not parts[0].isdigit():
                continue
            # Skip decommissioned mappings: struck-through (~~) rows or REMOVED notes
            # stay in the registry as history but are no longer active mappings.
            if "~~" in line or "REMOVED" in line:
                continue
            mappings.append({
                "num": int(parts[0]),
                "hub_path": parts[1].strip("`"),
                "hub_id": parts[2].strip("`"),
                "vault_path": parts[3].strip("`"),
                "notes": parts[4] if len(parts) > 4 else "",
            })
    return mappings


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"version": 1, "mappings": {}}


def save_state(state):
    state["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    STATE_PATH.write_text(json.dumps(state, indent=2))


def list_folder(folder_id):
    """List all files (recursively) in a Drive folder. Returns list of dicts with id/name/mimeType/modifiedTime/size/parents/path."""
    all_items = []

    def walk(fid, prefix):
        page_token = None
        while True:
            params = {
                "pageSize": 200,
                "fields": "files(id,name,mimeType,modifiedTime,size,parents,md5Checksum),nextPageToken",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "allDrives",
                "q": f"'{fid}' in parents and trashed=false",
            }
            if page_token:
                params["pageToken"] = page_token
            resp = api("GET", "/files", params)
            for f in resp.get("files", []):
                f["_path"] = f"{prefix}/{f['name']}" if prefix else f["name"]
                all_items.append(f)
                if f["mimeType"] == "application/vnd.google-apps.folder":
                    walk(f["id"], f["_path"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    walk(folder_id, "")
    return all_items


def safe_local_path(rel_path):
    """Sanitise a relative path for the local filesystem.

    Each segment is sanitised individually against bad chars; '/' separators are PRESERVED
    to keep the directory hierarchy. Drive filenames can contain ':' on macOS which OS X
    auto-replaces with '/' in display -- normalise to '_'.
    """
    segments = []
    for seg in rel_path.split("/"):
        # Replace anything that's a path separator IN the segment, plus null byte
        cleaned = re.sub(r"[\x00]", "_", seg)
        # macOS-display-flip: literal ':' in Drive name -> '_' on local
        cleaned = cleaned.replace(":", "_")
        segments.append(cleaned)
    return "/".join(segments)


def download_file(file_id, mime_type, dest_path):
    """Download a file. For native Google Docs, export to docx/xlsx/pptx; for others, alt=media."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if mime_type in GOOGLE_MIMES:
        ext, export_mime = GOOGLE_MIMES[mime_type]
        url = f"{DRIVE_BASE}/files/{file_id}/export?mimeType={urllib.parse.quote(export_mime)}"
        # Append extension if not already present
        if not str(dest_path).endswith(f".{ext}"):
            dest_path = Path(str(dest_path) + f".{ext}")
    else:
        url = f"{DRIVE_BASE}/files/{file_id}?alt=media&supportsAllDrives=true"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"})
    try:
        with urllib.request.urlopen(req) as resp:
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        return dest_path, True
    except urllib.error.HTTPError as e:
        return dest_path, False


def md5_of_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(64 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def cmd_registry():
    mappings = parse_registry()
    print(f"\n{len(mappings)} mappings:\n")
    for m in mappings:
        print(f"  [{m['num']:2d}] Hub: {m['hub_path']}")
        print(f"       Vault: {m['vault_path']}")
        print(f"       Hub ID: {m['hub_id']}")
        if m['notes']:
            print(f"       Notes: {m['notes'][:80]}")
        print()


def cmd_status():
    mappings = parse_registry()
    state = load_state()
    print(f"\n{len(mappings)} mappings in registry:\n")
    for m in mappings:
        st = state["mappings"].get(m["vault_path"], {})
        last = st.get("last_pulled", "(never)")
        files = st.get("file_count", 0)
        size = st.get("total_size_bytes", 0)
        size_h = f"{size / 1024 / 1024:.1f} MB" if size > 0 else "-"
        local_path = VAULT_ROOT / m["vault_path"]
        local_exists = "[EXISTS]" if local_path.exists() else "[MISSING]"
        print(f"  [{m['num']:2d}] {local_exists} {m['vault_path']}")
        print(f"       last_pulled: {last}  files: {files}  size: {size_h}")


def cmd_pull(filter_path=None):
    mappings = parse_registry()
    state = load_state()

    if filter_path:
        mappings = [m for m in mappings if filter_path in m["vault_path"] or filter_path in m["hub_path"]]
        if not mappings:
            print(f"No mapping matched '{filter_path}'")
            return
        print(f"Filtered to {len(mappings)} mapping(s)\n")

    for m in mappings:
        print(f"\n=== Pulling [{m['num']}] {m['hub_path']} -> {m['vault_path']} ===")
        local_root = VAULT_ROOT / m["vault_path"]
        try:
            items = list_folder(m["hub_id"])
        except RuntimeError as e:
            print(f"  ERR list_folder: {e}")
            continue

        files_only = [i for i in items if i["mimeType"] != "application/vnd.google-apps.folder"]
        folders = [i for i in items if i["mimeType"] == "application/vnd.google-apps.folder"]
        print(f"  {len(files_only)} files in {len(folders)} folders")

        # Create local folders
        for f in folders:
            (local_root / safe_local_path(f["_path"])).mkdir(parents=True, exist_ok=True)

        # Download files
        downloaded = 0
        skipped = 0
        errors = 0
        total_bytes = 0
        prev_files = state["mappings"].get(m["vault_path"], {}).get("files", {})
        new_files = {}

        for f in files_only:
            local_relpath = safe_local_path(f["_path"])
            local_path = local_root / local_relpath
            modified_time = f.get("modifiedTime", "")
            md5 = f.get("md5Checksum")

            # Skip if local exists and Hub modifiedTime hasn't changed since last pull
            prev = prev_files.get(f["id"])
            if prev and prev.get("modifiedTime") == modified_time and local_path.exists():
                skipped += 1
                if md5: total_bytes += int(f.get("size", 0))
                new_files[f["id"]] = prev
                continue

            saved_path, ok = download_file(f["id"], f["mimeType"], local_path)
            if ok:
                downloaded += 1
                if saved_path.exists():
                    total_bytes += saved_path.stat().st_size
                new_files[f["id"]] = {
                    "name": f["name"],
                    "path": str(saved_path.relative_to(local_root)),
                    "modifiedTime": modified_time,
                    "md5": md5,
                }
                # Save state every 10 files so partial pulls are recoverable
                if downloaded % 10 == 0:
                    state["mappings"][m["vault_path"]] = {
                        "hub_id": m["hub_id"],
                        "hub_path": m["hub_path"],
                        "last_pulled": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "file_count": len(new_files),
                        "total_size_bytes": total_bytes,
                        "files": new_files,
                        "in_progress": True,
                    }
                    save_state(state)
                    print(f"    ...{downloaded} downloaded, {skipped} skipped, {errors} errors (state saved)")
            else:
                errors += 1

        print(f"  Done: {downloaded} downloaded, {skipped} skipped, {errors} errors")

        # Update state (final, in_progress=False)
        state["mappings"][m["vault_path"]] = {
            "hub_id": m["hub_id"],
            "hub_path": m["hub_path"],
            "last_pulled": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "file_count": len(new_files),
            "total_size_bytes": total_bytes,
            "files": new_files,
            "in_progress": False,
        }
        save_state(state)


def cmd_clean(target_path):
    """Wipe a local mirror folder + reset state for it. Used after a bad pull."""
    mappings = parse_registry()
    target_mapping = None
    for m in mappings:
        if target_path in m["vault_path"] or target_path.rstrip("/") == m["vault_path"].rstrip("/"):
            target_mapping = m
            break
    if not target_mapping:
        print(f"No mapping matched '{target_path}'")
        return
    local_root = VAULT_ROOT / target_mapping["vault_path"]
    if local_root.exists():
        print(f"Wiping {local_root}...")
        shutil.rmtree(local_root)
        print(f"  done.")
    else:
        print(f"  {local_root} did not exist; nothing to wipe.")
    state = load_state()
    if target_mapping["vault_path"] in state.get("mappings", {}):
        del state["mappings"][target_mapping["vault_path"]]
        save_state(state)
        print(f"  Reset state entry.")


def cmd_push(target_path):
    """Push a single file or folder back to Hub. Explicit command, never automatic."""
    abs_path = (VAULT_ROOT / target_path).resolve() if not target_path.startswith("/") else Path(target_path)
    if not abs_path.exists():
        print(f"Not found: {abs_path}")
        return

    # Find which mapping this falls under
    rel = abs_path.relative_to(VAULT_ROOT)
    mappings = parse_registry()
    parent_mapping = None
    for m in mappings:
        if str(rel).startswith(m["vault_path"].rstrip("/")):
            parent_mapping = m
            break
    if not parent_mapping:
        print(f"Path '{rel}' is not under any sync mapping. Push only works for synced paths.")
        return

    state = load_state()
    mapping_state = state["mappings"].get(parent_mapping["vault_path"], {})
    files_state = mapping_state.get("files", {})

    print(f"\nPushing '{rel}' under mapping {parent_mapping['vault_path']} -> {parent_mapping['hub_path']}\n")
    print("Push not yet implemented in v0.1. Pull-only initial release.")
    print("To do this manually for now: use drive-api.py upload <local> <hub_folder_id> [name]")
    print(f"Hub folder ID for this mapping: {parent_mapping['hub_id']}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "registry":
        cmd_registry()
    elif cmd == "status":
        cmd_status()
    elif cmd == "pull":
        cmd_pull(arg)
    elif cmd == "clean":
        if not arg:
            print("Usage: hub-sync.py clean <vault-path>")
            sys.exit(1)
        cmd_clean(arg)
    elif cmd == "push":
        if not arg:
            print("Usage: hub-sync.py push <vault-path>")
            sys.exit(1)
        cmd_push(arg)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
