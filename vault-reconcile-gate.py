#!/usr/bin/env python3
"""vault-reconcile-gate.py — the PRE-I deletion gate (Business OS Part I). Walks every CONTENT file
in the vault and confirms it has a twin: a vault_notes row (for .md knowledge) or a drive_files object
(for everything else). Outputs a zero-content-orphans report. READ-ONLY — never deletes anything.

The vault must NOT be deleted until this reports 0 content orphans. The operating SKELETON
(CLAUDE.md, MAP, Library/processes, Library/skills, Daily, the PA-* projects, .claude, …) is excluded
— it relocates to My Drive / stays on the Mac, it isn't "content" to reconcile.

Usage: python3 vault-reconcile-gate.py [--list]   (--list prints every orphan)
"""
import os, sys, json, subprocess
VAULT = os.environ.get("VAULT", "/tmp/pbs")
VAULT = VAULT
SQL = f"{VAULT}/Library/processes/scripts/cc-sql.py"
LIST = "--list" in sys.argv

# operating skeleton — excluded (relocates to My Drive / stays on Mac, classified per the plan)
SKEL_DIRS = {".git", ".claude", ".obsidian", "Screenshots", "Daily",
             "Library/processes", "Library/skills",
             "Projects/PA-Command-Centre", "Projects/PA-General"}
SKEL_FILES = {"CLAUDE.md", "MAP.md", "MEMORY.md", "README.md"}

def is_skeleton(rel: str) -> bool:
    if rel in SKEL_FILES: return True
    return any(rel == d or rel.startswith(d + "/") for d in SKEL_DIRS)

def q(sql):
    return json.loads(subprocess.run(["python3", SQL, sql], capture_output=True, text=True).stdout or "[]")

print("loading twins from the DB…")
note_paths = {r["vault_path"] for r in q("SELECT vault_path FROM vault_notes")}
# drive_files names (lowercased) — the 'is it in Drive' set. One indexed scan.
drive_names = {(r["name"] or "").lower() for r in q("SELECT name FROM drive_files WHERE is_folder = false")}
print(f"  vault_notes paths: {len(note_paths)} · drive_files names: {len(drive_names)}")

content, twinned, orphans = 0, 0, []
for root, dirs, files in os.walk(VAULT):
    dirs[:] = [d for d in dirs if not d.startswith(".") or d in (".claude",)]  # still walk .claude to skip it explicitly
    for fn in files:
        if fn.startswith("."):  # .DS_Store etc
            continue
        rel = os.path.relpath(os.path.join(root, fn), VAULT)
        if is_skeleton(rel):
            continue
        content += 1
        has = (rel in note_paths) if fn.lower().endswith(".md") else (fn.lower() in drive_names)
        if has: twinned += 1
        else: orphans.append(rel)

print(f"\n=== RECONCILE GATE ===")
print(f"content files walked: {content} · twinned: {twinned} · ORPHANS: {len(orphans)}")
if orphans:
    by_top = {}
    for o in orphans: by_top[o.split("/")[0]] = by_top.get(o.split("/")[0], 0) + 1
    print("orphans by top-level area:")
    for k, v in sorted(by_top.items(), key=lambda x: -x[1]): print(f"   {v:5}  {k}")
    if LIST:
        print("\nall orphans:")
        for o in orphans: print("  ", o)
    print(f"\n⛔ NOT safe to delete the vault — {len(orphans)} content files have no Drive/DB twin. Resolve first.")
else:
    print("\n✅ zero content orphans — every content file has a Drive or DB twin. Gate PASSES.")