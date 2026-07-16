#!/usr/bin/env python3
"""triage-log.py -- the Triage Engine capture-on-decision tool (P2; the te-log twin).

Captures every triage decision as a triage_decisions ledger row and executes the decision's
side-effects as a TRIPLE-WRITE with a loud post-check:

  1. Gmail        -- label / archive per the final verb (the verb->primitive map in the
                     inbox-triage skill; Reply adds Replies, Task adds filing label only, ...)
  2. public.tasks -- close/update on evidence; CREATE only when the payload carries an explicit
                     Pete-confirmed task (interactive sessions only -- the standing rule forbids
                     AUTO-creation, not Pete-directed creation; the created task's id is recorded
                     on the decision row's task_id and the post-check re-reads it)
  3. triage_decisions -- the ledger row (proposed vs final, the learning substrate)

POST-CHECK: re-reads all three systems, prints one ✓/✗ line each, EXITS NON-ZERO on any ✗
(the te-log P2 semantics -- never "reported success on failed capture").

DRY-RUN BY DEFAULT -- nothing mutates without --apply. --manifest <path> appends one JSON line
per side-effect so any run is reversible (the te-log pattern).

IDEMPOTENT on the triggering Gmail message_id: a re-run of the same payload is a FULL NO-OP for
any row that is Pete-decided, overridden, applied, or carries a send_status; only a row still in
the pending-proposal state may be updated (the ledger's re-run semantics).

WRITE-ORDER RULE: the ledger row goes to 'applying' BEFORE any Gmail mutation, then flips to
'applied' -- record before action, at every mutating level.

Payload (one decision or a list):
{
  "thread_id": "...", "message_id": "...", "sender": "who@dom",
  "proposed": {"ask": "...", "verb": "...", "label": "...", "project": "...", "priority": "..."},
  "final":    {"ask": "...", "verb": "File", "label": "Receipts", "project": null, "priority": null},
  "decided_by": "pete",                     # pete | cron-proposed | cron-auto
  "overridden": false, "override_reason": ["wrong_label"],   # required if overridden
  "create_task": {"name": "...", "priority": "P2", "entity_slug": "...", "project_slug": "...",
                  "notes": "..."},          # ONLY on an explicit Pete confirmation
  "close_task_id": "<uuid>"                 # close-on-evidence
}

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-log.py --in decisions.json            # dry run
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-log.py --in decisions.json --apply
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-log.py --demo                         # P2 gate
"""
import os, sys, json, datetime as dt

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")


def a(v):  # sql text[] literal
    return "ARRAY[" + ",".join("'" + tl.esc(x) + "'" for x in v) + "]::text[]" if v else "NULL"


def q(v):  # nullable quoted
    return "NULL" if v is None else "'" + tl.esc(v) + "'"


def existing_row(message_id):
    rows = tl.cc_sql(f"SELECT * FROM triage_decisions WHERE message_id='{tl.esc(message_id)}'")
    return rows[0] if rows else None


def row_is_pending(row):
    return (row["decided_by"] in ("cron-proposed", "cron-auto") and not row["overridden"]
            and row["apply_status"] is None and row["send_status"] is None)


def bank_exemplar(dec):
    """On an override, upsert a triage_cases exemplar -- the learning substrate the brain reads.
    Lives in triage_cases (NOT on the mutable ledger row), so a later re-decision can never
    unlearn it. Keyed on the source message_id (a second correction updates the same case)."""
    mid = dec["message_id"]
    fin = dec.get("final") or {}
    reason = dec.get("correction_reason") or (dec.get("override_reason") or [None])[0]
    payload = {"ask": fin.get("ask"), "verb": fin.get("verb"), "label": fin.get("label"),
               "project": fin.get("project"), "priority": fin.get("priority"), "reason": reason,
               "sender": dec.get("sender"), "subject_gist": dec.get("subject_gist"),
               "body_gist": dec.get("body_gist")}
    pj = "'" + tl.esc(json.dumps(payload)) + "'::jsonb"
    if tl.cc_sql(f"SELECT id FROM triage_cases WHERE source_message_id='{tl.esc(mid)}'"):
        tl.cc_sql(f"UPDATE triage_cases SET payload={pj}, type='content', sender={q(dec.get('sender'))}, "
                  f"subject_gist={q(dec.get('subject_gist'))}, body_gist={q(dec.get('body_gist'))}, "
                  f"active=true WHERE source_message_id='{tl.esc(mid)}'")
    else:
        tl.cc_sql("INSERT INTO triage_cases (type, sender, subject_gist, body_gist, payload, source_message_id) "
                  f"VALUES ('content', {q(dec.get('sender'))}, {q(dec.get('subject_gist'))}, "
                  f"{q(dec.get('body_gist'))}, {pj}, {q(mid)})")


