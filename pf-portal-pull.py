#!/usr/bin/env python3
"""pf-portal-pull.py — refresh the CC PF Concepts Brain from the LIVE portal CMS (portal → CC, one-way).

The PassionFit online learning portal (SygmaSol/passion-fit, Supabase sghmdrvtlatjijbkqfld,
cms_* tables) is the SSOT for all portal-facing concept content (Pete, 20 Jul 2026). The CC
concepts brain's canonical portal notes (frontmatter source: befabulous-portal-cms) must mirror
it — never the other way round. This tool READS the portal (REST GETs only) and upserts the 24
mirror notes through the corpus's safe write path (cc_save.upsert), preserving each note's
<!-- PF-LINKS --> block. After --apply, it runs pf-link-pass --apply, cc-note-links-refresh
--corpus and pf-gates.py, and fails loudly if any gate is non-zero.

  VAULT=/tmp/pbs python3 /tmp/pbs/pf-portal-pull.py            # dry-run: report drift per note
  VAULT=/tmp/pbs python3 /tmp/pbs/pf-portal-pull.py --apply    # write + link pass + gates

Manual-run only (no cron) per the flag-before-cron rule.
"""
import json, os, re, subprocess, sys, urllib.request, importlib.util, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REF = "sghmdrvtlatjijbkqfld"
LINKS_BLOCK = re.compile(r"<!-- PF-LINKS -->.*?<!-- /PF-LINKS -->", re.S)
LINK_RE = re.compile(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]")

def q(sql):
    r = subprocess.run([sys.executable, f"{VAULT}/cc-sql.py", sql], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT})
    if r.returncode != 0:
        print("SQL ERROR:", (r.stdout + r.stderr)[:500], file=sys.stderr); sys.exit(2)
    return json.loads(r.stdout) if r.stdout.strip() else []

def portal():
    keys = json.loads(q("SELECT value FROM secrets WHERE name='passion-fit-supabase-keys.json'")[0]["value"])
    assert keys["project_ref"] == REF, f"secrets ref {keys['project_ref']} != expected {REF} — STOP"
    url, sk = keys["project_url"].rstrip("/"), keys["service_role_key"]
    def get(path):
        req = urllib.request.Request(f"{url}/rest/v1/{path}",
                                     headers={"apikey": sk, "Authorization": f"Bearer {sk}"})
        return json.loads(urllib.request.urlopen(req).read())
    return get

def render_blocks(blocks):
    out = []
    for b in sorted(blocks, key=lambda x: x["display_order"]):
        c = b["content"] or {}
        t = b["block_type"]
        if t == "heading":
            out.append("#" * min(int(c.get("level", 2)) + 1, 5) + " " + str(c.get("text", "")).strip())
        elif t == "paragraph":
            out.append(str(c.get("text", "")).strip())
        elif t == "callout":
            title = str(c.get("title", "")).strip(); body = str(c.get("body", "")).strip()
            out.append("> **" + title + "**" + ("\n> " + body.replace("\n", "\n> ") if body else ""))
        elif t == "list":
            items = c.get("items") or []
            mark = lambda i: (f"{i+1}." if c.get("ordered") else "-")
            out.append("\n".join(f"{mark(i)} {str(x).strip()}" for i, x in enumerate(items)))
    return "\n\n".join(x for x in out if x)

def media_resources(media_rows):
    """Module media section from cms_media (video/podcast/article rows keyed by module slug)."""
    out = {}
    for r in sorted(media_rows, key=lambda x: (x["media_type"], x.get("display_order") or 0)):
        flag = "" if r.get("is_active", True) else " (inactive)"
        out.setdefault(r["media_type"], []).append(f"- {r['title']}{flag} {r['url']}".strip())
    return "\n\n".join(f"**{k.title()}s:**\n" + "\n".join(v) for k, v in out.items())

