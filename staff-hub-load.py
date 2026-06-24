#!/usr/bin/env python3
"""Load the Staff Master Directory into the Internal Hub (hub.staff_directory + hub.staff_hr).

Repoints the staff directory from the standalone Vercel dashboard (sygma-staff.vercel.app)
to the Sygma Internal Hub (sygmaportal.com/hub/directory + /hub/profile). Reads the cache that
staff-master-sync.py already writes (Library/sy-hr/Staff Master.json) and splits each Directory
row into two privacy tiers:

  - hub.staff_directory — BASIC contact card. RLS: any staff (trainer/admin/owner) can read.
      employee_ref, full_name, preferred_name, honorific, job_title, sub_business, reports_to,
      work_email, work_mobile, employment_status.
  - hub.staff_hr — SENSITIVE operational HR. RLS: admin/owner read all; each staff member reads
      their OWN row (work_email matched to their hub.user_profiles email). DOB, NI number, home
      address, personal contacts, emergency contacts, vehicle, holiday/sick, RTW, driver policy,
      appraisal date, qualifications + staff-folder links.

NEVER loaded into the Hub: salary / bank / tax codes / pension / contract documents / payroll row
pointer / disciplinary / full appraisals — those live ONLY in the Pete & Mic Payroll Master /
Personnel private folders (owner-only), per [[staff-data-routing]]. This loader touches NONE of
those; it reads only the operational Directory tab. Also dropped: integration IDs (Asana/Xero/
Odoo/Soldo/Garmin/calendar/jotform) and the free-text `notes` field (can hold sensitive HR notes).

Usage:
    staff-hub-load.py [path-to-Staff-Master.json] [--dry-run]
    (default cache: Library/sy-hr/Staff Master.json)

The data lives in the Portal's Supabase project (rsczwfstwkthaybxhszy), hub schema, behind
RLS — written here via the Supabase Management API (account token). Run as the final step of
staff-master-sync.py (the nightly cron), or standalone to reload from the current cache.
"""
import json
import os
import re
import sys
import urllib.request
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = VAULT
CACHE_JSON = f"{VAULT}/Library/sy-hr/Staff Master.json"
TOKEN_FILE = f"{VAULT}/Library/processes/supabase-access-token.md"
REF = "rsczwfstwkthaybxhszy"  # Portal Supabase project (hosts the hub schema)
SUB_REF_BASE = 9001  # reserved synthetic employee_ref band for subcontractors (not payroll refs)

# Field tiers — the ONLY columns that reach the Hub. Anything not listed never leaves the Sheet.
BASIC_FIELDS = [
    "full_name", "preferred_name", "honorific", "job_title", "sub_business",
    "reports_to", "work_email", "work_mobile", "employment_status",
]
HR_FIELDS = [
    "work_email", "dob", "gender", "ni_number", "start_date", "end_date", "contract_type",
    "personal_email", "personal_mobile", "home_address",
    "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relationship",
    "vehicle_make_model", "vehicle_reg",
    "holiday_entitlement_days", "holidays_taken_ytd", "holidays_remaining",
    "sick_days_ytd", "appointments_ytd",
    "right_to_work_verified", "driver_policy_signed_date", "last_appraisal_date",
    "key_qualifications", "hub_staff_folder_url",
]


def sbp_token():
    m = re.search(r"sbp_[A-Za-z0-9]+", open(TOKEN_FILE).read())
    if not m:
        sys.exit("No sbp_ token found in supabase-access-token.md")
    return m.group(0)


def run_sql(sql, token):
    body = json.dumps({"query": sql}).encode()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def lit(v):
    """SQL literal for a text value; empty / None -> NULL."""
    if v is None:
        return "NULL"
    s = str(v).strip()
    # A leftover formula string (e.g. =IFERROR(SUMIFS(...))) means the sync pulled FORMULA not
    # value — treat as no data rather than storing the formula. (pull_sheet now sends formatted
    # values, so this is a belt-and-braces guard.)
    if s == "" or s.startswith("="):
        return "NULL"
    return "'" + s.replace("'", "''") + "'"


