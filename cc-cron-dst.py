#!/usr/bin/env python3
"""cc-cron-dst.py — daily DST self-heal for Lanzarote-local cron schedules.

Atlantic/Canary is UTC+0 in winter (WET) and UTC+1 in summer (WEST). Every cron's schedule is authored
LOCAL in its # CRON-META and stored as schedule_local; the UTC that Railway actually fires (schedule)
is only correct for the current season. When the offset flips (last Sun Mar → summer, last Sun Oct →
winter) every local schedule would otherwise mis-fire by an hour for ~6 months. This runs daily and:
  • first run → records the current offset as the baseline, changes nothing;
  • offset unchanged → no-op;
  • offset flipped → re-resolves schedule_local → UTC for every enabled cron and updates Railway +
    public.crons, so the season change heals itself with zero manual work.

Offset-flip-gated (not per-cron diff) so a one-off schedule_local quirk can never trigger a spurious change.
"""
# CRON-META
# what: Daily DST self-heal — when the Canary->UTC offset flips, re-resolve every cron's schedule_local to UTC on Railway
# why: Atlantic/Canary is UTC+0 winter / +1 summer; without this every local schedule mis-fires by an hour for ~6 months after each DST boundary
# reads: public.crons (schedule_local, schedule), cron_state (last offset)
# writes: Railway cronSchedule + public.crons.schedule (only on a DST flip)
# entity: command-centre
# schedule: 0 4 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import importlib.util, os, sys

HERE = os.environ.get("VAULT", os.path.dirname(os.path.abspath(__file__)))
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m); return m
cc = _load("cccron", f"{HERE}/cc-cron.py")
cron_state = _load("cron_state", f"{HERE}/cron_state.py")
from cron_tz import offset_hours

TZ = "Atlantic/Canary"
cur = offset_hours(TZ)
last = cron_state.get_state("cc-cron-dst", "last_offset", default=None)

if last is None:
    cron_state.set_state("cc-cron-dst", "last_offset", cur)
    print(f"cc-cron-dst: baseline offset UTC+{cur} recorded — no schedule changes on first run")
    sys.exit(0)
if last == cur:
    print(f"cc-cron-dst: offset unchanged (UTC+{cur}) — no-op")
    sys.exit(0)

print(f"cc-cron-dst: DST FLIP — offset UTC+{last} → UTC+{cur}; re-deriving every local schedule")
rows = cc.sb("GET", "crons?select=key,host,schedule,schedule_local,host_ref,timezone&host=eq.railway&enabled=eq.true")
changed = checked = 0
for r in rows:
    local = r.get("schedule_local")
    if not local:
        continue                                  # services / no local schedule
    checked += 1
    tz = r.get("timezone") or TZ
    utc, off, cross = cc.local_to_utc(local, tz)
    if utc == r.get("schedule"):
        continue
    sid = r.get("host_ref") or cc.find_service(r["key"])
    if not sid:
        print(f"  ! {r['key']}: no Railway service"); continue
    cc.set_instance(sid, {"cronSchedule": utc})
    cc.write_cron({"key": r["key"], "schedule": utc, "updated_at": cc.now_iso()})
    cc.log_event(r["key"], "schedule-changed", f"DST re-derive (UTC+{last}→UTC+{cur}) → {utc} (local {local})")
    print(f"  ✓ {r['key']}: {r.get('schedule')} → {utc}  (local {local})")
    changed += 1
cron_state.set_state("cc-cron-dst", "last_offset", cur)
print(f"cc-cron-dst: re-derived {changed}/{checked} crons for the UTC+{cur} season")
