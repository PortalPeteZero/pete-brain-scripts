#!/usr/bin/env python3
"""skill-drift-check.py -- the anti-drift gate. Greps every skill (and the property cards)
for references to projects/paths that no longer exist, resolved against the LIVE system —
so a structural change (a project archived, a doc migrated) can't silently rot a skill until
it breaks mid-use. Run after any structural change; "clean" = zero real drift (exit 0).

It is SMART, not a blanket grep (the naive version cries wolf):
  * live status resolver  -- one SELECT slug,status FROM projects; active=OK, archived/absent=DRIFT.
  * actionable-position    -- a slug is flagged only where it is USED as state (project_slug='X',
                             a WHERE/INSERT clause, or a mapping-table cell), never in explainer prose.
  * block-aware            -- skips `> [!note]/[!warning]` Historical/changelog callouts (kept on purpose).
  * path-class-aware       -- a dead `Library/processes/<x>.md` (no live vault_notes slug) or a retired
                             local tree path `Projects/<slug>/<sub>/` is DRIFT; a single-segment
                             Gmail-label ref `Projects/<Name>` / `Businesses/<Name>` is LIVE -> allowed.
  * cards too               -- scans property_declarations for archived-slug / Projects/… rot (reported).

Usage: VAULT=/tmp/pbs python3 skill-drift-check.py [skills_dir]   (default $VAULT/skills)
Exit 1 if any SKILL drift; card rot is reported but does not fail the gate (it is a separate clean-up).
"""
import os, re, sys, json, glob, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REF = "zhexcaflgahdcbzvbyfq"
SKILLS_DIR = sys.argv[1] if len(sys.argv) > 1 else f"{VAULT}/skills"
TOK = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()


def ccq(sql):
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=90).read().decode())


PROJECTS = {r["slug"]: r["status"] for r in ccq("SELECT slug,status FROM projects")}
NOTE_SLUGS = {r["slug"] for r in ccq("SELECT slug FROM vault_notes")}

SLUG_RE = re.compile(r'\b([A-Z]{2,4}-[A-Z][A-Za-z0-9-]+)\b')


def project_slug_values(line):
    """Slugs used AS a project_slug value (not entity_slug examples that merely share a line)."""
    found = set()
    for m in re.finditer(r"project_slug\s*=\s*'?([A-Za-z][\w-]+)'?", line):     # project_slug='X'
        found.add(m.group(1))
    for m in re.finditer(r"project_slug[^<\n]{0,40}<([^>]+)>", line):           # <A|B|C> placeholder list
        for s in re.split(r'[|,/ ]+', m.group(1)):
            if re.match(r'[A-Z]{2,4}-', s):
                found.add(s)
    m = re.match(r'\s*\|[^|]+\|\s*([A-Z]{2,4}-[\w-]+)\s*\|', line)              # mapping-table 2nd column
    if m:
        found.add(m.group(1))
    return {s for s in found if re.match(r'[A-Z]{2,4}-', s)}
# explainer / anti-regression / historical signal — a line carrying these is describing the past,
# not instructing an action, so its slugs/paths are intentional.
EXPLAINER = re.compile(r'\b(archived|retired|consolidated|folded|former|legacy|historical|changelog|'
                       r'deprecated|was |were |old |not the retired|used to|previously|superseded)\b', re.I)
