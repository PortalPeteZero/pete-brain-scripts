#!/usr/bin/env python3
"""clancy-dn-files.py — harvest every Depotnet attachment into Drive, without duplicating.

The file half of the API capture. Reads the signed URLs out of `clancy_dn_incidents.raw_api`
(banked by clancy-dn-ingest.py), downloads what Drive does not already hold, uploads it to that
damage's own folder, and records the `drive_id`.

WHY PYTHON AND NOT THE BROWSER. The first attempt downloaded through Chrome. It failed on a
whole class of files with a bare "Failed to fetch" and no usable error. Python gives the real
Azure response, retries properly, and can verify byte counts.

THE ENCODING RULE, MEASURED (1 Aug 2026, on
"1 Brompton Terrace, Perth PH2 7DH   HSF-177 Incident Alert Notification.pdf"):

    as-is                         InvalidURL (raw spaces are control characters to urllib)
    quote(path, safe="/")         403 — it also encodes the COMMA, which the signature covers
    quote(path, safe="/,")        403 — still encodes other reserved characters
    collapse runs of spaces       403 — the blob name really does hold the double spaces
    space -> "+"                  403
    space -> "%20", NOTHING ELSE  200, 492,145 bytes   <-- the only thing that works

Azure signed the path with the literal comma and the literal runs of spaces. Encode anything
except the space and the signature stops verifying. **51 of this year's 486 file paths already
contain %XX**, so a blanket quote() would double-encode those too. Hence `sign_safe_url()`:
replace ONLY " " with "%20" and touch nothing else, ever.

DEDUPE. Pete, 1 Aug 2026: "the key thing here is I don't want dupes". Drive happily stores two
files with the same name in the same folder and says nothing, so before uploading anything this
walks the damage's existing Drive folder and skips by filename. `--audit` reports without
touching. Run clancy-dn-drive-audit.py afterwards to prove it.

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-files.py --fy FY26/27            # harvest the year
  VAULT=/tmp/pbs python3 clancy-dn-files.py --id 133852             # one damage
  VAULT=/tmp/pbs python3 clancy-dn-files.py --fy FY26/27 --audit    # report only, no writes
"""
import os, re, sys, json, time, argparse, subprocess, tempfile, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
DRIVE = f"{VAULT}/drive-api.py"
DAMAGES_FOLDER = "19XZoec62Zjo02EQKWsrWszpOGcivG8aE"
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _k["url"], _k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}
_LS = re.compile(r"^(DIR|FILE)\s+(\S+)\s+(\S+)\s+([\w-]{20,})\s+(.*)$")

PHOTO_EXT = (".jpg", ".jpeg", ".png", ".heic", ".gif", ".bmp", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".avi", ".m4v", ".3gp")


def rest(path, method="GET", payload=None, extra=None):
    h = dict(H); h.update(extra or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
        data=json.dumps(payload).encode() if payload is not None else None, headers=h,
        method=method)
    try:
        t = urllib.request.urlopen(req, timeout=120).read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} {method} {path[:60]} — {e.read().decode()[:200]}")
    return json.loads(t) if t.strip() else None


def sign_safe_url(u):
    """The ONLY safe transform on a Depotnet signed URL. See the module docstring."""
    return u.replace(" ", "%20")


def kind_of(name):
    n = name.lower()
    if n.endswith(PHOTO_EXT):
        return "photo"
    if n.endswith(VIDEO_EXT):
        return "video"
    if n.endswith(".pdf") and n.startswith(("incident-", "incident manager")):
        return "pdf"
    return "document"


