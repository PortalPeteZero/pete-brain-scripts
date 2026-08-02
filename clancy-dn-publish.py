#!/usr/bin/env python3
"""clancy-dn-publish.py — publish EVERY Genny's Damage Depot page, in one command.

Pete, 1 Aug 2026: "i also want to check when your updating the DD you are updaintng the main DD
page, the incident, actions tab and dashboard along with thew new what the data tells us page".

He was right to check. The Depot is published by three different scripts, so on 1 Aug the 22
year pages were 54 minutes old, the hub was 3 hours old and the analysis page 2 hours old — all
of them behind a database that had just been re-ingested.

> Why does a database-driven page go stale at all? Because every page is a **static snapshot**.
> The script reads the database live AT BUILD TIME and writes finished HTML into
> `module_content`. That HTML then sits frozen until the script runs again. Nothing on this
> section refreshes itself and there is no cron (checked 1 Aug 2026).

So: one command, everything, in dependency order, and it reports the age of each page after.

  VAULT=/tmp/pbs python3 clancy-dn-publish.py              # publish the lot
  VAULT=/tmp/pbs python3 clancy-dn-publish.py --check      # report freshness, publish nothing
"""
import os, sys, json, time, argparse, subprocess, urllib.request, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))

# (label, argv, the module_key(s) it writes)
STEPS = [
    ("year pages — dashboard, incidents, actions, insights, per-damage (22 pages)",
     ["clancy-dn-pages.py", "--publish"], ["clancy-depotnet-damages"]),
    ("the hub — the Damage Depot front door",
     ["clancy-dn-hub.py", "--publish"], ["clancy-depotnet"]),
    ("what the data tells us — FY 2026/27",
     ["clancy-dn-analysis.py", "--publish", "--edition", "1",
      "--label", "what Depotnet holds today, before enrichment"],
     ["clancy-damage-analysis"], {"CLANCY_FY": "FY26/27"}),
    ("what the data tells us — FY 2025/26",
     ["clancy-dn-analysis.py", "--publish", "--edition", "1",
      "--label", "what Depotnet holds, before enrichment"],
     ["clancy-damage-analysis-2025-26"], {"CLANCY_FY": "FY25/26"}),
    ("unmapped damages",
     ["clancy-dn-unmapped.py", "--publish"], ["clancy-unmapped-damages"]),
    ("the reports library",
     ["clancy-dn-reports.py", "--publish"], ["clancy-damage-reports"]),
    # joined 2 Aug 2026 (edits-plan stage 1): the Genny & CAT section shares the Depot navbar,
    # so a nav change that skipped it stranded 13 live pages — including three this script is
    # now the ONLY generator for (the Chrome-era builders are gone).
    ("the Genny & CAT review section (13 pages)",
     ["clancy-dn-gc-pages.py", "--publish"], ["clancy-genny-cat-reviews"]),
    ("the glossary — the section's terms, from clancy_glossary",
     ["clancy-dn-glossary.py", "--publish"], ["clancy-damage-glossary"]),
]


def ages():
    keys = sorted({k for _, _, ks, *_ in STEPS for k in ks})
    q = ("module_content?select=module_key,updated_at&module_key=in.("
         + ",".join(f'"{k}"' for k in keys) + ")")
    req = urllib.request.Request(f"{_k['url']}/rest/v1/{q}",
        headers={"apikey": _k["service_role_key"],
                 "Authorization": f"Bearer {_k['service_role_key']}"})
    rows = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    now = datetime.datetime.now(datetime.timezone.utc)
    out = {}
    for r in rows:
        t = datetime.datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
        out[r["module_key"]] = round((now - t).total_seconds() / 60)
    return out


def report(title):
    a = ages()
    print(f"\n{title}")
    for _, _, keys, *_ in STEPS:
        for k in keys:
            m = a.get(k)
            print(f"   {k:34} " + (f"{m:>5} min old" if m is not None
                                   else "   never published"))
    spread = max(a.values()) - min(a.values()) if a else 0
    print(f"   spread between newest and oldest: {spread} min")
    return spread


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report freshness, publish nothing")
    a = ap.parse_args()
    if a.check:
        report("page freshness (nothing published):")
        return 0

    failed = []
    for i, (label, argv, _keys, *env) in enumerate(STEPS):
        extra = env[0] if env else {}
        # Pace the run. Each step fires tens of queries and Supabase throttles on volume, so six
        # heavy tools back to back reliably 429 the later ones - the FY25/26 analysis step failed
        # exactly this way on 2 Aug 2026 while every other page rebuilt, leaving the section
        # half-fresh. The per-request backoff is the safety net; this is what stops it being
        # needed. A few seconds between steps costs nothing on a publish that takes minutes.
        if i:
            time.sleep(8)
        print(f"\n=== {label}")
        r = subprocess.run(["python3", f"{VAULT}/{argv[0]}", *argv[1:]],
                           capture_output=True, text=True,
                           env={**os.environ, "VAULT": VAULT, **extra})
        tail = (r.stdout or r.stderr).strip().splitlines()
        for line in tail[-3:]:
            print("   " + line)
        if r.returncode:
            failed.append(label)
            print(f"   !! FAILED (exit {r.returncode})")

    report("published — page freshness now:")
    if failed:
        print(f"\n!! {len(failed)} step(s) failed: " + "; ".join(failed))
        return 1
    print("\nevery Depot page rebuilt from the current database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
