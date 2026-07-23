#!/usr/bin/env python3
# CRON-META
# what: Sync skills/helpers/connectors from pete-brain-scripts into the CC registries (public.skills/helpers/connectors) so the Process Library reflects the live code.
# why: SKILL.md and helper edits only appear in the CC after this runs — keeps the Process Library current with the actual code (Pete: the CC is a view, this keeps the view fresh).
# reads: skills/*/SKILL.md, *.py (repo root), public.crons (script_file), skills/README.md (versions)
# writes: CC public.skills, public.helpers, public.connectors (upsert on name; prunes deleted skills, guarded >=5 on disk)
# entity: personal
# schedule: 0 9 * * *
# timezone: Atlantic/Canary
# note: needs no extra secret — cc-cron injects CC_SUPABASE_URL/KEY env on the service. Idempotent / re-runnable; safe on demand after editing a skill.
# CRON-META-END
"""cc-skeleton-registry-sync.py — populate the CC registries that SURFACE the skeleton in the
Command Centre (Pete's "I need the relevant pages to see these"): public.helpers · public.skills ·
public.connectors. Same idea as public.crons for crons, but for the code/connectors a session uses.

Scans (read-only):
  • helpers     ← *.py (repo root, flat layout)         (name · path · kind · what · runs_where · secrets_used)
  • skills      ← skills/*/SKILL.md                      (name · path · what)
  • connectors  ← the *-api.py helpers (direct-API)      (service · kind · what · secret)

Re-runnable (upsert on name). The /m/process-library page reads these (page wiring = a later
website-careful step). Env-first CC keys so it also runs on Railway.
"""
import os, re, json, glob, datetime, subprocess, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.environ.get("VAULT", "/tmp/pbs")
# Env-first (cc-cron injects CC_SUPABASE_URL/KEY on every Railway cron); fall back to the local key file.
URL = os.environ.get("CC_SUPABASE_URL")
KEY = os.environ.get("CC_SUPABASE_SERVICE_KEY")
if not (URL and KEY):
    CC = json.load(open(os.path.join(VAULT, "Library/processes/secrets/command-centre-supabase-keys.json")))
    URL, KEY = CC["url"], CC["service_role_key"]
URL = URL.rstrip("/")

# The set of real secret NAMES from public.secrets — secrets_in() matches against THESE rather
# than a naive word-grep (the old regex produced junk like "WHERE, table"). Fetched once.
def _known_secret_names():
    """The secret names every connector's `secret` field is matched against.

    ⚠ This MUST NOT fail soft. It used to `return set()` on any exception, which meant a single
    transient API blip made every script look like it referenced no secrets at all -- and because
    the sync upserts, that would silently blank the recorded credential on ALL 40 connectors in one
    run. A wrong "nobody knows where this connection's access lives" across the whole registry is
    far worse than a failed run, so: retry once, then ABORT loudly. (19 Jul 2026 — found while
    chasing a blanked odoo connector, which turned out to be a stale-code sync rather than this,
    but the landmine was real and is now defused.)"""
    import time as _t
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(f"{URL}/rest/v1/secrets?select=name",
                headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
            names = {r["name"] for r in json.load(urllib.request.urlopen(req, timeout=30))}
            if names:
                return names
            raise RuntimeError("secrets table returned zero rows")
        except Exception as e:
            if attempt == 2:
                sys.exit(f"cc-skeleton-registry-sync: ABORT — could not read public.secrets ({e}). "
                         f"Refusing to run: it would blank the recorded credential on every connector.")
            _t.sleep(2)

SECRET_NAMES = _known_secret_names()

def upsert(table, rows):
    if not rows:
        return 0
    req = urllib.request.Request(f"{URL}/rest/v1/{table}?on_conflict=name",
        data=json.dumps(rows).encode(),
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}, method="POST")
    urllib.request.urlopen(req, timeout=60)
    return len(rows)

