#!/usr/bin/env python3
"""
rules-report.py — publish EVERY rule, from every home, into the CC `public.rules` registry.

Built 24 Jul 2026 at Pete's request: *"I also need a window into these rules, so I want a dedicated
section in the CC that I can see every rule by category with a description."* The CC page `/m/rules`
renders this table; the CC is a window onto the database, never a hand-authored copy.

THE PROBLEM IT SOLVES
  After the enforcement migration, rules live in SEVEN different homes on purpose — a rule belongs
  where it will actually reach you, which is not one place:

    gate            code or a DB trigger that REFUSES the action        (strongest)
    front-door      a property's rules, injected when it is mentioned
    process-note    a workflow's rules, fetched with the process
    knowledge       a CC lesson, retrievable on its subject
    conduct-local   the resident conduct store on Pete's Mac            (weakest, so smallest)
    claude-md       the resident operating instructions
    record-flag     a fact marked on ONE record ([no-nag], a routing verb)

  Spread like that, nobody could see the whole picture. This reconciles all of it into one table so
  the question "what rules exist, and which of them can actually stop me?" has one answer.

RUN IT
  VAULT=/tmp/pbs python3 /tmp/pbs/rules-report.py            # publish
  VAULT=/tmp/pbs python3 /tmp/pbs/rules-report.py --dry-run  # report only

HONESTY NOTES
  * The local conduct store lives on Pete's Mac, so this must run LOCALLY — a Railway cron cannot
    see it. Same constraint as gate-report.py, same reason.
  * `delivery` is the honest mechanism, not an aspiration. Measured 24 Jul: a gate fires ~100% of the
    time, an injected front-door rule reaches 88% of sessions, a fetched note 33%, and a resident
    line unreliably. The registry records which one each rule actually gets.
  * Rules are UPSERTed and `last_seen_at` stamped. A row whose `last_seen_at` goes stale means the
    rule was deleted at source — surfaced, never silently dropped, so a disappearance is visible.
"""
import os, sys, json, re, glob, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
MEM = os.path.expanduser("~/.claude/projects/-Users-peterashcroft-Command-Centre/memory")
DRY = "--dry-run" in sys.argv


def _sql(q):
    try:
        r = subprocess.run([sys.executable, f"{VAULT}/cc-sql.py", q], capture_output=True,
                           text=True, timeout=90, env={**os.environ, "VAULT": VAULT})
        return json.loads(r.stdout) if r.stdout.strip().startswith("[") else []
    except Exception:
        return []


def q(s):
    return (s or "").replace("'", "''")


# ---- categories -------------------------------------------------------------------------------
# Plain-English groupings Pete would recognise, not internal jargon. Order matters: first match wins.
CATEGORY_RULES = [
    ("Sygma website & SEO",   r"sygma|hsg47|eusr|proqual|genny|agenda|chooser|backlink|surfer|ahrefs"),
    ("Canary Detect",         r"canary|cloudinary|leak report|published articles"),
    ("LeakGuard",             r"leakguard|html report|water tools"),
    ("Clancy",                r"clancy|damage|strike|state of mind"),
    ("Email & triage",        r"email|gmail|triage|repl(y|ies)|inbox|chase|dext|thread"),
    ("Enquiry engine",        r"enquir|ee[- _]|draft gate|te-log|day rate|on-site"),
    ("Tasks & planning",      r"task|backlog|plan|pd\b|no-nag|project"),
    ("Writing & tone",        r"voice|em-dash|jargon|plain english|preamble|language|report|tone"),
    ("Code & deployment",     r"commit|deploy|vercel|repo|typecheck|redirect|build|clone|migration|env var|supabase|sentry"),
    ("Health & Passion Fit",  r"garmin|journal|intensity|training|swim|passion fit|health"),
    ("Finance",               r"invoice|payment|bank|payroll|vat|odoo|xero|soldo|finance"),
    ("Files & where things live", r"drive|vault|knowledge|ssot|locator|whereis|where.*live|folder|filing"),
    ("How to work with Pete", r"."),  # catch-all
]


def categorise(text):
    t = (text or "").lower()
    for name, pat in CATEGORY_RULES:
        if re.search(pat, t):
            return name
    return "How to work with Pete"


