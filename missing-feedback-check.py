#!/usr/bin/env python3
"""missing-feedback-check.py — which delivered courses got no evaluation feedback back?

WHY THIS EXISTS
  The Hub's "Missing feedback" tab has read "0 events to chase" since 23 Jun 2026 — not because
  feedback was complete, but because the thing that worked it out was switched off and never
  replaced. The old version read TRAINER CALENDARS via a cached JSON file that stopped being
  written; on Railway that file never existed at all, so the detector silently produced nothing and
  the dashboard published a confident zero. Staff were told weekly that there was nothing to chase.

THE RULE THIS IMPLEMENTS (Pete, 20 Jul 2026)
  - The MASTER TRAINING SHEET is the source of truth for what actually ran. Calendars are the
    trainer-facing mirror. So this reads the sheet, not diaries.
  - ONE DELIVERY = one (trainer, date, course). An open course is many booking rows and ONE
    delivery — the 13 Jul public course is 12 rows.
  - "Just check if any feedback is back, then we know it was attempted." ANY response against a
    delivery clears it. No per-customer chasing, deliberately: volumes are low and the whole
    pipeline moves to the Platform this year.
  - Rows whose trainer is not a platform trainer are SKIPPED (this is how Pete's own rare
    deliveries are excluded — expressed as a rule, so it holds for anyone else too).

  Report-only. It never writes. Exit 0 = ran, 2 = nothing to compare against (see below).

USAGE
  VAULT=/tmp/pbs python3 missing-feedback-check.py                 # default window
  VAULT=/tmp/pbs python3 missing-feedback-check.py --from 2026-06-01 --to 2026-06-30
  VAULT=/tmp/pbs python3 missing-feedback-check.py --json
"""
import argparse, datetime, importlib.util, json, os, sys, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PORTAL_REF = "rsczwfstwkthaybxhszy"
# Feedback lands a day or two late, so a delivery is not "missing" the moment it finishes.
GRACE_DAYS = 3


def _load(mod, path):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(VAULT, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _supabase_token():
    """Resolve the Supabase token the way the rest of the estate does: env var FIRST, then the
    materialised file, then the CC secrets table.

    Why the order matters: a Railway cron gets SUPABASE_TOKEN as an env var and does NOT
    necessarily have the file — railway-bootstrap only writes files for SECRETFILE__* vars. Reading
    the file first (or only) means the job dies on the container with FileNotFoundError while
    working perfectly on a laptop. Caught 20 Jul 2026 before any cron ran, not after.
    """
    import os as _o
    t = (_o.environ.get("SUPABASE_TOKEN") or "").strip()
    if t:
        return t
    p = f"{_o.environ.get('VAULT', '/tmp/pbs')}/Library/processes/secrets/supabase-token"
    if _o.path.exists(p):
        return open(p).read().strip()
    # Last resort: the CC secrets table, reachable from any container that has the CC keys.
    import json as _j, urllib.request as _u
    kp = f"{_o.environ.get('VAULT', '/tmp/pbs')}/Library/processes/secrets/command-centre-supabase-keys.json"
    url = _o.environ.get("CC_SUPABASE_URL"); key = _o.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        d = _j.loads(open(kp).read()); url, key = d["url"], d["service_role_key"]
    r = _u.Request(url.rstrip("/") + "/rest/v1/secrets?select=value&name=eq.supabase-token",
                   headers={"apikey": key, "Authorization": "Bearer " + key})
    return _j.loads(_u.urlopen(r, timeout=30).read())[0]["value"].strip()


def _portal(sql):
    tok = _supabase_token()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PORTAL_REF}/database/query",
        data=json.dumps({"query": sql}).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=90).read())


def platform_trainers():
    """Canonical trainer names from the Platform — the SSOT. Anyone not here is not chased."""
    rows = _portal("SELECT full_name FROM hub.staff_directory "
                   "WHERE trainer_id IS NOT NULL AND COALESCE(employment_status,'') <> 'Left'")
    return {r["full_name"].strip().lower() for r in rows}


