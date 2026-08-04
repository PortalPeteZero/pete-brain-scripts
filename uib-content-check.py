#!/usr/bin/env python3
"""uib-content-check.py -- the gate that keeps the Underground Intelligence Bureau
database-driven and Scout-answerable. Prints 0 when clean; exits 1 with a list when not.

Born 4 Aug 2026, the day the first build shipped with content in a TypeScript seed file
instead of the database. Pete: "why the fuck is it not in supabase?? everything was
supposed to be database driven" — and then: "how do we ensure anything we add, any small
thing no matter what it is goes into the database for scout". This is the answer that
is not a promise.

Three classes of failure it catches:

  1. CONTENT SMUGGLED INTO THE REPO -- prose hardcoded in page files instead of rows.
     The app has no static seed; anything that looks like one is a regression.
  2. ROWS SCOUT CANNOT USE -- published resources with no blocks and no summary payload,
     image assets without alt text, video resources without a transcript flag, course
     questions with no source_ref. (The DB publish gate blocks most of these at write
     time; this re-checks from outside in case anything got in around the trigger.)
  3. UNANSWERABLE CORPUS -- once the embedder exists: published blocks/resources with a
     null embedding. Reported as INFO until Scout ships, FAIL after (flip STRICT_EMBED).

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/uib-content-check.py            # check the DB
  VAULT=/tmp/pbs python3 /tmp/pbs/uib-content-check.py --repo DIR # also sweep a checkout
"""
import json, os, re, subprocess, sys, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REF = "xekedjpotwhhstpwganq"
STRICT_EMBED = True  # flipped 4 Aug 2026 — Scout is live; an unembedded block is invisible to it

def q(sql):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=120).read())

def main():
    fails, infos = [], []

    # ── 2: rows Scout cannot use ─────────────────────────────────────────────
    checks = [
        ("image assets without alt text",
         "select count(*) as n from resource_asset where kind='image' and coalesce(alt_text,'')=''"),
        ("video resources published without a transcript flag",
         "select count(*) as n from resource where kind='video' and status='published' and coalesce((meta->>'has_transcript')::boolean,false)=false"),
        ("course questions without a source_ref",
         "select count(*) as n from course_question where coalesce(source_ref,'')=''"),
        ("course steps without a source_ref",
         "select count(*) as n from course_step where coalesce(source_ref,'')=''"),
        ("published resources with an empty summary",
         "select count(*) as n from resource where status='published' and coalesce(summary,'')=''"),
        ("published articles with neither body blocks nor an outline",
         "select count(*) as n from resource r where kind='article' and status='published' "
         "and coalesce(jsonb_array_length(meta->'outline'),0)=0 "
         "and not exists (select 1 from resource_block b where b.resource_id=r.id)"),
        ("image blocks whose asset row is missing",
         "select count(*) as n from resource_block b where block_type='image' and asset_id is null"),
        ("topics used on resources that are not in the closed list",
         "select count(*) as n from (select unnest(topics) t from resource) x "
         "where not exists (select 1 from topic where slug=x.t)"),
        ("services used on resources that are not in the closed list",
         "select count(*) as n from (select unnest(services) s from resource) x "
         "where not exists (select 1 from service where slug=x.s)"),
    ]
    for label, sql in checks:
        n = q(sql)[0]["n"]
        if n:
            fails.append(f"{n:>3}  {label}")

    # ── 3: the corpus behind Scout ───────────────────────────────────────────
    emb = q("select "
            "(select count(*) from resource where status='published' and embedding is null) as r, "
            "(select count(*) from resource_block where embedding is null) as b")[0]
    n_unembedded = emb["r"] + emb["b"]
    msg = f"{emb['r']} published resources / {emb['b']} blocks with no embedding"
    if n_unembedded and STRICT_EMBED:
        fails.append("     " + msg + " — invisible to Scout")
    else:
        infos.append(msg)

    # ── 1: content smuggled into the repo ────────────────────────────────────
    repo = None
    if "--repo" in sys.argv:
        repo = sys.argv[sys.argv.index("--repo") + 1]
    if repo and os.path.isdir(repo):
        r = subprocess.run(["grep", "-rln", "--include=*.ts", "--include=*.tsx",
                            "-E", r"(VIDEOS|ARTICLES|COURSES|BOOK_CHAPTERS|DOWNLOADS)\s*:\s*\w*\[\]?\s*=\s*\[",
                            os.path.join(repo, "src")], capture_output=True, text=True)
        for f in [x for x in r.stdout.splitlines() if x]:
            fails.append(f"     static content seed in the repo: {f}")
        if os.path.exists(os.path.join(repo, "src/lib/content.ts")):
            fails.append("     src/lib/content.ts exists — the seed file is supposed to be dead")

    print("uib-content-check — the Bureau's database is the only home for content")
    for i in infos:
        print(f"  i  {i}")
    if fails:
        print(f"\n  {len(fails)} FAILURES:")
        for f in fails:
            print(f"  ✗ {f}")
        sys.exit(1)
    print("  0 failures. Everything a reader sees, Scout can see.")
    sys.exit(0)

if __name__ == "__main__":
    main()
