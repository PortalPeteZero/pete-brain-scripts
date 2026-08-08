#!/usr/bin/env python3
"""clancy-dn-capture.py — ONE command that lands a captured Depotnet incident in the CC.

THE PROBLEM THIS SOLVES: Depotnet holds far more per damage than the two bulk exports carry —
the 18 structured Incident Questions, the whole investigation (root cause, underlying cause,
lessons learnt, CAT/Genny mode usage), every attached document and photo, and per-action closure
detail. None of it is reachable by API for us (see AUTH note below), so it is captured through
Pete's logged-in Chrome and landed here.

RUN THE CHROME SIDE FIRST (the exact snippets live in [[clancy-depotnet-capture]]), then:

  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-capture.py <incident_id> --files <dir> [--actions <json>]
  ... --dry-run     say what would happen, write nothing

What it does, in order:
  1. reads the incident from `clancy_dn_incidents` (refuses if the register import has not run)
  2. creates Drive: External Sygma Solutions ▸ Clancy External ▸ Depotnet Damages ▸
     "{id} {town} ({utility}, {dd-mm-yyyy})" with documents/ + photos/ subfolders
  3. uploads every file in <dir> (PDF at the root, gallery_*/photo names to photos/, rest to
     documents/) and indexes them in `clancy_dn_files`
  4. parses the incident PDF via clancy-dn-pdf.py → `clancy_dn_answers` + promoted columns
  5. applies action closures from --actions json → `clancy_dn_actions.closed_at/closed_by`
  6. stamps `capture_drive_folder` + `pdf_captured_at` on the incident

Idempotent: re-running re-uploads nothing that is already named the same in the folder it
re-uses, and every DB write is an upsert.

AUTH note (checked 31 Jul 2026, do not re-litigate without new evidence): the Depotnet BI feeds
(clancy-bi.depotnet.co.uk/incidents.json, /incident-answers.json — refreshed 5x daily) return 401
"Invalid Credentials" for Pete's Clancy Microsoft account; Power BI is a viewer-only installed app
with no dataset access. If Depotnet ever issue feed credentials, this whole manual loop is
replaced by a scheduled pull — that is the upgrade path, not more scraping.
"""
import os, sys, re, json, argparse, subprocess, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]

# Drive: External Sygma Solutions ▸ Clancy External ▸ Depotnet Damages (customer-facing, Pete 31 Jul)
DAMAGES_FOLDER = "19XZoec62Zjo02EQKWsrWszpOGcivG8aE"
DRIVE = f"{VAULT}/drive-api.py"
PDF_PARSER = f"{VAULT}/clancy-dn-pdf.py"


def rest(path, method="GET", body=None, headers=None):
    h = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 data=(json.dumps(body).encode() if body is not None else None),
                                 headers=h, method=method)
    with urllib.request.urlopen(req, timeout=180) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def _all_rows(rest_fn, table_and_query):
    """Every row, keyset-paged on id. PostgREST caps a response at 1000 rows however large a
    `limit=` you pass and says nothing, so `limit=10000` on a growing table silently returns the
    first 1000 as if it were the lot. Harmless while clancy_dn_incidents held 535 rows (8 Aug 2026);
    the moment the register passes 1000, an unpaged read here makes real damages look NEW and
    re-inserts them. Added 8 Aug 2026 after the same cap cost an evening elsewhere."""
    out, last = [], None
    sep = "&" if "?" in table_and_query else "?"
    while True:
        q = f"{table_and_query}{sep}order=id.asc&limit=1000"
        if last is not None:
            q += f"&id=gt.{last}"
        batch = rest_fn(q)
        if not batch:
            break
        out += batch
        last = batch[-1]["id"]
        if len(batch) < 1000:
            break
    return out


