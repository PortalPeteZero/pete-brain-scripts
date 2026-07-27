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
        # seminar-summary is corpus-registered + shared, but it reaches frank_knowledge ONLY at
        # phase 6 — in the SAME change that retires the 96 legacy transcript fragments (Pete,
        # 27 Jul 2026: replaced, not supplemented). Until then the summaries mirror to the
        # portal's `seminars` table via --seminars below, never to Frank. Remove this exclusion
        # in the phase-6 change and nowhere else.
        "AND type <> 'seminar-summary' "
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


# ---------- seminars mirror (--seminars) ----------
# Mirrors CC seminar-summary records into the portal `seminars` table (phases 2-3 of the
# seminars plan). The summary IS the member-facing product; transcripts are never mirrored.
# Conflict contract (Pete, 27 Jul 2026 — "CC authors, portal can edit"):
#   cc_summary_hash = md5 of summary_md AS THE SYNC WROTE IT. A portal-side edit makes the row's
#   current md5 differ. CC changed + portal edited → CONFLICT: row skipped, reported, gate fails
#   (resolve by reconciling, then --force-cc to overwrite). Portal edited + CC unchanged → the
#   edit stands (reported, gate passes). Never silently overwritten.
FORCE_CC = "--force-cc" in sys.argv

META_RE = re.compile(r"^\*\*(?P<meta>[^*]+)\*\*\s*$", re.M)

def seminar_records():
    return cc_q(
        "SELECT id, slug, title, body, frontmatter FROM vault_notes "
        "WHERE type='seminar-summary' AND frontmatter->>'audience'='shared' ORDER BY slug")


def seminar_form(rec, concept_slugs, aliases, names):
    fm = rec["frontmatter"] or {}
    body = (rec["body"] or "").replace("\r\n", "\n")

    # 1. strip the CC nav quote-block (leading '> ' lines) — CC-only chrome
    lines = body.split("\n")
    i = 0
    while i < len(lines) and (lines[i].startswith(">") or lines[i].strip() == ""):
        # keep a non-nav quote (e.g. the undated recovered-recording note) — nav block only
        if lines[i].startswith(">") and "[[pf-seminar-index" not in lines[i] \
           and "**Seminar**" not in lines[i] and not lines[i].startswith("> Concepts:") \
           and "Index:" not in lines[i]:
            break
        i += 1
    body = "\n".join(lines[i:]).lstrip("\n")

    # 2. led_by from the first bold meta line, then strip it + the Concepts line + first divider
    led_by = None
    m = META_RE.search(body)
    if m and ("·" in m.group("meta")):
        segs = [s.strip() for s in m.group("meta").split("·")]
        led = [s for s in segs if s.lower().startswith("led by")]
        led_by = led[0][7:].strip() if led else (segs[-1] if len(segs) >= 3 else None)
        body = body.replace(m.group(0) + "\n", "", 1)
    body = re.sub(r"^\*\*Concepts:\*\*[^\n]*\n", "", body, count=1, flags=re.M)
    body = re.sub(r"^\s*---\s*\n", "", body, count=1)

    # 3. flatten wikilinks (concept links become chips from the concepts column, not inline)
    def repl(mm):
        target, alias = mm.group(1).strip(), mm.group(2)
        if alias:
            return alias.strip()
        t = normalise_tag(target.split("/")[-1].strip(), aliases)
        if t in names:
            return names[t][0]
        return t.replace("-", " ") if "-" in t and " " not in t else t
    body = WIKI.sub(repl, body).strip() + "\n"

    # 4. standfirst = the "In one paragraph" section, single line
    sf = None
    sm = re.search(r"## In one paragraph\s*\n+(.+?)(?:\n---|\n## )", body, re.S)
    if sm:
        sf = re.sub(r"\s+", " ", sm.group(1)).strip()[:900]

    title = re.sub(r"^Seminar\s+(\([^)]*\)|[0-9-]{8,10})\s+—\s+", "", rec["title"]).strip()
    date = fm.get("date") or None
    if date in ("null", "None", ""):  # the undated charter session stores a literal placeholder
        date = None
    concepts = sorted({normalise_tag(c, aliases) for c in (fm.get("concepts") or [])} & set(concept_slugs))
    sort_order = int(date.replace("-", "")) if date else 0
    row = {
        "cc_slug": rec["slug"], "slug": rec["slug"], "title": title,
        "seminar_date": date,
        # frontmatter booleans can arrive as strings — "false" must not read truthy
        "date_confirmed": str(fm.get("date_confirmed", True)).lower() != "false",
        "duration": fm.get("duration"), "led_by": led_by, "standfirst": sf,
        "summary_md": body, "concepts": concepts,
        "transcript_chars": fm.get("transcript_chars"), "sort_order": sort_order,
        "is_published": True,
    }
    row["cc_summary_hash"] = hashlib.md5(body.encode()).hexdigest()
    row["synced_hash"] = hashlib.md5(json.dumps(
        [row[k] for k in ("title", "seminar_date", "date_confirmed", "duration", "led_by",
                          "standfirst", "summary_md", "concepts", "sort_order")],
        ensure_ascii=False).encode()).hexdigest()
    return row


