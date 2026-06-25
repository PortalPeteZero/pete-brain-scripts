#!/usr/bin/env python3
"""
email-task-sync.py -- deterministic implementation of the 8-step email-task-sync algorithm.

Reconciles Gmail labels (Replies / Delegated) with Pete's Command Centre tasks in
`public.tasks`. Steps 1, 3, 4, 5, 7, 8 run as Python so the behaviour is repeatable; Step 6
(orphan routing) surfaces candidates with full context for the LLM/skill to action (task-name
generation + routing decision). Asana belongs to Jane only — this never touches it.

(Was sync-asana.py. Converted off the Asana API onto public.tasks on 2026-06-25 — the engine
the migration's docs already claimed but had never actually had.)

Usage:
  python3 email-task-sync.py            # run all steps, apply changes
  python3 email-task-sync.py --dry-run  # report only, no Gmail or CC mutations
  python3 email-task-sync.py --json     # emit raw JSON for chaining

Exit codes:
  0 = sync complete, no decisions needed
  1 = sync complete, Step 6 surfaced Replies-without-task threads (informational — no action required)
  2 = fatal error (auth, API, file)

Reads/writes:
  - CC `public.tasks` via cc-sql.py (open + recently-done; close = UPDATE status='done')
  - Gmail via gmail-api.py helper
  - VAULT env var (default /tmp/pbs) — all tools are flat at $VAULT/<tool>.py
"""

from __future__ import annotations
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# --- Configuration -----------------------------------------------------------

REPLIES_LABEL = "Label_165"   # Gmail tray label — renamed Actions→Replies 2026-06-25; ID unchanged, so modify/check by ID is rename-proof
DELEGATED_LABEL = "Label_170"

EMAIL = "pete.ashcroft@sygma-solutions.com"
def mime_link(tid): return f"https://links.mimestream.com/g/{EMAIL}/t/{tid}"
def gmail_link(tid): return f"https://mail.google.com/mail/u/0/#all/{tid}"

THREAD_URL_RE = re.compile(
    r"(?:mail\.google\.com/mail/u/0/#[a-z]+/|links\.mimestream\.com/g/[^/]+/t/)([A-Fa-f0-9]{16,17})"
)

# --- CC task store (public.tasks via cc-sql.py) ------------------------------

CC_SQL = os.path.join(VAULT, "cc-sql.py")

def cc_sql(query):
    """Run SQL against the CC. Returns a list of row dicts (SELECT) or [] (UPDATE/empty)."""
    r = subprocess.run([sys.executable, CC_SQL, query], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=60)
    if r.returncode != 0:
        sys.stderr.write(f"cc-sql error: {(r.stderr or r.stdout)[:300]}\n")
        raise RuntimeError("cc-sql failed")
    out = (r.stdout or "").strip()
    if not out:
        return []
    try:
        d = json.loads(out)
        return d if isinstance(d, list) else []
    except json.JSONDecodeError:
        return []

def sql_q(v):
    """Single-quote + escape a value for inline SQL."""
    return "'" + str(v).replace("'", "''") + "'"

# --- Gmail helper (imported as a module to avoid ~80 subprocess startups) -----

_gmail_helper_path = os.path.join(VAULT, "gmail-api.py")
_spec = importlib.util.spec_from_file_location("gmail_api", _gmail_helper_path)
_gmail_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gmail_mod)
_g = _gmail_mod.GmailAPI()

def gmail_search(query, max_results=100):
    return _g.search_threads(query, max_results=max_results)

def gmail_get_thread(tid):
    try: return _g.get_thread(tid, fmt="metadata")
    except Exception: return None

def gmail_modify(tid, add=None, remove=None):
    return _g.modify_thread(tid, add=add or [], remove=remove or [])

def thread_labels(thread):
    labels = set()
    if not thread: return labels
    for m in thread.get('messages', []):
        for l in m.get('labelIds', []):
            labels.add(l)
    return labels

def _extract_tids(notes):
    return sorted(set(THREAD_URL_RE.findall(notes or "")))

# --- The 8 Steps -------------------------------------------------------------

