#!/usr/bin/env python3
"""
dns-card-fix.py — replace the dead apex IP (216.198.79.1) in property cards with each domain's
ACTUAL LIVE apex A record (dug fresh from 8.8.8.8 — not assumed, not from vault notes). Exact-token
match only (won't touch 216.198.79.13x microsite IPs). Dry-run by default; flags + skips any domain
whose live A doesn't come back as the expected Vercel pair rather than writing a guess.
  python3 dns-card-fix.py            # DRY-RUN
  python3 dns-card-fix.py --apply
"""
import re, sys, os, subprocess
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

APPLY = "--apply" in sys.argv
VAULT = VAULT
EXPECT = {"216.150.1.1", "216.150.16.1"}          # the live-verified Vercel apex pair
DEAD = re.compile(r"(?<!\d)216\.198\.79\.1(?!\d)")  # exact token, never .13x

CARDS = {
    "Properties/The Leaky Finders Website/README.md": "theleakyfinders.es",
    "Properties/Pipebusters Lanzarote/README.md":     "pipebusterslanzarote.com",
    "Properties/LeakGuard Lanzarote/README.md":       "leakguardlanzarote.com",
    "Properties/Canary Detect Main Website/README.md":"canary-detect.com",
    "Properties/The Leaky Finders Ledger/README.md":  "leaky-ledger.com",
    "Properties/Leak Guard CRM/README.md":            "leakguard-manager.com",
    "Properties/O'Connor's Irish Bar/README.md":      "oconnors.bar",
    "Properties/Sygma Solutions Website/README.md":   "sygma-solutions.com",
    "Properties/Sygma Portal CRM/README.md":          "sygmaportal.com",
}

def dig_live(domain):
    try:
        out = subprocess.run(["dig", "+short", "+time=3", "A", domain, "@8.8.8.8"],
                             capture_output=True, text=True, timeout=8).stdout.splitlines()
        return sorted(l.strip() for l in out if re.match(r"^\d+\.\d+\.\d+\.\d+$", l.strip()))
    except Exception:
        return []

def main():
    print(("APPLY" if APPLY else "DRY-RUN") + " — repoint card apex IP to each domain's LIVE A record\n" + "=" * 80)
    changed = skipped = 0
    for rel, domain in CARDS.items():
        path = os.path.join(VAULT, rel)
        if not os.path.exists(path):
            print(f"  ⚠ MISSING {rel}"); continue
        live = dig_live(domain)
        live_set = set(live)
        repl = ", ".join(live)
        raw = open(path, encoding="utf-8").read()
        hits = len(DEAD.findall(raw))
        if not hits:
            print(f"  · {domain:26} no dead-IP token (already clean)"); continue
        # SAFETY: only write if the live result is exactly the expected Vercel pair — never guess
        if live_set != EXPECT:
            print(f"  ❌ {domain:26} live A = {live or 'NONE'} ≠ expected {sorted(EXPECT)} — SKIPPED (not guessing)")
            skipped += 1; continue
        new = DEAD.sub(repl, raw)
        for ln in raw.splitlines():
            if DEAD.search(ln):
                print(f"  ✏ {domain:26} | {ln.strip()[:70]}")
                print(f"     {'→ ' + DEAD.sub(repl, ln).strip()[:74]}")
        if APPLY:
            open(path, "w", encoding="utf-8").write(new)
        changed += 1
    print("=" * 80)
    print(f"{'Applied' if APPLY else 'DRY-RUN'}: {changed} card(s) {'fixed' if APPLY else 'to fix'}, {skipped} skipped (live mismatch).")
    if not APPLY:
        print("Re-run with --apply to write.")

if __name__ == "__main__":
    main()