def capture_walker(dec, apply=False, manifest=None):
    """Append-only Replies-tray event row: one row per (thread, outcome), synthetic timestamped
    message_id (collision-proof under UNIQUE(message_id)); excluded from learning metrics."""
    outcome = dec.get("outcome") or "defer"
    tid = dec["thread_id"]
    syn = dec.get("message_id") or f"{tid}:walker:{outcome}:{dt.datetime.now(dt.timezone.utc).isoformat()}"
    lines = [f"  walker[{outcome}] {tid[:20]}…"]
    if not apply:
        lines.append("  DRY would append walker row")
        return True, lines
    tl.cc_sql("INSERT INTO triage_decisions (thread_id, message_id, sender, decided_by, action, "
              "outcome, parent_id, apply_status, applied_at) VALUES ("
              f"{q(tid)}, {q(syn)}, {q(dec.get('sender'))}, 'pete', 'walker', {q(outcome)}, "
              f"{q(dec.get('parent_id'))}, 'applied', now())")
    if manifest:
        manifest.write(json.dumps({"step": "walker", "thread_id": tid, "outcome": outcome}) + "\n")
    if outcome in ("send", "de-tray", "already-done") and not dec.get("no_gmail"):
        try:
            g = tl.gmail()
            labels = {l["name"]: l["id"] for l in g.list_labels()}
            g.modify_thread(tid, remove=[labels.get("Replies", "Replies")])
        except Exception as e:
            lines.append(f"  ✗ Gmail strip Replies: {e}")
            return False, lines
    lines.append("  ✓ walker row appended")
    return True, lines


