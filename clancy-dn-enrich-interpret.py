#!/usr/bin/env python3
"""clancy-dn-enrich-interpret.py — hand out the documents that need reading BY A MODEL, and check them in.

Panel slides on Clancy's HSF-902 template parse deterministically (fixed headings). Everything
else — free-form panel decks, investigation reports, gang statements, permits, conference-call
notes — states its conclusion in prose, in whatever shape the author chose. Those need a reader,
not a regex.

This is the dispatcher for that pass, built the same way as the vision queue: batches out,
strict-schema results in, and nothing counted as done until a result exists that names its
evidence. The reader's ONE rule is that it may only report what the document says. "Not stated"
is always an allowed answer and is the right answer far more often than not.

  --batches N   split the outstanding documents into N batch files
  --check       what is still outstanding
  --load        validated results -> clancy_dn_doc_extracts (conclusions/lessons/method_failures)
"""
import os, sys, json, glob, argparse, urllib.request, urllib.parse, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
WORK = os.environ.get("ENRICH_WORK", "/tmp/enrich-work")
PARSER_VERSION = "e1-2026-08-03"
OUT = f"{WORK}/interpret/results"
BATCHES = f"{WORK}/interpret/batches"
MIN_TEXT = 400

SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _k["url"], _k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}


def rest(path, method="GET", body=None, headers=None):
    h = dict(H); h.update(headers or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 data=(json.dumps(body).encode() if body is not None else None),
                                 headers=h, method=method)
    with urllib.request.urlopen(req, timeout=180) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def rest_all(path, page=200):
    out, off = [], 0
    while True:
        chunk = rest(path, headers={"Range-Unit": "items", "Range": f"{off}-{off+page-1}"})
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page:
            break
        off += page
    return out


def candidates(fy="FY26/27"):
    ids = {r["id"] for r in rest_all(
        f"clancy_dn_incidents?select=id&fy=eq.{urllib.parse.quote(fy)}")}
    rows = rest_all(f"clancy_dn_doc_extracts?select=file_id,incident_id,file_name,doc_class"
                    f"&parser_version=eq.{PARSER_VERSION}"
                    f"&doc_class=not.in.(photo,video)&order=incident_id,file_id")
    return [r for r in rows if r["incident_id"] in ids]


def text_of(file_id):
    p = f"{WORK}/extracts/{file_id}.json"
    if not os.path.exists(p):
        return ""
    d = json.load(open(p))
    units = d.get("units") or {}
    return "\n\n".join(f"[{k}]\n{v}" for k, v in units.items())


def done(file_id):
    p = f"{OUT}/{file_id}.json"
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p))
    except Exception:
        return None
    for k in ("conclusions", "lessons", "method_failures", "key_facts"):
        if k not in d:
            return None
    return d


def outstanding():
    out = []
    for c in candidates():
        t = text_of(c["file_id"])
        if len(t) < MIN_TEXT:
            continue
        if done(c["file_id"]):
            continue
        out.append({**c, "chars": len(t)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--load", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    if a.batches:
        os.makedirs(BATCHES, exist_ok=True)
        for old in glob.glob(f"{BATCHES}/*.json"):
            os.remove(old)
        todo = outstanding()
        os.makedirs(f"{WORK}/interpret/text", exist_ok=True)
        for c in todo:
            open(f"{WORK}/interpret/text/{c['file_id']}.txt", "w").write(text_of(c["file_id"]))
        n = a.batches
        size = (len(todo) + n - 1) // n
        made = 0
        for i in range(0, len(todo), size):
            made += 1
            chunk = [{**c,
                      "text_path": f"{WORK}/interpret/text/{c['file_id']}.txt",
                      "result_path": f"{OUT}/{c['file_id']}.json"} for c in todo[i:i+size]]
            json.dump(chunk, open(f"{BATCHES}/batch-{made:02d}.json", "w"), indent=1)
            print(f"batch-{made:02d}: {len(chunk)} documents")
        print(f"{made} batches covering {len(todo)} documents")
        return

    if a.load:
        rows = []
        for c in candidates():
            d = done(c["file_id"])
            if not d:
                continue
            payload = {}
            if d.get("conclusions"):
                payload["conclusions"] = d["conclusions"][:12]
            if d.get("lessons"):
                payload["lessons"] = d["lessons"][:12]
            if d.get("method_failures"):
                payload["method_failures"] = d["method_failures"][:12]
            if d.get("people"):
                payload["people"] = d["people"][:20]
            if d.get("equipment"):
                payload["equipment"] = d["equipment"][:20]
            if d.get("dates"):
                payload["dates"] = d["dates"][:20]
            if not payload:
                continue
            payload["confidence"] = "read"
            rest(f"clancy_dn_doc_extracts?file_id=eq.{c['file_id']}"
                 f"&parser_version=eq.{PARSER_VERSION}", "PATCH", payload)
            rows.append(c["file_id"])
        print(f"loaded interpretive results for {len(rows)} documents")
        return

    todo = outstanding()
    have = len([c for c in candidates() if done(c["file_id"])])
    print(f"interpretive pass: {have} read, {len(todo)} outstanding")
    by = {}
    for c in todo:
        by[c["doc_class"]] = by.get(c["doc_class"], 0) + 1
    for k, v in sorted(by.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    sys.exit(0 if not todo else 2)


if __name__ == "__main__":
    main()
