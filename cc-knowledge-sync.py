#!/usr/bin/env python3
"""cc-knowledge-sync.py — keep the CC semantic layer CURRENT (pure DB job).

Runs the ONE embedder (cc-embedder.py) across vault_notes + tasks + notes. The embedder refreshes every
row whose CONTENT changed, using the SQL embed_input() single-source-of-truth + a content hash
(embedded_hash). So a manual edit from the app, phone, bot or raw SQL is picked up automatically, and a
stale-but-present vector can no longer hide — the old NULL-only check silently missed those.

SUCCESS-but-stale guard: after the embed pass, if any table still has DIRTY rows (content changed but the
stored embedding could not be refreshed — e.g. a Voyage outage) the job emails Pete once per day and logs
a cron_events row, so the board can't show green while vectors rot.

Replaces the old file-mtime re-ingest arm, which was a production NO-OP: the deployed container carries
only a handful of .md files and its state file reset on every deploy. Knowledge INGESTION is session-driven
via cc-knowledge-ingest.py; this cron owns freshness of the SEMANTIC LAYER only.
"""
# CRON-META
# what: Refreshes the CC semantic layer — re-embeds any vault_notes/tasks/notes row whose content changed (content-hash dirty detection via embed_input) and alerts if embeddings stay stale while the job succeeds.
# why: A manual edit (app/phone/bot/SQL) leaves a stale-but-present vector the old NULL-only check never caught; this is the self-healing freshness job + a SUCCESS-but-stale alarm so semantic search can't silently rot.
# reads: vault_notes/tasks/notes content vs embedded_hash (the freshness gate)
# writes: vault_notes/tasks/notes (embedding + embedded_hash)
# entity: command-centre
# secrets: VOYAGE_API_KEY, SUPABASE_TOKEN, GOOGLE_SA_JSON
# schedule: 0 * * * *
# timezone: Atlantic/Canary
# CRON-META-END
import json, os, sys, subprocess, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"
REF = "zhexcaflgahdcbzvbyfq"
CRON_KEY = "knowledge-reindex"
ALERT_TO = "pete.ashcroft@sygma-solutions.com"
GATE = {"vault_notes": "embed_input(title,body)", "tasks": "embed_input(name,notes)", "notes": "embed_input(title,body)"}

def mgmt_sql(q):
    tok = (os.environ.get("SUPABASE_TOKEN") or open(f"{SEC}/supabase-token").read()).strip()
    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "User-Agent": UA}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=120).read() or "[]")

def gate_counts():
    out = {}
    for t, ei in GATE.items():
        try:
            r = mgmt_sql(f"SELECT count(*) AS dirty FROM public.{t} "
                         f"WHERE length({ei})>0 AND (embedding IS NULL OR embedded_hash IS DISTINCT FROM md5({ei}))")
            out[t] = r[0]["dirty"] if r else -1
        except Exception as e:
            print(f"  gate {t} failed: {str(e)[:120]}"); out[t] = -1
    return out

def _cron_state():
    try:
        sys.path.insert(0, HERE)
        from importlib import import_module
        return import_module("cron_state")
    except Exception:
        return None

def alert(dirty):
    cs = _cron_state()
    today = datetime.date.today().isoformat()
    if cs and cs.get_state(CRON_KEY, "stale-alert-date") == today:
        print("  stale-alert: already sent today"); return
    lines = "\n".join(f"  {t}: {n} rows whose stored embedding no longer matches their content"
                      for t, n in dirty.items() if n and n > 0)
    body = ("The hourly knowledge-reindex job ran but the CC semantic layer is still STALE:\n\n" + lines +
            "\n\nEmbeddings could not be refreshed (likely a Voyage error). Semantic search / Ask may return "
            "outdated matches until this clears. Check the knowledge-reindex logs on Railway.\n")
    try:
        subprocess.run(["python3", os.path.join(HERE, "gmail-api.py"), "send", ALERT_TO,
                        "⚠ CC semantic search stale (knowledge-reindex)", body],
                       check=False, capture_output=True, timeout=60)
    except Exception as e:
        print("  stale-alert: email failed", str(e)[:120])
    try:
        d = ("SUCCESS-but-stale: " + ", ".join(f"{t}={n}" for t, n in dirty.items() if n and n > 0)).replace("'", "''")
        mgmt_sql(f"INSERT INTO public.cron_events (id, cron_key, at, kind, detail, actor) "
                 f"VALUES (gen_random_uuid(), '{CRON_KEY}', now(), 'stale-alert', '{d}', 'cc-knowledge-sync')")
    except Exception as e:
        print("  stale-alert: cron_events insert failed", str(e)[:120])
    if cs:
        try: cs.set_state(CRON_KEY, "stale-alert-date", today)
        except Exception: pass
    print("  stale-alert: SENT")

def main():
    # run the ONE embedder over all three tables (the content-hash dirty scan lives inside it)
    subprocess.run(["python3", os.path.join(HERE, "cc-embedder.py")], env=os.environ)
    dirty = gate_counts()
    total = sum(n for n in dirty.values() if n and n > 0)
    print("cc-knowledge-sync — post-embed gate:", dirty)
    # Persistence gate on the alert: a row can read dirty for one cycle for benign reasons (edited during
    # the pass, a one-off Voyage blip that self-heals next hour). Only alert when staleness PERSISTS across
    # two consecutive runs — that is genuine "SUCCESS-but-stale" (can't refresh), not transient noise.
    cs = _cron_state()
    prev = 0
    if cs:
        try: prev = int(cs.get_state(CRON_KEY, "last-dirty") or 0)
        except Exception: prev = 0
        try: cs.set_state(CRON_KEY, "last-dirty", total)
        except Exception: pass
    if total > 0 and prev > 0:
        print(f"  STILL stale after 2 consecutive runs ({total}) — alerting")
        alert(dirty)
    elif total > 0:
        print(f"  {total} stale this run — transient; will alert only if still stale next run")
    else:
        print("  all tables fresh (gate=0)")
    print("done")

if __name__ == "__main__":
    main()
