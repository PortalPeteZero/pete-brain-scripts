#!/usr/bin/env python3
"""skill-install-parity.py — prove the skills that ACTUALLY LOAD match the ones in the repo.

THE FAILURE THIS EXISTS FOR. On 4 Aug 2026 the Claude Exchange steps were added to
skills/brain/SKILL.md: read the Jim channel at resume (step 6b), sign off on it at close
(step 7d). On 5 Aug Pete asked "check chat for jims claude" — because the session had not.
The repo was right the whole time. The INSTALLED copy was 25 Jul, eleven days stale, and the
harness loads the installed copy, not the repo. So a step that existed, was correct, and was
committed had never once fired.

That is the same class Jim's Claude named the same week about its own work: "the skill is
AUTHORED, NOT INSTALLED — a draft, not a capability." Editing a SKILL.md changes nothing on
its own. Nothing in the system noticed for eleven days, because nothing was looking.

skill-drift-check.py already covers a DIFFERENT thing (dead references INSIDE a skill,
resolved against live tables). Neither it nor vault-check compares repo source to what the
harness actually loaded. This does exactly that and nothing else.

WHAT IT REPORTS, worst first:
  STALE         installed SKILL.md differs from the repo — the session is running old
                instructions RIGHT NOW. This is the one that bites, and it is silent.
  NOT INSTALLED in the repo, absent from every plugin dir. Lower severity on purpose: a
                skill can legitimately be served by a different plugin, or be repo-only.
                Reported so it is visible, never as a failure on its own.

Deliberately NOT a fixer. The installed tree is a MANAGED directory — its manifest.json
carries skillId/updatedAt per skill and is rewritten by the plugin sync. Hand-copying a file
in there could be reverted silently, which would leave everyone believing a fix had landed
when it had not — the exact failure this script exists to catch. The sanctioned route is
package-skill.py then install, and that ends with a human.

Usage:
  VAULT=/tmp/pbs python3 skill-install-parity.py            # report; exit 1 if anything STALE
  VAULT=/tmp/pbs python3 skill-install-parity.py --quiet    # only print problems
Exit 0 = every installed skill matches its repo source.
"""
import os
import sys
import glob
import pathlib

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REPO = pathlib.Path(VAULT) / "skills"
QUIET = "--quiet" in sys.argv

# Every place the harness may have unpacked skills. Globbed, never hard-coded: the plugin and
# session UUIDs change, and a hard-coded path would report a clean sheet from an empty folder.
INSTALL_GLOBS = [
    "~/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/*/*/skills",
    "~/.claude/skills",
    "~/.claude/plugins/*/skills",
]


def installed_dirs():
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

    roots = installed_dirs()
    if not roots:
        print("skill-install-parity: found NO installed skills directory. That is not a clean "
              "sheet — it means the comparison could not run. Check the harness paths.")
        return 1

    stale, missing, ok = [], [], []

    for src in sorted(REPO.glob("*/SKILL.md")):
        name = src.parent.name
        found = None
        for root in roots:
            cand = root / name / "SKILL.md"
            if cand.is_file():
                found = cand
                break
        if found is None:
            missing.append(name)
            continue
        if src.read_bytes() == found.read_bytes():
            ok.append(name)
        else:
            stale.append((name, found))

    print(f"skill-install-parity — {len(list(REPO.glob('*/SKILL.md')))} repo skill(s), "
          f"{len(roots)} install location(s), {len(ok)} matching")

    if not QUIET:
        for n in ok:
            print(f"  ✅ {n}")

    for n, path in stale:
        # The mtime is the useful number: it says how long the session has been running old rules.
        try:
            import datetime
            age = datetime.date.today() - datetime.date.fromtimestamp(path.stat().st_mtime)
            agestr = f", installed copy {age.days}d old"
        except Exception:
            agestr = ""
        print(f"  ⛔ STALE: {n} — installed SKILL.md differs from repo{agestr}")
        print(f"       loaded from: {str(path).replace(os.path.expanduser('~'), '~')}")

    for n in missing:
        print(f"  ⚠  NOT INSTALLED: {n} — in the repo, not in any plugin dir "
              f"(may be served by another plugin, or repo-only)")

    if stale:
        names = " ".join(n for n, _ in stale)
        print("\n  → THE SESSION IS RUNNING OLD INSTRUCTIONS. Repackage and install:")
        print(f"       VAULT={VAULT} python3 {VAULT}/package-skill.py {names}")
        print("     then install the .skill file(s) from ~/Downloads/cc-skills-to-install/")
        print("     Editing the repo does NOT change what loads. Only installing does.")
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Fail-open: a reporting bug must never block a session from starting.
        print(f"skill-install-parity: {e} (fail-open)", file=sys.stderr)
        sys.exit(0)
