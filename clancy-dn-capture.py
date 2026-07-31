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


def drive(*args):
    r = subprocess.run(["python3", DRIVE, *args], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    m = re.search(r"ID: ([\w-]+)", r.stdout)
    return m.group(1) if m else None


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
    tot = rest("clancy_dn_incidents?select=id&limit=10000")
    cap = rest("clancy_dn_incidents?select=id&pdf_captured_at=not.is.null&limit=10000")
    have_actions = {a["incident_id"] for a in
                    rest("clancy_dn_actions?select=incident_id&limit=10000")}
    print(f"captured {len(cap)} of {len(tot)} incidents"
          + (f" · newest captured: {done[0]['id']} ({(done[0]['incident_date'] or '')[:10]})" if done else " · none captured yet"))
    if not todo:
        print("nothing left to capture" + (f" in {fy}" if fy else ""))
        return
    print(f"\nnext {len(todo)} to capture ({'oldest' if oldest else 'newest'} first"
          + (f", {fy}" if fy else "") + "):")
    for r in todo:
        if with_actions_only and r["id"] not in have_actions:
            continue
        acts = " ·  HAS ACTIONS (do the Closed Actions scrape too)" if r["id"] in have_actions else ""
        print(f"  {r['id']}  {(r['incident_date'] or '')[:10]}  {r['fy']}  "
              f"{(r['contract_family'] or '')[:18]:18s} {(r['location'] or '')[:40]:40s} {r['status']}{acts}")
        print(f"      https://clancy.depotnet.co.uk/#/incidentmanager/imincident/{r['id']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("incident_id", nargs="?", type=int)
    ap.add_argument("--files", help="folder of everything downloaded for this incident")
    ap.add_argument("--actions", help="JSON from the Closed Actions tab scrape")
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
    if not a.incident_id or not a.files:
        sys.exit("give an incident id and --files, or use --queue to see where to start")
    iid = a.incident_id

    got = rest(f"clancy_dn_incidents?id=eq.{iid}&select=id,location,utility_class,incident_date,capture_drive_folder")
    if not got:
        sys.exit(f"incident {iid} is not in clancy_dn_incidents — run clancy-dn-import.py first")
    inc = got[0]

    files = sorted(f for f in os.listdir(a.files) if not f.startswith("."))
    pdfs = [f for f in files if f.lower().endswith(".pdf") and re.search(r"(incident[- ])", f, re.I)]
    photos = [f for f in files if is_photo(f)]
    docs = [f for f in files if f not in photos and f not in pdfs]
    print(f"incident {iid}: {len(pdfs)} pdf, {len(docs)} document(s), {len(photos)} photo(s)")
    print(f"  drive folder: {folder_name(inc)}")
    if a.dry_run:
        return

    # ---- Drive ----------------------------------------------------------
    root = drive("create-folder", folder_name(inc), DAMAGES_FOLDER)
    dsub = drive("create-folder", "documents", root)
    psub = drive("create-folder", "photos", root)
    idx = []
    for f in files:
        target = psub if f in photos else (root if f in pdfs else dsub)
        did = drive("upload", os.path.join(a.files, f), target, f)
        idx.append({"incident_id": iid, "kind": "photo" if f in photos else ("pdf" if f in pdfs else "document"),
                    "name": f, "drive_id": did,
                    "drive_folder": f"https://drive.google.com/drive/folders/{root}"})
        print(f"    up: {f}")
    if idx:
        rest("clancy_dn_files?on_conflict=incident_id,kind,name,uploaded_on_depotnet", "POST", idx,
             {"Prefer": "resolution=merge-duplicates"})

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
        n = 0
        for act in acts:
            patch = {}
            if act.get("closed"):
                patch["closed_at"] = _closed_iso(act["closed"])
            if act.get("closed_by"):
                patch["closed_by"] = act["closed_by"]
            if not patch:
                continue
            # match on action id when present, else on the description text for this incident
            if act.get("id"):
                rest(f"clancy_dn_actions?id=eq.{int(act['id'])}", "PATCH", patch)
                n += 1
            elif act.get("description"):
                key = urllib.request.quote(act["description"][:60])
                rest(f"clancy_dn_actions?incident_id=eq.{iid}&description=like.{key}*", "PATCH", patch)
                n += 1
        print(f"    action closures applied: {n}")

    rest(f"clancy_dn_incidents?id=eq.{iid}", "PATCH",
         {"capture_drive_folder": f"https://drive.google.com/drive/folders/{root}"})
    print(f"  done — https://drive.google.com/drive/folders/{root}")


if __name__ == "__main__":
    main()