def step1_pull_tasks():
    """Pull both open and recently-done (30d) CC tasks that link a Gmail thread.

    Both sides are needed: open tasks drive Step 4 (close on Gmail-side label removal);
    recently-done tasks drive Step 3 (strip the Gmail label after CC-side closure). If
    Step 1 skipped done tasks, the label would persist after closure, so the thread would
    wrongly linger in the Replies tray after its task is done. (No 100-cap workaround needed
    any more — SQL isn't paged.)
    """
    open_rows = cc_sql(
        "SELECT id, name, notes, due_on, project_slug, entity_slug, priority "
        "FROM tasks WHERE status != 'done'")
    closed_rows = cc_sql(
        "SELECT id, name, notes, updated_at FROM tasks "
        "WHERE status = 'done' AND updated_at > now() - interval '30 days'")

    linked_open = [{"id": r["id"], "name": (r.get("name") or "")[:90],
                    "thread_ids": _extract_tids(r.get("notes")),
                    "due_on": r.get("due_on"), "notes": r.get("notes") or "",
                    "project_slug": r.get("project_slug")}
                   for r in open_rows if _extract_tids(r.get("notes"))]
    linked_closed = [{"id": r["id"], "name": (r.get("name") or "")[:90],
                      "thread_ids": _extract_tids(r.get("notes")),
                      "notes": r.get("notes") or ""}
                     for r in closed_rows if _extract_tids(r.get("notes"))]
    return {"open": linked_open, "closed": linked_closed, "all_open_count": len(open_rows)}

def _fetch_thread_labels(tid):
    return tid, thread_labels(gmail_get_thread(tid))

def step3_strip_labels_from_closed(closed_tasks, dry_run=False, open_tasks=None):
    """For each closed task, strip Replies/Delegated from linked Gmail threads.

    Multi-task ownership rule: a thread can be linked to multiple tasks. Strip the
    workflow label only when NO OPEN task still links the same thread — otherwise the
    strip would orphan the open task's Gmail-side signal and Step 4 would then false-close
    the open task. Pass `open_tasks` so we can build the "still owned" set.

    `[no-sync-close]` keep-label rule (2026-06-25): the marker decouples the Gmail label
    from the task in BOTH directions. It already stops a label-removal from closing a task
    (Step 4); symmetrically, closing a `[no-sync-close]` task must NOT strip its Gmail label
    — the label/tray item is independent of the task (an overlap reply still owed, or a
    Part-C migration close where the Replies label is now the record). Keep the label.
    """
    stripped = []
    thread_to_task = {}
    for t in closed_tasks:
        for tid in t["thread_ids"]:
            thread_to_task.setdefault(tid, []).append(t)

    open_owned = set()
    if open_tasks:
        for t in open_tasks:
            for tid in t["thread_ids"]:
                open_owned.add(tid)

    # threads whose closing task says "keep the label" — never strip these
    keep_label = set()
    for t in closed_tasks:
        if "[no-sync-close]" in (t.get("notes") or ""):
            for tid in t["thread_ids"]:
                keep_label.add(tid)

    tids_to_check = [tid for tid in thread_to_task.keys()
                     if tid not in open_owned and tid not in keep_label]
    skipped_due_to_open = [tid for tid in thread_to_task.keys() if tid in open_owned]
    skipped_keep_label = [tid for tid in thread_to_task.keys()
                          if tid in keep_label and tid not in open_owned]

    if not tids_to_check:
        results = {}
    else:
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = dict(ex.map(_fetch_thread_labels, tids_to_check))

    for tid, labels in results.items():
        to_remove = []
        if REPLIES_LABEL in labels: to_remove.append(REPLIES_LABEL)
        if DELEGATED_LABEL in labels: to_remove.append(DELEGATED_LABEL)
        if to_remove:
            if not dry_run:
                gmail_modify(tid, remove=to_remove)
            tasks = thread_to_task[tid]
            stripped.append({"thread_id": tid, "labels_removed": to_remove,
                             "task_name": tasks[0]["name"], "task_id": tasks[0]["id"]})
    for tid in skipped_due_to_open:
        tasks = thread_to_task[tid]
        stripped.append({"thread_id": tid, "labels_removed": [], "skipped_open_owner": True,
                         "task_name": tasks[0]["name"], "task_id": tasks[0]["id"]})
    for tid in skipped_keep_label:
        tasks = thread_to_task[tid]
        stripped.append({"thread_id": tid, "labels_removed": [], "skipped_keep_label": True,
                         "task_name": tasks[0]["name"], "task_id": tasks[0]["id"]})
    return stripped

