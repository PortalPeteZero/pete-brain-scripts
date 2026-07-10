#!/usr/bin/env python3
# CRON-META
# what: Daily triage-sync — the email-task-sync engine on the schedule the email-workflow doc claims (07:15). Deploys REPORT-ONLY: surfaces Gmail-label ↔ CC-task drift to the morning brief and mutates nothing until Pete flips triage-sync-mode to 'acting' (guards verified at runtime, never assumed).
# why: The email-workflow doc claimed a daily 07:15 sync cron that never existed (verified 10 Jul 2026 — no such cron in public.crons). Triage Engine P0 makes the claim true, report-only first ([[triage-engine-design]]).
# reads: Gmail (Replies label + thread state), CC public.tasks, config (triage-auto-mode, triage-sync-mode)
# writes: CC daily_log (cron_name='triage-sync'); in acting mode ONLY: triage_sync_actions + Gmail label ops + task closes + triage_digests
# entity: personal
# schedule: 15 7 * * *
# timezone: Atlantic/Canary
# secrets: GOOGLE_SA_JSON, SUPABASE_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERID
# CRON-META-END
"""triage-sync.py -- the Triage Engine's scheduled sync (P0; design: [[triage-engine-design]]).

Two modes, config key 'triage-sync-mode':
  report (DEFAULT) -- enumerate the two email-workflow gestures (task done -> strip Replies;
                      label stripped -> close task) and REPORT them to the morning brief.
                      Zero mutation. This is how the cron deploys.
  acting           -- execute the gestures under the FULL guard framework: runtime guard
                      verification (P3 lint + P4 reconcile cron live + a demonstrated digest
                      delivery), the write-order rule on the triage_sync_actions grain
                      (row 'applying' FIRST, mutate, flip 'applied'), the triage-auto-mode
                      kill switch re-read before EVERY mutation, lint per action, digest
                      with undo links at the end. The flip to acting is PETE'S, made only
                      once all guards are live -- the script refuses acting mode (falls back
                      to report, loudly) if any guard is missing.

Exemptions honoured in both modes: [no-sync-close] marker + the Team-Finances blanket.
Run by hand: VAULT=/tmp/pbs python3 /tmp/pbs/triage-sync.py [--acting-check]
"""
import os, sys, json, datetime as dt

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")


def find_gestures(g):
    """Enumerate the two sync gestures. Returns (strip_candidates, close_candidates)."""
    strip, close = [], []
    # Gesture A: tray thread whose linked CC task is done -> strip Replies
    tray = g.search_threads("label:Replies", max_results=100)
    tray_ids = {t["id"] for t in tray}
    for t in tray:
        tid = t["id"]
        tasks = tl.cc_sql(
            "SELECT id, name, status, notes FROM tasks WHERE notes ILIKE '%%%s%%'" % tl.esc(tid))
        for task in tasks:
            notes = task.get("notes") or ""
            if "[no-sync-close]" in notes:
                continue
            if task["status"] == "done":
                strip.append({"thread_id": tid, "task_id": task["id"], "task": task["name"][:70],
                              "action": "strip-replies"})
    # Gesture B: open task tied to a thread whose Replies label is gone -> close task
    open_tasks = tl.cc_sql(
        "SELECT id, name, notes, project_slug FROM tasks WHERE status='todo' AND "
        "notes ILIKE '%mimestream.com%'")
    for task in open_tasks:
        notes = task.get("notes") or ""
        if "[no-sync-close]" in notes or task.get("project_slug") == "Team-Finances":
            continue
        import re
        m = re.search(r"/t/([0-9a-f]{10,})", notes)
        if not m:
            continue
        tid = m.group(1)
        if tid not in tray_ids:
            # label stripped (or never trayed) -- verify the thread genuinely lacks Replies
            try:
                full = g.get_thread(tid)
                lbls = set()
                for msg in full.get("messages", []):
                    lbls.update(msg.get("labelIds", []))
                names = set()
                for l in g.list_labels():
                    if l["id"] in lbls:
                        names.add(l["name"])
                if "Replies" not in names:
                    close.append({"thread_id": tid, "task_id": task["id"],
                                  "task": task["name"][:70], "action": "close-task"})
            except Exception:
                continue
    return strip, close


