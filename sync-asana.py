#!/usr/bin/env python3
"""
sync-asana.py -- deterministic implementation of the 8-step asana-gmail-sync algorithm.

Built 2026-05-20 in response to Pete's "how do we ensure the next time I run this you follow
all steps" — moves Steps 1, 3, 4, 5, 7, 8 from LLM-interpretive prose into Python code so the
behaviour is repeatable. Step 6 (orphan routing) still needs an LLM call for task-name
generation + routing decisions, so the script surfaces candidates with full context
(thread labels, sender, snippet, suggested routing) for the LLM to action.

Usage:
  python3 sync-asana.py            # run all steps, apply changes
  python3 sync-asana.py --dry-run  # report only, no Gmail or Asana mutations
  python3 sync-asana.py --json     # emit raw JSON for chaining (e.g. into LLM prompt)

Exit codes:
  0 = sync complete, no decisions needed
  1 = sync complete, Step 6 orphans need LLM routing
  2 = fatal error (auth, API, file)

Reads:
  - Asana PAT from Library/processes/secrets/asana-pat
  - Gmail via gmail-api.py helper
  - VAULT_ROOT env var (default /Users/peterashcroft/Second Brain)
"""

from __future__ import annotations
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

# --- Configuration -----------------------------------------------------------

VAULT_ROOT = os.environ.get("VAULT_ROOT", VAULT)
WS = "1213947679900731"
PETE = "1213947679900718"
PRIORITY_FIELD = "1213945150508559"
P2_ENUM = "1213945150508561"

ACTIONS_LABEL = "Label_165"
DELEGATED_LABEL = "Label_170"

TEAM_GENERAL = "1214564987703466"
SECTIONS = {
    "SY-General": "1214564987855498",
    "CD-General": "1214564987862794",
    "EA-General": "1214565283959281",
    "Delegated":  "1214564987864352",
}
TEAM_FINANCES = "1214565508668959"
INVOICE_SECTIONS = {
    "CD-To-Pay":   "1214565862019985",
    "CD-Overdue":  "1214565640727847",
    "CD-Awaiting": "1214565508808207",
    "SY-To-Pay":   "1214565670136545",
    "SY-Overdue":  "1214565862262856",
    "SY-Awaiting": "1214565640753174",
    "EA-Payments": "1214565801009636",
}

EMAIL = "pete.ashcroft@sygma-solutions.com"
def mime_link(tid): return f"https://links.mimestream.com/g/{EMAIL}/t/{tid}"
def gmail_link(tid): return f"https://mail.google.com/mail/u/0/#all/{tid}"

THREAD_URL_RE = re.compile(
    r"(?:mail\.google\.com/mail/u/0/#[a-z]+/|links\.mimestream\.com/g/[^/]+/t/)([A-Fa-f0-9]{16,17})"
)

# --- Helpers -----------------------------------------------------------------

def read_pat():
    pat_path = os.path.join(VAULT_ROOT, "Library/processes/secrets/asana-pat")
    if not os.path.isfile(pat_path):
        sys.stderr.write(f"FATAL: Asana PAT not found at {pat_path}\n")
        sys.exit(2)
    return open(pat_path).read().strip()

PAT = None  # set in main()

def asana_get(path, params=None):
    url = f"https://app.asana.com/api/1.0{path}"
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {PAT}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def asana_put(path, body):
    url = f"https://app.asana.com/api/1.0{path}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {PAT}", "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def asana_post(path, body):
    url = f"https://app.asana.com/api/1.0{path}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {PAT}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

# Import gmail-api.py as a module — avoids subprocess overhead on the many
# get-thread calls (the script makes ~80+ per run; subprocess startup is ~150ms each).
_gmail_helper_path = os.path.join(VAULT_ROOT, "Library/processes/scripts/gmail-api.py")
_spec = importlib.util.spec_from_file_location("gmail_api", _gmail_helper_path)
_gmail_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gmail_mod)
_g = _gmail_mod.GmailAPI()

def gmail_search(query, max_results=100):
    return _g.search_threads(query, max_results=max_results)

def gmail_get_thread(tid):
    try: return _g.get_thread(tid, fmt="metadata")
    except: return None

