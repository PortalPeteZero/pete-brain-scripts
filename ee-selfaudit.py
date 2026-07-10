#!/usr/bin/env python3
# CRON-META
# what: Weekly Enquiry Engine self-audit — re-runs the alias regression harness, the 10-fact SSOT spot checks, and ledger↔CRM parity, and reports green/red to the morning brief (daily_log). Audits the auditor so the hardening work doesn't rot.
# why: The 2026-07-09 audit found the previous build plan had drifted invisibly (facts index unreachable for sold courses, contradictory fee rules across notes). A weekly mechanical re-check catches rot the week it appears, not months later (hardening plan P4.4).
# reads: CC vault_notes (ee-alias-regression, ee-pricing) + enquiry_touches, Portal public.courses + contact_activities, the live sygma-solutions.com/agendas index
# writes: CC daily_log (cron_name='ee-selfaudit', one report per run)
# entity: sygma
# schedule: 0 7 * * 1
# timezone: Atlantic/Canary
# secrets: SUPABASE_TOKEN, SECRETFILE__sygma-portal-supabase-keys__json
# CRON-META-END
"""ee-selfaudit.py — weekly EE self-audit (hardening plan P4.4).

Checks (each one line, green/red):
  1. alias regression harness — every probe resolves as expected
  2. SSOT spot list — the D1 conditional fee is vault-wide-consistent; core £ figures present in ee-pricing
  3. facts-in-code — ee-facts.py still holds zero hardcoded course facts
  4. ledger parity — last 7 days: enquiry_touches reply/quote rows ↔ Engine CRM activities
  5. send discipline — post-P3 sends all carry retrieval receipt + lint pass

Run by hand:  VAULT=/tmp/pbs python3 /tmp/pbs/ee-selfaudit.py [--dry]
"""
import os, sys, json, subprocess, datetime as dt, importlib.util, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")

def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

tl = _load("telog", f"{VAULT}/te-log.py")

def main():
    dry = "--dry" in sys.argv
    lines, red = [], 0

    # 1. alias harness
    r = subprocess.run(["python3", f"{VAULT}/ee-alias-test.py"], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    ok = r.returncode == 0
    tail = (r.stdout or "").strip().split("\n")[-1]
    lines.append(("✅" if ok else "🔴") + f" alias regression: {tail}")
    red += 0 if ok else 1

    # 2. SSOT spot checks
    pricing = tl.cc_sql("SELECT body FROM vault_notes WHERE slug='ee-pricing'")[0]["body"]
    core_ok = all(x in pricing for x in ("£965", "£1,930", "£145", "£35pp")) and "back to back" in pricing.lower()
    sweep = tl.cc_sql("SELECT slug FROM vault_notes WHERE body ILIKE '%ONE EUSR fee (%' OR body ILIKE '%included in the combined quote%' OR body ILIKE '%included in the quoted cost%' ORDER BY slug")
    allowed = {"ee-hardening-plan", "ee-audit-findings-2026-07-09", "enquiry-engine-agenda-library-map"}
    sweep_bad = [r["slug"] for r in (sweep or []) if r["slug"] not in allowed]
    lines.append(("✅" if core_ok else "🔴") + " ee-pricing core figures + D1 conditional present")
    lines.append(("✅" if not sweep_bad else "🔴") + f" vault-wide fee sweep clean{'' if not sweep_bad else ': ' + ', '.join(sweep_bad)}")
    red += (0 if core_ok else 1) + (0 if not sweep_bad else 1)

    # 3. facts-in-code
    ef = open(f"{VAULT}/ee-facts.py").read()
    fic = ("MODEL = {" in ef) or ("SUPPORTING = {" in ef)
    lines.append(("✅" if not fic else "🔴") + " ee-facts.py holds zero hardcoded course facts")
    red += 0 if not fic else 1

    # 4. ledger parity (7 days)
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lrows = tl.cc_sql(f"SELECT count(*) n FROM enquiry_touches WHERE kind IN ('reply','quote') AND source='live' AND occurred_at >= '{cutoff}'")[0]["n"]
    arows = len(tl.portal_get("contact_activities", select="id", created_by_name="eq.Enquiry%20Engine",
                              occurred_at=f"gte.{cutoff}", activity_type="eq.email"))
    par_ok = abs(lrows - arows) <= 2
    lines.append(("✅" if par_ok else "🔴") + f" ledger parity 7d: {lrows} ledger reply/quote vs {arows} Engine email activities")
    red += 0 if par_ok else 1

    # 5. send discipline since P3 (2026-07-10)
    disc = tl.cc_sql("SELECT count(*) n FROM enquiry_touches WHERE kind IN ('reply','quote') AND source='live' "
                     "AND occurred_at > '2026-07-10T12:00:00Z' AND (retrieval_refs IS NULL OR cardinality(retrieval_refs)=0 OR lint_passed IS NOT TRUE)")[0]["n"]
    lines.append(("✅" if disc == 0 else "🔴") + f" sends missing retrieval-receipt or lint-pass since P3: {disc}")
    red += 0 if disc == 0 else 1

    body = f"## EE self-audit — {'ALL GREEN' if red == 0 else str(red) + ' RED'}\n" + "\n".join(f"- {l}" for l in lines)
    print(body)
    if not dry:
        today = dt.date.today().isoformat()
        tl.cc_sql(f"INSERT INTO daily_log (date, cron_name, content) VALUES ({tl.lit(today)}, 'ee-selfaudit', {tl.lit(body)})")
        print(f"\n✓ written to daily_log ({today})")
    sys.exit(0)

if __name__ == "__main__":
    main()
