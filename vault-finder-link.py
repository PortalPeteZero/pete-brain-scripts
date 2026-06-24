#!/usr/bin/env python3
"""
vault-finder-link.py -- Map an Asana project (and optional section) to a Finder file:/// URL
pointing at the matching vault folder.

Used by inbox-triage and asana-gmail-sync skills when adding a Finder link to task notes.

Usage (CLI):
  python3 vault-finder-link.py "CD-Website"
  python3 vault-finder-link.py "CD-Website" "seo"
  python3 vault-finder-link.py "SY-Clancy"            # exception: maps to Customers/, not Projects/
  python3 vault-finder-link.py --asana-gid 1213950769949807

Usage (library):
  from vault_finder_link import finder_url_for_asana
  url = finder_url_for_asana(project_name="CD-Website", section_name="seo")

Returns the file:/// URL string, or None if no matching vault folder is found.

VAULT_ROOT env var overrides the default scan path (useful when running outside Pete's Mac).
The emitted URL always uses /Users/peterashcroft/Second Brain/... (the host path) so links work
when opened on Pete's Mac, even if the script is running in a sandbox with a different mount path.
"""

import os, sys, urllib.parse, re
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

# Path used in emitted file:// URLs (Pete's Mac home — always)
HOST_VAULT_ROOT = VAULT
# Path used for filesystem reads (can be overridden in sandbox)
SCAN_VAULT_ROOT = os.environ.get("VAULT_ROOT", HOST_VAULT_ROOT)

# Customer-as-project exceptions (Asana project name → vault-relative path that ISN'T Projects/{name}/)
EXCEPTIONS = {
    "SY-Clancy": "Customers/SY-Clancy",
}


def vault_folder_for_project(project_name: str, section_name: str | None = None) -> str | None:
    """
    Returns the vault-relative folder path for a given Asana project + section, or None.
    """
    if not project_name:
        return None
    base = EXCEPTIONS.get(project_name, f"Projects/{project_name}")
    full = os.path.join(SCAN_VAULT_ROOT, base)
    if not os.path.isdir(full):
        return None
    if section_name:
        candidates = [
            section_name,
            section_name.lower(),
            section_name.replace(' ', '-'),
            section_name.replace(' ', '-').lower(),
            section_name.replace(' ', '_').lower(),
        ]
        for c in candidates:
            sub = os.path.join(full, c)
            if os.path.isdir(sub):
                return f"{base}/{c}"
    return base


def finder_url(vault_folder: str) -> str:
    """Build a file:/// URL from a vault-relative folder path. Always emits the host vault path."""
    abs_path = os.path.join(HOST_VAULT_ROOT, vault_folder)
    encoded = urllib.parse.quote(abs_path, safe='/')
    return f"file://{encoded}/"


def finder_url_for_asana(project_name: str | None = None, section_name: str | None = None, vault_folder: str | None = None) -> str | None:
    """
    Returns a file:/// URL pointing at the matching vault folder, or None.
    """
    if not vault_folder:
        vault_folder = vault_folder_for_project(project_name, section_name)
    if not vault_folder:
        return None
    return finder_url(vault_folder)


def lookup_by_asana_gid(gid: str) -> str | None:
    """Walk Projects/ + Customers/ READMEs for matching asana_gid: frontmatter."""
    for root_folder in ["Projects", "Customers"]:
        scan_path = os.path.join(SCAN_VAULT_ROOT, root_folder)
        if not os.path.isdir(scan_path):
            continue
        for entry in os.listdir(scan_path):
            readme = os.path.join(scan_path, entry, "README.md")
            if not os.path.isfile(readme):
                continue
            with open(readme) as f:
                fm_block = []
                in_fm = False
                for line in f:
                    if line.strip() == "---":
                        if in_fm:
                            break
                        in_fm = True
                        continue
                    if in_fm:
                        fm_block.append(line)
            content = "".join(fm_block)
            m = re.search(r'asana_gid:\s*"?(\d+)"?', content)
            if m and m.group(1) == gid:
                return finder_url(f"{root_folder}/{entry}")
    return None


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] == "--asana-gid":
        print(lookup_by_asana_gid(args[1]) or "")
        return
    project = args[0]
    section = args[1] if len(args) > 1 else None
    print(finder_url_for_asana(project_name=project, section_name=section) or "")


if __name__ == "__main__":
    main()