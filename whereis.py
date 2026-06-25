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

def q(sql):
    r = subprocess.run([sys.executable, os.path.join(HERE, "cc-sql.py"), sql],
                       capture_output=True, text=True, env={**os.environ})
    try:
        out = json.loads(r.stdout)
        return out if isinstance(out, list) else []
    except Exception:
        return []

def main():
    term = " ".join(sys.argv[1:]).strip()
    safe = re.sub(r"[^a-zA-Z0-9 _.-]", "", term)
    if not safe:
        sys.exit('usage: whereis.py "<thing>"   e.g. whereis.py "health dashboard"')
    L = f"%{safe}%"
    print(f"== whereis {safe!r} ==  (read-only; the system's own record)\n")
    hits = 0

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

    rows = q(f"SELECT domain, home, access FROM data_map "
             f"WHERE domain ILIKE '{L}' OR home ILIKE '{L}' OR access ILIKE '{L}' OR notes ILIKE '{L}' ORDER BY sort LIMIT 8")
    if rows:
        hits += len(rows); print("DATA-MAP  (domain → home — HIGH-LEVEL; trust crons/pages above for component detail):")
        for r in rows:
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

    if not hits:
        print("NOTHING FOUND. The system has no record matching that term — you do NOT know where it lives.")
        print("Widen the term, or check drive_files / vault_notes directly. Do not guess and state it as fact.")

if __name__ == "__main__":
    main()
