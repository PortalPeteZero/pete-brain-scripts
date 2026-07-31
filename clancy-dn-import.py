#!/usr/bin/env python3
"""clancy-dn-import.py — import Clancy Depotnet Incident Manager exports into the CC.

WHY: the CC's DepotNet Damages section (public.clancy_dn_incidents + public.clancy_dn_actions)
mirrors Depotnet's Incident Manager, which is the SSOT for every Clancy service damage group-wide.
Pete re-exports the two sheets from Depotnet whenever he wants a refresh; this importer upserts
them idempotently on Depotnet's own IDs and reports exactly what changed. It NEVER touches any
Sygma-added enrichment — those live outside these two tables.

The two exports (Depotnet ▸ Incident Manager):
  "Incident Register.xlsx"  — one row per service damage (the register)
  "Action Report.xlsx"      — one row per corrective action, FK Incident ID

Derivations added on import (kept out of Depotnet's own columns):
  fy               — UK financial year of Incident Date, "FY25/26" shape (year starts 1 Apr)
  contract_family  — display grouping of Depotnet's 25 contract names (e.g. all "Southern Water*"
                     variants → "Southern Water"); the raw name is always kept in `contract`
  utility_class    — auto-read from Description (gas / electric / water / comms...); rows carry
                     utility_confirmed=false until a human confirms — auto class is a best guess.
                     Import NEVER overwrites a row where utility_confirmed=true.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-import.py --register "~/Downloads/Incident Register.xlsx" \
      --actions "~/Downloads/Action Report.xlsx" [--dry-run]

Requires: openpyxl. Auth: the CC service key (command-centre-supabase-keys.json), same as cc-park.
"""
import os, sys, json, re, argparse, datetime, urllib.request

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

# ---- derivations ----------------------------------------------------------

def fy_of(d):
    if not isinstance(d, datetime.datetime):
        return None
    y = d.year if d.month >= 4 else d.year - 1
    return f"FY{y % 100:02d}/{(y + 1) % 100:02d}"

FAMILY_RULES = [
    (r"^Southern Water", "Southern Water"),
    (r"^Anglian Water", "Anglian Water"),
    (r"^SE Water", "South East Water"),
    (r"^Scottish Water", "Scottish Water"),
    (r"^UKPN", "UKPN"),
    (r"^SGN", "SGN"),
    (r"^Sutton East Surrey", "Sutton & East Surrey"),
    (r"^SEPD", "SEPD"),
    (r"^South West Water", "South West Water"),
    (r"^TW ", "Thames Water"),
    (r"^SSE$", "SSE"),
    (r"^HS2", "HS2"),
]

def family_of(contract):
    c = (contract or "").strip()
    for pat, fam in FAMILY_RULES:
        if re.match(pat, c, re.I):
            return fam
    return c or "Unstated"

# Order matters: first match wins. Most specific first.
UTILITY_RULES = [
    ("Electric — street lighting", r"street ?-?light|lamp ?(post|column)|lighting (cable|column)"),
    ("Electric — HV", r"\bHV\b|high voltage|11 ?kv|33 ?kv"),
    ("Gas", r"\bgas\b|\bPE main\b|\btop tee\b|poly service|\b(63|32|25) ?mm poly\b|\bMP\b service|\bLP\b service"),
    ("Comms / fibre", r"\bBT\b|open ?reach|virgin|gigaclear|cityfibre|fibre|fiber|\bcomms\b|telecom|\bCCTV\b|zayo|vodafone duct"),
    ("Electric — LV / service", r"\bLV\b|low voltage|electric|\bSWA\b|armoured|cable strike|service cable|cut ?-?out|\bUKPN\b|\bSSEN?\b cable|\bSSE cable\b|damaged cable|struck.*cable|cable.*struck|cable.*damag|hit.*cable"),
    ("Water", r"water (main|pipe|service)|\bMDPE\b|ferr(ule|ell?)|\bwater\b|hydrant|\bwashout\b"),
    ("Sewer / drainage", r"sewer|\bdrain(age)?\b|\bfoul\b|lateral"),
]

def classify_utility(desc):
    d = str(desc or "").lower()
    if not d.strip():
        return "Unclassified", None
    for name, pat in UTILITY_RULES:
        m = re.search(pat, d, re.I)
        if m:
            return name, m.group(0)[:40]
    return "Unclassified", None

# ---- xlsx reading ---------------------------------------------------------