def gmail_modify(tid, add=None, remove=None):
    return _g.modify_thread(tid, add=add or [], remove=remove or [])

def thread_labels(thread):
    labels = set()
    if not thread: return labels
    for m in thread.get('messages', []):
        for l in m.get('labelIds', []):
            labels.add(l)
    return labels

# --- The 8 Steps -------------------------------------------------------------

def step1_pull_tasks():
    """Pull both open and recently-completed (30d) Pete tasks with Gmail thread URLs.

    IMPORTANT: Asana's `/workspaces/{ws}/tasks/search` (typeahead) endpoint is capped at
    100 results per call with NO pagination (next_page is always null). A naive
    `assignee.any=pete & completed=false & limit=100` query truncates silently when Pete
    has more than 100 open tasks — any task beyond position 100 is invisible and Step 6
    treats its linked thread as an orphan, creating a duplicate.

    Fix: query by `text` filter for the URL substrings that appear in linked-task notes
    ('mail.google.com' and 'mimestream.com'). Each of those returns only tasks whose
    name OR notes contains the substring — a far narrower slice that fits comfortably
    under 100. Union the results, then filter to Pete-assigned + completion-state.

    Lesson: Library/lessons/2026-05-25-sync-asana-step-1-cap-100-no-pagination.md
    """
    today = datetime.now(timezone.utc)
    cutoff = (today - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")

    def _search(text_query, completed, completed_after=None):
        params = {
            "text": text_query,
            "assignee.any": PETE,
            "completed": "true" if completed else "false",
            "opt_fields": "name,notes,due_on,projects.name,memberships.section.name,custom_fields,completed,completed_at",
            "limit": 100,
        }
        if completed_after:
            params["completed_at.after"] = completed_after
        data = asana_get(f"/workspaces/{WS}/tasks/search", params).get("data", [])
        if len(data) >= 100:
            sys.stderr.write(
                f"WARN: text={text_query!r} completed={completed} returned {len(data)} — "
                "still at 100 cap; some linked tasks may be missed. Consider narrowing further.\n"
            )
        return data

    # Pull open tasks via BOTH URL flavours, union
    open_by_gid = {}
    for q in ("mail.google.com", "mimestream.com"):
        for t in _search(q, completed=False):
            open_by_gid[t["gid"]] = t
    open_tasks = list(open_by_gid.values())

    # Pull recently-closed tasks via BOTH URL flavours, union
    closed_by_gid = {}
    for q in ("mail.google.com", "mimestream.com"):
        for t in _search(q, completed=True, completed_after=cutoff):
            closed_by_gid[t["gid"]] = t
    closed_tasks = list(closed_by_gid.values())

    def extract(t):
        notes = t.get("notes", "") or ""
        ids = sorted(set(THREAD_URL_RE.findall(notes)))
        return ids

    linked_open = [{"gid": t["gid"], "name": t["name"][:90],
                    "thread_ids": extract(t), "due_on": t.get("due_on"),
                    "notes": t.get("notes", "") or "",
                    "projects": t.get("projects", []) or [],
                    "memberships": t.get("memberships", [])}
                   for t in open_tasks if extract(t)]
    linked_closed = [{"gid": t["gid"], "name": t["name"][:90],
                      "thread_ids": extract(t), "completed_at": t.get("completed_at")}
                     for t in closed_tasks if extract(t)]
    return {"open": linked_open, "closed": linked_closed, "all_open_count": len(open_tasks)}

def _fetch_thread_labels(tid):
    return tid, thread_labels(gmail_get_thread(tid))

def step3_strip_labels_from_closed(closed_tasks, dry_run=False, open_tasks=None):
    """For each closed task, strip Actions/Delegated from linked Gmail threads.

    Multi-task ownership rule: a Gmail thread can be linked to multiple Asana tasks
    (intentional, or accidental from duplicate detection). Strip the workflow label
    only when NO OPEN task still links the same thread. Otherwise the strip would
    orphan the open task's Gmail-side signal, and Step 4 would then auto-close the
    open task ("no Actions on linked thread = done") — cascade false-positive.

    Pass `open_tasks` so we can build the "still owned" set.
    """
    stripped = []
    thread_to_task = {}
    for t in closed_tasks:
        for tid in t["thread_ids"]:
            thread_to_task.setdefault(tid, []).append(t)

    # Build set of thread IDs still owned by an open linked task
    open_owned = set()
    if open_tasks:
        for t in open_tasks:
            for tid in t["thread_ids"]:
                open_owned.add(tid)

    # Parallel label fetch (only for threads we might strip)
    tids_to_check = [tid for tid in thread_to_task.keys() if tid not in open_owned]
    skipped_due_to_open = [tid for tid in thread_to_task.keys() if tid in open_owned]

    if not tids_to_check:
        results = {}
    else:
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = dict(ex.map(_fetch_thread_labels, tids_to_check))

    for tid, labels in results.items():
        to_remove = []
        if ACTIONS_LABEL in labels: to_remove.append(ACTIONS_LABEL)
        if DELEGATED_LABEL in labels: to_remove.append(DELEGATED_LABEL)
        if to_remove:
            if not dry_run:
                gmail_modify(tid, remove=to_remove)
            tasks = thread_to_task[tid]
            stripped.append({"thread_id": tid, "labels_removed": to_remove,
                             "task_name": tasks[0]["name"], "task_gid": tasks[0]["gid"]})
    # Surface the skipped-because-still-owned set so the report can show it
    for tid in skipped_due_to_open:
        tasks = thread_to_task[tid]
        stripped.append({"thread_id": tid, "labels_removed": [], "skipped_open_owner": True,
                         "task_name": tasks[0]["name"], "task_gid": tasks[0]["gid"]})
    return stripped

def step4_close_tasks_from_gmail(open_tasks, dry_run=False):
    """For each open task, close it if its linked Gmail thread no longer has Actions/Delegated.

    Opt-out 1 (marker): if task notes contain `[no-sync-close]`, leave the task open regardless
    of label state. Two uses: (a) "Pete-sent watch tasks" (chase X if no reply) where the linked
    thread is outbound and never Actions-labelled — added 2026-05-24 after Kathryn Morrison +
    Tom Ward PF watches got false-positive-closed; (b) "Asana-only tasks" (bills, cert batches,
    work items) de-trayed under the 2026-06-06 Action/Task split — the task tracks work done
    outside email, so Gmail label state must never close it.

    Opt-out 2 (blanket): any task in the Team-Finances project is exempt — a bill is never a
    reply, so the tray heuristic can't apply. Belt-and-braces for future bill tasks created
    without the marker. See Projects/PA-General/files/email-workflow-plan-2026-06-06-action-task-split.md.

    Every closure posts an audit comment on the task ("closed by sync — Actions label removed
    in Gmail {date}") so an accidental strip-to-clear-out is visible and one tap to reopen.
    """
    # Gather every unique thread ID across open tasks, fetch labels in parallel
    all_tids = set()
    for t in open_tasks:
        for tid in t["thread_ids"]:
            all_tids.add(tid)
    with ThreadPoolExecutor(max_workers=8) as ex:
        thread_to_labels = dict(ex.map(_fetch_thread_labels, all_tids))
    closures = []
    exempt = []
    for t in open_tasks:
        if not t["thread_ids"]: continue
        # Opt-out 1 — [no-sync-close] marker (watch tasks + Asana-only/de-trayed tasks)
        if "[no-sync-close]" in t.get("notes", ""):
            continue
        # Opt-out 2 — Team-Finances blanket (a bill is never a reply)
        if any(p.get("gid") == TEAM_FINANCES for p in t.get("projects", [])):
            labels_gone = all(
                ACTIONS_LABEL not in thread_to_labels.get(tid, set())
                and DELEGATED_LABEL not in thread_to_labels.get(tid, set())
                for tid in t["thread_ids"])
            if labels_gone:
                exempt.append({"task_gid": t["gid"], "task_name": t["name"],
                               "reason": "team-finances blanket"})
            continue
        all_missing = True
        for tid in t["thread_ids"]:
            labels = thread_to_labels.get(tid, set())
            if ACTIONS_LABEL in labels or DELEGATED_LABEL in labels:
                all_missing = False
                break
        if all_missing:
            if not dry_run:
                asana_put(f"/tasks/{t['gid']}", {"data": {"completed": True}})
                asana_post(f"/tasks/{t['gid']}/stories", {"data": {"text":
                    f"Closed by sync — Actions/Delegated label removed in Gmail, {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC. "
                    "If this strip was a tray clear-out rather than completion, reopen and ask Claude to mark it [no-sync-close]."}})
            closures.append({"task_gid": t["gid"], "task_name": t["name"],
                             "thread_ids": t["thread_ids"]})
    return closures, exempt

def step5_delegation_check(dry_run=False):
    """Check Team-General/Delegated section for tasks awaiting replies."""
    tasks = asana_get(f"/sections/{SECTIONS['Delegated']}/tasks", {
        "completed_since": "now",
        "opt_fields": "name,notes,assignee.name,due_on,created_at"
    }).get("data", [])
    return {"open_delegated_count": len(tasks),
            "details": [{"gid": t["gid"], "name": t["name"], "due": t.get("due_on")} for t in tasks]}

def step6_orphan_candidates(linked_open):
    """Find Actions-labelled threads with no linked open task. Returns candidates (no auto-create)."""
    actions_threads = gmail_search("label:Actions", 100)
    open_thread_ids = set()
    for t in linked_open:
        for tid in t["thread_ids"]:
            open_thread_ids.add(tid)
    candidate_tids = [t["id"] for t in actions_threads if t["id"] not in open_thread_ids]
    if not candidate_tids: return []
    # Parallel fetch
    def fetch(tid):
        th = gmail_get_thread(tid)
        return tid, th
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = dict(ex.map(fetch, candidate_tids))
    orphans = []
    snippet_by_tid = {t["id"]: t.get("snippet", "") for t in actions_threads}
    for tid, th in results.items():
        if not th: continue
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
    """Placeholder for auto-filter / demand-driven label suggestions. Computed elsewhere."""
    return {"auto_filter_suggestions": [], "demand_label_suggestions": []}

def step8_parity_check():
    """Vault folders vs Gmail labels — surface drift."""
    vault_customers = sorted([n for n in os.listdir(os.path.join(VAULT_ROOT, "Customers"))
                              if os.path.isdir(os.path.join(VAULT_ROOT, "Customers", n)) and not n.startswith(".")])
    vault_suppliers = sorted([n for n in os.listdir(os.path.join(VAULT_ROOT, "Suppliers"))
                              if os.path.isdir(os.path.join(VAULT_ROOT, "Suppliers", n)) and not n.startswith(".")])

    out = subprocess.run(["python3", os.path.join(VAULT_ROOT, "Library/processes/scripts/gmail-api.py"), "labels"],
                         capture_output=True, text=True)
    gmail_customers = []
    gmail_suppliers = []
    for line in out.stdout.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) < 2: continue
        _, name = parts
        if name.startswith("Customers/"): gmail_customers.append(name.replace("Customers/", ""))
        elif name.startswith("Suppliers/"): gmail_suppliers.append(name.replace("Suppliers/", ""))

    EXEMPT_SUPPLIERS = {"SY-Dext", "SY-Hindley-Business-Centre", "SY-Latitude"}

    return {
        "customers_vault_only": sorted(set(vault_customers) - set(gmail_customers)),
        "customers_gmail_only": sorted(set(gmail_customers) - set(vault_customers)),
        "suppliers_vault_only": sorted((set(vault_suppliers) - set(gmail_suppliers)) - EXEMPT_SUPPLIERS),
        "suppliers_gmail_only": sorted(set(gmail_suppliers) - set(vault_suppliers)),
        "exempt_suppliers_noted": sorted((set(vault_suppliers) - set(gmail_suppliers)) & EXEMPT_SUPPLIERS),
    }

