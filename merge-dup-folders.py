#!/usr/bin/env python3
"""merge-dup-folders.py -- merge specific duplicate sibling folders (keeper <- dup).

For each (keeper_id, dup_id): recursively move dup's children into keeper — when a
child folder name clashes with a keeper child folder, recurse into that pair instead
of creating a new duplicate — then trash the now-empty dup. Live (re-reads children
each step), so it reflects current Drive state, not the possibly-stale index.
Everything trashed is recoverable 30 days; moves are reversible.
"""
import json, time, base64, urllib.request, urllib.parse, urllib.error, tempfile, os, subprocess
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

V = VAULT
KEY = V + "/Library/processes/secrets/google-seo-service-account.json"
IMP = "pete.ashcroft@sygma-solutions.com"; SCOPE = "https://www.googleapis.com/auth/drive"; BASE = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
creds = json.load(open(KEY)); _tc = {}

def tok():
    now = int(time.time())
    if _tc.get("exp", 0) > now + 60: return _tc["tok"]
    b = lambda d: base64.urlsafe_b64encode(d if isinstance(d, bytes) else d.encode()).decode().rstrip("=")
    ts = b(json.dumps({"alg": "RS256", "typ": "JWT"})) + "." + b(json.dumps({"iss": creds["client_email"], "sub": IMP, "scope": SCOPE, "aud": "https://oauth2.googleapis.com/token", "exp": now + 3600, "iat": now}))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f: f.write(creds["private_key"]); kf = f.name
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", kf, "-binary"], input=ts.encode(), capture_output=True).stdout; os.unlink(kf)
    r = urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": ts + "." + b(sig)}).encode()))
    t = json.loads(r.read())["access_token"]; _tc.update(tok=t, exp=now + 3600); return t

def req(method, path, params, body=None):
    data = json.dumps(body).encode() if body else None
    for a in range(6):
        try:
            h = {"Authorization": f"Bearer {tok()}"}
            if data: h["Content-Type"] = "application/json"
            return json.loads(urllib.request.urlopen(urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(params), data=data, headers=h, method=method), timeout=90).read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503) and a < 5: time.sleep(2 * (a + 1)); continue
            raise
        except Exception:
            if a < 5: time.sleep(2 * (a + 1)); continue
            raise

def children(fid):
    out = []; pt = None
    while True:
        p = {"q": f"'{fid}' in parents and trashed=false", "includeItemsFromAllDrives": "true", "supportsAllDrives": "true", "corpora": "allDrives", "fields": "nextPageToken,files(id,name,mimeType)", "pageSize": 1000}
        if pt: p["pageToken"] = pt
        r = req("GET", "/files", p); out += r.get("files", []); pt = r.get("nextPageToken")
        if not pt: break
    return out

def move(fid, dest, cur):
    req("PATCH", f"/files/{fid}", {"addParents": dest, "removeParents": cur, "supportsAllDrives": "true"}, body={})

def trash(fid):
    req("PATCH", f"/files/{fid}", {"supportsAllDrives": "true"}, body={"trashed": True})

def merge(keeper, dup, depth=0):
    kfolders = {c["name"]: c["id"] for c in children(keeper) if c["mimeType"] == FOLDER}
    moved = 0
    for c in children(dup):
        if c["mimeType"] == FOLDER and c["name"] in kfolders:
            moved += merge(kfolders[c["name"]], c["id"], depth + 1)
        else:
            move(c["id"], keeper, dup); moved += 1
    trash(dup)
    print(f"{'  '*depth}merged {dup} -> {keeper} ({moved} items moved, dup trashed)", flush=True)
    return moved

# (keeper_id, dup_id) — keeper = the member with more content (empty dups just get trashed)
PAIRS = [
    ("1Th2FFeVSP4fsj3THvO9EkJv7d-ShXRAA", "1xaepXL9NhCDERuEH6h4SDN8ME8Zx1PPZ"),  # Volker 2021-07
    ("1meuhHkrPmoL_2QeyTl21lSz02M3n8Gj2", "1u1K8x3sOsPGSzNRLQLJQk1GzHjWugu9M"),  # Jackson Civil 2023-03
    ("1cvdUvuYj4stFZJpzSr4zedubZPmpavko", "1r7UiU6F3D_IQsK7bYfNlXMyl4hynAM83"),  # MWS 2023-03
    ("1k7G7f1RjWGIvewOwrjYQP-san3X6cJuE", "1TcllbtEJdXCNgfLAOol7ppSiZ_222lwO"),  # DDL 2023-05 (dup empty)
    ("1HpuuqNKf_LPAUXTSDIYjf4qtJa3tMiWH", "1Xt_mnHSPdEtSQZb9vKv3BrmHC9BEoWaf"),  # Farrans 2023-11 (dup empty)
    ("127lo6sVFL-3Six178sL7v1j_Qvj30B6Z", "1AivquCQ8CeZ792kQfY-DsNAPsfB72CW1"),  # David Binnington
    ("1mihQKJYVGxgCa5V01QMd6AALETx2M4W8", "1chJ8HsAquxPA9Oqk5PX0Oc2myb7XpYqJ"),  # Centre Approval Policies Pdf
    ("14PHSd_LhHbk9_3GrElDybXv57hxf2Dd4", "1QKlQf9t3n8TielVqOg-0BTxYO8GetnWY"),  # IQA Strategy Pdf (dup empty)
    ("13yzQ5KpUGSsH5zpdW66sa5ZdXivDzROT", "1Wcy7nmie15l6LH2-Y8SNpWIJd2U_1v7Z"),  # IQA Strategy Word
]
for k, d in PAIRS:
    try:
        merge(k, d)
    except Exception as e:
        print(f"FAILED {d} -> {k}: {e}", flush=True)
print("DONE", flush=True)