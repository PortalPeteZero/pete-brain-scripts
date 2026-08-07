#!/usr/bin/env python3
"""clancy-dd-source-links.py — every Sygma finding carries a link to the document it came from.

Pete, 5 Aug 2026: "whenever you make a statement or a comment or you quote somebody, wherever that
came from, there is a natural link in there to the drive document so I can check it myself. This
needs to happen to the ones that we've done so far, and it needs to be part of the process going
forward."

A finding Pete cannot check is worth less than no finding. This resolves each finding's sources
from the text itself and writes them to `clancy_dn_incidents.sygma_finding_sources` — a JSON array
running parallel to `sygma_findings`, one entry per finding, each holding the files it cites with
their Drive links. The per-damage page renders them under the finding.

WHY RESOLVED, NOT HAND-KEPT
  Hand-maintained citations rot the moment a finding is edited, and there were 88 of them across 15
  damages when this was written. The resolver reads the finding's own words, so a finding that
  quotes "the Fast Facts notice" links the Fast Facts PDF whether it was written today or a month
  ago, and an edited finding re-resolves on the next run.

HOW A SOURCE IS MATCHED, strongest first
  1 QUOTED TEXT   the finding quotes a run of words that appears in a document's extracted text or
                  in a transcribed scan. This is the strongest link there is: the words are
                  literally in that file. Beats every heuristic below.
  2 FILE NAME     the finding names the file, or enough of it to be unambiguous.
  3 ALIAS         the finding uses the plain-English name for a document ("the permit to dig",
                  "the pre-start form", "the electricity plans"). ALIASES holds the mapping.
  4 DEPOTNET      the finding cites a Depotnet field or answer rather than an attachment. That is
                  not a Drive file, so it links the damage's own record instead — still checkable.

A finding that resolves to NOTHING is reported and fails clancy-dd-workflow.py step 10. That is
deliberate: an unsourced assertion in the Sygma layer is exactly what this exists to stop.

  VAULT=/tmp/pbs python3 clancy-dd-source-links.py            # resolve every damage, report
  VAULT=/tmp/pbs python3 clancy-dd-source-links.py --apply    # ...and write them
  VAULT=/tmp/pbs python3 clancy-dd-source-links.py --id 153523 --apply
"""
import argparse, json, os, re, sys, urllib.request, urllib.error
from collections import defaultdict

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_K = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _K["url"], _K["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}

# Plain-English names people actually use for these documents, mapped to a pattern that matches the
# filename. Add to this rather than rewording a finding to suit the resolver.
ALIASES = [
    (r"fast facts|incident alert",                    r"fast facts"),
    (r"pre-?start|pre-?dig|service avoidance",        r"pre dig|service avoidance"),
    (r"permit to dig|\bptd\b|permit \d+|permit no",    r"\bptd\b|permit to dig"),
    (r"witness statement",                            r"witness statement"),
    (r"method statement",                             r"method statement"),
    (r"risk assessment|\brams\b",                     r"risk assessment|\bra signed\b|method statement"),
    (r"puwer",                                        r"puwer"),
    (r"daily (safety )?brief",                        r"daily safety brief"),
    (r"plant (daily )?check|excavator daily",         r"daily checks"),
    (r"induction",                                    r"induction"),
    (r"northern powergrid|utility plan|electricity plan|the plans|the drawing",
                                                      r"^elec|drawing"),
    # "panel pack" is what everyone calls it in conversation and in findings; the FILE is named
    # "Panel Review Slides…". Added 7 Aug 2026 — the phrase side was missing it, so findings citing
    # the pack resolved to nothing while the document sat right there on the damage.
    (r"panel review|panel pack|panel slides",         r"panel review"),
    (r"cat and genny data|genny and cat data|locator data|cat (data )?(download|report)|"
     r"cat\d?[_ ]?replay",
                                                      r"cat and genny|genny and cat|cat download|"
                                                      r"cat.?replay|^cat "),
    (r"photograph|photo\b|site photo|the images",     r"\.jpe?g$|\.png$"),
]
# A finding whose fact IS a Depotnet field links the damage's own record rather than an
# attachment. Extended 7 Aug 2026 — each addition below names a field the investigation actually
# holds, verified against clancy_dn_answers, so the claim "cites Depotnet's own fields" is true
# rather than a guess:
#   night working / weather  -> Incident Date & Time + "Confirm the weather conditions"
#   D&A                      -> "Post Incident Drugs And Alcohol Tests" + its result question
#   caused by <plant>        -> "Damage Caused By"
#   depth in mm              -> "Depth Of Utility (Approx) - Unit In MM"
#   job ref                  -> the incident's job_ref
#   subcontractor / direct   -> "Was The Damage Caused By A Subcontractor"
#   Genny/CAT training dates -> "Date(s) of Genny and CAT training"
#
# A fuzzy word-overlap test against the record text was BUILT AND REJECTED the same day: measured
# across all 66 unsourced findings, no threshold separated record-derived facts from Sygma
# judgements ("1 property lost supply" scored 0.33, "Cable not fully protected once exposed" 0.20),
# so any setting would have put a wrong citation under a finding Clancy read. Precise field names
# only — if a fact is not in a Depotnet field, it stays unsourced and the gate keeps saying so.
DEPOTNET_HINTS = re.compile(
    r"depotnet|investigation (report|answers?)|the record|sub-?category|question \d+|"
    r"lessons_learnt|root cause field|service interrupted|nearest light column|"
    r"night working|\bd&a\b|drugs? and alcohol|"
    r"caused by (a |an |the )?(pecker|breaker|excavator|digger|saw|grafter|shovel|spade|plant|"
    r"mini digger|insulated)|"
    r"\bat ~?\d{2,4}\s?mm\b|\(\s?~?\d{2,4}\s?mm\s?\)|\b\d{2,4}mm\b|"
    r"\bjob ref\b|"
    r"\bsubcontractor\b|\bdirect team\b|"
    r"in-?date on cat|cat (&|and) genny training|genny and cat training", re.I)


def get(path):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(URL + "/rest/v1/" + path, headers=H), timeout=240).read().decode())