# --- Main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, no mutations")
    ap.add_argument("--json", action="store_true", help="emit raw JSON output")
    args = ap.parse_args()

    global PAT
    PAT = read_pat()

    report = {"started_at": datetime.now(timezone.utc).isoformat(), "dry_run": args.dry_run}

    # Step 1
    sys.stderr.write("Step 1: pulling linked tasks (open + closed-30d)...\n")
    tasks = step1_pull_tasks()
    report["step1"] = {"open_linked": len(tasks["open"]), "closed_linked": len(tasks["closed"]),
                       "total_open_pete_tasks": tasks["all_open_count"]}

    # Step 3: strip Gmail labels from closed-task threads
    sys.stderr.write("Step 3: stripping Gmail labels from closed-task threads...\n")
    report["step3_stripped"] = step3_strip_labels_from_closed(tasks["closed"], args.dry_run, open_tasks=tasks["open"])

    # Step 4: close open tasks whose threads no longer have Actions/Delegated
    sys.stderr.write("Step 4: closing open tasks where Gmail labels removed...\n")
    report["step4_closures"], report["step4_exempt"] = step4_close_tasks_from_gmail(tasks["open"], args.dry_run)

    # Step 5: delegation
    sys.stderr.write("Step 5: delegation reply check...\n")
    report["step5"] = step5_delegation_check(args.dry_run)

    # Step 6: orphan candidates (surface only; LLM/skill must auto-create with routing decision)
    sys.stderr.write("Step 6: finding orphan candidates...\n")
    report["step6_orphan_candidates"] = step6_orphan_candidates(tasks["open"])

    # Step 7: pattern detection
    report["step7"] = step7_pattern_detection()

    # Step 8: parity check
    sys.stderr.write("Step 8: parity check...\n")
    report["step8_parity"] = step8_parity_check()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()

    if args.json:
        print(json.dumps(report, indent=2))
        sys.exit(1 if report["step6_orphan_candidates"] else 0)

    # Human-readable
    print(f"\n═══ sync-asana run ═══ {report['started_at'][:19]} {'[DRY-RUN]' if args.dry_run else ''}")
    print(f"\nStep 1: open linked tasks={report['step1']['open_linked']} | closed linked (30d)={report['step1']['closed_linked']}")
    s3_stripped = [s for s in report["step3_stripped"] if not s.get("skipped_open_owner")]
    s3_skipped = [s for s in report["step3_stripped"] if s.get("skipped_open_owner")]
    print(f"\nStep 3: stripped Gmail labels from {len(s3_stripped)} closed-task threads"
          f" (skipped {len(s3_skipped)} — open task still owns the thread)")
    for s in s3_stripped:
        names = ["Actions" if l == ACTIONS_LABEL else "Delegated" for l in s["labels_removed"]]
        print(f"  ✗ {s['thread_id']} | -{','.join(names)} | task: {s['task_name'][:60]}")
    for s in s3_skipped:
        print(f"  ⊝ {s['thread_id']} | skipped (open owner) | dup task: {s['task_name'][:60]}")
    print(f"\nStep 4: closed {len(report['step4_closures'])} open tasks where Gmail label removed"
          f" (exempt-skipped {len(report.get('step4_exempt', []))})")
    for c in report["step4_closures"]:
        print(f"  ✓ closed {c['task_gid']} | {c['task_name'][:60]}")
    for e in report.get("step4_exempt", []):
        print(f"  ⊝ exempt ({e['reason']}) | {e['task_name'][:60]}")
    print(f"\nStep 5: delegated open count = {report['step5']['open_delegated_count']}")
    print(f"\nStep 6: orphan candidates needing routing = {len(report['step6_orphan_candidates'])}")
    for o in report["step6_orphan_candidates"]:
        print(f"  ? {o['thread_id']} | from={o['from'][:40]} | subj={o['subject'][:50]} | labels={o['labels']}")
    print(f"\nStep 8 parity:")
    p = report["step8_parity"]
    print(f"  customers vault-only={p['customers_vault_only']} | gmail-only={p['customers_gmail_only']}")
    print(f"  suppliers vault-only={p['suppliers_vault_only']} | gmail-only={p['suppliers_gmail_only']}")
    print(f"  exempt suppliers (no label by design): {p['exempt_suppliers_noted']}")
    print()
    sys.exit(1 if report["step6_orphan_candidates"] else 0)

if __name__ == "__main__":
    main()
    _cc_pulse("run completed")


# --- Automations Log heartbeat (added 11 Jun 2026; non-fatal) ---
def _cc_pulse(summary: str):
    try:
        import sys as _s
        _s.path.insert(0, f"{VAULT}/Library/processes/scripts")
        import cc_publish
        cc_publish.pulse("sync-asana.py".replace(".py", ""), summary)
    except Exception:
        pass