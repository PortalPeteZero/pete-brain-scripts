#!/usr/bin/env python3
"""ee-data-parity.py — the EE reference-data parity gate (ee-pricing-db-plan, Step 8).

Proves no EE reference data is hardcoded or prose-drifted: prices live in the Portal DB
(price_list/customer_pricing), staff names in hub.staff_directory, stage-ids resolved from
pipeline_stages, source-bearing categories from the CC CHECK constraint. Runs to ZERO.
Stable infra ids (PORTAL_REF/CC_REF/OWNER_USER_ID/SHEET) are DEFENSIBLE config, not flagged.

Wired into ee-selfaudit + closeout. Exit 0 = clean; 1 = drift found.
"""
import os, re, sys, glob, importlib.util
VAULT = os.environ.get("VAULT", "/tmp/pbs")

def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(s)
    try: s.loader.exec_module(m)
    except SystemExit: pass
    return m

EE_PY = sorted(set(glob.glob(f"{VAULT}/ee-*.py") + [f"{VAULT}/te-log.py"]))
EE_PY = [p for p in EE_PY if os.path.basename(p) != "ee-data-parity.py"]

findings = []
def flag(cat, detail): findings.append((cat, detail))

# (a) no hardcoded £ figures in EE code (prices come from the DB now)
for p in EE_PY:
    for i, line in enumerate(open(p), 1):
        if re.search(r"£\s?\d", line) and not line.lstrip().startswith("#"):
            flag("hardcoded-price", f"{os.path.basename(p)}:{i} {line.strip()[:70]}")

# (b) no hardcoded staff-name tuple in ee-lint (names come from hub.staff_directory)
if re.search(r'for nm in \("[A-Z]\w+"', open(f"{VAULT}/ee-lint.py").read()):
    flag("hardcoded-staff-names", "ee-lint.py still has a literal staff-name tuple")

# (c) no bare stage-id integer maps/tuples in the EE tools (resolved from pipeline_stages)
for p in EE_PY:
    src = open(p).read()
    if re.search(r'\{\s*1:\s*"New"', src) or re.search(r'"won":\s*3', src) or re.search(r'stage_id"?\)?\s+in \(\s*\d', src) or 'stage_id="eq.2"' in src:
        flag("hardcoded-stage-id", f"{os.path.basename(p)} has a bare stage-id literal")

# (d) live-data sanity — prove the SSOT is readable and correct
try:
    ef = _load("ef", f"{VAULT}/ee-facts.py")
    pb = ef.price_book()
    if not pb:
        flag("pricing-unreadable", "price_book() empty — price_list not readable")
    elif pb.get("open_course_pp", {}).get("amount") != 175:
        flag("price-drift", f"open_course_pp = {pb.get('open_course_pp')} (want 175)")
    hc = ef.portal_q("SELECT count(*) c FROM customer_pricing WHERE item_key='open_course_pp' AND agreed_amount=145")
    if hc and hc[0]["c"] != 12:
        flag("honour-cohort", f"honour cohort = {hc[0]['c']} rows (want 12)")
except Exception as e:
    flag("ee-facts", f"ee-facts pricing unreadable: {str(e)[:60]}")

# (e) source-bearing: ee-signoff derives it == the CC CHECK constraint
try:
    so = _load("so", f"{VAULT}/ee-signoff.py")
    sb = set(so._source_bearing())
    d = so.cc("SELECT pg_get_constraintdef(oid) d FROM pg_constraint WHERE conname='ee_sourcebearing_needs_ref'")[0]["d"]
    check = set(re.findall(r"'([a-z]+)'::ee_correction_category", d))
    if sb != check:
        flag("source-bearing-drift", f"ee-signoff {sorted(sb)} != CHECK {sorted(check)}")
except Exception as e:
    flag("source-bearing", f"could not verify: {str(e)[:60]}")

# (f) staff names come live from the directory (>= 15 active+subcontractor)
try:
    staff = _load("el", f"{VAULT}/ee-lint.py")._staff_names()
    if len(staff) < 15:
        flag("staff-names", f"_staff_names() returned {len(staff)} (< 15 — directory read failing?)")
except Exception as e:
    flag("staff-names", f"could not verify: {str(e)[:60]}")

if findings:
    print(f"⛔ ee-data-parity: {len(findings)} finding(s)")
    for c, dt in findings:
        print(f"   ✗ [{c}] {dt}")
    sys.exit(1)
print("✅ ee-data-parity: ZERO drift — prices, staff names, stage-ids and source-bearing all live-sourced; "
      "honour cohort = 12; open course £175. (Stable infra ids are defensible config.)")
sys.exit(0)
