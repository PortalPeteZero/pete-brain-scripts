#!/usr/bin/env python3
"""property-freshness.py -- "am I actually up to date on this property?", answered from the
live systems, not from memory or a note.

Born 29 Jul 2026. Jim shipped to the Sygma Platform and emailed a write-up; the commit reached
no work_log row and the attachment reached no Drive folder, so a fresh session opening the
property would have been silently a day behind on a product about to go live. Pete: "i need you
to always be fully up to date every session we do, i cant afford fuck ups."

Run it BEFORE working a property. Every check reads the originating system:

  A card        the CC property card's declared fields, and whether supabase_ref names a real project
  B commits     commits on the repo's prod branch with no work_log row (source_ref match)
  C deploy      the host's live deployment state, and whether repo HEAD is the deployed commit
  D server      Supabase edge functions deployed since our newest work_log row for this property
  E inbound     internal emails carrying attachments that are not in the Drive index

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/property-freshness.py <property-key> [--days=N] [--all-inbound]

  --all-inbound  drop check E's property filter and list EVERY unfiled internal attachment.
                 That is an inbox-wide triage question, not a property one -- off by default,
                 because 28 of the first run's 29 "gaps" were other properties' paperwork.

Exit 0 = nothing outstanding. Exit 1 = at least one gap (so it works as a gate).

Caveat worth knowing: `drive_files` is refreshed by the drive-changes-watch cron every 15 min,
so a file you have JUST uploaded can still show as unfiled for one cycle.
"""
import os, sys, json, re, base64, subprocess, datetime, urllib.request, urllib.error

# Terms too generic to identify a property -- they match half the estate's post.
STOPWORDS = {"sygma", "canary", "detect", "system", "personal", "atico", "website",
             "site", "data", "main", "app", "apps", "crm", "hub", "solutions", "com"}

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"
CC_REF = "zhexcaflgahdcbzvbyfq"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
INTERNAL = ("@sygma-solutions.com", "@one-system.co.uk", "@canary-detect.com")

gaps: list[str] = []
notes: list[str] = []


def _tok(name):
    return open(f"{SEC}/{name}").read().strip()


def _api(url, tok, data=None, method=None):
    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "User-Agent": UA},
        method=method or ("POST" if data is not None else "GET"))
    return json.loads(urllib.request.urlopen(req, timeout=90).read().decode())


def ccq(sql):
    return _api(f"https://api.supabase.com/v1/projects/{CC_REF}/database/query",
                _tok("supabase-token"), {"query": sql})


def sq(text):
    return "'" + str(text).replace("'", "''") + "'"


def head(title):
    print(f"\n{title}\n" + "-" * len(title))


# ---------------------------------------------------------------- A. the card
def check_card(key):
    head("A. Property card")
    rows = ccq(f"SELECT key, name, f FROM property_declarations WHERE key = {sq(key)}")
    if not rows:
        gaps.append(f"no property card with key '{key}'")
        print(f"  GAP  no card named '{key}'")
        return None
    card = rows[0]
    f = card["f"] or {}
    print(f"  {card['name']}  ({f.get('ptype') or 'type?'} · {f.get('status') or 'status?'})")

    for field in ("github", "domains", "hosting", "prod_branch", "front_door"):
        if not f.get(field):
            gaps.append(f"card field '{field}' is blank")
            print(f"  GAP  card field '{field}' is blank")

    ref = f.get("supabase_ref")
    declared_ref = (f.get("declared") or {}).get("supabase_ref")
    if ref and declared_ref and ref != declared_ref:
        gaps.append(f"card supabase_ref disagrees with itself ({ref} top-level vs {declared_ref} declared)")
        print(f"  GAP  supabase_ref contradicts itself: {ref} (top-level) vs {declared_ref} (declared)")
    if ref:
        try:
            projects = _api("https://api.supabase.com/v1/projects", _tok("supabase-token"))
            match = next((p for p in projects if p["id"] == ref), None)
            if match:
                print(f"  ok   supabase_ref {ref} -> '{match['name']}'")
            else:
                gaps.append(f"supabase_ref {ref} is in no Supabase project we can see")
                print(f"  GAP  supabase_ref {ref} matches NO project in the account")
        except Exception as e:
            notes.append(f"could not verify supabase_ref ({e})")
            print(f"  ?    could not verify supabase_ref: {e}")
    return card


