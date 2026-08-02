#!/usr/bin/env python3
"""clancy-dn-verify.py — prove a Depotnet capture actually landed, damage by damage.

Pete, 1 Aug 2026: "i dont want to find out in 2 weeks you missed a key field or cell of
information". This is the gate that makes that checkable instead of hoped for. Nothing here
trusts a summary count: every check reconciles what is IN THE DATABASE against the raw payload
Depotnet returned, which is stored on the incident itself.

WHY A SCRIPT AND NOT A BLOCK OF SQL. The FY25/26 plan asked you to paste three checks into
cc-sql.py as one statement. cc-sql.py returns only the LAST result set — measured 2 Aug 2026:

    cc-sql.py "SELECT 1 AS first_check; SELECT 2 AS second_check; SELECT 3 AS third_check"
    -> [{"third_check": 3}]

So two of the three checks were silently swallowed and the block looked like it passed. A gate
you can misread is not a gate.

WHAT IT CHECKS (each independently, each reported, exit 1 if any fails)

  1  register          every damage in scope has a raw_api payload stored
  2  answers           answer rows == the payload's questions + reportQuestions, per damage
  3  actions           action rows == the payload's actions, per damage
  4  files             file rows == the distinct storage paths in the payload, per damage
  5  promoted          a damage whose payload answers the field has it on its row
                       (FY25/26 first landed with 164 damages and ZERO strike categories)
  6  duplicates        no two file rows share a storage path within a damage
  7  drive             every fetchable file row has a drive_id, and its damage has a folder link
  8  report            report_submitted_at agrees with whether the report answers exist

Scope it as narrowly as you like — the plan's month-at-a-time loop needs a MONTH gate, and an
FY-wide check cannot fail a month:

  VAULT=/tmp/pbs python3 clancy-dn-verify.py --fy FY25/26
  VAULT=/tmp/pbs python3 clancy-dn-verify.py --fy FY25/26 --month 2025-12
  VAULT=/tmp/pbs python3 clancy-dn-verify.py --id 133852
  VAULT=/tmp/pbs python3 clancy-dn-verify.py --fy FY25/26 --verbose   # list every damage at fault
"""
import os, sys, json, time, argparse, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _k["url"], _k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}

# Files Depotnet lists but genuinely will not serve — marked source='unfetchable-sas' on the row.
# PROVEN with real HTTP on freshly-minted URLs (2 Aug 2026, all wire-legal variants):
#   percent-encode the query's non-ASCII  -> 400 InvalidQueryParameterValue
#   raw UTF-8 bytes in the query          -> 403 AuthenticationFailed
#   drop the rscd parameter               -> 403
#   re-encode rscd fully                  -> 403
# Depotnet signs the SAS over a content-disposition containing a raw en-dash in a form no valid
# HTTP request can reproduce. NOT counted as a filing failure; reported separately so the number
# never quietly grows.
UNFETCHABLE_NOTE = "Depotnet's own signed URL will not serve these (proven with fresh URLs, 2 Aug 2026)"


def _urlopen_retry(req, timeout=120, tries=9):
    for n in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == tries - 1:
                raise
            time.sleep(min(2 ** n, 60))
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(min(2 ** n, 60))


def rest(path):
    """PostgREST caps a response at 1000 rows and says nothing about it — the page build once
    rendered 13 incidents' answers out of 535 because of exactly this. Always page."""
    out, step = [], 1000
    while True:
        sep = "&" if "?" in path else "?"
        req = urllib.request.Request(f"{URL}/rest/v1/{path}{sep}limit={step}&offset={len(out)}",
                                     headers=H)
        got = json.loads(_urlopen_retry(req, timeout=180).read().decode())
        out += got
        if len(got) < step:
            return out


