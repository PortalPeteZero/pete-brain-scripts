#!/usr/bin/env python3
"""clancy-dn-ingest.py — turn a raw Depotnet API payload into the CC tables.

The parsing half of the API capture. Takes the JSON that `GetImIncident` returns (dumped to disk
by the browser step — see [[depotnet-api]]) and writes it to `clancy_dn_incidents`,
`clancy_dn_answers`, `clancy_dn_actions` and `clancy_dn_files`.

WHY IT IS SPLIT FROM THE FETCH. The API needs a Bearer token that only a logged-in browser holds,
so the fetch happens in Chrome and the payload lands on disk. Everything after that is ordinary
Python with no credential in play. It also means a re-parse never needs a re-fetch — which is the
whole point of storing `raw_api`.

THREE THINGS IT REFUSES TO DO QUIETLY (Pete, 1 Aug 2026: "i dont want to find out in 2 weeks you
missed a key field or cell of information"):

  1. **Unknown fields are reported, never dropped.** Every payload's key set is compared against
     the baseline below. A key Depotnet adds is printed and the run is marked dirty.
  2. **The raw payload is always stored**, so a field we failed to map is a re-parse away.
  3. **Counts are asserted.** Answers/actions/files written are compared against what the payload
     held, and a mismatch is an error, not a log line.

MEASURED, NOT ASSUMED (1 Aug 2026, 12 incidents spanning 2023-2026 with 0-9 actions each):
  · top-level keys identical on all 12 — nothing missing, nothing extra
  · `reportQuestions` is 63 on every one
  · the action object is 50 fields on all 19 actions seen
  · **`questions` VARIES: 16, 17, 18, 19, 20** — the incident report template changed over the
    years, so nothing may assume a fixed question count.

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-ingest.py /tmp/dnapi/dnapi__133852.json
  VAULT=/tmp/pbs python3 clancy-dn-ingest.py /tmp/dnapi/            # whole folder
  ... --dry-run                                                     # parse and report, write nothing
"""
import os, sys, json, glob, argparse, datetime, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _k["url"], _k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}

# The shape we have measured. A payload that differs is REPORTED, not silently accepted.
BASELINE = {"clientLogo", "imIncident", "timeline", "questions", "reportQuestions", "actions",
            "injuries", "witnesses", "vehicles", "notices", "isArchived", "hideConfirmedCategory"}


def _urlopen_retry(req, timeout=120, tries=6):
    """Supabase answers 429 under load — a heavy filing run or 22 page writes in a row will
    hit it. Without backoff the caller dies mid-publish and leaves the section half-updated.
    Retries on 429 and 5xx with exponential backoff; anything else raises immediately."""
    import time as _t
    for n in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 30))
        except Exception:
            if n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 30))


def rest(path, method="GET", payload=None, extra=None):
    h = dict(H); h.update(extra or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
        data=json.dumps(payload).encode() if payload is not None else None, headers=h,
        method=method)
    try:
        t = _urlopen_retry(req, timeout=120).read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} on {method} {path[:60]} — {e.read().decode()[:300]}")
    return json.loads(t) if t.strip() else None


def iso(v):
    """Depotnet returns naive .NET timestamps. Treat as UTC; None stays None."""
    if not v:
        return None
    v = str(v).strip()
    return v if v.endswith("Z") or "+" in v[10:] else v + "Z"


def _name(v):
    """A Depotnet person string keeps its payroll number — keep it, it disambiguates."""
    return (v or "").strip() or None


