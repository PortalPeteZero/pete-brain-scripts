#!/usr/bin/env python3
"""worklog.py -- append one row to the CC Work Log (public.work_log): the single
cross-property index of work done. Skills call this at their existing save/ship point
(property-manager Step 6g, vault-writer Step 3a close-on-ship, brain Compress Step 7,
the per-skill report writers). It is the go-forward complement to the one-off backfill.

Resolves the property to its canonical slug + entity from the LIVE roster
(property_state), so a new site is covered automatically. Idempotent on --source-ref
(ON CONFLICT DO NOTHING), so re-running a hook never double-logs.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/worklog.py \\
     --property "Sygma Solutions Website" --area seo \\
     --title "de-optimise eusr-cat1 (cut keyword stuffing)" \\
     --evidence "words 1,180->840; H1 de-stacked" --outcome unknown \\
     --link "https://github.com/.../commit/abc123" \\
     --source-ref "git:PortalPeteZero/sygma-solutions-nextjs@abc123def456"

  # cross-cutting infra (no property):
  VAULT=/tmp/pbs python3 /tmp/pbs/worklog.py --entity Personal --area ops \\
     --title "training-audit: teachable orphan-ignore layer" --source-ref "pbs:2026-06-29:training-audit"

Notes:
  * --area in (seo,dev,ads) REQUIRES --evidence and --outcome (mirrors the DB CHECK;
    fails fast with a clear message so a hook can't silently violate it).
  * --date defaults to today (Atlantic/Canary). --actor defaults to claude.
  * --source-ref is REQUIRED -- it is the idempotency / dedup key.

Backstop mode (the GATE for raw main-session dev/deploy work the skill hooks don't cover):
  VAULT=/tmp/pbs python3 /tmp/pbs/worklog.py reconcile --repo owner/repo --git-dir /path [--since YYYY-MM-DD]
  -> lists that repo's commits NOT in work_log (exit 2 if any). Run at session/deploy close.
"""
import os, sys, json, re, argparse, datetime, subprocess, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REF = "zhexcaflgahdcbzvbyfq"
SEC = f"{VAULT}/Library/processes/secrets"
AREAS = ("seo", "content", "dev", "backlinks", "ads", "design", "finance", "ops")
EVIDENCE_AREAS = ("seo", "dev", "ads")
ENTITIES = ("Sygma", "Canary Detect", "Personal", "One System", "El Atico")

# property name/slug -> entity, for the handful whose roster `business` field is blank.
# (The roster is the source of truth for which properties exist; this only fills entity.)
_ENTITY_HINTS = [
    (re.compile(r'canary|leak|pipebuster|ecofinish|boyce', re.I), "Canary Detect"),
    (re.compile(r'sygma|locator', re.I), "Sygma"),
    (re.compile(r"o'?connor", re.I), "One System"),
]


def _tok():
    return open(f"{SEC}/supabase-token").read().strip()


def ccq(sql):
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {_tok()}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"SQL ERROR {e.code}: {e.read().decode()[:400]}\n"); raise


def lit(s):
    return "NULL" if s is None or s == "" else "'" + str(s).replace("'", "''") + "'"


def slugify(name):
    return re.sub(r'[^a-z0-9]+', '-', (name or "").lower()).strip('-')


