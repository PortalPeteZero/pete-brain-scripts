#!/usr/bin/env python3
"""ee-index-gen.py — regenerate workflow-design's Banked knowledge index FROM THE DB (plan P5.5).

The hand-kept index lagged the corpus (the audit caught it omitting notes). This regenerates the
section from vault_notes so it CANNOT lag: every non-ephemeral training-enquiries note of type
reference/process/plan is listed with its title, grouped by type. Run weekly by ee-selfaudit, or
by hand after banking a new reference note.

Usage:  VAULT=/tmp/pbs python3 /tmp/pbs/ee-index-gen.py [--dry]
"""
import os, sys, json, subprocess, importlib.util, datetime as dt

VAULT = os.environ.get("VAULT", "/tmp/pbs")
START = "## Banked knowledge index"
END_MARK = "<!-- /banked-index -->"

def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__); sys.exit(0)
    dry = "--dry" in sys.argv
    tl = _load("telog", f"{VAULT}/te-log.py")
    rows = tl.cc_sql(
        "SELECT slug, title, type FROM vault_notes "
        "WHERE tags @> ARRAY['training-enquiries'] AND type IN ('reference','process','plan','index') "
        "AND slug <> 'workflow-design' ORDER BY type, title")
    groups = {}
    for r in rows:
        groups.setdefault(r["type"], []).append(r)
    body_lines = [START, "",
                  f"*(AUTO-GENERATED from vault_notes by ee-index-gen.py — {dt.date.today().isoformat()}. "
                  "Do not hand-edit this section; bank a note with the training-enquiries tag and regenerate. "
                  "Worked enquiry/reply notes are retrieved semantically at Step 1 and are not listed here.)*", ""]
    label = {"reference": "References (facts & SSOTs)", "process": "Processes", "plan": "Plans", "index": "Indexes"}
    for t in ("reference", "process", "plan", "index"):
        if t in groups:
            body_lines.append(f"**{label[t]}**")
            for r in groups[t]:
                body_lines.append(f"- [[{r['slug']}]] — {r['title']}")
            body_lines.append("")
    body_lines.append(END_MARK)
    new_section = "\n".join(body_lines)

    b = tl.cc_sql("SELECT body FROM vault_notes WHERE slug='workflow-design'")[0]["body"]
    i = b.find(START)
    if i < 0:
        print("⛔ index section not found"); sys.exit(2)
    j = b.find(END_MARK)
    tail = b[j + len(END_MARK):] if j > i else ""     # first run: everything after START is the old hand list
    nb = b[:i] + new_section + tail
    print(f"index: {len(rows)} notes across {len(groups)} groups")
    if dry:
        print(new_section[:800]); return
    tl.cc_sql("UPDATE vault_notes SET body = $IDX$" + nb + "$IDX$ WHERE slug='workflow-design'")
    print("✓ workflow-design index regenerated")

if __name__ == "__main__":
    main()