def step4_close_tasks_from_gmail(open_tasks, dry_run=False):
    """Close an open CC task when its linked thread no longer has Replies/Delegated.

    Exemptions (the wrapper enforces both):
      1. `[no-sync-close]` marker in notes — never close on label state (Pete-sent watch
         tasks; and CC-only tasks: bills, cert batches, work items de-trayed under the
         2026-06-06 Action/Task split — their work happens outside email).
      2. Team-Finances blanket — any task with project_slug='Team-Finances' is exempt
         (a bill is never a reply).
    Every closure appends an audit note so an accidental strip-to-clear-out is visible.
    """
    all_tids = set()
    for t in open_tasks:
        for tid in t["thread_ids"]:
            all_tids.add(tid)
    with ThreadPoolExecutor(max_workers=8) as ex:
        thread_to_labels = dict(ex.map(_fetch_thread_labels, all_tids))
    closures, exempt = [], []
    for t in open_tasks:
        if not t["thread_ids"]:
            continue
        # Opt-out 1 — [no-sync-close] marker (watch tasks + CC-only/de-trayed tasks)
        if "[no-sync-close]" in (t.get("notes") or ""):
            continue
        labels_gone = all(
            REPLIES_LABEL not in thread_to_labels.get(tid, set())
            and DELEGATED_LABEL not in thread_to_labels.get(tid, set())
            for tid in t["thread_ids"])
        # Opt-out 2 — Team-Finances blanket (a bill is never a reply)
        if t.get("project_slug") == "Team-Finances":
            if labels_gone:
                exempt.append({"task_id": t["id"], "task_name": t["name"],
                               "reason": "team-finances blanket"})
            continue
        if labels_gone:
            if not dry_run:
                audit = ("\n\nClosed by sync — Replies/Delegated label removed in Gmail, "
                         f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC. If this strip "
                         "was a tray clear-out rather than completion, reopen (status='open') and ask "
                         "Claude to mark it [no-sync-close].")
                cc_sql(f"UPDATE tasks SET status='done', updated_at=now(), "
                       f"notes = coalesce(notes,'') || {sql_q(audit)} WHERE id = {sql_q(t['id'])}")
            closures.append({"task_id": t["id"], "task_name": t["name"], "thread_ids": t["thread_ids"]})
    return closures, exempt

def step5_delegation_check(dry_run=False):
    """List open Delegated-track tasks (project_slug='Team-General' + [delegated] marker).

    The reply-detection + chaser-draft logic is LLM-side per the skill; the wrapper surfaces
    the open delegated set so the skill can act on it.
    """
    rows = cc_sql(
        "SELECT id, name, notes, due_on FROM tasks "
        "WHERE status != 'done' AND project_slug = 'Team-General' AND notes LIKE '%[delegated]%'")
    return {"open_delegated_count": len(rows),
            "details": [{"id": r["id"], "name": r["name"], "due": r.get("due_on")} for r in rows]}

def step6_orphan_candidates(linked_open):
    """Find Replies-labelled threads with no linked open task. Surface candidates (no auto-create)."""
    # Transition-safe: matches the tray BEFORE and AFTER the Actions→Replies rename.
    # Trim to "label:Replies" once the rename has bedded in.
    actions_threads = gmail_search("label:Actions OR label:Replies", 100)
    open_thread_ids = set()
    for t in linked_open:
        for tid in t["thread_ids"]:
            open_thread_ids.add(tid)
    candidate_tids = [t["id"] for t in actions_threads if t["id"] not in open_thread_ids]
    if not candidate_tids:
        return []
    def fetch(tid):
        return tid, gmail_get_thread(tid)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = dict(ex.map(fetch, candidate_tids))
    snippet_by_tid = {t["id"]: t.get("snippet", "") for t in actions_threads}
    orphans = []
    for tid, th in results.items():
        if not th:
            continue
        labels = thread_labels(th)
        first_msg = th.get("messages", [{}])[0]
        headers = {h["name"]: h["value"] for h in first_msg.get("payload", {}).get("headers", [])}
        orphans.append({
            "thread_id": tid,
            "from": headers.get("From", "")[:80],
            "subject": headers.get("Subject", "")[:90],
            "snippet": snippet_by_tid.get(tid, "")[:200],
            "labels": [l for l in labels if l.startswith("Label_")],
        })
    return orphans

def step7_pattern_detection():
    """Placeholder for auto-filter / demand-driven label suggestions (computed by the skill)."""
    return {"auto_filter_suggestions": [], "demand_label_suggestions": []}

def step8_parity_check():
    """Surface the Gmail Customers/* and Suppliers/* labels for the skill's parity pass.

    The entity-side homes are now Drive folders + vault_notes (the local Customers/Suppliers
    vault tree is retired), so the label↔home comparison is a judgement the skill makes against
    `drive_files` / `vault_notes`. The wrapper just hands over the current Gmail label set.
    """
    out = subprocess.run([sys.executable, os.path.join(VAULT, "gmail-api.py"), "labels"],
                         capture_output=True, text=True)
    gmail_customers, gmail_suppliers = [], []
    for line in (out.stdout or "").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        _, name = parts
        if name.startswith("Customers/"):
            gmail_customers.append(name.replace("Customers/", ""))
        elif name.startswith("Suppliers/"):
            gmail_suppliers.append(name.replace("Suppliers/", ""))
    return {"gmail_customer_labels": sorted(gmail_customers),
            "gmail_supplier_labels": sorted(gmail_suppliers),
            "note": "entity-side parity (vs drive_files/vault_notes) is the skill's judgement pass"}

