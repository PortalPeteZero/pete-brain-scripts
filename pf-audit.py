#!/usr/bin/env python3
"""pf-audit.py — health check for the Passion Fit Concepts corpus + module. Read-only.
Run anytime (esp. after pf-ingest):  VAULT=/tmp/pbs python3 /tmp/pbs/pf-audit.py
Exit 0 = all PASS · 1 = one or more FAIL. Prints a PASS/FAIL line per check with evidence."""
import os, re, json, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# The canonical taxonomy (must match lib/pf/data.ts CATEGORIES in the command-centre app).
CONCEPTS = [
    "effective-goal-setting", "commitment-continuum", "prioritisation", "control-the-controllables",
    "transactional-state", "direction-support-matrix", "intuition-scale-learning-behaviours",
    "high-functioning-matrix", "the-development-paradox", "ipsative-assessment", "potential",
    "ipsative-progression-curve-green-line", "impact-influence-control-legacy",
    "presence", "safe-space-vs-soft-space", "listening-behaviours", "blame-and-ownership",
    "communication-hierarchy", "the-behaviours-of-the-accomplished",
]

def q(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return []

fails = 0
def check(name, ok, evidence=""):
    global fails
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + evidence) if evidence else ''}")
    if not ok:
        fails += 1

print("Passion Fit Concepts — audit\n")

# 1. all tagged notes embedded
row = q("SELECT count(*) total, count(embedding) emb FROM vault_notes WHERE tags && ARRAY['passionfit-concepts']")[0]
check("every PassionFit note is embedded", row["total"] == row["emb"], f"{row['emb']}/{row['total']} embedded")

# 2. all 19 concept notes present
have = {r["slug"] for r in q("SELECT slug FROM vault_notes WHERE type='concept' AND tags && ARRAY['befabulous-portal']")}
missing = [c for c in CONCEPTS if c not in have]
check("all 19 concept notes present", not missing, "missing: " + ", ".join(missing) if missing else f"{len(CONCEPTS)} concepts")

# 3. portal support pieces
need = {"glossary", "images-index", "category-core-accomplishment", "category-coachability", "category-philosophy-foundation"}
havep = {r["slug"] for r in q("SELECT slug FROM vault_notes WHERE tags && ARRAY['befabulous-portal'] AND type='reference'")}
check("portal glossary + image-index + 3 category overviews present", need <= havep, "missing: " + ", ".join(need - havep) if (need - havep) else "5/5")

# 4. no empty / tiny notes
tiny = q("SELECT title FROM vault_notes WHERE tags && ARRAY['passionfit-concepts'] AND (body IS NULL OR word_count < 10)")
check("no empty / tiny notes", not tiny, f"{len(tiny)} tiny: " + ", ".join(t["title"] for t in tiny[:3]) if tiny else "0")

# 5. no garbage (repeated-char) transcripts: body collapses to <1/4 its length
garb = q("SELECT title FROM vault_notes WHERE type IN ('video-transcript','seminar') AND length(regexp_replace(body,'(.)\\1{5,}','','g')) < length(body)/4 AND length(body) > 300")
check("no repeated-char garbage transcripts", not garb, f"{len(garb)}: " + ", ".join(t["title"] for t in garb[:3]) if garb else "clean")

# 6. no duplicate titles
dup = q("SELECT title FROM vault_notes WHERE tags && ARRAY['passionfit-concepts'] GROUP BY title HAVING count(*) > 1")
check("no duplicate-title notes", not dup, f"{len(dup)} dup titles" if dup else "0")

# 7. module registered + live
mod = q("SELECT slug, enabled, status FROM modules WHERE slug='pf-concepts'")
check("pf-concepts module registered + live", bool(mod) and mod[0].get("enabled") and mod[0].get("status") == "live",
      "row missing or disabled" if not (mod and mod[0].get("enabled")) else "live")

# 8. concept cross-link coverage (informational — concepts with 0 related)
lonely = []
for c in CONCEPTS:
    n = q(f"SELECT count(*) c FROM vault_notes WHERE tags && ARRAY['{c}'] AND tags && ARRAY['passionfit-concepts'] AND slug <> '{c}'")
    if n and n[0]["c"] == 0:
        lonely.append(c)
print(f"  [INFO] concepts with no related media (portal-only pages, not a failure): {', '.join(lonely) if lonely else 'none'}")

tot = q("SELECT count(*) c FROM vault_notes WHERE tags && ARRAY['passionfit-concepts']")[0]["c"]
print(f"\nCorpus: {tot} PassionFit notes. {'ALL CHECKS PASS' if not fails else str(fails) + ' CHECK(S) FAILED'}.")
sys.exit(1 if fails else 0)
