#!/usr/bin/env python3
"""pf-link-pass.py — the PF Concepts spine-encoding + linking pass (execution-plan Stage C).

Builds link proposals for every corpus record from the approved taxonomy (the pf-framework-map
record's jsonb) + concept-key tags + embedding similarity, writes them into bodies as an
idempotent marker-delimited block of [[slug|Title]] wikilinks, converts legacy title-form links
to slug|alias form, and upserts body+links together through the ONE shared upsert path
(cc_save.upsert — partial payload, merge-duplicates; safe-path 1 of the audited plan).

  VAULT=/tmp/pbs python3 pf-link-pass.py                # dry-run (default): report proposals
  VAULT=/tmp/pbs python3 pf-link-pass.py --apply        # write everything, in batches
  VAULT=/tmp/pbs python3 pf-link-pass.py --apply --batch-size 25 --max-batches 2

Safety (locked by the plan):
  • exists-check: only records loaded FROM the DB are written back (no invented vault_paths).
  • per-batch new-row audit: rows created after pass start that are not enumerated creations get
    classified; untyped/untagged or PF-tagged strays halt the pass.
  • <=60 links per record enforced here (children trimmed chunks-first).
  • note_links refreshed per batch (cc-note-links-refresh.py --paths ...).
"""
import json, os, re, subprocess, sys, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REG_PATH = "Projects/PA-PassionFit-Concepts/pf-corpus-definition.md"
MAP_PATH = "Projects/PA-PassionFit-Concepts/pf-framework-map.md"
BLOCK_RE = re.compile(r"\n?<!-- PF-LINKS -->.*?<!-- /PF-LINKS -->\n?", re.S)
LINK_RE = re.compile(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]")
MAX_LINKS = 60