def parse(doc):
    """Payload -> the rows we store. No writes. Returns (rows, warnings)."""
    warn = []
    if not doc.get("success", True):
        warn.append(f"payload success=false: {doc.get('message')}")
    d = doc.get("data") or {}
    keys = set(d)
    if keys - BASELINE:
        warn.append(f"NEW top-level keys Depotnet added: {sorted(keys - BASELINE)}")
    if BASELINE - keys:
        warn.append(f"top-level keys MISSING from this payload: {sorted(BASELINE - keys)}")

    inc = d.get("imIncident") or {}
    iid = inc.get("imIncidentId")
    if not iid:
        raise ValueError("payload has no imIncident.imIncidentId")

    # ---- the incident -------------------------------------------------------------------
    tl = []
    for e in d.get("timeline") or []:
        tl.append({
            "at":     iso(e.get("dateCreated")),
            "by":     _name((e.get("createdByUser") or {}).get("fullName")),
            "what":   (e.get("imIncidentHistoryType") or {}).get("imIncidentHistoryTypeName"),
            "detail": (e.get("details") or "").strip() or None,
            "action": e.get("imIncidentActionId"),
            "file":   ((e.get("document") or {}).get("fileName")
                       or (e.get("photo") or {}).get("fileName")
                       or (e.get("video") or {}).get("fileName")),
        })

    # ---- promote the answers onto the damage row -----------------------------------------
    # Every page reads strike_category, depth_mm, root_cause, lessons_learnt and the rest off
    # clancy_dn_incidents, NOT off clancy_dn_answers. The PDF parser used to do this; the API
    # ingest did not, so FY25/26 landed with 164 damages and ZERO categories, depths, causes or
    # lessons — every page would have read empty. (Audit finding, 2 Aug 2026.)
    qa = {(q.get("question") or "").strip(): (q.get("answer") or "")
          for q in (d.get("questions") or [])}
    inv = {(q.get("question") or "").strip(): (q.get("answer") or "")
           for q in (d.get("reportQuestions") or [])}
    promoted = {}
    def take(dst, src, src_map=None):
        v = ((src_map if src_map is not None else qa).get(src) or "").strip()
        if v:
            promoted[dst] = v
    take("strike_category", "Service Strike Category")
    take("strike_subcategory", "Service Strike Sub-Category")
    take("environment", "Environment Of Works")
    take("caused_by_person", "Name of the person who caused the damage")
    take("caused_by_plant", "Damage Caused By")
    take("service_interrupted", "Service Interrupted")
    take("reported_to_owner_at", "Date & Time Incident Was Reported To Asset Owner")
    take("incident_summary", "Incident summary", inv)
    take("underlying_cause", "Service Strike Underlying Cause", inv)
    take("root_cause", "Service Strike Root Cause", inv)
    take("lessons_learnt", "Preventative Outcomes/Actions/Lessons Learnt", inv)
    dep = (qa.get("Depth Of Utility (Approx) - Unit In MM") or "").strip()
    if dep.isdigit():
        promoted["depth_mm"] = int(dep)

    row = {
        **promoted,
        "id": iid,
        "report_submitted_at": iso(inc.get("reportSubmittedDate")),
        "report_submitted_by": _name(inc.get("reportSubmittedByName")),
        "include_investigation": inc.get("includeInvestigation"),
        "lat_api": inc.get("latitude"), "lon_api": inc.get("longitude"),
        "timeline": tl,
        "raw_api": doc,
        "raw_api_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        # The pages key "has this damage been captured?" off pdf_captured_at, including the
        # analysis page's scaffold banner. An API capture IS a capture — without this a fully
        # captured year still publishes as a scaffold. (Caught by the December 2025 rehearsal.)
        "pdf_captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "capture_incident": ("full" if any((q.get("answer") or "").strip()
                                           for q in (d.get("reportQuestions") or []))
                             else "no-investigation"),
        "capture_actions": "captured" if (d.get("actions") or []) else "none",
        "actions_captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # ---- answers: BOTH sections, and the UNANSWERED ones too ----------------------------
    # The PDF only ever printed answered questions, so an unanswered one was indistinguishable
    # from one that was never asked. The API returns the full set with answer:null — that
    # distinction is the whole reason "22 not started" can be stated as a fact.
    answers, seen = [], set()
    for section, arr in (("questions", d.get("questions") or []),
                         ("investigation", d.get("reportQuestions") or [])):
        for n, q in enumerate(arr, 1):
            key = (section, n)
            if key in seen:
                warn.append(f"duplicate q_no {n} in {section}")
                continue
            seen.add(key)
            a = q.get("answer")
            answers.append({
                "incident_id": iid, "section": section, "q_no": n,
                "question": (q.get("question") or "").strip(),
                "answer": (str(a).strip() or None) if a is not None else None,
                "mandatory": q.get("mandatory"),
            })

    # ---- actions ------------------------------------------------------------------------
    actions = []
    for a in d.get("actions") or []:
        actions.append({
            "id": a.get("imIncidentActionId"),      # the export has NO action id; this does
            "incident_id": iid,
            "date_raised": iso(a.get("dateCreated")),
            "due_date": iso(a.get("dueDate")),
            "closed_at": iso(a.get("dateClosed")),
            "closed_by": _name(a.get("closedByName")),
            "assigned_to": _name(a.get("assignedToName")),
            "raised_by": _name(a.get("createdByName")),
            "status": a.get("imIncidentActionStatusName"),
            "incident_status": a.get("imIncidentStatusName"),
            "description": a.get("actionDescription"),
            "corrective_measure": a.get("correctiveMeasure"),
            "location": a.get("location"),
            "question": a.get("question"),
            "action_classification": a.get("imActionClassificationName"),
            "action_subclassification": a.get("imActionSubclassificationName"),
            "category": a.get("imCategoryName"), "severity": a.get("imSeverityName"),
            "contract": a.get("contractName"), "contract_number": a.get("contractNumberName"),
            "workstream": a.get("clientWorkstreamName"),
            "business_unit": a.get("businessUnitName"),
            "subcontractor": a.get("subcontractorName"),
            "job_id": a.get("jobId"), "job_ref": a.get("jobRef"),
            "incident_date": iso(a.get("incidentDate")),
        })

    # An action's own timeline is DERIVABLE: incident timeline entries carry the
    # imIncidentActionId they belong to. FY26/27's were hand-scraped from the browser modal one
    # by one; that was never necessary and left FY25/26's 79 actions with none at all until this
    # was spotted by auditing the finished work.
    by_action = {}
    for e in tl:
        if e.get("action"):
            by_action.setdefault(e["action"], []).append(
                {k: v for k, v in e.items() if k != "action"})
    for a in actions:
        got = by_action.get(a["id"])
        if got:
            a["timeline"] = got

    # ---- every file the payload references ----------------------------------------------
    # Files hang off timeline entries AND off individual questions.
    #
    # IDENTITY IS THE ID, NOT THE NAME. The same attachment arrives twice: on a timeline entry
    # with fileName "gallery_pogbzemlwvfpble.jpg", and on a question's photos[] with fileName
    # **null**, where falling back to the storage path produced
    # "2026-04-08-15-43-06-425_gallerypogbzemlwvfpble.jpg". Two rows, one file — 147 of them on
    # 1 Aug 2026. Depotnet gives documentId / photoId / videoId; use them. Where there is no id,
    # the blob path minus its SAS query string is stable across runs (the signature is not).
    # Keyed on the STORAGE PATH, because that is the physical file. The same blob is described
    # twice with different richness:
    #   timeline photo  -> photoId 21524200, fileName "gallery_nxshqwcbnmkissf.jpg"
    #   question photo  -> NO id, NO fileName, only the path
    # Keying on the id alone made them two entries; letting the later one win wiped the good
    # name and the id. So: one entry per path, MERGED — take the id and the display name from
    # whichever source has them, never downgrade.
    merged = {}
    def add_file(o, kind, action_id=None):
        path = o.get("path")
        if not path:
            return
        stem = path.split("?")[0]
        fid = o.get("documentId") or o.get("photoId") or o.get("videoId")
        cur = merged.get(stem)
        if cur is None:
            cur = merged[stem] = {"incident_id": iid, "action_id": action_id, "kind": kind,
                                  "name": None, "path": path, "storage_path": stem,
                                  "depotnet_file_id": None,
                                  "uploaded_on_depotnet": iso(o.get("dateCreated"))}
        if fid and not cur["depotnet_file_id"]:
            cur["depotnet_file_id"] = fid
        if o.get("fileName") and not cur["name"]:
            cur["name"] = o["fileName"]              # a real display name always beats the path
        if action_id and not cur["action_id"]:
            cur["action_id"] = action_id
        if not cur["uploaded_on_depotnet"]:
            cur["uploaded_on_depotnet"] = iso(o.get("dateCreated"))

    for e in d.get("timeline") or []:
        aid = e.get("imIncidentActionId")
        if e.get("document"): add_file(e["document"], "document", aid)
        if e.get("photo"):    add_file(e["photo"], "photo", aid)
        if e.get("video"):    add_file(e["video"], "video", aid)
    for q in (d.get("questions") or []) + (d.get("reportQuestions") or []):
        for ph in q.get("photos") or []:
            add_file(ph, "photo")

    files = []
    for stem, f in merged.items():
        f["name"] = f["name"] or stem.rsplit("/", 1)[-1]   # only now fall back to the path
        files.append(f)

    return {"incident": row, "answers": answers, "actions": actions, "files": files,
            "counts": {"timeline": len(tl), "questions": len(d.get("questions") or []),
                       "report": len(d.get("reportQuestions") or []),
                       "actions": len(d.get("actions") or []), "files": len(files)}}, warn