def drive(*args):
    r = subprocess.run(["python3", DRIVE, *args], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    m = re.search(r"ID: ([\w-]+)", r.stdout)
    return m.group(1) if m else None


def ls(fid):
    r = subprocess.run(["python3", DRIVE, "ls", fid], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    out = []
    for line in r.stdout.splitlines():
        m = _LS.match(line.strip())
        if m:
            out.append({"type": m.group(1), "id": m.group(4), "name": m.group(5).strip()})
    return out


def existing_names(folder_id):
    """Every filename already under a damage's folder, at any depth. The dedupe key."""
    names = {}
    def walk(fid, depth=0):
        for e in ls(fid):
            if e["type"] == "FILE":
                names.setdefault(e["name"], e["id"])
            elif depth < 3:
                walk(e["id"], depth + 1)
    walk(folder_id)
    return names


def incident_folder(inc, create=True):
    """A damage's Drive home — matched on the ID PREFIX, created once, never duplicated."""
    want = f"{inc['id']} "
    hits = [(e["name"], e["id"]) for e in ls(DAMAGES_FOLDER)
            if e["type"] == "DIR" and e["name"].startswith(want)]
    if len(hits) > 1:
        hits.sort(key=lambda h: -len(ls(h[1])))
        print(f"    !! {len(hits)} folders exist for {inc['id']} — using the fullest")
    if hits:
        return hits[0][1]
    if not create:
        return None
    town = (inc.get("location") or "location not stated")[:48]
    util = (inc.get("utility_class") or "utility not classified").replace("Electric — ", "Electric ")
    d = (inc.get("incident_date") or "")[:10]
    d = "-".join(reversed(d.split("-"))) if d else "no date"
    return drive("create-folder", f"{inc['id']} {town} ({util}, {d})", DAMAGES_FOLDER)


def subfolder(parent, name):
    for e in ls(parent):
        if e["type"] == "DIR" and e["name"] == name:
            return e["id"]
    return drive("create-folder", name, parent)


def files_in_payload(raw):
    """Every distinct attachment the payload references, with its action when it has one."""
    out = {}
    def walk(o, depth=0, action=None):
        if depth > 7 or o is None:
            return
        if isinstance(o, dict):
            aid = o.get("imIncidentActionId", action)
            if o.get("fileName") and o.get("path"):
                out.setdefault(o["fileName"], {"name": o["fileName"], "path": o["path"],
                                               "action_id": aid,
                                               "kind": kind_of(o["fileName"])})
            for v in o.values():
                walk(v, depth + 1, aid)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1, action)
    walk(raw)
    return list(out.values())


def fetch(url, tries=3):
    last = None
    for n in range(tries):
        try:
            with urllib.request.urlopen(sign_safe_url(url), timeout=180) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(1.5 * (n + 1))
    raise last


def run(rows, audit=False):
    tot = {"seen": 0, "skipped": 0, "uploaded": 0, "failed": 0, "bytes": 0}
    failures = []
    for inc in rows:
        raw = inc.pop("raw_api", None)
        if not raw:
            print(f"  {inc['id']}  no raw_api stored — run clancy-dn-ingest.py first")
            continue
        want = files_in_payload(raw)
        tot["seen"] += len(want)
        folder = incident_folder(inc, create=not audit)
        have = existing_names(folder) if folder else {}
        todo = [f for f in want if f["name"] not in have]
        print(f"  {inc['id']}  {len(want):3} on Depotnet · {len(have):3} in Drive · "
              f"{len(todo):3} to fetch")
        if audit or not todo:
            tot["skipped"] += len(want) - len(todo)
            continue
        tot["skipped"] += len(want) - len(todo)
        dsub = psub = None
        for f in todo:
            try:
                blob = fetch(f["path"])
            except Exception as e:
                tot["failed"] += 1
                failures.append((inc["id"], f["name"], f"{type(e).__name__}: {str(e)[:80]}"))
                print(f"      FAILED {f['name'][:56]} — {type(e).__name__}")
                continue
            if f["kind"] == "photo":
                psub = psub or subfolder(folder, "photos")
                target = psub
            elif f["kind"] == "pdf":
                target = folder
            else:
                dsub = dsub or subfolder(folder, "documents")
                target = dsub
            with tempfile.TemporaryDirectory() as td:
                p = os.path.join(td, f["name"].replace("/", "-"))
                open(p, "wb").write(blob)
                did = drive("upload", p, target, f["name"])
            tot["uploaded"] += 1
            tot["bytes"] += len(blob)
            # the row records what Depotnet holds; drive_id records where OUR copy is
            q = (f"clancy_dn_files?select=id&incident_id=eq.{inc['id']}"
                 f"&name=eq.{urllib.request.quote(f['name'])}")
            got = rest(q)
            body = {"drive_id": did,
                    "drive_folder": f"https://drive.google.com/drive/folders/{folder}"}
            if got:
                rest(f"clancy_dn_files?id=eq.{got[0]['id']}", "PATCH", body,
                     {"Prefer": "return=minimal"})
            else:
                rest("clancy_dn_files", "POST",
                     [{"incident_id": inc["id"], "action_id": f["action_id"],
                       "kind": f["kind"], "name": f["name"], "source": "depotnet-api", **body}],
                     {"Prefer": "return=minimal"})
    return tot, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fy")
    ap.add_argument("--id", type=int)
    ap.add_argument("--audit", action="store_true", help="report only, upload nothing")
    a = ap.parse_args()
    sel = "select=id,location,utility_class,incident_date,raw_api"
    if a.id:
        q = f"clancy_dn_incidents?{sel}&id=eq.{a.id}"
    elif a.fy:
        q = f"clancy_dn_incidents?{sel}&fy=eq.{urllib.request.quote(a.fy)}&order=id"
    else:
        sys.exit("give --fy or --id")
    rows = rest(q)
    print(f"{len(rows)} damage(s){' — AUDIT ONLY, nothing will be written' if a.audit else ''}\n")
    tot, failures = run(rows, a.audit)
    print(f"\n{tot['seen']} attachment(s) on Depotnet · {tot['skipped']} already in Drive · "
          f"{tot['uploaded']} uploaded ({tot['bytes'] / 1e6:.1f} MB) · {tot['failed']} failed")
    if failures:
        print("\nfailures — these are NOT in Drive:")
        for iid, name, err in failures:
            print(f"  {iid}  {name[:60]}  {err}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
