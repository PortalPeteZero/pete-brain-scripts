#!/usr/bin/env python3
"""
hub-audit.py -- Drift-detection between live Sygma Hub state and structural docs.

Walks Sygma Hub's top-levels + key second-level folders via Drive API, compares
against:
  - Hub `MAP.md` (top-level structure block + dedicated section names)
  - Vault `Library/processes/hub-content-index.md` (top-level table + Drive IDs)

Reports:
  - Top-level folders on Hub but not in MAP/index
  - Top-levels in MAP/index but not on Hub
  - Top-levels missing READMEs
  - Drive ID mismatches between MAP/index and live state
  - Course Mapping.xlsx duplicates

Manual run only -- no scheduled task. Reuses service-account auth from drive-api.py.

Usage:
  python3 hub-audit.py                   # full audit, exit 0 if clean / 1 if drift found
  python3 hub-audit.py --quiet           # only print drift (suppress all-clean output)
  python3 hub-audit.py --json            # emit findings as JSON
"""

import json, sys, os, re, urllib.request, urllib.parse, urllib.error, base64, time, subprocess, tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VAULT_ROOT = SCRIPT_DIR.parent.parent.parent
KEY_PATH = SCRIPT_DIR.parent / "secrets" / "google-seo-service-account.json"
IMPERSONATE = "pete.ashcroft@sygma-solutions.com"
SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"

HUB_DRIVE_ID = "0APzpyHHfvUyIUk9PVA"
HUB_MAP_ID = "1s2AgfL3SAw0gWCWtezFC2Ir43MBTWV-y"
HUB_RULES_ID = "1Kv2QJ6lUPLS33fMdIawsf9ZvkFtcIbCE"
HUB_README_ID = "1zLZbBFBS-G-W1yxMzoBYSEbaX2WYMOVB"
INDEX_VAULT_PATH = VAULT_ROOT / "Library" / "processes" / "hub-content-index.md"

EXPECTED_TOP_LEVELS = {
    "Accreditations", "Archive", "Course Records", "Courses",
    "Customer Specific Documentation", "Customers and Suppliers",
    "HR", "Library", "Marketing", "Media", "Reports", "Sales & Pipeline",
}

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


def list_children(folder_id):
    """One-level listing."""
    items = []
    page_token = None
    while True:
        params = {
            "pageSize": 200,
            "fields": "files(id,name,mimeType,size,modifiedTime),nextPageToken",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
            "q": f"'{folder_id}' in parents and trashed=false",
        }
        if page_token: params["pageToken"] = page_token
        url = DRIVE_BASE + "/files?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"})
        r = json.loads(urllib.request.urlopen(req).read())
        items.extend(r.get("files", []))
        page_token = r.get("nextPageToken")
        if not page_token: break
    return items


def fetch_file(file_id):
    url = f"{DRIVE_BASE}/files/{file_id}?alt=media&supportsAllDrives=true"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"})
    return urllib.request.urlopen(req).read().decode("utf-8", errors="replace")


def parse_map_top_levels(map_md):
    """Extract top-level folder names from the MAP top-level structure block.

    Walks the first ``` ``` fenced block after a 'Top-level structure' header;
    matches lines like '├── Foo/' or '└── Bar/'. Only counts names that are in
    EXPECTED_TOP_LEVELS (treats the audit as 'does the doc agree with the 12
    canonical top-levels?').
    """
    found = set()
    in_header = False
    in_fence = False
    for line in map_md.split("\n"):
        if "Top-level structure (" in line:
            in_header = True
            continue
        if in_header and not in_fence and line.strip().startswith("```"):
            in_fence = True
            continue
        if in_header and in_fence and line.strip().startswith("```"):
            break
        if in_header and in_fence:
            m = re.search(r"[├└]── ([A-Za-z][A-Za-z0-9 &/_.\-]*?)/", line)
            if m:
                name = m.group(1).strip()
                if name in EXPECTED_TOP_LEVELS:
                    found.add(name)
    return found


def parse_index_top_levels(index_md):
    """Extract top-level folders + Drive IDs from hub-content-index.md.

    Only parses the FIRST markdown table in the doc (the 'At a glance' table).
    Stops at the first blank line after the table starts. Restricts to names
    matching EXPECTED_TOP_LEVELS so the parser doesn't false-match on later
    sub-tables (Library subfolders).
    """
    found = {}
    in_table = False
    for line in index_md.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and "Hub top-level" in stripped:
            in_table = True
            continue
        if in_table and not stripped.startswith("|"):
            break
        if in_table:
            m = re.match(r"^\| ([A-Za-z][A-Za-z0-9 &/_.\-]*?)/ \| `([0-9A-Za-z_\-]+)` \|", stripped)
            if m:
                name = m.group(1).strip()
                if name in EXPECTED_TOP_LEVELS:
                    found[name] = m.group(2).strip()
    return found


