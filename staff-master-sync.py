import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")
#!/usr/bin/env python3
"""
staff-master-sync.py -- Sygma Staff Master nightly sync (registered cron, 05:30 UK).

Status: LIVE. Feeds the vault person.md + the trainer roster. The platform (hub.staff_directory /
hub.staff_hr) is the SOURCE OF TRUTH — the inbound hub-load (staff-hub-load.py) was RETIRED 2026-06-10,
and the legacy Vercel staff-dashboard JSON caches were RETIRED 2026-06-19 (dead, no readers).

Architecture (per [[Library/processes/staff-data-routing]]):

  Hub Staff Master Google Sheet (operational, anyone with Hub access)
      ID: 1o04hBPhGzyyD3q2kHusLG5cHgAIOfsD0v2zajoEgtf8
      URL: https://docs.google.com/spreadsheets/d/1o04hBPhGzyyD3q2kHusLG5cHgAIOfsD0v2zajoEgtf8/edit
      Path: Sygma Hub / HR / Staff Master
      Tabs: Directory / 2024 / 2025 / 2026 / 2027 / Fleet / Subcontractors / Leavers / Org Chart

  ↓ this cron, 05:30 UK daily

  1. Library/sy-hr/Staff Master.json (single document, all tabs)
  2. Businesses/sygma-solutions/people/{kebab-name}.md (regen frontmatter only, preserve body)
  3. Library/processes/sygma-trainer-roster.yaml (regen rows where sub_business == "Sygma Training")
  4. (RETIRED 2026-06-19) Properties/Sygma Solutions Website/data/staff/*.json — standalone Vercel
     dashboard is dead + nothing reads these; no longer emitted.
  5. (RETIRED 2026-06-10) hub.staff_directory + hub.staff_hr via staff-hub-load.py — the platform is
     now the source of truth (edits at sygmaportal.com/hub/directory); inbound load disabled.
  6. Today's daily note line under "## Staff master sync (Automated)"
  7. P2 Asana task in SY-General if 2 consecutive runs fail

Diff surfacing rules (vs yesterday's JSON):
  - new starter (employment_status: Pre-Start or Active appeared)
  - leaver (employment_status: Leaver appeared)
  - google_calendar_id changed
  - vehicle_reg changed (driver change on a vehicle)
  - home_address changed (for cron-pickup of Sue's edits)

Companion `staff-master-vault-write.py` (manual, not this script) pushes vault edits up to the Sheet.

DONE (2026-06): registered as the nightly cron; Directory / Fleet / Subcontractors populated; the
Hub staff list (Phase 4 of the Portal merge) now reads hub.staff_directory + hub.staff_hr, fed by
Step 5 above. The standalone Vercel staff dashboard (sygma-staff.vercel.app) is superseded by the
Hub. Payroll is NEVER loaded here — the loader reads only the operational Directory tab.

Execution: use Desktop Commander start_process with nohup + log file polling (Bash sandbox has 45s cap).
"""

import json, os, sys, time, datetime, importlib.util, urllib.request, urllib.parse, urllib.error
import subprocess, tempfile
import yaml  # PyYAML — also used by jotform-normalise.py

# === Config (locked) ===

VAULT = VAULT
HUB_STAFF_MASTER_ID = "1o04hBPhGzyyD3q2kHusLG5cHgAIOfsD0v2zajoEgtf8"
CACHE_JSON = f"{VAULT}/Library/sy-hr/Staff Master.json"
PEOPLE_DIR = f"{VAULT}/Businesses/sygma-solutions/people"
ROSTER_YAML = f"{VAULT}/Library/processes/sygma-trainer-roster.yaml"
ALIASES_YAML = f"{VAULT}/Library/processes/sygma-trainer-aliases.yaml"   # curated, NEVER auto-generated
DASHBOARD_DATA = f"{VAULT}/Properties/Sygma Solutions Website/data/staff"   # Phase 11 destination
DAILY_DIR = f"{VAULT}/Daily"

