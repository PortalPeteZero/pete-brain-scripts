#!/usr/bin/env python3
"""whereis.py — "where does X live, and what feeds it?" in ONE read-only lookup.

The Business OS scattered "where things are" across several stores (crons, data_map,
modules, drive_files, vault_notes). Rather than know-which-table-and-column (and guess
when unsure), run this: it keyword-matches the term across every store and prints what
the SYSTEM actually records — the deployed script, the data flow (produces/consumes),
the page route, the Drive home, related knowledge. If it returns nothing for a claim
you're about to make, you DON'T KNOW — say so, don't invent.

Usage:  VAULT=/tmp/pbs python3 whereis.py "garmin"
"""
import sys, re, json, subprocess, os

HERE = os.path.dirname(os.path.abspath(__file__))

# A CC lookup that ERRORS must never be silently reported as an empty absence —
# that false "NOTHING FOUND" is the exact signal that tells Claude a home doesn't
# exist, and it's the root failure this tool exists to prevent. So distinguish
# error from empty: retry once, then flag it so main() warns instead of claiming absence.
_Q_ERROR = {"hit": False}

def q(sql, _retry=True):
    r = subprocess.run([sys.executable, os.path.join(HERE, "cc-sql.py"), sql],
                       capture_output=True, text=True, env={**os.environ})
    if r.returncode != 0:
        if _retry:
            return q(sql, _retry=False)   # one retry (transient throttle/network)
        _Q_ERROR["hit"] = True            # genuine error — do NOT let this read count as "empty"
        return []
    try:
        out = json.loads(r.stdout)
        return out if isinstance(out, list) else []
    except Exception:
        return []                         # returncode 0 but non-JSON = genuinely empty