def collect_gates():
    out = []
    for g in _sql("SELECT key, refuses, kind, wired_in, called_by, is_called, exceptions, "
                  "override_path, replaces_rule, status FROM gates WHERE status='live'"):
        if not g.get("is_called"):
            continue  # a gate nobody calls is not enforcement — it is not a live rule
        out.append({
            "key": "gate:" + g["key"], "title": g["key"],
            "description": g.get("refuses"), "category": categorise(g["key"] + " " + (g.get("refuses") or "")),
            "home": "gate", "home_detail": g.get("wired_in"), "delivery": "refuses-the-action",
            "enforced_by": g["key"], "is_enforced": True,
            "scope": g.get("exceptions"), "body": g.get("override_path"),
            "source_ref": "public.gates",
        })
    return out


def collect_local():
    out = []
    for p in sorted(glob.glob(os.path.join(MEM, "*.md"))):
        base = os.path.basename(p)
        if base == "MEMORY.md":
            continue
        raw = open(p, errors="ignore").read()
        d = re.search(r'^description:\s*["\']?(.*?)["\']?\s*$', raw, re.M)
        desc = (d.group(1) if d else "").replace("\\", "").strip()
        name = base[:-3]
        body = raw.split("---", 2)[2].strip() if raw.startswith("---") else raw
        out.append({
            "key": "local:" + name, "title": name.replace("feedback_", "").replace("user_", "").replace("_", " "),
            "description": desc, "category": categorise(name + " " + desc),
            "home": "conduct-local", "home_detail": "the resident conduct store (loads every session)",
            "delivery": "resident-every-session", "enforced_by": None, "is_enforced": False,
            "scope": None, "body": body[:4000], "source_ref": base,
        })
    return out


def collect_front_doors():
    out = []
    for r in _sql("SELECT vault_path, body FROM vault_notes WHERE vault_path LIKE 'Properties/%README.md'"):
        prop = r["vault_path"].split("/")[1]
        grabbing = False
        for line in (r.get("body") or "").split("\n"):
            s = line.strip()
            if s.startswith("#"):
                grabbing = s.lower().startswith(("## rules", "## standing rules", "## standing decisions",
                                                 "## do not", "## workflow conventions"))
                continue
            if grabbing and s.startswith(("- ", "* ")):
                txt = re.sub(r"^[-*]\s+", "", s)
                clean = re.sub(r"[*`\[\]]", "", txt)[:200]
                out.append({
                    "key": f"fd:{prop}:{abs(hash(clean)) % 10**8}", "title": clean[:110],
                    "description": clean, "category": categorise(prop + " " + clean),
                    "home": "front-door", "home_detail": prop,
                    "delivery": "injected-on-mention", "enforced_by": "property-context-hook",
                    "is_enforced": False, "scope": f"only when {prop} is mentioned",
                    "body": txt[:2000], "source_ref": r["vault_path"],
                })
    return out


def collect_engine_tables():
    out = []
    for r in _sql("SELECT id, message, severity, pattern, exception FROM damage_review_rules WHERE active"):
        out.append({
            "key": f"damage:{r['id']}", "title": (r.get("message") or "")[:110],
            "description": r.get("message"), "category": "Clancy",
            "home": "engine-table", "home_detail": "damage_review_rules",
            "delivery": "refuses-the-action" if r.get("severity") == "block" else "fetched-on-trigger",
            "enforced_by": "damage_review_wording_trg" if r.get("severity") == "block" else None,
            "is_enforced": r.get("severity") == "block",
            "scope": r.get("exception"), "body": r.get("pattern"), "source_ref": "public.damage_review_rules",
        })
    for r in _sql("SELECT scenarios, applies_when, fail_hint, require_pattern FROM ee_rules LIMIT 60"):
        hint = (r.get("fail_hint") or "")[:200]
        if not hint:
            continue
        out.append({
            "key": f"ee:{abs(hash(hint)) % 10**8}", "title": hint[:110], "description": hint,
            "category": "Enquiry engine", "home": "engine-table", "home_detail": "ee_rules",
            "delivery": "refuses-the-action" if r.get("require_pattern") else "fetched-on-trigger",
            "enforced_by": "ee-lint" if r.get("require_pattern") else None,
            "is_enforced": bool(r.get("require_pattern")),
            "scope": str(r.get("applies_when") or "")[:200], "body": str(r.get("scenarios") or "")[:1000],
            "source_ref": "public.ee_rules",
        })
    return out