# Asana failure-task placeholder (set during Phase 8 cron-registration step)
SY_STAFF_PROJECT_GID = "1215314810136091"
SY_STAFF_PHASE8_SECTION_GID = "1215304089121362"
ASANA_PAT_PATH = f"{VAULT}/Library/processes/secrets/asana-pat"
PETE_USER_GID = "1213947679900718"
ASANA_WS = "1213947679900731"
PRI_FIELD = "1213945150508559"
P2 = "1213945150508561"

# === Helpers ===

def kebab(name: str) -> str:
    out = []
    for c in name.strip():
        if c.isalnum(): out.append(c.lower())
        elif c in " -_": out.append("-")
    s = "".join(out)
    while "--" in s: s = s.replace("--", "-")
    return s.strip("-")

def load_helper(module_name):
    spec = importlib.util.spec_from_file_location(module_name, f"{VAULT}/Library/processes/scripts/{module_name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

# === Header alias map ===
# Sheet uses friendly Title Case headers; code reads snake_case keys.
# Map sheet header → snake_case canonical key. Unknown headers pass through unchanged.
HEADER_ALIAS = {
    "Ref #": "employee_ref", "Full Name": "full_name", "Preferred Name": "preferred_name",
    "Title": "honorific", "DOB": "dob", "Gender": "gender", "NI Number": "ni_number",
    "Start Date": "start_date", "End Date": "end_date", "Status": "employment_status",
    "Contract": "contract_type", "Role": "job_title", "Role / Job Title": "job_title",
    "Sub-business": "sub_business", "Reports To": "reports_to",
    "Work Email": "work_email", "Work Mobile": "work_mobile",
    "Personal Email": "personal_email", "Personal Mobile": "personal_mobile",
    "Home Address": "home_address",
    "Emergency: Name": "emergency_contact_name",
    "Emergency: Phone": "emergency_contact_phone",
    "Emergency: Relationship": "emergency_contact_relationship",
    "Vehicle (Make + Model)": "vehicle_make_model", "Make + Model": "make_model",
    "Vehicle Reg": "vehicle_reg",
    "Holiday Entitlement": "holiday_entitlement_days",
    "Hols Taken YTD": "holidays_taken_ytd", "Hols Remaining": "holidays_remaining",
    "Sick Days YTD": "sick_days_ytd", "Appts YTD": "appointments_ytd",
    "RTW Verified": "right_to_work_verified",
    "Driver Policy Signed": "driver_policy_signed_date",
    "Last Appraisal": "last_appraisal_date",
    "Key Qualifications": "key_qualifications",
    "Soldo Card?": "soldo_card_active", "Notes": "notes",
    "Google Cal ID": "google_calendar_id", "JotForm Name": "jotform_canonical_name",
    "Asana User GID": "asana_user_gid",
    "Xero ID": "xero_employee_id", "Odoo ID": "odoo_employee_id",
    "Soldo Ref": "soldo_cardholder_ref", "Garmin ID": "garmin_athlete_id",
    "Hub Folder": "hub_staff_folder_url", "Vault MD": "vault_person_md_url",
    "Payroll Row": "payroll_master_row_ref",
    # Fleet
    "Category": "category", "Owned / Leased": "owned_or_leased",
    "Current Driver": "current_driver", "Lease End": "lease_end", "MOT Due": "mot_due",
    "Last Mileage": "last_mileage", "Mileage Date": "mileage_date",
    "Previous Drivers": "previous_drivers",
    # Leavers
    "Leave Reason": "leave_reason", "Replaced By": "replaced_by", "Final Role": "final_role",
    # Org Chart
    "Level": "level", "Name": "name",
}

def canonical_key(header):
    """Map sheet header to snake_case key. Passes through if not in alias map."""
    return HEADER_ALIAS.get(header, header)

# === Step 1: pull the Sheet ===

import re
_HYPERLINK_RE = re.compile(r'^=HYPERLINK\("([^"]+)"\s*,\s*"[^"]*"\s*\)\s*$')

def _unwrap_hyperlink(v):
    """If v is a Google Sheets HYPERLINK formula, return the URL; else return v unchanged."""
    if isinstance(v, str):
        m = _HYPERLINK_RE.match(v)
        if m: return m.group(1)
    return v

def _fetch_values(tok, tab, render):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{HUB_STAFF_MASTER_ID}"
           f"/values/{urllib.parse.quote(tab, safe='')}?valueRenderOption={render}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    return json.load(urllib.request.urlopen(req)).get("values", [])


def pull_sheet():
    """Read all tabs of the Hub Staff Master into a single dict, normalising headers.

    Each cell uses the FORMATTED (display-ready) value, EXCEPT where the underlying cell is a
    HYPERLINK() formula — there we unwrap the formula to its URL. This is why we pull twice per
    tab: FORMATTED_VALUE gives computed values (the holiday/sick SUMIFS columns) and display
    dates (DOB / start / appraisal render as strings, not Google's serial numbers), while FORMULA
    lets us spot + unwrap HYPERLINK cells (Drive folder + vault links). Pulling FORMULA alone
    leaked SUMIFS formula strings and date serials into the cache — fixed 2026-06-08.
    """
    sh = load_helper("sheets-api")
    tok = sh.get_token()
    meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{HUB_STAFF_MASTER_ID}?fields=sheets.properties"
    req = urllib.request.Request(meta_url, headers={"Authorization": f"Bearer {tok}"})
    meta = json.load(urllib.request.urlopen(req))
    tabs = [s["properties"]["title"] for s in meta["sheets"]]
    out = {"_pulled_at": datetime.datetime.utcnow().isoformat() + "Z", "sheet_id": HUB_STAFF_MASTER_ID, "tabs": {}}
    for tab in tabs:
        fmt = _fetch_values(tok, tab, "FORMATTED_VALUE")   # computed + display-ready
        formula = _fetch_values(tok, tab, "FORMULA")        # to detect + unwrap HYPERLINK()
        if not fmt:
            out["tabs"][tab] = []
            continue
        headers = [canonical_key(h) for h in fmt[0]]
        rows = []
        for ri in range(1, len(fmt)):
            frow = fmt[ri]
            qrow = formula[ri] if ri < len(formula) else []
            row = {}
            for ci, h in enumerate(headers):
                fval = frow[ci] if ci < len(frow) else ""
                qval = qrow[ci] if ci < len(qrow) else ""
                url = _unwrap_hyperlink(qval)
                row[h] = url if url != qval else fval   # HYPERLINK -> URL, else formatted value
            rows.append(row)
        out["tabs"][tab] = rows
    return out

# === Step 2: cache the JSON ===

def write_cache(data):
    os.makedirs(os.path.dirname(CACHE_JSON), exist_ok=True)
    with open(CACHE_JSON, "w") as f:
        json.dump(data, f, indent=2)

# === Step 3: regen vault person.md frontmatter ===

def regen_people(directory_rows):
    """For each row in Directory, ensure a person.md exists; regenerate the frontmatter only."""
    os.makedirs(PEOPLE_DIR, exist_ok=True)
    touched = []
    for row in directory_rows:
        name = row.get("full_name", "").strip()
        if not name: continue
        slug = kebab(name)
        path = f"{PEOPLE_DIR}/{slug}.md"
        new_fm = {
            "type": "person",
            "name": name,
            "preferred_name": row.get("preferred_name", "") or name,
            "employee_ref": row.get("employee_ref", ""),
            "employment_status": row.get("employment_status", ""),
            "sub_business": row.get("sub_business", ""),
            "job_title": row.get("job_title", ""),
            "reports_to": row.get("reports_to", ""),
            "work_email": row.get("work_email", ""),
            "google_calendar_id": row.get("google_calendar_id", ""),
            "asana_user_gid": row.get("asana_user_gid", ""),
            "jotform_canonical_name": row.get("jotform_canonical_name", ""),
            "soldo_cardholder_ref": row.get("soldo_cardholder_ref", ""),
            "hub_staff_folder": row.get("hub_staff_folder_url", ""),
            "hub_master_row": row.get("employee_ref", ""),
            "vault_md_path": f"Businesses/sygma-solutions/people/{slug}.md",
            "updated": datetime.date.today().isoformat(),
            "tags": ["person", "sygma", row.get("employment_status", "").lower()],
        }
        body = ""
        if os.path.exists(path):
            existing = open(path).read()
            # Strip existing frontmatter
            if existing.startswith("---"):
                end = existing.find("\n---", 4)
                if end != -1: body = existing[end + 5:]
                else: body = existing
            else: body = existing
        else:
            body = f"\n# {name}\n\n[One-paragraph summary of role, scope, notable things — fill in.]\n\n## Where everything is\n\n- Hub operational paperwork: `Sygma Hub/HR/Staff/Active/{name}/`\n- Pete & Mic private paperwork: `Pete & Mic / Sygma Solutions Private / Personnel / Staff / Active / {name}/`\n- Payroll row: `Payroll Master` row {row.get('employee_ref','')} (owner-only)\n\n## Notes\n\n[Anything Claude needs to know beyond the structured data.]\n"
        # Write
        fm_lines = ["---"]
        for k, v in new_fm.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}: [{', '.join(v)}]")
            else:
                fm_lines.append(f'{k}: "{v}"' if (isinstance(v, str) and (":" in v or v.strip() == "")) else f"{k}: {v}")
        fm_lines.append("---")
        new = "\n".join(fm_lines) + "\n" + body.lstrip("\n")
        with open(path, "w") as f: f.write(new)
        touched.append(slug)
    return touched