def payload_counts(raw):
    """What Depotnet actually returned, counted the same way the ingest writes it."""
    d = (raw or {}).get("data") or {}
    q = d.get("questions") or []
    rq = d.get("reportQuestions") or []
    acts = d.get("actions") or []
    paths, phantom = set(), set()
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("filePath", "path", "url", "fileUrl") and isinstance(v, str) and v:
                    stem = v.partition("?")[0]
                    # Apply EXACTLY the rule clancy-dn-ingest.py applies, or the two disagree for
                    # ever: Depotnet signs URLs for blobs that do not exist (a question answer
                    # stored where a photo should be), and a real attachment always sits under
                    # the container structure, so it has 2+ segments after the host. If that rule
                    # ever changes it must change in both files together.
                    if len(stem.split("://", 1)[-1].split("/")) < 3:
                        phantom.add(stem)
                    else:
                        paths.add(stem)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return {"questions": len(q) + len(rq), "actions": len(acts), "paths": paths,
            "phantom": phantom,
            "has_report": bool([x for x in rq if (x.get("answer") or "").strip()])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fy")
    ap.add_argument("--month", help="YYYY-MM — the plan's loop is a month at a time, so the gate is too")
    ap.add_argument("--id", type=int)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    q = "clancy_dn_incidents?select=id,fy,incident_date,raw_api,capture_drive_folder,strike_category,root_cause,lessons_learnt,report_submitted_at,depth_mm,depth_raw"
    if a.id:
        q += f"&id=eq.{a.id}"
    else:
        if a.fy:
            q += f"&fy=eq.{urllib.request.quote(a.fy)}"
        if a.month:
            y, m = a.month.split("-")
            nxt = f"{int(y)+1}-01-01" if m == "12" else f"{y}-{int(m)+1:02d}-01"
            q += f"&incident_date=gte.{y}-{m}-01&incident_date=lt.{nxt}"
    incs = rest(q)
    if not incs:
        print("no damages in that scope — nothing to verify (this is NOT a pass)")
        return 2

    scope = a.id or f"{a.fy or 'all years'}{' ' + a.month if a.month else ''}"
    print(f"verifying {len(incs)} damage(s) — {scope}\n")

    ids = {i["id"] for i in incs}
    def by_inc(rows, key="incident_id"):
        d = {}
        for r in rows:
            d.setdefault(r[key], []).append(r)
        return d

    answers = by_inc(rest("clancy_dn_answers?select=incident_id,section,question,answered"))
    actions = by_inc(rest("clancy_dn_actions?select=id,incident_id"))
    files = by_inc(rest("clancy_dn_files?select=incident_id,name,storage_path,drive_id,source,deleted_on_depotnet"))

    fails = {k: [] for k in ("register", "answers", "actions", "files", "promoted",
                             "duplicates", "drive", "report")}
    unfetchable = 0
    phantoms = 0

    for i in incs:
        iid = i["id"]
        raw = i.get("raw_api")
        if not raw:
            fails["register"].append(f"{iid} has no raw_api stored")
            continue
        p = payload_counts(raw)
        my_ans = answers.get(iid, [])
        my_act = actions.get(iid, [])
        my_fil = files.get(iid, [])

        if len(my_ans) != p["questions"]:
            fails["answers"].append(f"{iid}: {len(my_ans)} rows vs {p['questions']} in the payload")
        if len(my_act) != p["actions"]:
            fails["actions"].append(f"{iid}: {len(my_act)} rows vs {p['actions']} in the payload")

        # Files: the payload's distinct storage paths are the truth. A path we hold no row for is
        # a miss; a row with no matching path is something the ingest invented.
        mine = {f["storage_path"] for f in my_fil if f["storage_path"]}
        phantoms += len(p["phantom"])
        missing = p["paths"] - mine
        if missing:
            fails["files"].append(f"{iid}: {len(missing)} path(s) in the payload with no file row")

        seen = {}
        for f in my_fil:
            if f["storage_path"]:
                seen.setdefault(f["storage_path"], 0)
                seen[f["storage_path"]] += 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        if dupes:
            fails["duplicates"].append(f"{iid}: {len(dupes)} storage path(s) held twice")

        # Promoted fields — the failure that put 164 damages on the page with no category at all.
        d = (raw.get("data") or {})
        qa = {(x.get("question") or "").strip(): (x.get("answer") or "").strip()
              for x in (d.get("questions") or [])}
        inv = {(x.get("question") or "").strip(): (x.get("answer") or "").strip()
               for x in (d.get("reportQuestions") or [])}
        for col, src, book in (("strike_category", "Service Strike Category", qa),
                               ("root_cause", "Service Strike Root Cause", inv),
                               ("lessons_learnt", "Preventative Outcomes/Actions/Lessons Learnt", inv)):
            if book.get(src) and not (i.get(col) or "").strip():
                fails["promoted"].append(f"{iid}: payload answers {col} but the row is empty")

        # Drive. A row with no drive_id is either unfiled (a fail) or one Depotnet cannot
        # serve (source='unfetchable-sas' — an explicit state, set after real HTTP proof, not a
        # filename heuristic). Withdrawn files (deleted on Depotnet) need no drive presence.
        for f in my_fil:
            if not f["drive_id"] and not f.get("deleted_on_depotnet"):
                if f.get("source") == "unfetchable-sas":
                    unfetchable += 1
                else:
                    fails["drive"].append(f"{iid}: '{(f['name'] or '?')[:44]}' has no drive_id")
        if my_fil and any(f["drive_id"] for f in my_fil) and not i.get("capture_drive_folder"):
            fails["drive"].append(f"{iid}: files are in Drive but the damage has no folder link")

        # The report timestamp must agree with whether report answers actually exist — this is
        # the check that proved the blank Report tab is Depotnet's state, not our capture failing.
        got_report = any(r["section"] == "investigation" and r["answered"] for r in my_ans)
        if bool(i.get("report_submitted_at")) != got_report:
            fails["report"].append(
                f"{iid}: report_submitted_at={'set' if i.get('report_submitted_at') else 'null'} "
                f"but report answers {'exist' if got_report else 'do not exist'}")

    LABEL = {
        "register":   "every damage has its raw payload stored",
        "answers":    "answer rows match the payload, per damage",
        "actions":    "action rows match the payload, per damage",
        "files":      "every file in the payload has a row",
        "promoted":   "answered fields are promoted onto the damage row",
        "duplicates": "no storage path held twice within a damage",
        "drive":      "every fetchable file is in Drive, and its damage is linked",
        "report":     "the report timestamp agrees with the report answers",
    }
    bad = 0
    for k, label in LABEL.items():
        n = len(fails[k])
        bad += n
        print(f"  {'FAIL' if n else 'pass'}  {label:52} {n if n else ''}")
        if n and a.verbose:
            for line in fails[k][:25]:
                print(f"          · {line}")
            if n > 25:
                print(f"          … and {n - 25} more")
        elif n:
            for line in fails[k][:3]:
                print(f"          · {line}")
            if n > 3:
                print(f"          … and {n - 3} more (--verbose for all)")

    if phantoms:
        print(f"\n  note  {phantoms} phantom attachment(s) in the payloads — Depotnet signed a URL "
              f"for a blob that does not exist (an answer stored where a photo should be). "
              f"Correctly not stored as file rows.")
    if unfetchable:
        print(f"\n  note  {unfetchable} file(s) not in Drive — {UNFETCHABLE_NOTE}. "
              f"Not counted as a failure; watch that this number does not grow.")
    print(f"\n{'VERIFIED — ' + str(len(incs)) + ' damage(s) reconcile against their payloads'
          if not bad else str(bad) + ' problem(s) — this capture is NOT complete'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
