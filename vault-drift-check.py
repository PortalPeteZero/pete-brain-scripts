VAULT = os.environ.get("VAULT", "/tmp/pbs")
#!/usr/bin/env python3
"""
vault-drift-check.py

[BUSINESS OS — RETIRING (2026-06-22, Part D).] This audits the OLD vault structure.
Superseded by the self-maintaining `drive_files` index (the `drive-changes-watch`
capture cron) + the derived MAP.md. The brain Resume no longer calls it. Do not
rely on its output; retire at H1. Ledger:
Projects/PA-Command-Centre/files/part-d-reference-repoint-ledger-2026-06-22.md

Walks the vault and the cron registry. Reports anything that doesn't match
its convention. Designed to run quarterly via the `vault-drift-check`
scheduled task. Emails Pete the report ONLY if non-zero issues are found.

Conventions checked:
1. Every active `Projects/{name}/` has README.md, files/, README has YAML frontmatter
   with type/status/prefix/slug/category.
2. Every `Customers/{name}/` and `Suppliers/{name}/` has non-empty README.md with
   gmail_label/gmail_url frontmatter.
3. Every active scheduled task in the cron registry (mcp__scheduled-tasks__list)
   has a vault recovery mirror at `Library/skills/scheduled/{taskId}/SKILL.md`,
   and the mirror's first 200 chars match the canonical first 200 chars (drift
   detection without full content compare).
4. Every `Library/processes/scripts/*.py` is referenced from at least one
   `*.md` config file in `Library/processes/`. Orphan scripts get flagged.
5. `hub-content-index.md` top-level count vs live Hub.
6. Vault<->Drive parity (added 2026-05-03): file counts in vault `Personal/family/`
   vs Drive `Pete & Mic / Ashcroft Family/`, and vault
   `Businesses/sygma-solutions/owner-private/` vs Drive
   `Pete & Mic / Sygma Solutions Private/`. Flags asymmetries (file present one
   side, missing the other) so manual reconciliation can happen. Does NOT
   auto-delete -- the sync helper is additive only by design.
7. `Personal/inbox/` lingerers (added 2026-05-03): files older than 7 days in
   Personal/inbox/ get flagged.
8. Top-level greeter READMEs (added 2026-05-03): every documented top-level
   section has a README.md.

Output: a markdown report. Stdout if no email recipient, otherwise sent via
gmail-api.py.
"""
import os
import re
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
from importlib.machinery import SourceFileLoader

# VAULT path auto-detect -- the script may run in three contexts:
#   1. Host (LaunchAgent / Desktop Commander)            -> /tmp/pbs
#   2. Cowork sandbox bash                               -> /sessions/{session}/mnt/Command Centre
#   3. Imported by another script that already has VAULT -> override via env VAULT_PATH
#
# Preferred invocation from a Cowork session is Desktop Commander (start_process)
# so the script runs on the host and the /sessions/ sandbox branch is bypassed
# entirely -- avoids stale-session permission-denied iteration. See CLAUDE.md
# "Scheduled task SKILL.md prompts must invoke scripts via Desktop Commander,
# not workspace bash" rule + 2026-05-13 fix.
def _safe_exists(p):
    """Path.exists() raises PermissionError for stale Cowork sandbox siblings
    where /sessions/{old}/mnt/ is no longer stat-able. Treat as does-not-exist."""
    try:
        return p.exists()
    except (PermissionError, OSError):
        return False


def _detect_vault():
    env_override = os.environ.get("VAULT_PATH")
    if env_override and _safe_exists(Path(env_override)):
        return Path(env_override)
    # Host path -- canonical. Check first so DC / LaunchAgent invocations skip
    # the /sessions/ iteration altogether.
    host = Path(VAULT)
    if _safe_exists(host):
        return host
    # Sandbox path -- /sessions/{session}/mnt/Command Centre (any session id).
    # Stale sessions can be present but unreadable -- skip rather than abort.
    sandbox_root = Path("/sessions")
    if _safe_exists(sandbox_root):
        try:
            entries = list(sandbox_root.iterdir())
        except (PermissionError, OSError):
            entries = []
        for session_dir in entries:
            candidate = session_dir / "mnt" / "Command Centre"
            if _safe_exists(candidate):
                return candidate
    raise SystemExit(
        "vault-drift-check: cannot locate vault. Tried VAULT_PATH env, "
        "/tmp/pbs, and /sessions/.../mnt/Command Centre. "
        "Set VAULT_PATH explicitly if running outside the standard contexts."
    )

VAULT = _detect_vault()

# === checks ===

def check_projects():
    issues = []
    projects_dir = VAULT / "Projects"
    if not projects_dir.is_dir():
        issues.append("Projects/ directory missing")
        return issues

    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name.startswith("_"):
            continue  # _archive
        readme = d / "README.md"
        files_dir = d / "files"

        if not readme.is_file():
            issues.append(f"Projects/{d.name}/ has no README.md")
            continue
        # Post 2026-05-06 restructure: a parent project with sub-project subfolders
        # (each sub-project = its own folder with its own README + files/) does NOT
        # need a files/ at the parent root. Detect this case: parent has at least one
        # subdir that is itself a sub-project (contains README.md AND files/).
        is_parent_with_subprojects = any(
            sub.is_dir()
            and not sub.name.startswith("_")
            and sub.name != "files"
            and (sub / "README.md").is_file()
            and (sub / "files").is_dir()
            for sub in d.iterdir()
        )
        if not files_dir.is_dir() and not is_parent_with_subprojects:
            issues.append(f"Projects/{d.name}/ has no files/ subfolder")

        # Frontmatter checks
        text = readme.read_text(errors="ignore")
        if not text.startswith("---\n"):
            issues.append(f"Projects/{d.name}/README.md has no YAML frontmatter")
            continue
        end_idx = text.find("\n---\n", 4)
        if end_idx == -1:
            issues.append(f"Projects/{d.name}/README.md frontmatter not terminated")
            continue
        fm = text[4:end_idx]
        for required in ["type", "status", "category"]:
            if not re.search(rf"^{required}:\s*\S", fm, re.MULTILINE):
                issues.append(f"Projects/{d.name}/README.md missing `{required}:` field")
    return issues


