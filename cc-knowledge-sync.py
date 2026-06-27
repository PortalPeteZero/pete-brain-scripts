#!/usr/bin/env python3
"""cc-knowledge-sync.py — keep the CC's semantic search CURRENT, automatically.

Finds vault `.md` changed since the last run → (re)ingests them into `vault_notes` → nulls the
embeddings of changed notes so they get re-embedded with the new content → embeds anything un-embedded
(direct Voyage). So any NEW or EDITED note / process / doc becomes searchable on its own, without a
manual ingest step. Runs from `cc-refresh` (→ Railway at Part H).

⚠ FUTURE — ON-WRITE indexing (Pete, 22 Jun: "i want a note or plan somewhere to add the on-write
later"): this cron is the v1 (catch-up on a schedule). The next step is to index the INSTANT something
is written — a small write-hook on each path that creates content (Cowork capture, Claude Code edits,
the CC's own writes) that calls ingest+embed for just that file. That makes search real-time instead of
up-to-the-last-sync. Tracked in the master plan checklist. Until then, this cron keeps it current.

Usage: python3 cc-knowledge-sync.py [--full]   (--full re-scans everything, ignoring the last-run stamp)
"""
# CRON-META
# what: Re-indexes the CC knowledge base — re-ingests changed docs and re-embeds any un-embedded vault_notes/notes so semantic search and the bot stay current with manual edits.
# why: A manual CC edit (app / phone / bot / raw SQL) can land a row with no embedding; this is the self-healing safety net so nothing goes invisible to semantic search. (Was hand-run via cc-refresh; this is the deferred "Part H" Railway deployment.)
# reads: vault .md file changes + vault_notes/notes rows with a null embedding
# writes: vault_notes (ingest + embeddings), notes (embeddings)
# entity: command-centre
# secrets: VOYAGE_API_KEY, SUPABASE_TOKEN
# schedule: 0 * * * *
# timezone: Atlantic/Canary
# CRON-META-END
import json, os, sys, subprocess, datetime, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"
STATE = os.path.join(HERE, "_logs", "knowledge-sync-state.json")
# the vault content trees that become vault_notes (the operating skeleton + knowledge)
ROOTS = ["Library", "Projects", "Daily", "Businesses", "Customers", "Suppliers", "Properties", "Personal", "Accreditations"]
LOOKBACK_H = 72   # first-run / no-state window

def _k():
    k = json.load(open(f"{SEC}/command-centre-supabase-keys.json")); return k["url"], k["service_role_key"]

def load_state():
    try: return json.load(open(STATE))
    except Exception: return {}

def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE, "w"))

def changed_since(ts):
    out = []
    for r in ROOTS:
        base = os.path.join(VAULT, r)
        if not os.path.isdir(base): continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if not d.startswith(".") and d != "_archive"]
            for f in fn:
                if f.endswith(".md") and not f.startswith("."):
                    p = os.path.join(dp, f)
                    try:
                        if os.path.getmtime(p) > ts: out.append(os.path.relpath(p, VAULT))
                    except Exception: pass
    return out

def null_embeddings(paths):
    """Null changed notes' embeddings so they're re-embedded with the new body (FTS is already live).
    Uses the Management API SQL endpoint — paths contain spaces, so a PostgREST in.() URL can't carry them."""
    if not paths: return
    REF = "zhexcaflgahdcbzvbyfq"
    tok = (os.environ.get("SUPABASE_TOKEN") or open(f"{SEC}/supabase-token").read().strip())
    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    for i in range(0, len(paths), 200):
        batch = paths[i:i + 200]
        vals = ",".join("'" + p.replace("'", "''") + "'" for p in batch)
        q = f"update public.vault_notes set embedding=null where vault_path in ({vals});"
        req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{REF}/database/query",
            data=json.dumps({"query": q}).encode(),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "User-Agent": UA}, method="POST")
        try: urllib.request.urlopen(req, timeout=120)
        except Exception as e: print("  ⚠ null-embed batch failed:", str(e)[:140])

def run(script, *args):
    r = subprocess.run(["python3", os.path.join(HERE, script), *args], capture_output=True, text=True)
    return (r.stdout.strip().splitlines() or [r.stderr.strip()[-160:]])[-1]

def main():
    st = load_state()
    now = time.time()
    since = 0 if "--full" in sys.argv else st.get("last_run_epoch", now - LOOKBACK_H * 3600)
    changed = changed_since(since)
    print(f"cc-knowledge-sync — {len(changed)} .md changed since {datetime.datetime.fromtimestamp(since, datetime.timezone.utc).isoformat()}")
    if changed:
        for i in range(0, len(changed), 80):
            print("  ingest:", run("cc-knowledge-ingest.py", *changed[i:i + 80]))
        null_embeddings(changed)      # so edited notes get a fresh embedding, not a stale one
    print("  embed:", run("cc-knowledge-embed-backfill.py"))
    st["last_run_epoch"] = now
    st["last_run_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    st["last_changed"] = len(changed)
    save_state(st)
    print("done")

if __name__ == "__main__": main()
