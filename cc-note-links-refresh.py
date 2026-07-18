#!/usr/bin/env python3
"""cc-note-links-refresh.py — rebuild the note_links table from vault_notes.links arrays.

The walkable-graph surfaces (/m/brain backlinks/outlinks, cc-knowledge-api backlinks|outlinks)
read note_links — which historically nothing maintained (stale since 2026-06-20; the PF audit's
central finding). This helper makes note_links a pure derivation of the links arrays:

  for each source record: note_links rows = (src_id, dst_target, dst_id)
  where dst_id resolves dst_target by slug (newest source_updated wins, matching
  cc-knowledge-api._resolve) or exact vault_path; unresolvable targets keep dst_id NULL.

Usage:
  VAULT=/tmp/pbs python3 cc-note-links-refresh.py --corpus          # PF corpus (registry-driven)
  VAULT=/tmp/pbs python3 cc-note-links-refresh.py --paths a.md b.md # specific vault_paths
  VAULT=/tmp/pbs python3 cc-note-links-refresh.py --all             # every record with links
"""
import json, os, subprocess, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REG_PATH = "Projects/PA-PassionFit-Concepts/pf-corpus-definition.md"


def q(sql):
    r = subprocess.run([sys.executable, f"{VAULT}/cc-sql.py", sql],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        print(f"SQL ERROR: {r.stderr.strip()[:400]}", file=sys.stderr); sys.exit(2)
    return json.loads(r.stdout) if r.stdout.strip() else []


def scope_sql(args):
    if "--all" in args:
        return "SELECT id FROM vault_notes WHERE coalesce(array_length(links,1),0) > 0"
    if "--paths" in args:
        paths = args[args.index("--paths") + 1:]
        inlist = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
        return f"SELECT id FROM vault_notes WHERE vault_path IN ({inlist})"
    # default: --corpus
    return (f"SELECT id FROM vault_notes WHERE tags && ARRAY[("
            f"SELECT frontmatter->>'corpus_tag' FROM vault_notes WHERE vault_path='{REG_PATH}')] "
            f"AND type IN (SELECT jsonb_array_elements_text(frontmatter->'corpus_types') "
            f"FROM vault_notes WHERE vault_path='{REG_PATH}')")


def main():
    scope = scope_sql(sys.argv[1:])
    out = q(f"""
WITH scope AS ({scope}),
del AS (DELETE FROM note_links WHERE src_id IN (SELECT id FROM scope) RETURNING 1),
targets AS (
  SELECT v.id AS src_id, t.dst_target
  FROM vault_notes v JOIN scope s ON s.id = v.id
  CROSS JOIN LATERAL unnest(v.links) AS t(dst_target)
),
resolved AS (
  SELECT tg.src_id, tg.dst_target,
    coalesce(
      (SELECT id FROM vault_notes WHERE vault_path = tg.dst_target
         OR vault_path = tg.dst_target || '.md' LIMIT 1),
      (SELECT id FROM vault_notes WHERE slug = tg.dst_target
         ORDER BY source_updated DESC NULLS LAST LIMIT 1)
    ) AS dst_id
  FROM targets tg
),
ins AS (
  INSERT INTO note_links (src_id, dst_target, dst_id)
  SELECT DISTINCT src_id, dst_target, dst_id FROM resolved
  RETURNING 1
)
SELECT (SELECT count(*) FROM del) AS deleted, (SELECT count(*) FROM ins) AS inserted,
       (SELECT count(*) FROM resolved WHERE dst_id IS NULL) AS unresolved
""")
    print(f"note_links refresh: deleted {out[0]['deleted']}, inserted {out[0]['inserted']}, "
          f"unresolved targets {out[0]['unresolved']}")


if __name__ == "__main__":
    main()
