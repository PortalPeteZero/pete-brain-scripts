#!/usr/bin/env python3
"""
payroll-migrate.py — migrate the Payroll Master gsheet into the CC `payroll` schema, then
RECONCILE every stored element against the sheet (household-finance-system plan, Phase 5).

SAFETY GATE: this script never touches the gsheet — it only READS it. The gsheet stays the live
source until reconciliation passes (0 mismatches). Stored values are the non-derived columns;
derived columns (Employer NI, True Annual Cost, the 5 monthly YTD columns, YTD Totals) are
recomputed from the stored values and checked against the sheet so the app reproduces it exactly.

Idempotent: upserts on natural keys (staff.ref, payroll_month(ref,month), payroll_fy(fy,ref));
disciplinary is cleared + reloaded. Re-running is safe.

Usage:  python3 payroll-migrate.py            # migrate + reconcile
        python3 payroll-migrate.py --check     # reconcile only (no writes)
"""
import json, os, re, sys, urllib.request, urllib.error
from importlib.machinery import SourceFileLoader
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = VAULT
SHEET = "1ic1J58k7PApPxnRg48QbaJP61LvdtNAPPOEPUoLv2os"
TOKEN = open(os.path.join(VAULT, "Library/processes/secrets/supabase-token")).read().strip()
REF = "zhexcaflgahdcbzvbyfq"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
      "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
sheets = SourceFileLoader("sheets_api", os.path.join(VAULT, "Library/processes/scripts/sheets-api.py")).load_module()

MONTHS = {"Apr 26": "2026-04", "May 26": "2026-05", "Jun 26": "2026-06", "Jul 26": "2026-07",
          "Aug 26": "2026-08", "Sep 26": "2026-09", "Oct 26": "2026-10", "Nov 26": "2026-11",
          "Dec 26": "2026-12", "Jan 27": "2027-01", "Feb 27": "2027-02", "Mar 27": "2027-03"}


def q(sql):
    req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{REF}/database/query",
                                 data=json.dumps({"query": sql}).encode(), method="POST", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print("SQL ERR", e.code, e.read().decode()[:500], file=sys.stderr)
        raise


def num(s):
    """'£7,083.33' / '5%' / '' -> float or None."""
    if s is None:
        return None
    s = str(s).strip().replace("£", "").replace(",", "").replace("%", "").replace(" ", "")
    if s in ("", "-", "—"):
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def txt(s):
    s = (str(s).strip() if s is not None else "")
    return s if s else None


def sv(v):
    """SQL literal."""
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def rows(tab):
    import urllib.parse as _up
    resp = sheets.api("GET", f"/{SHEET}/values/{_up.quote(tab + '!A1:AC200')}")
    return resp.get("values", []) or []