# a slug is "actionable" (real instruction) when used as state
ACTIONABLE = re.compile(r"project_slug|INSERT INTO tasks|WHERE .*slug|\|\s*[A-Z]{2,4}-[A-Z]")
LABEL_CTX = re.compile(r'\b(gmail|label)\b', re.I)
DEAD_MD = re.compile(r'Library/processes/([a-z0-9-]+)\.md')
# retired LOCAL tree = at least two path segments under Projects/ or Personal/ (e.g. Projects/SY-Website/seo/)
LOCAL_TREE = re.compile(r'\b(Projects|Personal)/[A-Za-z][\w{}-]*/[\w{}.-]+')
CALLOUT = re.compile(r'^\s*>\s*\[!(note|warning|info)\]', re.I)
# Retired 2026-07 task-model vocabulary — "the date is the switch" replaced the +N-day auto-date ladder,
# the PD→dated-P1 stub, and the date-derived tier fallback. Any of these in a live instruction = drift.
LADDER = [
    (re.compile(r"\+2/\+7/\+30d"), "auto-date ladder"),
    (re.compile(r"today\s*\+\s*2\s*days", re.I), "P1 +2d auto-date"),
    (re.compile(r"today\s*\+\s*7\s*days", re.I), "P2 +7d auto-date"),
    (re.compile(r"today\s*\+\s*30\s*days", re.I), "P3 +30d auto-date"),
    (re.compile(r"<today\s*\+\s*\d+\s*d?>"), "<today+Nd> due placeholder"),
    (re.compile(r"P1\s*[=→]\s*\+?\s*2d"), "P1=+2d ladder"),
    (re.compile(r"P2\s*[=→]\s*\+?\s*7d"), "P2=+7d ladder"),
    (re.compile(r"P3\s*[=→]\s*\+?\s*30d"), "P3=+30d ladder"),
    (re.compile(r"PD stored as a dated P1"), "PD→dated-P1 stub"),
    (re.compile(r"date-derived tier", re.I), "date-derived tier fallback"),
]

skill_drift, card_drift = [], []

for path in sorted(glob.glob(f"{SKILLS_DIR}/*/SKILL.md")):
    name = os.path.basename(os.path.dirname(path))
    in_hist = False
    for i, line in enumerate(open(path, errors="ignore"), 1):
        if CALLOUT.match(line) and EXPLAINER.search(line):
            in_hist = True
        elif in_hist and not line.lstrip().startswith(">"):
            in_hist = False
        if in_hist or EXPLAINER.search(line):
            continue
        # archived/nonexistent slug used AS a project_slug value
        for slug in project_slug_values(line):
            st = PROJECTS.get(slug, "NONEXISTENT")
            if st in ("archived", "NONEXISTENT"):
                skill_drift.append((name, i, "project", slug, st, line.strip()[:90]))
        # dead migrated doc
        for m in DEAD_MD.finditer(line):
            if m.group(1) not in NOTE_SLUGS:
                skill_drift.append((name, i, "dead-doc", m.group(0), "no live note", line.strip()[:90]))
        # retired local tree path (not a Gmail label)
        if not LABEL_CTX.search(line):
            for m in LOCAL_TREE.finditer(line):
                skill_drift.append((name, i, "local-path", m.group(0), "retired tree", line.strip()[:90]))
        # retired task-model ladder / stub vocabulary (2026-07 date-is-the-switch migration)
        for rx, label in LADDER:
            if rx.search(line):
                skill_drift.append((name, i, "task-ladder", label, "retired model", line.strip()[:90]))

# --- cards: declared.projects citing archived/nonexistent slugs or Projects/ wikilinks (report only) ---
cards = ccq("""SELECT name, f->'declared'->>'projects' projects, f->'declared'->>'project_slug' slug
               FROM property_declarations WHERE f->'declared'->>'projects' IS NOT NULL
                 OR f->'declared'->>'project_slug' IS NOT NULL""")
for c in cards:
    for field in ("projects", "slug"):
        v = c.get(field) or ""
        for m in SLUG_RE.finditer(v):
            st = PROJECTS.get(m.group(1), "NONEXISTENT")
            if st in ("archived", "NONEXISTENT"):
                card_drift.append((c["name"], field, m.group(1), st))
        if "Projects/" in v:
            card_drift.append((c["name"], field, "Projects/… wikilink (retired tree form)", "path"))

print(f"skill-drift-check :: {SKILLS_DIR}  ({len(PROJECTS)} projects, {len(NOTE_SLUGS)} notes)\n")
if skill_drift:
    print(f"SKILL DRIFT — {len(skill_drift)} (gate FAILS):")
    for d in skill_drift:
        print(f"  {d[0]}:L{d[1]}  [{d[2]}] {d[3]} ({d[4]})  | {d[5]}")
else:
    print("SKILLS: clean — no drift.")
if card_drift:
    print(f"\nCARD ROT (report only, not gating) — {len(card_drift)}:")
    for d in card_drift[:40]:
        print(f"  {d[0]}  {d[1]}: {d[2]} ({d[3]})")
print()
sys.exit(1 if skill_drift else 0)
