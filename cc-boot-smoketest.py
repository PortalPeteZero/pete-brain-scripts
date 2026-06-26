#!/usr/bin/env python3
"""cc-boot-smoketest.py — verify every helper can resolve its credentials/paths after boot.

Catches the "vision-api class" bug: a helper that (a) references a secret file that wasn't
materialised under VAULT, or (b) hardcodes a path to the RETIRED local vault (-Second-Brain) or a
non-VAULT absolute secret path that won't resolve in a fresh /tmp/pbs boot.

Run after `pete-session-bootstrap.py`:  VAULT=/tmp/pbs python3 /tmp/pbs/cc-boot-smoketest.py
Exit code 0 = all clear; 1 = problems found (so it can gate a boot or run in CI/cron).
"""
import os, re, sys, glob

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.join(VAULT, "Library", "processes", "secrets")
scripts = sorted(p for p in glob.glob(os.path.join(VAULT, "*.py"))
                 if os.path.basename(p) != "cc-boot-smoketest.py")

SECREF = [re.compile(r'secrets/([A-Za-z0-9][A-Za-z0-9._-]+\.[A-Za-z0-9]+)'),
          re.compile(r'\{SEC\}/([A-Za-z0-9][A-Za-z0-9._-]+)')]
# retired / fragile hardcoded locations
STALE = re.compile(r'-Second-Brain|/Users/[^"\']*/Second Brain')

missing_secret = []   # (script, secret-file)
stale_path = []       # (script, snippet)
for s in scripts:
    name = os.path.basename(s)
    src = open(s, encoding="utf-8", errors="replace").read()
    refs = set()
    for pat in SECREF:
        refs.update(pat.findall(src))
    for r in refs:
        if not os.path.isfile(os.path.join(SEC, r)):
            missing_secret.append((name, r))
    for m in STALE.findall(src):
        stale_path.append((name, m))

ok = not (missing_secret or stale_path)
print(f"cc-boot-smoketest — {len(scripts)} helpers scanned, secrets dir: {SEC}")
print(f"  secrets present: {len(os.listdir(SEC)) if os.path.isdir(SEC) else 'DIR MISSING'}")
if stale_path:
    print(f"\n  ✗ STALE retired-vault path references ({len(stale_path)}):")
    for n, m in stale_path: print(f"     {n}: {m}")
if missing_secret:
    print(f"\n  ✗ helpers referencing a secret file NOT materialised ({len(missing_secret)}):")
    for n, r in sorted(set(missing_secret)): print(f"     {n}: secrets/{r}")
if ok:
    print("\n  ✓ all helpers resolve their secrets; no retired-vault paths")
sys.exit(0 if ok else 1)