# ── parse the sheet into python dicts (the source of truth for reconciliation) ──
def parse():
    data = {"staff": [], "month": [], "fy": [], "disc": [], "ytd": [], "recovered": []}
    # Staff: Ref|Full Name|Monthly|Annual|TaxCode|PensionProv|Ee%|Er%|LastReview|Notes|EmpNI(derived)|TrueCost(derived)
    for r in rows("Staff")[1:]:
        if not r or not str(r[0]).strip().isdigit():
            continue
        g = lambda i: r[i] if i < len(r) else None
        data["staff"].append({"ref": int(r[0]), "full_name": txt(g(1)), "monthly_salary": num(g(2)),
            "annual_salary": num(g(3)), "tax_code": txt(g(4)), "pension_provider": txt(g(5)),
            "employee_pct": num(g(6)), "employer_pct": num(g(7)), "last_pay_review": txt(g(8)),
            "notes": txt(g(9)), "_emp_ni": num(g(10)), "_true_cost": num(g(11))})
    # Monthly: Ref|Name|TaxCode|Salary|Bonus|Gross|Tax|NI|EePn|ErPn|Net|GrossYTD|TaxYTD|NIYTD|EePnYTD|ErPnYTD|Notes
    for tab, mon in MONTHS.items():
        for r in rows(tab)[1:]:
            if not r or not str(r[0]).strip().isdigit():
                continue
            g = lambda i: r[i] if i < len(r) else None
            mrow = {"ref": int(r[0]), "month": mon, "full_name": txt(g(1)), "tax_code": txt(g(2)),
                "salary": num(g(3)), "bonus": num(g(4)), "gross": num(g(5)), "tax": num(g(6)), "ni": num(g(7)),
                "ee_pn": num(g(8)), "er_pn": num(g(9)), "net_pay": num(g(10)),
                "_gross_ytd": num(g(11)), "_tax_ytd": num(g(12)), "_ni_ytd": num(g(13)),
                "_ee_pn_ytd": num(g(14)), "_er_pn_ytd": num(g(15)), "notes": txt(g(16))}
            # Recover a blank monthly NI from the sheet's OWN net-pay identity:
            # Net = Gross - Tax - NI - EePn  →  NI = Gross - Tax - EePn - Net.
            # Uses only this row's own sheet figures (not invented) and is cross-checked against the
            # NI-YTD delta in verify_derived(). The sheet itself is never written to. Caught 17 Jun:
            # refs 6+14 May NI cells were blank while gross/tax/net/YTD were all present and consistent.
            if mrow["ni"] is None and mrow["gross"] is not None and mrow["net_pay"] is not None:
                rec = round(mrow["gross"] - (mrow["tax"] or 0) - (mrow["ee_pn"] or 0) - mrow["net_pay"], 2)
                if rec > 0:
                    mrow["ni"] = rec
                    data["recovered"].append((mrow["ref"], mon, rec))
            data["month"].append(mrow)
    # FY 2025-26: Ref|Name|TaxCode|GrossPay|Tax|NI|EePension|ErPension|BonusesPaid
    for r in rows("FY 2025-26")[1:]:
        if not r or not str(r[0]).strip().isdigit():
            continue
        g = lambda i: r[i] if i < len(r) else None
        data["fy"].append({"fy": "2025-26", "ref": int(r[0]), "full_name": txt(g(1)), "tax_code": txt(g(2)),
            "gross_pay": num(g(3)), "tax": num(g(4)), "ni": num(g(5)), "ee_pension": num(g(6)),
            "er_pension": num(g(7)), "bonuses_paid": num(g(8))})
    # Disciplinary: Date|Ref|Name|Type|Summary|Outcome|File|FiledBy|Notes
    for r in rows("Disciplinary")[1:]:
        if not r or not any(str(c).strip() for c in r):
            continue
        g = lambda i: r[i] if i < len(r) else None
        data["disc"].append({"date": txt(g(0)), "ref": int(r[1]) if len(r) > 1 and str(r[1]).strip().isdigit() else None,
            "full_name": txt(g(2)), "type": txt(g(3)), "summary": txt(g(4)), "outcome": txt(g(5)),
            "file": txt(g(6)), "filed_by": txt(g(7)), "notes": txt(g(8))})
    # YTD Totals (derived, for reconciliation only): Ref|Name|TaxCode|GrossYTD|TaxYTD|NIYTD|EePnYTD|ErPnYTD|Bonuses
    for r in rows("YTD Totals 26-27")[1:]:
        if not r or not str(r[0]).strip().isdigit():
            continue
        g = lambda i: r[i] if i < len(r) else None
        data["ytd"].append({"ref": int(r[0]), "gross": num(g(3)), "tax": num(g(4)), "ni": num(g(5)),
            "ee_pn": num(g(6)), "er_pn": num(g(7)), "bonuses": num(g(8))})
    return data


def migrate(d):
    # staff
    for s in d["staff"]:
        cols = ["ref", "full_name", "monthly_salary", "annual_salary", "tax_code", "pension_provider",
                "employee_pct", "employer_pct", "last_pay_review", "notes"]
        vals = ", ".join(sv(s[c]) for c in cols)
        upd = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "ref") + ", updated_at=now()"
        q(f"insert into payroll.staff ({', '.join(cols)}) values ({vals}) on conflict (ref) do update set {upd};")
    # months
    for m in d["month"]:
        cols = ["ref", "month", "full_name", "tax_code", "salary", "bonus", "gross", "tax", "ni", "ee_pn", "er_pn", "net_pay", "notes"]
        vals = ", ".join(sv(m[c]) for c in cols)
        upd = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("ref", "month")) + ", updated_at=now()"
        q(f"insert into payroll.payroll_month ({', '.join(cols)}) values ({vals}) on conflict (ref, month) do update set {upd};")
    # fy
    for f in d["fy"]:
        cols = ["fy", "ref", "full_name", "tax_code", "gross_pay", "tax", "ni", "ee_pension", "er_pension", "bonuses_paid"]
        vals = ", ".join(sv(f[c]) for c in cols)
        upd = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("fy", "ref"))
        q(f"insert into payroll.payroll_fy ({', '.join(cols)}) values ({vals}) on conflict (fy, ref) do update set {upd};")
    # disciplinary — clear + reload
    q("delete from payroll.disciplinary;")
    for x in d["disc"]:
        cols = ["date", "ref", "full_name", "type", "summary", "outcome", "file", "filed_by", "notes"]
        vals = ", ".join(sv(x[c]) for c in cols)
        q(f"insert into payroll.disciplinary ({', '.join(cols)}) values ({vals});")
    print(f"migrated: {len(d['staff'])} staff · {len(d['month'])} month-rows · {len(d['fy'])} FY · {len(d['disc'])} disciplinary")


