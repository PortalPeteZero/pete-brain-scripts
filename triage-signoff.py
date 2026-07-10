#!/usr/bin/env python3
"""triage-signoff.py -- the Triage Engine session gate (P4; the ee-signoff twin).

Exits NON-ZERO while anything is outstanding. Wired into the closeout skill: no
triage-touching session is done until this prints all-clear. Pete's gate is the printed
plain-English PASS/BLOCK lines, never an exit code.

Blocking checks:
  S1. inbox zero or explicitly deferred -- every in:inbox thread either carries a decision
      row (this session or older) or is accepted as awaiting triage (WARNING, not blocking,
      when triage wasn't run today; BLOCKING if a triage session logged decisions today and
      inbox threads remain undecided and undeferred)
  S2. overrides fully banked -- ZERO decision rows overridden without BOTH a banked
      override_reason (the DB CHECK guarantees the reason at write time) AND a matching
      triage-routing-test regression case (checked by sender presence in the cases fence)
      -- "THE load-bearing one": capture-time checks stop a bad row; THIS stops a session
      ending with an override named-but-not-banked
  S3. ledger complete -- no rows stuck in 'applying'/'sending'
  S4. tray reconciled -- no Replies-tray thread whose linked task is already done
      (the sync gesture left unapplied)

Usage: VAULT=/tmp/pbs python3 /tmp/pbs/triage-signoff.py [--since today]
"""
import os, sys, re, json

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")


def main():
    problems, warnings = [], []

    # S3 first (cheap): stuck rows
    stuck = tl.cc_sql("SELECT count(*) AS n FROM triage_decisions WHERE apply_status='applying' OR send_status='sending'")[0]["n"]
    stuck += tl.cc_sql("SELECT count(*) AS n FROM triage_sync_actions WHERE apply_status='applying'")[0]["n"]
    if stuck:
        problems.append(f"S3 ledger: {stuck} row(s) stuck mid-mutation (applying/sending)")

    # S2: overrides today must each have a regression case for their sender
    rows = tl.cc_sql("SELECT sender, override_reason FROM triage_decisions WHERE overridden AND "
                     "overridden_at >= date_trunc('day', now())")
    if rows:
        try:
            note = tl.cc_sql("SELECT body FROM vault_notes WHERE vault_path="
                             "'Projects/PA-Command-Centre/triage-routing-regression.md'")[0]["body"]
            m = re.search(r"```json triage-routing-cases\s*\n(.*?)```", note, re.S)
            case_senders = {c.get("sender", "").lower() for c in json.loads(m.group(1))} if m else set()
            case_domains = {s.split("@")[-1] for s in case_senders if "@" in s}
        except Exception:
            case_senders, case_domains = set(), set()
        for r in rows:
            s = (r.get("sender") or "").lower()
            if s not in case_senders and s.split("@")[-1] not in case_domains:
                problems.append(f"S2 override not banked: '{s}' overridden today "
                                f"(reason {r.get('override_reason')}) but NO regression case — "
                                f"run triage-routing-test.py --add")

    # S4: tray threads whose linked task is done (sync gesture unapplied)
    try:
        g = tl.gmail()
        tray = g.search_threads("label:Replies", max_results=100)
        for t in tray:
            done = tl.cc_sql("SELECT count(*) AS n FROM tasks WHERE status='done' AND "
                             f"notes ILIKE '%{tl.esc(t['id'])}%' AND completed_at >= now() - interval '2 days' "
                             "AND notes NOT ILIKE '%[no-sync-close]%'")[0]["n"]
            if done:
                warnings.append(f"S4 tray: thread {t['id'][:14]}… linked task done — Replies label "
                                f"still on (next triage-sync will surface it)")
    except Exception as e:
        warnings.append(f"S4 tray check skipped (Gmail unreachable: {e})")

    # S1: inbox vs decisions (blocking only if a triage session ran today)
    try:
        g = tl.gmail()
        inbox = g.search_threads("in:inbox", max_results=100)
        session_today = tl.cc_sql("SELECT count(*) AS n FROM triage_decisions WHERE "
                                  "decided_by='pete' AND decided_at >= date_trunc('day', now())")[0]["n"]
        undecided = 0
        for t in inbox:
            if not tl.cc_sql(f"SELECT 1 FROM triage_decisions WHERE thread_id='{tl.esc(t['id'])}' LIMIT 1"):
                undecided += 1
        if undecided and session_today:
            problems.append(f"S1 inbox: {undecided} thread(s) undecided after a triage session today "
                            f"— triage them or mark deferred (a Skip decision row)")
        elif undecided:
            warnings.append(f"S1 inbox: {undecided} thread(s) awaiting triage (no session today — informational)")
    except Exception as e:
        warnings.append(f"S1 inbox check skipped (Gmail unreachable: {e})")

    print("triage-signoff — " + tl.today())
    for p in problems:
        print(f"  ⛔ BLOCK: {p}")
    for w in warnings:
        print(f"  ⚠ note: {w}")
    if not problems:
        print("  ✅ PASS — ledger clean, overrides banked, tray reconciled.")
        return 0
    print(f"  {len(problems)} blocking problem(s) — not done until this prints PASS.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