def resolve_property(prop):
    """Match `prop` (name or slug) against the live property_state roster.
    Returns (property_slug, property_name, entity) or (slugify(prop), prop, None)."""
    if not prop:
        return (None, None, None)
    rows = ccq("""SELECT p->>'name' AS name, p->>'business' AS business
                  FROM property_state, jsonb_array_elements(payload->'properties') p
                  WHERE id=(SELECT max(id) FROM property_state)""")
    want = slugify(prop)
    for r in rows:
        nm = r["name"]
        if slugify(nm) == want or nm.lower() == prop.lower():
            biz = (r.get("business") or "").lower()
            ent = None
            if "canary" in biz:
                ent = "Canary Detect"
            elif "sygma" in biz:
                ent = "Sygma"
            elif "personal" in biz:
                ent = "Personal"
            elif "atico" in biz:
                ent = "El Atico"
            if not ent:
                for rx, e in _ENTITY_HINTS:
                    if rx.search(nm):
                        ent = e; break
            return (slugify(nm), nm, ent)
    # not in roster -> accept as free text (still logged, slug derived)
    return (want, prop, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--entity")
    ap.add_argument("--property")
    ap.add_argument("--project")
    ap.add_argument("--area", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--detail")
    ap.add_argument("--link")
    ap.add_argument("--evidence")
    ap.add_argument("--outcome")
    ap.add_argument("--actor", default="claude")
    ap.add_argument("--status", default="shipped")
    ap.add_argument("--source-ref", dest="source_ref", required=True)
    a = ap.parse_args()

    if a.area not in AREAS:
        sys.exit(f"worklog: --area must be one of {AREAS}")
    if a.area in EVIDENCE_AREAS and not (a.evidence and a.outcome):
        sys.exit(f"worklog: --area '{a.area}' REQUIRES --evidence and --outcome "
                 f"(what changed + worked/no-change/regressed/too-early/unknown). This mirrors the DB rule.")

    pslug, pname, p_ent = resolve_property(a.property)
    entity = a.entity or p_ent
    if entity and entity not in ENTITIES:
        sys.exit(f"worklog: --entity must be one of {ENTITIES}")
    date = a.date or datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d")

    cols = "date,entity_slug,property_slug,property_name,project_slug,area,title,detail,link,evidence,outcome,actor,status,source_ref"
    vals = ",".join([lit(date), lit(entity), lit(pslug), lit(pname), lit(a.project), lit(a.area),
                     lit(a.title), lit(a.detail), lit(a.link), lit(a.evidence), lit(a.outcome),
                     lit(a.actor), lit(a.status), lit(a.source_ref)])
    before = ccq("SELECT count(*) AS c FROM work_log")[0]["c"]
    ccq(f"INSERT INTO public.work_log ({cols}) VALUES ({vals}) ON CONFLICT (source_ref) DO NOTHING")
    after = ccq("SELECT count(*) AS c FROM work_log")[0]["c"]
    if after > before:
        print(f"worklog: logged [{a.area}] {pname or entity or 'cross-cutting'} — {a.title[:70]}")
    else:
        print(f"worklog: already logged (source_ref={a.source_ref}) — no-op")


def reconcile():
    """Backstop GATE: diff a repo's commits against the work_log and list any not logged.
    Covers the path the close-on-ship skill hooks miss -- raw main-session dev/deploy work.
    Exit 2 if gaps exist (so a close routine can detect it).

    Matching is by SHA, not by rigid ref format: a commit counts as logged if its SHA
    appears ANYWHERE in a work_log entry's source_ref OR detail (the `git:owner/repo@sha`
    prefix is optional, short 7+ SHAs are fine), and `shaA..shaB` ranges are expanded via
    `git rev-list` so one range/feature entry covers every commit inside it. This stops
    thoroughly-but-readably-logged work (feature slugs, `cd 2316dd4..32677fb` ranges,
    `cd a/b` pairs) tripping false "unlogged" alarms.
      worklog.py reconcile --repo owner/repo --git-dir /path/to/checkout [--since YYYY-MM-DD]"""
    ap = argparse.ArgumentParser(prog="worklog.py reconcile")
    ap.add_argument("--repo", required=True, help="owner/repo (shown in output; matching is by SHA anywhere in source_ref/detail)")
    ap.add_argument("--git-dir", dest="git_dir", required=True, help="local checkout to read commits from")
    ap.add_argument("--since", default=None, help="default: today (Atlantic/Canary)")
    a = ap.parse_args(sys.argv[2:])
    since = a.since or datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d")
    out = subprocess.run(
        ["git", "-C", a.git_dir, "log", f"--since={since} 00:00:00", "--pretty=%H\t%s", "--no-merges"],
        capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"worklog reconcile: git log failed for {a.git_dir}: {out.stderr.strip()[:200]}")
    commits = [l.split("\t", 1) for l in out.stdout.strip().splitlines() if "\t" in l]
    # Collect every SHA-like token referenced anywhere in the work log (source_ref + detail),
    # in any format, then expand `A..B` ranges to every commit between them (resolved in this
    # repo). A commit is "logged" if its full SHA starts with one of those tokens. The
    # tokeniser + prefix-match live in worklog_sha (shared with the closeout skill's
    # ownership alignment) so discovery and ownership can never disagree.
    import worklog_sha
    res = ccq("SELECT COALESCE(source_ref,'') AS s, COALESCE(detail,'') AS d FROM work_log")
    text = " ".join(((r.get("s") or "") + " " + (r.get("d") or "")) for r in (res or []))
    tokens = worklog_sha.logged_tokens(text, a.git_dir)
    missing = [(full[:9], subj) for full, subj in commits
               if not worklog_sha.is_present(full, tokens)]
    if not missing:
        print(f"worklog reconcile: OK -- all {len(commits)} commit(s) in {a.repo} since {since} are logged.")
        return
    print(f"worklog reconcile: UNLOGGED -- {len(missing)} of {len(commits)} commit(s) in {a.repo} since {since} NOT in work_log:")
    for short, subj in missing:
        print(f"  {short}  {subj[:90]}")
    print(f"Log each:  worklog.py --entity .. [--project ..] --area dev --title .. --evidence .. --outcome worked "
          f"--link https://github.com/{a.repo}/commit/<sha> --source-ref git:{a.repo}@<sha>")
    sys.exit(2)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reconcile":
        reconcile()
    else:
        main()
