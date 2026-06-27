#!/usr/bin/env python3
"""vault-notes-dup-guard.py — watchdog for body-duplication corruption in the CC vault_notes.

WHY THIS EXISTS: on 2026-06-20 a one-off bulk-loader populated vault_notes in a single ~3-minute
burst and, through a body-variable-reuse bug, wrote ONE note's body onto MANY unrelated notes
(right title, wrong body). ~147 folder/README notes were corrupted; it sat unnoticed for a week.
This guard makes that class of fault impossible to miss again: it detects any body shared across
MORE THAN ONE distinct title (the corruption signature — legitimate identical-boilerplate notes
share the SAME title too, so they are NOT flagged) and raises a single CC task.

Detection runs server-side via the read-only SQL function public.vault_notes_body_dups()
(length(body)>200 AND a body spanning >1 distinct title). READ-ONLY against vault_notes — this
guard never edits or deletes a note; it only observes and raises/clears one tracking task.

# CRON-META
# what: watchdog — flag vault_notes body-duplication corruption (one body across many notes) and raise/clear a CC task
# why: a 20 Jun 2026 bulk-load fanned one body onto ~147 notes and went unnoticed for a week — Pete: never again, make it self-announce
# reads: CC public.vault_notes via the read-only RPC vault_notes_body_dups()
# writes: CC public.tasks ONLY — upserts/clears a single [dup-guard] tracking task. Never touches vault_notes.
# entity: personal
# schedule: 0 9 * * *
# timezone: Atlantic/Canary
# CRON-META-END
"""
import os, json, uuid, sys, urllib.request, urllib.error
from collections import defaultdict

VAULT = os.environ.get("VAULT", "/tmp/pbs")
KEYS = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))
URL, SVC = KEYS["url"].rstrip("/"), KEYS["service_role_key"]
MARKER = "[dup-guard]"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, method=method,
        headers={"apikey": SVC, "Authorization": f"Bearer {SVC}", "Content-Type": "application/json",
                 "Prefer": "return=representation"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            txt = resp.read().decode()
            return resp.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def build_notes(rows):
    """Plain-English summary for the CC operating surface (Pete reads this)."""
    clusters = defaultdict(list)
    for r in rows:
        clusters[r["body_md5"]].append(r)
    lines = [
        f"{len(rows)} notes are carrying another note's body (right title, wrong contents).",
        "Cause: the one-off bulk-load on 20 Jun 2026 reused a body value across notes. Not the live system; nothing scheduled does this.",
        "These notes show the WRONG text right now and should be restored (true content lives in Google Drive) or cleared.",
        "",
        f"{len(clusters)} groups (each group = many notes sharing one body):",
    ]
    for h, g in sorted(clusters.items(), key=lambda x: -len(x[1])):
        first = (g[0]["first_line"] or "").lstrip("# ").strip()[:50]
        lines.append(f"  • {len(g)} notes wrongly showing “{first}” (e.g. {g[0]['vault_path']})")
    lines += ["", "Full live list any time:  SELECT * FROM public.vault_notes_body_dups();"]
    return "\n".join(lines)


def main():
    status, rows = req("POST", "rpc/vault_notes_body_dups", {})
    if status >= 300:
        print(f"dup-guard: detector RPC failed ({status}): {rows}", file=sys.stderr)
        return 1
    rows = rows or []
    # find an existing open tracking task (idempotent — one task, updated in place)
    st, existing = req("GET", f"tasks?select=id&status=eq.todo&name=like.{MARKER}*")
    existing_id = existing[0]["id"] if (isinstance(existing, list) and existing) else None

    if not rows:
        if existing_id:
            req("PATCH", f"tasks?id=eq.{existing_id}",
                {"status": "done", "completed_at": "now",
                 "notes": "vault_notes body-duplication cleared — 0 notes affected. Auto-closed by vault-notes-dup-guard."})
            print("dup-guard: CLEAN — 0 duplicated bodies; closed the open tracking task.")
        else:
            print("dup-guard: CLEAN — 0 duplicated bodies.")
        return 0

    name = f"{MARKER} {len(rows)} notes carry another note's body (20-Jun bulk-load corruption)"
    notes = build_notes(rows)
    if existing_id:
        req("PATCH", f"tasks?id=eq.{existing_id}", {"name": name, "notes": notes, "priority": "P2"})
        print(f"dup-guard: {len(rows)} corrupted notes — updated tracking task {existing_id}")
    else:
        payload = {"id": str(uuid.uuid4()), "name": name, "notes": notes, "priority": "P2",
                   "entity_slug": "Personal", "project_slug": "PA-Command-Centre",
                   "status": "todo", "source": "claude"}
        st2, ins = req("POST", "tasks", payload)
        if st2 >= 300:
            print(f"dup-guard: found {len(rows)} corrupted but task insert failed ({st2}): {ins}", file=sys.stderr)
            return 1
        print(f"dup-guard: {len(rows)} corrupted notes — raised tracking task {payload['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