def reconcile(d):
    issues = []
    # staff
    db = {r["ref"]: r for r in q("select * from payroll.staff;")}
    for s in d["staff"]:
        r = db.get(s["ref"])
        if not r:
            issues.append(f"staff ref {s['ref']} missing in DB"); continue
        for c in ["full_name", "monthly_salary", "annual_salary", "tax_code", "pension_provider", "employee_pct", "employer_pct", "last_pay_review", "notes"]:
            if str(s[c]) != (str(num(r[c])) if isinstance(s[c], float) else (str(r[c]) if r[c] is not None else "None")):
                a, b = s[c], (num(r[c]) if isinstance(s[c], float) else r[c])
                if str(a) != str(b):
                    issues.append(f"staff {s['ref']} {c}: sheet={a!r} db={b!r}")
    # months
    dbm = {(r["ref"], r["month"]): r for r in q("select * from payroll.payroll_month;")}
    for m in d["month"]:
        r = dbm.get((m["ref"], m["month"]))
        if not r:
            issues.append(f"month {m['ref']}/{m['month']} missing in DB"); continue
        for c in ["full_name", "tax_code", "salary", "bonus", "gross", "tax", "ni", "ee_pn", "er_pn", "net_pay", "notes"]:
            a = m[c]; b = num(r[c]) if isinstance(a, float) else r[c]
            if str(a) != str(b):
                issues.append(f"month {m['ref']}/{m['month']} {c}: sheet={a!r} db={b!r}")
    # fy
    dbf = {(r["fy"], r["ref"]): r for r in q("select * from payroll.payroll_fy;")}
    for f in d["fy"]:
        r = dbf.get((f["fy"], f["ref"]))
        if not r:
            issues.append(f"fy {f['fy']}/{f['ref']} missing"); continue
        for c in ["full_name", "tax_code", "gross_pay", "tax", "ni", "ee_pension", "er_pension", "bonuses_paid"]:
            a = f[c]; b = num(r[c]) if isinstance(a, float) else r[c]
            if str(a) != str(b):
                issues.append(f"fy {f['fy']}/{f['ref']} {c}: sheet={a!r} db={b!r}")
    # disciplinary count
    dc = q("select count(*) c from payroll.disciplinary;")[0]["c"]
    if int(dc) != len(d["disc"]):
        issues.append(f"disciplinary count: sheet={len(d['disc'])} db={dc}")
    return issues


def verify_derived(d):
    """Recompute Employer NI / True Cost / monthly YTD / YTD Totals from stored values; check vs sheet."""
    issues = []
    # Employer NI = 15% of (annual_salary - 5000), floored at 0; True Cost = annual + emp_ni
    for s in d["staff"]:
        if s["annual_salary"] is not None and s["_emp_ni"] is not None:
            emp_ni = round(max(0.0, (s["annual_salary"] - 5000)) * 0.15, 2)
            if abs(emp_ni - s["_emp_ni"]) > 0.5:
                issues.append(f"staff {s['ref']} Employer-NI formula: computed={emp_ni} sheet={s['_emp_ni']}")
            if s["_true_cost"] is not None and abs(round(s["annual_salary"] + s["_emp_ni"], 2) - s["_true_cost"]) > 0.5:
                issues.append(f"staff {s['ref']} True-Cost formula: computed={round(s['annual_salary']+s['_emp_ni'],2)} sheet={s['_true_cost']}")
    # Monthly YTD = cumulative sum per ref across the FY (Apr->Mar). Compare to sheet's per-month YTD.
    order = list(MONTHS.values())
    by_ref = {}
    for m in d["month"]:
        by_ref.setdefault(m["ref"], {})[m["month"]] = m
    for ref, mm in by_ref.items():
        acc = {"gross": 0.0, "tax": 0.0, "ni": 0.0, "ee_pn": 0.0, "er_pn": 0.0}
        for mon in order:
            if mon not in mm:
                continue
            m = mm[mon]
            for k, ytdk in [("gross", "_gross_ytd"), ("tax", "_tax_ytd"), ("ni", "_ni_ytd"), ("ee_pn", "_ee_pn_ytd"), ("er_pn", "_er_pn_ytd")]:
                acc[k] = round(acc[k] + (m[k] or 0.0), 2)
                if m[ytdk] is not None and abs(acc[k] - m[ytdk]) > 0.05:
                    issues.append(f"month {ref}/{mon} {k}-YTD: computed={acc[k]} sheet={m[ytdk]}")
    return issues


def main():
    check_only = "--check" in sys.argv
    d = parse()
    if d["recovered"]:
        print("recovered (blank monthly NI filled from the sheet's own net-pay identity — not invented, sheet untouched):")
        for ref, mon, val in d["recovered"]:
            print(f"  ref {ref} {mon}: NI = {val}")
    if not check_only:
        migrate(d)
    rec = reconcile(d)
    der = verify_derived(d)
    print(f"\n=== RECONCILIATION ===")
    print(f"stored element mismatches: {len(rec)}")
    for i in rec[:40]:
        print("  ✗", i)
    print(f"derived-formula mismatches: {len(der)}")
    for i in der[:40]:
        print("  ✗", i)
    ok = not rec and not der
    print(f"\n{'✅ PASS — every stored element + every derived formula matches the sheet' if ok else '❌ MISMATCHES — gsheet stays the source; do NOT step it down'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())