# === Step 4: regen sygma-trainer-roster.yaml ===

def regen_roster(directory_rows):
    """Write sygma-trainer-roster.yaml from Sygma Training rows, MERGING the
    curated alias sidecar (sygma-trainer-aliases.yaml).

    The sheet carries canonical/preferred/calendar/status; the sidecar carries
    the free-text `aliases` + `bare_aliases` per trainer plus the top-level
    `multi_trainer_separators` / `ambiguous_bare` that jotform-normalise.py needs
    to canonicalise free-text trainer names. The sidecar is hand-curated and is
    NEVER auto-generated, so roster regen can no longer wipe the aliases.
    (Fix for the 2026-06-08 silent-wipe: the old regen emitted only the 5 sheet
    fields and clobbered the in-roster aliases, zeroing trainer attribution.)"""
    rows = [r for r in directory_rows if r.get("sub_business", "").strip() == "Sygma Training"]
    sidecar = {}
    if os.path.exists(ALIASES_YAML):
        try:
            sidecar = yaml.safe_load(open(ALIASES_YAML).read()) or {}
        except Exception as e:
            print(f"  WARN: could not read alias sidecar ({e}); roster will have no aliases")
    talias = sidecar.get("trainer_aliases", {}) or {}
    trainers = []
    for t in rows:
        canonical = t.get("jotform_canonical_name") or t.get("full_name", "")
        entry = {
            "canonical": canonical,
            "full_name": t.get("full_name", ""),
            "preferred_name": t.get("preferred_name", ""),
            "google_calendar_id": t.get("google_calendar_id", ""),
            "employment_status": t.get("employment_status", ""),
        }
        a = talias.get(canonical) or talias.get(t.get("full_name", "")) or {}
        if a.get("aliases"): entry["aliases"] = list(a["aliases"])
        if a.get("bare_aliases"): entry["bare_aliases"] = list(a["bare_aliases"])
        trainers.append(entry)
    roster = {"trainers": trainers}
    if sidecar.get("multi_trainer_separators"):
        roster["multi_trainer_separators"] = list(sidecar["multi_trainer_separators"])
    if sidecar.get("ambiguous_bare"):
        roster["ambiguous_bare"] = sidecar["ambiguous_bare"]
    header = ("# Auto-regenerated by staff-master-sync.py — DO NOT EDIT BY HAND\n"
              f"# Last run: {datetime.datetime.utcnow().isoformat()}Z\n"
              "# Sheet fields from Hub Staff Master Directory MERGED with curated\n"
              "# aliases from sygma-trainer-aliases.yaml — edit THAT file, not this one.\n")
    os.makedirs(os.path.dirname(ROSTER_YAML), exist_ok=True)
    with open(ROSTER_YAML, "w") as f:
        f.write(header)
        yaml.safe_dump(roster, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return len(trainers)

# === Step 5: Phase 11 dashboard JSON caches — RETIRED 2026-06-19 ===
# The standalone Vercel staff dashboard (sygma-staff.vercel.app) is dead and nothing reads these
# JSONs. Function kept for a one-off manual re-seed only; the nightly sync no longer calls it.

def emit_dashboard_caches(data):
    os.makedirs(DASHBOARD_DATA, exist_ok=True)
    with open(f"{DASHBOARD_DATA}/directory.json", "w") as f:
        json.dump(data["tabs"].get("Directory", []), f, indent=2)
    with open(f"{DASHBOARD_DATA}/fleet.json", "w") as f:
        json.dump(data["tabs"].get("Fleet", []), f, indent=2)
    with open(f"{DASHBOARD_DATA}/subcontractors.json", "w") as f:
        json.dump(data["tabs"].get("Subcontractors", []), f, indent=2)
    with open(f"{DASHBOARD_DATA}/leavers.json", "w") as f:
        json.dump(data["tabs"].get("Leavers", []), f, indent=2)
    for y in ["2024","2025","2026","2027"]:
        with open(f"{DASHBOARD_DATA}/leave-{y}.json", "w") as f:
            json.dump(data["tabs"].get(y, []), f, indent=2)

# === Step 6: surface diffs vs yesterday into the daily note ===

def diff_and_log(today_data, prev_path, hub_status=""):
    today_dir = {r.get("full_name"): r for r in today_data["tabs"].get("Directory", []) if r.get("full_name")}
    prev_dir = {}
    if prev_path and os.path.exists(prev_path):
        try:
            prev = json.load(open(prev_path))
            prev_dir = {r.get("full_name"): r for r in prev.get("tabs", {}).get("Directory", []) if r.get("full_name")}
        except Exception: pass

    diffs = []
    for name, row in today_dir.items():
        if name not in prev_dir:
            diffs.append(f"NEW: {name} ({row.get('employment_status','')})")
            continue
        for field in ["employment_status", "google_calendar_id", "vehicle_reg", "home_address"]:
            if row.get(field, "") != prev_dir[name].get(field, ""):
                diffs.append(f"CHANGED {name}.{field}: '{prev_dir[name].get(field,'')}' → '{row.get(field,'')}'")
    for name in prev_dir:
        if name not in today_dir:
            diffs.append(f"REMOVED: {name}")

    today = datetime.date.today().isoformat()
    daily_path = f"{DAILY_DIR}/{today}.md"
    line = f"\n## Staff master sync (Automated)\n- Run at {datetime.datetime.utcnow().isoformat()}Z\n- Directory rows: {len(today_dir)} | Diffs since yesterday: {len(diffs)}\n"
    for d in diffs[:10]:
        line += f"  - {d}\n"
    if len(diffs) > 10:
        line += f"  - (+{len(diffs)-10} more — see Library/sy-hr/Staff Master.json)\n"
    if hub_status:
        line += f"- Hub load: {hub_status}\n"
    os.makedirs(DAILY_DIR, exist_ok=True)
    if os.path.exists(daily_path):
        with open(daily_path, "a") as f: f.write(line)
    else:
        header = f"---\ntype: daily\ndate: {today}\ntags: [daily]\n---\n\n# Daily {today}\n"
        with open(daily_path, "w") as f: f.write(header + line)
    return diffs

# === Step 7: failure handling ===

def raise_p2(reason):
    """Raise a P2 Asana task if 2 consecutive failures."""
    try:
        pat = open(ASANA_PAT_PATH).read().strip()
        notes = f"staff-master-sync.py failed: {reason}\n\nCheck Library/sy-hr/Staff Master.json freshness and the Hub Sheet permissions."
        body = {"data": {"workspace": ASANA_WS, "name": f"staff-master-sync.py FAILED — {datetime.date.today().isoformat()}",
                "projects": [SY_STAFF_PROJECT_GID], "assignee": PETE_USER_GID, "notes": notes,
                "custom_fields": {PRI_FIELD: P2}}}
        req = urllib.request.Request("https://app.asana.com/api/1.0/tasks", method="POST",
            headers={"Authorization": f"Bearer {pat}", "Content-Type": "application/json"},
            data=json.dumps(body).encode())
        r = json.load(urllib.request.urlopen(req))
        gid = r["data"]["gid"]
        # Move to Phase 8 section
        body2 = {"data": {"task": gid}}
        req2 = urllib.request.Request(f"https://app.asana.com/api/1.0/sections/{SY_STAFF_PHASE8_SECTION_GID}/addTask",
            method="POST", headers={"Authorization": f"Bearer {pat}", "Content-Type": "application/json"},
            data=json.dumps(body2).encode())
        urllib.request.urlopen(req2)
    except Exception as e:
        sys.stderr.write(f"P2 raise itself failed: {e}\n")

# === main ===

def main():
    try:
        # Pre-stash yesterday's cache for diffing
        prev_path = CACHE_JSON.replace(".json", ".prev.json")
        if os.path.exists(CACHE_JSON):
            try:
                with open(CACHE_JSON) as f, open(prev_path, "w") as g:
                    g.write(f.read())
            except Exception: pass

        data = pull_sheet()
        write_cache(data)

        directory = data["tabs"].get("Directory", [])
        subs = data["tabs"].get("Subcontractors", [])
        # Subcontractors don't have employee_ref but need person.md frontmatter too.
        # Synthesise minimal Directory-shaped rows so regen_people writes their frontmatter.
        sub_rows = []
        for s in subs:
            sub_rows.append({
                "full_name": s.get("full_name", ""),
                "preferred_name": s.get("preferred_name", ""),
                "employee_ref": "",  # subs don't have one
                "employment_status": "Subcontractor",
                "sub_business": s.get("sub_business", ""),
                "job_title": s.get("job_title", ""),
                "reports_to": "",
                "work_email": s.get("work_email", ""),
                "google_calendar_id": s.get("work_email", "") if "@sygma-solutions.com" in s.get("work_email","") else "",
                "asana_user_gid": "",
                "jotform_canonical_name": s.get("full_name", ""),
                "soldo_cardholder_ref": "",
                "hub_staff_folder_url": "",
            })
        touched_people = regen_people(directory + sub_rows)
        trainer_count = regen_roster(directory)
        # emit_dashboard_caches(data)  # RETIRED 2026-06-19 — dead Vercel dashboard, no readers

        # Step 8: RETIRED 2026-06-10 — the Portal's internal section is now the SOURCE OF TRUTH for
        # hub.staff_directory + hub.staff_hr (staff are added/edited at sygmaportal.com/hub/directory,
        # + holidays in hub.staff_leave). The inbound sheet→hub load is DISABLED so platform edits are
        # not clobbered nightly. The sheet stays downstream for payroll; the vault person.md, roster +
        # dashboard caches written above are unaffected. To re-seed manually (rare), run
        # staff-hub-load.py by hand. See staff-cms-plan-2026-06-09.md (Phase 4).
        hub_status = "skipped — platform is source of truth (inbound sheet→hub load retired 2026-06-10)"

        diffs = diff_and_log(data, prev_path, hub_status)

        print(f"staff-master-sync OK: Directory={len(directory)} rows, "
              f"people.md touched={len(touched_people)}, roster trainers={trainer_count}, "
              f"diffs={len(diffs)} | Hub: {hub_status}")
        # The Command Centre staff-directory module was REMOVED on 2026-06-14 — staff live on the
        # Sygma Platform (sygmaportal.com/hub/directory, the source of truth), so the CC mirror is
        # retired. This sync still regenerates the vault person.md cards from the Staff Master sheet.
        return 0
    except Exception as e:
        # Record fail. Two consecutive fails raise the P2.
        marker = "/tmp/staff-master-sync-last-fail"
        prev_fail = os.path.exists(marker)
        with open(marker, "w") as f: f.write(datetime.datetime.utcnow().isoformat() + "\n")
        sys.stderr.write(f"staff-master-sync FAILED: {e}\n")
        if prev_fail:
            raise_p2(str(e))
            try: os.unlink(marker)
            except Exception: pass
        return 1

if __name__ == "__main__":
    sys.exit(main())