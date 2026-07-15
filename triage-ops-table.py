#!/usr/bin/env python3
"""triage-ops-table.py -- the MECHANICAL propose-format gate for triage.

The recurring failure (Pete, 15 Jul 2026): after reading the inbox, Claude narrates a
prose summary instead of rendering the per-thread ops table with a proposed verb. A banner
in the skill is just words that can be skipped. THIS tool makes the table code-produced:

  1. It takes the round file + a judgments JSON (one entry per thread: ask + proposed verb
     [+ label/task/hand_to/engine]).
  2. It REFUSES unless EVERY round-file thread has a judgment (no thread may be silently
     dropped) and every row passes triage-validator (ask<->verb matrix).
  3. It PRINTS the canonical staged ops table (# . Ask . From/Subject . Action . Task . flags),
     batches of <=10.
  4. It WRITES the capture-ready batch to /tmp/triage-ops-<session_id>.json -- the ONLY file
     triage-log should apply. So the table is produced by code, and capture cannot happen
     without it having been rendered + validated first.

Judgments JSON: [{"thread_id":"..","ask":"reply","verb":"Reply","label":"..","task":{..},
                  "hand_to":"Jane","engine":"ee","note":".."}, ...]

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-ops-table.py <round_file> <judgments.json>
  (prints the staged ops table; writes /tmp/triage-ops-<session>.json; exit 0 ok / 2 invalid)
"""
import os, sys, json, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")

def _load(name, mod):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(VAULT, name))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def main():
    if len(sys.argv) < 3:
        print(__doc__); return 2
    round_file, judg_file = sys.argv[1], sys.argv[2]
    rnd = json.load(open(round_file))
    judg = json.load(open(judg_file))
    threads = {t["id"]: t for t in rnd["threads"]}
    by_tid = {j["thread_id"]: j for j in judg}

    # GATE 1: every round-file thread must carry a judgment (no silent drops)
    missing = [t["id"] for t in rnd["threads"] if t["id"] not in by_tid]
    if missing:
        print(f"BLOCKED: {len(missing)} round thread(s) have NO judgment — every thread needs a "
              f"proposed verb before the table renders:")
        for tid in missing:
            print(f"  · {threads[tid]['subject'][:60]}  ({tid})")
        return 2

    # GATE 2: validate every row through triage-validator (ask<->verb matrix)
    val = _load("triage-validator.py", "tv")
    ops = []
    for j in judg:
        t = threads.get(j["thread_id"])
        if not t:
            print(f"BLOCKED: judgment for unknown thread {j['thread_id']}"); return 2
        verb = j["verb"]
        if j.get("engine") and verb == "Route":
            verb_disp = f"Route {j['engine']}"
        elif j.get("hand_to") and verb.startswith("Hand"):
            verb_disp = f"Hand to {j['hand_to']}"
        elif j.get("label") and verb in ("File", "Keep", "Reply", "Task", "Route"):
            verb_disp = f"{verb} {j['label']}" if verb in ("File", "Keep") else verb
        else:
            verb_disp = verb
        # the ops Task cell is ONLY for Reply/Task verbs; a Hand-to's chase task rides the
        # Delegate cell (and still flows to the capture batch as create_task).
        task_cell = "Y" if (j.get("task") and (verb.startswith("Reply") or verb.startswith("Task"))) else None
        ops.append({"row": len(ops)+1, "ask": j["ask"], "action": verb_disp,
                    "task": task_cell,
                    "delegate": (j.get("hand_to") if verb.startswith("Hand") else None),
                    "_tid": j["thread_id"], "_subj": t["subject"], "_from": t.get("from",""),
                    "_flags": t.get("flags", []), "_j": j})
    try:
        val.validate_ops([{k: v for k, v in o.items() if not k.startswith("_")} for o in ops])
    except Exception as e:
        print(f"BLOCKED: ops table invalid — {e}"); return 2

    # RENDER the canonical staged table
    print(f"\nTRIAGE OPS TABLE — session {rnd['session_id'][:8]} · {len(ops)} threads · "
          f"proposed actions (say `go` / `except #N: <change>` / `cancel`)\n")
    print(f"{'#':>2}  {'Ask':10} {'From / Subject':44} {'Action':26} {'Task':4} Flags")
    print("-" * 100)
    for o in ops:
        subj = (o['_from'].split('<')[0].strip()[:16] + " · " + o['_subj'])[:44]
        fl = ",".join(o['_flags'])[:14]
        print(f"{o['row']:>2}  {o['ask']:10} {subj:44} {o['action'][:26]:26} "
              f"{(o['task'] or '-'):4} {fl}")

    # WRITE the capture-ready batch (the ONLY file triage-log applies)
    out = f"/tmp/triage-ops-{rnd['session_id']}.json"
    batch = []
    for o in ops:
        j = o["_j"]
        dec = {"thread_id": j["thread_id"], "message_id": threads[j["thread_id"]]["newest_message_id"],
               "sender": o["_from"], "session_id": rnd["session_id"],
               "final": {"ask": j["ask"], "verb": j["verb"], "label": j.get("label")},
               "decided_by": "pete"}
        if j.get("engine"): dec["engine"] = j["engine"]
        if j.get("task"): dec["create_task"] = j["task"]
        if j.get("note"): dec["body_gist"] = j["note"]
        batch.append(dec)
    json.dump(batch, open(out, "w"), indent=1)
    print(f"\n✓ validated + rendered. On `go`: capture with triage-log --in {out} --apply")
    return 0

if __name__ == "__main__":
    sys.exit(main())
