#!/usr/bin/env python3
"""
property-state-selftest.py — §7 meta-verification: prove each probe check on a known-GOOD
and a known-BAD case, so a buggy check can't pass silently. Run after editing the probe.
Exits non-zero if any assertion fails.
"""
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("pls", os.path.join(os.path.dirname(__file__), "property-live-state.py"))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

fails = []
def check(label, got, want):
    ok = got == want
    print(f"  [{'✓' if ok else '✗'}] {label}: got {got!r}" + ("" if ok else f"  EXPECTED {want!r}"))
    if not ok: fails.append(label)

print("dns_verdict — dead apex IP detection (the 2026-06-07 estate outage):")
check("retired IP 216.198.79.1 → down", p.dns_verdict({"dns": {"A": ["216.198.79.1"]}})[0], "down")
check("retired IP 76.76.21.21 → down", p.dns_verdict({"dns": {"A": ["76.76.21.21"]}})[0], "down")
check("live IP 216.150.1.1 → no verdict (None)", p.dns_verdict({"dns": {"A": ["216.150.1.1"]}})[0], None)

print("resolve_liveness — liveness from the DOMAIN, Vercel READY is context not override (post-2026-06-07):")
check("domain up → up", p.resolve_liveness({"up": True, "host": "vercel", "dns": {"A": ["216.150.1.1"]}}, None, "")[0], "up")
check("INCIDENT: dead apex IP + Vercel READY → DOWN (was falsely 'up' — hid the outage)",
      p.resolve_liveness({"up": None, "host": "vercel", "dns": {"A": ["216.198.79.1"]}}, {"state": "READY"}, "")[0], "down")
check("timeout + Vercel READY (valid IP) → DOWN (deploy READY ≠ domain serving; fault is DNS/edge)",
      p.resolve_liveness({"up": None, "host": "vercel", "dns": {"A": ["216.150.1.1"]}}, {"state": "READY"}, "")[0], "down")
check("timeout + no Vercel → down (unreachable, not a false 'unknown')", p.resolve_liveness({"up": None, "host": "x"}, None, "")[0], "down")
check("no domain on card + Vercel READY → up (app-only, nothing masked)", p.resolve_liveness(None, {"state": "READY"}, "")[0], "up")
check("real 4xx/5xx → down", p.resolve_liveness({"up": False, "host": "x", "status": 503}, None, "")[0], "down")
check("real down BUT status sunset → expected-down", p.resolve_liveness({"up": False, "host": "x"}, None, "sunset")[0], "expected-down")
check("unreachable BUT status archived → expected-down", p.resolve_liveness({"up": None, "host": "x"}, None, "archived")[0], "expected-down")
check("dead apex IP BUT status unpublished → expected-down (Leakbusters: intentional, not a false anomaly)", p.resolve_liveness({"up": None, "host": "x", "dns": {"A": ["76.76.21.21"]}}, {"state": "READY"}, "unpublished")[0], "expected-down")

print("drift_flags — repo-vs-deployed:")
check("repo == deployed → no drift", p.drift_flags("up", {"head": "abc1234"}, {"deployed": "abc1234"}, None, ""), [])
bad = p.drift_flags("up", {"head": "abc1234"}, {"deployed": "def5678"}, None, "")
check("repo != deployed → flags 'repo ahead'", any("repo ahead" in f for f in bad), True)

print("drift_flags — DOWN only on positive evidence:")
check("live 'down' → DOWN flag", any("DOWN" in f for f in p.drift_flags("down", None, None, None, "")), True)
check("live 'unknown' (genuine no-signal, e.g. no domain) → NO DOWN flag", any("DOWN" in f for f in p.drift_flags("unknown", None, None, None, "")), False)

print("drift_flags — host mismatch (Cloudflare-edge must NOT false-fire):")
mm = p.drift_flags("up", None, None, {"up": True, "host": "manus"}, "vercel")
check("real origin disagrees (live manus ≠ card vercel) → mismatch", any("host mismatch" in f for f in mm), True)
edge = p.drift_flags("up", None, None, {"up": True, "host": "cloudflare(edge)"}, "vercel")
check("cloudflare-edge vs card vercel → NO mismatch (edge is transparent)", any("host mismatch" in f for f in edge), False)
noreach = p.drift_flags("up", None, None, {"up": None, "host": "vercel"}, "manus")
check("origin unreachable → NO mismatch (don't guess)", any("host mismatch" in f for f in noreach), False)

print()
if fails:
    print(f"SELF-TEST FAILED — {len(fails)} check(s): {fails}"); sys.exit(1)
print(f"SELF-TEST PASSED — every probe check correct on known-good AND known-bad.")
