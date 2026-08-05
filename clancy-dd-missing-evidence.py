#!/usr/bin/env python3
"""clancy-dd-missing-evidence.py — what does the record SAY exists that we do not hold?

Pete, 5 Aug 2026, after spotting a video in the record that no finding mentioned: "so why didnt
you raise this key finding in your conclusions you wrote?"

Because every other check asks *did we read what we have*. None of them asks *what does the
record say exists that we do not have*. Those are different questions, and only the second one
finds a missing video.

The miss it was built from, damage 153523:
  · The Service Avoidance pre-start form answers YES to "Has a Service Avoidance Video call been
    completed with the team?" and names its evidence as "Main street pic Brett Ward.jpg". That
    file is not held. The declaration confirming the call (question 15) is blank.
  · Tony Widnall's signed witness statement says "Video of the area was taken before any cutting
    or digging took place." No video is held on the damage, in any form.
  Both lines were READ. Both were in front of me. Neither was checked against the file list,
  because nothing asked it to be.

WHERE IT LOOKS
  Every readable word we hold for a damage: the document extracts, the transcribed scans (a
  photographed form's contents only exist there), and Depotnet's own question answers.

WHAT IT REPORTS, by strength
  1 NAMED FILE     the text names a file with an extension ("Main street pic Brett Ward.jpg") and
                   no file we hold matches it. Strongest: someone wrote down a specific artefact.
  2 MEDIUM CLAIMED the text asserts a recording exists ("video of the area was taken", "footage",
                   "dashcam") and we hold NO file of that kind on the damage at all.
  3 ASSERTED       a Depotnet answer says an artefact was PRODUCED or REVIEWED (a CAT data
                   download, a trial hole record, a D&A test, a witness statement) and nothing
                   on the record could be it. Needs nobody to have written a filename down,
                   which is why it catches what the other two miss.

WHAT IT DELIBERATELY IGNORES
  Generic plurals. "photographs were taken" against a damage that holds photographs is not a gap,
  and a checker that says otherwise is noise. A medium is only reported when NONE of that kind is
  held. Boilerplate template lines ("insert photos here", "upload screenshot of...") are matched
  as questions, never as claims — the ANSWER is what gets checked.

  VAULT=/tmp/pbs python3 clancy-dd-missing-evidence.py                # FY26/27
  VAULT=/tmp/pbs python3 clancy-dd-missing-evidence.py --id 153523
  VAULT=/tmp/pbs python3 clancy-dd-missing-evidence.py --all-years --json

Exit 0 = nothing referenced is missing. Exit 1 = something is. Exit 2 = could not run.
"""
import argparse, json, os, re, sys, urllib.parse, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_K = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _K["url"], _K["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}"}

# A filename, and ONLY a filename. Without the delimiter anchor and the no-sentence-punctuation
# rule this happily swallowed the sentence in front of it and reported
# "all unticked. FPM Evidence section shows one uploaded file, 'Main street pic Brett Ward.jpg"
# as the missing artefact. A name may contain spaces, so the stop condition has to be the
# punctuation that only ever appears BETWEEN sentences, never inside a filename.
FILE_RE = re.compile(
    r"(?:^|[\s:>'\"\u2018\u2019\u201c\u201d(\[|])"
    r"([A-Za-z0-9][A-Za-z0-9 _()&'\u2010-\u2015-]{2,70}?"
    r"\.(?:jpe?g|png|gif|bmp|heic|mp4|mov|avi|3gp|mkv|wmv|m4v|pdf|docx?|xlsx?|pptx?|msg|csv))"
    r"(?![A-Za-z0-9])",
    re.I)

# A recording is CLAIMED to exist. Present tense of the artefact, not of the instruction: the
# blank form line "Upload Screenshot of Video Call" is a question, so it is excluded below.
MEDIA_CLAIM = [
    ("video", re.compile(r"\b(video|footage|recording|dashcam|helmet cam|body ?cam)\b"
                         r"(?:[^.|?]{0,60})\b(was|were|is|are|has been|have been|taken|recorded|"
                         r"captured|uploaded|attached|available|held|sent)\b", re.I)),
    ("video", re.compile(r"\b(was|were)\b[^.|?]{0,40}\b(video|footage|recorded|filmed)\b", re.I)),
]
# A video CALL is a conversation, not footage. "Has a Service Avoidance Video call been completed"
# is a process question and firing on it buries the real find. The call's own EVIDENCE is a named
# file, so a genuinely missing screenshot is still caught by FILE_RE.
NOT_FOOTAGE = re.compile(r"video ?(call|conference|link|meeting)", re.I)
# OUR OWN extractor's annotations, not the record's claim. An email's inline images come out of
# the .msg as "[attachment: image001.jpg]" followed by "[image — routed to vision]": that file was
# handled, it is a part of a document we hold rather than a separate artefact anyone is missing.
# Matching our own bookkeeping and reporting it as a gap is how a useful check becomes noise --
# 20 of the first 22 hits across FY26/27 were exactly this.
OUR_MARKER = re.compile(r"\[attachment:|routed to vision|\[image\b|\[document\b|\[embedded", re.I)

