#!/usr/bin/env python3
"""uib-content-check.py -- the gate that keeps the Underground Intelligence Bureau
database-driven and Agent-Hertz-answerable. Prints 0 when clean; exits 1 with a list when not.

Born 4 Aug 2026, the day the first build shipped with content in a TypeScript seed file
instead of the database. Pete: "why the fuck is it not in supabase?? everything was
supposed to be database driven" — and then: "how do we ensure anything we add, any small
thing no matter what it is goes into the database for scout" (the agent, since renamed Agent Hertz). This is the answer that
is not a promise.

Three classes of failure it catches:

  1. CONTENT SMUGGLED INTO THE REPO -- prose hardcoded in page files instead of rows.
     The app has no static seed; anything that looks like one is a regression.
  2. ROWS AGENT HERTZ CANNOT USE -- published resources with no blocks and no summary payload,
     image assets without alt text, video resources without a transcript flag, course
     questions with no source_ref. (The DB publish gate blocks most of these at write
     time; this re-checks from outside in case anything got in around the trigger.)
  3. UNANSWERABLE CORPUS -- once the embedder exists: published blocks/resources with a
     null embedding. Reported as INFO until the agent ships, FAIL after (flip STRICT_EMBED).

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/uib-content-check.py            # check the DB
  VAULT=/tmp/pbs python3 /tmp/pbs/uib-content-check.py --repo DIR # also sweep a checkout
"""
import json, os, re, subprocess, sys, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REF = "xekedjpotwhhstpwganq"
STRICT_EMBED = True  # flipped 4 Aug 2026 — the agent (now Agent Hertz) is live; an unembedded block is invisible to it

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

    # ── 2: rows Agent Hertz cannot use ─────────────────────────────────────────────
    checks = [
        ("image assets without alt text",
         "select count(*) as n from resource_asset where kind='image' and coalesce(alt_text,'')=''"),
        # A handful of the on-site clips are instrument audio with nobody speaking. Those are
        # exempt when they say so (meta.no_speech) AND still carry a block describing what is
        # audible, so Agent Hertz can see them. Silence is a reason; a missing transcript is not.
        ("video resources published without a transcript flag",
         "select count(*) as n from resource r where r.kind='video' and r.status='published' "
         "and coalesce((r.meta->>'has_transcript')::boolean,false)=false "
         "and not (coalesce((r.meta->>'no_speech')::boolean,false) "
         "         and exists (select 1 from resource_block b where b.resource_id=r.id))"),
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
        # /videos renders a clip under its pair group. A group that does not exist means the
        # video renders nowhere at all: it is live, published, and invisible. Caught exactly
        # that way on 5 Aug 2026 after a typo'd group slug.
        ("videos pointing at a clip_pair group that does not exist (invisible on /videos)",
         "select count(*) as n from resource r where r.meta->>'pair_group' is not null "
         "and not exists (select 1 from clip_pair p where p.slug = r.meta->>'pair_group')"),
        ("topics used on resources that are not in the closed list",
         "select count(*) as n from (select unnest(topics) t from resource) x "
         "where not exists (select 1 from topic where slug=x.t)"),
        ("services used on resources that are not in the closed list",
         "select count(*) as n from (select unnest(services) s from resource) x "
         "where not exists (select 1 from service where slug=x.s)"),
    ]

    # ── em-dashes, across EVERY text column in the schema ─────────────────────
    # Pete's voice carries no em-dashes. The first sweep (5 Aug 2026) checked the
    # four columns that came to mind and missed topic blurbs, quiz options, course
    # source refs and a list block, so this walks information_schema instead of
    # trusting a hand-written list: a new column is covered the day it is added.
    # Exempt: rows quoting a manufacturer or standard word for word, because
    # altering a quote is the worse fault. resource_block 121 is the Radiodetection
    # C.A.T.4 user guide and says "quoted verbatim" in the text itself.
    #
    # Also exempt: the three support_session* tables (added 6 Aug 2026). This gate
    # exists to police OUR voice in OUR content. Those tables hold what a delegate
    # typed into a booking form -- their name, their job title, their contract, a
    # note about what they are bringing. A delegate pasting a job title with an
    # em-dash in it is not a voice failure, and letting it fail the gate would mean
    # the Bureau's own check goes red on data Pete does not control and cannot fix
    # without editing somebody else's words.
    dash_sql = """
do $$
declare r record; n bigint; extra text;
begin
  create temp table if not exists uib_dash_hits(tbl text, col text, n bigint);
  delete from uib_dash_hits;
  for r in select table_name, column_name from information_schema.columns
           where table_schema='public' and data_type in ('text','character varying','jsonb')
             and table_name not in ('auth_token','ask_log',
                                    'support_session','support_session_request','support_session_notify')
  loop
    extra := case when r.table_name='resource_block' and r.column_name='text'
                  then ' and text not ilike ''%quoted verbatim%''' else '' end;
    execute format('select count(*) from public.%I where %I::text like ''%%—%%''%s',
                   r.table_name, r.column_name, extra) into n;
    if n > 0 then insert into uib_dash_hits values (r.table_name, r.column_name, n); end if;
  end loop;
end $$;
select coalesce(sum(n),0)::bigint as n, coalesce(string_agg(tbl||'.'||col, ', '), '') as where_
from uib_dash_hits"""
    d = q(dash_sql)[0]
    n_dash = int(d["n"])  # sum() comes back numeric, which this API encodes as a string
    if n_dash:
        fails.append(f"{n_dash:>3}  em-dashes in content (not Pete's voice): {d['where_']}")
    for label, sql in checks:
        n = q(sql)[0]["n"]
        if n:
            fails.append(f"{n:>3}  {label}")

    # ── 3: the corpus behind Agent Hertz ───────────────────────────────────────────
    emb = q("select "
            "(select count(*) from resource where status='published' and embedding is null) as r, "
            "(select count(*) from resource_block where embedding is null) as b")[0]
    n_unembedded = emb["r"] + emb["b"]
    msg = f"{emb['r']} published resources / {emb['b']} blocks with no embedding"
    if n_unembedded and STRICT_EMBED:
        fails.append("     " + msg + " — invisible to Agent Hertz")
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
    print("  0 failures. Everything a reader sees, Agent Hertz can see.")
    sys.exit(0)

if __name__ == "__main__":
    main()