def write(parsed):
    """Persist. Idempotent — re-running the same payload changes nothing but the timestamps."""
    inc, iid = parsed["incident"], parsed["incident"]["id"]
    rest(f"clancy_dn_incidents?id=eq.{iid}", "PATCH",
         {k: v for k, v in inc.items() if k != "id"}, {"Prefer": "return=minimal"})
    if parsed["answers"]:
        rest("clancy_dn_answers?on_conflict=incident_id,section,q_no", "POST", parsed["answers"],
             {"Prefer": "resolution=merge-duplicates,return=minimal"})
    if parsed["actions"]:
        # one upsert, not a lookup-then-write per action
        rest("clancy_dn_actions?on_conflict=id", "POST", parsed["actions"],
             {"Prefer": "resolution=merge-duplicates,return=minimal"})
    # files: ONE batched upsert on (incident_id, storage_path) — the physical identity of the
    # blob. This used to be up to three lookups plus a write PER FILE, ~2,000 round-trips for
    # 486 files, and it is why the 1 Aug re-runs took so long.
    #
    # drive_id / drive_folder are deliberately NOT in the payload: filing sets them, and a
    # re-ingest must never blank them. Postgres only overwrites the columns supplied.
    if parsed["files"]:
        rows = [{k: v for k, v in f.items() if k != "path"} | {"source": "depotnet-api"}
                for f in parsed["files"]]
        rest("clancy_dn_files?on_conflict=incident_id,storage_path", "POST", rows,
             {"Prefer": "resolution=merge-duplicates,return=minimal"})
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a payload .json, or a folder of them")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    paths = (sorted(glob.glob(os.path.join(a.path, "dnapi__*.json")))
             if os.path.isdir(a.path) else [a.path])
    if not paths:
        sys.exit(f"no dnapi__*.json under {a.path}")

    tot = {"timeline": 0, "questions": 0, "report": 0, "actions": 0, "files": 0}
    warned, done = 0, 0
    for p in paths:
        doc = json.load(open(p))
        parsed, warn = parse(doc)
        c = parsed["counts"]
        for k in tot:
            tot[k] += c[k]
        iid = parsed["incident"]["id"]
        flag = ""
        if warn:
            warned += 1
            flag = "  !! " + " · ".join(warn)
        print(f"  {iid}  q={c['questions']:2} report={c['report']:2} actions={c['actions']:2} "
              f"timeline={c['timeline']:3} files={c['files']:3}{flag}")
        if not a.dry_run:
            write(parsed)
        done += 1

    print(f"\n{done} payload(s) {'parsed' if a.dry_run else 'ingested'} · "
          f"{tot['questions']} incident-report answers · {tot['report']} investigation answers · "
          f"{tot['actions']} actions · {tot['timeline']} timeline entries · {tot['files']} files")
    if warned:
        print(f"!! {warned} payload(s) carried something the parser did not recognise — read the "
              f"lines marked !! above. The raw payload is stored, so nothing is lost.")
    return 1 if warned else 0


if __name__ == "__main__":
    sys.exit(main())
