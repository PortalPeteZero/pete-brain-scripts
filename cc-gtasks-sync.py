#!/usr/bin/env python3
"""cc-gtasks-sync.py — two-way sync between CC PDs (public.tasks) and the "Command Centre" Google Tasks list.

The date model (2026-07): only **PD** tasks (dated commitments) sync. P1-P4 stay CC-only. This mirrors every
open PD into Pete's "Command Centre" Google Tasks list (which shows on his calendar as checkbox tasks), and
pulls back phone-side adds / edits / completions.

Design (echo-proof, no dupes, no data loss):
  * MATCH by `gtask_id` stored on the CC row (claim on first sight → never duplicates).
  * FIELD-LEVEL 3-WAY MERGE against a last-synced snapshot (`gtask_synced_state` = {title,date,done} at the
    last sync). For each field, the side that CHANGED it since the snapshot wins; if both changed, CC wins
    (it's the brain). This is echo-proof (a write we just made matches the snapshot next run → no action) AND
    avoids reverting a genuine phone edit just because an unrelated CC write bumped updated_at.
  * Titles compare trim/NFC-tolerant (Google trims on store), so a trailing space never ping-pongs.
  * DELETE policy: a task deleted on Google (same list) → mark the CC PD DONE (never hard-delete). A CC PD
    hard-deleted in the app / by cc-park → a `gtask_tombstones` row → Pass 0 deletes the mirrored Google task
    (no resurrection). A list rename/recreate is detected (stale gtasklist_id) → re-mirror, never mass-complete.
  * DATES round-trip as bare YYYY-MM-DD (Google keeps only the date; no tz shift).
  * PD-only OUT: we only ever create/patch Google tasks from CC PDs. Phone-adds pulled IN become PDs (if dated)
    or undated P3 (if not) — captured either way.

Hardened after an adversarial review (2026-07-01): title-normalisation ping-pong, list-rename mass-complete,
non-atomic create+claim, PostgREST 1000-row cap, list pagination, and the field-level merge were all fixed;
verified live end-to-end (create, idempotency, date round-trip, pull, merge, completion, tombstone-delete).

Run:  VAULT=/tmp/pbs python3 cc-gtasks-sync.py --dry-run   (compute + print, NO writes)
      VAULT=/tmp/pbs python3 cc-gtasks-sync.py             (apply)
"""
# CRON-META
# key: cc-gtasks-sync
# what: Two-way sync between CC PDs (public.tasks) and Pete's "Command Centre" Google Tasks list.
# why: dated commitments (PDs) surface on Pete's phone/calendar as tasks, and phone-side adds/edits/completions flow back — decision 4 of the 2026-07 task model.
# reads: public.tasks (open PDs + claimed rows) + gtask_tombstones; Google Tasks API (GOOGLE_SA_JSON, DWD as pete@sygma)
# writes: Google Tasks "Command Centre" list; public.tasks (gtask_id/gtasklist_id/gtask_synced_state, pulled edits/completions/imports); clears gtask_tombstones
# entity: personal
# schedule: */5 * * * *
# timezone: Atlantic/Canary
# secrets: GOOGLE_SA_JSON
# note: PD-only, field-level 3-way merge, echo-proof. Client = tasks-api.py. Adversarially reviewed + live-verified 2026-07-01.
# CRON-META-END
import json, os, sys, datetime, unicodedata
import urllib.request, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")
CC_LIST_TITLE = "Command Centre"
DRY = "--dry-run" in sys.argv or "-n" in sys.argv


# ── CC (Supabase public.tasks) access ─────────────────────────────────────────
def _cc():
    url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        d = json.load(open(os.path.join(VAULT, "Library/processes/secrets/command-centre-supabase-keys.json")))
        url, key = d["url"], d["service_role_key"]
    return url.rstrip("/"), key