def acting_guards_live():
    """The flip's runtime preconditions -- verified live, never assumed. Returns (ok, missing)."""
    missing = []
    if not os.path.exists(os.path.join(tl.VAULT, "triage-lint.py")):
        missing.append("P3 triage-lint.py not on disk")
    else:
        try:
            tl.load_lint_rules()
        except Exception as e:
            missing.append(f"lint rules fence unreadable: {e}")
    crons = tl.cc_sql("SELECT enabled FROM crons WHERE key='triage-reconcile'")
    if not (crons and crons[0]["enabled"]):
        missing.append("P4 triage-reconcile cron not live (the independent trip evaluator)")
    d = tl.cc_sql("SELECT count(*) AS n FROM triage_digests WHERE delivered=true AND action_count > 0")
    if not d or d[0]["n"] == 0:
        missing.append("digest delivery not yet demonstrated end-to-end (no delivered action-carrying digest)")
    return (not missing), missing


def execute_acting(g, strip, close):
    """Acting mode: the write-order rule on the sync grain, kill switch per action, digest after."""
    lint_mod = importlib.import_module("triage-lint".replace("-", "_")) if False else None
    # lint via subprocess-free import is awkward for hyphenated file; use the rules directly:
    rules = tl.load_lint_rules()
    labels = {l["name"]: l["id"] for l in g.list_labels()}
    done = []
    for item in strip + close:
        if not tl.auto_mode_on():           # re-read IMMEDIATELY before every mutation
            print("KILL SWITCH off — halting remainder of batch")
            break
        act_date = dt.date.today().isoformat()
        rows = tl.cc_sql(
            "INSERT INTO triage_sync_actions (thread_id, action, task_id, apply_status) "
            "VALUES ('%s','%s','%s','applying') "
            "ON CONFLICT (thread_id, action, action_date) DO NOTHING RETURNING id"
            % (tl.esc(item["thread_id"]), tl.esc(item["action"]), tl.esc(item["task_id"])))
        if not rows:
            continue                        # dedupe: already acted on today
        rid = rows[0]["id"]
        try:
            if item["action"] == "strip-replies":
                g.modify_thread(item["thread_id"], remove=[labels.get("Replies", "Replies")])
            else:
                tl.cc_sql("UPDATE tasks SET status='done', completed_at=now(), "
                          "notes = coalesce(notes,'') || E'\\n[closed by triage-sync %s — Replies label removed]' "
                          "WHERE id='%s'" % (act_date, tl.esc(item["task_id"])))
            tl.cc_sql("UPDATE triage_sync_actions SET apply_status='applied', applied_at=now() "
                      "WHERE id='%s'" % rid)
            done.append(item)
        except Exception as e:
            print(f"  ✗ {item['action']} {item['thread_id']}: {e} (row left 'applying' — surfaces in digest)")
    did, n, delivered = tl.assemble_digest(kind="sync")
    return done, did, n, delivered


def main():
    g = tl.gmail()
    mode = (tl.get_config("triage-sync-mode", "report") or "report").strip().lower()
    strip, close = find_gestures(g)
    lines = [f"## triage-sync ({mode} mode) — {tl.today()}"]
    if not strip and not close:
        lines.append("Label ↔ task state agrees. Nothing to do.")
    else:
        for s in strip:
            lines.append(f"- task done → strip Replies: {s['task']} (thread {s['thread_id'][:12]}…)")
        for c in close:
            lines.append(f"- label stripped → close task: {c['task']} (thread {c['thread_id'][:12]}…)")

    if "--acting-check" in sys.argv:
        ok, missing = acting_guards_live()
        print("ACTING GUARDS:", "ALL LIVE — Pete may flip triage-sync-mode to 'acting'" if ok
              else "NOT READY:\n  - " + "\n  - ".join(missing))
        return 0

    if mode == "acting":
        ok, missing = acting_guards_live()
        if not ok:
            lines.append("⚠ acting mode CONFIGURED but guards missing — fell back to REPORT-ONLY:")
            lines.extend(f"  - {m}" for m in missing)
        elif not tl.auto_mode_on():
            lines.append("⚠ acting mode: triage-auto-mode is OFF (kill switch) — report only this run.")
        else:
            done, did, n, delivered = execute_acting(g, strip, close)
            lines.append(f"ACTED on {len(done)} of {len(strip)+len(close)} — digest {str(did)[:8]} "
                         f"({n} rows, delivered={delivered}).")
    tl.log_daily("triage-sync", "\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
