#!/usr/bin/env python3
"""clancy-dn-vision-queue.py — hand the enrichment's vision queue out in batches, and check it back in.

Every image the enrichment finds (native photos, image-only PDF pages, photos embedded in panel
slides and reports, video frames, email attachments) goes on `vision-queue.jsonl`. A model has to
actually LOOK at each one — there is no deterministic reader for a photograph. This tool is the
dispatcher: it hands out batches of not-yet-read items and verifies what comes back.

The anti-skim contract (Pete, 3 Aug 2026): an item is only DONE when a result file exists with a
non-empty description AND the reader recorded whether the image contains readable text (and if it
does, the transcription). `--check` counts what is outstanding; `clancy-dn-enrich.py --gate` refuses
to pass while anything is unread.

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-vision-queue.py --batches 12        # print batch manifests
  VAULT=/tmp/pbs python3 clancy-dn-vision-queue.py --batch 3           # one batch's items as JSON
  VAULT=/tmp/pbs python3 clancy-dn-vision-queue.py --check             # what is still unread
"""
import os, sys, json, hashlib, argparse, glob

WORK = os.environ.get("ENRICH_WORK", "/tmp/enrich-work")
QUEUE = f"{WORK}/vision-queue.jsonl"
RESULTS = f"{WORK}/vision/results"


def key(path):
    return hashlib.md5(path.encode()).hexdigest()


def load():
    if not os.path.exists(QUEUE):
        return []
    seen, out = set(), []
    for line in open(QUEUE):
        v = json.loads(line)
        if v["path"] in seen:
            continue
        seen.add(v["path"])
        out.append(v)
    return out


_DB = None


def _db_readings():
    """image_path -> stored reading, keyset-paged, loaded once.

    Added 8 Aug 2026. This tool only ever looked at the /tmp result files, but those are a HANDOFF:
    `clancy-dn-enrich-index.py --load` copies them into clancy_dn_image_readings, which is what the
    pages and the promotion actually read. /tmp is cleaned by the OS and by any other session, so a
    fully-read year came back as "1016 items, 30 read, 986 outstanding" — and the stop hook then
    demanded 986 already-read images be read AGAIN, which would have doubled the cost and
    overwritten good readings with a second opinion.

    Paged on id because PostgREST caps a response at 1000 rows however large a limit you pass —
    the single unpaged read returned 1000 of 1016 and left 16 looking unread.
    """
    global _DB
    if _DB is not None:
        return _DB
    _DB = {}
    try:
        import urllib.request
        sec = os.path.expanduser("~/.config/pete-secrets")
        if not os.path.exists(f"{sec}/command-centre-supabase-keys.json"):
            sec = f"{os.environ.get('VAULT', '/tmp/pbs')}/Library/processes/secrets"
        k = json.load(open(f"{sec}/command-centre-supabase-keys.json"))
        url, sr = k["url"], k["service_role_key"]
        h = {"apikey": sr, "Authorization": f"Bearer {sr}"}
        last = None
        while True:
            q = ("clancy_dn_image_readings?select=id,image_path,description,has_text,transcription"
                 "&order=id.asc&limit=1000")
            if last is not None:
                q += f"&id=gt.{last}"
            req = urllib.request.Request(f"{url}/rest/v1/{q}", headers=h)
            batch = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
            if not batch:
                break
            for r in batch:
                if r.get("image_path"):
                    _DB[r["image_path"]] = r
            last = batch[-1]["id"]
            if len(batch) < 1000:
                break
    except Exception as e:
        # Never turn a lookup failure into a false "unread" — say so instead.
        print(f"  (note: could not read stored readings — {e}; counting result files only)",
              file=sys.stderr)
    return _DB


def _meets_contract(d):
    if not d:
        return None
    if not (d.get("description") or "").strip():
        return None
    if "has_text" not in d:
        return None
    if d.get("has_text") and not (d.get("transcription") or "").strip():
        return None
    return d


def result_of(v):
    """The scratch file if present, else the stored reading. Same anti-skim contract either way."""
    p = f"{RESULTS}/{key(v['path'])}.json"
    if os.path.exists(p):
        try:
            d = _meets_contract(json.load(open(p)))
            if d:
                return d
        except Exception:
            pass
    return _meets_contract(_db_readings().get(v["path"]))


def unread():
    return [v for v in load() if result_of(v) is None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int, help="split the unread queue into N batch files")
    ap.add_argument("--batch", type=int, help="print one batch's manifest")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    os.makedirs(RESULTS, exist_ok=True)
    q = load()
    todo = unread()

    if a.check or not any([a.batches, a.batch is not None]):
        by_origin = {}
        for v in todo:
            by_origin[v["origin"]] = by_origin.get(v["origin"], 0) + 1
        print(f"vision queue: {len(q)} items, {len(q)-len(todo)} read, {len(todo)} outstanding")
        for k, n in sorted(by_origin.items(), key=lambda x: -x[1]):
            print(f"  {k}: {n}")
        sys.exit(0 if not todo else 2)

    if a.batches:
        os.makedirs(f"{WORK}/vision/batches", exist_ok=True)
        for old in glob.glob(f"{WORK}/vision/batches/*.json"):
            os.remove(old)
        n = a.batches
        # group by incident so one batch sees a damage's whole story where possible
        todo_sorted = sorted(todo, key=lambda v: (v["incident_id"], v["file_id"], v["path"]))
        size = (len(todo_sorted) + n - 1) // n
        made = 0
        for i in range(0, len(todo_sorted), size):
            made += 1
            chunk = todo_sorted[i:i+size]
            bp = f"{WORK}/vision/batches/batch-{made:02d}.json"
            json.dump([{**v, "result_path": f"{RESULTS}/{key(v['path'])}.json"} for v in chunk],
                      open(bp, "w"), indent=1)
            print(f"batch-{made:02d}: {len(chunk)} items -> {bp}")
        print(f"{made} batches covering {len(todo_sorted)} outstanding items")
        return

    if a.batch is not None:
        bp = f"{WORK}/vision/batches/batch-{a.batch:02d}.json"
        print(open(bp).read())


if __name__ == "__main__":
    main()
