#!/usr/bin/env python3
"""clancy-dn-drive-audit.py — what is ACTUALLY in the Depotnet Damages Drive tree.

Pete, 1 Aug 2026: "the key thing here is I don't want dupes so this run will involve more work
checking every drive folder".

A capture that uploads without looking first will duplicate, because Drive happily allows two
files with the same name in the same folder and says nothing. This walks the whole tree and
reports the truth: one folder per damage, what is in it, and anything doubled — by folder name,
by file name within a folder, and by damage id.

It is READ-ONLY. It never uploads, moves or deletes; it prints what a capture should skip.

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-drive-audit.py                 # human report
  VAULT=/tmp/pbs python3 clancy-dn-drive-audit.py --json out.json # machine-readable inventory
  VAULT=/tmp/pbs python3 clancy-dn-drive-audit.py --id 133852     # one damage
"""
import os, re, sys, json, argparse, subprocess, collections

VAULT = os.environ.get("VAULT", "/tmp/pbs")
DRIVE = f"{VAULT}/drive-api.py"
DAMAGES_FOLDER = "19XZoec62Zjo02EQKWsrWszpOGcivG8aE"   # Drive ▸ … ▸ Depotnet Damages

# drive-api.py ls prints: TYPE  SIZE  MODIFIED  ID  NAME
_LS = re.compile(r"^(DIR|FILE)\s+(\S+)\s+(\S+)\s+([\w-]{20,})\s+(.*)$")


def ls(folder_id):
    # RAISE on a failed listing — never return [] for it. drive-api.py exits 1 with empty stdout
    # on any API error, so ignoring the return code makes an outage indistinguishable from an
    # empty folder — and for an AUDIT that is fatal: a failed top-level listing reported
    # "0 folder-sets, no problems", a clean pass over nothing. Same fix as clancy-dn-files.py.
    r = subprocess.run(["python3", DRIVE, "ls", folder_id], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode:
        raise RuntimeError(f"Drive listing failed for {folder_id}: "
                           + (r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}"))
    out = []
    for line in r.stdout.splitlines():
        m = _LS.match(line.strip())
        if m:
            out.append({"type": m.group(1), "size": m.group(2), "modified": m.group(3),
                        "id": m.group(4), "name": m.group(5).strip()})
    return out


def walk(folder_id, depth=0):
    """Every FILE under a folder, with the sub-path it sits in."""
    files = []
    for e in ls(folder_id):
        if e["type"] == "FILE":
            files.append({**e, "sub": ""})
        elif depth < 3:
            for f in walk(e["id"], depth + 1):
                f["sub"] = e["name"] + ("/" + f["sub"] if f["sub"] else "")
                files.append(f)
    return files


def audit(only=None):
    tree, problems = {}, []
    kids = ls(DAMAGES_FOLDER)
    by_damage = collections.defaultdict(list)
    for k in kids:
        if k["type"] != "DIR":
            problems.append(f"loose FILE at the top of Depotnet Damages: {k['name']}")
            continue
        m = re.match(r"^(\d{3,7})\s", k["name"])
        if m:
            by_damage[int(m.group(1))].append(k)
        elif not k["name"].startswith("exports"):
            problems.append(f"folder with no damage id in its name: {k['name']}")

    for did, folders in sorted(by_damage.items()):
        if only and did != only:
            continue
        if len(folders) > 1:
            problems.append(f"damage {did} has {len(folders)} folders — "
                            + ", ".join(f"{f['id']}({f['name'][:40]})" for f in folders))
        entry = {"folders": [], "files": {}, "dupe_names": []}
        for f in folders:
            files = walk(f["id"])
            entry["folders"].append({"id": f["id"], "name": f["name"], "files": len(files)})
            for x in files:
                entry["files"].setdefault(x["name"], []).append(
                    {"folder": f["id"], "sub": x["sub"], "id": x["id"], "size": x["size"]})
        # A real duplicate is the same name TWICE IN THE SAME PLACE. The first version compared
        # names across the whole tree and flagged six damages whose only crime was a README.md at
        # the folder root, in a sub-folder and in _raw — three legitimate files. A noisy gate gets
        # ignored, so the key is (folder, sub-path, name).
        for name, hits in entry["files"].items():
            spots = {}
            for h in hits:
                spots.setdefault((h["folder"], h["sub"]), []).append(h)
            for (fid, sub), same in spots.items():
                if len(same) > 1:
                    entry["dupe_names"].append(name)
                    problems.append(f"damage {did}: '{name}' appears {len(same)} times in "
                                    f"{fid}/{sub or '(root)'}")
        tree[did] = entry
    return tree, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the inventory here")
    ap.add_argument("--id", type=int, help="audit one damage only")
    a = ap.parse_args()

    tree, problems = audit(a.id)
    total_files = sum(len(v["files"]) for v in tree.values())
    total_copies = sum(len(h) for v in tree.values() for h in v["files"].values())
    print(f"{len(tree)} damage folder-set(s) · {total_files} distinct filename(s) · "
          f"{total_copies} actual file(s) in Drive")
    for did in sorted(tree):
        v = tree[did]
        marks = ""
        if len(v["folders"]) > 1:
            marks += f"  !! {len(v['folders'])} FOLDERS"
        if v["dupe_names"]:
            marks += f"  !! {len(v['dupe_names'])} duplicated filename(s)"
        print(f"  {did}  {sum(f['files'] for f in v['folders']):3} file(s){marks}")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"  · {p}")
    else:
        print("\nno duplicates, no loose files, one folder per damage.")
    if a.json:
        json.dump({"tree": {str(k): v for k, v in tree.items()}, "problems": problems},
                  open(a.json, "w"), indent=1)
        print(f"\ninventory -> {a.json}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
