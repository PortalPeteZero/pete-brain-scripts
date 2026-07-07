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
SOURCE_BEARING = ("pricing", "dates", "factual", "routing", "structure")

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
    inlist = ",".join(f"'{c}'" for c in SOURCE_BEARING)

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

    # (1)+(3) Replies tray — informational live-Gmail heuristic (a lingering worked thread = a send
    #  not put through te-log, which auto-files on --apply). Fail-soft.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("gmail_api_mod", f"{VAULT}/gmail-api.py")
        gm = importlib.util.module_from_spec(spec); spec.loader.exec_module(gm)
        g = gm.GmailAPI()
        tray = g.search_threads("label:Replies OR label:Actions", max_results=50) if hasattr(g, "search_threads") else []
        n_tray = len(tray) if isinstance(tray, list) else 0
        print(f"\n[i ] Replies tray: {n_tray} waiting (informational — worked enquiries must be de-trayed by te-log --apply)")
    except Exception as e:
        print(f"\n[i ] Replies tray: (could not read live — {type(e).__name__}) — verify manually")

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