def check_customers_suppliers():
    issues = []
    for section in ["Customers", "Suppliers"]:
        section_dir = VAULT / section
        if not section_dir.is_dir():
            continue
        for d in sorted(section_dir.iterdir()):
            if not d.is_dir():
                continue
            readme = d / "README.md"
            if not readme.is_file():
                issues.append(f"{section}/{d.name}/ has no README.md")
                continue
            if readme.stat().st_size == 0:
                issues.append(f"{section}/{d.name}/README.md is empty (0 bytes)")
                continue
            text = readme.read_text(errors="ignore")
            if not text.startswith("---\n"):
                issues.append(f"{section}/{d.name}/README.md has no YAML frontmatter")
                continue
            end_idx = text.find("\n---\n", 4)
            if end_idx == -1:
                issues.append(f"{section}/{d.name}/README.md frontmatter not terminated")
                continue
            fm = text[4:end_idx]
            for required in ["gmail_label", "gmail_url"]:
                if not re.search(rf"^{required}:\s*\S", fm, re.MULTILINE):
                    issues.append(f"{section}/{d.name}/README.md missing `{required}:` field")
    return issues


def check_properties():
    issues = []
    props_dir = VAULT / "Properties"
    if not props_dir.is_dir():
        return issues
    for d in sorted(props_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name.startswith("_"):
            continue
        readme = d / "README.md"
        if not readme.is_file():
            issues.append(f"Properties/{d.name}/ has no README.md")
            continue
        text = readme.read_text(errors="ignore")
        if not text.startswith("---\n"):
            issues.append(f"Properties/{d.name}/README.md has no frontmatter")
            continue
        end_idx = text.find("\n---\n", 4)
        if end_idx == -1:
            continue
        fm = text[4:end_idx]
        if not re.search(r"^property_type:\s*\S", fm, re.MULTILINE):
            issues.append(f"Properties/{d.name}/README.md missing `property_type:` field")
    return issues


def check_scheduled_tasks():
    """Compare cron registry to vault recovery mirrors.

    Note: the canonical scheduled-task store lives at
    ~/Documents/Claude/Scheduled/ on the host. From the Cowork sandbox
    that path is not mounted -- Path.home() resolves to /sessions/{sid}.
    In that case, the check skips gracefully with a clear message
    (rather than false-positive). Invoke this script via Desktop
    Commander from Cowork for full coverage, OR run from the host
    directly. Per [[external-service-routing]].
    """
    issues = []
    registry_path = Path.home() / "Documents" / "Claude" / "Scheduled"
    mirror_dir = VAULT / "Library" / "skills" / "scheduled"

    if not registry_path.is_dir():
        # Distinguish sandbox-can't-reach (skip) from host-but-missing (real drift)
        is_sandbox = str(Path.home()).startswith("/sessions/")
        if is_sandbox:
            issues.append(
                f"Scheduled-task lockstep check SKIPPED: running in Cowork sandbox "
                f"(Path.home()={Path.home()}). Invoke via Desktop Commander "
                f"(mcp__Desktop_Commander__start_process with `python3 \"{VAULT}/Library/processes/scripts/vault-drift-check.py\"`) "
                f"to verify scheduled-task lockstep, OR run from host directly. "
                f"Per [[external-service-routing]]: filesystem-shape host-path "
                f"checks must use Desktop Commander or a helper API, not the sandbox."
            )
            return issues
        issues.append(f"Cron registry path {registry_path} not accessible")
        return issues

    canonical_tasks = {}
    for task_dir in registry_path.iterdir():
        if not task_dir.is_dir():
            continue
        skill_md = task_dir / "SKILL.md"
        if skill_md.is_file():
            canonical_tasks[task_dir.name] = skill_md

    for task_id, canonical in canonical_tasks.items():
        mirror = mirror_dir / task_id / "SKILL.md"
        if not mirror.is_file():
            issues.append(f"Scheduled task `{task_id}` has no vault recovery mirror at {mirror.relative_to(VAULT)}")
            continue
        # Compare first 500 bytes; full content compare is too noisy
        c_head = canonical.read_bytes()[:500]
        m_head = mirror.read_bytes()[:500]
        if c_head != m_head:
            c_size = canonical.stat().st_size
            m_size = mirror.stat().st_size
            if c_size != m_size:
                issues.append(
                    f"Scheduled task `{task_id}` mirror drift: canonical {c_size}B vs mirror {m_size}B (rebuild from canonical)"
                )
    return issues


def check_orphan_scripts():
    """Library/processes/scripts/*.py should be referenced from at least one *.md in Library/processes/."""
    issues = []
    scripts_dir = VAULT / "Library" / "processes" / "scripts"
    processes_dir = VAULT / "Library" / "processes"

    if not scripts_dir.is_dir():
        return issues

    # Read all process md files
    md_files = list(processes_dir.glob("*.md"))
    md_blob = "\n".join(f.read_text(errors="ignore") for f in md_files)

    for script in sorted(scripts_dir.iterdir()):
        if script.suffix != ".py":
            continue
        if script.name in md_blob:
            continue
        issues.append(f"Orphan script: Library/processes/scripts/{script.name} not referenced in any Library/processes/*.md")
    return issues


def check_skills_archives():
    """For each Library/skills/{name}/SKILL.md, the matching .skill archive must exist and be newer."""
    issues = []
    skills_dir = VAULT / "Library" / "skills"
    if not skills_dir.is_dir():
        return issues
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name in ("_previous", "scheduled"):
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.is_file():
            continue
        archive = skills_dir / f"{d.name}.skill"
        if not archive.is_file():
            issues.append(f"Skill `{d.name}` has SKILL.md but no `{d.name}.skill` archive")
            continue
        if skill_md.stat().st_mtime > archive.stat().st_mtime + 60:
            issues.append(f"Skill `{d.name}` SKILL.md is newer than archive (archive needs rebuild)")
    return issues


# === added 2026-05-03 ===

JUNK_NAMES = {".DS_Store", "Icon\r", "Icon", ".Spotlight-V100", ".fseventsd", ".Trashes", ".TemporaryItems", ".VolumeIcon.icns", ".tmp.drivedownload"}


# Drive Shared Drive folder IDs for vault<->Drive parity. These are the
# Pete & Mic shared-drive folders. Folder IDs sourced from drive-api.py
# `find-by-name "X" 0AJi-EpCq7c0wUk9PVA`.
DRIVE_PAIR_FOLDER_IDS = {
    "ashcroft_family": "1O9FV7vxRvdr7NdDNM-3-M597kDXmnqb3",
    "sygma_solutions_private": "1kiwQKiOr8LE7Jpwln0OyhVK-2W8AGcDs",
}


def _all_relpaths(root: Path) -> set:
    """Return relative paths of all files under root, excluding macOS junk."""
    if not root.exists():
        return set()
    out = set()
    for p in root.rglob("*"):
        if p.is_file() and p.name not in JUNK_NAMES and not p.name.startswith("._"):
            out.add(str(p.relative_to(root)))
    return out


def _drive_count_recursive(drv_module, folder_id, _seen=None, _depth=0):
    """Count files (not folders) recursively under a Drive folder via drive-api.

    Uses drive-api.py's `api()` helper -- the same path drive-api.py itself uses.
    Shared-drive aware via supportsAllDrives/includeItemsFromAllDrives/corpora.
    Pagination handled. Depth cap = 50 (protects against rogue cycles).
    """
    if _seen is None:
        _seen = set()
    if folder_id in _seen or _depth > 50:
        return 0
    _seen.add(folder_id)

    count = 0
    page_token = None
    while True:
        params = {
            "pageSize": 1000,
            "fields": "nextPageToken,files(id,name,mimeType)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
            "q": f"'{folder_id}' in parents and trashed=false",
        }
        if page_token:
            params["pageToken"] = page_token
        resp = drv_module.api("GET", "/files", params)
        for f in resp.get("files", []):
            mt = f.get("mimeType", "")
            if "folder" in mt:
                count += _drive_count_recursive(drv_module, f["id"], _seen, _depth + 1)
            else:
                count += 1
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return count


def check_vault_drive_parity():
    """Flag count mismatches between vault and Drive Shared Drive folders.

    Uses drive-api.py to enumerate Drive recursively via API (NOT the local
    Drive Desktop cache). Filesystem reads of ~/Library/CloudStorage/...
    would fail silently when run from the Cowork sandbox and false-flag
    the sync as broken. Per [[Library/processes/external-service-routing]]
    and [[Library/lessons/2026-05-16-helper-first-external-service-discipline]].

    Note: vault uses lowercase / kebab-case / flattened folder names
    (austin/, cars/), Drive uses TitleCase With Spaces and may have
    intermediate folders (Family Members/Austin/, Vehicles/). Paths are
    NOT 1:1 comparable. Counts are the practical parity signal -- if
    counts differ by more than ±5, sync hasn't fully propagated.
    """
    issues = []

    # NOTE (2026-06-03): the sygma-solutions owner-private pair was REMOVED here.
    # Owner-private is now Drive-direct (no vault mirror) — the vault holds only a
    # pointer README. Comparing vault (1 file) vs Drive (~380 files) would flag a
    # ~379-file false drift every run. Only Personal/family remains a 2-way mirror.
    # See [[vault-drive-sync]] History 2026-06-03 + the removed pair in vault-drive-sync.py.
    pairs = [
        {
            "label": "Personal/family <-> Pete & Mic/Ashcroft Family",
            "vault": VAULT / "Personal/family",
            "drive_folder_id": DRIVE_PAIR_FOLDER_IDS["ashcroft_family"],
        },
    ]

    # Load drive-api.py as a module via SourceFileLoader (same pattern as
    # the gmail-api.py loader at the bottom of main()).
    try:
        drv = SourceFileLoader(
            "drive_api",
            str(VAULT / "Library/processes/scripts/drive-api.py"),
        ).load_module()
    except Exception as e:
        return [
            f"Vault<->Drive parity check skipped: could not load drive-api.py ({e}). "
            f"Per [[external-service-routing]] this check uses the API, not the filesystem cache."
        ]

    for pair in pairs:
        vault_count = len(_all_relpaths(pair["vault"]))
        try:
            drive_count = _drive_count_recursive(drv, pair["drive_folder_id"])
        except Exception as e:
            issues.append(
                f"{pair['label']}: Drive API count failed ({e}). "
                f"Helper required: drive-api.py. See [[external-service-routing]]."
            )
            continue
        delta = abs(vault_count - drive_count)
        if delta > 5:
            side = "more in vault" if vault_count > drive_count else "more on Drive"
            issues.append(
                f"{pair['label']}: count mismatch (vault={vault_count} drive={drive_count}, {side}). "
                f"Investigate via drive-api.py recursive listing; run vault-drive-sync.py to reconcile."
            )
    return issues


def check_inbox_lingerers():
    """Flag files in Personal/inbox/ older than 7 days.

    Skips macOS junk and files inside Drive Desktop sync scratch directories
    (`.tmp.drivedownload`, `.tmp.driveupload`) which are transient and self-cleaning.

    Also skips reference-library content that lives under Personal/inbox/ but isn't
    inbox-shape (not pending triage): the Flipper Zero IR-code database (~6,200 .ir
    files in `Flipper-IRDB-main/`) is a cloned GitHub repo, not stuff to triage.
    Extend `reference_library_dirs` and `reference_library_extensions` if other
    similar repos land here.
    """
    import time
    issues = []
    inbox = VAULT / "Personal/inbox"
    if not inbox.is_dir():
        return issues
    threshold = time.time() - (7 * 24 * 3600)
    drive_sync_scratch_dirs = {".tmp.drivedownload", ".tmp.driveupload"}
    # Reference-library carve-outs: not inbox content, not pending triage.
    reference_library_dirs = {"Flipper-IRDB-main"}
    reference_library_extensions = {".ir"}  # Flipper IR codes (defensive — also covered by the dir above)
    old_files = []
    for f in inbox.rglob("*"):
        if not f.is_file():
            continue
        if f.name in JUNK_NAMES or f.name.startswith(".") or f.name == "README.md":
            continue
        # Skip anything inside Drive Desktop sync-scratch dirs
        if any(p.name in drive_sync_scratch_dirs for p in f.parents):
            continue
        # Skip reference-library content (cloned repos / device databases, not inbox triage)
        if any(p.name in reference_library_dirs for p in f.parents):
            continue
        if f.suffix.lower() in reference_library_extensions:
            continue
        if f.stat().st_mtime < threshold:
            old_files.append(str(f.relative_to(VAULT)))
    if old_files:
        issues.append(
            f"Personal/inbox/ has {len(old_files)} file(s) older than 7 days needing triage. "
            f"First few: {old_files[:5]}"
        )
    return issues


def check_map_drift():
    """Walk key folders and flag any folder/file not mentioned in MAP.md.

    Catches out-of-session additions (Pete adds a file in Obsidian, Michaela
    uploads via Drive, sync pulls into Personal/inbox/, Drive Desktop downloads
    new content). MAP.md only auto-updates when a session updates it; this
    closes the gap.

    Future-proof: walks both folders AND files in tracked locations. New folder
    types (e.g. a future Personal/civic-roles/ if Pete adds one) will be caught
    automatically because the walk is by directory, not by hard-coded list.
    """
    issues = []
    map_path = VAULT / "MAP.md"
    if not map_path.is_file():
        issues.append("MAP.md missing entirely")
        return issues
    map_content = map_path.read_text()

    def in_map(name: str) -> bool:
        # Liberal: a folder/file is "in MAP" if its name appears anywhere
        # in MAP.md as a backtick path or wikilink. Avoids false positives
        # on common words by requiring slash or backtick context.
        candidates = [
            f"`{name}`", f"`{name}/`",
            f"[[{name}]]",
            f"/{name}/", f"/{name}.md",
        ]
        return any(c in map_content for c in candidates)

    # Sections where MAP indexes EACH child (folders + key files)
    folder_walk_sections = [
        "Projects",       # each project folder enumerated
        "Properties",     # each property
        "Customers",      # each customer
        "Suppliers",      # each supplier
        "Accreditations", # each body
        "Businesses",     # each trading entity
        "Personal",       # each personal area
    ]

    # Sections where MAP indexes EACH .md file (individual reference docs)
    file_walk_sections = [
        "Library/processes",  # each process / API config / script doc
        "Library/templates",  # each README template
        "Library/skills",     # each skill folder
    ]

    # Sections MAP describes as a folder summary (don't enumerate every file inside)
    # These are intentionally skipped: Library/lessons/, Library/audits/,
    # Library/decisions/, Library/competitors/, Library/market/, Library/sy-*/

    skip_names = {"_archive", "_previous", ".obsidian", ".trash", "node_modules", "scripts", "secrets"}

    # Walk folders in folder_walk_sections (only direct child FOLDERS, not files)
    for section in folder_walk_sections:
        section_path = VAULT / section
        if not section_path.is_dir():
            continue
        for child in sorted(section_path.iterdir()):
            if not child.is_dir():
                continue  # files inside (like README.md) are not enumerated separately
            if child.name.startswith(".") or child.name.startswith("_") or child.name in skip_names:
                continue
            if child.name in JUNK_NAMES:
                continue
            if not in_map(child.name):
                rel = child.relative_to(VAULT)
                issues.append(f"`{rel}/` (folder) not in MAP.md")

    # Walk files in file_walk_sections (only direct child .md FILES at top level)
    for section in file_walk_sections:
        section_path = VAULT / section
        if not section_path.is_dir():
            continue
        for child in sorted(section_path.iterdir()):
            if child.name.startswith(".") or child.name.startswith("_") or child.name in skip_names:
                continue
            if child.is_dir():
                # For Library/skills/, the FOLDER is what should be in MAP
                if section == "Library/skills" and not in_map(child.name):
                    rel = child.relative_to(VAULT)
                    issues.append(f"`{rel}/` (skill folder) not in MAP.md")
                continue
            if not child.name.endswith(".md"):
                continue
            if child.name in JUNK_NAMES:
                continue
            if not in_map(child.name) and not in_map(child.stem):
                rel = child.relative_to(VAULT)
                issues.append(f"`{rel}` (file) not in MAP.md")

    return issues[:50]  # cap noise; if there are 50+, drift is bigger than MAP can solve in one pass


def check_helper_script_discipline():
    """Enforce the helper-first external-service rule.

    Three sub-checks:
    1. Orphan helpers -- `*-api.py` / `*-api.sh` helpers that no skill, process
       doc, scheduled task, or lesson references. Surfaces deployment gaps.
    2. Connector-supersession violations -- code using Zapier-Google patterns
       (`mcp__5b85914c-*__google_*`) when a Google API helper exists.
    3. Filesystem-shape Drive reads -- code reading `/Users/.../Library/CloudStorage/`
       directly (the local Drive Desktop cache) instead of going through
       drive-api.py.

    See [[Library/processes/external-service-routing]] for the rule.
    """
    issues = []
    scripts_dir = VAULT / "Library" / "processes" / "scripts"
    if not scripts_dir.is_dir():
        return issues

    # Files that legitimately mention these anti-patterns as documentation /
    # anti-examples (the routing doc, the canonical lesson, the older lessons
    # that motivated the rule). Excluded from violation detection.
    discipline_doc_names = {
        "external-service-routing.md",
        "2026-05-16-helper-first-external-service-discipline.md",
        "feedback_drive_docs_via_helper_not_zapier.md",
        "feedback_gmail_as_truth.md",
        "feedback_asana_direct_api_when_mcp_fails.md",
        "drive-api.py",  # may legitimately reference CloudStorage in comments
        "vault-drift-check.py",  # contains these strings as part of detection
        "shared-drives.md",
        "helper-script-registry.py",
    }

    # === Sub-check 1: orphan helpers ===
    search_dirs_for_refs = [
        VAULT / "Library" / "processes",
        VAULT / "Library" / "skills",
        VAULT / "Library" / "lessons",
        VAULT,  # picks up CLAUDE.md + MAP.md
    ]
    blob_parts = []
    for d in search_dirs_for_refs:
        if not d.is_dir():
            continue
        for f in d.rglob("*.md"):
            try:
                blob_parts.append(f.read_text(errors="ignore"))
            except Exception:
                continue
    big_blob = "\n".join(blob_parts)

    helpers = [
        p for p in sorted(scripts_dir.iterdir())
        if p.is_file() and (p.name.endswith("-api.py") or p.name.endswith("-api.sh"))
    ]
    for helper in helpers:
        # Reference test: filename appears, OR stem (`gmail-api`) appears
        if helper.name in big_blob or helper.stem in big_blob:
            continue
        issues.append(
            f"Orphan helper: {helper.name} exists at Library/processes/scripts/ "
            f"but no skill / process doc / lesson / scheduled task references it. "
            f"Per [[external-service-routing]] every helper should be cited "
            f"from at least one consumer."
        )

    # Helper: build a deduplicated set of files to scan once.
    # rglob from overlapping parents (Library/processes/scripts vs Library/processes)
    # would surface the same file twice otherwise.
    def _collect_files(*roots, suffixes={".md", ".py", ".sh"}):
        seen = set()
        out = []
        for root in roots:
            if not Path(root).is_dir():
                continue
            for f in Path(root).rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix not in suffixes:
                    continue
                if f.name in discipline_doc_names:
                    continue
                rp = f.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                out.append(f)
        return out

    # === Sub-check 2: connector-supersession violations ===
    superseded_patterns = [
        ("mcp__5b85914c-", "Zapier Google connector (superseded by drive-api.py / docs-api.py / sheets-api.py / gmail-api.py / calendar-api.py)"),
    ]
    for f in _collect_files(VAULT / "Library" / "skills", scripts_dir):
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        for pattern, label in superseded_patterns:
            if pattern in text:
                issues.append(
                    f"Connector-supersession violation: {f.relative_to(VAULT)} "
                    f"contains `{pattern}*`. {label}. "
                    f"Per [[external-service-routing]]."
                )
                break

    # === Sub-check 3: filesystem-shape Drive reads ===
    # Some files legitimately use the CloudStorage path:
    #   - vault-drive-sync.py: filesystem rsync IS the design (drive-api.py has
    #     no rsync semantics; CloudStorage is the right tool for this one job)
    #   - documentation that references the path informationally
    # Such files opt out via a marker comment:
    #   `# drive-cloudstorage-allowed` (scripts) or `<!-- drive-cloudstorage-allowed -->` (md)
    # The drift-check skips any file containing this marker.
    cloud_pattern = "/Library/CloudStorage/"
    opt_out_markers = ("drive-cloudstorage-allowed",)
    for f in _collect_files(VAULT / "Library" / "skills", scripts_dir, VAULT / "Library" / "processes"):
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        if cloud_pattern not in text:
            continue
        if any(marker in text for marker in opt_out_markers):
            continue  # explicit opt-out -- legitimate filesystem-shape usage
        issues.append(
            f"Filesystem-shape Drive read: {f.relative_to(VAULT)} contains "
            f"`{cloud_pattern}*` without the `drive-cloudstorage-allowed` opt-out marker. "
            f"Use drive-api.py instead, OR add the marker if filesystem-shape is intentional. "
            f"Per [[external-service-routing]]."
        )

    return issues


def check_claude_md_wikilinks():
    """Every CLAUDE.md wikilink should resolve to a real file.

    Catches broken cross-references introduced by file renames / retires.
    """
    issues = []
    claude_md = VAULT / "CLAUDE.md"
    if not claude_md.is_file():
        return issues
    text = claude_md.read_text(errors="ignore")
    targets = set(re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", text))
    # Ignore intentional placeholders (literal examples)
    placeholders = {"Screenshots/..."}
    for target in sorted(targets):
        if target in placeholders:
            continue
        stem = target.split("/")[-1] if "/" in target else target
        candidates = list(VAULT.rglob(f"{stem}.md"))
        if not candidates:
            candidates = list(VAULT.rglob(stem))
        if not candidates:
            issues.append(f"CLAUDE.md wikilink does not resolve: [[{target}]]")
    return issues


def check_memory_index_parity():
    """All on-disk memory files should be indexed in MEMORY.md."""
    issues = []
    mem_dir = Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
    if not mem_dir.exists():
        return issues
    # Find the memory directory: spaces/*/memory/MEMORY.md
    memory_md_candidates = list(mem_dir.rglob("memory/MEMORY.md"))
    if not memory_md_candidates:
        return issues
    memory_md = memory_md_candidates[0]
    memory_dir = memory_md.parent
    on_disk = sorted(p.name for p in memory_dir.glob("*.md") if p.name != "MEMORY.md")
    text = memory_md.read_text(errors="ignore")
    indexed = set(re.findall(r"\]\(([a-z0-9_.-]+\.md)\)", text))
    missing = set(on_disk) - indexed
    extras = indexed - set(on_disk)
    if missing:
        issues.append(f"MEMORY.md missing {len(missing)} on-disk memory file(s): {sorted(missing)[:5]}{'...' if len(missing) > 5 else ''}")
    if extras:
        issues.append(f"MEMORY.md references {len(extras)} non-existent file(s): {sorted(extras)[:5]}{'...' if len(extras) > 5 else ''}")
    return issues


def check_lesson_index_parity():
    """Every lesson file should be in Library/lessons/README.md."""
    issues = []
    lessons_dir = VAULT / "Library" / "lessons"
    readme = lessons_dir / "README.md"
    if not readme.is_file():
        issues.append("Library/lessons/README.md missing")
        return issues
    on_disk = sorted(p.name for p in lessons_dir.glob("*.md") if p.name != "README.md")
    text = readme.read_text(errors="ignore")
    indexed = set(re.findall(r"\]\((20[0-9]{2}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+\.md)\)", text))
    missing = set(on_disk) - indexed
    extras = indexed - set(on_disk)
    if missing:
        issues.append(f"lessons/README.md missing {len(missing)} on-disk lesson(s): {sorted(missing)[:5]}{'...' if len(missing) > 5 else ''}")
    if extras:
        issues.append(f"lessons/README.md references {len(extras)} non-existent file(s): {sorted(extras)[:5]}{'...' if len(extras) > 5 else ''}")
    return issues


def check_vault_root_anomalies():
    """Vault root should only contain the 10 documented top-level sections + CLAUDE.md / MAP.md.

    Anything else surfaces as a finding for Pete's call.
    """
    issues = []
    expected_dirs = {"Projects","Properties","Customers","Suppliers","Accreditations","Businesses","Personal","Library","Daily","Screenshots"}
    expected_files = {"CLAUDE.md","MAP.md"}
    # Top-level extras Pete has explicitly kept (audit 2026-05-17): supabase/ (CLI scratch), All Canary Images/ (CD asset folder).
    # If new ones land they should surface.
    pete_allowlist = {"supabase"}  # All Canary Images moved to Properties/Canary Detect Main Website/images/ on 2026-05-17
    for p in sorted(VAULT.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_file():
            if p.name in expected_files:
                continue
            issues.append(f"Vault root has unexpected file: `{p.name}`. Move to its proper home, OR add to allowlist.")
        else:
            if p.name in expected_dirs or p.name in pete_allowlist:
                continue
            issues.append(f"Vault root has unexpected directory: `{p.name}/`. Move to a documented section, OR surface for routing decision.")
    return issues


def check_skills_junk_files():
    """Library/skills/ should contain only skill folders + matching `.skill` archives.

    Anything else (random zips, scratch files) gets flagged.
    """
    issues = []
    skills_dir = VAULT / "Library" / "skills"
    if not skills_dir.is_dir():
        return issues
    expected_extras = {"_previous", "scheduled", "README.md"}
    valid_skill_names = {p.name for p in skills_dir.iterdir() if p.is_dir() and p.name not in expected_extras and (p / "SKILL.md").exists()}
    for p in sorted(skills_dir.iterdir()):
        if p.name in expected_extras:
            continue
        if p.is_dir():
            if (p / "SKILL.md").exists():
                continue
            issues.append(f"Library/skills/ has non-skill directory `{p.name}/` (no SKILL.md)")
        else:
            if p.suffix == ".skill" and p.stem in valid_skill_names:
                continue
            issues.append(f"Library/skills/ has unexpected file `{p.name}`. Should be {{skill}}.skill archive or README.md only.")
    return issues


def check_lessons_cited_from_skills():
    """A lesson is 'deployed' if it's cited from where it fires: a skill SKILL.md, CLAUDE.md,
    another lesson, a process doc (Library/processes/), or a project doc (Projects/).
    A lesson whose home is the README index (a general behavioural rule with no workflow
    home) opts out with `deployment: readme-only` in its frontmatter.

    Lessons cited from none of those AND not flagged readme-only = deployment gap.
    The fix is a DECISION (skill / process-or-project doc / readme-only), not reflexively
    adding to a skill. Framework: Library/audits/2026-05-16-lesson-deployment-matrix.md.

    Excludes the lesson README itself.
    """
    issues = []
    lessons_dir = VAULT / "Library" / "lessons"
    if not lessons_dir.is_dir():
        return issues

    # Build the consumer corpus: every skill SKILL.md + CLAUDE.md + every other lesson
    corpus_paths = [VAULT / "CLAUDE.md"]
    skills_root = VAULT / "Library" / "skills"
    if skills_root.is_dir():
        for s in skills_root.iterdir():
            if s.is_dir() and s.name not in ("_previous","scheduled"):
                skill_md = s / "SKILL.md"
                if skill_md.exists():
                    corpus_paths.append(skill_md)
    # Also walk Library/skills/scheduled/{name}/SKILL.md — scheduled tasks have their own SKILL.mds
    # which are legitimate citation homes for lessons about cron behaviour, helper APIs, etc.
    # (Mirror-only — canonical lives in ~/Documents/Claude/Scheduled/ but content is kept in lockstep.)
    scheduled_root = VAULT / "Library" / "skills" / "scheduled"
    if scheduled_root.is_dir():
        for s in scheduled_root.iterdir():
            if s.is_dir():
                skill_md = s / "SKILL.md"
                if skill_md.exists():
                    corpus_paths.append(skill_md)
    # All lesson files (each can cite another)
    lesson_files = [p for p in lessons_dir.glob("*.md") if p.name != "README.md"]
    corpus_paths.extend(lesson_files)
    # Process docs + project docs are legitimate deployment homes (a cron lesson lives in its
    # process doc; a project-workflow lesson lives in the project's files). Count them too.
    proc_root = VAULT / "Library" / "processes"
    if proc_root.is_dir():
        for p in proc_root.glob("*.md"):
            corpus_paths.append(p)
        for p in proc_root.glob("*/*.md"):
            if "scripts" not in p.parts and "secrets" not in p.parts:
                corpus_paths.append(p)
    projects_root = VAULT / "Projects"
    if projects_root.is_dir():
        for p in projects_root.rglob("*.md"):
            if "_archive" not in p.parts:
                corpus_paths.append(p)

    corpus = ""
    for p in corpus_paths:
        try:
            corpus += "\n" + p.read_text(errors="ignore")
        except Exception:
            pass

    for lesson in sorted(lesson_files):
        stem = lesson.stem
        own_text = lesson.read_text(errors="ignore")
        # Opt-out: a lesson whose home is the README index declares `deployment: readme-only`
        # (or `dismissed`) in frontmatter — deliberately not wired to any workflow consumer.
        front = own_text[:400]
        if "deployment: readme-only" in front or "deployment: dismissed" in front:
            continue
        # Reference test: stem appears in the consumer corpus, external to the lesson itself.
        own_count = own_text.count(stem)
        total_count = corpus.count(stem)
        external_count = total_count - own_count
        if external_count == 0:
            issues.append(
                f"Lesson `{lesson.name}` is not cited from any skill, CLAUDE.md, process/project doc, "
                f"or other lesson (deployment gap). DECIDE its home: a skill (only if it fires in that "
                f"skill's workflow — discuss with Pete before editing a skill), the process/project "
                f"doc where it fires, OR add `deployment: readme-only` to its frontmatter if the README "
                f"index is its home. Don't reflexively add to a skill. Framework: "
                f"[[Library/audits/2026-05-16-lesson-deployment-matrix]]."
            )
    return issues


def check_top_level_readmes():
    """Every documented top-level section should have a README.md."""
    issues = []
    # 10 top-levels post 2026-05-06 restructure (Invoices/ folded into Projects/Team-Finances/, Delegated/ folded into Projects/Team-General/Delegated/)
    expected = [
        "Projects", "Properties", "Customers", "Suppliers",
        "Accreditations", "Businesses",
        "Personal", "Library", "Daily", "Screenshots",
    ]
    for section in expected:
        readme = VAULT / section / "README.md"
        if not readme.is_file():
            issues.append(f"Top-level {section}/ has no README.md")
    return issues


# === report assembly ===

def check_automations_dashboard_parity():
    """automations-dashboard/automations.json must mirror reality.

    Sub-checks:
    1. cowork category <-> ~/Documents/Claude/Scheduled/ task folders
       (banner-aware: folders whose SKILL.md head says DISABLED/DECOMMISSIONED
       are exempt from missing-entry, but an `active` json status on a
       bannered task IS drift).
    2. launchd category <-> ~/Library/LaunchAgents/com.peterashcroft.*.plist.
    3. index.html embedded JSON == automations.json (re-embed forgotten).
    4. live page generated stamp == local generated stamp (deploy forgotten).

    Host-path sub-checks skip gracefully in the Cowork sandbox (same pattern
    as check_scheduled_tasks). Added 2026-06-06 after the IP-cron decommission
    missed the dashboard 3-step. See
    [[Library/lessons/2026-06-06-cron-changes-update-dashboard-skills-point-at-registries]].
    """
    import json as _json
    issues = []
    dash_dir = VAULT / "Library" / "processes" / "automations-dashboard"
    json_path = dash_dir / "automations.json"
    html_path = dash_dir / "index.html"
    if not json_path.is_file():
        return [f"automations.json missing at Library/processes/automations-dashboard/"]
    try:
        data = _json.loads(json_path.read_text())
    except Exception as e:
        return [f"automations.json unparseable: {e}"]
    cats = {c.get("key"): c for c in data.get("categories", [])}

    def _bannered(skill_path):
        try:
            head = skill_path.read_text(errors="ignore")[:800].upper()
            return ("DECOMMISSIONED" in head) or ("DISABLED" in head)
        except Exception:
            return False

    # 1. cowork <-> canonical Scheduled/ folders (host only)
    registry_path = Path.home() / "Documents" / "Claude" / "Scheduled"
    if registry_path.is_dir():
        cw_tasks = cats.get("cowork", {}).get("tasks", [])
        json_ids = {t["id"] for t in cw_tasks}
        folders, banners = set(), set()
        for d in registry_path.iterdir():
            if d.is_dir() and (d / "SKILL.md").is_file():
                folders.add(d.name)
                if _bannered(d / "SKILL.md"):
                    banners.add(d.name)
        for missing in sorted((folders - banners) - json_ids):
            issues.append(
                f"Cowork cron `{missing}` exists in ~/Documents/Claude/Scheduled/ with no "
                f"automations.json entry -- run the dashboard 3-step (json -> embed -> deploy)"
            )
        for ghost in sorted(json_ids - folders):
            issues.append(
                f"automations.json cowork entry `{ghost}` has no canonical task folder -- "
                f"task deleted? Remove the entry via the 3-step"
            )
        for t in cw_tasks:
            if t["id"] in banners and t.get("status") == "active":
                issues.append(
                    f"Cowork cron `{t['id']}`: SKILL.md carries a DISABLED/DECOMMISSIONED banner "
                    f"but automations.json still says `active` -- update json + deploy"
                )
    elif str(Path.home()).startswith("/sessions/"):
        issues.append(
            "Automations cowork/launchd parity sub-checks SKIPPED (Cowork sandbox cannot see "
            "~/Documents or ~/Library). Run via Desktop Commander for full coverage."
        )

    # 2. launchd <-> plists (host only; sandbox already flagged above)
    la_dir = Path.home() / "Library" / "LaunchAgents"
    if la_dir.is_dir():
        plists = {p.name[len("com.peterashcroft."):-len(".plist")] for p in la_dir.glob("com.peterashcroft.*.plist")}
        ld_ids = {t["id"] for t in cats.get("launchd", {}).get("tasks", [])}
        for missing in sorted(plists - ld_ids):
            issues.append(f"launchd agent `{missing}` has no automations.json entry -- run the dashboard 3-step")
        for ghost in sorted(ld_ids - plists):
            issues.append(f"automations.json launchd entry `{ghost}` has no plist in ~/Library/LaunchAgents/ -- update json via the 3-step")

    # 3. embedded copy in index.html == automations.json
    if html_path.is_file():
        h = html_path.read_text()
        try:
            s = h.index('<script id="data"'); s = h.index(">", s) + 1
            e = h.index("</script>", s)
            if _json.loads(h[s:e]) != data:
                issues.append("index.html embedded JSON differs from automations.json -- re-embed + deploy (3-step steps 2-3)")
        except Exception as e2:
            issues.append(f"index.html data block unreadable: {e2}")
    else:
        issues.append("automations-dashboard/index.html missing")

    # 4. live page freshness (network; transient failures are not drift)
    try:
        import urllib.request as _ur
        with _ur.urlopen("https://pete-automations.vercel.app/automations.json", timeout=10) as r:
            live = _json.loads(r.read())
        if live.get("generated") != data.get("generated"):
            issues.append(
                f"Live dashboard generated={live.get('generated')} but local automations.json "
                f"generated={data.get('generated')} -- run automations-dashboard/deploy.py"
            )
    except Exception:
        pass
    return issues


def check_cc_map_drift():
    """The Command Centre map (`Properties/Pete Command Centre/cc-map.md`) is GENERATED by
    `cc-map.py` from the live CC Supabase tables. This is the staleness backstop (plan Phase 1):
    flag if the map is missing or hasn't been regenerated recently. The daily `cc-map` cron + the
    post-deploy hook (`cc-deploy.py`) should keep it under a day old; if it drifts past 3 days,
    a wiring has stopped. Network-free — reads the age from the map's own frontmatter."""
    p = VAULT / "Properties/Pete Command Centre/cc-map.md"
    if not p.exists():
        return ["`Properties/Pete Command Centre/cc-map.md` missing — run `python3 Library/processes/scripts/cc-map.py`"]
    txt = p.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"generated_at:\s*([0-9T:\-]+)Z", txt)
    if not m:
        return ["cc-map.md has no `generated_at` — it should be machine-generated; run `cc-map.py`"]
    try:
        gen = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
        age = (datetime.now() - gen).total_seconds() / 86400  # ≤1h tz skew is irrelevant at a 3-day threshold
        if age > 3:
            return [f"cc-map.md is {age:.1f} days stale (generated {m.group(1)}Z) — the daily `cc-map` cron / deploy hook may have stopped; run `python3 Library/processes/scripts/cc-map.py`"]
    except Exception:
        pass
    return []


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    # --map-only / --quick / --automations-only CLI flags: subset the checks
    map_only = "--map-only" in sys.argv
    quick_mode = "--quick" in sys.argv
    automations_only = "--automations-only" in sys.argv

    if automations_only:
        sections = [
            ("Automations dashboard parity (json / embed / live / crons)", check_automations_dashboard_parity),
        ]
    elif map_only:
        sections = [
            ("MAP.md drift (folders / files not in MAP)", check_map_drift),
            ("CC map staleness (cc-map.md)", check_cc_map_drift),
        ]
    elif quick_mode:
        # Quick mode: skip Drive parity (slow) and orphan-script walk
        sections = [
            ("Top-level greeter READMEs", check_top_level_readmes),
            ("MAP.md drift (folders / files not in MAP)", check_map_drift),
            ("CC map staleness (cc-map.md)", check_cc_map_drift),
            ("Personal/inbox/ lingerers (>7 days)", check_inbox_lingerers),
        ]
    else:
        sections = [
            ("Top-level greeter READMEs", check_top_level_readmes),
            ("Vault root anomalies", check_vault_root_anomalies),
            ("MAP.md drift (folders / files not in MAP)", check_map_drift),
            ("CC map staleness (cc-map.md)", check_cc_map_drift),
            ("CLAUDE.md wikilink resolution", check_claude_md_wikilinks),
            ("MEMORY.md ↔ memory files parity", check_memory_index_parity),
            ("lessons/README.md ↔ lesson files parity", check_lesson_index_parity),
            ("Lesson deployment gaps (cited from 0 consumers)", check_lessons_cited_from_skills),
            ("Project READMEs", check_projects),
            ("Customer + Supplier READMEs", check_customers_suppliers),
            ("Property READMEs", check_properties),
            ("Scheduled task lockstep", check_scheduled_tasks),
            ("Automations dashboard parity (json / embed / live / crons)", check_automations_dashboard_parity),
            ("Orphan scripts", check_orphan_scripts),
            ("Skill archives", check_skills_archives),
            ("Skills folder junk files", check_skills_junk_files),
            ("Vault<->Drive parity", check_vault_drive_parity),
            ("Helper-script discipline (external-service-routing)", check_helper_script_discipline),
            ("Personal/inbox/ lingerers (>7 days)", check_inbox_lingerers),
        ]

    all_issues = {}
    for name, fn in sections:
        try:
            issues = fn()
        except Exception as e:
            issues = [f"CHECK FAILED: {e}"]
        all_issues[name] = issues

    total = sum(len(v) for v in all_issues.values())
    if total == 0:
        print(f"vault-drift-check {today}: no issues found.")
        return 0

    # Build the report
    lines = [
        f"# Vault drift check, {today}",
        "",
        f"**{total} issues found** across {len([k for k,v in all_issues.items() if v])} categories.",
        "",
        "Drift-detection scheduled task. Walks the vault, compares to convention, reports anything that doesn't match. See [[Library/lessons/2026-05-03-vault-rot-audit-and-drift-prevention]] for context.",
        "",
    ]
    for section_name, issues in all_issues.items():
        if not issues:
            continue
        lines.append(f"## {section_name} ({len(issues)})")
        lines.append("")
        for issue in issues:
            lines.append(f"- {issue}")
        lines.append("")

    lines.append("## Next steps")
    lines.append("")
    lines.append("Pick the highest-priority bucket (usually missing READMEs or scheduled-task drift) and fix in a focused session.")
    lines.append("")

    report = "\n".join(lines)

    # Save to vault
    audit_dir = VAULT / "Library" / "audits"
    audit_dir.mkdir(exist_ok=True)
    report_path = audit_dir / f"{today}-vault-drift-check.md"
    report_path.write_text(
        f"---\ntype: drift-check\ndate: {today}\nissues_total: {total}\nstatus: open\ntags: [drift-check, automation]\n---\n\n" + report
    )
    print(f"Report written to {report_path.relative_to(VAULT)}")
    print(f"Total issues: {total}")

    # Email if recipient set
    recipient = os.environ.get("DRIFT_REPORT_RECIPIENT")
    if recipient:
        try:
            m = SourceFileLoader(
                "gmail_api",
                str(VAULT / "Library/processes/scripts/gmail-api.py"),
            ).load_module()
            g = m.GmailAPI(user="pete.ashcroft@sygma-solutions.com")
            html_body = "<pre style='font-family:Menlo,monospace;font-size:12px'>" + report + "</pre>"
            g.send(to=recipient, subject=f"Vault drift check ({total} issues) — {today}", body=html_body, html=True)
            print(f"Emailed report to {recipient}")
        except Exception as e:
            print(f"Email send failed: {e}")

    return total


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)