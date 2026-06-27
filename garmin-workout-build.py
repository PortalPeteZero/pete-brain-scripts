import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")
#!/usr/bin/env python3
"""Garmin Connect workout builder — turn a simple step spec into a structured
workout, upload it to Garmin Connect, and optionally schedule it on a date.

Companion to `garmin-api.py` (which is read-only). This is the WRITE path for
workouts. Wraps the same `python-garminconnect` client + cached tokens.

WHY: Pete pastes a session in plain shorthand; we convert it to Garmin's
structured-workout JSON and push it so it syncs to his watch. First used
2026-05-27 (indoor bike). See [[garmin-workout-push]] for the shorthand→Garmin
mapping rules and worked examples.

------------------------------------------------------------------------------
SPEC FORMAT (Python list, or JSON file for the CLI)

Each item is a step dict. Two shapes:

  Simple step:
    {"kind": "warmup|interval|recovery|rest|cooldown",
     "dur": 600,                 # seconds  (or use "dist_m" / "lap": true)
     "label": "Comfortable",     # free text shown on the watch
     "target": {...}}            # optional, omit for no target

  Repeat group:
    {"repeat": 4, "steps": [ <simple steps> ]}

END CONDITION (pick one per simple step):
  "dur": <seconds>   |   "dist_m": <metres>   |   "lap": true (press lap to end)

TARGETS (optional; default no target):
  {"type": "power",   "low": 200, "high": 250}      # watts
  {"type": "hr",      "low": 130, "high": 150}      # bpm
  {"type": "hr.zone", "zone": 2}                     # HR zone 1-5
  {"type": "cadence", "low": 50,  "high": 70}        # rpm/spm
  {"type": "pace",    "low": 3.0, "high": 3.3}       # m/s
  {"type": "power.zone"/"pace.zone"/"speed.zone", "zone": N}

SPORT: "cycling" (default) | "running" | "swimming" | "walking" | "hiking"

------------------------------------------------------------------------------
LIBRARY USAGE
  from importlib import util
  m = util.spec_from_file_location(...)            # or sys.path insert
  wb = GarminWorkoutBuilder()
  wid = wb.push(name="Turbo 27 May", sport="cycling", spec=steps,
                schedule_date="2026-05-27")        # schedule_date optional

  # dry run (no upload): wb.build(name, sport, spec) -> dict

CLI
  python3 garmin-workout-build.py spec.json --name "Turbo 27 May" \\
          --sport cycling --schedule 2026-05-27
  python3 garmin-workout-build.py spec.json --name X --dry-run   # print JSON only
  python3 garmin-workout-build.py --list                          # recent workouts
  python3 garmin-workout-build.py --delete <workoutId>            # remove one
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# Post-migration the helpers are flat at $VAULT/; keep the old nested path as a
# fallback so this resolves under either layout.
_GARMIN_API = Path(f"{VAULT}/garmin-api.py")
if not _GARMIN_API.exists():
    _GARMIN_API = Path(f"{VAULT}/Library/processes/scripts/garmin-api.py")


def _load_client():
    spec = importlib.util.spec_from_file_location("garmin_api", _GARMIN_API)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GarminAPI().client


SPORTS = {
    "cycling":   {"sportTypeId": 2, "sportTypeKey": "cycling",   "displayOrder": 2},
    "running":   {"sportTypeId": 1, "sportTypeKey": "running",   "displayOrder": 1},
    "swimming":  {"sportTypeId": 4, "sportTypeKey": "swimming",  "displayOrder": 4},
    "walking":   {"sportTypeId": 3, "sportTypeKey": "walking",   "displayOrder": 3},
    "hiking":    {"sportTypeId": 3, "sportTypeKey": "hiking",    "displayOrder": 3},
}
STEP_TYPES = {
    "warmup":   {"stepTypeId": 1, "stepTypeKey": "warmup",   "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest":     {"stepTypeId": 5, "stepTypeKey": "rest",     "displayOrder": 5},
    "repeat":   {"stepTypeId": 6, "stepTypeKey": "repeat",   "displayOrder": 6},
}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
TARGETS = {
    "power":      {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone",   "displayOrder": 2},
    "power.zone": {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone",   "displayOrder": 2},
    "cadence":    {"workoutTargetTypeId": 3, "workoutTargetTypeKey": "cadence.zone", "displayOrder": 3},
    "hr":         {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4},
    "hr.zone":    {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4},
    "pace":       {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone",    "displayOrder": 6},
    "pace.zone":  {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone",    "displayOrder": 6},
    "speed.zone": {"workoutTargetTypeId": 5, "workoutTargetTypeKey": "speed.zone",   "displayOrder": 5},
}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time",       "displayOrder": 2, "displayable": True}
END_DIST = {"conditionTypeId": 3, "conditionTypeKey": "distance",   "displayOrder": 3, "displayable": True}
END_LAP  = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
END_ITER = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}


class GarminWorkoutBuilder:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = _load_client()
        return self._client

    # ---- step construction -------------------------------------------------
    def _end(self, s):
        if s.get("lap"):
            return END_LAP, None
        if "dist_m" in s:
            return END_DIST, float(s["dist_m"])
        return END_TIME, float(s.get("dur", 0))

    def _target(self, s):
        t = s.get("target")
        if not t:
            return NO_TARGET, None, None, None
        tt = TARGETS.get(t["type"], NO_TARGET)
        if "zone" in t:
            return tt, None, None, int(t["zone"])
        return tt, t.get("low"), t.get("high"), None

    def _simple(self, s, order):
        end, endval = self._end(s)
        tt, lo, hi, zone = self._target(s)
        return {
            "type": "ExecutableStepDTO", "stepId": order, "stepOrder": order,
            "stepType": STEP_TYPES[s["kind"]], "childStepId": None,
            "endCondition": end, "endConditionValue": endval, "endConditionCompare": None,
            "targetType": tt, "targetValueOne": lo, "targetValueTwo": hi, "zoneNumber": zone,
            "description": s.get("label"),
        }

    def build(self, name, sport, spec):
        sportdto = SPORTS[sport]
        steps, order = [], 0
        for item in spec:
            if "repeat" in item:
                order += 1
                grp_order = order
                children = []
                for child in item["steps"]:
                    order += 1
                    children.append(self._simple(child, order))
                steps.append({
                    "type": "RepeatGroupDTO", "stepId": grp_order, "stepOrder": grp_order,
                    "stepType": STEP_TYPES["repeat"], "childStepId": 1,
                    "numberOfIterations": int(item["repeat"]), "smartRepeat": False,
                    "endCondition": END_ITER, "endConditionValue": float(item["repeat"]),
                    "workoutSteps": children, "skipLastRestStep": False,
                })
            else:
                order += 1
                steps.append(self._simple(item, order))
        return {
            "sportType": sportdto, "workoutName": name,
            "workoutSegments": [{"segmentOrder": 1, "sportType": sportdto, "workoutSteps": steps}],
        }

    # ---- push / schedule / manage ------------------------------------------
    def push(self, name, sport, spec, schedule_date=None):
        wo = self.build(name, sport, spec)
        res = self.client.upload_workout(wo)
        wid = res.get("workoutId") if isinstance(res, dict) else None
        out = {"workoutId": wid, "name": name}
        if wid and schedule_date:
            sch = self.client.schedule_workout(wid, schedule_date)
            out["scheduleId"] = sch.get("workoutScheduleId") if isinstance(sch, dict) else None
            out["scheduled_for"] = schedule_date
        # Record the built session in the CC so the training-feedback loop can reference what was
        # prescribed when feedback is logged later (planned-vs-delivered). CC-only, non-fatal.
        if wid:
            _record_planned_session(wid, name, sport, spec, schedule_date)
        return out

    def verify(self, wid):
        back = self.client.get_workout_by_id(wid)
        segs = back.get("workoutSegments", [])
        return {"name": back.get("workoutName"),
                "steps": len(segs[0]["workoutSteps"]) if segs else 0}


def _record_planned_session(wid, name, sport, spec, schedule_date):
    """Upsert the built session into the CC `health_planned_session` table so the
    [[training-feedback-loop]] can look up what was prescribed when feedback is logged later.
    Keyed on the scheduled (activity) date. CC-only, Drive-free, non-fatal."""
    if not schedule_date:
        return  # no activity date → the feedback loop can't key to it; nothing to record
    import urllib.request
    try:
        url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
        if not (url and key):
            kp = Path(VAULT) / "Library/processes/secrets/command-centre-supabase-keys.json"
            kd = json.loads(kp.read_text()); url, key = kd["url"], kd["service_role_key"]
        base = url.rstrip("/")
        hdr = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
        q = base + f"/rest/v1/health_planned_session?date=eq.{schedule_date}&select=seq"
        existing = json.loads(urllib.request.urlopen(urllib.request.Request(q, headers=hdr), timeout=20).read())
        seq = max([r["seq"] for r in existing], default=0) + 1
        row = {"date": schedule_date, "seq": seq, "source": "built-workout",
               "spec": {"name": name, "sport": sport, "steps": spec},
               "garmin_workout_id": wid, "scheduled_date": schedule_date}
        urllib.request.urlopen(urllib.request.Request(base + "/rest/v1/health_planned_session",
            data=json.dumps(row).encode(), headers={**hdr, "Prefer": "return=minimal"}, method="POST"), timeout=20)
        print(f"  CC: planned-session recorded ({schedule_date} seq {seq}, workout {wid})")
    except Exception as e:
        print(f"  (planned-session CC record skipped: {e})", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?", help="JSON file: list of step dicts")
    ap.add_argument("--name", default="Workout")
    ap.add_argument("--sport", default="cycling", choices=list(SPORTS))
    ap.add_argument("--schedule", help="YYYY-MM-DD to schedule on")
    ap.add_argument("--dry-run", action="store_true", help="print JSON, do not upload")
    ap.add_argument("--list", action="store_true", help="list recent workouts")
    ap.add_argument("--delete", help="delete a workout by id")
    a = ap.parse_args()
    wb = GarminWorkoutBuilder()

    if a.list:
        print(json.dumps(wb.client.get_workouts(0, 15), indent=2)[:4000]); return
    if a.delete:
        wb.client.delete_workout(a.delete); print(f"deleted {a.delete}"); return

    spec = json.loads(Path(a.spec).read_text())
    if a.dry_run:
        print(json.dumps(wb.build(a.name, a.sport, spec), indent=2)); return
    out = wb.push(a.name, a.sport, spec, a.schedule)
    print("PUSHED:", json.dumps(out))
    if out.get("workoutId"):
        print("VERIFY:", json.dumps(wb.verify(out["workoutId"])))


if __name__ == "__main__":
    main()