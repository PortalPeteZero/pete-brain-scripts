#!/usr/bin/env python3
"""pf-portal-sync.py — the ONE CC→portal write path: mirrors the athlete-safe PassionFit corpus
into the portal's `frank_knowledge` + `frank_concepts` tables (Frank's grounding).

Contract = the converged plan [[2026-07-18-pf-brain-portal-plan]] (round-8 convergence, 22 Jul 2026)
+ the Frank-mirror exception in [[pf-corpus-definition]]. Highlights:
  • FULL MIRROR DIFF every run — never incremental-by-trigger. Self-healing.
  • Eligibility: corpus-scoped shared MINUS superseded-bannered MINUS the pull-mirror-stamped
    MINUS type concept-diagram MINUS the pf-framework-map spine.
  • ALL inline wiki refs flatten to plain text; the graph lives in the columns.
  • Embeddings voyage-3.5-lite dim 1024, computed CC-side, written in the SAME row write as the
    content + mirrored_hash (no half-written state).
  • Writes exactly the three sync-owned tables; never cms_*. frank_usage purge is conditional
    (skips until the table exists at P2).
  • Ends with the mandatory GATE — all counters must print 0/==; exit non-zero otherwise.

Run (session-driven — the closing step of any session that adds PF material; no cron):
    VAULT=/tmp/pbs python3 /tmp/pbs/pf-portal-sync.py          # dry-run: report the diff + gate preview
    VAULT=/tmp/pbs python3 /tmp/pbs/pf-portal-sync.py --apply  # write, then run the gate
"""
import os, re, sys, json, time, hashlib, subprocess, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.join(VAULT, "Library/processes/secrets")
STAMP = "Mirrored from the LIVE portal CMS"
BANNER = "> [!warning] SUPERSEDED DRAFT"
VOYAGE_MODEL, DIM = "voyage-3.5-lite", 1024
APPLY = "--apply" in sys.argv


# ---------- CC side ----------
def cc_q(sql):
    for attempt in (1, 2, 3):
        r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql], capture_output=True, text=True)
        try:
            return json.loads(r.stdout)
        except Exception:
            if attempt < 3:
                time.sleep(3 * attempt)
                continue
            sys.exit(f"CC SQL failed after retries: {r.stderr.strip()[:200]}")


# ---------- portal side (REST, service role — bypasses the deny-all RLS by design) ----------
_pk = json.load(open(f"{SEC}/passion-fit-supabase-keys.json"))
PURL, PKEY = _pk["project_url"], _pk["service_role_key"]

def portal(method, path, body=None, prefer=None):
    for attempt in (1, 2, 3):
        try:
            req = urllib.request.Request(f"{PURL}/rest/v1/{path}",
                data=json.dumps(body).encode() if body is not None else None,
                headers={"apikey": PKEY, "Authorization": f"Bearer {PKEY}",
                         "Content-Type": "application/json",
                         **({"Prefer": prefer} if prefer else {})},
                method=method)
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if attempt < 3:
                time.sleep(3 * attempt); continue
            sys.exit(f"portal {method} {path} failed: {e.code} {e.read().decode()[:200]}")
        except Exception as e:
            if attempt < 3:
                time.sleep(3 * attempt); continue
            sys.exit(f"portal {method} {path} failed after retries: {e}")


# ---------- embeddings ----------
VKEY = open(f"{SEC}/voyage-api-key").read().strip()

def embed(texts):
    out = []
    for i in range(0, len(texts), 32):
        batch = texts[i:i + 32]
        for attempt in (1, 2, 3):
            try:
                req = urllib.request.Request("https://api.voyageai.com/v1/embeddings",
                    data=json.dumps({"input": batch, "model": VOYAGE_MODEL,
                                     "input_type": "document", "output_dimension": DIM}).encode(),
                    headers={"Authorization": f"Bearer {VKEY}", "Content-Type": "application/json"})
                data = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())
                out += [d["embedding"] for d in data["data"]]
                break
            except Exception as e:
                if attempt < 3:
                    time.sleep(5 * attempt); continue
                sys.exit(f"voyage embed failed: {e}")
    return out


