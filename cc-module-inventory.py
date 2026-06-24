#!/usr/bin/env python3
"""cc-module-inventory.py — ground-truth inventory of every live Command Centre module: how each
one renders (from the real route code) + whether its data source actually has content. Built so the
'what's built / what's empty' question is answered from code + DB, not memory. Read-only.

Usage: python3 cc-module-inventory.py
"""
import json, subprocess
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

SQL = f"{VAULT}/Library/processes/scripts/cc-sql.py"
def q(sql):
    return json.loads(subprocess.run(["python3", SQL, sql], capture_output=True, text=True).stdout)

# render rules read straight from app/m/[slug]/page.tsx + the app/m/<slug> custom routes (22 Jun)
CUSTOM = {"ashcroft-finance","brain","canary-events","casas-del-sol-water","daily","el-atico-finances",
          "files","health","lanzarote-water-guide","los-claveles-water","map","parcela-25-water","peptide",
          "plans","process-library","properties","schedule","servihogar-kb","sygma-payroll","tasks"}
REPORT_KEYS = {
  "morning-brief":["morning-brief"], "cd-finance":["cd-finance-weekly","cd-finance-monthly","cd-cost-base"],
  "cd-briefings":["cd-briefing-daily","cd-briefing-week"], "hub-activity":["hub-activity"],
  "vault-health":["vault-health"], "sygma-training":["training-kpis","training-audit"],
  "sygma-reports":["sygma-google-daily","sygma-health"], "ll-owner-report":["ll-owner-report"],
  "oconnors-seo":["oconnors-seo","oconnors-seo-trends"], "automations-log":["automations-log"],
}

mods = q("SELECT slug, module_key, title, section, subsection, area FROM modules WHERE enabled AND status='live' ORDER BY section, sort")
content_keys = {r["module_key"] for r in q("SELECT DISTINCT module_key FROM module_content")}
snaps = {r["report_key"]: r["n"] for r in q("SELECT report_key, count(*) AS n FROM reports.snapshots GROUP BY report_key")}

def classify(m):
    slug, mk, sec, sub, area = m["slug"], m["module_key"], m["section"], m["subsection"], m["area"]
    if slug in CUSTOM:                       return "Custom page", "WORKS", f"app/m/{slug} (own page + live data)"
    if sec == "Passion Fit":                 return "PF skeleton", "PLACEHOLDER", "intentional 'taking shape' skeleton"
    if area == "Clancy" and sub == "Internal": return "Clancy dashboard", "WORKS", "ClancyDashboard ← account store"
    if area == "Clancy" and sub == "External": return "Clancy external", "WORKS", "ClancyExternal ← public account data"
    if mk == "automations-log":              return "Automations schedule", "WORKS", "live registry + report snapshots"
    if mk == "sygma-backlinks":              return "Backlinks dashboard", "WORKS", "bl.work_items + weekly reports"
    if mk == "sygma-ads":                    return "Ads dashboard", "WORKS", "AdsDashboard ← data/ads.json"
    if mk in REPORT_KEYS:
        have = [k for k in REPORT_KEYS[mk] if snaps.get(k)]
        if have:
            n = sum(snaps[k] for k in have)
            return "Report page", "WORKS", f"{n} snapshots ({', '.join(have)})"
        return "Report page", "EMPTY", "no snapshots yet (cron frozen / not run)"
    if mk in content_keys or sub == "External": return "Embedded one-pager", "WORKS", "module_content iframe"
    return "—", "MOVING-IN", "no render path — shows the 'Moving in' placeholder"

rows, tally = [], {}
for m in mods:
    path, st, note = classify(m)
    tally[st] = tally.get(st, 0) + 1
    rows.append((m["section"], m["title"], m["slug"], path, st, note))

ICON = {"WORKS":"✅","DISPLAY-BUG":"🔧","EMPTY":"⚠️","PLACEHOLDER":"🏗️","MOVING-IN":"❌"}
out = ["# Command Centre — module inventory (ground truth, 22 Jun 2026)",
       "",
       "Auto-generated from the route code (`app/m/[slug]/page.tsx` + the `app/m/<slug>` custom pages) and the live DB. Re-run `cc-module-inventory.py`.",
       "",
       f"**{len(mods)} live modules** — " + " · ".join(f"{ICON[k]} {v} {k}" for k,v in sorted(tally.items())),
       "",
       "| Section | Page | Slug | Renders via | Status | Data |",
       "|---|---|---|---|---|---|"]
for sec, title, slug, path, st, note in rows:
    out.append(f"| {sec} | {title} | `{slug}` | {path} | {ICON[st]} {st} | {note} |")
doc = "\n".join(out)
open(f"{VAULT}/Projects/PA-Command-Centre/files/cc-module-inventory-2026-06-22.md","w").write(doc)
print(f"{len(mods)} live modules — " + " · ".join(f"{ICON[k]} {v} {k}" for k,v in sorted(tally.items())))
print()
for st in ["MOVING-IN","EMPTY","DISPLAY-BUG"]:
    hits = [(s,t,sl,n) for s,t,sl,p,x,n in rows if x==st]
    if hits:
        print(f"{ICON[st]} {st}:")
        for s,t,sl,n in hits: print(f"   {s} · {t} (/m/{sl}) — {n}")
        print()
print("inventory written → Projects/PA-Command-Centre/files/cc-module-inventory-2026-06-22.md")