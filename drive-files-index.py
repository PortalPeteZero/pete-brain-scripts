#!/usr/bin/env python3
"""drive-files-index.py -- scan the home drives + My Drive and upsert every
folder + file into the CC `drive_files` table (the file-index / where-is-everything).

Flat-scan per drive (fast) + in-memory path resolution, then batched upsert
(on_conflict=drive_file_id) into CC Supabase via the REST API. Idempotent —
re-run any time for a full refresh; the Changes-API watcher keeps it current between runs.

PRUNE PASS (added 25 Jul 2026). Upsert-only meant a row for a file that had LEFT the indexed
scope survived every future re-index forever — nothing ever deleted it. Measured that day:
63 stale rows, 59 of them iPhone camera-roll videos captured on 2026-06-20 whose ancestry ran
up into the shared drive "Petes Photo Archive", which the MAP lists as excluded entirely. The
My-Drive ownership fix (2026-06-25, below) stopped NEW leaks but could not clear the existing
rows, and a full re-index did not either. Pete had to delete them by hand.

So after a scan, rows in the table that this scan did NOT see are treated as prune candidates.
It is fail-safe by construction — it would rather leave a stale row than delete a live one:
  • ANY per-drive "SCAN FAILED", or ANY failed upsert batch, skips the prune entirely
    (otherwise one transient API blip would delete that whole drive's index).
  • A partial/errored read of the existing id list skips the prune (never "partial = complete").
  • Candidates first indexed AFTER this scan started are left alone — that is the 15-min
    changes-watcher inserting a genuinely new file mid-scan (indexed_at is FIRST-seen and is
    not touched by later upserts).
  • An absurd candidate count (>1% of the table) is a scan anomaly, not stale rows: refuse.
  • Every surviving candidate is VERIFIED one-by-one against the Drive API before deletion.
    A candidate that still exists and IS in indexed scope is NOT deleted — it is reported,
    because that means the scan missed a live file and that is a bug worth seeing.

REPORT-ONLY by default (the house pattern, same as drive-path-rebuild.py) — the scan+upsert
always writes, but nothing is deleted without --apply.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/drive-files-index.py            # index + report prune candidates
  VAULT=/tmp/pbs python3 /tmp/pbs/drive-files-index.py --apply    # index + delete verified-stale rows
"""
import json, time, base64, urllib.request, urllib.parse, urllib.error, tempfile, os, subprocess, sys
import os
from datetime import datetime
VAULT = os.environ.get("VAULT", "/tmp/pbs")

V = VAULT
KEY = V + "/Library/processes/secrets/google-seo-service-account.json"
IMP = "pete.ashcroft@sygma-solutions.com"; SCOPE = "https://www.googleapis.com/auth/drive"; BASE = "https://www.googleapis.com/drive/v3"
ck = json.load(open(V + "/Library/processes/secrets/command-centre-supabase-keys.json"))
CCURL = ck["url"]; SR = ck["service_role_key"]
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

def api(params):
    for a in range(7):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/files?" + urllib.parse.urlencode(params), headers={"Authorization": f"Bearer {tok()}"}), timeout=120).read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 500, 502, 503) and a < 6: time.sleep(2 * (a + 1)); continue
            raise
        except Exception:
            if a < 6: time.sleep(2 * (a + 1)); continue
            raise

def page(params):
    out = []; pt = None
    while True:
        q = dict(params)
        if pt: q["pageToken"] = pt
        r = api(q); out += r.get("files", []); pt = r.get("nextPageToken")
        if not pt: break
    return out

SHARED = {"Sygma Hub": "0APzpyHHfvUyIUk9PVA", "Canary Detect": "0AAcMZiTrK0txUk9PVA", "Sygma Private": "0AC_ioGo0GJ3tUk9PVA", "Ashcroft Family": "0ACX0xe254y5kUk9PVA", "One System": "0AGTfg0QwTS8kUk9PVA", "El Atico": "0AP-TBWWevTInUk9PVA",
          "Sygma Mala": "0ANYL9DOJQtmQUk9PVA", "Sygma Trainers": "0AP9_VgbvNGyEUk9PVA", "External Sygma Solutions": "0AOTm_FPU_iRmUk9PVA", "External Canary Detect": "0APjm9rgEA8PDUk9PVA",
          "Entities Private": "0APHr3b2NkrNNUk9PVA", "Passion Fit": "0AI3_VD66sPWyUk9PVA"}
FFIELDS = "nextPageToken,files(id,name,parents,driveId)"
XFIELDS = "nextPageToken,files(id,name,parents,mimeType,size,modifiedTime,driveId)"