def seminars_main():
    concept_slugs, members, aliases = load_taxonomy()
    names = display_names(members)
    forms = [seminar_form(r, concept_slugs, aliases, names) for r in seminar_records()]
    print(f"seminars eligible: {len(forms)}")

    existing = {r["cc_slug"]: r for r in portal(
        "GET", "seminars?select=cc_slug,synced_hash,cc_summary_hash,summary_md")}
    to_write, conflicts, portal_edited_kept, unchanged = [], [], [], 0
    for f in forms:
        ex = existing.get(f["cc_slug"])
        if not ex:
            to_write.append(f); continue
        cur_hash = hashlib.md5((ex["summary_md"] or "").encode()).hexdigest()
        edited = bool(ex.get("cc_summary_hash")) and cur_hash != ex["cc_summary_hash"]
        cc_changed = f["synced_hash"] != ex.get("synced_hash")
        if cc_changed and edited and not FORCE_CC:
            conflicts.append(f["cc_slug"])
        elif cc_changed:
            to_write.append(f)
        elif edited:
            portal_edited_kept.append(f["cc_slug"])
        else:
            unchanged += 1
    stale = [s for s in existing if s not in {f["cc_slug"] for f in forms}]
    print(f"diff: {len(to_write)} to upsert · {len(stale)} to delete · {unchanged} unchanged"
          f" · {len(portal_edited_kept)} portal-edited (kept) · {len(conflicts)} CONFLICT")
    for s in conflicts:
        print(f"  CONFLICT {s} — CC changed AND portal edited; resolve, then --force-cc")
    for s in portal_edited_kept:
        print(f"  note: {s} edited portal-side; CC unchanged, edit stands")

    if not APPLY:
        print("(dry-run — pass --apply to write; gate below reflects CURRENT portal state)")
    else:
        if to_write:
            vecs = embed([f"{f['title']}\n\n{f['summary_md']}" for f in to_write])
            rows = [{**f, "embedding": "[" + ",".join(f"{x:.7f}" for x in v) + "]"}
                    for f, v in zip(to_write, vecs)]
            for i in range(0, len(rows), 10):
                portal("POST", "seminars?on_conflict=cc_slug", rows[i:i + 10],
                       prefer="resolution=merge-duplicates")
            print(f"upserted {len(rows)}")
        for s in stale:
            portal("DELETE", f"seminars?cc_slug=eq.{s}")
        if stale:
            print(f"deleted {len(stale)}")

    # gate
    fails = 0
    def gate(name, ok, evidence):
        nonlocal fails
        print(f"  GATE [{'PASS' if ok else 'FAIL'}] {name} — {evidence}")
        if not ok:
            fails += 1
    prow = portal("GET", "seminars?select=cc_slug,slug,title,seminar_date,sort_order,summary_md,"
                         "standfirst,led_by,synced_hash,cc_summary_hash&limit=1000")
    gate("portal count == eligible count", len(prow) == len(forms), f"{len(prow)} vs {len(forms)}")
    nulls = portal("GET", "seminars?select=cc_slug&embedding=is.null")
    gate("0 NULL embeddings", len(nulls) == 0, str(len(nulls)))
    gate("0 unresolved conflicts", len(conflicts) == 0, str(len(conflicts)))
    wiki_left = sum(1 for r in prow if "[[" in (r["summary_md"] or ""))
    gate("0 '[[' in summary_md", wiki_left == 0, str(wiki_left))
    want = {f["cc_slug"]: f["synced_hash"] for f in forms}
    kept = set(portal_edited_kept) | set(conflicts)
    stale_h = sum(1 for r in prow if r["cc_slug"] not in kept
                  and want.get(r["cc_slug"]) != r["synced_hash"])
    gate("0 stale synced_hash (excluding kept portal edits)", stale_h == 0, str(stale_h))
    bad_sort = sum(1 for r in prow if r["seminar_date"]
                   and r["sort_order"] != int(r["seminar_date"].replace("-", "")))
    gate("sort_order matches date", bad_sort == 0, str(bad_sort))
    no_sf = sum(1 for r in prow if not r["standfirst"])
    gate("standfirst present on every row", no_sf == 0, str(no_sf))
    print(("GATE: ALL PASS" if not fails else f"GATE: {fails} FAILURE(S)") +
          (" (dry-run)" if not APPLY else ""))
    sys.exit(1 if (fails and APPLY) else 0)


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
    seminars_main() if "--seminars" in sys.argv else main()