def emp_ref(row):
    """Return the integer employee_ref, or None if the row has no usable ref."""
    v = str(row.get("employee_ref", "")).strip()
    if not v:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def upsert_sql(table, cols, rows_vals):
    """Build an INSERT ... ON CONFLICT (employee_ref) DO UPDATE for the given column list."""
    collist = ", ".join(cols)
    values = ",\n  ".join("(" + ", ".join(rv) + ")" for rv in rows_vals)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "employee_ref")
    return (
        f"insert into hub.{table} ({collist}, updated_at)\nvalues\n  "
        + ",\n  ".join("(" + ", ".join(rv) + ", now())" for rv in rows_vals)
        + f"\non conflict (employee_ref) do update set {updates}, updated_at = now();"
    )


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    path = args[0] if args else CACHE_JSON
    if not os.path.exists(path):
        sys.exit(f"Cache not found: {path} (run staff-master-sync.py first).")

    data = json.load(open(path))
    directory = data.get("tabs", {}).get("Directory", [])
    subs = data.get("tabs", {}).get("Subcontractors", [])

    # Employees: real Directory rows with an integer employee_ref.
    staff = [(emp_ref(r), r) for r in directory]
    staff = [(ref, r) for ref, r in staff if ref is not None and (r.get("full_name") or "").strip()]
    if not staff:
        sys.exit("No staff rows with an employee_ref found in the Directory tab — refusing to wipe the Hub.")

    # Subcontractors: contact-only people on a separate tab — no employee_ref, no payroll, NO HR row.
    # They appear in the directory (badged) and get Hub access by their Portal role, but carry no
    # hub.staff_hr. Synthetic refs in a reserved band (SUB_REF_BASE+) let them fit the employee_ref-
    # keyed table; worker_type disambiguates. (Transitional — public.staff will key subs on a
    # surrogate id with a null employee_ref.) Sorted for stable ref assignment run-to-run.
    sub_rows = sorted([s for s in subs if (s.get("full_name") or "").strip()],
                      key=lambda s: (s.get("full_name") or "").lower())

    dir_cols = ["employee_ref", "worker_type"] + BASIC_FIELDS
    hr_cols = ["employee_ref"] + HR_FIELDS

    dir_vals = [[str(ref), "'employee'"] + [lit(r.get(c)) for c in BASIC_FIELDS] for ref, r in staff]
    for i, s in enumerate(sub_rows):
        srow = {**s, "employment_status": "Subcontractor", "reports_to": None}
        dir_vals.append([str(SUB_REF_BASE + i), "'subcontractor'"] + [lit(srow.get(c)) for c in BASIC_FIELDS])

    hr_vals = [[str(ref)] + [lit(r.get(c)) for c in HR_FIELDS] for ref, r in staff]  # employees only

    all_refs = [ref for ref, _ in staff] + [SUB_REF_BASE + i for i in range(len(sub_rows))]
    refs_in = ", ".join(str(r) for r in all_refs)
    stmts = [
        upsert_sql("staff_directory", dir_cols, dir_vals),
        upsert_sql("staff_hr", hr_cols, hr_vals),
        # Drop anyone no longer present (leaver / removed sub). Cascade clears any hr row.
        f"delete from hub.staff_directory where employee_ref not in ({refs_in});",
    ]
    sql = "\n".join(stmts)

    if dry:
        print(sql)
        print(f"\n-- DRY RUN — {len(staff)} employees + {len(sub_rows)} subcontractors")
        return

    token = sbp_token()
    run_sql(sql, token)
    res = run_sql(
        "select (select count(*) from hub.staff_directory) as directory, "
        "(select count(*) from hub.staff_directory where worker_type='subcontractor') as subs, "
        "(select count(*) from hub.staff_hr) as hr;", token
    )
    print(f"Loaded {len(staff)} employees + {len(sub_rows)} subcontractors into the Hub: "
          f"staff_directory={res[0]['directory']} ({res[0]['subs']} subs), staff_hr={res[0]['hr']}.")


if __name__ == "__main__":
    main()