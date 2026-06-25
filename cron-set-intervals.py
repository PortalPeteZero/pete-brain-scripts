#!/usr/bin/env python3
"""cron-set-intervals.py — set each cron's expected_interval_hours from its schedule, so the
degraded-systems banner + drift-check can flag a cron that's genuinely OVERDUE (ran, but hasn't
run again within its expected window) or FAILED — without false-flagging crons that simply haven't
had a scheduled run yet.

Run-stamping itself needs nothing: every Railway service runs via railway-bootstrap.py, which
stamps last_run_at/last_status on each run. This only sets the "how often should it run" metadata.

Idempotent — safe to re-run (the drift-check calls it weekly to pick up schedule changes).
"""
import json, urllib.request, urllib.parse, re, os
from pathlib import Path

VAULT = os.environ.get("VAULT", "/tmp/pbs")
CC = json.load(open(Path(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json")))
H = {"apikey": CC['service_role_key'], "Authorization": f"Bearer {CC['service_role_key']}", "Content-Type": "application/json"}

def cc_get(p):
    return json.loads(urllib.request.urlopen(urllib.request.Request(f"{CC['url'].rstrip('/')}/rest/v1/{p}", headers=H)).read())

def cc_patch(p, body):
    urllib.request.urlopen(urllib.request.Request(f"{CC['url'].rstrip('/')}/rest/v1/{p}",
        data=json.dumps(body).encode(), headers={**H, "Prefer": "return=minimal"}, method="PATCH"), timeout=30)

def expected_interval_hours(sched):
    """Approx the max expected gap (hours) between fires from a 5-field cron expression."""
    if not sched: return None
    f = sched.split()
    if len(f) < 5: return None
    mn, hr, dom, mon, dow = f[:5]
    m = re.match(r"\*/(\d+)", mn)
    if m: return round(int(m.group(1)) / 60, 2)
    if hr == "*": return 1
    m = re.match(r"\*/(\d+)", hr)
    if m: return int(m.group(1))
    if "," in hr:
        hrs = sorted(int(x) for x in hr.split(",") if x.isdigit())
        if len(hrs) >= 2:
            gaps = [hrs[i + 1] - hrs[i] for i in range(len(hrs) - 1)] + [24 - hrs[-1] + hrs[0]]
            return max(gaps)
    if dow not in ("*", "?"): return 168     # weekly
    if dom not in ("*", "?"): return 744     # monthly
    return 24                                 # daily

crons = cc_get("crons?select=key,schedule")
n = 0
for c in crons:
    eih = expected_interval_hours(c.get("schedule"))
    if eih is not None:
        cc_patch(f"crons?key=eq.{urllib.parse.quote(c['key'])}", {"expected_interval_hours": eih})
        n += 1
print(f"set expected_interval_hours on {n}/{len(crons)} crons")
