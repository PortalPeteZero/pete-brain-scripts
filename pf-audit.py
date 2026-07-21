#!/usr/bin/env python3
"""pf-audit.py — health check for the Passion Fit Concepts corpus + module. Read-only.
Run anytime (esp. after pf-ingest):  VAULT=/tmp/pbs python3 /tmp/pbs/pf-audit.py
Exit 0 = all PASS · 1 = one or more FAIL. Prints a PASS/FAIL line per check with evidence."""
import os, re, json, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# The canonical concept set is DERIVED LIVE from the pf-framework-map taxonomy (non-influences
# family members) — the DB-driven source the /m/pf-concepts page renders. Never hard-code it here
# (a hard-coded 19 went blind when seven-steps-of-performance became the 20th, found 22 Jul 2026).

def q(sql):
    # Retry once on transient network failure — a dropped connection must FAIL LOUD, never
    # read as empty data (an SSL flap once made every concept look "missing", 22 Jul 2026).
    import time
    for attempt in (1, 2, 3):
        r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql], capture_output=True, text=True)
        try:
            return json.loads(r.stdout)
        except Exception:
            if attempt < 3:
                time.sleep(3 * attempt)
                continue
            print(f"SQL ERROR (after retry): {r.stderr.strip()[:200]}", file=sys.stderr)
            sys.exit(2)

fails = 0
def check(name, ok, evidence=""):
    global fails
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + evidence) if evidence else ''}")
    if not ok:
        fails += 1

print("Passion Fit Concepts — audit\n")

# 1. all tagged notes embedded AND FRESH (hash gate — catches stale-but-present vectors, not just NULLs)
row = q("SELECT count(*) total, count(*) FILTER (WHERE embedding IS NOT NULL AND embedded_hash = md5(embed_input(title,body))) emb FROM vault_notes WHERE tags && ARRAY['passionfit-concepts']")[0]
check("every PassionFit note is embedded & current", row["total"] == row["emb"], f"{row['emb']}/{row['total']} fresh")

# 2. all 19 concept notes present
wanted = {r["slug"] for r in q(
    "SELECT m.value AS slug FROM vault_notes v, "
    "jsonb_array_elements(v.frontmatter->'taxonomy'->'families') fam, "
    "jsonb_array_elements_text(fam->'members') m(value) "
    "WHERE v.slug='pf-framework-map' AND fam->>'key' <> 'influences'")}
have = {r["slug"] for r in q("SELECT slug FROM vault_notes WHERE type='concept' AND tags && ARRAY['befabulous-portal']")}
missing, extra = sorted(wanted - have), sorted(have - wanted)
check(f"concept pages exactly match the taxonomy ({len(wanted)})", not missing and not extra,
      (("missing: " + ", ".join(missing) + " ") if missing else "") + (("extra: " + ", ".join(extra)) if extra else "") or f"{len(have)} concepts")

# 3. portal support pieces
need = {"glossary", "images-index", "category-core-accomplishment", "category-coachability", "category-philosophy-foundation"}
havep = {r["slug"] for r in q("SELECT slug FROM vault_notes WHERE tags && ARRAY['befabulous-portal'] AND type='reference'")}
check("portal glossary + image-index + 3 category overviews present", need <= havep, "missing: " + ", ".join(need - havep) if (need - havep) else "5/5")

# 4. no empty / tiny notes
tiny = q("SELECT title FROM vault_notes WHERE tags && ARRAY['passionfit-concepts'] AND (body IS NULL OR word_count < 10)")
check("no empty / tiny notes", not tiny, f"{len(tiny)} tiny: " + ", ".join(t["title"] for t in tiny[:3]) if tiny else "0")

# 5. no garbage (repeated-char) transcripts: body collapses to <1/4 its length
# regex runs CLIENT-side: the PG backreference regex over transcript bodies blew the 90s
# statement window under load (22 Jul 2026) — fetch the ~1MB and collapse-test locally.
garb_rows = q("SELECT title, body FROM vault_notes WHERE tags && ARRAY['passionfit-concepts'] AND type IN ('video-transcript','seminar') AND length(body) > 300")
garb = [r for r in garb_rows if len(re.sub(r'(.)\1{5,}', '', r["body"])) < len(r["body"]) / 4]
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
for c in sorted(wanted):
    n = q(f"SELECT count(*) c FROM vault_notes WHERE tags && ARRAY['{c}'] AND tags && ARRAY['passionfit-concepts'] AND slug <> '{c}'")
    if n and n[0]["c"] == 0:
        lonely.append(c)
print(f"  [INFO] concepts with no related media (portal-only pages, not a failure): {', '.join(lonely) if lonely else 'none'}")

tot = q("SELECT count(*) c FROM vault_notes WHERE tags && ARRAY['passionfit-concepts']")[0]["c"]
print(f"\nCorpus: {tot} PassionFit notes. {'ALL CHECKS PASS' if not fails else str(fails) + ' CHECK(S) FAILED'}.")
sys.exit(1 if fails else 0)
