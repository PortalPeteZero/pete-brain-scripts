#!/usr/bin/env python3
"""drift-check.py — weekly self-check of the Command Centre's own health (Phase 5.4).

Re-runs the cheap, DB-queryable half of the migration's Phase-0 sweep every week so regressions
surface on their own instead of being found by accident:
  - drive-index mislabels (a row whose parent is in a DIFFERENT drive — the My Drive bug class)
  - automations that have failed or gone overdue vs their expected interval
  - a stalled drive-index (a change-token that hasn't advanced)
  - un-embedded knowledge (semantic search would silently miss it)
It also refreshes each cron's expected_interval_hours (schedules may have changed).

Report-only: writes a dated summary to daily_log (cron_name='drift-check') + prints it. The only
writes are the interval refresh + the log row. Findings are sorted into OK / NEEDS-ATTENTION so the
weekly read is "what's wrong", not a to-do list. Deeper repo-grep checks (orphan data files, stale
doc refs) are out of scope here — this is the always-on DB watch.
"""
# CRON-META
# what: weekly self-check of the Command Centre's own health (drift-check)
# why: surface migration/health regressions (drive mislabels, failed/overdue crons, stalled drives, un-embedded notes) automatically instead of by accident
# reads: drive_files, crons, drive_change_tokens, vault_notes
# writes: daily_log (drift-check summary) + refreshes crons.expected_interval_hours
# entity: cc
# report: automations-log
# schedule: 0 9 * * 0
# timezone: Atlantic/Canary
# CRON-META-END
import os, sys, json, subprocess, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
CC_SQL = os.path.join(VAULT, "cc-sql.py")

def q(sql):
    r = subprocess.run([sys.executable, CC_SQL, sql], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=90)
    if r.returncode != 0:
        sys.stderr.write(f"cc-sql error: {(r.stderr or r.stdout)[:300]}\n")
        return []
    out = (r.stdout or "").strip()
    try:
        d = json.loads(out); return d if isinstance(d, list) else []
    except json.JSONDecodeError:
        return []

def main():
    # 0. refresh expected_interval_hours (idempotent)
    try:
        subprocess.run([sys.executable, os.path.join(VAULT, "cron-set-intervals.py")],
                       env={**os.environ, "VAULT": VAULT}, timeout=120, capture_output=True)
    except Exception as e:
        sys.stderr.write(f"interval refresh skipped: {e}\n")

    findings = []   # (severity, line)

    # 1. drive-index mislabels — any row whose parent is in a different drive
    mis = q("SELECT p.drive AS parent_drive, count(*) c FROM drive_files a "
            "JOIN drive_files p ON p.drive_file_id=a.parent_id WHERE a.drive<>p.drive "
            "GROUP BY p.drive ORDER BY 2 DESC")
    total_mis = sum(int(r["c"]) for r in mis)
    if total_mis:
        findings.append(("⚠", f"Drive index: {total_mis} mislabelled rows (parent in another drive): "
                              + ", ".join(f"{r['c']}→{r['parent_drive']}" for r in mis[:5])))
    else:
        findings.append(("✓", "Drive index: no mislabelled rows"))

    # 2. failed + overdue automations
    failed = q("SELECT key FROM crons WHERE enabled IS NOT FALSE AND last_status IS NOT NULL "
               "AND upper(last_status)<>'SUCCESS' ORDER BY key")
    overdue = q("SELECT key, last_run_at::date d, expected_interval_hours h FROM crons "
                "WHERE enabled IS NOT FALSE AND last_run_at IS NOT NULL AND expected_interval_hours IS NOT NULL "
                "AND last_run_at < now() - (expected_interval_hours*1.5||' hours')::interval ORDER BY key")
    if failed:
        findings.append(("⚠", f"Automations FAILED ({len(failed)}): " + ", ".join(r["key"] for r in failed[:8])))
    if overdue:
        findings.append(("⚠", f"Automations OVERDUE ({len(overdue)}): "
                              + ", ".join(f"{r['key']}(last {r['d']})" for r in overdue[:8])))
    if not failed and not overdue:
        findings.append(("✓", "Automations: none failed or overdue"))

    # 3. stalled drives
    stale = q("SELECT drive, updated_at::timestamp(0) u FROM drive_change_tokens "
              "WHERE updated_at < now() - interval '90 min' ORDER BY updated_at")
    if stale:
        findings.append(("⚠", f"Drive watch STALLED ({len(stale)}): "
                              + ", ".join(f"{r['drive']}(since {r['u']})" for r in stale)))
    else:
        findings.append(("✓", "Drive watch: all drives polling"))

    # 4. un-embedded knowledge
    un = q("SELECT count(*) c FROM vault_notes WHERE embedding IS NULL")
    nun = int(un[0]["c"]) if un else 0
    findings.append((("⚠" if nun else "✓"), f"Knowledge: {nun} un-embedded notes" if nun else "Knowledge: all embedded"))

    warns = [f for f in findings if f[0] == "⚠"]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (f"DRIFT-CHECK {stamp} — {'⚠ ' + str(len(warns)) + ' need attention' if warns else '✓ all clear'}")
    body = header + "\n" + "\n".join(f"  {sev} {line}" for sev, line in findings)
    print(body)

    # report-only: record the weekly result in daily_log
    today = datetime.date.today().isoformat()
    safe = body.replace("$$", "")
    q(f"INSERT INTO daily_log (date, cron_name, content) VALUES ('{today}','drift-check',$$%s$$)" % safe)

    # heartbeat
    try:
        sys.path.insert(0, VAULT)
        import cc_publish
        cc_publish.pulse("drift-check", header)
    except Exception:
        pass

if __name__ == "__main__":
    main()
