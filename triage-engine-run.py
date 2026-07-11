#!/usr/bin/env python3
# CRON-META
# what: The Triage Engine offline runner — pulls new inbox mail (enriched), classifies facts-first, writes PROPOSAL rows, executes only what Pete has explicitly enabled (L2 auto-file / L3 auto-draft / L4 shadow-then-live per fact), queues everything else (enquiries ALWAYS queue), and delivers the daily-digest sweep with undo links. Writes a heartbeat digest row every window even when it did nothing — the empty digest IS the liveness signal triage-reconcile watches.
# why: Triage value while Pete is offline WITHOUT crossing the line: the engine PREPARES; Pete decides everything irreversible. All auto flags default FALSE — a fresh deploy of this runner proposes and heartbeats, nothing more ([[triage-engine-design]]).
# reads: Gmail (in:inbox, enriched via triage-pull --full), triage_routing_facts, triage_decisions, config (triage-auto-mode re-read before EVERY action)
# writes: triage_decisions (proposals + auto rows), triage_digests, Gmail (ONLY for Pete-enabled facts, lint-gated), CC daily_log
# entity: personal
# schedule: 0 6,10,14,18,22 * * *
# timezone: Atlantic/Canary
# secrets: GOOGLE_SA_JSON, SUPABASE_TOKEN
# CRON-META-END
"""triage-engine-run.py -- the Triage Engine offline runner (P5).

Pipeline per run:
  1. PULL      -- triage-pull --full semantics (per-message enrichment: real addresses,
                  To/Cc, automated-origin headers, message_id, Authentication-Results,
                  body text, attachment flag, prior-Pete-outbound)
  2. STATE-FIRST -- skip any message whose ledger row is non-pending (re-encounter = no-op)
  3. CLASSIFY  -- facts-first (triage-action-classify), ask from content ALWAYS
  4. PROPOSE   -- L1: decision rows decided_by='cron-proposed' (zero mutation)
  5. EXECUTE   -- only for Pete-enabled facts, each action:
                  kill switch re-read -> lint -> write-order (row 'applying' FIRST) -> mutate
                  L2 auto-file  (fact.auto_file_enabled)
                  L3 auto-draft (fact.auto_send_enabled -- a held Gmail draft, basis_refs banked)
                  L4 send       (fact.auto_send_enabled: SHADOW rows; + fact.auto_send_live:
                                 real send via the outbox pattern, to the whitelisted address)
                  ENQUIRIES ALWAYS QUEUE -- never auto-routed offline (the lint blocks them
                  on every auto path anyway; the runner never even attempts)
  6. DIGEST    -- assemble_digest('runner'): a SWEEP of all undigested action rows; the row
                  writes even when empty (heartbeat). Post-check: delivery failure or
                  3 consecutive unreviewed action-carrying digests -> pause auto-mode + ping.

Run by hand: VAULT=/tmp/pbs python3 /tmp/pbs/triage-engine-run.py [--limit N]
"""
import os, sys, json, subprocess

sys.path.insert(0, os.environ.get("VAULT", "/tmp/pbs"))
import importlib
tl = importlib.import_module("triage_lib")

UNREVIEWED_PAUSE_N = 3


def q(v):
    return "NULL" if v is None else "'" + tl.esc(v) + "'"


