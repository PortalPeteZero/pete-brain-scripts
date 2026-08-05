#!/usr/bin/env python3
"""skill-install-parity.py — prove a skill is the SAME thing at every link in its chain.

A skill reaches a session through three hops, and each one has now been caught broken:

    repo folder            skills/<name>/            the thing you edit
        |  package-skill.py
    packaged archive       skills/<name>.skill       the thing that gets shipped
        |  delivery
    delivered package      ~/Downloads/cc-skills-to-install/<name>.skill
        |  install (human, in the app)
    installed copy         the plugin dir            THE THING THAT ACTUALLY LOADS

BOTH FAILURES BELOW HAPPENED ON 5 AUG 2026, INDEPENDENTLY, AND NEITHER WAS VISIBLE.

  * Link 3 (installed). The Claude Exchange steps were added to skills/brain/SKILL.md on 4 Aug:
    read the Jim channel at resume, sign off at close. On 5 Aug Pete asked "check chat for jims
    claude" — because no session had ever run them. The repo was right the whole time; the
    INSTALLED brain was 25 Jul, eleven days stale, and the harness loads the installed copy.
    Nine skills were stale, up to 25 days.
  * Link 1 (archive). Same morning, another session committed "pf-seminars: rebuild the packaged
    archive (was stranded at 1.3.0)" — source edited, archive left behind. Ship that archive and
    you install the old skill while the repo reads correct.

The lesson both share: editing a SKILL.md changes NOTHING on its own. Jim's Claude named the
identical class the same week about its own work — "the skill is AUTHORED, NOT INSTALLED — a
draft, not a capability."

Comparison is FULL FOLDER, not just SKILL.md. brain carries 16 files; a stale reference file is
just as capable of teaching a session the wrong thing as a stale SKILL.md.

Not a fixer, by design. The installed tree is a MANAGED directory whose manifest.json is rewritten
by the plugin sync, so hand-copying could be silently reverted — leaving everyone believing a fix
had landed when it had not, which is the exact failure this exists to catch. Archive and delivery
drift DO have a safe fix and it is printed: package-skill.py. Installing stays a human step.

(skill-drift-check.py is a different tool: it hunts dead references INSIDE a skill against live
tables. Nothing compared a skill to its own downstream copies until this.)

Usage:
  VAULT=/tmp/pbs python3 skill-install-parity.py            # full report
  VAULT=/tmp/pbs python3 skill-install-parity.py --quiet    # problems only
Exit 0 = every link matches for every skill.
"""
import os
import sys
import glob
import zipfile
import hashlib
import pathlib
import datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REPO = pathlib.Path(VAULT) / "skills"
DELIVERED = pathlib.Path.home() / "Downloads" / "cc-skills-to-install"
QUIET = "--quiet" in sys.argv

# Globbed, never hard-coded: the plugin and session UUIDs change between installs, and a
# hard-coded path would quietly report a clean sheet from a folder that no longer exists.
INSTALL_GLOBS = [
    "~/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/*/*/skills",
    "~/.claude/skills",
    "~/.claude/plugins/*/skills",
]


def _sha(b):
    return hashlib.sha1(b).hexdigest()


def folder_map(d: pathlib.Path):
    """{relative path: sha1} for every file under a skill folder."""
    return {str(p.relative_to(d)): _sha(p.read_bytes()) for p in d.rglob("*") if p.is_file()}


def zip_map(path: pathlib.Path):
    """{archive member: sha1}. package-skill.py puts SKILL.md at the archive ROOT, not under a
    folder, so members line up with folder_map keys directly."""
    with zipfile.ZipFile(path) as z:
        return {i.filename: _sha(z.read(i.filename)) for i in z.infolist() if not i.is_dir()}


def diff(a, b):
    """(missing_from_b, extra_in_b, content_differs) — full-folder, not just SKILL.md."""
    return (sorted(set(a) - set(b)), sorted(set(b) - set(a)),
            sorted(k for k in set(a) & set(b) if a[k] != b[k]))


def describe(d):
    missing, extra, changed = d
    bits = []
    if changed:
        bits.append(f"{len(changed)} file(s) differ ({', '.join(changed[:3])}"
                    + (", …" if len(changed) > 3 else "") + ")")
    if missing:
        bits.append(f"{len(missing)} missing ({', '.join(missing[:3])})")
    if extra:
        bits.append(f"{len(extra)} extra ({', '.join(extra[:3])})")
    return "; ".join(bits)


