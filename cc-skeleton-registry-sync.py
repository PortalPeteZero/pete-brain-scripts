#!/usr/bin/env python3
"""cc-skeleton-registry-sync.py — populate the CC registries that SURFACE the skeleton in the
Command Centre (Pete's "I need the relevant pages to see these"): public.helpers · public.skills ·
public.connectors. Same idea as public.crons for crons, but for the code/connectors a session uses.

Scans (read-only):
  • helpers     ← Library/processes/scripts/*.py        (name · path · kind · what · runs_where · secrets_used)
  • skills      ← Library/skills/*/SKILL.md              (name · path · what)
  • connectors  ← the *-api.py helpers (direct-API)      (service · kind · what · secret)

Re-runnable (upsert on name). The /m/process-library page reads these (page wiring = a later
website-careful step). Env-first CC keys so it also runs on Railway.
"""
import os, re, json, glob, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")
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

# cron script basenames (these run on Railway) — for the runs_where tag
man = json.load(open(os.path.join(VAULT, "Library/processes/crons-manifest.json")))
cron_bn = {os.path.basename(c.get("script_file", "")) for c in man["crons"] if c.get("script_file")}

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
    helpers.append({"name": name, "path": f"Library/processes/scripts/{name}", "kind": kind,
                    "what": what, "runs_where": runs, "secrets_used": sec})
    if is_api:
        svc = name[:-7].replace("-", " ")
        connectors.append({"name": svc, "kind": "direct-api", "what": what, "secret": sec})

# 2. skills
skills = []
for sk in sorted(glob.glob(os.path.join(VAULT, "Library/skills/*/SKILL.md"))):
    txt = open(sk, encoding="utf-8", errors="replace").read()
    nm = re.search(r'^name:\s*(.+)$', txt, re.M)
    desc = re.search(r'^description:\s*(.+)$', txt, re.M)
    folder = os.path.basename(os.path.dirname(sk))
    name = (nm.group(1).strip().strip('"\'') if nm else folder)[:120]
    what = (desc.group(1).strip().strip('"\'') if desc else first_doc(txt))[:400]
    rel = os.path.relpath(sk, VAULT)
    skills.append({"name": name, "path": rel, "what": what})

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
