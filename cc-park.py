#!/usr/bin/env python3
"""cc-park.py — backlog lifecycle for the tasks-vs-backlog model.

  park    : task -> project backlog note (append to section), delete the task, ensure P4 pointer.
  done    : remove a backlog line (completed); prune the pointer if the backlog is now empty.
  promote : backlog line -> a real task (P + due); remove the line; prune pointer if empty.
  show    : print a project's backlog note.

Backlog note = vault_notes (type backlog, slug {project_slug}-backlog, sectioned). Tasks = public.tasks.
DB is SSOT; no trail (completed/parked items just move). See [[ways-of-working-tasks-vs-backlog]].

  VAULT=/tmp/pbs python3 cc-park.py park    --task <id> --project <slug> --section "<S>" [--item "<text>"]
  VAULT=/tmp/pbs python3 cc-park.py done    --project <slug> --match "<substring>"
  VAULT=/tmp/pbs python3 cc-park.py promote --project <slug> --match "<substring>" --priority P2 [--due YYYY-MM-DD]
  VAULT=/tmp/pbs python3 cc-park.py show    --project <slug>
"""
import json, os, sys, urllib.request, urllib.parse, argparse, datetime, subprocess, re

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}

def rest(method, path, body=None, prefer=None):
    h = dict(H)
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(f"{URL}/rest/v1/{path}", data=(json.dumps(body).encode() if body is not None else None), headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode(); return json.loads(txt) if txt.strip() else None

def get_note(slug):
    rows = rest("GET", f"vault_notes?slug=eq.{urllib.parse.quote(slug)}&select=vault_path,title,body,entity")
    return rows[0] if rows else None

def sanitize(s):
    return s.replace("\n", " ").replace("|", "/").strip()

def append_to_section(body, section, item):
    line = f"- [ ] {sanitize(item)}"
    marker = f"## {section}"
    if marker in body:
        start = body.index(marker); nxt = body.find("\n## ", start + len(marker))
        if nxt == -1: return body.rstrip() + "\n" + line + "\n"
        return body[:nxt].rstrip() + "\n" + line + "\n" + body[nxt:]
    return body.rstrip() + f"\n\n## {section}\n{line}\n"

def open_items(body):
    return [l for l in body.splitlines() if l.strip().startswith("- [ ]")]

def proj_meta(project_slug):
    rows = rest("GET", f"projects?slug=eq.{urllib.parse.quote(project_slug)}&select=name,entity_slug")
    if rows: return rows[0]["name"], rows[0].get("entity_slug")
    return project_slug, None

def pointer_name(project_name): return f"Work through {project_name} backlog"

def ensure_pointer(project_slug, project_name, entity):
    name = pointer_name(project_name)
    if rest("GET", f"tasks?name=eq.{urllib.parse.quote(name)}&status=eq.todo&select=id"): return "exists"
    rest("POST", "tasks", {"name": name, "priority": "P4", "due_on": None, "entity_slug": entity,
         "project_slug": project_slug, "status": "todo", "source": "claude",
         "notes": f"Pointer to the {project_name} backlog ([[{project_slug}-backlog]]). Bump priority/date to schedule a session; back to P4 after."},
         prefer="return=minimal"); return "created"

def prune_pointer_if_empty(project_slug, body):
    """If no open items remain, delete the P4 pointer (and the empty note)."""
    if open_items(body): return "kept"
    pname, _ = proj_meta(project_slug)
    rest("DELETE", f"tasks?name=eq.{urllib.parse.quote(pointer_name(pname))}&status=eq.todo", prefer="return=minimal")
    rest("DELETE", f"vault_notes?slug=eq.{project_slug}-backlog", prefer="return=minimal")
    return "pruned (pointer + empty note removed)"

def write_note(project_slug, title, body, entity):
    bslug = f"{project_slug}-backlog"; vpath = f"Projects/{project_slug}/backlog.md"
    fm = {"type": "backlog", "slug": bslug, "title": title, "entity": (entity or ""), "tags": [project_slug, "backlog"], "project": project_slug}
    rest("POST", "vault_notes?on_conflict=vault_path", {"vault_path": vpath, "slug": bslug, "type": "backlog",
         "entity": (entity or ""), "title": title, "body": body, "frontmatter": fm,
         "tags": [project_slug, "backlog"], "links": [], "word_count": len(body.split()),
         "source_updated": datetime.date.today().isoformat(), "embedding": None}, prefer="resolution=merge-duplicates,return=minimal")

def backfill():
    try: subprocess.run([sys.executable, f"{VAULT}/cc-knowledge-embed-backfill.py"], capture_output=True, timeout=60, env={**os.environ, "VAULT": VAULT})
    except Exception: pass

def park(task_id, project_slug, section, item=None):
    t = rest("GET", f"tasks?id=eq.{task_id}&select=id,name,entity_slug,gtask_id,gtasklist_id")
    if not t: sys.exit(f"task {task_id} not found")
    item = item or t[0]["name"]
    pname, pentity = proj_meta(project_slug)
    note = get_note(f"{project_slug}-backlog")
    if note: body, title, ent = append_to_section(note["body"], section, item), note["title"], note.get("entity") or pentity
    else:
        title = f"{pname} — backlog"
        body = f"# {title}\n\nThe \"next time we work on {pname}\" worklist. Pointer task keeps it on the radar.\n\n## {section}\n- [ ] {sanitize(item)}\n"
        ent = pentity
    write_note(project_slug, title, body, ent)
    # If the parked task is a synced PD, tombstone its gtask_id so the Google Tasks sync deletes the mirrored
    # Google task instead of re-importing it (resurrection). The sync clears the tombstone.
    if t[0].get("gtask_id"):
        rest("POST", "gtask_tombstones?on_conflict=gtask_id",
             {"gtask_id": t[0]["gtask_id"], "gtasklist_id": t[0].get("gtasklist_id")},
             prefer="resolution=merge-duplicates,return=minimal")
    rest("DELETE", f"tasks?id=eq.{task_id}", prefer="return=minimal")
    ptr = ensure_pointer(project_slug, pname, pentity); backfill()
    print(f"PARKED: '{sanitize(item)[:50]}' -> {project_slug}-backlog [{section}] | task deleted | pointer={ptr}")

def find_line(body, match):
    for l in body.splitlines():
        if l.strip().startswith("- [") and match.lower() in l.lower(): return l
    return None

def done(project_slug, match):
    note = get_note(f"{project_slug}-backlog")
    if not note: sys.exit("no backlog note")
    line = find_line(note["body"], match)
    if not line: sys.exit(f"no backlog line matching '{match}'")
    body = "\n".join(l for l in note["body"].splitlines() if l != line) + "\n"
    write_note(project_slug, note["title"], body, note.get("entity"))
    pr = prune_pointer_if_empty(project_slug, body); backfill()
    print(f"DONE: removed '{line.strip()[:50]}' | pointer={pr}")

def promote(project_slug, match, priority, due):
    note = get_note(f"{project_slug}-backlog")
    if not note: sys.exit("no backlog note")
    line = find_line(note["body"], match)
    if not line: sys.exit(f"no backlog line matching '{match}'")
    text = re.sub(r"^- \[.\]\s*", "", line.strip())
    pname, pentity = proj_meta(project_slug)
    # The date is the switch (2026-07 task model): a promoted item WITH a date becomes a PD, and the
    # requested tier is recorded as base_priority so clearing the date later reverts to it. Without a date
    # it stays the plain undated tier. Kills the old "dated P2" hole.
    task_body = {"name": text, "priority": ("PD" if due else priority), "due_on": due, "entity_slug": pentity,
         "project_slug": project_slug, "status": "todo", "source": "claude",
         "notes": f"Promoted from the {pname} backlog."}
    if due: task_body["base_priority"] = priority
    rest("POST", "tasks", task_body, prefer="return=minimal")
    body = "\n".join(l for l in note["body"].splitlines() if l != line) + "\n"
    write_note(project_slug, note["title"], body, note.get("entity"))
    pr = prune_pointer_if_empty(project_slug, body); backfill()
    print(f"PROMOTED: '{text[:50]}' -> task ({priority}{', due '+due if due else ''}) | line removed | pointer={pr}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("park"); p.add_argument("--task"); p.add_argument("--project"); p.add_argument("--section"); p.add_argument("--item")
    d = sub.add_parser("done"); d.add_argument("--project"); d.add_argument("--match")
    pr = sub.add_parser("promote"); pr.add_argument("--project"); pr.add_argument("--match"); pr.add_argument("--priority", default="P2"); pr.add_argument("--due")
    s = sub.add_parser("show"); s.add_argument("--project")
    a = ap.parse_args()
    if a.cmd == "park": park(a.task, a.project, a.section, a.item)
    elif a.cmd == "done": done(a.project, a.match)
    elif a.cmd == "promote": promote(a.project, a.match, a.priority, a.due)
    elif a.cmd == "show": show = get_note(f"{a.project}-backlog"); print(show["body"] if show else "(no backlog note)")
    else: ap.print_help()