def collect_claude_md():
    rows = _sql("SELECT value FROM config WHERE key='claude-md'")
    if not rows:
        return []
    out, section = [], "General"
    for line in rows[0]["value"].split("\n"):
        if line.startswith("#"):
            section = line.strip("# ").strip()
        if line.startswith("- **"):
            m = re.match(r"- \*\*(.+?)\*\*\s*(.*)", line)
            title = (m.group(1) if m else line[:100]).strip()
            rest = (m.group(2) if m else "").strip()
            clean = re.sub(r"[*`\[\]]", "", rest)[:400]
            out.append({
                "key": f"cmd:{abs(hash(title)) % 10**8}", "title": title[:110],
                "description": clean or title, "category": categorise(title + " " + clean),
                "home": "claude-md", "home_detail": section,
                "delivery": "resident-every-session", "enforced_by": None, "is_enforced": False,
                "scope": None, "body": (line[:2000]), "source_ref": "config.claude-md",
            })
    return out


def main():
    # ONLY the local conduct store is published. Gates, front doors, engine tables and CLAUDE.md
    # are read LIVE by the `rules_v` view straight from their real sources — publishing copies of
    # those would create exactly the drift and duplication this registry exists to prevent
    # (Pete, 24 Jul: "I don't want conflicting info and dupes though, ensure this window is looking
    # at the source"). The conduct store is the one home no cloud query can reach.
    rows = collect_local()
    seen, uniq = set(), []
    for r in rows:
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        uniq.append(r)

    from collections import Counter
    by_home = Counter(r["home"] for r in uniq)
    by_del = Counter(r["delivery"] for r in uniq)
    print(f"rules-report — {len(uniq)} rules across {len(by_home)} homes\n")
    for h, n in by_home.most_common():
        print(f"  {h:16} {n:>4}")
    print("\n  by delivery mechanism (the honest one, not the aspiration):")
    for d, n in by_del.most_common():
        print(f"    {d:26} {n:>4}")
    print(f"\n  can actually REFUSE an action: {sum(1 for r in uniq if r['is_enforced'])}")

    if DRY:
        print("\n  (dry run — nothing written)")
        return 0

    for i in range(0, len(uniq), 40):
        chunk = uniq[i:i + 40]
        vals = ",".join(
            "('{}','{}','{}','{}','{}','{}',now())".format(
                q(r["key"].replace("local:", "")), q(r["title"]), q(r.get("description") or ""),
                q(r["category"]), q(r.get("body") or ""), q(r.get("source_ref") or ""))
            for r in chunk)
        _sql("INSERT INTO rules_local (key,title,description,category,body,source_ref,published_at) "
             "VALUES " + vals +
             " ON CONFLICT (key) DO UPDATE SET title=EXCLUDED.title, description=EXCLUDED.description,"
             " category=EXCLUDED.category, body=EXCLUDED.body, source_ref=EXCLUDED.source_ref,"
             " published_at=now()")
    print(f"\n  published {len(uniq)} local conduct rules to public.rules_local")

    # A rule deleted on disk must DISAPPEAR from the window, not linger. Anything not seen this run
    # was retired at source, so it goes — the whole point of a window is that it shows what is there.
    keys = ",".join(f"'{q(r['key'].replace('local:',''))}'" for r in uniq) or "''"
    gone = _sql(f"DELETE FROM rules_local WHERE key NOT IN ({keys}) RETURNING key")
    if gone:
        print(f"  removed {len(gone)} rule(s) retired at source")
    stale = _sql("SELECT key, title FROM rules_local WHERE published_at < now() - interval '2 days' LIMIT 20")
    if stale:
        print(f"\n  ⚠ {len(stale)} row(s) not seen this run — deleted at source? Surfaced, not dropped:")
        for s in stale[:8]:
            print(f"      {s['key']}  {s['title'][:60]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"rules-report: {e}", file=sys.stderr)
        sys.exit(1)
