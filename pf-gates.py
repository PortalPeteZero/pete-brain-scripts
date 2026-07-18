#!/usr/bin/env python3
"""pf-gates.py — the PF Concepts brain quality gates (G1–G6 gating + W1 watch).

Defined by the audited execution plan [[2026-07-18-pf-brain-phases-1-4-execution-plan]].
The corpus population comes from the pf-corpus-definition registry record (tag + corpus_types)
— NEVER a hard-coded list here. Prints one line per gate; exit 0 only when every GATING
counter is zero (W1 is a non-gating watch).

    VAULT=/tmp/pbs python3 /tmp/pbs/pf-gates.py [--json]

Gates:
  G1  link floor    canonical concepts (<3 resolvable links) + support records (<1)  -> 0
  G2  audience      corpus records with no frontmatter.audience                      -> 0
  G3  influences    influence records not linked to a canonical concept, or with no
                    inbound link from any corpus record                              -> 0
  G4  graph sync    corpus records whose links array != their note_links rows        -> 0
  G5  variants      variant-needed masters w/o variant + stale variant_master_hash   -> 0
  G6  hygiene       content-tagged records with type outside the registry, or any
                    record with >60 links                                            -> 0
  W1  cap watch     records with >=55 links (non-gating, early warning)
"""
import json, os, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REG_PATH = "Projects/PA-PassionFit-Concepts/pf-corpus-definition.md"


def q(sql):
    r = subprocess.run([sys.executable, f"{VAULT}/cc-sql.py", sql],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        print(f"SQL ERROR: {r.stderr.strip()[:300]}", file=sys.stderr)
        sys.exit(2)
    return json.loads(r.stdout)


def main():
    reg = q(f"SELECT frontmatter->>'corpus_tag' AS tag, frontmatter->'corpus_types' AS types "
            f"FROM vault_notes WHERE vault_path='{REG_PATH}'")
    if not reg:
        print("FATAL: pf-corpus-definition registry record not found"); sys.exit(2)
    tag = reg[0]["tag"]
    types = reg[0]["types"]
    tlist = ",".join(f"'{t}'" for t in types)
    # One CTE reused by every gate: the corpus, its slugs, and per-record resolvable-link counts.
    corpus = (f"corpus AS (SELECT id, slug, vault_path, type, tags, links, frontmatter "
              f"FROM vault_notes WHERE tags && ARRAY['{tag}'] AND type IN ({tlist})), "
              f"slugs AS (SELECT slug FROM corpus), "
              f"resolved AS (SELECT c.id, count(s.slug) AS n FROM corpus c "
              f"LEFT JOIN LATERAL unnest(c.links) l(t) ON true "
              f"LEFT JOIN slugs s ON s.slug = l.t GROUP BY c.id)")

    g1 = q(f"WITH {corpus} SELECT count(*) AS n FROM corpus c JOIN resolved r ON r.id=c.id "
           f"WHERE (c.type='influence') = false AND "
           f"((c.type='concept' AND 'befabulous-portal' = ANY(c.tags) AND r.n < 3) "
           f"OR (NOT (c.type='concept' AND 'befabulous-portal' = ANY(c.tags)) AND r.n < 1))")[0]["n"]

    g2 = q(f"WITH {corpus} SELECT count(*) AS n FROM corpus "
           f"WHERE frontmatter->>'audience' IS NULL")[0]["n"]

    g3 = q(f"WITH {corpus} SELECT count(*) AS n FROM corpus i WHERE i.type='influence' AND ("
           f"NOT EXISTS (SELECT 1 FROM corpus c, unnest(i.links) l(t) "
           f"  WHERE c.slug=l.t AND c.type='concept' AND 'befabulous-portal' = ANY(c.tags)) "
           f"OR NOT EXISTS (SELECT 1 FROM corpus c2 WHERE i.slug = ANY(c2.links) AND c2.id<>i.id))")[0]["n"]

    g4 = q(f"WITH {corpus} SELECT count(*) AS n FROM corpus c WHERE "
           f"(SELECT coalesce(array_agg(DISTINCT dst_target ORDER BY dst_target),'{{}}') "
           f" FROM note_links WHERE src_id=c.id) IS DISTINCT FROM "
           f"(SELECT coalesce(array_agg(DISTINCT t ORDER BY t),'{{}}') FROM unnest(c.links) l(t))")[0]["n"]

    g5 = q(f"WITH {corpus} SELECT "
           f"(SELECT count(*) FROM corpus m WHERE m.frontmatter->>'audience'='variant-needed' "
           f" AND NOT EXISTS (SELECT 1 FROM corpus v WHERE v.frontmatter->>'variant_of'=m.vault_path)) + "
           f"(SELECT count(*) FROM corpus v JOIN vault_notes m ON m.vault_path=v.frontmatter->>'variant_of' "
           f" WHERE v.frontmatter ? 'variant_of' AND "
           f" v.frontmatter->>'variant_master_hash' IS DISTINCT FROM md5(embed_input(m.title,m.body))) AS n")[0]["n"]

    g6 = q(f"SELECT (SELECT count(*) FROM vault_notes WHERE tags && ARRAY['{tag}'] "
           f"AND type NOT IN ({tlist})) + "
           f"(SELECT count(*) FROM vault_notes WHERE tags && ARRAY['{tag}'] "
           f"AND coalesce(array_length(links,1),0) > 60) AS n")[0]["n"]

    w1 = q(f"WITH {corpus} SELECT count(*) AS n FROM corpus "
           f"WHERE coalesce(array_length(links,1),0) >= 55")[0]["n"]

    gates = {"G1 link floor": g1, "G2 audience": g2, "G3 influences": g3,
             "G4 graph sync": g4, "G5 variants": g5, "G6 hygiene": g6}
    if "--json" in sys.argv:
        print(json.dumps({**gates, "W1 cap watch": w1}))
    else:
        for name, n in gates.items():
            print(f"{name:16s} {n:5d}  {'PASS' if n == 0 else 'FAIL'}")
        print(f"{'W1 cap watch':16s} {w1:5d}  (non-gating)")
    sys.exit(0 if all(v == 0 for v in gates.values()) else 1)


if __name__ == "__main__":
    main()