def deliveries_from_master(date_from, date_to):
    """One entry per (trainer, date, course) from the master sheet — the source of truth."""
    ta = _load("training_audit", "training-audit.py")
    import openpyxl
    wb = openpyxl.load_workbook(ta.download_master(), data_only=True)
    seen = {}
    for tab in wb.sheetnames:
        for row in ta.parse_master_sheet(wb, tab):
            trainer = (row.get("trainer") or "").strip()
            course = (row.get("course") or "").strip()
            for d in (row.get("dates") or []):
                if not (date_from <= d <= date_to):
                    continue
                key = (trainer.lower(), d.isoformat(), course.lower())
                e = seen.setdefault(key, {"trainer": trainer, "date": d.isoformat(),
                                          "course": course, "customers": set()})
                if row.get("company"):
                    e["customers"].add(str(row["company"]).strip())
    return seen


def feedback_keys(date_from, date_to):
    """(trainer, date) pairs that have at least one evaluation response.

    Read from the normalised dataset the evaluation pipeline builds. If it is not present this
    returns None and the caller REFUSES to report — a missing input must never render as a clean
    zero, which is the exact defect this script replaces."""
    dd = os.environ.get("EVAL_DATA_DIR") or f"{VAULT}/Properties/Sygma Solutions Website/data/training-evaluations"
    path = os.path.join(dd, "all-normalised.json")
    if not os.path.exists(path):
        return None
    got = set()
    for r in json.load(open(path)):
        t, d = (r.get("trainer") or "").strip().lower(), (r.get("date_uk") or "").strip()
        if t and d and date_from.isoformat() <= d <= date_to.isoformat():
            got.add((t, d))
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from")
    ap.add_argument("--to", dest="d_to")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    today = datetime.date.today()
    d_to = datetime.date.fromisoformat(a.d_to) if a.d_to else today - datetime.timedelta(days=GRACE_DAYS)
    d_from = datetime.date.fromisoformat(a.d_from) if a.d_from else d_to - datetime.timedelta(days=60)

    trainers = platform_trainers()
    deliveries = deliveries_from_master(d_from, d_to)
    got = feedback_keys(d_from, d_to)

    if got is None:
        print("REFUSING TO REPORT: no normalised evaluation dataset found, so 'nothing missing' would "
              "be meaningless rather than clean. Run the evaluation sync first, or point "
              "EVAL_DATA_DIR at its output.", file=sys.stderr)
        return 2

    missing, skipped, cancelled = [], [], []
    for (tl, d, _c), e in sorted(deliveries.items(), key=lambda kv: kv[1]["date"]):
        if tl not in trainers:
            skipped.append(e); continue          # not a platform trainer — Pete's rare ones land here
        if (tl, d) in got:
            continue                              # ANY feedback back = attempted = covered
        # A course that did not run cannot have feedback. Chasing it wastes someone's morning and
        # teaches them the list is noise. Found on the first hand-check: 7 of 59 June flags were
        # cancellations, e.g. "Clancy - Cancelled when Mark arrived, No Delegates".
        blob = " ".join(e["customers"]).lower()
        if "cancel" in blob or "no delegate" in blob:
            cancelled.append(e); continue
        missing.append(e)

    if a.json:
        print(json.dumps({"window": [d_from.isoformat(), d_to.isoformat()],
                          "deliveries": len(deliveries), "missing": len(missing),
                          "skipped_non_trainer": len(skipped), "skipped_cancelled": len(cancelled),
                          "rows": [{**m, "customers": sorted(m["customers"])} for m in missing]}, indent=1))
        return 0

    print(f"=== Missing feedback — deliveries {d_from} to {d_to} (grace {GRACE_DAYS}d) ===")
    print(f"  {len(deliveries)} deliveries on the master sheet · {len(missing)} to chase "
          f"· {len(cancelled)} skipped (cancelled / no delegates) "
          f"· {len(skipped)} skipped (trainer not on the platform)")
    for m in missing:
        cust = ", ".join(sorted(m["customers"]))[:60]
        print(f"   {m['date']}  {m['trainer']:20s} {(m['course'] or '?')[:34]:34s} {cust}")
    if skipped:
        print(f"\n  skipped (not a platform trainer, so never chased):")
        for s in skipped:
            print(f"   {s['date']}  {s['trainer']:20s} {(s['course'] or '?')[:34]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
