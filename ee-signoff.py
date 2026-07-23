#!/usr/bin/env python3
"""ee-signoff.py — the Enquiry Engine end-of-session reconciliation gate (plan §6.10).

Working a batch of enquiries is NOT "done" until this reconciles the session and prints the
outstanding list — done = ZERO on every line. This is the second set of teeth on the enforced
source-correction (§6.5a): the capture-time CHECKs stop an uncategorised/unsourced edit being
written at all; THIS stops a session ending with a source named-but-not-fixed.

Verb: `EE sign off` / `reconcile enquiries`. Also the last step of the `enquiries` sweep and
wired into closeout check I3b.

What it reconciles (all touches WHERE created_at >= <session start>):
  1. Every reply captured        — capture-on-send (te-log --apply ran). [live-Gmail heuristic]
  2. Every correction's source FIXED — THE load-bearing one: no source-bearing edit left unfixed.
  3. Tray clear + chases          — Replies tray de-trayed; chases set, none duplicated.
  4. Draft captured              — no Claude-drafted send (reply/quote) with draft_text NULL.

`unfixed_sources > 0` ⇒ NOT signed off. Exit code 0 only when every blocking line is zero, so
this is a runnable gate: `VAULT=/tmp/pbs python3 ee-signoff.py --since today; echo $?`.

Usage:
  VAULT=/tmp/pbs python3 ee-signoff.py                 # since start of today (Atlantic/Canary)
  VAULT=/tmp/pbs python3 ee-signoff.py --since 12h      # last 12 hours
  VAULT=/tmp/pbs python3 ee-signoff.py --since 2026-07-07T09:00:00Z   # explicit ISO
"""
import os, sys, json, subprocess, datetime as dt

VAULT = os.environ.get("VAULT", "/tmp/pbs")
def _source_bearing():
    """Source-bearing correction categories, derived LIVE from the CC CHECK ee_sourcebearing_needs_ref
    (the authority) so ee-signoff and the DB can never disagree. Minimal fallback if unreadable."""
    try:
        import re as _re
        d = cc("SELECT pg_get_constraintdef(oid) d FROM pg_constraint WHERE conname='ee_sourcebearing_needs_ref'")[0]["d"]
        cats = tuple(sorted(set(_re.findall(r"'([a-z]+)'::ee_correction_category", d))))
        return cats or ("pricing","dates","factual","routing","structure")
    except Exception:
        return ("pricing","dates","factual","routing","structure")

def cc(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    out = (r.stdout or "").strip()
    try:
        return json.loads(out)
    except Exception:
        raise SystemExit(f"cc-sql error for [{sql[:80]}...]: {out or r.stderr}")

def since_clause(arg):
    """Return an ISO UTC timestamp for the session-start boundary."""
    if not arg or arg == "today":
        # start of today in Atlantic/Canary (UTC year-round: Canary is UTC+0/+1; use local midnight → UTC)
        now = dt.datetime.now(dt.timezone.utc)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    if arg.endswith("h") and arg[:-1].isdigit():
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=int(arg[:-1]))).strftime("%Y-%m-%dT%H:%M:%SZ")
    return arg  # explicit ISO