def drive(*args):
    r = subprocess.run(["python3", DRIVE, *args], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    m = re.search(r"ID: ([\w-]+)", r.stdout)
    return m.group(1) if m else None


def _children(parent):
    """(name, id) of everything directly under a Drive folder."""
    r = subprocess.run(["python3", DRIVE, "ls", parent], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r"^(DIR|FILE)\s+\S+\s+\S+\s+([\w-]{20,})\s+(.*)$", line.strip())
        if m:
            out.append((m.group(3).strip(), m.group(2)))
    return out


def incident_folder(inc):
    """A damage's Drive home — created ONCE, reused for ever after.

    This used to be a bare `create-folder`, which makes a NEW folder every run. Drive allows
    duplicate names, so nothing complained: on 1 Aug 2026 damage 133852 ended up with THREE
    folders, and `capture_drive_folder` pointed at an empty one while the real files sat in the
    first. Matching on the full name would not have saved it either — the name embeds location
    and utility_class, and both had changed between runs.

    So the match is on the ID PREFIX (`"133852 "`), which is the only stable part.
    """
    want = f"{inc['id']} "
    hits = [(n, f) for n, f in _children(DAMAGES_FOLDER) if n.startswith(want)]
    if len(hits) > 1:
        # Duplicates already exist from before this fix. Take the one that actually holds the
        # files — the empty shells sort no differently by name, so content is the only tiebreak.
        hits.sort(key=lambda h: -sum(1 for _ in _children(h[1])))
        print(f"  !! {len(hits)} Drive folders exist for damage {inc['id']} — using the fullest "
              f"({hits[0][0][:60]}). Merge and bin the empties.")
    if hits:
        print(f"  drive folder: reusing {hits[0][0]}")
        return hits[0][1]
    fid = drive("create-folder", folder_name(inc), DAMAGES_FOLDER)
    print(f"  drive folder: created {folder_name(inc)}")
    return fid


def subfolder(parent, name):
    """documents/ or photos/ under a damage's home — reused, never re-created."""
    for n, fid in _children(parent):
        if n == name:
            return fid
    return drive("create-folder", name, parent)


def folder_name(inc):
    town = (inc.get("location") or "location not stated")[:48]
    util = (inc.get("utility_class") or "utility not classified").replace("Electric — ", "Electric ")
    d = (inc.get("incident_date") or "")[:10]
    d = "-".join(reversed(d.split("-"))) if d else "no date"
    return f"{inc['id']} {town} ({util}, {d})"


def is_photo(name):
    return bool(re.match(r"^(gallery_|Strike Area|IMG[_-]|photo)", name, re.I)) or \
        (name.lower().endswith((".jpg", ".jpeg", ".png", ".heic")) and not name.lower().startswith("incident-"))


def queue(limit, fy=None, oldest=False, with_actions_only=False):
    """WHERE DO I START? — the resumable pointer. Prints what is still uncaptured, newest first,
    so a session never has to be told which incidents to do. Capture is stateful in the DB
    (`pdf_captured_at`), so this survives sessions, crashes and re-runs."""
    q = "clancy_dn_incidents?select=id,incident_date,fy,contract_family,location,status,pdf_captured_at"
    q += "&pdf_captured_at=is.null"
    if fy:
        q += f"&fy=eq.{urllib.request.quote(fy)}"
    q += f"&order=incident_date.{'asc' if oldest else 'desc'}&limit={limit}"
    todo = rest(q)
    done = rest("clancy_dn_incidents?select=id,incident_date&pdf_captured_at=not.is.null"
                "&order=incident_date.desc&limit=1")
    tot = _all_rows(rest, "clancy_dn_incidents?select=id")
    cap = _all_rows(rest, "clancy_dn_incidents?select=id&pdf_captured_at=not.is.null")
    _acts = _all_rows(rest, "clancy_dn_actions?select=id,incident_id,date_raised")
    have_actions = {a["incident_id"] for a in _acts}
    # There is no export "high-water mark" to worry about. Pete, 1 Aug 2026: there are no Service
    # Damage actions on Depotnet after 23 Jun 2026 at all, and both registers import to the same
    # day. So a damage with no action has no action — it is not a gap in what we can see.
    print(f"captured {len(cap)} of {len(tot)} incidents"
          + (f" · newest captured: {done[0]['id']} ({(done[0]['incident_date'] or '')[:10]})" if done else " · none captured yet"))
    if not todo:
        print("nothing left to capture" + (f" in {fy}" if fy else ""))
        return
    print("\nA damage is NOT captured until BOTH sides are done: the incident (PDF, documents,"
          "\nphotos) AND its actions (closure detail + each action's own photos/videos/documents).")
    print(f"\nnext {len(todo)} to capture ({'oldest' if oldest else 'newest'} first"
          + (f", {fy}" if fy else "") + "):")
    for r in todo:
        if with_actions_only and r["id"] not in have_actions:
            continue
        # EVERY entry states its action work, including the ones where we cannot see any.
        # A blank used to mean "nothing to do here", but 16 of this year's damages post-date the
        # actions export, so a blank was really "we do not know". Pete, 31 Jul: he should never
        # have to ask whether the actions were done. The queue says it every time.
        n_act = sum(1 for a in have_actions if a == r["id"])
        # Three DISTINCT states — "none outstanding" and "none recorded in Depotnet" are not the
        # same thing and must never share wording (Pete, 31 Jul).
        if r["id"] in have_actions:
            acts = " ·  ACTIONS RECORDED"
            todo_line = (
                f"      ACTIONS ({n_act} recorded): open BOTH tabs (Outstanding + Closed).\n"
                "               From the Closed grid take Created, Assigned To, CLOSED and CLOSED BY —\n"
                "               closed_at/closed_by exist ONLY here, never in the Action Report export.\n"
                "               Then click View on EVERY action. The modal holds Location, and its own\n"
                "               Photos / Videos / Documents / Timeline tabs. On damage 133852 that was 10\n"
                "               documents and 57 timeline entries that nothing else in Depotnet exposes.\n"
                "               Land it with:  --actions <json>  (see --actions-help)")
        else:
            acts = " ·  NONE recorded in our export"
            todo_line = ("      ACTIONS: our export holds no action record for this damage — meaning none was ever RAISED,\n"
                         "               not that none is outstanding. Open both tabs to confirm, then set capture_actions='none'.")
        print(f"  {r['id']}  {(r['incident_date'] or '')[:10]}  {r['fy']}  "
              f"{(r['contract_family'] or '')[:18]:18s} {(r['location'] or '')[:40]:40s} {r['status']}{acts}")
        print(f"      https://clancy.depotnet.co.uk/#/incidentmanager/imincident/{r['id']}")
        print(todo_line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("incident_id", nargs="?", type=int)
    ap.add_argument("--files", help="folder of everything downloaded for this incident")
    ap.add_argument("--actions", help="JSON from the Closed Actions tab scrape: a list of {id|description, closed, closed_by, location, documents:[names], timeline:[rows]}")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--actions-none", action="store_true",
                    help="this damage genuinely has no actions in Depotnet (records 'none')")
    ap.add_argument("--incident-missing", metavar="WHY",
                    help="the incident PDF was attempted and could not be retrieved")
    ap.add_argument("--actions-missing", metavar="WHY",
                    help="the action detail was attempted and could not be retrieved")
    ap.add_argument("--queue", action="store_true",
                    help="show what is still uncaptured (newest first) and where to resume")
    ap.add_argument("--limit", type=int, default=15, help="queue size (default 15 — about a week)")
    ap.add_argument("--fy", help="restrict the queue to one financial year, e.g. FY26/27")
    ap.add_argument("--oldest", action="store_true", help="queue oldest-first instead of newest")
    ap.add_argument("--with-actions", action="store_true",
                    help="queue only incidents that have corrective actions")
    a = ap.parse_args()
    if a.queue:
        queue(a.limit, a.fy, a.oldest, a.with_actions)
        return
    # --files is for the incident harvest. An ACTIONS-only run is legitimate and common: the
    # closure detail, an action's Location, its documents and its Timeline all live behind the
    # View modal and have nothing to do with the incident's own file folder. Requiring --files
    # here made that run impossible, which is why the 1 Aug 2026 backfill had to be hand-written.
    if not a.incident_id or not (a.files or a.actions or a.actions_none or a.actions_missing
                                 or a.incident_missing):
        sys.exit("give an incident id with --files (incident harvest) or --actions/--actions-none "
                 "(action side), or use --queue to see where to start")
    iid = a.incident_id

    got = rest(f"clancy_dn_incidents?id=eq.{iid}&select=id,location,utility_class,incident_date,capture_drive_folder")
    if not got:
        sys.exit(f"incident {iid} is not in clancy_dn_incidents — run clancy-dn-import.py first")
    inc = got[0]

    files = sorted(f for f in os.listdir(a.files) if not f.startswith(".")) if a.files else []
    pdfs = [f for f in files if f.lower().endswith(".pdf") and re.search(r"(incident[- ])", f, re.I)]
    photos = [f for f in files if is_photo(f)]
    docs = [f for f in files if f not in photos and f not in pdfs]
    print(f"incident {iid}: {len(pdfs)} pdf, {len(docs)} document(s), {len(photos)} photo(s)")
    print(f"  drive folder: {folder_name(inc)}")
    if a.dry_run:
        return

    # ---- Drive ----------------------------------------------------------
    root = incident_folder(inc)
    dsub = subfolder(root, "documents")
    psub = subfolder(root, "photos")
    idx = []
    for f in files:
        target = psub if f in photos else (root if f in pdfs else dsub)
        did = drive("upload", os.path.join(a.files, f), target, f)
        idx.append({"incident_id": iid, "kind": "photo" if f in photos else ("pdf" if f in pdfs else "document"),
                    "name": f, "drive_id": did,
                    "drive_folder": f"https://drive.google.com/drive/folders/{root}"})
        print(f"    up: {f}")
    if idx:
        # MATCH-THEN-WRITE, not a blind upsert (fixed 7 Aug 2026, damage 153523). This used to
        # POST with on_conflict=incident_id,kind,name,uploaded_on_depotnet — a key combination
        # that HAS NO UNIQUE INDEX on the table, so PostgREST answered 400 and the whole index
        # write died AFTER the files had already gone up to Drive. The result was the worst kind
        # of half-done: the evidence filed, and nothing in the database pointing at it.
        #
        # The rows may already exist (clancy-dn-ingest.py creates them from the Depotnet payload,
        # which is now the usual order), and those rows carry the identity columns this tool does
        # not have — storage_path and depotnet_file_id — so they must be UPDATED, never replaced.
        # Match on what this tool does know, (incident_id, kind, name), and insert only what is
        # genuinely new.
        existing = {}
        for r in (rest(f"clancy_dn_files?incident_id=eq.{iid}&select=id,kind,name") or []):
            existing[(r["kind"], r["name"])] = r["id"]
        fresh = []
        for row in idx:
            rid = existing.get((row["kind"], row["name"]))
            if rid:
                rest(f"clancy_dn_files?id=eq.{rid}", "PATCH",
                     {"drive_id": row["drive_id"], "drive_folder": row["drive_folder"]},
                     {"Prefer": "return=minimal"})
            else:
                fresh.append(row)
        if fresh:
            rest("clancy_dn_files", "POST", fresh, {"Prefer": "return=minimal"})
        print(f"    indexed: {len(idx) - len(fresh)} updated, {len(fresh)} new")

    # ---- PDF → answers + promoted fields --------------------------------
    for p in pdfs:
        r = subprocess.run(["python3", PDF_PARSER, os.path.join(a.files, p)],
                           capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
        print("   ", (r.stdout or r.stderr).strip().replace("\n", "\n    "))

    # ---- capture state, recorded explicitly so the register shows real gaps -------
    import datetime as _dt
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    state = {}
    if a.incident_missing:
        state["capture_incident"] = "missing"
        state["capture_note"] = a.incident_missing
    if a.actions_missing:
        state["capture_actions"] = "missing"
        state["capture_note"] = ((state.get("capture_note", "") + " · ") if state.get("capture_note") else "") + a.actions_missing
        state["actions_captured_at"] = now_iso
    elif a.actions_none:
        state["capture_actions"] = "none"
        state["actions_captured_at"] = now_iso
    elif a.actions:
        state["capture_actions"] = "captured"
        state["actions_captured_at"] = now_iso
    else:
        # no action flag given: if Depotnet holds no actions for this damage, that IS the answer
        if not rest(f"clancy_dn_actions?select=id&incident_id=eq.{iid}&limit=1"):
            state["capture_actions"] = "none"
            state["actions_captured_at"] = now_iso
    if state:
        rest(f"clancy_dn_incidents?id=eq.{iid}", "PATCH", state)
        print(f"    capture state: {state.get('capture_actions','-')} (actions)"
              + (f", {state['capture_incident']} (incident)" if state.get("capture_incident") else ""))

    # ---- action closures -------------------------------------------------
    if a.actions:
        import datetime as _dt2
        def _closed_iso(v):
            # Depotnet's Closed Actions tab renders dd/mm/yyyy HH:MM — not valid for a timestamptz PATCH
            try:
                return _dt2.datetime.strptime(v.strip(), "%d/%m/%Y %H:%M").isoformat()
            except ValueError:
                return v  # unrecognised shape: pass through, let PostgREST reject it loudly
        acts = json.load(open(a.actions))
        n = docs_n = 0
        for act in acts:
            patch = {}
            if act.get("closed"):
                patch["closed_at"] = _closed_iso(act["closed"])
            if act.get("closed_by"):
                patch["closed_by"] = act["closed_by"]
            if not patch:
                continue
            # An action's View modal also holds a Location and its OWN Photos / Videos /
            # Documents / Timeline. Nothing else in Depotnet exposes them — not the Action Report
            # export, not the incident PDF. On damage 133852 that was 10 documents and 57 timeline
            # entries we had never seen (found 1 Aug 2026).
            if act.get("location"):
                patch["location"] = act["location"]
            if act.get("timeline"):
                # `timeline` is jsonb: hand PostgREST the LIST, not json.dumps() of it, or it
                # stores a JSON *string* scalar and jsonb_array_length blows up on read.
                tl = act["timeline"]
                patch["timeline"] = tl if isinstance(tl, list) else [str(tl)]
            patch["detail_captured_at"] = now_iso

            # match on action id when present, else on the description text for this incident
            aid = None
            if act.get("id"):
                aid = int(act["id"])
                rest(f"clancy_dn_actions?id=eq.{aid}", "PATCH", patch)
                n += 1
            elif act.get("description"):
                key = urllib.request.quote(act["description"][:60])
                rest(f"clancy_dn_actions?incident_id=eq.{iid}&description=like.{key}*", "PATCH", patch)
                got = rest(f"clancy_dn_actions?select=id&incident_id=eq.{iid}"
                           f"&description=like.{key}*")
                aid = got[0]["id"] if got else None
                n += 1
            # the action's own documents, recorded against BOTH the damage and the action
            for name in dict.fromkeys(act.get("documents") or []):
                if not rest(f"clancy_dn_files?select=id&incident_id=eq.{iid}"
                            f"&name=eq.{urllib.request.quote(name)}"
                            + (f"&action_id=eq.{aid}" if aid else "")):
                    rest("clancy_dn_files", "POST",
                         [{"incident_id": iid, "action_id": aid, "kind": "document",
                           "name": name, "source": "depotnet-action-tab",
                           "captured_at": now_iso}])
                    docs_n += 1
        print(f"    action closures applied: {n} · action documents recorded: {docs_n}")

    rest(f"clancy_dn_incidents?id=eq.{iid}", "PATCH",
         {"capture_drive_folder": f"https://drive.google.com/drive/folders/{root}"})
    print(f"  done — https://drive.google.com/drive/folders/{root}")


if __name__ == "__main__":
    main()