# ---------- taxonomy + display names ----------
def load_taxonomy():
    row = cc_q("SELECT frontmatter->'taxonomy' AS tax FROM vault_notes WHERE slug='pf-framework-map'")[0]["tax"]
    concept_slugs, members = [], []  # members = (slug, family_key) over ALL families
    for fam in row["families"]:
        for m in fam["members"]:
            members.append((m, fam["key"]))
            if fam["key"] != "influences":
                concept_slugs.append(m)
    aliases = row.get("concept_key_aliases", {}) or {}
    return concept_slugs, members, aliases


def display_names(members):
    slugs = [m for m, _ in members]
    rows = cc_q("SELECT slug, title FROM vault_notes WHERE slug IN ("
                + ",".join("'" + s + "'" for s in slugs) + ")")
    titles = {r["slug"]: r["title"] for r in rows}
    out = {}
    for slug, fam in members:
        t = titles.get(slug, slug.replace("-", " ").title())
        t = re.sub(r"\s*\([^()]*\)\s*$", "", t).strip()  # strip ONE trailing parenthetical
        out[slug] = (t, fam)
    return out


# ---------- eligibility + mirrored form ----------
def eligible_records():
    return cc_q(
        "WITH ct AS (SELECT jsonb_array_elements_text(frontmatter->'corpus_types') t "
        "  FROM vault_notes WHERE vault_path='Projects/PA-PassionFit-Concepts/pf-corpus-definition.md') "
        "SELECT id, slug, title, body, type, tags, links FROM vault_notes "
        "WHERE tags && ARRAY['passionfit-concepts'] AND type IN (SELECT t FROM ct) "
        "AND frontmatter->>'audience'='shared' "
        "AND body NOT LIKE '%" + BANNER + "%' "
        "AND body NOT LIKE '%" + STAMP + "%' "
        "AND type <> 'concept-diagram' AND slug <> 'pf-framework-map' "
        "ORDER BY slug")


def normalise_tag(tag, aliases):
    t = tag[len("concept-"):] if tag.startswith("concept-") else tag
    return aliases.get(t, t)