def read_sheet(path):
    from openpyxl import load_workbook
    wb = load_workbook(os.path.expanduser(path), data_only=True)
    ws = wb.active
    hdr = [c.value for c in ws[1]]
    return [dict(zip(hdr, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]

def iso(v):
    return v.isoformat() if isinstance(v, datetime.datetime) else None

def s(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None

# ---- main -----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", required=True)
    ap.add_argument("--actions", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    reg = read_sheet(a.register)
    act = read_sheet(a.actions)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # current DB state for change detection + utility_confirmed protection
    existing = {r["id"]: r for r in rest("clancy_dn_incidents?select=*&limit=10000")}
    existing_act = {r["id"]: r for r in rest("clancy_dn_actions?select=*&limit=10000")}

    DN_COLS = ["date_raised", "incident_date", "category", "subcategory", "raised_by", "job_id",
               "job_ref", "contract", "contract_number", "workstream", "business_unit", "location",
               "severity", "subcontractor", "description", "status"]

    inc_rows, added, changed = [], 0, 0
    for r in reg:
        rid = int(r["ID"])
        row = {
            "id": rid,
            "date_raised": iso(r["Date Raised"]), "incident_date": iso(r["Incident Date"]),
            "category": s(r["Category"]), "subcategory": s(r["Subcategory"]),
            "raised_by": s(r["Raised By"]), "job_id": s(r["Job ID"]), "job_ref": s(r["Job Ref"]),
            "contract": s(r["Contract"]), "contract_number": s(r["Contract Number"]),
            "workstream": s(r["Workstream"]), "business_unit": s(r["Business Unit"]),
            "location": s(r["Location"]), "severity": s(r["Severity"]),
            "subcontractor": s(r["Subcontractor"]), "description": s(r["Description"]),
            "status": s(r["Status"]),
            "fy": fy_of(r["Incident Date"]),
            "contract_family": family_of(s(r["Contract"])),
            "last_import": now,
        }
        old = existing.get(rid)
        if old is None:
            added += 1
            row["import_changed_at"] = now
            uc, kw = classify_utility(row["description"])
            row["utility_class"], row["utility_keyword"] = uc, kw
        else:
            # normalise old timestamps for comparison
            def norm(v):
                return str(v).replace(" ", "T")[:19] if v else None
            diff = [c for c in DN_COLS
                    if (norm(old.get(c)) if c.endswith("date") or c.endswith("raised") else old.get(c))
                    != (norm(row.get(c)) if c.endswith("date") or c.endswith("raised") else row.get(c))]
            if diff:
                changed += 1
                row["import_changed_at"] = now
            if old.get("utility_confirmed"):
                row["utility_class"] = old["utility_class"]
                row["utility_keyword"] = old.get("utility_keyword")
                row["utility_confirmed"] = True
            else:
                uc, kw = classify_utility(row["description"])
                row["utility_class"], row["utility_keyword"] = uc, kw
        inc_rows.append(row)

    act_rows, a_added, a_changed = [], 0, 0
    ACT_COLS = ["incident_id", "job_id", "job_ref", "date_raised", "raised_by", "incident_date",
                "due_date", "category", "severity", "action_classification",
                "action_subclassification", "assigned_to", "contract", "contract_number",
                "workstream", "business_unit", "subcontractor", "question", "description",
                "status", "incident_status", "corrective_measure"]
    for r in act:
        rid = int(r["ID"])
        row = {
            "id": rid, "incident_id": int(r["Incident ID"]) if r["Incident ID"] else None,
            "job_id": s(r["Job ID"]), "job_ref": s(r["Job Ref"]),
            "date_raised": iso(r["Date Raised"]), "raised_by": s(r["Raised By"]),
            "incident_date": iso(r["Incident Date"]), "due_date": iso(r["Due Date"]),
            "category": s(r["Category"]), "severity": s(r["Severity"]),
            "action_classification": s(r["Action Classification"]),
            "action_subclassification": s(r["Action Subclassification"]),
            "assigned_to": s(r["Assigned To"]), "contract": s(r["Contract"]),
            "contract_family": family_of(s(r["Contract"])),
            "contract_number": s(r["Contract Number"]), "workstream": s(r["Workstream"]),
            "business_unit": s(r["Business Unit"]), "subcontractor": s(r["Subcontractor"]),
            "question": s(r["Question"]), "description": s(r["Description"]),
            "status": s(r["Status"]), "incident_status": s(r["Incident Status"]),
            "corrective_measure": s(r["Corrective Measure"]),
            "last_import": now,
        }
        old = existing_act.get(rid)
        if old is None:
            a_added += 1
            row["import_changed_at"] = now
        else:
            def norm(v):
                return str(v).replace(" ", "T")[:19] if v else None
            diff = [c for c in ACT_COLS
                    if (norm(old.get(c)) if ("date" in c or c == "date_raised") else old.get(c))
                    != (norm(row.get(c)) if ("date" in c or c == "date_raised") else row.get(c))]
            if diff:
                a_changed += 1
                row["import_changed_at"] = now
        act_rows.append(row)

    print(f"register: {len(inc_rows)} rows -> +{added} new, ~{changed} changed, "
          f"{len(inc_rows)-added-changed} unchanged")
    print(f"actions:  {len(act_rows)} rows -> +{a_added} new, ~{a_changed} changed, "
          f"{len(act_rows)-a_added-a_changed} unchanged")
    gone = set(existing) - {r['id'] for r in inc_rows}
    gone_a = set(existing_act) - {r['id'] for r in act_rows}
    if gone:
        print(f"NOTE: {len(gone)} incident(s) in the CC are NOT in this export (kept, never deleted): {sorted(gone)[:10]}")
    if gone_a:
        print(f"NOTE: {len(gone_a)} action(s) in the CC are NOT in this export (kept): {sorted(gone_a)[:10]}")
    if a.dry_run:
        print("dry-run: nothing written")
        return

    H = {"Prefer": "resolution=merge-duplicates"}
    for i in range(0, len(inc_rows), 200):
        rest("clancy_dn_incidents", "POST", inc_rows[i:i+200], H)
    for i in range(0, len(act_rows), 200):
        rest("clancy_dn_actions", "POST", act_rows[i:i+200], H)
    print("written.")

if __name__ == "__main__":
    main()