# --- Main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, no mutations")
    ap.add_argument("--json", action="store_true", help="emit raw JSON output")
    args = ap.parse_args()

    report = {"started_at": datetime.now(timezone.utc).isoformat(), "dry_run": args.dry_run}

    sys.stderr.write("Step 1: pulling linked tasks (open + done-30d) from public.tasks...\n")
    tasks = step1_pull_tasks()
    report["step1"] = {"open_linked": len(tasks["open"]), "closed_linked": len(tasks["closed"]),
                       "total_open_tasks": tasks["all_open_count"]}

    sys.stderr.write("Step 3: stripping Gmail labels from done-task threads...\n")
    report["step3_stripped"] = step3_strip_labels_from_closed(tasks["closed"], args.dry_run, open_tasks=tasks["open"])

    sys.stderr.write("Step 4: closing open tasks where Gmail labels removed...\n")
    report["step4_closures"], report["step4_exempt"] = step4_close_tasks_from_gmail(tasks["open"], args.dry_run)

    sys.stderr.write("Step 5: delegation check...\n")
    report["step5"] = step5_delegation_check(args.dry_run)

    sys.stderr.write("Step 6: finding orphan candidates...\n")
    report["step6_orphan_candidates"] = step6_orphan_candidates(tasks["open"])

    report["step7"] = step7_pattern_detection()

    sys.stderr.write("Step 8: parity labels...\n")
    report["step8_parity"] = step8_parity_check()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()

    if args.json:
        print(json.dumps(report, indent=2))
        sys.exit(1 if report["step6_orphan_candidates"] else 0)

    print(f"\n═══ email-task sync ═══ {report['started_at'][:19]} {'[DRY-RUN]' if args.dry_run else ''}")
    print(f"\nStep 1: open linked tasks={report['step1']['open_linked']} | done linked (30d)={report['step1']['closed_linked']}")
    s3_stripped = [s for s in report["step3_stripped"] if s.get("labels_removed")]
    s3_skipped = [s for s in report["step3_stripped"] if s.get("skipped_open_owner")]
    s3_keep = [s for s in report["step3_stripped"] if s.get("skipped_keep_label")]
    print(f"\nStep 3: stripped Gmail labels from {len(s3_stripped)} done-task threads"
          f" (skipped {len(s3_skipped)} — open task still owns the thread;"
          f" kept {len(s3_keep)} — [no-sync-close] keeps the label as the record)")
    for s in s3_stripped:
        names = ["Replies" if l == REPLIES_LABEL else "Delegated" for l in s["labels_removed"]]
        print(f"  ✗ {s['thread_id']} | -{','.join(names)} | task: {s['task_name'][:60]}")
    print(f"\nStep 4: closed {len(report['step4_closures'])} open tasks where Gmail label removed"
          f" (exempt-skipped {len(report.get('step4_exempt', []))})")
    for c in report["step4_closures"]:
        print(f"  ✓ closed {c['task_id']} | {c['task_name'][:60]}")
    for e in report.get("step4_exempt", []):
        print(f"  ⊝ exempt ({e['reason']}) | {e['task_name'][:60]}")
    print(f"\nStep 5: delegated open count = {report['step5']['open_delegated_count']}")
    print(f"\nStep 6: Replies threads with no task = {len(report['step6_orphan_candidates'])} "
          f"(expected state — surfaced for awareness; the label is the record, NO task is created)")
    for o in report["step6_orphan_candidates"]:
        print(f"  • {o['thread_id']} | from={o['from'][:40]} | subj={o['subject'][:50]} | labels={o['labels']}")
    p = report["step8_parity"]
    print(f"\nStep 8 (Gmail labels for the skill's parity pass): "
          f"{len(p['gmail_customer_labels'])} customer · {len(p['gmail_supplier_labels'])} supplier labels")
    print()
    sys.exit(1 if report["step6_orphan_candidates"] else 0)

def _cc_pulse(summary: str):
    try:
        sys.path.insert(0, VAULT)
        import cc_publish
        cc_publish.pulse("email-task-sync", summary)
    except Exception:
        pass

if __name__ == "__main__":
    main()
    _cc_pulse("run completed")