def main():
    term = " ".join(sys.argv[1:]).strip()
    safe = re.sub(r"[^a-zA-Z0-9 _.-]", "", term)
    if not safe:
        sys.exit('usage: whereis.py "<thing>"   e.g. whereis.py "health dashboard"')
    L = f"%{safe}%"
    print(f"== whereis {safe!r} ==  (read-only; the system's own record)\n")
    hits = 0

    # PROPERTIES — the SSOT for "where does this website/app live + how do I connect"
    # (property_declarations). Runs FIRST and token-matches (name/domain/repo) so a natural
    # query like "canary detect website" resolves to the repo+host+stack, not an old note.
    # This is the fix for the recurring "grepped a mirror and cloned the wrong repo" failure.
    _STOP = {"website", "site", "web", "app", "the", "main", "page", "form", "com",
             "www", "our", "my", "a", "of", "and", "repo", "github", "how", "connect", "where"}
    _tokens = [t for t in re.split(r"[^a-z0-9]+", safe.lower()) if len(t) >= 2 and t not in _STOP]
    if _tokens:
        props = q("SELECT name, f FROM property_declarations")
        scored = []
        for p in props:
            f = p.get("f") or {}
            name = (p.get("name") or "").lower()
            doms = " ".join(f.get("domains") or []).lower()
            gh = (f.get("github") or "").lower()
            # Weight NAME > DOMAIN > REPO so the property the query actually NAMES ranks first.
            # Deliberately NOT matching `department` -- every sibling property shares it, which made
            # the whole department tie and buried the real match (the bug this fix exists to kill).
            score = (3 * sum(1 for t in _tokens if t in name)
                     + 2 * sum(1 for t in _tokens if t in doms)
                     + 1 * sum(1 for t in _tokens if t in gh))
            if score:
                scored.append((score, p, f))
        scored.sort(key=lambda x: -x[0])
        if scored:
            hits += len(scored)
            print("PROPERTIES  (property_declarations — the SSOT for where a site/app lives + how to connect):")
            for score, p, f in scored[:5]:
                dec = f.get("declared") or {}
                stack = dec.get("stack") or f.get("stack") or ""
                print(f"  • {p['name']}  [{f.get('status') or '?'}]  domain(s): {', '.join(f.get('domains') or []) or '-'}")
                print(f"      repo    = {f.get('github') or '-'}  (branch {f.get('prod_branch') or 'main'})")
                host = f.get("hosting") or ""
                vp = f.get("vercel_project") or ""
                vt = f.get("vercel_team") or ""
                print(f"      hosting = {host or '-'}"
                      + (f"  · vercel project {vp}" if vp else "")
                      + (f" (team {vt})" if vt else ""))
                if f.get("supabase_ref"):
                    print(f"      supabase= {f.get('supabase_ref')}")
                if stack:
                    print(f"      stack   = {stack}")
                anal = ", ".join(x for x in [
                    (f"GA4 {f.get('ga4')}" if f.get('ga4') else ""),
                    (f"GTM {f.get('gtm')}" if f.get('gtm') else ""),
                    (f"GSC {f.get('gsc')}" if f.get('gsc') else ""),
                    (f"Ahrefs {f.get('ahrefs')}" if f.get('ahrefs') else ""),
                ] if x)
                if anal:
                    print(f"      analytics= {anal}")
            print()

    rows = q(f"SELECT key, script_file, host, schedule, produces, consumes, status FROM crons "
             f"WHERE key ILIKE '{L}' OR script_file ILIKE '{L}' OR produces ILIKE '{L}' "
             f"OR consumes ILIKE '{L}' OR what ILIKE '{L}' ORDER BY key LIMIT 12")
    if rows:
        hits += len(rows); print("CRONS  (the writer + data flow — script_file is the DEPLOYED script):")
        for r in rows:
            print(f"  • {r['key']}  [{r['host']} · {r.get('schedule') or '-'} · {r.get('status') or '?'}]  script_file={r['script_file']}")
            if r.get('produces'): print(f"      writes → {str(r['produces'])[:150]}")
            if r.get('consumes'): print(f"      reads  ← {str(r['consumes'])[:150]}")
        print()

    rows = q(f"SELECT module_key, slug, title, section, reads FROM modules "
             f"WHERE module_key ILIKE '{L}' OR slug ILIKE '{L}' OR title ILIKE '{L}' ORDER BY module_key LIMIT 12")
    if rows:
        hits += len(rows); print("CC PAGES  (commandcentre.info — clone fresh: command-centre repo, app/m/<slug>):")
        for r in rows:
            print(f"  • /m/{r['slug']}  \"{r['title']}\"  [{r.get('section') or ''}]  key={r['module_key']}")
            rd = r.get('reads') or []
            if rd:
                print(f"      feeds ← {', '.join(rd)}")
        print()

    # LINEAGE — term names a table/feed: who WRITES it (cron) + who READS it (CC page).
    # Closes the page→feed→writer loop (modules.reads, Phase 5.3). Verified, not guessed.
    writers = q(f"SELECT key, script_file, produces FROM crons WHERE produces ILIKE '{L}' ORDER BY key LIMIT 12")
    readers = q(f"SELECT slug, reads FROM modules "
                f"WHERE EXISTS (SELECT 1 FROM unnest(reads) x WHERE x ILIKE '{L}') ORDER BY slug LIMIT 20")
    if writers or readers:
        hits += len(writers) + len(readers)
        print("LINEAGE  (table/feed → writer cron + reader page — script_file is the DEPLOYED writer):")
        for w in writers:
            print(f"  writer →  {w['key']}  script_file={w['script_file']}  produces {str(w['produces'])[:90]}")
        for rr in readers:
            m = [x for x in (rr.get('reads') or []) if safe.lower() in x.lower()] or (rr.get('reads') or [])
            print(f"  reader ←  /m/{rr['slug']}  reads {', '.join(m)}")
        print()

    # DATA-MAP — the SSOT for "what kind of thing lives where". Token-matched (score domain>home>access+notes)
    # over the small (~48-row) table so natural phrasings resolve ("family finance", "email routing rules",
    # "bank statements", "my tasks"); whole-phrase ILIKE fallback so a bare-stopword/zero-token query never loses hits.
    dm_rows = []
    if _tokens:
        dm = q("SELECT domain, home, access, notes FROM data_map ORDER BY sort")
        scored = []
        for r in dm:
            dom = (r.get('domain') or '').lower(); home = (r.get('home') or '').lower()
            rest = ((r.get('access') or '') + ' ' + (r.get('notes') or '')).lower()
            score = (3 * sum(1 for t in _tokens if t in dom)
                     + 2 * sum(1 for t in _tokens if t in home)
                     + 1 * sum(1 for t in _tokens if t in rest))
            if score:
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        dm_rows = [r for _, r in scored[:8]]
    if not dm_rows:
        dm_rows = q(f"SELECT domain, home, access FROM data_map "
                    f"WHERE domain ILIKE '{L}' OR home ILIKE '{L}' OR access ILIKE '{L}' OR notes ILIKE '{L}' ORDER BY sort LIMIT 8")
    if dm_rows:
        hits += len(dm_rows); print("DATA-MAP  (domain → home — the SSOT for what kind of thing lives where):")
        for r in dm_rows:
            print(f"  • {r['domain']} → {r['home']}   ({r.get('access') or ''})")
        print()

    rows = q(f"SELECT drive, path FROM drive_files WHERE name ILIKE '{L}' ORDER BY path LIMIT 8")
    if rows:
        hits += len(rows); print("DRIVE FILES  (sample; full index = drive_files):")
        for r in rows:
            print(f"  • [{r['drive']}] {r['path']}")
        print()

    rows = q(f"SELECT title, type FROM vault_notes WHERE title ILIKE '{L}' OR slug ILIKE '{L}' "
             f"ORDER BY updated_at DESC LIMIT 6")
    if rows:
        hits += len(rows); print("KNOWLEDGE  (vault_notes — cc-knowledge-api.py for full text):")
        for r in rows:
            print(f"  • {str(r['title'])[:84]}  ({r.get('type') or ''})")
        print()

    if _Q_ERROR["hit"]:
        print("⚠ LOOKUP ERRORED (not empty). One or more reads failed (throttle/network) — this is NOT an "
              "'it doesn't exist' answer. RE-RUN whereis before concluding a home is missing.")
    elif not hits:
        print("NOTHING FOUND. The system has no record matching that term — you do NOT know where it lives.")
        print("Widen the term, or check drive_files / vault_notes directly. Do not guess and state it as fact.")

if __name__ == "__main__":
    main()
