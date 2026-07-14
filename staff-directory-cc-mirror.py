#!/usr/bin/env python3
"""staff-directory-cc-mirror.py -- refresh the CC copy of the Sygma staff directory.

Reads the LIVE SSOT `hub.staff_directory` on the Sygma Platform Supabase
(rsczwfstwkthaybxhszy) and mirrors the CONTACT-CARD tier only into the Command
Centre `public.staff_directory` (zhexcaflgahdcbzvbyfq), so the 24/7 cc-agent bot
(which reads only the CC) can answer "who is X / who are the trainers".

NOT scheduled. Staff rarely change -- run this ON DEMAND when a joiner/leaver/role
change happens (Pete, 14 Jul 2026). Prints a diff (add/update/remove); --dry-run
shows the diff without writing.

Sensitive columns (salary/NI/DOB/address/HR notes) are NEVER read or written here --
only the basic tier below. Salary stays in the CC `payroll` schema (owner-private).

Usage:
  staff-directory-cc-mirror.py [--dry-run]
"""
import os, sys, json, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
TOK = (os.environ.get("SUPABASE_TOKEN") or "").strip() or \
      open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
HUB_REF = "rsczwfstwkthaybxhszy"      # Sygma Platform (hub schema) -- the SSOT
CC_REF  = "zhexcaflgahdcbzvbyfq"      # Command Centre
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

BASIC = ["employee_ref", "full_name", "preferred_name", "honorific", "job_title",
         "sub_business", "reports_to", "work_email", "work_mobile",
         "employment_status", "worker_type"]

def run(ref, sql):
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{ref}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": UA},
        method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR {ref} HTTP {e.code}: {e.read().decode()[:300]}")

def esc(v):
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"

def main():
    dry = "--dry-run" in sys.argv

    hub = run(HUB_REF, f"SELECT {', '.join(BASIC)} FROM hub.staff_directory")
    cc  = run(CC_REF, "SELECT employee_ref, full_name FROM public.staff_directory")
    hub_refs = {str(r["employee_ref"]) for r in hub}
    cc_refs  = {str(r["employee_ref"]) for r in cc}

    adds    = sorted(hub_refs - cc_refs)
    removes = sorted(cc_refs - hub_refs)
    print(f"hub={len(hub)}  cc(before)={len(cc)}  +{len(adds)} new  -{len(removes)} leaver(s)")
    if removes:
        gone = [r["full_name"] for r in cc if str(r["employee_ref"]) in removes]
        print("  removing:", ", ".join(gone))

    if dry:
        print("[dry-run] no writes.")
        return

    # Upsert every hub row (insert new, refresh existing) in one statement.
    rows_sql = ",\n  ".join(
        "(" + ", ".join(esc(r.get(c)) for c in BASIC) + ", now(), now())" for r in hub)
    cols = ", ".join(BASIC) + ", source_updated_at, synced_at"
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in BASIC if c != "employee_ref")
    run(CC_REF, f"""
INSERT INTO public.staff_directory ({cols})
VALUES
  {rows_sql}
ON CONFLICT (employee_ref) DO UPDATE SET
  {updates}, source_updated_at=EXCLUDED.source_updated_at, synced_at=now();
""")
    # Drop leavers no longer in the hub.
    if removes:
        in_list = ", ".join(esc(x) for x in removes)
        run(CC_REF, f"DELETE FROM public.staff_directory WHERE employee_ref IN ({in_list})")

    after = run(CC_REF, "SELECT count(*) FROM public.staff_directory")[0]["count"]
    print(f"done. cc(after)={after} rows.")

if __name__ == "__main__":
    main()
