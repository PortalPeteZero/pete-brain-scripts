#!/usr/bin/env python3
"""package-skill.py — repackage a skill's .skill archive from its source folder AND
deliver the current package to the local Downloads folder, ready to install in Cowork.

WHY: skills live at $VAULT/skills/<name>/ (source folder) with a sibling <name>.skill
(a zip of the folder's CONTENTS — SKILL.md at the archive root). The source + archive
must stay in lockstep, and the installable package must reach Pete's Mac. Doing that by
hand drifted (Downloads held pre-repackage versions). This makes packaging one command
that ALWAYS rebuilds the archive in lockstep and drops it into a single canonical local
folder, so "skill updated" → "current package waiting to install" is automatic.

USAGE
  package-skill.py <name> [<name> ...]   repackage + deliver the named skills
  package-skill.py --all                 repackage + deliver every skill
  package-skill.py --changed             repackage + deliver only skills whose source
                                         differs from its current .skill (content-compare)
  package-skill.py --no-deliver ...      rebuild the .skill(s) only; skip local delivery
  (no args defaults to --changed — "package whatever I just edited")

Delivery target: ~/Downloads/cc-skills-to-install/  (+ _INSTALL-ME.md manifest).
Skipped automatically when there is no ~/Downloads (e.g. a cloud/Railway run) — the
repo .skill is still rebuilt so source + archive never drift.
"""
import os, sys, io, zipfile, hashlib, shutil, datetime, pathlib, re

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SKILLS = pathlib.Path(VAULT) / "skills"
DOWNLOADS = pathlib.Path.home() / "Downloads"
DEST = DOWNLOADS / "cc-skills-to-install"
SKIP_NAMES = {".DS_Store", "__pycache__", ".git", "_previous"}

def all_skills():
    return sorted(p.name for p in SKILLS.iterdir()
                  if p.is_dir() and p.name not in SKIP_NAMES and (p / "SKILL.md").exists())

def _members(folder):
    out = []
    for root, dirs, fns in os.walk(folder):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_NAMES]
        for fn in sorted(fns):
            if fn in SKIP_NAMES or fn.endswith(".pyc"):
                continue
            full = pathlib.Path(root) / fn
            out.append((str(full.relative_to(folder)), full))
    return sorted(out)

def build_bytes(name):
    """Zip the CONTENTS of skills/<name>/ deterministically (fixed timestamp → byte-stable:
    the archive only changes when the skill's content changes, not on every rebuild)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, full in _members(SKILLS / name):
            zi = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, full.read_bytes())
    return buf.getvalue()

def content_sig(zbytes_or_path):
    """Hash of {entry-name -> bytes} inside an archive, ignoring zip metadata/timestamps,
    so 'changed?' compares real content not packaging noise."""
    try:
        zf = (zipfile.ZipFile(io.BytesIO(zbytes_or_path)) if isinstance(zbytes_or_path, bytes)
              else zipfile.ZipFile(zbytes_or_path))
    except (FileNotFoundError, zipfile.BadZipFile):
        return None
    h = hashlib.sha256()
    with zf as z:
        for n in sorted(z.namelist()):
            if n.endswith("/"):
                continue
            h.update(n.encode()); h.update(z.read(n))
    return h.hexdigest()

def skill_version(name):
    md = (SKILLS / name / "SKILL.md").read_text(errors="ignore")
    m = re.search(r"^version:\s*(.+)$", md, re.M)
    return m.group(1).strip() if m else ""

def main():
    args = [a for a in sys.argv[1:]]
    deliver = "--no-deliver" not in args
    args = [a for a in args if a != "--no-deliver"]

    if not args or args == ["--changed"]:
        targets = [n for n in all_skills()
                   if content_sig(build_bytes(n)) != content_sig(SKILLS / f"{n}.skill")]
        mode = "changed"
    elif args == ["--all"]:
        targets = all_skills(); mode = "all"
    else:
        for n in args:
            if not (SKILLS / n / "SKILL.md").exists():
                sys.exit(f"package-skill: no such skill '{n}' (looked in {SKILLS})")
        targets = args; mode = "named"

    if not targets:
        print("package-skill: nothing to package — every .skill matches its source.")
        return

    built = []
    for n in targets:
        (SKILLS / f"{n}.skill").write_bytes(build_bytes(n))
        built.append(n)
        print(f"  ✓ packaged {n}.skill")

    if not deliver:
        print(f"\npackage-skill: rebuilt {len(built)} archive(s); local delivery skipped (--no-deliver).")
        return
    if not DOWNLOADS.exists():
        print(f"\npackage-skill: rebuilt {len(built)} archive(s); no ~/Downloads here, skipped local delivery.")
        return

    DEST.mkdir(parents=True, exist_ok=True)
    for n in built:
        shutil.copy2(SKILLS / f"{n}.skill", DEST / f"{n}.skill")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# Skills to install — updated {stamp}", "",
             f"{len(built)} package(s) below are the CURRENT versions, rebuilt from source.",
             "Install each in the Cowork app's skill installer (same as before); each replaces",
             "its installed version. Once installed, this folder can be emptied.", ""]
    for n in sorted(built):
        v = skill_version(n)
        lines.append(f"- **{n}.skill**" + (f"  ({v})" if v else ""))
    (DEST / "_INSTALL-ME.md").write_text("\n".join(lines) + "\n")
    print(f"\npackage-skill: delivered {len(built)} package(s) → {DEST}")
    print(f"  ({mode} mode) — install from that folder in Cowork.")

if __name__ == "__main__":
    main()