def main():
    apply_mode = "--apply" in sys.argv
    get = portal()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    cats = {c["id"]: c for c in get("cms_categories?select=*")}
    mods = get("cms_modules?select=*&order=display_order.asc")
    blocks = get("cms_content_blocks?select=module_id,block_type,content,display_order")
    media = get("cms_media?select=module_id,media_type,title,url,is_active,display_order")
    sups = get("cms_supporting_concepts?select=*&order=display_order.asc")
    supblocks = get("cms_supporting_concept_blocks?select=supporting_concept_id,block_type,content,display_order")
    catblocks = get("cms_category_intro_blocks?select=category_id,block_type,content,display_order")
    gloss = get("cms_glossary?select=term,definition,is_active&order=display_order.asc")
    images = get("cms_images?select=key,public_url,alt_text,caption&order=key.asc")

    # --- explicit title → CC slug mapping (abort on any miss) ---
    def slugify(t):
        t = t.lower().replace("&", "and").replace("–", " ").replace("/", " ")
        return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t)).strip("-")
    SLUG_FIX = {
        "impact-influence-control-legacy": "impact-influence-control-legacy",
        "ipsative-progression-curve-green-line": "ipsative-progression-curve-green-line",
        "intuition-scale-and-learning-behaviours": "intuition-scale-learning-behaviours",
        "direction-support-matrix": "direction-support-matrix",
    }
    notes = q("SELECT vault_path, slug, links, body, frontmatter FROM vault_notes "
              "WHERE frontmatter->>'source'='befabulous-portal-cms'")
    by_slug = {n["slug"]: n for n in notes}
    # two notes carry long path-derived slugs; the filename is the stable key
    for n in notes:
        base = n["vault_path"].rsplit("/", 1)[-1][:-3]
        by_slug.setdefault(base, n)
    by_path = {n["vault_path"]: n for n in notes}

    updates = []  # (note, new_body_without_links_block)

    for m in mods:
        s = slugify(m["title"]); s = SLUG_FIX.get(s, s)
        n = by_slug.get(s)
        if not n: print(f"ABORT: no CC note for module '{m['title']}' (slug {s})"); sys.exit(2)
        cat = cats.get(m["category_id"], {})
        mb = [b for b in blocks if b["module_id"] == m["id"]]
        mm = [x for x in media if x["module_id"] == s]
        body = (f"{(m.get('description') or '').strip()}\n\n"
                f"**Category:** {cat.get('title','')}\n\n"
                f"**Subtitle:** {(m.get('subtitle') or '').strip()}\n\n"
                f"## Concept\n\n{render_blocks(mb)}\n")
        res = media_resources(mm)
        if res: body += f"\n## Module media (from the portal CMS)\n\n{res}\n"
        updates.append((n, body))

    for spc in sups:
        s = slugify(spc["title"]); s = SLUG_FIX.get(s, s)
        n = by_slug.get(s)
        if not n: print(f"ABORT: no CC note for supporting concept '{spc['title']}' (slug {s})"); sys.exit(2)
        sb = [b for b in supblocks if b["supporting_concept_id"] == spc["id"]]
        body = (f"{(spc.get('description') or '').strip()}\n\n"
                f"**Category:** Supporting Concepts\n\n"
                f"## Concept\n\n{render_blocks(sb)}\n")
        pod = (spc.get("podcast_title") or "").strip(); podu = (spc.get("podcast_url") or "").strip()
        if pod or podu: body += f"\n**Podcast:** {pod} {podu}\n"
        updates.append((n, body))

    CATMAP = {"Core Accomplishment Behaviours (ECPC – Easy Peasy)": "Personal/passion-fit/concepts/portal/category-core-accomplishment.md",
              "Coachability Behaviours": "Personal/passion-fit/concepts/portal/category-coachability.md",
              "Philosophy Foundation Models": "Personal/passion-fit/concepts/portal/category-philosophy-foundation.md"}
    for cid, c in cats.items():
        n = by_path.get(CATMAP.get(c["title"], ""))
        if not n: print(f"ABORT: no CC note for category '{c['title']}'"); sys.exit(2)
        cb = [b for b in catblocks if b["category_id"] == cid]
        mlist = "\n".join(f"- {m['title']}" for m in mods if m["category_id"] == cid)
        body = (f"{(c.get('description') or '').strip()}\n\n{render_blocks(cb)}\n\n"
                f"## Modules in this category\n\n{mlist}\n")
        updates.append((n, body))

    n = by_path.get("Personal/passion-fit/concepts/portal/glossary.md")
    if not n: print("ABORT: glossary note missing"); sys.exit(2)
    terms = "\n\n".join(f"**{g['term']}** — {g['definition']}" for g in gloss if g.get("is_active", True))
    updates.append((n, f"The portal glossary, A–Z ({sum(1 for g in gloss if g.get('is_active', True))} live terms).\n\n{terms}\n"))

    n = by_path.get("Personal/passion-fit/concepts/portal/images-index.md")
    if not n: print("ABORT: images-index note missing"); sys.exit(2)
    imx = "\n".join(f"- `{i['key']}` — {(i.get('alt_text') or i.get('caption') or '').strip()} {i['public_url']}" for i in images)
    updates.append((n, f"Index of the portal's CMS image library ({len(images)} images).\n\n{imx}\n"))

    # --- write ---
    header = (f"> [!info] Mirrored from the LIVE portal CMS (Supabase {REF}) — the portal is the SSOT "
              f"for this content. Last pulled {now} by pf-portal-pull.py. Edit in the portal admin, "
              f"never here.\n\n")
    rows = []
    for n, new_content in updates:
        old_block = LINKS_BLOCK.search(n["body"] or "")
        block = ("\n\n" + old_block.group(0) + "\n") if old_block else ""
        new_body = header + new_content.rstrip() + block
        changed = re.sub(r"Last pulled [^b]+by", "", new_body) != re.sub(r"Last pulled [^b]+by", "", n["body"] or "")
        links = sorted({m[0].split("#")[0].strip() for m in LINK_RE.findall(new_body) if m[0].strip()})
        print(f"{'UPDATE' if changed else 'nochange':8s} {n['vault_path']}  (links kept: {len(links)})")
        if changed:
            rows.append({"vault_path": n["vault_path"], "body": new_body, "links": links, "embedded_hash": None})

    print(f"\n{len(rows)} of {len(updates)} notes need updating.")
    if not apply_mode:
        print("Dry-run. Re-run with --apply to write."); return
    if rows:
        spec = importlib.util.spec_from_file_location("cc_save", f"{VAULT}/cc-save.py")
        cc_save = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc_save)
        cc_save.upsert(rows)
        print(f"UPSERTED {len(rows)} notes.")
    # frontmatter sync-stamp (safe path 2: read whole jsonb, merge, write back)
    for nrow, _ in updates:
        fm = q(f"SELECT frontmatter FROM vault_notes WHERE vault_path='{nrow['vault_path']}'")[0]["frontmatter"]
        fm["source"] = "befabulous-portal-cms"
        fm["source_detail"] = f"live portal Supabase {REF} (new Next.js build)"
        fm["source_synced"] = now
        fm_json = json.dumps(fm).replace("'", "''")
        q(f"UPDATE vault_notes SET frontmatter='{fm_json}'::jsonb WHERE vault_path='{nrow['vault_path']}' RETURNING id")
    print("frontmatter sync-stamped on all 24.")
    subprocess.run(f'cd "{VAULT}" && python3 pf-link-pass.py --apply', shell=True)
    subprocess.run(f'cd "{VAULT}" && python3 cc-note-links-refresh.py --corpus', shell=True)
    g = subprocess.run(f'cd "{VAULT}" && python3 pf-gates.py', shell=True, capture_output=True, text=True)
    print(g.stdout[-1500:])
    if g.returncode != 0:
        print("GATES NON-ZERO — investigate before calling this done."); sys.exit(2)

if __name__ == "__main__":
    main()