def cc_get(path):
    url, key = _cc()
    req = urllib.request.Request(f"{url}/rest/v1/{path}", headers={"apikey": key, "Authorization": "Bearer " + key})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def cc_patch(path, body):
    url, key = _cc()
    req = urllib.request.Request(f"{url}/rest/v1/{path}", method="PATCH", data=json.dumps(body).encode(),
        headers={"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json", "Prefer": "return=minimal"})
    urllib.request.urlopen(req, timeout=30).read()

def cc_insert(body):
    url, key = _cc()
    req = urllib.request.Request(f"{url}/rest/v1/tasks", method="POST", data=json.dumps(body).encode(),
        headers={"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json", "Prefer": "return=representation"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def cc_delete(path):
    url, key = _cc()
    req = urllib.request.Request(f"{url}/rest/v1/{path}", method="DELETE",
        headers={"apikey": key, "Authorization": "Bearer " + key, "Prefer": "return=minimal"})
    urllib.request.urlopen(req, timeout=30).read()


def _norm(s):
    """Normalise a title for comparison the way Google Tasks stores it: NFC-unicode + trimmed. Without this,
    Google trimming a trailing space (or normalising unicode) makes a title compare unequal forever, so the
    sync re-pushes the same row every run (the updated_at trigger keeps CC 'newest'). Compare-only; we don't
    mutate the stored name."""
    return unicodedata.normalize("NFC", (s or "")).strip()


def _teq(a, b):
    """Title equality that tolerates Google's on-store trim/normalise."""
    return _norm(a) == _norm(b)


def _eqv(a, b):
    return a == b


def _merge(cc_v, g_v, b_v, eq):
    """3-way field merge against the last-synced base: the side that CHANGED the field since the base wins.
    If both changed (or there's no base yet), CC wins (it's the brain). This is what stops a record-level
    last-writer-wins from reverting a genuine phone edit just because an unrelated CC write bumped updated_at."""
    if eq(cc_v, g_v):
        return cc_v
    cc_changed = not eq(cc_v, b_v)
    g_changed = not eq(g_v, b_v)
    if g_changed and not cc_changed:
        return g_v
    return cc_v


def _ts(s):
    """Parse an ISO/RFC3339 timestamp to epoch seconds; None/'' -> 0."""
    if not s:
        return 0.0
    s = s.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        # Google sometimes returns millis with 'Z' already handled; fall back to date only
        try:
            return datetime.datetime.fromisoformat(s[:19] + "+00:00").timestamp()
        except ValueError:
            return 0.0


def main():
    spec = importlib.util.spec_from_file_location("tasks_api", os.path.join(VAULT, "tasks-api.py"))
    tmod = importlib.util.module_from_spec(spec); spec.loader.exec_module(tmod)
    api = tmod.TasksAPI()
    date_of = api.date_of

    lst = api.get_or_create_list(CC_LIST_TITLE)
    list_id = lst["id"]

    # CC side: open PDs + any row already claimed by a gtask_id (to reconcile completion/deletion).
    # Explicit high limit: PostgREST silently caps a plain GET (default ~1000 rows). A truncated `claimed`
    # set would drop a claimed row from cc_by_gtask and Pass C would re-import its Google task as a duplicate.
    # LIMIT is far above Pete's task count; if either ever hits it we warn (a real cap, not a silent dup).
    LIMIT = 5000
    cols = "id,name,due_on,priority,base_priority,status,completed_at,updated_at,gtask_id,gtasklist_id,gtask_synced_at,gtask_synced_state"
    open_pds = cc_get(f"tasks?priority=eq.PD&completed_at=is.null&select={cols}&order=id&limit={LIMIT}")
    claimed = cc_get(f"tasks?gtask_id=not.is.null&select={cols}&order=id&limit={LIMIT}")
    if len(open_pds) >= LIMIT or len(claimed) >= LIMIT:
        print(f"WARNING: CC task query hit the {LIMIT}-row limit — add pagination before trusting the sync.", file=sys.stderr)
    rows = {r["id"]: r for r in open_pds}
    for r in claimed:
        rows.setdefault(r["id"], r)
    cc_by_gtask = {r["gtask_id"]: r for r in rows.values() if r.get("gtask_id")}

    # Google side: everything in the CC list (completed + hidden + deleted)
    g_tasks = api.list_tasks(list_id)
    g_by_id = {t["id"]: t for t in g_tasks}

    log = []
    def act(msg):
        log.append(msg)

    # ── Pass 0: tombstones — PDs hard-deleted in the CC app / by cc-park. Delete the mirrored Google task
    # (so it does NOT get re-imported as a phantom phone-add), then clear the tombstone. ──────────────────
    tombs = cc_get("gtask_tombstones?select=gtask_id,gtasklist_id")
    tomb_ids = {t["gtask_id"] for t in tombs}
    for t in tombs:
        gid = t["gtask_id"]
        act(f"TOMBSTONE → delete Google task {gid}")
        if not DRY:
            try:
                api.delete_task(t.get("gtasklist_id") or list_id, gid)
            except Exception:
                pass  # already gone on the Google side
            cc_delete(f"gtask_tombstones?gtask_id=eq.{gid}")

    # ── Pass A: reconcile matched pairs (CC rows that already have a gtask_id) ──
    for r in list(rows.values()):
        gid = r.get("gtask_id")
        if not gid:
            continue
        g = g_by_id.get(gid)
        cc_done = r.get("completed_at") is not None
        if g is None:
            # A gtask_id missing from THIS list's task set only means "deleted" when the row was actually
            # claimed IN THIS list. If the row carries a DIFFERENT gtasklist_id, the list was renamed or
            # recreated on the phone (get_or_create_list matches by title → a new/empty list_id), and every
            # claimed PD would look "deleted" → mass-completion. Guard: same-list miss → Google-side delete →
            # CC done; other-list (or unknown) miss → re-mirror fresh (clear the claim so Pass B re-creates),
            # never complete.
            if r.get("gtasklist_id") == list_id:
                if not cc_done:
                    act(f"GOOGLE-DELETED → CC done: '{r['name'][:60]}'")
                    if not DRY:
                        cc_patch(f"tasks?id=eq.{r['id']}", {"status": "done", "completed_at": _now_iso()})
            else:
                act(f"STALE LIST ({r.get('gtasklist_id')}≠{list_id}) → re-mirror: '{r['name'][:50]}'")
                if not DRY:
                    cc_patch(f"tasks?id=eq.{r['id']}", {"gtask_id": None, "gtasklist_id": None, "gtask_synced_at": None})
                r["gtask_id"] = None  # so Pass B re-creates it in the current list this same run
            continue
        if g.get("deleted"):
            if not cc_done:
                act(f"GOOGLE-DELETED(flag) → CC done: '{r['name'][:60]}'")
                if not DRY:
                    cc_patch(f"tasks?id=eq.{r['id']}", {"status": "done", "completed_at": _now_iso()})
            continue
        g_done = g.get("status") == "completed"
        g_name = g.get("title", "") or ""
        g_date = date_of(g.get("due"))
        cc_name = r["name"]; cc_date = r.get("due_on")
        # Field-level 3-way merge against the last-synced snapshot (gtask_synced_state).
        base = r.get("gtask_synced_state") or {}
        if isinstance(base, str):            # defensive: a legacy row stored the snapshot as a JSON string
            try:
                base = json.loads(base)
            except Exception:
                base = {}
        res_name = _merge(cc_name, g_name, base.get("title"), _teq)
        res_date = _merge(cc_date, g_date, base.get("date"), _eqv)
        res_done = _merge(cc_done, g_done, base.get("done"), _eqv)
        new_state = {"title": res_name, "date": res_date, "done": bool(res_done)}
        push_needed = (not _teq(res_name, g_name)) or (res_date != g_date) or (bool(res_done) != g_done)
        pull_needed = (not _teq(res_name, cc_name)) or (res_date != cc_date) or (bool(res_done) != cc_done)
        if not push_needed and not pull_needed:
            if base != new_state and not DRY:   # only the snapshot drifted (e.g. Google trimmed the title)
                cc_patch(f"tasks?id=eq.{r['id']}", {"gtask_synced_state": new_state})
            continue
        if push_needed:
            act(f"PUSH CC→G: '{res_name[:50]}' | date {res_date} | {'done' if res_done else 'open'}")
            if not DRY:
                if res_done and not g_done:
                    api.complete_task(list_id, gid)
                elif not res_done and g_done:
                    api.uncomplete_task(list_id, gid, due_date=res_date)
                api.patch_task(list_id, gid, title=res_name, due_date=res_date)
        if pull_needed:
            act(f"PULL G→CC: '{res_name[:50]}' | date {res_date} | {'done' if res_done else 'open'}")
        if not DRY:
            patch = {"gtask_synced_state": new_state}
            if not _teq(res_name, cc_name):
                patch["name"] = res_name
            if res_date != cc_date:
                patch["due_on"] = res_date
            if bool(res_done) != cc_done:
                patch.update({"status": "done", "completed_at": _now_iso()} if res_done else {"status": "todo", "completed_at": None})
            cc_patch(f"tasks?id=eq.{r['id']}", patch)

    # ── Pass B: open PDs with no gtask_id yet → create in Google ──────────────
    for r in open_pds:
        if r.get("gtask_id"):
            continue
        act(f"CREATE in Google: '{r['name'][:50]}' | date {r.get('due_on')}")
        if not DRY:
            created = api.insert_task(list_id, title=r["name"], due_date=r.get("due_on"))
            # Atomic-ish claim: if the write-back of gtask_id fails, ROLL BACK the Google task we just made —
            # otherwise it's an orphan that Pass B re-creates (duplicate) and Pass C re-imports next run.
            try:
                seed = {"title": r["name"], "date": r.get("due_on"), "done": False}   # snapshot = what we just mirrored
                cc_patch(f"tasks?id=eq.{r['id']}", {
                    "gtask_id": created["id"], "gtasklist_id": list_id,
                    "gtask_synced_at": created.get("updated"), "gtask_synced_state": seed})
                cc_by_gtask[created["id"]] = r
            except Exception as e:
                try:
                    api.delete_task(list_id, created["id"])
                except Exception:
                    pass
                act(f"  ! claim-back failed, rolled back the Google task ({e}); will retry next run")

    # ── Pass C: Google tasks nobody in CC claims → import (phone-adds) ─────────
    for g in g_tasks:
        if g.get("deleted") or g.get("status") == "completed":
            continue
        if g["id"] in cc_by_gtask:
            continue
        if g["id"] in tomb_ids:
            continue  # just tombstoned/deleted this run — never re-import
        g_date = date_of(g.get("due"))
        priority = "PD" if g_date else "P3"
        base = "P2" if g_date else "P3"
        act(f"IMPORT G→CC (new phone-add): '{(g.get('title') or '(untitled)')[:50]}' | date {g_date} | {priority}")
        if not DRY:
            cc_insert({
                "name": g.get("title") or "(untitled Google task)",
                "priority": priority, "base_priority": base, "due_on": g_date,
                "entity_slug": None, "project_slug": "General", "status": "todo", "source": "gtasks",
                "notes": "Added from Google Tasks (Command Centre list).",
                "gtask_id": g["id"], "gtasklist_id": list_id, "gtask_synced_at": g.get("updated"),
                "gtask_synced_state": {"title": g.get("title") or "(untitled Google task)", "date": g_date, "done": False},
            })

    mode = "DRY-RUN (no writes)" if DRY else "APPLIED"
    print(f"cc-gtasks-sync [{mode}] · list='{CC_LIST_TITLE}' ({list_id}) · CC PDs open={len(open_pds)} · Google tasks={len(g_tasks)}")
    if log:
        print(f"{len(log)} action(s):")
        for m in log:
            print("  -", m)
    else:
        print("in sync — no actions.")
    return 0


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


if __name__ == "__main__":
    sys.exit(main())
