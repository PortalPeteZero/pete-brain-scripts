#!/usr/bin/env python3
"""Trash a filtered subset of TRASH-marked files from the dedupe manifest.

Usage:
  dedupe-trash-from-manifest.py safe   # ONLY Archive/binned/misfile dups, excl App Data (unambiguously dead)
  dedupe-trash-from-manifest.py all    # every TRASH row (DANGER: includes live folders) -- needs Pete

Each selected file already has a KEEP copy in its md5 group (guaranteed by the
dry-run logic), so content is always preserved; trashed files stay recoverable
in Drive trash for 30 days.
"""
import json, time, base64, urllib.request, urllib.parse, urllib.error, tempfile, os, subprocess, sys, re
from concurrent.futures import ThreadPoolExecutor
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

V = VAULT
KEY = V + "/Library/processes/secrets/google-seo-service-account.json"
IMP = "pete.ashcroft@sygma-solutions.com"; SCOPE = "https://www.googleapis.com/auth/drive"; BASE = "https://www.googleapis.com/drive/v3"
M = V + "/Projects/PA-Command-Centre/files/dedupe-manifest-2026-06-21.tsv"
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

def trash(fid):
    for a in range(7):
        try:
            req = urllib.request.Request(BASE + f"/files/{fid}?" + urllib.parse.urlencode({"supportsAllDrives": "true"}), data=json.dumps({"trashed": True}).encode(), headers={"Authorization": f"Bearer {tok()}", "Content-Type": "application/json"}, method="PATCH")
            urllib.request.urlopen(req, timeout=60); return
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503) and a < 6: time.sleep(2 * (a + 1)); continue
            raise
        except Exception:
            if a < 6: time.sleep(2 * (a + 1)); continue
            raise

rows = [l.rstrip("\n").split("\t") for l in open(M) if l.strip()]
rows = [r for r in rows if len(r) >= 5 and r[1] == "TRASH"]
mode = sys.argv[1] if len(sys.argv) > 1 else "safe"
if mode == "safe":
    sel = [r for r in rows if re.search(r"Archive/|binned|misfile", r[4]) and "App Data" not in r[4]]
elif mode == "rest":
    # the held live-folder dups, EXCLUDING App Data (app-referenced). One copy always
    # survives per md5 group (KEEP rows are never trashed); everything recoverable 30 days.
    sel = [r for r in rows if "App Data" not in r[4] and not re.search(r"Archive/|binned|misfile", r[4])]
else:
    sel = rows
print(f"mode={mode}: selected {len(sel)} files, {sum(int(r[2]) for r in sel)//1048576} MB", flush=True)
done = 0; fails = []
def do(r):
    try: trash(r[3]); return None
    except Exception as e: return f"{r[3]}\t{r[4]}\t{e}"
with ThreadPoolExecutor(max_workers=12) as ex:
    for x in ex.map(do, sel):
        if x: fails.append(x)
        else: done += 1
print(f"TRASHED {done}, failed {len(fails)}", flush=True)
if fails: open(M + f".{mode}-fails", "w").write("\n".join(fails))