# cron script basenames (these run on Railway) — for the runs_where tag.
# Read from public.crons (the live registry); crons-manifest.json was retired with the Railway cutover.
try:
    _cr = urllib.request.Request(f"{URL}/rest/v1/crons?select=script_file",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
    cron_bn = {os.path.basename(c["script_file"]) for c in json.load(urllib.request.urlopen(_cr, timeout=30)) if c.get("script_file")}
except Exception:
    cron_bn = set()

def first_doc(text):
    m = re.search(r'"""(.+?)(?:\n|""")', text, re.S)
    if m:
        line = m.group(1).strip().splitlines()[0].strip()
        return line[:300]
    m = re.search(r'^#\s*(.+)$', text, re.M)
    return (m.group(1).strip()[:300]) if m else ""

def secrets_in(text):
    """Which real secret NAMES does this script reference? Three signals, unioned:
      1. a known secret name appearing verbatim (covers `secrets/<name>`, `_cc_secret("<name>")`,
         env-file paths, doc mentions) — matched against public.secrets, so no junk;
      2. `_cc_secret(...)` / `cc_secret(...)` call arguments (quoted);
      3. `SECRETFILE__<name>` CRON-META tokens (env-var convention)."""
    found = set()
    for name in SECRET_NAMES:
        if name and name in text:
            found.add(name)
    for arg in re.findall(r'_?cc_secret\(\s*["\']([^"\']+)["\']', text):
        found.add(arg)
    for tok in re.findall(r'SECRETFILE__([A-Za-z0-9_]+)', text):
        found.add(tok.replace("__", "."))
    return ", ".join(sorted(found))

# Pre-build version map from skills/README.md (canonical version source)
_ver_map = {}
try:
    _readme = open(os.path.join(VAULT, "skills/README.md"), encoding="utf-8", errors="replace").read()
    for _row in re.finditer(r'\|\s*`([^/`]+)/`[^|]*\|\s*([v\d][^\|]+)\|', _readme):
        _ver_map[_row.group(1).strip()] = _row.group(2).strip()
except Exception:
    pass

def parse_frontmatter_description(txt):
    """Parse description from SKILL.md frontmatter, handling YAML block scalars (>, >-, |, etc.)."""
    fm_match = re.match(r'^---\s*\n(.*?)\n---', txt, re.S)
    if not fm_match:
        return first_doc(txt)
    fm = fm_match.group(1)
    desc_match = re.search(r'^description:\s*(.*)$', fm, re.M)
    if not desc_match:
        return first_doc(txt)
    inline = desc_match.group(1).strip()
    if inline and inline not in ('>', '>-', '|-', '|', '>+', '|+'):
        return inline[:400]
    # Block scalar: collect indented continuation lines
    after = fm[fm.index(desc_match.group(0)) + len(desc_match.group(0)):]
    lines = []
    for line in after.split('\n'):
        if not line:
            continue
        if line[0] in (' ', '\t'):
            lines.append(line.strip())
        else:
            break
    return (' '.join(lines) if lines else first_doc(txt))[:400]

def git_last_commit(path):
    """Return ISO datetime of the last git commit for path, or None."""
    try:
        rel = os.path.relpath(path, HERE)
        r = subprocess.run(['git', '-C', HERE, 'log', '-1', '--format=%cI', '--', rel],
                           capture_output=True, text=True, timeout=10)
        dt = r.stdout.strip()
        return dt if dt else None
    except Exception:
        return None

# 1. helpers
def _tracked_py():
    """Basenames of the root *.py files git actually tracks, or None if git can't tell us.

    Why: this scan globs the working directory, so a scratch file a session left lying about in
    /tmp/pbs gets registered as though it were a real helper. That is exactly how fpv2.py and
    frank-probe.py — neither ever committed — ended up in the registry pointing at nothing
    (found 23 Jul 2026). A helper lives in the repo; if it isn't committed, it isn't a helper.

    Returns None (meaning "can't tell, register everything") whenever git is unavailable or gives
    an implausibly small answer, so a non-git deploy keeps the old behaviour instead of silently
    registering nothing.
    """
    try:
        r = subprocess.run(["git", "-C", HERE, "ls-files", "*.py"],
                           capture_output=True, text=True, timeout=15)
        names = {os.path.basename(l) for l in r.stdout.splitlines() if l and "/" not in l}
        return names if len(names) >= 50 else None
    except Exception:
        return None

_tracked = _tracked_py()
_skipped_untracked = []
helpers, connectors = [], []
for p in sorted(glob.glob(os.path.join(HERE, "*.py"))):
    name = os.path.basename(p)
    if _tracked is not None and name not in _tracked:
        _skipped_untracked.append(name)
        continue
    txt = open(p, encoding="utf-8", errors="replace").read()
    what = first_doc(txt)
    sec = secrets_in(txt)
    is_api = name.endswith("-api.py")
    kind = "api" if is_api else ("cron" if name in cron_bn else "tool")
    runs = "railway" if name in cron_bn else ("both" if is_api else "local")
    helpers.append({"name": name, "path": name, "kind": kind,
                    "what": what, "runs_where": runs, "secrets_used": sec})
    if is_api:
        svc = name[:-7].replace("-", " ")
        connectors.append({"name": svc, "kind": "direct-api", "what": what, "secret": sec})

# 2. skills
skills = []
live_names = set()
for sk in sorted(glob.glob(os.path.join(VAULT, "skills/*/SKILL.md"))):
    txt = open(sk, encoding="utf-8", errors="replace").read()
    nm = re.search(r'^name:\s*(.+)$', txt, re.M)
    folder = os.path.basename(os.path.dirname(sk))
    name = (nm.group(1).strip().strip('"\'') if nm else folder)[:120]
    live_names.add(name)
    what = parse_frontmatter_description(txt)
    rel = os.path.relpath(sk, VAULT)
    # version: README table is canonical; fall back to frontmatter `version:` if present
    version = _ver_map.get(folder)
    if not version:
        vm = re.search(r'^version:\s*(.+)$', txt, re.M)
        version = vm.group(1).strip() if vm else None
    # last_edited: git log preferred, fall back to file mtime
    last_edited = git_last_commit(sk)
    if not last_edited:
        mtime = os.path.getmtime(sk)
        last_edited = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    # ALWAYS include `version` (None if unknown) — PostgREST bulk-insert rejects rows with
    # differing key sets, so a single version-less skill would 400 the whole batch + break the cron.
    row = {"name": name, "path": rel, "what": what, "content": txt, "last_edited": last_edited,
           "version": version}
    skills.append(row)

# Prune deleted skills (guard: only when at least 5 found on disk)
if live_names and len(live_names) >= 5:
    try:
        req = urllib.request.Request(f"{URL}/rest/v1/skills?select=name",
            headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
        db_names = {r["name"] for r in json.load(urllib.request.urlopen(req, timeout=30))}
        stale = db_names - live_names
        if stale:
            stale_csv = ",".join(stale)
            del_req = urllib.request.Request(
                f"{URL}/rest/v1/skills?name=in.({stale_csv})",
                headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                         "Prefer": "return=minimal"}, method="DELETE")
            urllib.request.urlopen(del_req, timeout=30)
            print(f"pruned: {stale}")
    except Exception as e:
        print(f"prune warning: {e}")

# 3. a few known MCP/platform connectors not captured as *-api.py
connectors += [
    {"name": "Desktop Commander (MCP)", "kind": "mcp", "what": "Filesystem/process access outside the vault (Drives, My Drive, disk)", "secret": "—"},
    {"name": "Railway", "kind": "direct-api", "what": "Cron + 24/7 agent host (GraphQL API)", "secret": "railway-token (CC secrets)"},
    {"name": "Supabase (CC)", "kind": "direct-api", "what": "The Command Centre database — data + brain + registries", "secret": "command-centre-supabase-keys.json / supabase-token"},
]

nh = upsert("helpers", helpers)
ns = upsert("skills", skills)
nc = upsert("connectors", connectors)

# Prune deleted helpers. Skills have had this since the start; helpers never did, so the registry
# only ever grew and a deleted helper left a row pointing at nothing forever (the cc-locator-audit
# "stale-helper-row" finding). Added 23 Jul 2026 after frank-probe.py surfaced that way.
#
# The skills guard (">=5 on disk") is far too weak here — there are ~250 helpers, so a partial or
# wrong-directory scan passing that check could delete hundreds of rows. Two guards instead: a
# healthy absolute count, AND a cap on how much of the registry one run may remove. A genuine
# deletion is a handful of rows; anything bigger is a broken scan, and it should refuse and say so.
PRUNE_MIN_LIVE = 50
PRUNE_MAX_SHARE = 0.10

live_helper_names = {h["name"] for h in helpers}
if len(live_helper_names) >= PRUNE_MIN_LIVE:
    try:
        req = urllib.request.Request(f"{URL}/rest/v1/helpers?select=name,path",
            headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
        db_rows = json.load(urllib.request.urlopen(req, timeout=30))
        # Only prune what this scan actually OWNS: root-level *.py. It globs HERE/*.py and nothing
        # else, so two kinds of row are none of its business and must be left alone —
        #   • a non-Python helper (apple-pass-type-id-csr-gen.sh), and
        #   • a helper deliberately registered from a sub-folder (account/account-log.py).
        # Both were in the registry when this prune was written, and both would have been wrongly
        # deleted as "stale" without this filter.
        db_names = {r["name"] for r in db_rows
                    if r["name"].endswith(".py") and "/" not in (r.get("path") or "")}
        stale = db_names - live_helper_names
        if stale and len(stale) > max(1, int(len(db_names) * PRUNE_MAX_SHARE)):
            print(f"helper-prune REFUSED: {len(stale)} of {len(db_names)} rows would go "
                  f"(> {int(PRUNE_MAX_SHARE * 100)}%) — that reads like a bad scan, not deletions. "
                  f"Not pruning: {sorted(stale)[:10]}")
        elif stale:
            q = ",".join(f'"{n}"' for n in sorted(stale))
            del_req = urllib.request.Request(f"{URL}/rest/v1/helpers?name=in.({q})",
                headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                         "Prefer": "return=minimal"}, method="DELETE")
            urllib.request.urlopen(del_req, timeout=30)
            print(f"helpers pruned: {sorted(stale)}")
    except Exception as e:
        print(f"helper-prune warning: {e}")
else:
    print(f"helper-prune skipped: only {len(live_helper_names)} helpers found on disk "
          f"(need {PRUNE_MIN_LIVE}) — refusing to prune off a suspect scan")

if _skipped_untracked:
    print(f"not registered ({len(_skipped_untracked)} untracked, not in the repo): {sorted(_skipped_untracked)}")
print(f"helpers: {nh} | skills: {ns} | connectors: {nc}")
