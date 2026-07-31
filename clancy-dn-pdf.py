#!/usr/bin/env python3
"""clancy-dn-pdf.py — parse a Depotnet incident PDF into the CC.

WHY: the register/action exports carry only the header fields. The per-incident PDF carries the
18 structured Incident Questions AND (once an investigation is complete) the full Report —
root cause, underlying cause, incident summary and lessons learnt. That is the material the
damages register needs in its "Outcome & key learning" column, and no bulk export we have access
to contains it (the Depotnet BI feeds need credentials Depotnet has not issued us).

So the capture is: Chrome (Pete's logged-in session) downloads the PDF + the incident's documents
and photos → this parses the PDF into `clancy_dn_answers` (one row per question) and promotes the
key fields onto `clancy_dn_incidents` → files are filed to Drive and indexed in `clancy_dn_files`.

Idempotent: re-parsing the same PDF overwrites the same rows (PK incident_id+section+question).

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-pdf.py <file.pdf> [<file.pdf> ...]
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-pdf.py --dir <folder-of-pdfs>
  ... --dry-run     parse and print, write nothing
"""
import os, sys, re, json, argparse, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]

def rest(path, method="GET", body=None, headers=None):
    h = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 data=(json.dumps(body).encode() if body is not None else None),
                                 headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        t = r.read().decode()
        return json.loads(t) if t else None

FOOTER = re.compile(r"Depotnet Incident Manager - please email.*?Page \d+ of \d+\n?", re.S)

# Both sections print the same way: "<n> <question>" then the answer on the next line(s), with
# short yes/no and date answers appended inline to the question line instead. Investigation
# questions restart their own numbering under a "Report Details" heading.
INLINE = [
    re.compile(r"^(?P<q>.*?)\s+(?P<a>YES|NO|N/A)$"),
    re.compile(r"^(?P<q>.*?)\s+(?P<a>\d{2}-\d{2}-\d{4}(?: \d{2}:\d{2})?)$"),
    re.compile(r"^(?P<q>.*?)\s+(?P<a>\d{1,2}:\d{2})$"),
    re.compile(r"^(?P<q>.*?\(Approx\) - Unit In MM)\s+(?P<a>\d+)$"),
    re.compile(r"^(?P<q>.*?Category)\s+(?P<a>Category \d)$"),
]


def _numbered(block):
    """Parse a '<n> question\\nanswer' block into [{q_no, question, answer}]."""
    items = []
    for chunk in re.split(r"\n(?=\d{1,2} )", block):
        mm = re.match(r"^(\d{1,2}) (.+)", chunk.strip(), re.S)
        if not mm:
            continue
        n, rest_ = int(mm.group(1)), mm.group(2).strip()
        lines = [l.strip() for l in rest_.split("\n")]
        # a question can wrap over lines; the answer starts after it. Join, then split on
        # the inline patterns; whatever is left after the first line is the answer body.
        q, a = lines[0], " ".join(lines[1:]).strip()
        for pat in INLINE:
            m2 = pat.match(q)
            if m2:
                q = m2.group("q").strip()
                a = (m2.group("a") + (" " + a if a else "")).strip()
                break
        else:
            # wrapped question: pull continuation lines that are clearly part of the question
            # (they end with ')' or the question ends mid-clause) — handled by the inline
            # match on the joined form below.
            joined = " ".join(lines)
            for pat in INLINE:
                m3 = pat.match(joined)
                if m3 and len(m3.group("q")) > len(q):
                    q, a = m3.group("q").strip(), m3.group("a").strip()
                    break
        items.append({"q_no": n, "question": q.strip(" *:"), "answer": (a or None)})
    return items