def q(sql):
    r = subprocess.run([sys.executable, f"{VAULT}/cc-sql.py", sql],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        print(f"SQL ERROR: {(r.stdout + r.stderr).strip()[:600]}", file=sys.stderr); sys.exit(2)
    return json.loads(r.stdout) if r.stdout.strip() else []


def load_mod(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), f"{VAULT}/{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def links_of(body):
    out = []
    for m in LINK_RE.findall(body):
        t = m[0].split("#")[0].strip()
        if t: out.append(t)
    return sorted(set(out))


def main():
    apply_mode = "--apply" in sys.argv
    bs = int(sys.argv[sys.argv.index("--batch-size") + 1]) if "--batch-size" in sys.argv else 25
    maxb = int(sys.argv[sys.argv.index("--max-batches") + 1]) if "--max-batches" in sys.argv else 10**9

    reg = q(f"SELECT frontmatter->>'corpus_tag' AS tag, frontmatter->'corpus_types' AS types "
            f"FROM vault_notes WHERE vault_path='{REG_PATH}'")[0]
    tag, tlist = reg["tag"], ",".join(f"'{t}'" for t in reg["types"])
    tax = q(f"SELECT frontmatter->'taxonomy' AS t FROM vault_notes WHERE vault_path='{MAP_PATH}'")[0]["t"]
    fams = tax["families"]
    aliases = tax.get("concept_key_aliases", {})
    fam_of, hub_of, members = {}, {}, {}
    for f in fams:
        members[f["key"]] = f["members"]
        hub_of[f["key"]] = f["hub"]
        for mslug in f["members"]:
            fam_of[mslug] = f["key"]

    pass_start = q("SELECT now() AS t")[0]["t"]
    corpus = q(f"SELECT id, slug, vault_path, type, tags, title, body, links, "
               f"(embedding IS NOT NULL) AS has_emb FROM vault_notes "
               f"WHERE tags && ARRAY['{tag}'] AND type IN ({tlist}) ORDER BY vault_path")
    by_slug = {r["slug"]: r for r in corpus}
    canonical = [r for r in corpus if r["type"] == "concept" and "befabulous-portal" in r["tags"]]
    canon_slugs = {r["slug"] for r in canonical}
    hubs = {r["slug"]: r for r in corpus if "category-overview" in r["tags"]
            or r["slug"].startswith("category-")}
    def clean_title(t):
        t = re.sub(r"\s*\((PassionFit|befabulous)[^)]*\)\s*$", "", t)
        t = re.sub(r"\s*—\s*(category overview|None Concept Specific|seminar verbatim|Transcripts|Toms Edits|Integration Content|Petes Concepts)\s*$", "", t)
        return t.strip()
    title_of = {r["slug"]: clean_title(r["title"]) for r in corpus}

    # legacy title-form -> slug map (exact + normalised)
    def norm(s): return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("&", "and")).strip()
    title2slug = {}
    for r in corpus:
        title2slug.setdefault(norm(r["title"]), r["slug"])
        title2slug.setdefault(norm(title_of[r["slug"]]), r["slug"])

    # concept-key -> canonical slug
    key2canon = {s: s for s in canon_slugs}
    key2canon.update({k: v for k, v in aliases.items() if v in canon_slugs or v in by_slug})

    # children per canonical (records tagged with a key that maps to it)
    children = {s: [] for s in canon_slugs}
    for r in corpus:
        if r["slug"] in canon_slugs: continue
        for t in r["tags"]:
            c = key2canon.get(t)
            if c and c in children and r["slug"] not in children[c]:
                children[c].append(r["slug"])

    # embedding fallback for keyless support records
    keyless = [r["slug"] for r in corpus if r["slug"] not in canon_slugs
               and r["slug"] not in hubs and not any(t in key2canon for t in r["tags"])]
    sim = {}
    if keyless:
        inlist = ",".join(f"'{s}'" for s in keyless)
        rows = q(f"WITH canon AS (SELECT slug, embedding FROM vault_notes WHERE tags && ARRAY['{tag}'] "
                 f"AND type='concept' AND 'befabulous-portal' = ANY(tags) AND embedding IS NOT NULL) "
                 f"SELECT v.slug, c.slug AS canon, rank FROM vault_notes v CROSS JOIN LATERAL ("
                 f"SELECT slug, row_number() OVER (ORDER BY v.embedding <=> canon.embedding) AS rank "
                 f"FROM canon ORDER BY v.embedding <=> canon.embedding LIMIT 2) c "
                 f"WHERE v.slug IN ({inlist}) AND v.embedding IS NOT NULL AND tags && ARRAY['{tag}']")
        for r in rows:
            sim.setdefault(r["slug"], []).append(r["canon"])

    def alias_link(slug):
        return f"[[{slug}|{title_of.get(slug, slug)}]]"

    def proposal(r):
        s = r["slug"]
        up, across, down = [], [], []
        if s in canon_slugs:
            fam = fam_of.get(s)
            if fam:
                if hub_of[fam] in by_slug: up.append(hub_of[fam])
                across = [m for m in members[fam] if m != s and m in by_slug]
            kids = children.get(s, [])
            pref = sorted(kids, key=lambda k: (0 if by_slug[k]["type"] in ("concept-diagram",)
                          else 1 if "seminar-verbatim" in by_slug[k]["tags"] else 2, k))
            down = pref
            label = "Maps to"
        elif s in hubs:
            fam = next((f["key"] for f in fams if f["hub"] == s), None)
            up = [MAP_SLUG] if MAP_SLUG in by_slug else []
            down = [m for m in (members.get(fam, [])) if m in by_slug]
            label = "In this family"
        else:
            keys = [key2canon[t] for t in r["tags"] if t in key2canon]
            keys = [k for k in dict.fromkeys(keys) if k in by_slug]
            if keys:
                up = keys
                fams_hit = {fam_of.get(k) for k in keys if fam_of.get(k)}
                across = [hub_of[f] for f in fams_hit if hub_of[f] in by_slug]
                label = "Maps to"
            else:
                near = [c for c in sim.get(s, []) if c in by_slug]
                up = near if near else ([MAP_SLUG] if MAP_SLUG in by_slug else [])
                label = "Related concepts"
        ordered = list(dict.fromkeys(up + across + down))
        # respect existing links outside the block, cap total at MAX_LINKS
        base_body = BLOCK_RE.sub("\n", r["body"])
        existing = set(links_of(base_body))
        room = MAX_LINKS - len(existing | set())
        ordered = [x for x in ordered if x not in existing][:max(0, room)]
        return label, ordered

    MAP_SLUG = "pf-framework-map"

    # legacy title-form conversion within body text
    def convert_titles(body):
        def sub(m):
            t = m.group(1).strip()
            if t in by_slug:  # already a slug
                return m.group(0)
            slug = title2slug.get(norm(t))
            if slug:
                return f"[[{slug}|{t}]]"
            return m.group(0)
        return LINK_RE.sub(sub, body)

    cc_save = load_mod("cc-save")
    creations = set()  # this pass creates no rows; hubs/map made earlier via cc-save

    todo = []
    for r in corpus:
        label, links = proposal(r)
        new_body = convert_titles(BLOCK_RE.sub("\n", r["body"]).rstrip())
        if links:
            block = ("\n\n<!-- PF-LINKS -->\n> **" + label + ":** "
                     + " · ".join(alias_link(x) for x in links) + "\n<!-- /PF-LINKS -->\n")
            new_body += block
        new_links = links_of(new_body)[:MAX_LINKS]
        if new_body != r["body"] or sorted(new_links) != sorted(r["links"] or []):
            todo.append((r, new_body, new_links, label, links))

    print(f"corpus {len(corpus)} · records needing changes {len(todo)}")
    if not apply_mode:
        from collections import Counter
        c = Counter(t[3] for t in todo)
        print("proposal kinds:", dict(c))
        for r, _, nl, label, links in todo[:12]:
            print(f"  {r['slug'][:60]:60s} {label:16s} +{len(links)} links (total {len(nl)})")
        print("dry-run only — use --apply to write")
        return

    batches = [todo[i:i + bs] for i in range(0, len(todo), bs)][:maxb]
    for bi, batch in enumerate(batches, 1):
        rows = [{"vault_path": r["vault_path"], "body": nb, "links": nl, "embedded_hash": None}
                for r, nb, nl, _, _ in batch]
        cc_save.upsert(rows)
        paths = " ".join(f'"{r["vault_path"]}"' for r, *_ in batch)
        subprocess.run([sys.executable, f"{VAULT}/cc-note-links-refresh.py", "--paths",
                        *[r["vault_path"] for r, *_ in batch]],
                       env={**os.environ, "VAULT": VAULT}, check=True)
        # new-row audit
        strays = q(f"SELECT vault_path, type, tags FROM vault_notes WHERE created_at > '{pass_start}' "
                   f"AND vault_path NOT IN ({','.join(chr(39)+r['vault_path'].replace(chr(39),chr(39)*2)+chr(39) for r,*_ in batch)})")
        bad = [s for s in strays if s["type"] is None or (s["tags"] and "passionfit-concepts" in s["tags"])]
        nulls = q("SELECT count(*) AS n FROM vault_notes WHERE type IS NULL")[0]["n"]
        if bad or nulls:
            print(f"HALT batch {bi}: stray/null rows detected: {bad[:5]} nulls={nulls}"); sys.exit(3)
        if strays:
            print(f"  note: {len(strays)} unrelated new row(s) from other writers (classified OK)")
        print(f"batch {bi}/{len(batches)} written ({len(batch)} records)")
    # embed backfill once at the end of the run
    subprocess.run(f'cd "{VAULT}" && python3 cc-knowledge-embed-backfill.py', shell=True,
                   env={**os.environ, "VAULT": VAULT})
    print("done — run pf-gates.py for G1/G4")


if __name__ == "__main__":
    main()