def installed_roots():
    out = []
    for g in INSTALL_GLOBS:
        for p in glob.glob(os.path.expanduser(g)):
            if os.path.isdir(p):
                out.append(pathlib.Path(p))
    return out


def main():
    if not REPO.is_dir():
        print(f"skill-install-parity: no repo skills dir at {REPO} — cannot compare (fail-open)")
        return 0

    roots = installed_roots()
    if not roots:
        # An empty result is NOT a pass. Say so, loudly: a silent zero here would read as clean.
        print("skill-install-parity: found NO installed skills directory. That is not a clean "
              "sheet — the comparison could not run. Check the harness paths in INSTALL_GLOBS.")
        return 1

    skills = sorted(p.parent for p in REPO.glob("*/SKILL.md"))
    problems = {"archive": [], "delivery": [], "installed": []}
    clean, notinstalled = [], []

    for src in skills:
        name = src.name
        on_disk = folder_map(src)
        issues = []

        # LINK 1 — repo folder vs its own packaged archive. The pf-seminars failure.
        arc = REPO / f"{name}.skill"
        if not arc.exists():
            problems["archive"].append((name, "no .skill archive in the repo at all"))
            issues.append("archive")
        else:
            d = diff(on_disk, zip_map(arc))
            if any(d):
                problems["archive"].append((name, describe(d)))
                issues.append("archive")

        # LINK 2 — repo archive vs the copy Pete installs from. package-skill.py's own docstring
        # records this drifting before ("Downloads held pre-repackage versions").
        dlv = DELIVERED / f"{name}.skill"
        if dlv.exists():
            d = diff(on_disk, zip_map(dlv))
            if any(d):
                problems["delivery"].append((name, describe(d)))
                issues.append("delivery")

        # LINK 3 — repo folder vs what the harness actually loaded. The brain failure.
        found = next((r / name for r in roots if (r / name / "SKILL.md").is_file()), None)
        if found is None:
            notinstalled.append(name)
        else:
            d = diff(on_disk, folder_map(found))
            if any(d):
                try:
                    age = (datetime.date.today() - datetime.date.fromtimestamp(
                        (found / "SKILL.md").stat().st_mtime)).days
                    age = f", installed copy {age}d old"
                except Exception:
                    age = ""
                problems["installed"].append((name, describe(d) + age))
                issues.append("installed")

        if not issues:
            clean.append(name)

    total = len(skills)
    print(f"skill-install-parity — {total} repo skill(s), {len(roots)} install location(s), "
          f"{len(clean)} clean end to end")

    if not QUIET:
        for n in clean:
            print(f"  ✅ {n}")

    for n, why in problems["installed"]:
        print(f"  ⛔ INSTALLED STALE: {n} — {why}")
    for n, why in problems["archive"]:
        print(f"  ⛔ ARCHIVE DRIFT:   {n} — packaged .skill does not match its source: {why}")
    for n, why in problems["delivery"]:
        print(f"  ⚠  DELIVERY DRIFT: {n} — the package in ~/Downloads is not the current source: {why}")
    for n in notinstalled:
        print(f"  ⚠  NOT INSTALLED:  {n} — in the repo, absent from every plugin dir "
              f"(may be served by another plugin, or repo-only)")

    repackage = sorted({n for n, _ in problems["archive"]} | {n for n, _ in problems["delivery"]}
                       | {n for n, _ in problems["installed"]})
    if repackage:
        print(f"\n  → repackage:  VAULT={VAULT} python3 {VAULT}/package-skill.py {' '.join(repackage)}")
    if problems["installed"]:
        print("  → then INSTALL the .skill file(s) from ~/Downloads/cc-skills-to-install/ in the app.")
        print("     Until that happens the session keeps running the OLD instructions — repackaging")
        print("     alone changes nothing about what loads.")

    return 1 if any(problems.values()) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Fail-open: a reporting bug must never stop a session starting.
        print(f"skill-install-parity: {e} (fail-open)", file=sys.stderr)
        sys.exit(0)
