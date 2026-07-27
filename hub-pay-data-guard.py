#!/usr/bin/env python3
"""hub-pay-data-guard.py -- refuse employee pay data landing in the Sygma Hub drive.

Pete's rule (27 Jul 2026): "everything except wages, money related stuff goes to hub."
So the Hub is the default home for staff paperwork -- licences, passports, CVs,
qualifications, inductions -- and the ONLY things held back to Sygma Private are the
pay-bearing ones: contracts (they carry the salary clause), payslips, P60/P45/P11D,
bank details, tax codes, pension elections, salary reviews.

Why a gate and not another rule: the rule already existed in [[staff-data-routing]] and was
correct. It still lost. On 27 Jul a PDF of Kevin Morley's contract of employment, stating
GBP 40,000 per annum, was sitting in Sygma Hub/HR/Staff/Active/ where every Hub user could
read it. A rule that cannot refuse an action is a wish.

Enforcement point is drive-api.py's upload/move, because helper-first discipline means every
Drive write goes through it.

Usage:
  VAULT=/tmp/pbs python3 hub-pay-data-guard.py --audit          # sweep the Hub drive
  VAULT=/tmp/pbs python3 hub-pay-data-guard.py --check "<name>" # test one filename
  # library: from hub_pay_data_guard import check   (loaded by path -- see drive-api.py)
"""
import os, re, sys, json, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# The drives that are shared with staff. A pay document must never land in one of these.
SHARED_DRIVES = ("sygma hub",)

# Document types that disclose pay. Deliberately specific: "contract" or "bank details" alone
# is far too broad (customer contracts and Sygma's own bank details are legitimately in the
# Hub), so each pattern names an EMPLOYEE pay artefact.
PAY_PATTERNS = [
    (r"contract\s*of\s*employment",          "a contract of employment carries the salary clause"),
    (r"statement\s*of\s*main\s*terms",       "the statement of main terms carries the salary clause"),
    (r"\bpayslip",                            "a payslip is pay data"),
    (r"pay\s*statement",                      "a pay statement is pay data"),
    (r"\bP60\b",                              "a P60 states annual pay"),
    (r"\bP45\b",                              "a P45 states pay to date"),
    (r"\bP11D\b",                             "a P11D states taxable benefits"),
    (r"\bP46\b",                              "a P46 / starter checklist carries tax details"),
    (r"starter\s*checklist",                  "a starter checklist carries tax details"),
    (r"salary\s*(review|letter|increase)",    "a salary document is pay data"),
    (r"\bremuneration\b",                     "remuneration documents are pay data"),
    (r"pay\s*(review|rise|award)",            "a pay review is pay data"),
    (r"\bnominas?\b",                         "a nomina is a Spanish payslip"),
    (r"tax\s*code",                           "a tax code is pay data"),
    (r"pension\s*(election|opt|enrol)",       "pension elections are pay-linked"),
    (r"company\s*accounts",                   "company accounts show profit"),
    (r"profit\s*(and|&)\s*loss",              "a P&L shows profit"),
]

# Bank details are only pay data when they belong to a PERSON. The company's own bank details,
# the landlord's, and a customer's questionnaire asking for ours are all legitimate Hub content
# -- measured 27 Jul 2026 against 78,965 real Hub files, where the unconditional pattern gave
# 3 false positives to 1 true one. So these fire only inside a staff folder.
STAFF_CONTEXT = (r"hr/staff/", r"personnel/", r"/staff/active/", r"/staff/leavers/")
STAFF_ONLY_PATTERNS = [
    (r"(bank|account)\s*details?\b", "an employee's bank details are pay data"),
    (r"sort\s*code",                 "a sort code is bank data"),
]

# A blank template or example discloses nobody's pay, so it belongs in the Hub with the rest
# of the forms. Without this the guard would block HR/Forms, which is legitimate Hub content.
TEMPLATE_MARKERS = (
    "template", "example", "blank", "(pro forma)", "proforma", "specimen", "sample",
)

# Another company's HR pack held for training delivery is their document, not our payroll.
EXEMPT_PATH_MARKERS = (
    "app data/",                 # the live Portal/CRM document store (customer material)
    "hr/forms/",                 # our own blank form library
    "sales & pipeline/tenders/", # tender submissions quote our procedures, not our payroll
)


def check(name, path="", drive=""):
    """Return (allowed, reason). Only refuses a pay artefact heading into a shared drive."""
    if drive and drive.strip().lower() not in SHARED_DRIVES:
        return True, ""
    hay = f"{path} {name}".lower()
    if any(m in hay for m in TEMPLATE_MARKERS):
        return True, ""
    if any(m in hay for m in EXEMPT_PATH_MARKERS):
        return True, ""
    for pat, why in PAY_PATTERNS:
        if re.search(pat, hay, re.I):
            return False, why
    if any(re.search(c, hay, re.I) for c in STAFF_CONTEXT):
        for pat, why in STAFF_ONLY_PATTERNS:
            if re.search(pat, hay, re.I):
                return False, why
    return True, ""


def cc_sql(sql):
    out = subprocess.run(["python3", os.path.join(VAULT, "cc-sql.py"), sql],
                         capture_output=True, text=True,
                         env={**os.environ, "VAULT": VAULT}).stdout
    try:
        return json.loads(out)
    except Exception:
        sys.exit(f"cc-sql failed: {out[:400]}")


def audit():
    rows = cc_sql("SELECT drive, path, name FROM drive_files "
                  "WHERE drive = 'Sygma Hub' AND NOT is_folder ORDER BY path")
    bad = []
    for r in rows:
        ok, why = check(r["name"], r["path"], r["drive"])
        if not ok:
            bad.append((r["path"], why))
    print(f"hub-pay-data-guard: {len(rows)} file(s) in Sygma Hub checked")
    if not bad:
        print("  ✓ no employee pay data in the shared drive")
        return 0
    print(f"  ⚠ {len(bad)} pay-bearing file(s) in a drive every Hub user can read:")
    for p, why in bad:
        print(f"     {p}\n         → {why}")
    print("\n  Move each to Sygma Private / Personnel / Staff / <status> / <Name> /.")
    return 1


if __name__ == "__main__":
    if "--check" in sys.argv:
        n = sys.argv[sys.argv.index("--check") + 1]
        ok, why = check(n, drive="Sygma Hub")
        print(("ALLOW  " if ok else "REFUSE ") + n + ("" if ok else f"  → {why}"))
        sys.exit(0 if ok else 1)
    sys.exit(audit())
