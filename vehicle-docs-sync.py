#!/usr/bin/env python3
"""vehicle-docs-sync.py -- make the Sygma platform a view onto the vehicle documents in Drive.

Drive is the STORE: `Sygma Hub/Vehicles/<REG>/` holds V5s, agreements, settlement letters,
service invoices, photos. The platform is the VIEW: `hub.vehicle_documents` indexes what is
in each vehicle's folder so /hub/fleet can list and link it without holding a second copy.

Reads Drive LIVE (not the CC drive_files index) so a document filed a minute ago shows up
immediately rather than waiting for the 15-minute drive-changes-watch cron.

A folder only counts when its name exactly matches a `hub.fleet.vehicle_reg`. The
`On order - ...` folders are skipped by design: no reg yet, so no fleet row to hang off.

Finance paperwork is flagged `sensitive` so the table's RLS keeps it to owners + admins
(Pete, 27 Jul 2026) -- a driver can see their own V5 and service history, never the money.

Usage:
  VAULT=/tmp/pbs python3 vehicle-docs-sync.py            # dry run -- prints the plan
  VAULT=/tmp/pbs python3 vehicle-docs-sync.py --apply    # write it
"""
import os, sys, json, importlib.util, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PORTAL_REF = "rsczwfstwkthaybxhszy"
HUB_DRIVE = "Sygma Hub"
VEHICLES_FOLDER = "Vehicles"

_spec = importlib.util.spec_from_file_location("drive_api", os.path.join(VAULT, "drive-api.py"))
drive = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(drive)

SB_TOKEN = (os.environ.get("SUPABASE_TOKEN") or "").strip() or \
    open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()


def portal_q(sql):
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PORTAL_REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {SB_TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "curl/8.7.1"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"portal query failed {e.code}: {e.read().decode()}")


def q(s):
    """Single-quote escape for SQL literals."""
    return "null" if s is None else "'" + str(s).replace("'", "''") + "'"


# Filename -> (doc_type, sensitive). First match wins, so put the specific ones first.
# Anything naming money or an agreement is sensitive; the driver-facing documents are not.
RULES = [
    ("settlement", "settlement", True),
    ("agreement", "agreement", True),
    ("hire purchase", "agreement", True),
    ("finance", "agreement", True),
    ("lease", "agreement", True),
    ("invoice", "invoice", True),
    ("quotation", "quote", True),
    ("quote", "quote", True),
    ("proposal", "quote", True),
    ("v5", "v5", False),
    ("logbook", "v5", False),
    ("log book", "v5", False),
    ("mot", "mot", False),
    ("service", "service", False),
    ("insurance", "insurance", False),
    ("warranty", "warranty", False),
    ("handbook", "handbook", False),
]


def classify(name, mime):
    low = name.lower()
    for needle, doc_type, sensitive in RULES:
        if needle in low:
            return doc_type, sensitive
    if (mime or "").startswith("image/"):
        return "photo", False
    return "other", False


def children(folder_id):
    """Direct children of a folder (drive-api.ls prints rather than returns)."""
    out, token = [], None
    while True:
        params = {"q": f"'{folder_id}' in parents and trashed=false", "pageSize": 1000,
                  "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)",
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
                  "corpora": "allDrives"}
        if token:
            params["pageToken"] = token
        resp = drive.api("GET", "/files", params)
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return out


def walk(folder_id, prefix=""):
    """Every file under a folder, recursively, with its path relative to the vehicle folder."""
    files = []
    for f in children(folder_id):
        if "folder" in f.get("mimeType", ""):
            files.extend(walk(f["id"], f"{prefix}{f['name']}/"))
        else:
            f["_relpath"] = prefix + f["name"]
            files.append(f)
    return files


def main():
    apply = "--apply" in sys.argv

    # The Vehicles folder inside the Sygma Hub drive.
    drives = drive.api("GET", "/drives", {"pageSize": 100})["drives"]
    hub = next((d for d in drives if d["name"] == HUB_DRIVE), None)
    if not hub:
        sys.exit(f"drive '{HUB_DRIVE}' not found")
    root = next((c for c in children(hub["id"])
                 if c["name"] == VEHICLES_FOLDER and "folder" in c["mimeType"]), None)
    if not root:
        sys.exit(f"'{VEHICLES_FOLDER}' folder not found in {HUB_DRIVE}")

    regs = {r["vehicle_reg"] for r in portal_q("SELECT vehicle_reg FROM hub.fleet")}

    found, skipped = {}, []
    for c in children(root["id"]):
        if "folder" not in c.get("mimeType", ""):
            continue
        if c["name"] not in regs:
            skipped.append(c["name"])
            continue
        found[c["name"]] = walk(c["id"])

    rows, seen_ids = [], []
    for reg, files in sorted(found.items()):
        for f in files:
            doc_type, sensitive = classify(f["_relpath"], f.get("mimeType"))
            seen_ids.append(f["id"])
            rows.append((reg, f["id"], f["_relpath"], f"Vehicles/{reg}/{f['_relpath']}",
                         f.get("mimeType"), f.get("size"), f.get("modifiedTime"),
                         doc_type, sensitive))

    print(f"vehicle-docs-sync: {len(found)} vehicle folder(s), {len(rows)} document(s)")
    for reg, files in sorted(found.items()):
        print(f"  {reg}: {len(files)} file(s)")
        for f in files:
            dt, sens = classify(f["_relpath"], f.get("mimeType"))
            print(f"      {f['_relpath']}  [{dt}{' SENSITIVE' if sens else ''}]")
    if skipped:
        print(f"  skipped (no matching hub.fleet reg): {', '.join(sorted(skipped))}")

    if not apply:
        print("\ndry run -- pass --apply to write")
        return

    if rows:
        values = ",".join(
            "(" + ",".join([q(r[0]), q(r[1]), q(r[2]), q(r[3]), q(r[4]),
                            "null" if r[5] is None else str(int(r[5])),
                            q(r[6]), q(r[7]), "true" if r[8] else "false"]) + ")"
            for r in rows)
        portal_q(f"""
            INSERT INTO hub.vehicle_documents
              (vehicle_reg, drive_file_id, name, drive_path, mime, size_bytes,
               modified_time, doc_type, sensitive)
            VALUES {values}
            ON CONFLICT (drive_file_id) DO UPDATE SET
              vehicle_reg=excluded.vehicle_reg, name=excluded.name,
              drive_path=excluded.drive_path, mime=excluded.mime,
              size_bytes=excluded.size_bytes, modified_time=excluded.modified_time,
              doc_type=excluded.doc_type, sensitive=excluded.sensitive,
              synced_at=now()
        """)

    # Drop anything no longer in Drive, so a deleted/moved document leaves the platform too.
    keep = ",".join(q(i) for i in seen_ids) if seen_ids else "''"
    gone = portal_q(f"DELETE FROM hub.vehicle_documents WHERE drive_file_id NOT IN ({keep}) "
                    f"RETURNING vehicle_reg, name")
    total = portal_q("SELECT count(*) AS n FROM hub.vehicle_documents")[0]["n"]
    print(f"\napplied: {len(rows)} upserted, {len(gone)} removed, {total} indexed in total")
    for g in gone:
        print(f"  removed: {g['vehicle_reg']} / {g['name']}")


if __name__ == "__main__":
    main()