def build(drive, folders, files):
    fmap = {f["id"]: (f["name"], (f.get("parents") or [None])[0]) for f in folders}
    def path(fid):
        parts = []; cur = fid; seen = set()
        while cur in fmap and cur not in seen:
            seen.add(cur); nm, par = fmap[cur]; parts.append(nm); cur = par
        return "/".join(reversed(parts))
    rows = []
    for f in folders:
        rows.append({"drive_file_id": f["id"], "name": f["name"], "path": path(f["id"]), "drive": drive, "entity": drive, "mime": "folder", "size": None, "modified_time": None, "is_folder": True, "parent_id": (f.get("parents") or [None])[0]})
    for f in files:
        par = (f.get("parents") or [None])[0]
        pp = path(par)
        rows.append({"drive_file_id": f["id"], "name": f["name"], "path": (pp + "/" + f["name"]) if pp else f["name"], "drive": drive, "entity": drive, "mime": f.get("mimeType"), "size": int(f["size"]) if f.get("size") else None, "modified_time": f.get("modifiedTime"), "is_folder": False, "parent_id": par})
    # cold-backup folders are hidden from the file index (Pete, 2026-06-26)
    return [r for r in rows if "_backups" not in (r["path"] or "").split("/")]

def scan_shared(name, did):
    common = {"corpora": "drive", "driveId": did, "includeItemsFromAllDrives": "true", "supportsAllDrives": "true", "pageSize": 1000}
    folders = page({**common, "q": "mimeType='application/vnd.google-apps.folder' and trashed=false", "fields": FFIELDS})
    files = page({**common, "q": "mimeType!='application/vnd.google-apps.folder' and trashed=false", "fields": XFIELDS})
    return build(name, folders, files)

def scan_mydrive():
    common = {"corpora": "user", "spaces": "drive", "supportsAllDrives": "true", "pageSize": 1000}
    # `'me' in owners` keeps My Drive to the files Pete OWNS. Without it, corpora=user also returns
    # every "Shared with me" item, which then gets indexed + relabelled 'My Drive' — clutter that
    # doesn't match Pete's real My Drive. (My-Drive ownership fix, 2026-06-25.)
    folders = page({**common, "q": "mimeType='application/vnd.google-apps.folder' and trashed=false and 'me' in owners", "fields": FFIELDS})
    files = page({**common, "q": "mimeType!='application/vnd.google-apps.folder' and trashed=false and 'me' in owners", "fields": XFIELDS})
    # A user-OWNED item that physically lives in a shared drive surfaces in this corpora=user pass too.
    # Its own scan_shared() pass already captures it with the correct drive + full path, so drop it here
    # — otherwise build() relabels it 'My Drive' (the original index-corruption bug; same guard as
    # drive-changes-watch.py: `if did is None and f.get("driveId"): continue`).
    folders = [f for f in folders if not f.get("driveId")]
    files = [f for f in files if not f.get("driveId")]
    return build("My Drive", folders, files)

def getfile(fid):
    """One files.get, for VERIFYING a prune candidate. Returns the metadata dict, the string
    "gone" for a 404 (the file genuinely no longer exists), or None if we could not find out —
    and None must never be read as "gone"."""
    for a in range(5):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(
                BASE + "/files/" + urllib.parse.quote(fid) + "?" + urllib.parse.urlencode(
                    {"fields": "id,name,trashed,driveId,ownedByMe", "supportsAllDrives": "true"}),
                headers={"Authorization": f"Bearer {tok()}"}), timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code in (404, 410): return "gone"
            if e.code in (403, 429, 500, 502, 503) and a < 4: time.sleep(2 * (a + 1)); continue
            return None
        except Exception:
            if a < 4: time.sleep(2 * (a + 1)); continue
            return None


def epoch(ts):
    """indexed_at as stored ('2026-06-22 06:59:42.162827+00') → epoch seconds. An unparseable
    value returns +inf, i.e. treated as brand-new and therefore never pruned."""
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except Exception:
        return float("inf")


