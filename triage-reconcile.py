#!/usr/bin/env python3
# CRON-META
# what: Nightly Triage Engine reconciler — read-only audit of Gmail ↔ tasks ↔ tray ↔ the triage ledger, plain-English drift lines to the morning brief. Independently recomputes the kill-switch trips (unreviewed digests, undo counts, per-class send ceiling); its ONE permitted write is the trip itself (flip triage-auto-mode off + Telegram ping). Runner-liveness line arms only once the triage-engine-run cron is deployed.
# why: Drift is invisible until something compares the systems — the EE-P4 shape on the triage grain. The trips must be evaluated OUTSIDE the runner so a regressed runner cannot keep auto-acting unattended ([[triage-engine-design]]).
# reads: Gmail (Replies tray), CC public.tasks, triage_decisions, triage_digests, triage_sync_actions, crons, config
# writes: CC daily_log (cron_name='triage-reconcile'); ON TRIP ONLY: config triage-auto-mode + Telegram ping
# entity: personal
# schedule: 50 6 * * *
# timezone: Atlantic/Canary
# secrets: GOOGLE_SA_JSON, SUPABASE_TOKEN
# CRON-META-END
"""triage-reconcile.py -- the Triage Engine nightly drift reconciler (P4; ee-reconcile twin).

Drift classes (one plain-English line each; zero drift = one silent OK line):
  1. stuck-rows          -- decision rows in 'applying'/'sending' older than 2h (crash mid-mutation)
  2. undigested-applied  -- applied rows with digest_id NULL older than one runner window (the
                            digest-sweep backstop: the sweep itself regressed)
  3. tray-vs-ledger      -- a Replies-tray thread with no decision row at all (arrived + trayed
                            outside every capture path)
  4. sync-undone-noise   -- sync actions undone by Pete (surfaced so persistent wrongness is seen)
  5. runner-liveness     -- no triage digest in the last runner window (ARMED ONLY while the
                            triage-engine-run cron exists in the crons registry)
KILL-SWITCH TRIPS (recomputed independently of the runner; the one permitted write):
  T1. N consecutive unreviewed action-carrying digests (N=3)
  T2. digest-undo clicks per day (decisions.overridden_at + sync_actions.undone_at; N=3/day)
  T3. per-class daily send ceiling (send_status='sent' per fact_id per day; ceiling=5)

Run by hand: VAULT=/tmp/pbs python3 /tmp/pbs/triage-reconcile.py [--demo]
"""
import os, sys, datetime as dt

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")

UNREVIEWED_TRIP_N = 3
UNDO_TRIP_PER_DAY = 3
SEND_CEILING_PER_CLASS = 5
RUNNER_WINDOW_HOURS = 10   # 3x/day cadence → a digest at least every ~8h + slack


