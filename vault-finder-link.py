#!/usr/bin/env python3
"""
vault-finder-link.py -- Map a project (and optional section) to its Google Drive folder URL.

Used by inbox-triage and email-task-sync skills when adding a working-folder link to task notes.

Usage (CLI):
  python3 vault-finder-link.py "CD-Website"
  python3 vault-finder-link.py "CD-Website" "seo"

Usage (library):
  from vault_finder_link import finder_url_for_asana
  url = finder_url_for_asana(project_name="CD-Website", section_name="seo")

Prints/returns the https://drive.google.com/drive/folders/... URL, or blank/None when the project
has no Drive folder registered (projects.drive_folder_url) -- callers omit the link in that case.

Rewritten 2026-07-03 (Item 7 of plan-pete-brain-scripts-local-vault-remediation-2026-07-02):
the old version walked the local vault (Projects/, Customers/) and emitted file:// URLs; that
filesystem was retired in the 24 Jun Business OS thin-client cutover. Lookups now hit the CC DB
(`projects` for the folder, `drive_files` for section subfolders). The old `--asana-gid` README
frontmatter walk was retired outright: it had zero live callers and its data source (vault README
frontmatter) no longer exists anywhere, so there is nothing to back an asana_gid column with.
"""

import json, os, sys, urllib.parse, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"


def _cc():
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        d = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
        url, key = d["url"], d["service_role_key"]
    return url.rstrip("/"), key


def _rest(path):
    base, key = _cc()
    req = urllib.request.Request(f"{base}/rest/v1/{path}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def vault_folder_for_project(project_name: str, section_name: str | None = None) -> str | None:
    """Returns the Drive folder URL for a project (deep-linking the section subfolder when it
    exists in the drive_files index), or None when the project has no registered Drive folder."""
    if not project_name:
        return None
    rows = _rest(f"projects?slug=eq.{urllib.parse.quote(project_name)}"
                 f"&select=slug,drive_folder_url,drive_folder_id&limit=1")
    if not rows or not rows[0].get("drive_folder_url"):
        return None
    folder_url, folder_id = rows[0]["drive_folder_url"], rows[0].get("drive_folder_id")
    if section_name and folder_id:
        subs = _rest(f"drive_files?parent_id=eq.{urllib.parse.quote(folder_id)}"
                     f"&is_folder=eq.true&select=name,drive_file_id")
        candidates = [
            section_name,
            section_name.lower(),
            section_name.replace(' ', '-'),
            section_name.replace(' ', '-').lower(),
            section_name.replace(' ', '_').lower(),
        ]
        for c in candidates:
            for s in subs:
                if (s.get("name") or "").lower() == c.lower() and s.get("drive_file_id"):
                    return f"https://drive.google.com/drive/folders/{s['drive_file_id']}"
    return folder_url


def finder_url_for_asana(project_name: str | None = None, section_name: str | None = None) -> str | None:
    """Kept name + signature for the two SKILL.md callers. Returns the Drive URL or None."""
    try:
        return vault_folder_for_project(project_name, section_name)
    except Exception:
        return None   # no keys / no network -> callers omit the link, same as no-match


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] == "--asana-gid":
        print("", flush=True)
        print("--asana-gid was retired 2026-07-03: the vault README frontmatter it searched no longer "
              "exists and nothing called it. Look the project up by slug instead.", file=sys.stderr)
        sys.exit(2)
    project = args[0]
    section = args[1] if len(args) > 1 else None
    print(finder_url_for_asana(project_name=project, section_name=section) or "")


if __name__ == "__main__":
    main()