def sql(query):
    """One SQL read via cc-sql.py (the house pattern — same as drive-path-rebuild.py). Returns
    None on ANY failure, and None means ERRORED, never 'no rows': the prune treats a failed read
    as a reason to do nothing at all."""
    r = subprocess.run(["python3", os.path.join(V, "cc-sql.py"), query],
                       env={**os.environ, "VAULT": V}, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        # cc-sql.py prints its error to STDOUT, so stderr alone is usually empty.
        why = (r.stderr or "").strip() or (r.stdout or "").strip()
        print(f"SQL READ FAILED: {why[:220]}", flush=True); return None
    try:
        return json.loads(r.stdout)
    except Exception as e:
        print(f"SQL READ UNPARSEABLE: {e}", flush=True); return None


def existing():
    """{drive_file_id: indexed_at-as-epoch} for the WHOLE table, in one read — 152k rows come back
    as a single newline-joined blob (~10 MB, ~12 s; the same thing paged over the REST API at
    PostgREST's 1000-row cap took 136 s). Returns None if the read failed or came back empty —
    an empty drive_files is impossible here, so that is a lying read, not a truth to prune on."""
    r = sql("SELECT string_agg(drive_file_id||'~'||indexed_at, chr(10)) AS blob FROM drive_files")
    if r is None: return None
    blob = (r[0].get("blob") if r else None)
    if not blob:
        print("EXISTING-READ came back EMPTY — impossible for this table, so the read lied", flush=True)
        return None
    out = {}
    for line in blob.split("\n"):
        fid, _, ts = line.partition("~")
        if fid: out[fid] = epoch(ts)
    return out


def details(ids):
    """path + drive for the (small) candidate set, for the report. Best-effort: the prune decision
    never depends on this, so a failure just costs us the pretty labels."""
    if not ids: return {}
    lst = ",".join("'" + x.replace("'", "''") + "'" for x in ids)
    r = sql(f"SELECT drive_file_id, path, drive FROM drive_files WHERE drive_file_id IN ({lst})")
    return {x["drive_file_id"]: x for x in (r or [])}


def snapshot(ids):
    """Save the FULL rows about to be deleted into the CC config key `drive-files-prune-last`, so a
    wrong prune is recoverable. When the 63 stale rows were cleared by hand on 25 Jul 2026 the
    snapshot went to an ephemeral scratchpad and was gone within the hour — hence a durable home.
    Returns False if it could not be written, and the caller then deletes NOTHING."""
    lst = ",".join("'" + x.replace("'", "''") + "'" for x in ids)
    r = sql("INSERT INTO config (key, value, updated_at) SELECT 'drive-files-prune-last',"
            " json_build_object('pruned_at', now(), 'rows', json_agg(d))::text, now()"
            f" FROM drive_files d WHERE d.drive_file_id IN ({lst})"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()")
    return r is not None


def delete_ids(ids):
    H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}
    done = 0
    for i in range(0, len(ids), 100):
        b = ids[i:i + 100]
        u = f"{CCURL}/rest/v1/drive_files?drive_file_id=in.({','.join(urllib.parse.quote(x) for x in b)})"
        for a in range(5):
            try:
                urllib.request.urlopen(urllib.request.Request(u, headers=H, method="DELETE"), timeout=120)
                done += len(b); break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and a < 4: time.sleep(2 * (a + 1)); continue
                print("DELETE ERR", e.code, e.read().decode()[:300], flush=True); break
            except Exception:
                if a < 4: time.sleep(2 * (a + 1)); continue
                print("DELETE ERR (gave up on this batch)", flush=True); break
    return done


def classify(fid, row):
    """Is this row's file still something we index? Returns (verdict, note).
    stale-* = safe to delete · in-scope = DO NOT delete (the scan missed a live file) ·
    unknown = could not verify, leave alone."""
    # Policy exclusion needs no API call: cold-backup folders are deliberately hidden from the
    # index (Pete, 2026-06-26), so a row under one is stale by rule even though the file is live.
    if "_backups" in (row.get("path") or "").split("/"):
        return "stale-policy", "under a _backups folder — excluded from the index by rule"
    m = getfile(fid)
    if m is None:
        return "unknown", "Drive lookup did not complete"
    if m == "gone":
        return "stale-gone", "no longer exists in Drive"
    if m.get("trashed"):
        return "stale-trashed", "in the Drive trash"
    did = m.get("driveId")
    if did:
        if did in set(SHARED.values()):
            return "in-scope", f"lives in {[k for k, v in SHARED.items() if v == did][0]} — an indexed drive"
        return "stale-scope", f"lives in shared drive {did}, which is not indexed"
    if not m.get("ownedByMe"):
        return "stale-scope", "not owned by Pete (a 'Shared with me' item) — not in My Drive scope"
    return "in-scope", "owned in My Drive — an indexed scope"


def upsert(rows):
    H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    done = 0
    for i in range(0, len(rows), 500):
        b = rows[i:i + 500]
        req = urllib.request.Request(f"{CCURL}/rest/v1/drive_files?on_conflict=drive_file_id", data=json.dumps(b).encode(), headers=H, method="POST")
        for a in range(5):
            try:
                urllib.request.urlopen(req, timeout=120); done += len(b); break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and a < 4: time.sleep(2 * (a + 1)); continue
                print("UPSERT ERR", e.code, e.read().decode()[:300], flush=True); break
            except Exception:
                if a < 4: time.sleep(2 * (a + 1)); continue
                raise
    return done

APPLY = "--apply" in sys.argv
STARTED = time.time()                      # epoch seconds; compared against parsed indexed_at

all_rows = []; failed = []
for name, did in SHARED.items():
    try:
        r = scan_shared(name, did); print(f"{name}: {len(r)} rows", flush=True); all_rows += r
    except Exception as e:
        print(f"{name}: SCAN FAILED {e}", flush=True); failed.append(name)
try:
    r = scan_mydrive(); print(f"My Drive: {len(r)} rows", flush=True); all_rows += r
except Exception as e:
    print(f"My Drive: SCAN FAILED {e}", flush=True); failed.append("My Drive")
print(f"TOTAL {len(all_rows)} rows -> upserting to CC drive_files", flush=True)
n = upsert(all_rows)
print(f"DONE: upserted {n} rows", flush=True)

# ---------------------------------------------------------------- prune pass
print("\n=== prune pass (rows in the index this scan did not see) ===", flush=True)
if failed:
    print(f"SKIPPED — {len(failed)} drive(s) failed to scan ({', '.join(failed)}). Pruning on a partial\n"
          f"          scan would delete those drives' whole index. Fix the scan and re-run.", flush=True)
    sys.exit(0)
if n != len(all_rows):
    print(f"SKIPPED — only {n} of {len(all_rows)} rows upserted, so some batches failed. The scan\n"
          f"          result is not fully written; not deleting anything off it. Re-run.", flush=True)
    sys.exit(0)

have = existing()
if have is None:
    print("SKIPPED — could not read the existing index in full (see error above). A partial read\n"
          "          would look like thousands of missing rows. Nothing deleted. Re-run.", flush=True)
    sys.exit(0)

seen = {r["drive_file_id"] for r in all_rows}
missing = [fid for fid in have if fid not in seen]
# The changes-watcher runs every 15 min. A file created mid-scan is inserted by it and was never
# in our scan — its indexed_at (FIRST-seen, never bumped by later upserts) postdates our start,
# which is exactly how we tell it apart from a genuinely stale row.
fresh = [fid for fid in missing if have[fid] >= STARTED]
cand = [fid for fid in missing if have[fid] < STARTED]

print(f"index holds {len(have)} rows · this scan saw {len(seen)} · candidates {len(cand)}"
      + (f" ({len(fresh)} first indexed after this scan started, left alone)" if fresh else ""), flush=True)

CAP = max(500, len(have) // 100)
if len(cand) > CAP:
    print(f"SKIPPED — {len(cand)} candidates exceeds the {CAP}-row sanity cap (1% of the index).\n"
          f"          That many rows going missing at once is a SCAN problem, not stale data.\n"
          f"          Nothing deleted. Investigate before forcing anything.", flush=True)
    sys.exit(0)

det = details(cand)
buckets = {}
for fid in sorted(cand, key=lambda f: (det.get(f, {}).get("path") or "")):
    row = det.get(fid) or {"path": f"(id {fid})", "drive": "?"}
    verdict, note = classify(fid, row)
    buckets.setdefault(verdict, []).append((fid, row, note))

LABEL = {"stale-gone": "gone from Drive", "stale-trashed": "in the Drive trash",
         "stale-scope": "outside the indexed scope", "stale-policy": "excluded by index policy",
         "in-scope": "STILL LIVE AND IN SCOPE — the scan missed these, NOT pruned",
         "unknown": "could not verify — left alone"}
for v in ("stale-gone", "stale-trashed", "stale-scope", "stale-policy", "in-scope", "unknown"):
    items = buckets.get(v) or []
    if not items: continue
    print(f"\n  {LABEL[v]}: {len(items)}", flush=True)
    for fid, row, note in items[:10]:
        print(f"    [{row.get('drive')}] {row.get('path')}\n        {note}", flush=True)
    if len(items) > 10:
        print(f"    ... +{len(items) - 10} more", flush=True)

stale = [fid for v in ("stale-gone", "stale-trashed", "stale-scope", "stale-policy")
         for fid, _r, _n in (buckets.get(v) or [])]
if not cand:
    print("  clean — every indexed row was seen by this scan.", flush=True)
elif not stale:
    print("\n  nothing to prune (no candidate verified as stale).", flush=True)
elif APPLY:
    if not snapshot(stale):
        print("\nSKIPPED — could not save the pre-delete snapshot to config `drive-files-prune-last`.\n"
              "          Nothing deleted: an unrecoverable prune is not worth it. Re-run.", flush=True)
        sys.exit(0)
    d = delete_ids(stale)
    print(f"\nPRUNED {d} of {len(stale)} verified-stale row(s)."
          f"\n  full rows saved first to config `drive-files-prune-last` (restorable).", flush=True)
    if d != len(stale):
        print("  some deletes failed (see errors above) — re-run to finish.", flush=True)
else:
    print(f"\n  {len(stale)} row(s) verified stale. Re-run with --apply to delete them.", flush=True)
if buckets.get("in-scope"):
    print(f"\n  !! {len(buckets['in-scope'])} live in-scope file(s) were absent from this scan — that is a\n"
          f"     scan gap, not stale data. They were NOT deleted. Worth investigating.", flush=True)
sys.exit(0)