#!/usr/bin/env python3
"""ee-data-parity.py — the EE reference-data parity gate (ee-learning-database-plan).

Proves no EE reference data is hardcoded or prose-drifted: prices live in the CC DB
(ee_rates/ee_customer_rates), the course list in ee_catalogue over Portal public.courses, staff
names in hub.staff_directory, stage-ids resolved from pipeline_stages, source-bearing categories
from the CC CHECK constraint. Runs to ZERO. Stable infra ids are DEFENSIBLE config, not flagged.

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

# (d) live-data sanity — prove the EE SSOT (CC ee_rates/ee_customer_rates) is readable and correct
try:
    ef = _load("ef", f"{VAULT}/ee-facts.py")
    pb = ef.price_book()
    if not pb:
        flag("pricing-unreadable", "price_book() empty — ee_rates not readable")
    elif pb.get("open_course_pp", {}).get("amount") != 175:
        flag("price-drift", f"open_course_pp = {pb.get('open_course_pp')} (want 175)")
    for k in ("open_course_pp", "onsite_day_rate", "eusr_reg", "sygma_inhouse"):
        if k not in pb:
            flag("missing-rate", f"ee_rates missing {k}")
    amt, src = ef.resolve_line("onsite_day_rate", contact_ref="f70757e5-ae16-47dc-9db1-75986f186354")
    if src != "customer-override" or amt != 945:
        flag("customer-rate", f"Renature onsite resolved {amt}/{src} (want 945/customer-override)")
except Exception as e:
    flag("ee-facts", f"ee-facts pricing unreadable: {str(e)[:60]}")

# (g) EE catalogue integrity — every curated course resolves its facts + cert options in ee_rates
try:
    cat = ef.cc_q("SELECT course_key, cert_options, attachments FROM ee_catalogue")
    codes = [c["course_key"] for c in cat]
    if not codes:
        flag("catalogue-empty", "ee_catalogue has no rows")
    facts = {r["code"] for r in ef.portal_q(
        "SELECT code FROM public.courses WHERE code IN (%s)" % ",".join("'%s'" % c for c in codes))} if codes else set()
    for c in cat:
        if c["course_key"] not in facts:
            flag("catalogue-orphan", f"ee_catalogue {c['course_key']} has no public.courses facts row")
        for ck in (c.get("cert_options") or []):
            if ck not in (pb or {}):
                flag("cert-option-unknown", f"{c['course_key']} cert_option '{ck}' not in ee_rates")
        if not (c.get("attachments") or []):
            flag("catalogue-no-agenda", f"{c['course_key']} has no attachments")
except Exception as e:
    flag("catalogue", f"could not verify ee_catalogue: {str(e)[:60]}")

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
print("✅ ee-data-parity: ZERO drift — prices (CC ee_rates), the customer special (ee_customer_rates), "
      "the course list (ee_catalogue over public.courses), staff names, stage-ids and source-bearing all "
      "live-sourced; open course £175. (Stable infra ids are defensible config.)")
sys.exit(0)