def parse_pdf(path):
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    text = FOOTER.sub("", text)
    m = re.search(r"Incident Manager \((\d+)\)", text)
    if not m:
        raise ValueError(f"{path}: no incident id in PDF")
    iid = int(m.group(1))
    out = {"incident_id": iid, "questions": [], "investigation": []}

    body = text.split("Incident Questions", 1)[1] if "Incident Questions" in text else ""
    if "Report Details" in body:
        qpart, rpart = body.split("Report Details", 1)
    else:
        qpart, rpart = body, ""
    qpart = re.split(r"\nAdditional Photos", qpart)[0]
    rpart = re.split(r"\nAdditional Photos|\nDocuments\b", rpart)[0]
    out["questions"] = _numbered(qpart)
    out["investigation"] = [q for q in _numbered(rpart) if q["answer"]]
    return out


def promote(parsed):
    """Lift the fields worth having as first-class columns on the incident row."""
    qa = {q["question"]: q["answer"] for q in parsed["questions"]}
    inv = {q["question"]: q["answer"] for q in parsed["investigation"]}
    row = {}
    def take(dst, src, d=qa):
        v = d.get(src)
        if v:
            row[dst] = v
    take("strike_category", "Service Strike Category")
    take("strike_subcategory", "Service Strike Sub-Category")
    take("environment", "Environment Of Works")
    take("caused_by_person", "Name of the person who caused the damage")
    take("caused_by_plant", "Damage Caused By")
    take("service_interrupted", "Service Interrupted")
    take("reported_to_owner_at", "Date & Time Incident Was Reported To Asset Owner")
    d = qa.get("Depth Of Utility (Approx) - Unit In MM")
    if d and d.strip().isdigit():
        row["depth_mm"] = int(d.strip())
    gps = qa.get("Date and Time of the Service Strike") or ""
    g = re.search(r"(-?\d{1,2}\.\d{3,})\s+(-?\d{1,3}\.\d{2,})", gps)
    if g:
        row["lat"], row["lon"] = float(g.group(1)), float(g.group(2))
    take("incident_summary", "Incident summary", inv)
    take("underlying_cause", "Service Strike Underlying Cause", inv)
    take("root_cause", "Service Strike Root Cause", inv)
    take("lessons_learnt", "Preventative Outcomes/Actions/Lessons Learnt", inv)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="*")
    ap.add_argument("--dir")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    paths = list(a.pdfs)
    if a.dir:
        paths += [os.path.join(a.dir, f) for f in sorted(os.listdir(a.dir)) if f.lower().endswith(".pdf")]
    if not paths:
        sys.exit("no PDFs given")
    for p in paths:
        try:
            parsed = parse_pdf(p)
        except Exception as e:
            print(f"SKIP {os.path.basename(p)}: {e}")
            continue
        iid = parsed["incident_id"]
        prom = promote(parsed)
        print(f"{iid}: {len(parsed['questions'])} question(s), {len(parsed['investigation'])} investigation field(s)"
              f"{' — INVESTIGATION COMPLETE' if parsed['investigation'] else ' — no investigation yet'}")
        for key in ("root_cause", "underlying_cause", "lessons_learnt"):
            if prom.get(key):
                print(f"    {key}: {prom[key][:90]}")
        if a.dry_run:
            continue
        if not rest(f"clancy_dn_incidents?id=eq.{iid}&select=id"):
            print(f"    ! incident {iid} is not in the register — import the export first; skipped")
            continue
        rows = [{"incident_id": iid, "section": "questions", "q_no": q["q_no"],
                 "question": q["question"], "answer": q["answer"]} for q in parsed["questions"]]
        rows += [{"incident_id": iid, "section": "investigation", "q_no": q["q_no"],
                  "question": q["question"], "answer": q["answer"]} for q in parsed["investigation"]]
        if rows:
            rest("clancy_dn_answers?on_conflict=incident_id,section,q_no", "POST", rows,
                 {"Prefer": "resolution=merge-duplicates"})
        import datetime as _dt
        rest(f"clancy_dn_incidents?id=eq.{iid}", "PATCH",
             {**prom, "pdf_captured_at": _dt.datetime.now(_dt.timezone.utc).isoformat()})
        print(f"    written: {len(rows)} answer row(s), {len(prom)} promoted field(s)")


if __name__ == "__main__":
    main()
