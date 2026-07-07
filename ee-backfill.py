#!/usr/bin/env python3
"""ee-backfill.py — the LIGHT SEED backfill for the Enquiry Engine brain (plan §8, reframed 2026-07-07).

⛔ The big historical dig is DROPPED. This is a one-off light seed floored at 2026-06-01 (EE start):
reconcile the EE-era corpus (the ~74 `training-enquiries` notes already filed) into the new structure —
give each enquiry/enquiry-reply note a `source='backfill'` `enquiry_touches` row (draft_text NULL, so it
NEVER enters edit-rate maths — the live forward loop measures convergence). Corpus DEPTH comes from the
forward loop over time, not from mining history.

Two modes:
  • reconcile-corpus (DEFAULT) — CC-only, zero Gmail: link each existing 1-June+ corpus note to a
    backfill touch. Safe to --apply. This is the actual light seed.
  • mine (--source bot-form|inbound|label) — READ-ONLY Gmail scan → a Phase-0 candidate manifest for
    Pete to eyeball. NEVER auto-applies (the plan's hard human gate); --apply is refused here without
    an explicit --i-have-reviewed flag.

Hard guarantees (§8.6): --mine read-only is ALWAYS ON (no "hot" mode); no Gmail write path is ever
called; no chase tasks; every write emits to --manifest JSONL (reversible: touches by source='backfill').

Usage:
  VAULT=/tmp/pbs python3 ee-backfill.py                      # dry-run reconcile-corpus (default)
  VAULT=/tmp/pbs python3 ee-backfill.py --apply              # write the backfill touches
  VAULT=/tmp/pbs python3 ee-backfill.py --source bot-form --dry-run   # Gmail Phase-0 candidate manifest
"""
import os, sys, json, subprocess, datetime as dt

VAULT = os.environ.get("VAULT", "/tmp/pbs")
FLOOR = "2026-06-01"                      # decision #3: EE-start floor; below = pre-EE, do not load
OUT_DIR = "/tmp/ee-backfill"             # §8.9 WRITE-GUARD: hard-coded /tmp default, never cwd/project
MINE = True                              # §8.6 read-only guard — ALWAYS ON, no hot mode

def cc(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql], capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    out = (r.stdout or "").strip()
    try: return json.loads(out)
    except Exception: return out or (r.stderr or "").strip()

def lit(s):
    if s is None: return "NULL"
    if isinstance(s, bool): return "true" if s else "false"
    if isinstance(s, (int, float)): return str(s)
    return "'" + str(s).replace("'", "''") + "'"

def reconcile_corpus(apply, manifest):
    """Give each existing 1-June+ corpus enquiry/enquiry-reply note a source='backfill' touch row."""
    notes = cc(
        "SELECT slug, vault_path, type, frontmatter->>'date' AS d, frontmatter->>'thread_id' AS tid, "
        "left(body, 4000) AS body FROM vault_notes "
        "WHERE tags @> ARRAY['training-enquiries'] AND type IN ('enquiry','enquiry-reply') "
        f"AND coalesce(frontmatter->>'date','2026-06-01') >= '{FLOOR}' "
        "AND vault_path NOT IN (SELECT vault_path FROM public.enquiry_touches WHERE vault_path IS NOT NULL) "
        "ORDER BY d NULLS LAST"
    )
    if not isinstance(notes, list):
        print("query error:", notes); return 0
    print(f"=== reconcile-corpus: {len(notes)} un-reconciled EE-era note(s) (floor {FLOOR}) ===")
    n = 0
    for note in notes:
        kind = "reply" if note["type"] == "enquiry-reply" else "enquiry"
        occurred = (note.get("d") or FLOOR)[:10]
        print(f"  {'[apply]' if apply else '[dry]'} {occurred}  {kind:7}  {note['vault_path']}")
        if apply:
            cc("INSERT INTO public.enquiry_touches "
               "(vault_path, slug, thread_id, kind, sent_text, source, historical, as_of, occurred_at) VALUES ("
               f"{lit(note['vault_path'])}, {lit(note['slug'])}, {lit(note.get('tid'))}, {lit(kind)}, "
               f"{lit(note.get('body'))}, 'backfill', true, {lit(occurred)}, {lit(occurred + 'T00:00:00Z')}) "
               "ON CONFLICT (vault_path) DO UPDATE SET source='backfill', historical=true, updated_at=now()")
            if manifest:
                manifest.write(json.dumps({"kind": "backfill_touch", "vault_path": note["vault_path"]}) + "\n")
        n += 1
    return n

def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    source = next((args[i + 1] for i, a in enumerate(args) if a == "--source" and i + 1 < len(args)), None)
    os.makedirs(OUT_DIR, exist_ok=True)
    batch = "reconcile" if not source else f"mine-{source}"
    manpath = next((args[i + 1] for i, a in enumerate(args) if a == "--manifest" and i + 1 < len(args)), f"{OUT_DIR}/manifest-{batch}.jsonl")

    print(f"=== ee-backfill · mode={'mine:'+source if source else 'reconcile-corpus'} · {'APPLY' if apply else 'DRY-RUN'} · --mine read-only ALWAYS ON ===")

    if source:
        # Gmail Phase-0 candidate generation is READ-ONLY and gated behind Pete's review.
        if apply and "--i-have-reviewed" not in args:
            print("⛔ Gmail-mine --apply refused: the plan's hard gate requires Pete to eyeball the Phase-0 "
                  "candidate manifest first. Run --dry-run, have Pete review, then re-run with --i-have-reviewed.")
            sys.exit(2)
        print("Gmail read-only mining is scaffolded but intentionally NOT auto-run here — the light-seed "
              "reframe (decision #3) makes the existing corpus the seed; slipped-capture mining is a "
              "Pete-reviewed Phase 0. Use reconcile-corpus (default) for the actual seed.")
        return

    manifest = open(manpath, "a") if apply else None
    n = reconcile_corpus(apply, manifest)
    if manifest: manifest.close()
    print(f"\n=== {'wrote' if apply else 'would write'} {n} backfill touch(es)"
          + (f" · manifest {manpath}" if apply else "") + " · ZERO Gmail mutations, ZERO new tasks ===")

if __name__ == "__main__":
    main()
