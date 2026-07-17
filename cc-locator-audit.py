#!/usr/bin/env python3
# CRON-META: name=cc-locator-audit schedule="30 6 * * *" tz=Atlantic/Canary what="Report-only CC Locator drift check: unhomed tables + dead/stale data_map homes" produces="report (stdout / CC page)" consumes="information_schema,data_map"
"""cc-locator-audit.py — the CC Locator self-maintaining drift check (Pillar B / B2).

REPORT-ONLY (the house pattern, like connection-parity.py): it prints a report and exits with a
gap count. It NEVER writes tasks or mutates anything — gaps surface at closeout/briefing for Pete.

Answers Requirement #2 ("keeps itself updated, no reminders"): every day it reconciles the LIVE
system against data_map (the locator's SSOT) and flags drift, so a new kind or a rotted home is
caught automatically instead of waiting for someone to notice.

Checks:
  (a) COMPLETENESS — every populated public base table + view is either HOMED (its name appears
      in a data_map row's home/notes/backing_ref) or on the explicit INFRA allow-list. A new,
      unhomed, populated table is drift.
  (d) DEAD / STALE HOME — every data_map backing_ref of form `table:public.X` points at a table
      that EXISTS and is NON-EMPTY (existence alone misses the Daily-notes class = real-but-empty).

Usage:  VAULT=/tmp/pbs python3 /tmp/pbs/cc-locator-audit.py [--json]
        echo $?     # 0 = clean, N = number of gaps (report-only; never a task)
"""
import os, sys, json, subprocess, re

VAULT = os.environ.get("VAULT", "/tmp/pbs")

def q(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       env={**os.environ, "VAULT": VAULT}, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"[cc-locator-audit] query failed: {r.stderr[:160]}\n")
        return None            # None = errored (distinct from [] empty) so we never mis-report
    try:
        return json.loads(r.stdout)
    except Exception:
        return []

# Engine-internal tables that answer no "where does X live" question — intentionally NOT homed.
# (Membership tables of homed subsystems are covered by the subsystem's data_map text, not here.)
INFRA_ALLOW = {
    # pure engine-internal
    "access_audit", "agent_cron_prompts", "agent_jobs", "app_settings", "cc_map", "cron_events",
    "cron_state", "drive_change_tokens", "gtask_tombstones", "memory_chunks", "module_user_grants",
    "note_links", "profiles", "raw_captures", "tags", "tasks_premig_20260701", "user_groups",
    "groups", "triage_sync_actions",
    # the locator's own registries (covered by dedicated resolver blocks, not the data_map text)
    "data_map", "property_declarations", "property_state", "staff_directory",
    # members of homed subsystems (the anchor row homes the subsystem; these are covered by it)
    "account_config", "account_deliverables", "account_documents", "account_kpi", "account_meetings",
    "account_obligations", "account_risks", "account_state",                 # KAM (anchor: account_people)
    "ee_public_courses",                                                       # EE (anchor: enquiry_touches)
    "garmin_weekly_recovery",                                                  # Garmin view (anchor: garmin_daily)
    "triage_cases", "triage_templates",                                        # triage engine internals
    "damage_review_rules",                                                     # clancy (anchor: clancy_damages)
    "bank_account_history",                                                    # banking (anchor: bank_accounts)
    "training_rep", "training_session_code_map", "training_weekly_totals", "training_weekly_volume",  # training (anchor: training_session)
    "health_config", "health_planned_session", "health_weekly",               # PF (anchors: health_journal/feedback)
    "module_content",                                                          # pages (anchor: modules)
}

def main():
    as_json = "--json" in sys.argv
    gaps = []

    dm = q("SELECT domain, home, access, notes, backing_ref FROM data_map ORDER BY sort")
    tbls = q("SELECT c.relname AS name, c.relkind AS kind FROM pg_class c "
             "JOIN pg_namespace n ON n.oid=c.relnamespace "
             "WHERE n.nspname='public' AND c.relkind IN ('r','v','m') ORDER BY c.relname")
    if dm is None or tbls is None:
        print("cc-locator-audit: a lookup ERRORED — aborting (not reporting false drift). Re-run.")
        sys.exit(99)

    # (a) COMPLETENESS
    dm_text = " ".join((r.get("home") or "") + " " + (r.get("notes") or "") + " " + (r.get("backing_ref") or "")
                       for r in dm).lower()
    for t in tbls:
        name = t["name"]
        if name in INFRA_ALLOW:
            continue
        if name.lower() in dm_text:          # named anywhere in a data_map row = homed
            continue
        # populated? (skip genuinely empty internal tables)
        cnt = q(f"SELECT count(*) AS n FROM public.{name}")
        n = (cnt[0]["n"] if cnt else 0)
        if n and n > 0:
            gaps.append({"kind": "unhomed-table", "detail": f"{name} ({n} rows) is populated but has NO data_map home and is not on the infra allow-list", "severity": "medium"})

    # (d) DEAD / STALE HOME — backing_ref table:public.X must exist AND be non-empty
    for r in dm:
        ref = (r.get("backing_ref") or "")
        m = re.match(r"table:public\.([a-z_0-9]+)$", ref)
        if not m:
            continue
        tn = m.group(1)
        cnt = q(f"SELECT count(*) AS n FROM public.{tn}")
        if cnt is None:                      # table missing → query errors
            gaps.append({"kind": "dead-home", "detail": f"'{r['domain']}' → backing_ref {ref}, but that table does not exist (retired?)", "severity": "high"})
        elif not cnt or cnt[0]["n"] == 0:
            gaps.append({"kind": "empty-home", "detail": f"'{r['domain']}' → backing_ref {ref}, but that table is EMPTY (the Daily-notes/Asana class — home points at a table with no data)", "severity": "high"})

    if as_json:
        print(json.dumps({"gaps": gaps, "count": len(gaps)}, indent=1))
    else:
        print(f"=== CC Locator drift check (report-only) — {len(gaps)} gap(s) ===")
        for g in sorted(gaps, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x["severity"], 3)):
            print(f"  [{g['severity']:6}] {g['kind']}: {g['detail']}")
        if not gaps:
            print("  clean — every populated table is homed, and no data_map home points at a dead/empty table.")
        print("\n(report-only — surfaces at closeout/briefing; no tasks created)")

    sys.exit(len(gaps))

if __name__ == "__main__":
    main()
