#!/usr/bin/env python3
"""
drive-readme-staleness.py -- on-demand audit that closes the gap the 24-Jun Business OS
cutover left: nothing ever read the CONTENT of Drive README/doc files, so vault-era /
Asana references sat frozen in them for weeks (found 29 Jun 2026).

Bounded Drive-API sweep: pulls every README.md across the drives from the `drive_files`
index, downloads each body via alt=media, and greps for retired-system fingerprints.
Reports hits for a human to fix. NOT a cron (no sprawl) -- invoked on demand and by the
vault-check skill's system audit.

Usage:  VAULT=/tmp/pbs python3 drive-readme-staleness.py [--full] [--limit N]
        (default samples nothing -- does the full sweep; --limit caps for a quick check)

Process doc: Library/processes/hub-maintenance.md  ·  lesson: 2026-06-29-migration-doneness-must-grep-drive-file-contents
"""
import importlib.util, json, os, re, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("drv", os.path.join(HERE, "drive-api.py"))
drv = importlib.util.module_from_spec(spec); spec.loader.exec_module(drv)

# Retired-system fingerprints. EXCLUDE [[hub-maintenance]] -- that is the CURRENT
# boilerplate hub-reconcile writes into every Hub README, not staleness.
FINGERPRINTS = [
    (r"asana", "asana"),
    (r"\bvault-drive-sync\b", "vault-drive-sync"),
    (r"vault is source of truth|Vault holds the source of truth|serves both vault", "vault-as-truth"),
    (r"\[\[Library/", "old-tree [[Library/"),
    (r"\[\[Projects/", "old-tree [[Projects/"),
    (r"\[\[Businesses/|Businesses/sygma", "old-tree Businesses/"),
    (r"\bDataview\b", "Dataview (Obsidian)"),
    (r"\[\[vault-drive-sync\]\]|\[\[vault-routing", "vault wikilink"),
]

def readmes(limit=None):
    sql = "SELECT drive, path, drive_file_id FROM drive_files WHERE name ILIKE '%readme.md' ORDER BY drive, path"
    if limit:
        sql += f" LIMIT {int(limit)}"
    out = subprocess.check_output(["python3", os.path.join(HERE, "cc-sql.py"), sql],
                                  env={**os.environ, "VAULT": VAULT})
    return json.loads(out)

def main():
    limit = None
    if "--limit" in sys.argv:
        limit = sys.argv[sys.argv.index("--limit") + 1]
    rows = readmes(limit)
    print(f"staleness audit: {len(rows)} READMEs across the drives")
    flagged, errors = [], 0
    for i, r in enumerate(rows):
        try:
            drv.get_file(r["drive_file_id"], "/tmp/_stale.md")
            t = open("/tmp/_stale.md", encoding="utf-8", errors="replace").read()
        except Exception:
            errors += 1; continue
        hits = sorted({label for pat, label in FINGERPRINTS if re.search(pat, t, re.I)})
        if hits:
            flagged.append((r["drive"], r["path"], hits))
        if i % 100 == 0:
            print(f"  {i}/{len(rows)} scanned ({len(flagged)} flagged)", flush=True)
    print(f"\n=== {len(flagged)} stale READMEs (of {len(rows)}, {errors} unreadable) ===")
    for drive, path, hits in flagged:
        print(f"  [{drive}] {path}  ->  {', '.join(hits)}")
    return flagged

if __name__ == "__main__":
    main()
