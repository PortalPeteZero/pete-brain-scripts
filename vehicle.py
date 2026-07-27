#!/usr/bin/env python3
"""vehicle.py -- everything Sygma knows about a vehicle, in one command.

Built 27 Jul 2026 because the fleet data is spread over four tables on a DIFFERENT database
(the Sygma Platform, not the CC), plus Drive, plus the P11D. A session that had to rediscover
that each time would keep getting it wrong -- and did.

  VAULT=/tmp/pbs python3 vehicle.py find "YP20 SKV"   # one vehicle, everything
  VAULT=/tmp/pbs python3 vehicle.py list              # the whole fleet, one line each
  VAULT=/tmp/pbs python3 vehicle.py gaps              # what is missing or stale
  VAULT=/tmp/pbs python3 vehicle.py contract          # the operating rules, printed

Registrations are matched with or without the space, because the insurer and DVLA write
YP20SKV while the fleet table stores YP20 SKV.

Operating contract: the `sygma-vehicles` note in vault_notes. Read it before enriching.
"""
import os, sys, json, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PORTAL = "rsczwfstwkthaybxhszy"          # Sygma Platform -- NOT the Command Centre database
TOKEN = (os.environ.get("SUPABASE_TOKEN") or "").strip() or \
    open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()


def q(sql):
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PORTAL}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "curl/8.7.1"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"platform query failed {e.code}: {e.read().decode()[:300]}")


def lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def money(v):
    return "—" if v in (None, "") else f"£{float(v):,.2f}"


def norm(reg):
    """Match with or without the space: the insurer and DVLA drop it, hub.fleet keeps it."""
    return "".join(str(reg).split()).upper()


def find(reg):
    n = norm(reg)
    v = q(f"SELECT * FROM hub.fleet WHERE replace(vehicle_reg,' ','') = {lit(n)}")
    if not v:
        near = q("SELECT vehicle_reg FROM hub.fleet ORDER BY vehicle_reg")
        sys.exit(f"no vehicle {reg}. Fleet is: " + ", ".join(r['vehicle_reg'] for r in near))
    v = v[0]
    reg_sp = v["vehicle_reg"]
    drv = q(f"SELECT full_name FROM hub.staff_directory WHERE employee_ref = "
            f"{v['current_driver_ref'] if v['current_driver_ref'] is not None else 'NULL'}")
    print("=" * 68)
    print(f"  {reg_sp}   {v.get('make_model') or ''}")
    print("=" * 68)
    print(f"  category   {v.get('category') or '—'}")
    print(f"  tenure     {v.get('owned_or_leased') or '—'}   "
          f"(Owned = our asset, finance included; Leased = contract hire only)")
    print(f"  driver     {drv[0]['full_name'] if drv else '— unassigned —'}")
    print(f"  MOT due    {v.get('mot_due') or '— none (too new, or not held)'}")
    print(f"  tax due    {v.get('tax_due') or '—'}")
    print(f"  lease end  {v.get('lease_end') or '—'}")
    print(f"  mileage    {v.get('last_mileage') or '—'}"
          + (f" as at {v['mileage_date']}" if v.get('mileage_date') else ""))

    fin = q(f"SELECT * FROM hub.fleet_finance WHERE vehicle_reg = {lit(reg_sp)}")
    print("\n  FINANCE (owner/admin only)")
    if not fin:
        print("    no finance record — presumed owned outright, unverified")
    for f in fin:
        print(f"    {f.get('agreement_type')}  ·  {f.get('funder') or '—'}"
              f"{'  (broker ' + f['broker'] + ')' if f.get('broker') else ''}")
        print(f"    agreement  {f.get('agreement_no') or '—'}   status {f.get('status')}")
        print(f"    monthly    {money(f.get('monthly_payment'))} ex VAT"
              f"   term {f.get('term_months') or '—'} months"
              f"   {f.get('start_date') or '—'} to {f.get('end_date') or '—'}")
        if f.get("contract_mileage"):
            print(f"    mileage    {f['contract_mileage']:,} contract"
                  f"   excess {f.get('excess_mileage_ppm') or '—'}p per mile")
        if f.get("outstanding_balance") is not None:
            print(f"    OUTSTANDING {money(f['outstanding_balance'])} as at "
                  f"{f.get('outstanding_as_of') or 'NO DATE — treat as unusable'}")
        if f.get("settlement_figure") is not None:
            print(f"    SETTLEMENT  {money(f['settlement_figure'])} quoted "
                  f"{f.get('settlement_quoted_on') or '?'}, valid to "
                  f"{f.get('settlement_valid_until') or '?'}"
                  "   (a settlement is NOT the book balance)")
        if f.get("notes"):
            print("    notes:")
            for line in str(f["notes"]).split("\n"):
                if line.strip():
                    print(f"      {line.strip()}")

    bik = q(f"SELECT * FROM hub.vehicle_bik WHERE vehicle_reg = {lit(reg_sp)} ORDER BY tax_year DESC")
    print("\n  P11D BENEFIT (owner/admin only)")
    if not bik:
        print("    none declared" + ("" if v.get("current_driver_ref") is None
                                     else " — check whether one is due"))
    for b in bik:
        print(f"    {b['tax_year']}  {b['benefit_kind']}  {b['employee_name']}"
              f"   {money(b.get('cash_equivalent'))}"
              + (f"   {b['co2_gkm']} g/km" if b.get("co2_gkm") else ""))

    docs = q(f"SELECT name, doc_type, sensitive, drive_file_id FROM hub.vehicle_documents "
             f"WHERE vehicle_reg = {lit(reg_sp)} ORDER BY name")
    print(f"\n  DOCUMENTS ({len(docs)})   Drive: Sygma Hub/Vehicles/{reg_sp}/")
    for d in docs:
        print(f"    [{d['doc_type']}{'/sensitive' if d['sensitive'] else ''}] {d['name']}")
        print(f"       https://drive.google.com/file/d/{d['drive_file_id']}/view")
    if v.get("notes"):
        print("\n  NOTES")
        print(f"    {v['notes']}")