def drift_lines(demo_seed=False):
    lines, trips = [], []

    # 1. stuck rows
    stuck = tl.cc_sql("SELECT count(*) AS n FROM triage_decisions WHERE "
                      "(apply_status='applying' OR send_status='sending') AND created_at < now() - interval '2 hours'")[0]["n"]
    stuck += tl.cc_sql("SELECT count(*) AS n FROM triage_sync_actions WHERE apply_status='applying' "
                       "AND created_at < now() - interval '2 hours'")[0]["n"]
    if stuck:
        lines.append(f"stuck-rows: {stuck} row(s) crashed mid-mutation (applying/sending > 2h) — need a look.")

    # 2. digest-sweep backstop
    # v6: scope to CRON rows only -- manual pete rows legitimately carry digest_id NULL forever
    # (the digest sweep is a cron concern), so counting them was a permanent false drift signal.
    orphan = tl.cc_sql("SELECT count(*) AS n FROM triage_decisions WHERE apply_status='applied' "
                       "AND decided_by IN ('cron-auto','cron-proposed') "
                       f"AND digest_id IS NULL AND applied_at < now() - interval '{RUNNER_WINDOW_HOURS} hours'")[0]["n"]
    if orphan:
        lines.append(f"undigested-applied: {orphan} applied row(s) with digest_id NULL older than one "
                     f"runner window — the digest sweep itself may have regressed.")

    # 3. tray vs ledger
    try:
        g = tl.gmail()
        tray = g.search_threads("label:Replies", max_results=100)
        missing = 0
        for t in tray:
            if not tl.cc_sql(f"SELECT 1 FROM triage_decisions WHERE thread_id='{tl.esc(t['id'])}' LIMIT 1"):
                missing += 1
        if missing:
            lines.append(f"tray-vs-ledger: {missing} tray thread(s) with no decision row — arrived outside "
                         f"every capture path (informational until the runner ships proposals for all mail).")
    except Exception as e:
        lines.append(f"tray-vs-ledger: check failed ({e}) — Gmail unreachable?")

    # 4. sync undos (yesterday)
    undone = tl.cc_sql("SELECT count(*) AS n FROM triage_sync_actions WHERE undone_at >= now() - interval '1 day'")[0]["n"]
    if undone:
        lines.append(f"sync-undone: Pete undid {undone} sync action(s) in the last day — check the pattern.")

    # 5. runner liveness (armed only while the runner cron is registered)
    runner = tl.cc_sql("SELECT enabled FROM crons WHERE key='triage-engine-run'")
    if runner and runner[0]["enabled"]:
        recent = tl.cc_sql("SELECT count(*) AS n FROM triage_digests WHERE kind='runner' AND "
                           f"created_at > now() - interval '{RUNNER_WINDOW_HOURS} hours'")[0]["n"]
        if recent == 0:
            lines.append(f"runner-liveness: NO triage digest in the last {RUNNER_WINDOW_HOURS}h — "
                         f"the runner may have crashed silently. (Telegram-pinged.)")
            tl.tg_send("TRIAGE ENGINE: no runner digest in the last window — the offline runner "
                       "may be down. The morning brief has the line.")

    # ---- kill-switch trips (independent recomputation) ----
    unrev = tl.cc_sql("SELECT count(*) AS n FROM (SELECT reviewed_at FROM triage_digests "
                      "WHERE action_count > 0 ORDER BY created_at DESC LIMIT %d) t "
                      "WHERE reviewed_at IS NULL" % UNREVIEWED_TRIP_N)[0]["n"]
    if unrev >= UNREVIEWED_TRIP_N:
        trips.append(f"{unrev} consecutive action-carrying digests unreviewed")
    # v6: the T2 ledger arm is RETIRED until Phase 4 -- a manual Pete correction is NOT a
    # digest-undo click, and every correction-heavy manual session (the engine's whole point)
    # would false-trip the kill switch. Only the sync-actions undo arm remains.
    undos = tl.cc_sql("SELECT count(*) AS n FROM triage_sync_actions WHERE undone_at >= now() - interval '1 day'")[0]["n"]
    if undos >= UNDO_TRIP_PER_DAY:
        trips.append(f"{undos} digest-undo clicks in 24h (bound {UNDO_TRIP_PER_DAY})")
    ceil = tl.cc_sql("SELECT fact_id, count(*) AS n FROM triage_decisions WHERE send_status='sent' "
                     "AND decided_at >= now() - interval '1 day' GROUP BY fact_id HAVING count(*) > %d"
                     % SEND_CEILING_PER_CLASS)
    if ceil:
        trips.append(f"per-class send ceiling breached: {[(r['fact_id'][:8], r['n']) for r in ceil]}")

    return lines, trips


def main():
    demo = "--demo" in sys.argv
    if demo:
        print("P4 GATE DEMO — triage-reconcile")
        # seed one drift case: a stuck 'applying' row backdated 3h
        tl.cc_sql("DELETE FROM triage_decisions WHERE message_id='p4-demo-stuck-1'")
        tl.cc_sql("INSERT INTO triage_decisions (thread_id, message_id, decided_by, apply_status, created_at) "
                  "VALUES ('p4-demo-thread','p4-demo-stuck-1','cron-proposed','applying', now() - interval '3 hours')")
        print("  seeded: one stuck 'applying' row (backdated 3h)")

    lines, trips = drift_lines()
    report = ["## triage-reconcile — " + tl.today()]
    if not lines and not trips:
        report.append("Triage reconcile — zero drift. All systems agree.")
    else:
        report.extend("- " + l for l in lines)

    if trips and (tl.get_config("triage-auto-mode", "off") == "on"):
        tl.trip_kill_switch("; ".join(trips))
        report.append(f"⛔ KILL SWITCH TRIPPED: {'; '.join(trips)}")
    elif trips:
        report.append(f"(trip condition present but auto-mode already off: {'; '.join(trips)})")

    if demo:
        print("  drift caught (exact morning-brief format):")
        for l in report[1:]:
            print("   " + l)
        caught = any("stuck-rows" in l for l in lines)
        tl.cc_sql("DELETE FROM triage_decisions WHERE message_id='p4-demo-stuck-1'")
        lines2, _ = drift_lines()
        clean = not any("stuck-rows" in l for l in lines2)
        print(f"  seeded row removed → re-run: {'zero stuck-rows drift' if clean else 'STILL DIRTY'}")
        print(f"\nP4 RECONCILE GATE: {'PASS — seeded drift caught first run, zero when clean' if caught and clean else 'FAIL'}")
        return 0 if caught and clean else 1

    tl.log_daily("triage-reconcile", "\n".join(report))
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
