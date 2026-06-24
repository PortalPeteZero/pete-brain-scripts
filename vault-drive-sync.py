VAULT = os.environ.get("VAULT", "/tmp/pbs")
#!/usr/bin/env python3
# drive-cloudstorage-allowed -- This script implements the rsync-style filesystem
# sync that is the intentional exception to the helper-first rule. drive-api.py
# has no rsync semantics; Drive Desktop's CloudStorage path IS the right tool
# for this one job. See [[external-service-routing]] for the marker convention.
"""
vault-drive-sync.py -- Bidirectional sync between vault and Pete & Mic Drive,
plus pull-only mirror of My Drive root into Personal/inbox/.

Two-way pairs now propagate DELETES in BOTH directions (added 2026-06-05), using a
per-pair manifest of the last reconciled state to tell a genuine delete apart from a
file that simply hasn't been created yet. Heavy safety guards protect against the
Drive-Desktop-offline failure mode (a side that looks empty must NOT trigger a mass
wipe of the other side).

Three sync paths:
1. vault Personal/family/                       <-> Drive Pete & Mic / Ashcroft Family/  (2-way, deletes propagate)
2. (REMOVED 2026-06-03) Sygma owner-private -- now Drive-direct, NOT mirrored. Do not re-add.
3. My Drive root (excluding business + system)  ->  vault Personal/inbox/                (pull-only, additive)

Two-way reconciliation algorithm (per pair), given:
  M = manifest (relpath -> per-side size+mtime) from the last successful reconciled run
  V = current vault file set
  D = current drive file set
For each relpath:
  - in V and D : rsync --update copies the newer over the older (content reconcile)
  - in V only  : if in M and UNCHANGED in V since M  -> deleted on Drive  -> delete from V
                 else (new in V, or edited in V after a Drive delete) -> keep, push to D
  - in D only  : symmetric -> deleted in vault -> delete from D; else keep, pull to V
Delete/edit conflicts always resolve to KEEP the surviving copy (never lose an edit).

Safety guards (all must pass before any delete is applied):
  - Both roots must exist.
  - If the manifest is non-empty, neither side may be empty (Drive-offline guard).
  - Total deletes across both sides must be <= DELETE_CAP (max(25, 5% of manifest)).
    Over the cap -> ABORT the pair's deletes (additive sync still runs) unless --force.
  - First run (no manifest) -> NO deletes; just additive sync + write the manifest.
  - --dry-run -> compute and print the plan, touch nothing, don't write the manifest.

Usage:
  python3 vault-drive-sync.py             # run all syncs (deletes propagate, capped)
  python3 vault-drive-sync.py --dry-run   # show the full plan (incl. planned deletes), change nothing
  python3 vault-drive-sync.py --force      # allow deletes to exceed the safety cap (use with care)
  python3 vault-drive-sync.py --no-deletes # additive-only, legacy behaviour (no delete propagation)
  python3 vault-drive-sync.py --status     # show last run from state file
  python3 vault-drive-sync.py --inbox-only # only pull My Drive -> Personal/inbox/

Path conventions:
- Drive Desktop local mirror at /Users/peterashcroft/Library/CloudStorage/GoogleDrive-.../...
- Runs against the LOCAL CloudStorage paths (Drive Desktop handles cloud<->local separately).

Setup:
- chmod +x vault-drive-sync.py
- Schedule: see Library/processes/vault-drive-sync.md for launchd plist + cron alternative.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# === Config ===
VAULT = Path(VAULT)
PETE_AND_MIC = Path(
    "/Users/peterashcroft/Library/CloudStorage/"
    "GoogleDrive-pete.ashcroft@sygma-solutions.com/Shared drives/Pete & Mic"
)
MY_DRIVE = Path(
    "/Users/peterashcroft/My Drive (pete.ashcroft@sygma-solutions.com)"
)
STATE_FILE = VAULT / "Library/processes/vault-drive-sync-state.json"
MANIFEST_DIR = VAULT / "Library/processes"

# Delete-propagation safety caps
DELETE_CAP_ABS = 25      # never auto-delete more than this many files per side ...
DELETE_CAP_PCT = 0.05    # ... or more than this fraction of the manifest, whichever is larger.

# Two-way sync pairs (vault <-> drive)
#
# NOTE (2026-06-03): sygma-owner-private was REMOVED from this list deliberately.
# Owner-private content (payroll PDFs, accounts, personnel binaries) is now
# Drive-direct: it lives ONLY at `Pete & Mic / Sygma Solutions Private/` and is
# accessed directly via the CloudStorage path. It is NOT mirrored into the vault.
# Do NOT re-add this pair. See [[vault-drive-sync]] History 2026-06-03.
# Excludes for the My Drive inbox pair (basename match, any depth). The drive side is the WHOLE
# My Drive root, so these trees must be kept out of both rsync and the manifest/delete logic.
MY_DRIVE_EXCLUDES = [
    "Business Brain",      # Pete's older Manus second brain - explicitly do not touch
    "Google AI Studio",    # Google AI Studio app folder
    "Icon",                # macOS Drive Desktop metadata
    "02 Pictures",         # Photo library lives in vault Personal/pictures/ -- pull-only caused 118 GB dup (2026-05-06)
    "Personal",            # Drive "mirror" symlink that loops back into the vault Personal tree --
                           # following it (with -L) nested Personal/inbox/Personal/inbox/... 20 deep,
                           # 2.7 GB / 23k files (2026-06-05). My Drive root is scratch only; the canonical
                           # Personal area already lives in the vault. Never pull it here.
    ".mirror-symlink",     # Drive Desktop's internal mirror store (target of mirror symlinks)
    ".tmp.drivedownload",  # Drive Desktop's in-progress download staging
    ".DS_Store",
    ".Spotlight-V100",
    ".Trashes",
    ".fseventsd",
]

TWO_WAY_PAIRS = [
    {
        "name": "ashcroft-family",
        "vault": VAULT / "Personal/family",
        "drive": PETE_AND_MIC / "Ashcroft Family",
    },
    # My Drive inbox: pull scratch IN (My Drive -> inbox), NEVER push inbox content up to My Drive.
    # DELETE policy (changed 2026-06-19): the sync NEVER deletes from My Drive (`no_drive_delete`).
    # My Drive is the user's own durable space, not disposable scratch — adding files there is always
    # safe (they get pulled into the inbox and left on My Drive). Deletes only propagate ONE way:
    # a file removed from My Drive drops from the inbox. Triaging a file out of the inbox no longer
    # deletes the My Drive original (you remove that yourself). Rationale: the old both-ways policy
    # nearly binned a deliberate 76-file Clancy folder parked on My Drive (Pete, 19 Jun) — My Drive
    # being a delete-mirror was surprising + risky. follow_symlinks=False guards the mirror-symlink
    # loop; the excludes keep Business Brain / Personal / 02 Pictures out.
    {
        "name": "my-drive-inbox",
        "vault": VAULT / "Personal/inbox",
        "drive": MY_DRIVE,
        "excludes": MY_DRIVE_EXCLUDES,
        "follow_symlinks": False,
        "push_additions": False,
        "no_drive_delete": True,
    },
]

# Google-native pointer files (.gdoc/.gsheet/...) are Drive-Desktop stubs for cloud-native
# docs. They CANNOT round-trip through rsync into a Drive folder -- Drive Desktop strips them,
# so they look "deleted on Drive" and the delete logic would wrongly delete the vault copy
# (this exact bug deleted a Scot Lane .gdoc on 2026-06-05, since restored). They are excluded
# from BOTH rsync and the snapshot/manifest entirely, so they're never copied or deleted.
GNATIVE_EXTS = (
    ".gdoc", ".gsheet", ".gslides", ".gdraw", ".gform", ".gsite",
    ".gmap", ".gtable", ".glink", ".gjam", ".gnote", ".gscript",
)

# Common rsync excludes for all paths
COMMON_EXCLUDES = [
    ".DS_Store",
    "Icon\\r",     # macOS Drive Desktop folder icon (with carriage return)
    "._*",         # macOS resource forks
    ".Trashes",
    ".Spotlight-V100",
    ".fseventsd",
    ".TemporaryItems",
] + [f"*{ext}" for ext in GNATIVE_EXTS]   # Google-native pointer stubs -- see GNATIVE_EXTS note

# Names skipped when snapshotting a tree for manifest/delete logic (mirror of the excludes above).
SNAPSHOT_SKIP_NAMES = {
    ".DS_Store", ".Trashes", ".Spotlight-V100", ".fseventsd", ".TemporaryItems",
}


def _detect_case_duplicates(src: Path, dst: Path) -> list:
    """Return list of case-different top-level folder name collisions between src and dst.

    Added 2026-05-03 night after sync created hmrc-personal/ alongside HMRC Personal/.
    """
    if not src.is_dir() or not dst.is_dir():
        return []
    src_names = {p.name for p in src.iterdir() if p.is_dir()}
    dst_names = {p.name for p in dst.iterdir() if p.is_dir()}
    src_lower = {n.lower(): n for n in src_names}
    dst_lower = {n.lower(): n for n in dst_names}
    collisions = []
    for low, src_n in src_lower.items():
        if low in dst_lower and dst_lower[low] != src_n:
            collisions.append((src_n, dst_lower[low]))
    return collisions


def snapshot(root: Path, exclude_names=frozenset(), follow_symlinks: bool = True) -> dict:
    """Return {relpath: [size, mtime_int]} for every real file under root, skipping junk.

    `exclude_names`: basenames to skip at any depth (mirrors rsync --exclude NAME semantics) --
    used for the inbox pair so the My Drive root's excluded trees (Business Brain, Personal
    symlink, 02 Pictures, ...) never enter the manifest/delete logic.
    `follow_symlinks=False`: don't traverse symlinked dirs and skip symlink entries (Drive
    mirror-symlink loop guard).
    Returns {} for a missing root (caller distinguishes missing-vs-empty via root.exists()).
    """
    out = {}
    if not root.is_dir():
        return out
    skip = set(exclude_names) | SNAPSHOT_SKIP_NAMES
    for dp, dn, fn in os.walk(root, followlinks=follow_symlinks):
        kept = []
        for d in dn:
            if d in skip or d.startswith(".tmp.drivedownload"):
                continue
            if not follow_symlinks and os.path.islink(os.path.join(dp, d)):
                continue
            kept.append(d)
        dn[:] = kept
        for f in fn:
            if f in skip or f.startswith("._") or f == "Icon\r":
                continue
            if f.endswith(GNATIVE_EXTS):   # Google-native pointer stub -- never sync/delete
                continue
            full = os.path.join(dp, f)
            if not follow_symlinks and os.path.islink(full):
                continue
            rel = os.path.relpath(full, root)
            try:
                st = os.stat(full)
                out[rel] = [st.st_size, int(st.st_mtime)]
            except OSError:
                out[rel] = [-1, 0]
    return out


def _manifest_path(name: str) -> Path:
    return MANIFEST_DIR / f"vault-drive-sync-manifest-{name}.json"


def load_manifest(name: str) -> dict:
    p = _manifest_path(name)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def save_manifest(name: str, snap: dict) -> None:
    _manifest_path(name).write_text(json.dumps(snap, separators=(",", ":")))


def _unchanged(cur: list, man_side: list) -> bool:
    """Same file as last reconciled run? Compare size, allow small mtime jitter.

    Drive Desktop can rewrite mtimes on download. A 2 s tolerance avoids treating a
    re-materialised-but-identical file as 'edited'. Erring toward 'changed' is the SAFE
    direction anyway (it turns a delete into a keep), so this only trims false conflicts.
    """
    if not man_side or man_side[0] == -1:
        return False
    return cur[0] == man_side[0] and abs(cur[1] - man_side[1]) <= 2


def plan_deletes(vsnap: dict, dsnap: dict, manifest: dict):
    """Decide which only-on-one-side files are genuine deletes to propagate.

    Manifest value shape: [v_size, v_mtime, d_size, d_mtime].
    Returns (del_from_vault, del_from_drive, conflicts).
    """
    del_from_vault, del_from_drive, conflicts = [], [], []
    only_v = set(vsnap) - set(dsnap)
    only_d = set(dsnap) - set(vsnap)
    for rel in only_v:
        if rel in manifest:
            man = manifest[rel]
            existed_both = len(man) >= 4 and man[0] != -1 and man[2] != -1
            if not existed_both:
                continue                        # baseline one-sided file, NOT a delete -> leave it
            if _unchanged(vsnap[rel], man[0:2]):
                del_from_vault.append(rel)      # genuine delete on Drive side
            else:
                conflicts.append(["edited-in-vault-deleted-on-drive", rel])  # keep + push
        # else: brand new in vault -> keep + push (handled by additive rsync)
    for rel in only_d:
        if rel in manifest:
            man = manifest[rel]
            existed_both = len(man) >= 4 and man[0] != -1 and man[2] != -1
            if not existed_both:
                continue                        # baseline one-sided file, NOT a delete -> leave it
            if _unchanged(dsnap[rel], man[2:4]):
                del_from_drive.append(rel)      # genuine delete on vault side
            else:
                conflicts.append(["edited-on-drive-deleted-in-vault", rel])  # keep + pull
        # else: brand new on drive -> keep + pull (handled by additive rsync)
    return sorted(del_from_vault), sorted(del_from_drive), sorted(conflicts)


def _delete_cap(manifest: dict) -> int:
    return max(DELETE_CAP_ABS, int(len(manifest) * DELETE_CAP_PCT))


def apply_deletes(root: Path, rels: list, dry_run: bool) -> list:
    """Delete the given relpaths under root; prune emptied dirs. Returns relpaths actually removed."""
    removed = []
    for rel in rels:
        full = root / rel
        if dry_run:
            removed.append(rel)
            continue
        try:
            if full.exists():
                full.unlink()
            removed.append(rel)
        except OSError as e:
            print(f"    ! could not delete {full}: {e}")
    if not dry_run:
        for dp, dn, fn in os.walk(root, topdown=False):
            if dp == str(root):
                continue
            try:
                if not os.listdir(dp):
                    os.rmdir(dp)
            except OSError:
                pass
    return removed


def rsync_one_way(src: Path, dst: Path, excludes: list, dry_run: bool = False, follow_symlinks: bool = True) -> dict:
    """rsync src/ -> dst/ with --update (newer wins). Additive (creates/updates only).

    follow_symlinks=True (-L) resolves Drive *shortcuts* -- correct for the 2-way pairs.
    follow_symlinks=False (--no-links) SKIPS symlinks entirely -- used for the My Drive inbox
    pull, where Drive "mirror" symlinks can loop back into the vault and recurse infinitely
    (the 2026-06-05 Personal/inbox/Personal/inbox/... blowup). Belt-and-braces alongside the
    explicit `Personal` / `.mirror-symlink` excludes.
    """
    if not src.exists():
        return {"ok": False, "error": f"source not found: {src}", "src": str(src), "dst": str(dst)}
    case_collisions = _detect_case_duplicates(src, dst)
    if case_collisions:
        return {
            "ok": False,
            "error": f"case-only-different folder names detected: {case_collisions}. Align naming before sync.",
            "src": str(src), "dst": str(dst), "case_collisions": case_collisions,
        }
    dst.mkdir(parents=True, exist_ok=True)
    link_flag = "-L" if follow_symlinks else "--no-links"
    cmd = ["rsync", "-a", link_flag, "--update", "--no-perms", "--no-owner", "--no-group", "--itemize-changes"]
    if dry_run:
        cmd.append("--dry-run")
    for ex in COMMON_EXCLUDES + excludes:
        cmd.extend(["--exclude", ex])
    cmd.extend([str(src) + "/", str(dst) + "/"])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        changes = [line for line in proc.stdout.splitlines() if line and line[0] in "<>.cdfh*"]
        return {"ok": proc.returncode == 0, "src": str(src), "dst": str(dst),
                "files_changed": len(changes), "stderr": proc.stderr[-500:] if proc.stderr else "", "exit": proc.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "rsync timeout (>1h)", "src": str(src), "dst": str(dst)}
    except Exception as e:
        return {"ok": False, "error": str(e), "src": str(src), "dst": str(dst)}


def two_way(pair: dict, dry_run: bool = False, deletes: bool = True, force: bool = False) -> dict:
    """Two-way sync with manifest-based delete propagation, then additive rsync both ways."""
    name = pair["name"]
    vault, drive = pair["vault"], pair["drive"]
    # Per-pair options. `excludes`/`follow_symlinks` matter for the inbox pair whose drive side is
    # the whole My Drive root. `push_additions=False` means new vault-side files are NOT pushed to
    # drive (inbox is a vault staging area -- we pull scratch IN and propagate deletes BOTH ways,
    # but never populate My Drive from the inbox).
    ex = pair.get("excludes", [])
    fsym = pair.get("follow_symlinks", True)
    push = pair.get("push_additions", True)
    res = {"name": name, "deletes_enabled": deletes}

    if not vault.exists() or not drive.exists():
        res["error"] = f"root missing (vault={vault.exists()}, drive={drive.exists()}) -- skipped"
        return res

    manifest = load_manifest(name)
    vsnap = snapshot(vault, ex, fsym)
    dsnap = snapshot(drive, ex, fsym)
    res["counts"] = {"vault": len(vsnap), "drive": len(dsnap), "manifest": len(manifest)}

    del_v, del_d, conflicts = [], [], []
    delete_status = "skipped (deletes off)"
    if deletes and manifest:
        del_v, del_d, conflicts = plan_deletes(vsnap, dsnap, manifest)
        if pair.get("no_drive_delete") and del_d:
            del_d = []  # My Drive is durable storage, not inbox scratch -- never delete from it (see pair config)
        cap = _delete_cap(manifest)
        side_empty = (len(vsnap) == 0 or len(dsnap) == 0)
        total_del = len(del_v) + len(del_d)
        # Abort guards. On abort we PAUSE the whole pair -- crucially we DON'T run the
        # additive rsync (which would re-push the would-be-deleted files and silently
        # revert the delete) and DON'T rewrite the manifest (so the pending delete
        # survives for a later --force re-run).
        if total_del > 0 and side_empty:
            res["delete_status"] = ("ABORTED: a side is empty (Drive offline?) -- pair PAUSED, "
                                    "no sync this run. Check Drive Desktop, then re-run.")
            res["paused"] = True
            res["deleted_from_vault"] = del_v
            res["deleted_from_drive"] = del_d
            res["conflicts_kept"] = conflicts
            return res
        if total_del > cap and not force:
            res["delete_status"] = (f"ABORTED: {total_del} deletes > cap {cap} -- pair PAUSED, no sync "
                                    f"this run. Review, then re-run with --force to apply.")
            res["paused"] = True
            res["deleted_from_vault"] = del_v
            res["deleted_from_drive"] = del_d
            res["conflicts_kept"] = conflicts
            return res
        removed_v = apply_deletes(vault, del_v, dry_run)
        removed_d = apply_deletes(drive, del_d, dry_run)
        delete_status = (f"{'PLANNED' if dry_run else 'APPLIED'}: -{len(removed_v)} vault, "
                         f"-{len(removed_d)} drive (cap {cap})")
    elif deletes and not manifest:
        delete_status = "first run: no manifest yet -- additive only, manifest written after"

    res["delete_status"] = delete_status
    res["deleted_from_vault"] = del_v
    res["deleted_from_drive"] = del_d
    res["conflicts_kept"] = conflicts

    # Additive content reconcile (creates + newer-wins). Pull always; push only if push_additions.
    if push:
        res["push_vault_to_drive"] = rsync_one_way(vault, drive, ex, dry_run, follow_symlinks=fsym)
    else:
        res["push_vault_to_drive"] = {"ok": True, "skipped": "push_additions disabled (pull-only-additive pair)"}
    res["pull_drive_to_vault"] = rsync_one_way(drive, vault, ex, dry_run, follow_symlinks=fsym)

    # Rebuild manifest from the now-reconciled state (live runs only).
    if not dry_run:
        v2 = snapshot(vault, ex, fsym)
        d2 = snapshot(drive, ex, fsym)
        merged = {}
        for rel in set(v2) | set(d2):
            vv = v2.get(rel, [-1, 0])
            dd = d2.get(rel, [-1, 0])
            merged[rel] = [vv[0], vv[1], dd[0], dd[1]]
        save_manifest(name, merged)
        res["manifest_written"] = len(merged)

    return res


def save_state(payload: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2))


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def show_status() -> None:
    state = load_state()
    if not state:
        print("No previous run state found.")
        return
    print(f"Last run: {state.get('last_run_iso', 'unknown')}  (mode: {state.get('mode', '?')})")
    for r in state.get("results", []):
        if "name" not in r:
            continue
        print(f"\n[{r['name']}]")
        if "delete_status" in r:
            print(f"  deletes: {r['delete_status']}")
            if r.get("deleted_from_vault"):
                print(f"    - vault: {r['deleted_from_vault']}")
            if r.get("deleted_from_drive"):
                print(f"    - drive: {r['deleted_from_drive']}")
            if r.get("conflicts_kept"):
                print(f"    conflicts kept: {r['conflicts_kept']}")
        for key in ("push_vault_to_drive", "pull_drive_to_vault", "result"):
            if isinstance(r.get(key), dict):
                v = r[key]
                print(f"  {key}: ok={v.get('ok')} files_changed={v.get('files_changed', '?')}")


def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    inbox_only = "--inbox-only" in args
    force = "--force" in args
    deletes = "--no-deletes" not in args
    if "--status" in args:
        show_status()
        return 0

    started = datetime.now(timezone.utc)
    results = []

    pairs = [p for p in TWO_WAY_PAIRS if p["name"] == "my-drive-inbox"] if inbox_only else TWO_WAY_PAIRS
    for pair in pairs:
        arrow = "->" if pair.get("push_additions", True) is False else "<->"
        print(f"[sync] {pair['name']}: {pair['drive'].name} {arrow} {pair['vault'].name} "
              f"(deletes={'on' if deletes else 'off'}{', force' if force else ''}{', DRY-RUN' if dry_run else ''})")
        r = two_way(pair, dry_run, deletes, force)
        if r.get("error"):
            print(f"  ! {r['error']}")
        if "delete_status" in r:
            print(f"  deletes: {r['delete_status']}")
        results.append(r)

    finished = datetime.now(timezone.utc)
    payload = {
        "last_run_iso": finished.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
        "mode": "dry-run" if dry_run else "live",
        "deletes": deletes,
        "force": force,
        "inbox_only": inbox_only,
        "results": results,
    }

    if not dry_run:
        save_state(payload)
        print(f"\nState saved to {STATE_FILE}")
    print(f"Done in {payload['duration_seconds']:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())