# ASSERTED ARTEFACTS — the third and most important shape, and the one that needs nobody to have
# written a filename down. A Depotnet answer says a thing was PRODUCED or REVIEWED; if it was,
# there should be something on the record to look at.
#
# Pete, 5 Aug 2026: "again i dunno why you dindt pick up there was no data download."
# Damage 153523 answers YES to "CAT data downloaded and reviewed in the portal?" and YES to all
# four per-mode reviews (Genny / Power / Radio / Avoidance), with every mode's usage time recorded
# as 00:00 — and holds NO CAT data file of any kind. Damage 152586, the control, holds
# "Hollow Lane 152586 - CAT and Genny data review.pdf". The claim was unverifiable and nothing
# said so.
#
# (question matches, answer counts as YES, what the artefact looks like in a filename, plain name)
ASSERTED = [
    (re.compile(r"cat data download|data downloaded and reviewed|download review", re.I),
     re.compile(r"^\s*(yes|y)\b", re.I),
     re.compile(r"cat|genny|locator|scan|download", re.I),
     "the CAT/Genny data download it says was reviewed"),
    (re.compile(r"trial hole", re.I),
     re.compile(r"^\s*(yes|y)\b", re.I),
     re.compile(r"trial ?hole|hsf-?163|record sheet", re.I),
     "the trial hole record it says was completed"),
    (re.compile(r"drugs and alcohol|d&a test", re.I),
     re.compile(r"^\s*(yes|y)\b", re.I),
     re.compile(r"drug|alcohol|d&a", re.I),
     "the drugs and alcohol test result it says was carried out"),
    (re.compile(r"witness statement", re.I),
     re.compile(r"^\s*(yes|y)\b", re.I),
     re.compile(r"statement", re.I),
     "the witness statement it says was taken"),
]
VIDEO_EXT = re.compile(r"\.(mp4|mov|avi|3gp|mkv|wmv|m4v)$", re.I)
# Lines that are the FORM's own wording, not an assertion by a person.
TEMPLATE = re.compile(r"insert (photos?|images?)|upload (a |the )?(screenshot|photo|document|video)"
                      r"|if yes,? (then )?upload|please upload|attach (a |the )?copy", re.I)


def get(path):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(URL + "/rest/v1/" + path, headers=H), timeout=240).read().decode())


def norm_name(s):
    """Compare filenames the way a human would: case, spacing and Depotnet's date prefix ignored."""
    s = (s or "").lower().strip()
    s = re.sub(r"^\d{4}-\d\d-\d\d-[\d-]+_", "", s)
    return re.sub(r"[^a-z0-9.]+", "", s)