WIKI = re.compile(r"!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

def mirror_form(rec, concept_slugs, aliases, names, mirror_slugs):
    concepts = sorted({normalise_tag(t, aliases) for t in (rec["tags"] or [])} & set(concept_slugs))

    def repl(m):
        target, alias = m.group(1).strip(), m.group(2)
        if alias:
            return alias.strip()
        t = normalise_tag(target.split("/")[-1].strip(), aliases)
        if t in names:
            return names[t][0]
        return t.replace("-", " ") if "-" in t and " " not in t else t
    body = WIKI.sub(repl, rec["body"] or "")

    links = sorted({l for l in (rec["links"] or []) if l in mirror_slugs})
    h = hashlib.md5(json.dumps([rec["title"], body, rec["type"], concepts, links],
                               ensure_ascii=False).encode()).hexdigest()
    return {"cc_id": rec["id"], "slug": rec["slug"], "title": rec["title"], "body": body,
            "type": rec["type"], "concepts": concepts, "links": links, "mirrored_hash": h}


# ---------- main ----------
def main():
    concept_slugs, members, aliases = load_taxonomy()
    names = display_names(members)
    recs = eligible_records()
    mirror_slugs = {r["slug"] for r in recs}
    forms = [mirror_form(r, concept_slugs, aliases, names, mirror_slugs) for r in recs]
    print(f"eligible: {len(forms)} records · taxonomy members: {len(names)}")

    existing = {r["cc_id"]: r["mirrored_hash"]
                for r in portal("GET", "frank_knowledge?select=cc_id,mirrored_hash")}
    changed = [f for f in forms if existing.get(f["cc_id"]) != f["mirrored_hash"]]
    keep_ids = {f["cc_id"] for f in forms}
    stale_ids = [i for i in existing if i not in keep_ids]
    print(f"diff: {len(changed)} to upsert · {len(stale_ids)} to delete · "
          f"{len(forms) - len(changed)} unchanged")

    if not APPLY:
        print("(dry-run — pass --apply to write; gate below reflects CURRENT portal state)")
    else:
        # embed changed FIRST, then one write per row carrying content + embedding + hash
        if changed:
            vecs = embed([f"{f['title']}\n\n{f['body']}" for f in changed])
            rows = [{**f, "embedding": "[" + ",".join(f"{x:.7f}" for x in v) + "]"}
                    for f, v in zip(changed, vecs)]
            for i in range(0, len(rows), 25):
                portal("POST", "frank_knowledge?on_conflict=cc_id", rows[i:i + 25],
                       prefer="resolution=merge-duplicates")
            print(f"upserted {len(rows)}")
        ids = list(stale_ids)
        for i in range(0, len(ids), 50):
            portal("DELETE", "frank_knowledge?cc_id=in.(" + ",".join(ids[i:i + 50]) + ")")
        if ids:
            print(f"deleted {len(ids)}")
        # frank_concepts: full rewrite each run
        crows = [{"slug": s, "display_name": n, "family": fam} for s, (n, fam) in names.items()]
        portal("POST", "frank_concepts?on_conflict=slug", crows, prefer="resolution=merge-duplicates")
        have = {r["slug"] for r in portal("GET", "frank_concepts?select=slug")}
        extra = sorted(have - set(names))
        for i in range(0, len(extra), 50):
            portal("DELETE", "frank_concepts?slug=in.(" + ",".join(extra[i:i + 50]) + ")")
        print(f"frank_concepts: {len(crows)} rows")
        # conditional frank_usage purge (table exists only from P2)
        try:
            portal("DELETE", "frank_usage?day=lt." + time.strftime("%Y-%m-%d", time.gmtime(time.time() - 90 * 86400)))
            print("frank_usage purge ran (90-day horizon)")
        except urllib.error.HTTPError:
            print("frank_usage purge skipped (table absent — created at P2)")

    # ---------- THE GATE ----------
    fails = 0
    def gate(name, ok, evidence):
        nonlocal fails
        print(f"  GATE [{'PASS' if ok else 'FAIL'}] {name} — {evidence}")
        if not ok:
            fails += 1
    prow = portal("GET", "frank_knowledge?select=cc_id,slug,title,body,type,concepts,links,mirrored_hash")
    pids = {r["cc_id"] for r in prow}
    pslugs = {r["slug"] for r in prow}
    gate("mirror count == eligible count", len(prow) == len(forms), f"{len(prow)} vs {len(forms)}")
    gate("0 rows failing eligibility", pids == keep_ids,
         f"{len(pids - keep_ids)} extra, {len(keep_ids - pids)} missing")
    bad_links = sum(1 for r in prow for l in (r["links"] or []) if l not in pslugs)
    gate("0 links to non-mirrored targets", bad_links == 0, str(bad_links))
    wiki_left = sum(1 for r in prow if "[[" in (r["body"] or ""))
    gate("0 '[[' in mirrored bodies", wiki_left == 0, str(wiki_left))
    fc = portal("GET", "frank_concepts?select=slug")
    gate("frank_concepts == taxonomy member count", len(fc) == len(names), f"{len(fc)} vs {len(names)}")
    nulls = portal("GET", "frank_knowledge?select=cc_id&embedding=is.null")
    gate("0 NULL embeddings", len(nulls) == 0, str(len(nulls)))
    want = {f["cc_id"]: f["mirrored_hash"] for f in forms}
    stale = sum(1 for r in prow if want.get(r["cc_id"]) != r["mirrored_hash"])
    gate("0 stale mirrored_hash", stale == 0, str(stale))
    print(("GATE: ALL PASS" if not fails else f"GATE: {fails} FAILURE(S)") +
          (" (dry-run)" if not APPLY else ""))
    sys.exit(1 if (fails and APPLY) else 0)


if __name__ == "__main__":
    main()
