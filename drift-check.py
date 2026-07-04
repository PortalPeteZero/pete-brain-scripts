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
weekly read is "what's wrong", not a to-do list.

It also runs `connection-parity.py --json` (the connection-updater backstop) and folds the gap
count into the digest — still REPORT-ONLY: parity FIXES (which need repo writes + re-ingest) are
escalated to a session, never performed here. connection-parity is dual-runtime safe (DB legs run
in the container; its P5 repo-leg self-skips when the container has no .git and says so in the
digest). Deeper repo-grep checks beyond parity remain out of scope — this is the always-on DB watch.
"""
# CRON-META
# what: weekly self-check of the Command Centre's own health (drift-check)
# why: surface migration/health regressions (drive mislabels, failed/overdue crons, stalled drives, un-embedded notes) automatically instead of by accident
# reads: drive_files, crons, drive_change_tokens, vault_notes, secrets, helpers (via connection-parity.py)
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

    # 4. semantic-layer freshness — HASH GATE (catches stale-but-present vectors, not just NULLs) across
    #    all three embedding tables; plus a DEAD-MAN on the freshness cron itself. The cron's own
    #    SUCCESS-but-stale alert lives inside that cron, so it shares the cron's failure domain — if the
    #    cron dies entirely, staleness accrues with no alert. This external cross-check closes that gap.
    #    (Source: public.crons only — cron_events carries only lifecycle kinds, no per-run success signal.)
    GATE = {"vault_notes": "embed_input(title,body)", "tasks": "embed_input(name,notes)", "notes": "embed_input(title,body)"}
    stale_bits = []
    for t, ei in GATE.items():
        r = q(f"SELECT count(*) c FROM {t} WHERE length({ei})>0 AND (embedding IS NULL OR embedded_hash IS DISTINCT FROM md5({ei}))")
        c = int(r[0]["c"]) if r else 0
        if c: stale_bits.append(f"{t}={c}")
    if stale_bits:
        findings.append(("⚠", "Semantic layer STALE (content≠embedding): " + ", ".join(stale_bits)))
    else:
        findings.append(("✓", "Semantic layer: all embeddings current (hash gate = 0)"))
    ki = q("SELECT last_status, last_run_at::timestamp(0) r, (last_run_at < now() - interval '26 hours') AS overdue "
           "FROM crons WHERE key='knowledge-reindex'")
    if ki:
        row = ki[0]
        if str(row.get("last_status") or "").upper() != "SUCCESS" or row.get("overdue"):
            findings.append(("⚠", f"knowledge-reindex freshness cron unhealthy: last_status={row.get('last_status')}, "
                                  f"last_run={row.get('r')} — semantic staleness may be accruing UNALERTED"))
        else:
            findings.append(("✓", "knowledge-reindex freshness cron: healthy (recent SUCCESS)"))

    # Connection-registry parity (connection-updater backstop) — REPORT-ONLY: classify into the
    # digest, never fix here (fixes need repo writes + re-ingest, escalated to a session). The
    # parity script is dual-runtime safe: DB legs run everywhere; its P5 repo-leg self-reports
    # `SKIPPED (no .git…)` when the container lacks git history — that INFO line is exactly the
    # empirical .git check, surfaced in the weekly read.
    try:
        pr = subprocess.run([sys.executable, os.path.join(VAULT, "connection-parity.py"), "--json"],
                            capture_output=True, text=True, timeout=120)
        pdata = json.loads(pr.stdout or "{}")
        ngaps = pdata.get("gaps", 0)
        if ngaps:
            types = ", ".join(pdata.get("gap_types", []))
            sample = "; ".join(f"{f['rule']} {f['subject']}" for f in pdata.get("findings", [])[:4])
            findings.append(("⚠", f"Connection parity: {ngaps} gap(s) [{types}] — run `connection-parity.py` in a session to fix. e.g. {sample}"))
        else:
            findings.append(("✓", "Connection parity: 0 gaps (secrets ↔ registry ↔ config notes ↔ helpers consistent)"))
        for inf in pdata.get("info", []):
            findings.append(("ℹ", f"Connection parity {inf['subject']}: {inf['detail']}"))
    except Exception as e:
        findings.append(("⚠", f"Connection parity: check did not run ({e})"))

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