def capture(dec, apply=False, manifest=None):
    """Process one decision. Returns (ok, lines)."""
    lines = []
    # Walker event rows (Replies-tray send/de-tray/already-done/defer) are append-only,
    # keyed on their own synthetic message_id -- they never touch the no-op / re-decision path.
    if dec.get("action") == "walker":
        return capture_walker(dec, apply, manifest)
    mid = dec["message_id"]
    fin = dec.get("final") or {}
    pro = dec.get("proposed") or {}

    row = existing_row(mid)
    if row and not row_is_pending(row):
        # v6 cross-round re-decision: the SAME message re-triaged in a later session. If the
        # disposition is unchanged it is a true no-op; if it changed, UPDATE the row in place and
        # re-execute the verb (no carry-forward, no new round entity -- message_id IS the key).
        same = (row.get("final_verb") == fin.get("verb")
                and row.get("final_label") == fin.get("label")
                and row.get("final_ask") == fin.get("ask"))
        if same:
            lines.append(f"  = {mid[:24]}… unchanged re-decision — FULL NO-OP")
            return True, lines
        lines.append(f"  ↻ {mid[:24]}… re-decision ({row.get('final_verb')}→{fin.get('verb')}) — updating in place")

    if not apply:
        lines.append(f"  DRY {mid[:24]}… would write decision row + verb '{fin.get('verb')}' "
                     f"label '{fin.get('label')}'" +
                     (" + CREATE task (Pete-confirmed)" if dec.get("create_task") else "") +
                     (f" + close task {dec.get('close_task_id')}" if dec.get("close_task_id") else ""))
        return True, lines

    # guards on auto rows: never create a task; never send here
    if dec.get("decided_by") in ("cron-proposed", "cron-auto") and dec.get("create_task"):
        lines.append("  ✗ REFUSED: create_task on an auto row — the never-auto-create rule")
        return False, lines

    fact = tl.match_fact(dec.get("sender") or "")
    fact_id = q(fact["id"]) if fact else "NULL"

    # 1) ledger row FIRST (write-order rule): applying
    ov = bool(dec.get("overridden"))
    # lint bank: the payload's lint verdict MUST land on the row (ledger spec; found
    # dropped 10 Jul 2026 by triage-health goal 3 — 9 applied auto rows, NULL lint)
    lint_rep = dec.get("lint_report")
    lint_passed = dec.get("lint_passed", (lint_rep or {}).get("passed") if isinstance(lint_rep, dict) else None)
    lp = "NULL" if lint_passed is None else ("true" if lint_passed else "false")
    lr = "NULL" if lint_rep is None else "'" + tl.esc(json.dumps(lint_rep)) + "'::jsonb"
    # v6 columns (read-in-full / learning): session scope + read-proof + partial-content mark
    sid = q(dec.get("session_id"))
    bq = q(dec.get("body_quote"))
    sg = q(dec.get("subject_gist"))
    bg = q(dec.get("body_gist"))
    cr = q(dec.get("correction_reason"))
    pc = "true" if dec.get("partial_content") else "false"
    eng = q(dec.get("engine"))
    if row:  # pending proposal OR a cross-round re-decision being finalised
        tl.cc_sql("UPDATE triage_decisions SET "
                  f"final_ask={q(fin.get('ask'))}, final_verb={q(fin.get('verb'))}, "
                  f"final_label={q(fin.get('label'))}, final_project={q(fin.get('project'))}, "
                  f"final_priority={q(fin.get('priority'))}, decided_by='pete', "
                  f"overridden={'true' if ov else 'false'}, "
                  f"overridden_at={'now()' if ov else 'NULL'}, "
                  f"override_reason={a(dec.get('override_reason'))}, "
                  f"lint_passed={lp}, lint_report={lr}, basis_refs={a(dec.get('basis_refs'))}, "
                  f"session_id={sid}, body_quote={bq}, subject_gist={sg}, body_gist={bg}, "
                  f"correction_reason={cr}, partial_content={pc}, engine={eng}, "
                  f"apply_status='applying', decided_at=now() WHERE message_id='{tl.esc(mid)}'")
    else:
        tl.cc_sql("INSERT INTO triage_decisions (thread_id, sender, message_id, fact_id, "
                  "proposed_ask, proposed_verb, proposed_label, proposed_project, proposed_priority, "
                  "final_ask, final_verb, final_label, final_project, final_priority, "
                  "overridden, overridden_at, override_reason, decided_by, "
                  "lint_passed, lint_report, basis_refs, session_id, body_quote, subject_gist, "
                  "body_gist, correction_reason, partial_content, engine, apply_status) VALUES ("
                  f"{q(dec['thread_id'])}, {q(dec.get('sender'))}, {q(mid)}, {fact_id}, "
                  f"{q(pro.get('ask'))}, {q(pro.get('verb'))}, {q(pro.get('label'))}, "
                  f"{q(pro.get('project'))}, {q(pro.get('priority'))}, "
                  f"{q(fin.get('ask'))}, {q(fin.get('verb'))}, {q(fin.get('label'))}, "
                  f"{q(fin.get('project'))}, {q(fin.get('priority'))}, "
                  f"{'true' if ov else 'false'}, {'now()' if ov else 'NULL'}, "
                  f"{a(dec.get('override_reason'))}, {q(dec.get('decided_by') or 'pete')}, "
                  f"{lp}, {lr}, {a(dec.get('basis_refs'))}, {sid}, {bq}, {sg}, {bg}, {cr}, {pc}, {eng}, "
                  "'applying')")
    if manifest:
        manifest.write(json.dumps({"step": "ledger", "message_id": mid}) + "\n")
    # On an override, bank the correction as a triage_cases exemplar (decoupled from this row).
    if ov:
        bank_exemplar(dec)

    ok = True
    # 2) Gmail mutation per verb (skipped for demo/no-gmail payloads)
    gmail_done = None
    if not dec.get("no_gmail"):
        try:
            g = tl.gmail()
            labels = {l["name"]: l["id"] for l in g.list_labels()}
            def _resolve(name):
                # exact name, else UNIQUE suffix match ("SY-Clancy" -> "Customers/SY-Clancy").
                # Returns (label_id, error). A short/wrong name that can't resolve is an ERROR,
                # never a silent no-op (16 Jul 2026: short names archived threads with NO label).
                if not name:
                    return None, None
                if name in labels:
                    return labels[name], None
                hits = [n for n in labels if n == name or n.endswith("/" + name)]
                if len(hits) == 1:
                    return labels[hits[0]], None
                return None, ("no label named/ending '%s'" % name if not hits
                              else "%d labels match '%s' (ambiguous)" % (len(hits), name))
            verb = (fin.get("verb") or "").lower()
            add, remove = [], []
            filing_id, label_err = _resolve(fin.get("label"))
            if filing_id:
                add.append(filing_id)
            # verbs that MUST carry a resolvable filing label; if it doesn't resolve, REFUSE to
            # mutate (leave the thread in the inbox) rather than archive it unlabelled.
            needs_label = bool(fin.get("label")) and (
                verb.startswith(("file", "task", "reply", "keep")) or verb == "route")
            if needs_label and not filing_id:
                ok = False
                lines.append("  ✗ Gmail: filing label '%s' did not resolve (%s) -- NOT mutating "
                             "(thread left in inbox, not archived)" % (fin.get("label"), label_err))
            else:
                if verb.startswith("reply"):                # Reply / Reply+Task -> filing label + Replies + archive
                    add.append(labels.get("Replies")); remove.append("INBOX")
                elif verb.startswith("hand"):               # Hand to {person} -> Delegated, keep INBOX
                    add.append(labels.get("Delegated"))
                elif verb == "route":                       # engine intake (EE): filing (TE) label + Replies + archive
                    add.append(labels.get("Replies")); remove.append("INBOX")
                elif verb == "keep":                        # add filing label, KEEP in inbox
                    pass
                elif verb in ("skip", "-", ""):             # defer: no Gmail change
                    add, remove = [], []
                elif verb.startswith(("file", "task")):     # filing label + archive
                    remove.append("INBOX")
                elif verb == "clear":                       # noise: archive, no label
                    remove, add = ["INBOX"], []
                add = [x for x in add if x]                 # drop any unresolved Replies/Delegated
                if add or remove:
                    g.modify_thread(dec["thread_id"], add=add or None, remove=remove or None)
                    gmail_done = {"add": add, "remove": remove}
                    if manifest:
                        manifest.write(json.dumps({"step": "gmail", "thread_id": dec["thread_id"],
                                                   "add": add, "remove": remove}) + "\n")
        except Exception as e:
            ok = False
            lines.append(f"  ✗ Gmail: {e}")

    # 3) tasks: close on evidence / create on explicit Pete confirmation
    task_id = None
    if dec.get("close_task_id"):
        tl.cc_sql("UPDATE tasks SET status='done', completed_at=now() WHERE id='%s'"
                  % tl.esc(dec["close_task_id"]))
        if manifest:
            manifest.write(json.dumps({"step": "task-close", "task_id": dec["close_task_id"]}) + "\n")
    if dec.get("create_task"):
        t = dec["create_task"]
        rows = tl.cc_sql("INSERT INTO tasks (id, name, priority, base_priority, due_on, entity_slug, "
                         "project_slug, status, source, tags, notes) VALUES (gen_random_uuid(), "
                         f"{q(t['name'])}, {q(t.get('priority') or 'P3')}, {q(t.get('priority') or 'P3')}, "
                         f"NULL, {q(t.get('entity_slug'))}, {q(t.get('project_slug') or 'General')}, "
                         f"'todo', 'claude', {a(t.get('tags'))}, {q(t.get('notes'))}) RETURNING id")
        task_id = rows[0]["id"] if rows else None
        tl.cc_sql(f"UPDATE triage_decisions SET task_id={q(task_id)} WHERE message_id='{tl.esc(mid)}'")
        if manifest:
            manifest.write(json.dumps({"step": "task-create", "task_id": task_id}) + "\n")

    # flip applied
    tl.cc_sql(f"UPDATE triage_decisions SET apply_status='applied', applied_at=now() "
              f"WHERE message_id='{tl.esc(mid)}'")

    # ---- POST-CHECK: re-read all three ----
    # Direct committed SELECT (not existing_row, which returned stale/None and produced a FALSE ✗
    # on applied rows -- 16 Jul 2026; the row was correct, the re-read was wrong).
    fresh = tl.cc_sql("SELECT apply_status FROM triage_decisions WHERE message_id='%s'" % tl.esc(mid))
    c1 = bool(fresh) and fresh[0].get("apply_status") == "applied"
    lines.append(f"  {'✓' if c1 else '✗'} ledger: row applied")
    c2 = True
    if gmail_done:
        try:
            g = tl.gmail()
            full = g.get_thread(dec["thread_id"])
            lbls = set()
            for msg in full.get("messages", []):
                lbls.update(msg.get("labelIds", []))
            c2 = all(x in lbls for x in gmail_done["add"])
        except Exception:
            c2 = False
    lines.append(f"  {'✓' if c2 else '✗'} gmail: labels verified" if gmail_done
                 else "  ✓ gmail: no mutation requested")
    c3 = True
    if task_id:
        c3 = bool(tl.cc_sql(f"SELECT 1 FROM tasks WHERE id='{task_id}'"))
    if dec.get("close_task_id"):
        c3 = c3 and bool(tl.cc_sql("SELECT 1 FROM tasks WHERE id='%s' AND status='done'"
                                   % tl.esc(dec["close_task_id"])))
    lines.append(f"  {'✓' if c3 else '✗'} tasks: state verified")
    # forced-failure hook for the demo
    if dec.get("_force_postcheck_fail"):
        c3 = False
        lines.append("  ✗ tasks: FORCED post-check failure (demo)")
    return ok and c1 and c2 and c3, lines


