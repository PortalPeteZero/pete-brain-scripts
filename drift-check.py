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

It also runs `gmail-filter-parity.py --json` (added 27 Jul 2026) and folds its gap count in, on the
same report-only contract. That gate exists because a filter reading `from:<Pete's own address> →
Briefings` with no subject condition mislabelled 2,194 messages over four weeks and survived a
session that was actively investigating a different Briefings filter. A check that only runs when
someone thinks to run it would have missed it exactly as the humans did.
"""
# CRON-META
# what: weekly self-check of the Command Centre's own health (drift-check)
# why: surface migration/health regressions (drive mislabels, failed/overdue crons, stalled drives, un-embedded notes) automatically instead of by accident
# reads: drive_files, crons, drive_change_tokens, vault_notes, secrets, helpers (via connection-parity.py), Gmail filter settings (via gmail-filter-parity.py), Google Contacts mirror + Odoo res.partner (via people.py check)
# writes: daily_log (drift-check summary) + refreshes crons.expected_interval_hours
# entity: cc
# report: automations-log
# schedule: 0 9 * * 0
# timezone: Atlantic/Canary
# CRON-META-END
import os, sys, json, re, subprocess, datetime

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
    #    The gate reads each table's STORED generated column content_hash (= md5(embed_input(...)),
    #    computed on write) — same rule as cc-embedder.py and public.semantic_stale_count().
    stale_bits = []
    for t in ("vault_notes", "tasks", "notes"):
        r = q(f"SELECT count(*) c FROM {t} WHERE content_hash IS NOT NULL AND (embedding IS NULL OR embedded_hash IS DISTINCT FROM content_hash)")
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

    # Gmail-filter parity — REPORT-ONLY, same contract as connection-parity above. Added 27 Jul 2026
    # after a filter created 1 Jul (`from:<Pete's own address>` → Briefings, no subject condition)
    # mislabelled 2,194 messages and survived a 2 Jul session that was investigating a DIFFERENT
    # Briefings filter. Three careful sessions could not see it; a weekly machine check can.
    # A failure to RUN is reported as a failure, never folded into the clean line.
    try:
        gr = subprocess.run([sys.executable, os.path.join(VAULT, "gmail-filter-parity.py"), "--json"],
                            capture_output=True, text=True, timeout=120,
                            env={**os.environ, "VAULT": VAULT})
        gdata = json.loads(gr.stdout or "{}")
        ggaps = gdata.get("gaps", [])
        nchecked = gdata.get("filters_checked", 0)
        if not gr.stdout.strip():
            findings.append(("⚠", f"Gmail-filter parity: check produced no output — treat as NOT RUN ({(gr.stderr or '')[:160]})"))
        elif ggaps:
            codes = ", ".join(sorted({g["code"] for g in ggaps}))
            sample = "; ".join(f"{g['code']} {g['detail'][:90]}" for g in ggaps[:3])
            findings.append(("⚠", f"Gmail-filter parity: {len(ggaps)} gap(s) [{codes}] across {nchecked} filter(s) — "
                                  f"run `gmail-filter-parity.py` in a session to fix. e.g. {sample}"))
        else:
            findings.append(("✓", f"Gmail-filter parity: 0 gaps ({nchecked} filters — no over-broad self match, "
                                  f"no overlap, Briefings still Mode A)"))
    except Exception as e:
        findings.append(("⚠", f"Gmail-filter parity: check did not run ({e}) — this is NOT a pass"))

    # Known-wrong names — REPORT-ONLY. Added 6 Aug 2026.
    # A Plaud transcript rendered Kier as "Kears" on 23 July. It escaped into Roy Cotterill's
    # contact record and two Clancy write-ups and sat there as fact for a fortnight, while the SAME
    # account spelled Kier correctly on two other contacts. One company, one account, two names, so
    # a search for Kier found half the picture.
    # A lesson note about Plaud mangling names already existed and did not stop it, because it
    # covered PEOPLE and this was a COMPANY, and because a note is something somebody has to
    # remember to read. Pete: "Don't make a memory -- FIX THE PROCESS." So it runs weekly here.
    try:
        nr = subprocess.run([sys.executable, os.path.join(VAULT, "entity-name-check.py")],
                            capture_output=True, text=True, timeout=300,
                            env={**os.environ, "VAULT": VAULT})
        out = nr.stdout or ""
        if "0 known-wrong names" in out:
            findings.append(("✓", "Known-wrong names: none anywhere they would be read as fact"))
        else:
            m = re.search(r"(\d+) place\(s\) still carrying", out)
            n = m.group(1) if m else "some"
            findings.append(("⚠", f"Known-wrong names: {n} place(s) carry a name we KNOW is wrong "
                                  f"(a customer or person mis-transcribed). Run "
                                  f"`entity-name-check.py` for the list. Verbatim transcripts are "
                                  f"exempt and must never be edited."))
    except Exception as e:
        findings.append(("⚠", f"Known-wrong names: check did not run ({e}) — this is NOT a pass"))

    # People hygiene — REPORT-ONLY, same contract as the two parity checks above. Added 6 Aug 2026.
    # Odoo (Canary Detect's accounting system) was carrying 340 contact rows whose NAME was a bare
    # email address, with no customer or supplier rank: 308 created in a single second on 23 Feb
    # 2026 and 32 more on 15 Mar 2023. The domains were Sygma's world — Cadent, BAM, Scottish Water,
    # Clancy, Morrison, Openreach. It sat there for six months because `people check` only ever read
    # the Google Contacts mirror, which is the DOWNSTREAM store, so the two upstream business
    # systems were never examined by anything.
    # It was not cosmetic: `people find` truthfully reported Clancy staff as living in Canary
    # Detect's system, and a bare first-name row BLOCKED creating a real Clancy manager as a
    # duplicate. Pete, 6 Aug 2026: "why does odoo hold clancy info, that shouldnt happen."
    # A weekly machine check is the only thing that finds an import nobody remembers running.
    try:
        # --no-record: people check writes its own daily_log row, and drift-check already writes one.
        # It prints the HUMAN report first and the JSON last, unlike the parity tools above, so take
        # the last JSON-looking line rather than parsing the whole of stdout.
        pr2 = subprocess.run([sys.executable, os.path.join(VAULT, "people.py"), "check", "--json", "--no-record"],
                             capture_output=True, text=True, timeout=180,
                             env={**os.environ, "VAULT": VAULT})
        jline = next((l for l in reversed((pr2.stdout or "").splitlines()) if l.strip().startswith("{")), "")
        if not jline:
            findings.append(("⚠", f"People hygiene: check produced no JSON — treat as NOT RUN ({(pr2.stderr or '')[:160]})"))
        else:
            pdata = json.loads(jline)
            addr = pdata.get("address_shaped_names", 0)
            dupes = pdata.get("gaps", 0) - addr
            if addr:
                findings.append(("⚠", f"People hygiene: {addr} business-store contact(s) NAMED BY AN EMAIL ADDRESS "
                                      f"with no customer/supplier rank — unprocessed import sitting in a live "
                                      f"accounting system. Run `people check` for the dates, then verify nothing "
                                      f"references them before removing anything."))
            if dupes > 0:
                findings.append(("⚠", f"People hygiene: {dupes} duplicate/overlap gap(s) on the phone mirror — run `people check`"))
            if not addr and dupes <= 0:
                findings.append(("✓", f"People hygiene: 0 gaps ({pdata.get('contacts', 0)} phone records, "
                                      f"no address-shaped names in the business stores)"))
    except Exception as e:
        findings.append(("⚠", f"People hygiene: check did not run ({e}) — this is NOT a pass"))

    # Documented-command rot — do the commands our own notes tell sessions to run still exist?
    # A dead flag is silent (an argparse-less script swallows it and does something else), so this
    # only ever surfaces when someone follows the doc and reports work that never happened.
    # See doc-command-check.py; born 19 Jul 2026 from `garmin-daily-cc.py --publish-only`.
    try:
        dr = subprocess.run([sys.executable, os.path.join(VAULT, "doc-command-check.py"), "--json"],
                            capture_output=True, text=True, timeout=180)
        ddata = json.loads(dr.stdout or "{}")
        n = ddata.get("count", 0)
        if n:
            sample = "; ".join(
                f"{f['script']}{' ' + f['flag'] if f['flag'] else ''} (in {f['note'][:40]})"
                for f in ddata.get("findings", [])[:4])
            findings.append(("⚠", f"Documented commands: {n} broken reference(s) — a note tells sessions to "
                                  f"run something that no longer exists. e.g. {sample}"))
        else:
            findings.append(("✓", f"Documented commands: all valid ({ddata.get('notes_scanned', 0)} notes scanned)"))
    except Exception as e:
        findings.append(("⚠", f"Documented commands: check did not run ({e})"))

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
