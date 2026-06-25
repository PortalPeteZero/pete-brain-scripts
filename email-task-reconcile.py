#!/usr/bin/env python3
"""email-task-reconcile.py — close-on-ship for Pete's CC tasks (public.tasks).

The durable fix for "finished the work, never ticked the task off". Given the things a
session just shipped (a task id, or a keyword that appears in the task's name/notes), it
finds the matching OPEN tasks and — with --apply — marks them done (status='done') with an
audit note. Lists first; never closes on assumption. Asana is Jane's only; this never touches it.

(Replaces asana-reconcile.py, whose evidence model was wired to pre-cutover infra — local
/code repos + the retired Daily/ vault folder. This is the cloud-native close-on-ship.)

Usage:
  email-task-reconcile.py --ship <id|keyword> [<id|keyword> ...]   # list the matches
  email-task-reconcile.py --ship <...> --apply                     # close the matches
"""
import sys, os, json, subprocess
from datetime import datetime, timezone

VAULT = os.environ.get("VAULT", "/tmp/pbs")
CC_SQL = os.path.join(VAULT, "cc-sql.py")

def cc_sql(query):
    r = subprocess.run([sys.executable, CC_SQL, query], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=60)
    if r.returncode != 0:
        sys.stderr.write(f"cc-sql error: {(r.stderr or r.stdout)[:300]}\n"); sys.exit(2)
    out = (r.stdout or "").strip()
    if not out:
        return []
    try:
        d = json.loads(out); return d if isinstance(d, list) else []
    except json.JSONDecodeError:
        return []

def sql_q(v):
    return "'" + str(v).replace("'", "''") + "'"

def main():
    args = sys.argv[1:]
    if "--ship" not in args:
        sys.exit(__doc__)
    apply_close = "--apply" in args
    terms = [a for a in args[args.index("--ship") + 1:] if not a.startswith("--")]
    if not terms:
        sys.exit("usage: email-task-reconcile.py --ship <id|keyword> [...] [--apply]")

    rows = cc_sql("SELECT id, name, priority, project_slug, notes FROM tasks WHERE status != 'done'")
    hits = []
    for r in rows:
        hay = ((r.get("name") or "") + " " + (r.get("notes") or "")).lower()
        for term in terms:
            if str(r["id"]) == term or term.lower() in hay:
                hits.append((r, term)); break

    if not hits:
        print("No open task matches the shipped item(s).")
        return
    print("Open tasks matching what shipped — check before closing:")
    for r, term in hits:
        print(f"  [{r.get('priority') or '—'}] {(r.get('name') or '')[:60]} | "
              f"{r.get('project_slug') or '—'} | {r['id']} (matched: {term})")
    if not apply_close:
        print("\nRe-run with --apply to close these with an audit note.")
        return

    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
    for r, term in hits:
        audit = (f"\n\nClosed by reconcile {stamp} UTC — shipped this session (matched '{term}'). "
                 "Verify the linked work landed.")
        cc_sql(f"UPDATE tasks SET status='done', updated_at=now(), "
               f"notes = coalesce(notes,'') || {sql_q(audit)} WHERE id = {sql_q(r['id'])}")
        print(f"  ✓ closed {r['id']} | {(r.get('name') or '')[:50]}")

if __name__ == "__main__":
    main()
