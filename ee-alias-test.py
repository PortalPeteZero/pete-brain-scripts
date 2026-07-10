#!/usr/bin/env python3
"""ee-alias-test.py — the EE alias regression harness (hardening plan P1).

Runs every phrase in the [[ee-alias-regression]] vault note through ee-facts.lookup and checks
the resolution. One command, exits non-zero on any failure — wired into the EE sign-off and the
weekly self-audit. Add a row to the note whenever a mis-resolution is found/fixed: a corrected
mistake becomes a permanent test.

Usage:  VAULT=/tmp/pbs python3 /tmp/pbs/ee-alias-test.py
"""
import os, sys, json, re, subprocess, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")

def load_probes():
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py",
                        "SELECT body FROM vault_notes WHERE slug='ee-alias-regression'"],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    body = json.loads(r.stdout)[0]["body"]
    probes = []
    for line in body.split("\n"):
        m = re.match(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$", line)
        if m and m.group(1) not in ("Phrase", "---"):
            phrase, exp = m.group(1), m.group(2)
            if not set(phrase) <= {"-", " "}:
                probes.append((phrase, exp))
    return probes

def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__); sys.exit(0)
    spec = importlib.util.spec_from_file_location("ef", f"{VAULT}/ee-facts.py")
    ef = importlib.util.module_from_spec(spec); spec.loader.exec_module(ef)
    probes = load_probes()
    if not probes:
        print("⛔ no probes loaded from ee-alias-regression"); sys.exit(2)
    fails = 0
    for phrase, exp in probes:
        res = ef.lookup(phrase)
        if exp == "NOMATCH":
            ok = res is None
            got = "no match" if res is None else (res.get("code") or "ambiguous")
        elif exp == "AMBIG":
            ok = bool(res) and res.get("ambiguous") is True
            got = "ambiguous" if ok else ("no match" if res is None else res.get("code"))
        else:
            codes = exp.split("/")
            ok = bool(res) and not res.get("ambiguous") and res.get("code") in codes
            got = "no match" if res is None else ("ambiguous" if res.get("ambiguous") else res.get("code"))
        print(f"{'PASS' if ok else 'FAIL'}  {phrase!r:45s} expected {exp:12s} got {got}")
        fails += 0 if ok else 1
    print(f"\n=== alias regression: {len(probes) - fails}/{len(probes)} pass ===")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