def load_classifier():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tac", os.path.join(tl.VAULT, "triage-action-classify.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_lint():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tlint", os.path.join(tl.VAULT, "triage-lint.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def pull(limit):
    r = subprocess.run(["python3", os.path.join(tl.VAULT, "triage-pull.py"), "in:inbox",
                        str(limit), "--full"], capture_output=True, text=True,
                       env={**os.environ, "VAULT": tl.VAULT})
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 60
    tac, tlint = load_classifier(), load_lint()
    threads = pull(limit)
    report = [f"runner: {len(threads)} inbox thread(s) pulled"]
    proposed = acted = queued = 0

    for t in threads:
        mid = t.get("message_id") or f"thread:{t['id']}"
        # 2. state-first: non-pending row => full no-op
        rows = tl.cc_sql(f"SELECT decided_by, overridden, apply_status, send_status "
                         f"FROM triage_decisions WHERE message_id='{tl.esc(mid)}'")
        if rows:
            r0 = rows[0]
            pending = (r0["decided_by"] in ("cron-proposed", "cron-auto") and not r0["overridden"]
                       and r0["apply_status"] is None and r0["send_status"] is None)
            if not pending:
                continue
        # 3. classify (facts route, content classifies)
        sender = t.get("sender_addr") or ""
        route = tac.facts_route(sender)
        draft = tac.classify_inbox([{
            "thread_id": t["id"],
            "messages": [{"from": t.get("from", ""), "subject": t.get("subject", ""),
                          "body": t.get("body_text", "")}]}], use_facts=False)[0]
        ask = draft.get("ask_classification")
        fact_id = route.get("fact_id")

        # 4. propose (L1) -- idempotent
        if not rows:
            tl.cc_sql("INSERT INTO triage_decisions (thread_id, sender, message_id, fact_id, "
                      "proposed_ask, proposed_verb, proposed_label, decided_by) VALUES ("
                      f"{q(t['id'])}, {q(sender)}, {q(mid)}, {q(fact_id)}, {q(ask)}, "
                      f"{q(route.get('fact_verb') or ('File' if route.get('fact_label') else None))}, "
                      f"{q(route.get('fact_label'))}, 'cron-proposed') "
                      "ON CONFLICT (message_id) DO NOTHING")
            proposed += 1

        # 5. execute -- ONLY Pete-enabled facts; enquiries always queue
        if not fact_id:
            queued += 1
            continue
        fact = tl.cc_sql(f"SELECT * FROM triage_routing_facts WHERE id='{fact_id}'")[0]
        action_base = {"thread_id": t["id"], "message_id": mid, "sender": sender,
                       "subject": t.get("subject", ""), "body_text": t.get("body_text", ""),
                       "ask": ask,
                       "label": fact.get("gmail_label"),
                       "headers": {"to": t.get("to", []), "cc": t.get("cc", []),
                                    "auto_submitted": t.get("auto_submitted", ""),
                                    "precedence": t.get("precedence", ""),
                                    "list_id": t.get("list_id", ""),
                                    "authentication_results": t.get("authentication_results", ""),
                                    "reply_to": t.get("reply_to", "")},
                       "has_attachment": t.get("has_attachment", False),
                       "prior_pete_outbound": t.get("prior_pete_outbound", False)}

        # ---- L2 auto-file ----
        if fact.get("auto_file_enabled"):
            if not tl.auto_mode_on():
                report.append("KILL SWITCH off — batch halted mid-run"); break
            ok, lint_report = tlint.lint(dict(action_base, level="L2"))
            if ok:
                tl.cc_sql("UPDATE triage_decisions SET decided_by='cron-auto', "
                          f"final_ask={q(ask)}, final_verb='File', final_label={q(fact['gmail_label'])}, "
                          f"apply_status='applying', lint_passed=true, "
                          f"lint_report='{tl.esc(json.dumps(lint_report))}'::jsonb, "
                          f"basis_refs=ARRAY['fact:{fact_id}']::text[] "
                          f"WHERE message_id='{tl.esc(mid)}'")
                try:
                    g = tl.gmail()
                    labels = {l["name"]: l["id"] for l in g.list_labels()}
                    add = [labels[fact["gmail_label"]]] if fact.get("gmail_label") in labels else []
                    g.modify_thread(t["id"], add=add or None, remove=["INBOX"])
                    tl.cc_sql(f"UPDATE triage_decisions SET apply_status='applied', applied_at=now() "
                              f"WHERE message_id='{tl.esc(mid)}'")
                    acted += 1
                except Exception as e:
                    report.append(f"  ✗ L2 {t['id'][:12]}: {e} (row left 'applying' — digest surfaces it)")
            else:
                tl.cc_sql(f"UPDATE triage_decisions SET lint_passed=false, "
                          f"lint_report='{tl.esc(json.dumps(lint_report))}'::jsonb "
                          f"WHERE message_id='{tl.esc(mid)}'")
                queued += 1
            continue

        # ---- L3 / L4 (auto_send_enabled facts) ----
        if fact.get("auto_send_enabled"):
            tmpl = tl.cc_sql("SELECT * FROM triage_templates WHERE template_slug='ack-basic'")
            basis = [f"tmpl:ack-basic", f"fact:{fact_id}"]
            draft_text = tmpl[0]["body"] if tmpl else ""
            act = dict(action_base, level="L4", basis_refs=basis, draft_text=draft_text)
            if not tl.auto_mode_on():
                report.append("KILL SWITCH off — batch halted mid-run"); break
            ok, lint_report = tlint.lint(act)
            if not ok:
                tl.cc_sql(f"UPDATE triage_decisions SET lint_passed=false, "
                          f"lint_report='{tl.esc(json.dumps(lint_report))}'::jsonb "
                          f"WHERE message_id='{tl.esc(mid)}'")
                queued += 1
                continue
            if not fact.get("auto_send_live"):
                # SHADOW: the full ledger row IS the shadow log; no Gmail call ever made
                tl.cc_sql("UPDATE triage_decisions SET decided_by='cron-auto', "
                          f"final_verb='Reply', send_status='shadow', lint_passed=true, "
                          f"lint_report='{tl.esc(json.dumps(lint_report))}'::jsonb, "
                          f"basis_refs=ARRAY['tmpl:ack-basic','fact:{fact_id}']::text[] "
                          f"WHERE message_id='{tl.esc(mid)}'")
                acted += 1
            else:
                # LIVE: outbox pattern -- 'sending' BEFORE the Gmail call, 'sent' after
                tl.cc_sql("UPDATE triage_decisions SET decided_by='cron-auto', "
                          f"final_verb='Reply', send_status='sending', lint_passed=true, "
                          f"lint_report='{tl.esc(json.dumps(lint_report))}'::jsonb, "
                          f"basis_refs=ARRAY['tmpl:ack-basic','fact:{fact_id}']::text[] "
                          f"WHERE message_id='{tl.esc(mid)}'")
                try:
                    g = tl.gmail()
                    g.reply_thread(t["id"], draft_text)   # threads to the whitelisted sender
                    tl.cc_sql(f"UPDATE triage_decisions SET send_status='sent' "
                              f"WHERE message_id='{tl.esc(mid)}'")
                    acted += 1
                except Exception as e:
                    report.append(f"  ✗ L4 send {t['id'][:12]}: {e} (row stuck 'sending' — digest surfaces, never auto-retried)")
            continue

        queued += 1

    # 6. digest -- the sweep + heartbeat
    did, n, delivered = tl.assemble_digest(kind="runner")
    report.append(f"proposed {proposed} · acted {acted} · queued {queued} · "
                  f"digest {str(did)[:8]} ({n} actions, delivered={delivered})")

    # digest post-check: delivery failure or N consecutive unreviewed -> pause + ping
    if n > 0 and not delivered:
        tl.trip_kill_switch("digest delivery FAILED — the recovery surface must never fail silently")
        report.append("⛔ paused auto-mode: digest delivery failed")
    unrev = tl.cc_sql("SELECT count(*) AS n FROM (SELECT reviewed_at FROM triage_digests "
                      "WHERE action_count > 0 ORDER BY created_at DESC LIMIT %d) t "
                      "WHERE reviewed_at IS NULL" % UNREVIEWED_PAUSE_N)[0]["n"]
    if unrev >= UNREVIEWED_PAUSE_N and tl.get_config("triage-auto-mode") == "on":
        tl.trip_kill_switch(f"{unrev} consecutive action-carrying digests unreviewed")
        report.append("⛔ paused auto-mode: digests going unreviewed")

    tl.log_daily("triage-engine-run", "\n".join(report))
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