# ------------------------------------------------------------- B. commits
def check_commits(card, days):
    head("B. Commits vs the Work Log")
    f = card["f"] or {}
    repo = f.get("github")
    if not repo:
        print("  n/a  no repo declared")
        return None
    branch = f.get("prod_branch") or "main"
    work = f"/tmp/freshness-{repo.split('/')[-1]}"
    pat = _tok("github-pat")
    subprocess.run(["rm", "-rf", work], check=False)
    r = subprocess.run(["git", "clone", "-q", "--branch", branch,
                        f"https://{pat}@github.com/{repo}.git", work],
                       capture_output=True, text=True)
    if r.returncode:
        gaps.append(f"could not clone {repo}: {r.stderr.strip()[:120]}")
        print(f"  GAP  clone failed: {r.stderr.strip()[:120]}")
        return None
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    out = subprocess.run(["git", "-C", work, "log", f"--since={since}",
                          "--pretty=format:%H\x1f%an\x1f%ad\x1f%s", "--date=short"],
                         capture_output=True, text=True).stdout.strip()
    commits = [l.split("\x1f") for l in out.splitlines() if l]
    logged = {r["source_ref"] for r in ccq(
        f"SELECT source_ref FROM work_log WHERE source_ref ILIKE {sq('%' + repo + '%')}") if r["source_ref"]}

    def is_logged(sha):
        return any(sha[:7] in ref for ref in logged)

    unlogged = [c for c in commits if not is_logged(c[0])]
    print(f"  {len(commits)} commit(s) on {branch} in the last {days}d · {len(unlogged)} with no Work Log row")
    for sha, author, date, subj in unlogged:
        gaps.append(f"unlogged commit {sha[:7]} ({author}) {subj[:60]}")
        print(f"  GAP  {sha[:7]} {date} {author:<16} {subj[:58]}")
    head_sha = subprocess.run(["git", "-C", work, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    return head_sha


# -------------------------------------------------------------- C. deploy
def check_deploy(card, head_sha):
    head("C. Deployed build")
    f = card["f"] or {}
    if (f.get("hosting") or "").lower() != "vercel" or not head_sha:
        print("  n/a  not a Vercel property, or repo unavailable")
        return
    r = subprocess.run(["python3", f"{VAULT}/vercel-api.py", "deploy-for-sha", head_sha[:7]],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    out = (r.stdout or r.stderr).strip()
    state = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("State:")), None)
    if state == "READY":
        print(f"  ok   repo HEAD {head_sha[:7]} is live (READY)")
    else:
        gaps.append(f"repo HEAD {head_sha[:7]} is not a READY deployment (state={state or 'unknown'})")
        print(f"  GAP  repo HEAD {head_sha[:7]} not live — state={state or 'unknown'}")
        print("       " + out.replace("\n", "\n       ")[:400])


# ------------------------------------------------- D. server-side (edge functions)
def check_edge(card, key):
    head("D. Server-side deploys vs the Work Log")
    ref = (card["f"] or {}).get("supabase_ref")
    if not ref:
        print("  n/a  no Supabase project on the card")
        return
    rows = ccq("SELECT max(date) AS d FROM work_log WHERE property_slug = "
               f"{sq(key)} OR property_name = {sq(card['name'])}")
    last = rows[0]["d"] if rows and rows[0]["d"] else "1970-01-01"
    try:
        fns = _api(f"https://api.supabase.com/v1/projects/{ref}/functions", _tok("supabase-token"))
    except Exception as e:
        notes.append(f"could not list edge functions ({e})")
        print(f"  ?    could not list edge functions: {e}")
        return
    newer = []
    for fn in fns:
        upd = datetime.datetime.fromtimestamp(fn["updated_at"] / 1000, datetime.timezone.utc)
        if upd.date().isoformat() > last:
            newer.append((fn["slug"], fn.get("version"), upd.strftime("%Y-%m-%d %H:%M")))
    print(f"  newest Work Log row for this property: {last}")
    if not newer:
        print(f"  ok   no edge function deployed since then ({len(fns)} total)")
    for slug, ver, upd in sorted(newer, key=lambda x: x[2], reverse=True):
        gaps.append(f"edge function {slug} v{ver} deployed {upd} after the last Work Log row")
        print(f"  GAP  {slug} v{ver} deployed {upd} — later than our newest record")


def property_terms(card, key):
    """Distinctive words that mean 'this email is about THIS property'."""
    f = card["f"] or {}
    raw = [key, card["name"], f.get("github") or "", *(f.get("domains") or [])]
    terms = set()
    for chunk in raw:
        for w in re.split(r"[^A-Za-z0-9]+", str(chunk).lower()):
            if len(w) >= 4 and w not in STOPWORDS:
                terms.add(w)
    # the bare domain label too, e.g. sygmaportal.com -> sygmaportal
    for d in (f.get("domains") or []):
        lab = str(d).lower().split(".")[0].replace("www", "").strip("-")
        if len(lab) >= 4:
            terms.add(lab)
    return terms


# ------------------------------------------------------------ E. inbound email
def check_inbound(card, days, key, all_inbound=False):
    head("E. Inbound work not filed")
    terms = property_terms(card, key)
    if all_inbound:
        print("  scope: ALL internal attachments (--all-inbound)")
    else:
        print(f"  scope: attachments whose subject or filename mentions {sorted(terms)}")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("gmail_api", f"{VAULT}/gmail-api.py")
        ga = importlib.util.module_from_spec(spec); spec.loader.exec_module(ga)
        g = ga.GmailAPI()
    except Exception as e:
        notes.append(f"could not reach Gmail ({e})")
        print(f"  ?    could not reach Gmail: {e}")
        return
    senders = " OR ".join(f"from:{d}" for d in INTERNAL)
    try:
        threads = g.search_threads(f"({senders}) has:attachment newer_than:{days}d", 40)
    except Exception as e:
        notes.append(f"Gmail search failed ({e})")
        print(f"  ?    Gmail search failed: {e}")
        return
    seen = set()
    for t in threads:
        tid = t["id"] if isinstance(t, dict) else t
        for m in g.get_thread(tid).get("messages", []):
            hs = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
            frm = hs.get("from", "")
            subj = hs.get("subject", "")
            if not any(d in frm for d in INTERNAL):
                continue
            for a in g.list_attachments_in_message(m["id"]):
                fn = a.get("filename") or ""
                # signature images and inline junk are not deliverables
                if not fn or fn.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".ics")):
                    continue
                if fn in seen:
                    continue
                if not all_inbound:
                    hay = (subj + " " + fn).lower()
                    if not any(t in hay for t in terms):
                        continue
                seen.add(fn)
                hit = ccq(f"SELECT 1 FROM drive_files WHERE name = {sq(fn)} LIMIT 1")
                if not hit:
                    gaps.append(f"attachment not filed: '{fn}' from {frm[:40]}")
                    print(f"  GAP  not in Drive: {fn[:64]}  <- {frm[:38]}")
    print(f"  checked {len(seen)} attachment(s) from internal senders in the last {days}d")


def main():
    sys.stdout.reconfigure(line_buffering=True)   # progress must be visible while it runs
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    days = 14
    all_inbound = "--all-inbound" in sys.argv
    for x in sys.argv[1:]:
        if x.startswith("--days"):
            days = int(x.split("=", 1)[1]) if "=" in x else days
    if not a:
        sys.exit(__doc__)
    key = a[0]
    print(f"property-freshness: {key}  (window {days}d)")
    card = check_card(key)
    if card:
        head_sha = check_commits(card, days)
        check_deploy(card, head_sha)
        check_edge(card, key)
        check_inbound(card, days, key, all_inbound)

    head("VERDICT")
    for n in notes:
        print(f"  ?    {n}")
    if gaps:
        print(f"  {len(gaps)} GAP(S) — this property is NOT up to date:")
        for gp in gaps:
            print(f"    - {gp}")
        sys.exit(1)
    print("  0 gaps — up to date. Safe to start work.")


if __name__ == "__main__":
    main()