def norm(s):
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def quoted_runs(text):
    """Runs of words the finding presents as a quotation. Straight and curly quotes."""
    out = []
    for m in re.finditer(r'["“”]([^"“”]{12,400})["“”]', text or ""):
        out.append(m.group(1))
    return out


def resolve(finding, files, texts):
    """Return the files this finding cites, strongest evidence first."""
    hits, why = {}, {}
    low = (finding or "").lower()

    # 1 QUOTED TEXT — the words are literally in that document
    for q in quoted_runs(finding):
        nq = norm(q)
        if len(nq) < 12:
            continue
        for fid, body in texts.items():
            if nq[:120] and nq[:120] in body:
                hits[fid] = 3
                why[fid] = "quotes this document"

    # 2 FILE NAME named in the finding
    for f in files:
        stem = re.sub(r"\.[a-z0-9]{2,5}$", "", f["name"]).lower()
        stem = re.sub(r"^\d{4}-\d\d-\d\d-[\d-]+_", "", stem)
        if len(stem) >= 6 and stem in low and hits.get(f["id"], 0) < 2:
            hits[f["id"]] = 2
            why[f["id"]] = "names this document"

    # 3 ALIAS — the plain-English name
    for phrase, filepat in ALIASES:
        if re.search(phrase, low):
            for f in files:
                if re.search(filepat, f["name"].lower()) and hits.get(f["id"], 0) < 1:
                    hits[f["id"]] = 1
                    why[f["id"]] = "the finding refers to this by name"

    by = {f["id"]: f for f in files}
    out, weak = [], 0
    for fid, rank in sorted(hits.items(), key=lambda kv: -kv[1]):
        f = by.get(fid)
        if not f:
            continue
        # Alias matches are a guess from plain-English wording, so a finding that says
        # "the photographs" would otherwise link every photo on the damage and bury the one
        # document it actually quotes. Quoted-text and named-file matches are never capped.
        if rank == 1:
            weak += 1
            if weak > 2:
                continue
        out.append({"file_id": fid, "name": f["name"], "why": why[fid], "rank": rank,
                    "url": (f"https://drive.google.com/file/d/{f['drive_id']}/view"
                            if f.get("drive_id") else None)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    q = ("clancy_dn_incidents?select=id,location,sygma_findings&sygma_findings=not.is.null"
         "&order=id.asc&limit=200")
    if a.id:
        q = f"clancy_dn_incidents?select=id,location,sygma_findings&id=eq.{a.id}"
    inc = get(q)
    if not inc:
        sys.exit("no damages with Sygma findings")

    total = linked = unsourced = 0
    for r in inc:
        iid = r["id"]
        files = get(f"clancy_dn_files?incident_id=eq.{iid}&select=id,name,drive_id"
                    f"&order=id.asc&limit=400")
        texts = {}
        for e in get(f"clancy_dn_doc_extracts?incident_id=eq.{iid}"
                     f"&select=file_id,extracted_text&order=id.asc&limit=400"):
            if e.get("file_id"):
                texts[e["file_id"]] = norm(e.get("extracted_text"))
        # transcribed scans count as that document's text
        for row in get(f"clancy_dn_incidents?id=eq.{iid}&select=doc_transcripts"):
            for d in (row.get("doc_transcripts") or []):
                blob = " ".join((p.get("text") or "") + " " + (p.get("desc") or "")
                                for p in d.get("pages") or [])
                texts[d["file_id"]] = (texts.get(d["file_id"], "") + " " + norm(blob)).strip()

        srcs, missing = [], []
        for i, fnd in enumerate(r["sygma_findings"] or []):
            total += 1
            got = resolve(fnd, files, texts)
            if got:
                linked += 1
            elif DEPOTNET_HINTS.search(fnd or ""):
                got = [{"file_id": None, "name": "Depotnet record for this damage",
                        "why": "cites Depotnet's own fields, not an attachment", "url": None}]
                linked += 1
            else:
                unsourced += 1
                missing.append(i)
            srcs.append(got)

        mark = "" if not missing else f"   UNSOURCED findings: {missing}"
        print(f"  {iid} {(r['location'] or '')[:30]:<30} "
              f"{sum(1 for s in srcs if s)}/{len(srcs)} findings sourced{mark}")
        if a.apply:
            urllib.request.urlopen(urllib.request.Request(
                URL + f"/rest/v1/clancy_dn_incidents?id=eq.{iid}",
                data=json.dumps({"sygma_finding_sources": srcs}).encode(),
                headers={**H, "Prefer": "return=minimal"}, method="PATCH"), timeout=120)

    print(f"\n{linked}/{total} findings carry a source Pete can open. {unsourced} unsourced.")
    if a.apply:
        print("written to clancy_dn_incidents.sygma_finding_sources")
    sys.exit(1 if unsourced else 0)


if __name__ == "__main__":
    main()
