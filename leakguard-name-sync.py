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

def name_matches_address(name, address_line1, city, house_number=None):
    """Does this ThingsLog name identify this property? The ONE definition, shared by the checkers.

    Token-based, not substring-based, and that is the whole point. format_name() below builds
    `Town - Street - Number - Unit - Villa`, so a substring test against the raw address_line1
    breaks the moment an address carries its house number inline: "Avenida Italia 9" is not a
    substring of "Puerto del Carmen - Avenida Italia - 9 - Casa 16", although they are the same
    place and the name is character-for-character what this tool generates.

    That false positive was live in BOTH lg-brief.py and lg-commission.py on 29 Jul 2026, on
    04326710 (Gillian Guidi), where ThingsLog and the CRM held identical names. Every LeakGuard
    session opened by announcing a disagreement that did not exist, above the instruction "quote
    ThingsLog, not the CRM, until these are reconciled" — so the one line meant to build trust in
    the reconciliation was the line undermining it.

    Every significant word and number of the address must appear in the name, in any order and
    whatever the separators. A name pointing at a different property still fails, which is the
    thing this check is actually for.
    """
    if not name or _clean(name) in ("!", "?"):
        return False

    def toks(s):
        return {t for t in re.split(r"[^0-9a-z]+", (s or "").lower()) if t}

    want = toks((address_line1 or "").split(",")[0]) | toks(city) | toks(house_number)
    return want.issubset(toks(name))


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
    # EVERY page. /api/v2/devices defaults to size=20 against 30 devices, so reading page 0 left 10
    # devices invisible: they fell to the "?" default below and were reported as needing a rename
    # when their ThingsLog names were already correct. Verified 27 Jul 2026 (totalElements 30,
    # totalPages 2).
    tl_devs = {d["number"]: d.get("name") for d in m._all_devices(base, tok)}

    print(f"{'DEVICE':10} {'THINGSLOG NOW':38} {'-> PROPOSED':38} CHANGED")
    print("-" * 100)
    changes = []
    for r in rows:
        num = r["device_number"]; now = tl_devs.get(num, "?"); target_name = format_name(r)
        changed = _clean(now) != _clean(target_name)
        # An unassigned spare has no property, so format_name() returns "". Those rows are already
        # excluded from `changes` below (`and target_name`), but the table printed "CHANGE" against
        # them, which reads as "this tool is about to blank nine device names". Say what it will
        # actually do.
        flag = ("skip (no property)" if changed and not target_name
                else "CHANGE" if changed else "ok")
        print(f"{num:10} {str(now)[:38]:38} {target_name[:38]:38} {flag}")
        if changed and target_name:
            changes.append((num, now, target_name))

    print(f"\n{len(changes)} of {len(rows)} would change.")
    if not apply:
        print("Dry-run only. Re-run with --apply to write BOTH ThingsLog and devices.device_name.")
        return
    print("\nAPPLYING...")
    failed = []
    for num, old, new in changes:
        applied = set_name_thingslog(m, base, tok, cid, num, new)
        # THE READ-BACK IS CHECKED. It was already being fetched and returned by
        # set_name_thingslog(), and then thrown away — the CRM was updated whatever ThingsLog had
        # actually stored. ThingsLog returns HTTP 200 for fields it silently drops (proven on the
        # device DTO's own latitude/longitude), so writing our copy on the strength of a 200 is how
        # the copy and the record drift apart while the tool prints success.
        if _clean(applied) != _clean(new):
            failed.append(num)
            print(f"  {num}: CRM NOT UPDATED — ThingsLog stored {applied!r}, not {new!r}. "
                  f"ThingsLog is the record, so our copy is left alone rather than made to "
                  f"disagree with it.")
            continue
        _db(f"UPDATE devices SET device_name = '{new.replace(chr(39), chr(39)+chr(39))}' WHERE device_number = '{num}' AND tl_output_index = 0")
        print(f"  {num}: '{old}' -> '{applied}'  (CRM device_name updated)")
    print(f"\nDone. {len(changes) - len(failed)} device(s) renamed in ThingsLog + CRM."
          + (f"  {len(failed)} FAILED: {', '.join(failed)}" if failed else ""))
    if failed:
        sys.exit(1)

if __name__ == "__main__":
    main()
