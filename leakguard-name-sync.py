#!/usr/bin/env python3
"""leakguard-name-sync.py — operator tool to make the ThingsLog device name equal the CRM address.

Reads each device's structured CRM address (LeakGuard Supabase) + the current ThingsLog name, builds the
canonical town-first name (Python mirror of src/lib/deviceName.ts), and shows before -> after.

  dry-run (default): python3 leakguard-name-sync.py [<device_number>|all]
  apply:             python3 leakguard-name-sync.py all --apply     # writes BOTH ThingsLog + devices.device_name

Safety: dry-run by default; reads each device first (pre-change name captured in the printed table);
one device at a time; no auto round-trip (the webhook does NOT mirror names back, so we write both systems).
Auth: ThingsLog full-access login session (thingslog-login.json); LeakGuard DB via Supabase Mgmt API (supabase-token).
"""
import sys, os, re, json, subprocess, urllib.request, importlib.util as ilu

PROJECT = "uuhzjytscifrpuqpfrdc"  # LeakGuard Supabase

def _sec(name):
    out = subprocess.run(["python3", "/tmp/pbs/cc-sql.py",
        f"SELECT value FROM secrets WHERE name='{name}' ORDER BY updated_at DESC NULLS LAST LIMIT 1"],
        capture_output=True, text=True, env={**os.environ, "VAULT": "/tmp/pbs"})
    return json.loads(out.stdout)[0]["value"]

def _db(query):
    tok = _sec("supabase-token")
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT}/database/query",
        data=json.dumps({"query": query}).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "leakguard-name-sync/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

# --- formatter: mirror of src/lib/deviceName.ts ---
def _clean(s): return re.sub(r"\s+", " ", (s or "")).strip()

def format_name(row):
    ov = _clean(row.get("thingslog_name_override"))
    if ov: return ov
    # Town - Street - Number - Unit - Villa (villa last; always town then street)
    segs = [_clean(row.get("city")), _clean(row.get("address_line1")),
            _clean(row.get("house_number")), _clean(row.get("unit")), _clean(row.get("property_name"))]
    return " - ".join(s for s in segs if s)

# --- ThingsLog helper internals ---
def _tl():
    spec = ilu.spec_from_file_location("tl", "/tmp/pbs/thingslog-api.py")
    m = ilu.module_from_spec(spec); spec.loader.exec_module(m)
    c = m._creds(); return m, c, c["base_url"], m._login(c), str(c.get("company_id"))

def set_name_thingslog(m, base, tok, cid, number, new_name):
    dev = m._get(base, tok, f"/api/v2/devices/{number}")
    dto_fields = ["description","deviceIcon","extendedHardwareSupport","hwVersion","iconId","language",
                  "manufacturingDate","model","name","nomenclature","replacementNumber","rmaHistory",
                  "swVersion","warrantyPeriodMonths"]
    dto = {f: dev.get(f) for f in dto_fields}; dto["name"] = new_name
    req = urllib.request.Request(base + f"/api/v2/devices/{number}", data=json.dumps(dto).encode(), method="PUT",
        headers={"Authorization": f"Bearer {tok}", "X-Company-Id": cid, "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20)
    return m._get(base, tok, f"/api/v2/devices/{number}").get("name")

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    target = args[0] if args else "all"
    # MULTI-METER: the ThingsLog device NAME is one physical field per logger. Drive it from the
    # MAIN meter (tl_output_index=0) only — a sub-meter row (port>0) has its own display name via
    # thingslog_name_override and must NOT push its name to the shared physical device or stomp the
    # port-0 CRM name. So scope the whole sync to port-0 rows.
    where = " WHERE d.tl_output_index = 0" + ("" if target == "all" else f" AND d.device_number = '{target}'")
    rows = _db(f"""SELECT d.device_number, d.device_name AS current_crm, d.thingslog_name_override,
        p.city, p.property_name, p.address_line1, p.house_number, p.unit
        FROM devices d LEFT JOIN properties p ON p.id = d.property_id{where}
        ORDER BY d.device_number""")

    m, c, base, tok, cid = _tl()
    tl_devs = {d["number"]: d.get("name") for d in m._get(base, tok, "/api/v2/devices").get("content", [])}

    print(f"{'DEVICE':10} {'THINGSLOG NOW':38} {'-> PROPOSED':38} CHANGED")
    print("-" * 100)
    changes = []
    for r in rows:
        num = r["device_number"]; now = tl_devs.get(num, "?"); target_name = format_name(r)
        changed = _clean(now) != _clean(target_name)
        flag = "CHANGE" if changed else "ok"
        print(f"{num:10} {str(now)[:38]:38} {target_name[:38]:38} {flag}")
        if changed and target_name:
            changes.append((num, now, target_name))

    print(f"\n{len(changes)} of {len(rows)} would change.")
    if not apply:
        print("Dry-run only. Re-run with --apply to write BOTH ThingsLog and devices.device_name.")
        return
    print("\nAPPLYING...")
    for num, old, new in changes:
        applied = set_name_thingslog(m, base, tok, cid, num, new)
        _db(f"UPDATE devices SET device_name = '{new.replace(chr(39), chr(39)+chr(39))}' WHERE device_number = '{num}' AND tl_output_index = 0")
        print(f"  {num}: '{old}' -> '{applied}'  (CRM device_name updated)")
    print(f"\nDone. {len(changes)} devices renamed in ThingsLog + CRM.")

if __name__ == "__main__":
    main()