def listing():
    rows = q("""SELECT f.vehicle_reg, f.make_model, f.category, f.owned_or_leased,
                       s.full_name AS driver, ff.funder, ff.monthly_payment, ff.status
                FROM hub.fleet f
                LEFT JOIN hub.staff_directory s ON s.employee_ref = f.current_driver_ref
                LEFT JOIN hub.fleet_finance ff ON ff.vehicle_reg = f.vehicle_reg
                ORDER BY f.vehicle_reg""")
    print(f"{'REG':<10}{'VEHICLE':<28}{'TENURE':<9}{'DRIVER':<19}{'FUNDER':<26}{'MONTHLY':>10}")
    print("-" * 104)
    live = 0.0
    for r in rows:
        m = r.get("monthly_payment")
        if m and r.get("status") == "active":
            live += float(m)
        print(f"{r['vehicle_reg']:<10}{(r.get('make_model') or '')[:26]:<28}"
              f"{(r.get('owned_or_leased') or '')[:8]:<9}{(r.get('driver') or '—')[:17]:<19}"
              f"{(r.get('funder') or '—')[:24]:<26}{money(m):>10}")
    print("-" * 104)
    print(f"{len(rows)} vehicles · live monthly commitment {money(live)} ex VAT")


def gaps():
    checks = [
        ("no finance record at all",
         "SELECT f.vehicle_reg FROM hub.fleet f LEFT JOIN hub.fleet_finance ff "
         "ON ff.vehicle_reg=f.vehicle_reg WHERE ff.vehicle_reg IS NULL"),
        ("active agreement with no monthly payment",
         "SELECT vehicle_reg FROM hub.fleet_finance WHERE status='active' AND monthly_payment IS NULL"),
        ("leased with no lease end date",
         "SELECT vehicle_reg FROM hub.fleet WHERE owned_or_leased='Leased' AND lease_end IS NULL"),
        ("outstanding balance with no as-of date (unusable)",
         "SELECT vehicle_reg FROM hub.fleet_finance WHERE outstanding_balance IS NOT NULL "
         "AND outstanding_as_of IS NULL"),
        ("outstanding balance over 6 months old",
         "SELECT vehicle_reg FROM hub.fleet_finance WHERE outstanding_as_of < CURRENT_DATE - 180"),
        ("settlement quote expired",
         "SELECT vehicle_reg FROM hub.fleet_finance WHERE settlement_valid_until < CURRENT_DATE"),
        ("MOT overdue",
         "SELECT vehicle_reg FROM hub.fleet WHERE mot_due < CURRENT_DATE"),
        ("MOT or tax due within 60 days",
         "SELECT vehicle_reg FROM hub.fleet WHERE mot_due BETWEEN CURRENT_DATE AND CURRENT_DATE+60 "
         "OR tax_due BETWEEN CURRENT_DATE AND CURRENT_DATE+60"),
        ("assigned driver but no P11D benefit declared",
         "SELECT f.vehicle_reg FROM hub.fleet f WHERE f.current_driver_ref IS NOT NULL "
         "AND NOT EXISTS (SELECT 1 FROM hub.vehicle_bik b WHERE b.vehicle_reg=f.vehicle_reg)"),
        ("no documents filed",
         "SELECT f.vehicle_reg FROM hub.fleet f WHERE NOT EXISTS "
         "(SELECT 1 FROM hub.vehicle_documents d WHERE d.vehicle_reg=f.vehicle_reg)"),
    ]
    total = 0
    for label, sql in checks:
        rows = q(sql)
        if rows:
            total += len(rows)
            print(f"  {label}: {', '.join(r['vehicle_reg'] for r in rows)}")
    print(f"\n{total} item(s) flagged. Not all are faults — a new car has no MOT, a spare has no "
          f"driver, an outright-owned vehicle has no finance.")


CONTRACT = """
Sygma vehicles — the rules that matter (full note: `sygma-vehicles` in vault_notes)

  · Data lives on the SYGMA PLATFORM database rsczwfstwkthaybxhszy, not the CC.
  · A financed vehicle is still Owned. Only contract hire is Leased.
  · Vehicle finance may live in the Hub drive. Staff wages and company accounts may not.
  · Finance and P11D are owner+admin only. A driver must never see the money on their vehicle.
  · A lease "schedule" IS the agreement — classify it agreement + sensitive.
  · A settlement quote is NOT the book balance. Say which one you mean.
  · An outstanding balance without an as-of date is unusable.
  · Latitude is usually the BROKER, not the funder. Check who the lender is.
  · Novuna schedules are e-signed PDFs: the values are rendered, not text. Render to PNG and read.
  · When figures do not make sense, get the signed agreement before theorising.
"""

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "find" and len(sys.argv) > 2:
        find(" ".join(sys.argv[2:]))
    elif cmd == "gaps":
        gaps()
    elif cmd == "contract":
        print(CONTRACT)
    else:
        listing()
