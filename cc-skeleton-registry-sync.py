#!/usr/bin/env python3
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
CC = json.load(open(os.path.join(VAULT, "Library/processes/secrets/command-centre-supabase-keys.json")))
URL, KEY = CC["url"].rstrip("/"), CC["service_role_key"]

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
    found = set(re.findall(r'secrets[/"\'\s]+([A-Za-z0-9_.\-]+\.?[A-Za-z0-9]*)', text))
    found = {s.strip('"\'/ ') for s in found if len(s) > 2 and not s.startswith(".")}
    return ", ".join(sorted(found)[:8])

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
helpers, connectors = [], []
for p in sorted(glob.glob(os.path.join(HERE, "*.py"))):
    name = os.path.basename(p)
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
    row = {"name": name, "path": rel, "what": what, "content": txt, "last_edited": last_edited}
    if version:
        row["version"] = version
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
print(f"helpers: {nh} | skills: {ns} | connectors: {nc}")