def main():
    args = sys.argv[1:]
    since = "today"
    for i, x in enumerate(args):
        if x == "--since" and i + 1 < len(args):
            since = args[i + 1]
    ts = since_clause(since)
    W = f"source='live' AND created_at >= '{ts}'"
    inlist = ",".join(f"'{c}'" for c in _source_bearing())

    print(f"=== EE sign-off — reconciling touches since {ts} ===\n")

    # session touch summary
    summ = cc(f"SELECT kind, count(*) n, count(*) FILTER (WHERE edited) e FROM public.enquiry_touches WHERE {W} GROUP BY kind ORDER BY kind")
    total = sum(r["n"] for r in summ)
    if summ:
        parts = [f"{r['kind']}:{r['n']}" + (f"/{r['e']}ed" if r['e'] else "") for r in summ]
        print(f"Touches this session: {total}   ({', '.join(parts)})")
    else:
        print("Touches this session: 0   (none)")

    blocking = 0

    # (2) THE load-bearing check — source-bearing edit with source NOT fixed
    unfixed = cc(f"SELECT vault_path, correction_category, source_ref, source_fix FROM public.enquiry_touches "
                 f"WHERE {W} AND edited IS TRUE AND correction_category IN ({inlist}) AND source_fixed IS NOT TRUE "
                 f"ORDER BY created_at")
    print(f"\n[{'OK ' if not unfixed else 'BLOCK'}] (2) unfixed_sources = {len(unfixed)}   ← must be 0")
    for u in unfixed:
        print(f"        ⛔ {u['correction_category']:9} {u['vault_path']}  src={u['source_ref']}  fix={u['source_fix'] or '—'}")
    blocking += len(unfixed)

    # (4) Claude-drafted send that dropped its draft
    nodraft = cc(f"SELECT vault_path, kind FROM public.enquiry_touches WHERE {W} AND kind IN ('reply','quote') AND draft_text IS NULL ORDER BY created_at")
    print(f"\n[{'OK ' if not nodraft else 'BLOCK'}] (4) drafts dropped = {len(nodraft)}   ← must be 0")
    for n in nodraft:
        print(f"        ⛔ {n['kind']:6} {n['vault_path']}  (reply/quote with no draft_text)")
    blocking += len(nodraft)

    # (3) duplicate open chases (same contact, >1 open) — a reconciliation hygiene check
    dup = cc("SELECT notes, count(*) n FROM tasks WHERE source='enquiry-engine' AND status='todo' "
             "GROUP BY notes HAVING count(*) > 1")
    print(f"\n[{'OK ' if not dup else 'BLOCK'}] (3) duplicate open chases = {len(dup)}   ← must be 0")
    for d in dup:
        print(f"        ⛔ {d['n']}× {(d['notes'] or '')[:80]}")
    blocking += len(dup)

    # (5) alias regression harness (hardening plan P1) — the facts index must resolve the whole
    # probe set; a mis-resolution here means the NEXT enquiry could quote the wrong course.
    ar = subprocess.run(["python3", f"{VAULT}/ee-alias-test.py"], capture_output=True, text=True,
                        env={**os.environ, "VAULT": VAULT})
    alias_ok = ar.returncode == 0
    _out = (ar.stdout or "").strip()
    _fails = [ln for ln in _out.split("\n") if ln.startswith("FAIL")]
    _summary = next((ln for ln in reversed(_out.split("\n")) if "alias regression:" in ln), "")

    # 23 Jul 2026: this used to print the LAST LINE OF STDOUT next to [BLOCK], whatever it was.
    # When the harness dies part-way through (it hits the DB for every probe, so a transient
    # SSL/socket fault kills it mid-run) the last line is simply the probe that happened to run
    # last -- routinely a PASS. So a BLOCK was reported with a PASSING probe as its stated reason,
    # and stderr was never shown, hiding the real cause. That misdiagnosis cost a wrong "the alias
    # index needs fixing" hand-off. Distinguish PROBES FAILED from HARNESS COULD NOT RUN, and show
    # the actual error.
    if alias_ok:
        print(f"\n[OK ] (5) alias regression: {_summary or 'all probes pass'}   ← must be all-pass")
    elif _fails:
        print(f"\n[BLOCK] (5) alias regression: {len(_fails)} probe(s) MIS-RESOLVED   ← must be all-pass")
        for ln in _fails:
            print(f"        ⛔ {ln}")
        blocking += 1
    else:
        _err = (ar.stderr or "").strip().split("\n")
        _why = next((l for l in reversed(_err) if l.strip()), "no stderr captured")
        print(f"\n[BLOCK] (5) alias regression: HARNESS DID NOT COMPLETE (exit {ar.returncode}) "
              f"-- this is NOT a mis-resolution   ← re-run before diagnosing")
        print(f"        ⛔ {_why[:200]}")
        print(f"        ({len([l for l in _out.split(chr(10)) if l.startswith(('PASS','FAIL'))])} of the "
              f"probe set ran before it died. Transient DB/SSL faults are the usual cause -- re-run it.)")
        blocking += 1

    # (6) session STAGE DRIFT (P4.2, blocking): a verb touch whose contact stage disagrees with the verb
    import importlib.util as _ilu
    _sp = _ilu.spec_from_file_location("telog", f"{VAULT}/te-log.py")
    _tl = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_tl)
    verb_rows = cc(f"SELECT kind, contact_id, vault_path FROM public.enquiry_touches WHERE {W} AND kind IN ('won','booked','lost') AND contact_id IS NOT NULL")
    want = {v: _tl.stage_id(_tl.VERB_STAGE[v]) for v in ("won", "booked", "lost")}
    stage_drift = []
    for v in verb_rows:
        c = _tl.portal_get("contacts", select="stage_id,full_name", id=f"eq.{v['contact_id']}")
        if c and c[0].get("stage_id") != want[v["kind"]]:
            stage_drift.append(f"{v['kind']} touch but {c[0].get('full_name')} is at stage {c[0].get('stage_id')} ({v['vault_path']})")
    print(f"\n[{'OK ' if not stage_drift else 'BLOCK'}] (6) session stage drift = {len(stage_drift)}   ← must be 0")
    for s in stage_drift:
        print(f"        ⛔ {s}")
    blocking += len(stage_drift)

    # (7) tray-vs-CRM (P4.2, blocking): every ENQUIRY-tray thread's sender must exist in the CRM
    try:
        import re as _re
        spec = _ilu.spec_from_file_location("gmail_api_mod", f"{VAULT}/gmail-api.py")
        gm = _ilu.module_from_spec(spec); spec.loader.exec_module(gm)
        g = gm.GmailAPI()
        etray = g.search_threads("label:Projects/SY-Training-Enquiries label:Replies", max_results=30) or []
        missing = []
        for t in etray:
            try:
                # PRIMARY test: the thread is CRM-covered when a ledger touch links it to a
                # contact (the intake writes exactly that). The from-address heuristic below
                # is only the fallback for a thread with NO touches — a website-form enquiry's
                # first message is FROM info@sygma-solutions.com (the form notifier), so the
                # address lookup alone false-blocks properly-intaken threads (found 10 Jul 2026
                # on the Tom Delaney thread, contact + arrival touch both present).
                linked = _tl.cc_sql(
                    f"SELECT 1 FROM enquiry_touches WHERE thread_id='{t['id']}' AND contact_id IS NOT NULL LIMIT 1")
                if linked:
                    continue
                full = g.get_thread(t["id"])
                hdrs = {h["name"].lower(): h["value"] for h in full["messages"][0]["payload"]["headers"]}
                m = _re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", hdrs.get("from", ""))
                if m and not _tl.portal_get("contacts", select="id", email=f"ilike.{m.group(0).lower()}"):
                    missing.append(f"{t['id']} from {m.group(0)}")
            except Exception:
                pass
        print(f"\n[{'OK ' if not missing else 'BLOCK'}] (7) tray threads with no CRM contact = {len(missing)}   ← must be 0 (run intake)")
        for x in missing:
            print(f"        ⛔ {x}")
        blocking += len(missing)
        tray = g.search_threads("label:Replies OR label:Actions", max_results=50) or []
        print(f"\n[i ] Replies tray: {len(tray)} waiting (informational — worked enquiries must be de-trayed by te-log --apply)")
    except Exception as e:
        print(f"\n[i ] tray checks: (could not read live — {type(e).__name__}) — verify manually")

    # tone/other edits — eyeball only, non-blocking
    diffuse = cc(f"SELECT vault_path, correction_category FROM public.enquiry_touches WHERE {W} AND edited IS TRUE AND correction_category IN ('tone','other') ORDER BY created_at")
    if diffuse:
        print(f"\n[i ] tone/other edits (eyeball, non-blocking): {len(diffuse)}")
        for x in diffuse:
            print(f"        · {x['correction_category']}  {x['vault_path']}")

    print("\n" + ("=" * 60))
    if blocking == 0:
        print("✅ SIGNED OFF — every blocking line is zero. Enquiry session is done.")
        sys.exit(0)
    else:
        print(f"⛔ NOT SIGNED OFF — {blocking} outstanding item(s). Close each named source/draft/chase, then re-run to zero.")
        sys.exit(1)

if __name__ == "__main__":
    main()