def demo():
    print("P2 GATE DEMO — triage-log triple-write semantics")
    mid = "p2-demo-msg-001"
    tl.cc_sql(f"DELETE FROM triage_decisions WHERE message_id LIKE 'p2-demo-msg-%'")
    dec = {"thread_id": "p2-demo-thread", "message_id": mid, "sender": "bot@md.getsentry.com",
           "proposed": {"ask": "info-only", "verb": "File", "label": "Newsletters"},
           "final": {"ask": "info-only", "verb": "File", "label": "Newsletters"},
           "decided_by": "pete", "no_gmail": True}
    print("\n1. first --apply (scratch decision, no Gmail):")
    ok1, lines = capture(dec, apply=True); print("\n".join(lines))
    print("\n2. re-run of the SAME payload (must be a full no-op):")
    ok2, lines = capture(dec, apply=True); print("\n".join(lines))
    noop = any("FULL NO-OP" in l for l in lines)
    print("\n3. forced post-check failure (must exit non-zero):")
    dec2 = dict(dec, message_id="p2-demo-msg-002", _force_postcheck_fail=True)
    ok3, lines = capture(dec2, apply=True); print("\n".join(lines))
    print(f"   capture returned ok={ok3} → the CLI would exit {'1 (non-zero) ✓' if not ok3 else '0 ✗'}")
    print("\n4. cleanup:")
    tl.cc_sql(f"DELETE FROM triage_decisions WHERE message_id LIKE 'p2-demo-msg-%'")
    left = tl.cc_sql("SELECT count(*) AS n FROM triage_decisions WHERE message_id LIKE 'p2-demo-msg-%'")[0]["n"]
    print(f"   scratch rows remaining: {left}")
    verdict = ok1 and ok2 and noop and (not ok3) and left == 0
    print(f"\nP2 GATE: {'PASS — applied, no-op on re-run, non-zero on forced failure, clean' if verdict else 'FAIL'}")
    return 0 if verdict else 1


def main():
    if "--demo" in sys.argv:
        return demo()
    if "--in" not in sys.argv:
        print(__doc__); return 2
    path = sys.argv[sys.argv.index("--in") + 1]
    apply = "--apply" in sys.argv
    manifest = None
    if "--manifest" in sys.argv:
        manifest = open(sys.argv[sys.argv.index("--manifest") + 1], "a")
    payload = json.load(open(path))
    decs = payload if isinstance(payload, list) else [payload]
    all_ok = True
    for dec in decs:
        ok, lines = capture(dec, apply=apply, manifest=manifest)
        print(f"{dec.get('message_id','?')[:30]}:")
        print("\n".join(lines))
        all_ok = all_ok and ok
    if manifest:
        manifest.close()
    print(f"\n{'ALL OK' if all_ok else 'FAILURES — see ✗ lines above'} ({len(decs)} decision(s), "
          f"{'applied' if apply else 'dry-run'})")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