def scan(iid):
    files = get(f"clancy_dn_files?incident_id=eq.{iid}&select=id,name,kind&order=id.asc&limit=400")
    held = {norm_name(f["name"]) for f in files}
    held_stems = {norm_name(re.sub(r"\.[a-z0-9]{2,5}$", "", f["name"], flags=re.I)) for f in files}
    have_video = any(f["kind"] == "video" or VIDEO_EXT.search(f["name"]) for f in files)

    sources = []
    for e in get(f"clancy_dn_doc_extracts?incident_id=eq.{iid}"
                 f"&select=file_name,extracted_text&order=id.asc&limit=400"):
        sources.append((e["file_name"], e.get("extracted_text") or ""))
    for row in get(f"clancy_dn_incidents?id=eq.{iid}&select=doc_transcripts"):
        for d in (row.get("doc_transcripts") or []):
            for p in d.get("pages") or []:
                sources.append((d["name"] + " (scanned)",
                                (p.get("text") or "") + "\n" + (p.get("desc") or "")))
    answers = get(f"clancy_dn_answers?incident_id=eq.{iid}"
                  f"&select=q_no,section,question,answer&order=q_no.asc&limit=300")
    for a in answers:
        if a.get("answer"):
            sources.append((f"Depotnet answer Q{a['q_no']} [{a['section']}]", str(a["answer"])))

    found, seen = [], set()

    # 3 ASSERTED — an answer says an artefact exists, and nothing on the record could be it.
    for a in answers:
        q, ans = a.get("question") or "", str(a.get("answer") or "")
        for qrx, arx, frx, label in ASSERTED:
            if not qrx.search(q) or not arx.match(ans.strip()):
                continue
            if any(frx.search(f["name"]) for f in files):
                continue
            key = ("asserted", label)
            if key in seen:
                continue
            seen.add(key)
            found.append({"kind": "ASSERTED, NOT HELD", "what": label,
                          "where": f"Depotnet answer Q{a['q_no']} [{a['section']}]",
                          "quote": f"{q.strip()[:130]} -> {ans.strip()[:60]}"})

    for where, text in sources:
        if not text:
            continue
        for m in FILE_RE.finditer(text):
            name = m.group(1).strip()
            n = norm_name(name)
            if n in held or norm_name(re.sub(r"\.[a-z0-9]{2,5}$", "", name)) in held_stems:
                continue
            # A partial capture is not a missing file. Depotnet stores an answer as a list of
            # storage paths ("default/client18/jobs/2026-07-31-10-52-29-457_Grasby strike pic
            # 1.jpg,..."), and the delimiter anchor can land mid-name and yield "strike pic
            # 1.jpg". If any file we hold ENDS WITH what was captured, we hold it.
            if any(h.endswith(n) or n.endswith(h) for h in held if len(h) >= 8):
                continue
            if len(n) < 8:
                continue
            around = text[max(0, m.start() - 40):m.end() + 60]
            if OUR_MARKER.search(around):
                continue
            key = ("file", n)
            if key in seen:
                continue
            seen.add(key)
            found.append({"kind": "NAMED FILE", "what": name, "where": where,
                          "quote": text[max(0, m.start() - 90):m.end() + 40].strip()})
        if have_video:
            continue
        for label, rx in MEDIA_CLAIM:
            for m in rx.finditer(text):
                line = text[max(0, m.start() - 120):m.end() + 120]
                if TEMPLATE.search(line) or NOT_FOOTAGE.search(line):
                    continue
                if "?" in text[m.start():m.end() + 40]:
                    continue                      # a question is not an assertion
                key = ("media", label, where)
                if key in seen:
                    continue
                seen.add(key)
                found.append({"kind": "MEDIUM CLAIMED", "what": f"a {label} is said to exist",
                              "where": where, "quote": " ".join(line.split())[:220]})
    return files, found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int)
    ap.add_argument("--fy", default="FY26/27")
    ap.add_argument("--all-years", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        if a.id:
            inc = get(f"clancy_dn_incidents?select=id,location&id=eq.{a.id}")
        else:
            fys = ["FY26/27", "FY25/26"] if a.all_years else [a.fy]
            inc = []
            for fy in fys:
                inc += get("clancy_dn_incidents?select=id,location&fy=eq."
                           + urllib.parse.quote(fy, safe="") + "&order=id.asc&limit=4000")
        out, n_gap = [], 0
        for r in inc:
            files, found = scan(r["id"])
            if found:
                n_gap += len(found)
                out.append({"id": r["id"], "location": r["location"], "files": len(files),
                            "missing": found})
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"clancy-dd-missing-evidence: COULD NOT RUN — {e}", file=sys.stderr)
        sys.exit(2)

    if a.json:
        print(json.dumps({"damages_with_gaps": len(out), "gaps": n_gap, "detail": out}, indent=1))
        sys.exit(1 if n_gap else 0)

    print(f"=== EVIDENCE THE RECORD NAMES BUT WE DO NOT HOLD ===")
    print(f"    {len(inc)} damage(s) scanned · {len(out)} with a gap · {n_gap} item(s)\n")
    for d in out:
        print(f"  {d['id']}  {(d['location'] or '')[:38]:<38} ({d['files']} files held)")
        for m in d["missing"]:
            print(f"      [{m['kind']}] {m['what']}")
            print(f"          named in: {m['where'][:70]}")
            print(f"          \"{m['quote'][:170]}\"")
        print()
    if not out:
        print("  nothing referenced in any record is missing from what we hold.")
    sys.exit(1 if n_gap else 0)


if __name__ == "__main__":
    main()
