#!/usr/bin/env python3
"""cc-project-api.py (B2) — the full "new project" build-out in one call.

Given an entity + name, it: creates the CC `projects` row, a default "General" bucket, the Drive
folder in the entity's correct drive (per the routing map), a seeded `vault_notes` knowledge home
(tagged with the slug so it links on the project page), and — optionally — a Gmail label. Every
step degrades gracefully: if Drive/Gmail aren't reachable the row + bucket + knowledge still land
and the missing links are reported. The CC "New project" button writes the same row+bucket; this is
the skill-side path so "we need a project for X" resolves to the same homes.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/cc-project-api.py "Project Name" --entity "Canary Detect" [--desc "..."] [--gmail] [--no-drive]

Reads: command-centre-supabase-keys.json (CC DB), drive-api.py / gmail-api.py (sibling helpers).
"""
import argparse, json, re, subprocess, sys, os
import urllib.request, urllib.parse, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
HR = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}

# entity → (drive name, top-level parent folder name) per the routing map.
ROUTING = {
    "Personal": ("My Drive", "Projects"),
    "One System": ("One System", "Projects"),
    "El Atico": ("El Atico", "Projects"),
    "Canary Detect": ("Canary Detect", "Projects"),
    "Sygma": ("Sygma Hub", "Projects"),
}

def rest(method, path, body=None, headers=None):
    h = dict(HR)
    if headers: h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, headers=h, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read() or "null")
    except urllib.error.HTTPError as e:
        return {"_error": f"{e.code} {e.read().decode()[:200]}"}

def slugify(name):
    # Convention (enforced by DB CHECK projects_slug_eq_name): slug == name, verbatim. No lowercasing —
    # that's what produced the `sy-cices-usmp` drift. Kept as a function so the call site is unchanged.
    return (name or "").strip() or "project"

def helper(script, *args):
    try:
        r = subprocess.run(["python3", f"{VAULT}/{script}", *args], capture_output=True, text=True, timeout=60,
                           env={**os.environ, "VAULT": VAULT})
        return (r.stdout or "") + (r.stderr or ""), r.returncode
    except Exception as e:
        return f"(helper {script} failed: {e})", 1

def find_projects_folder_id(drive, parent_name):
    # Look up the top-level "<parent_name>" folder id in <drive> from the drive_files index.
    rows = rest("GET", f"drive_files?drive=eq.{urllib.parse.quote(drive)}&name=eq.{urllib.parse.quote(parent_name)}&is_folder=eq.true&select=drive_file_id,path&limit=20")
    if isinstance(rows, list) and rows:
        # prefer the shallowest path (top-level Projects, not Archive/Projects)
        rows.sort(key=lambda r: len((r.get("path") or "").split("/")))
        return rows[0].get("drive_file_id")
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--entity", required=True)
    ap.add_argument("--desc", default="")
    ap.add_argument("--gmail", action="store_true", help="also create a Gmail label")
    ap.add_argument("--no-drive", action="store_true", help="skip Drive folder creation")
    a = ap.parse_args()

    slug = slugify(a.name)
    out = {"slug": slug, "name": a.name, "entity": a.entity, "links": {}}

    # 1) projects row + General bucket
    existing = rest("GET", f"projects?slug=eq.{slug}&select=slug")
    if isinstance(existing, list) and existing:
        print(json.dumps({"_error": f"project '{slug}' already exists"})); sys.exit(1)
    res = rest("POST", "projects", {"slug": slug, "name": a.name, "entity_slug": a.entity, "status": "active", "description": a.desc or None}, {"Prefer": "return=minimal"})
    if isinstance(res, dict) and "_error" in res:
        # projects_pkey backstop: 23505 here means a concurrent create won the race after the GET above.
        msg = f"project '{slug}' already exists" if "23505" in res["_error"] else res["_error"]
        print(json.dumps({"_error": msg})); sys.exit(1)
    rest("POST", "buckets", {"project_slug": slug, "name": "General", "sort_order": 0}, {"Prefer": "return=minimal"})
    out["bucket"] = "General"

    # 2) Drive folder in the entity's drive
    drive, parent_name = ROUTING.get(a.entity, ("Sygma Hub", "Projects"))
    if not a.no_drive:
        parent_id = find_projects_folder_id(drive, parent_name)
        if parent_id:
            res, rc = helper("drive-api.py", "create-folder", a.name, parent_id)
            fid = None
            m = re.search(r"\b([A-Za-z0-9_-]{25,})\b", res)
            if rc == 0 and m:
                fid = m.group(1)
                furl = f"https://drive.google.com/drive/folders/{fid}"
                rest("PATCH", f"projects?slug=eq.{slug}", {"drive": drive, "drive_folder_id": fid, "drive_folder_url": furl}, {"Prefer": "return=minimal"})
                out["links"]["drive"] = furl
            else:
                out["links"]["drive"] = f"(could not create under {drive}/{parent_name}: {res[:120]})"
        else:
            out["links"]["drive"] = f"(no '{parent_name}' folder found in {drive} — create the Drive folder manually)"

    # 3) seeded knowledge home in vault_notes (tagged with the slug → links on the project page)
    body = f"# {a.name}\n\nProject overview (auto-seeded by cc-project-api). Entity: {a.entity}.\n\n{a.desc}\n"
    kn = rest("POST", "vault_notes", {
        "slug": f"{slug}-overview", "vault_path": f"projects/{slug}/overview.md",
        "title": f"{a.name} — overview", "body": body, "type": "project", "tags": [slug],
    }, {"Prefer": "return=minimal"})
    out["links"]["knowledge"] = f"vault_notes:{slug}-overview" if not (isinstance(kn, dict) and kn.get("_error")) else f"(knowledge seed failed: {kn['_error'][:100]})"

    # 4) optional Gmail label
    if a.gmail:
        res, rc = helper("gmail-api.py", "create-label", f"{a.entity}/{slug}")
        out["links"]["gmail_label"] = f"{a.entity}/{slug}" if rc == 0 else f"(label create failed: {res[:100]})"

    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