def audit():
    findings = []

    # Live Hub state
    print("Fetching live Hub state...", file=sys.stderr)
    root_items = list_children(HUB_DRIVE_ID)
    live_dirs = {i["name"]: i["id"] for i in root_items if i["mimeType"] == "application/vnd.google-apps.folder"}
    live_files = [i for i in root_items if i["mimeType"] != "application/vnd.google-apps.folder"]

    # Expected top-levels check
    extra_on_hub = set(live_dirs) - EXPECTED_TOP_LEVELS
    missing_on_hub = EXPECTED_TOP_LEVELS - set(live_dirs)
    for n in sorted(extra_on_hub):
        findings.append({"severity": "info", "kind": "unexpected-top-level", "message": f"Hub has top-level '{n}' not in EXPECTED_TOP_LEVELS (this constant in hub-audit.py may need updating)"})
    for n in sorted(missing_on_hub):
        findings.append({"severity": "high", "kind": "missing-top-level", "message": f"Expected top-level '{n}' is NOT on Hub"})

    # Root files check
    expected_roots = {"HUB-RULES.md", "MAP.md", "README.md"}
    actual_roots = {f["name"] for f in live_files}
    for n in expected_roots - actual_roots:
        findings.append({"severity": "high", "kind": "missing-root-file", "message": f"Hub root missing '{n}'"})
    for n in actual_roots - expected_roots:
        findings.append({"severity": "info", "kind": "extra-root-file", "message": f"Hub root has unexpected file '{n}'"})

    # README presence in each top-level
    for tl_name, tl_id in sorted(live_dirs.items()):
        kids = list_children(tl_id)
        has_readme = any(k["name"].lower() == "readme.md" for k in kids)
        if not has_readme:
            findings.append({"severity": "high", "kind": "missing-readme", "message": f"Top-level '{tl_name}/' is missing README.md"})

    # MAP cross-check
    print("Reading MAP.md...", file=sys.stderr)
    map_md = fetch_file(HUB_MAP_ID)
    map_tops = parse_map_top_levels(map_md)
    if map_tops:
        for n in sorted(set(live_dirs) - map_tops):
            findings.append({"severity": "medium", "kind": "map-missing-top-level", "message": f"Top-level '{n}/' not listed in Hub MAP.md top-level structure block"})
        for n in sorted(map_tops - set(live_dirs)):
            findings.append({"severity": "medium", "kind": "map-stale-top-level", "message": f"Hub MAP.md lists '{n}/' which doesn't exist on live drive"})
    else:
        findings.append({"severity": "info", "kind": "map-parse-empty", "message": "Could not parse top-level block from MAP.md (regex didn't match anything -- format may have changed)"})

    # Vault hub-content-index cross-check
    if INDEX_VAULT_PATH.exists():
        print("Reading vault hub-content-index.md...", file=sys.stderr)
        index_md = INDEX_VAULT_PATH.read_text()
        index_entries = parse_index_top_levels(index_md)
        if index_entries:
            for n, expected_id in sorted(index_entries.items()):
                if n not in live_dirs:
                    findings.append({"severity": "medium", "kind": "index-stale-top-level", "message": f"vault hub-content-index lists '{n}/' which doesn't exist on Hub"})
                elif live_dirs[n] != expected_id:
                    findings.append({"severity": "high", "kind": "index-id-mismatch", "message": f"vault hub-content-index has '{n}/' with id `{expected_id}` but live id is `{live_dirs[n]}`"})
            for n in sorted(set(live_dirs) - set(index_entries)):
                findings.append({"severity": "medium", "kind": "index-missing-top-level", "message": f"Top-level '{n}/' not listed in vault hub-content-index"})
        else:
            findings.append({"severity": "info", "kind": "index-parse-empty", "message": "Could not parse top-level table from vault hub-content-index"})
    else:
        findings.append({"severity": "high", "kind": "index-missing", "message": f"Vault hub-content-index missing at {INDEX_VAULT_PATH}"})

    # Course Mapping.xlsx duplicate check (in Hub Courses/)
    courses_kids = list_children("1lVk2TtIRyGjJV5cNZMeTcj3gxAOCvani")
    course_mapping_files = [k for k in courses_kids if k["name"] == "Course Mapping.xlsx"]
    if len(course_mapping_files) > 1:
        findings.append({"severity": "medium", "kind": "duplicate-course-mapping",
                         "message": f"{len(course_mapping_files)} copies of Course Mapping.xlsx in Hub Courses/. IDs: {[f['id'] for f in course_mapping_files]}. Trash older ones."})
    elif len(course_mapping_files) == 0:
        findings.append({"severity": "high", "kind": "missing-course-mapping", "message": "Course Mapping.xlsx missing from Hub Courses/"})

    return findings


def main():
    quiet = "--quiet" in sys.argv
    as_json = "--json" in sys.argv

    findings = audit()

    if as_json:
        print(json.dumps(findings, indent=2))
        sys.exit(0 if not findings else 1)

    if not findings:
        if not quiet:
            print("\n✅ Hub audit clean -- live state matches structural docs.\n")
        sys.exit(0)

    by_sev = {"high": [], "medium": [], "info": []}
    for f in findings:
        by_sev[f.get("severity", "info")].append(f)

    print(f"\n=== Hub audit findings ({len(findings)}) ===\n")
    for sev in ["high", "medium", "info"]:
        items = by_sev[sev]
        if not items:
            continue
        print(f"-- {sev.upper()} ({len(items)}) --")
        for f in items:
            print(f"  [{f['kind']}]  {f['message']}")
        print()

    sys.exit(1 if by_sev["high"] or by_sev["medium"] else 0)


if __name__ == "__main__":
    main()
