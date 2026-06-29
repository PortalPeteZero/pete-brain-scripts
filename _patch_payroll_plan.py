import json, subprocess, os, sys
env = dict(os.environ); env["VAULT"]="/tmp/pbs"
def sql(q):
    r = subprocess.run(["python3","/tmp/pbs/cc-sql.py",q],capture_output=True,text=True,env=env)
    return r.stdout
TITLE="CD + El Atico payroll sections — Spanish nóminas (plan)"
rows=json.loads(sql(f"SELECT body, frontmatter FROM vault_notes WHERE title='{TITLE}' LIMIT 1"))
body=rows[0]["body"]
orig=body

# A) Gating line in "The two sections"
old_g='- **Gating**: **Pete-only / owner-private** (default for salary data — Pete can widen later). Decided 27 Jun.'
new_g=('- **Gating** (split — decided 27 Jun, re-confirmed in the 27 Jun re-audit):\n'
'   - **CD payroll (`/m/cd-payroll`) → owner-private** (`tier=private`), matching the existing `cd-finance`. Use the **newer `requireOwner()` double-gate** on top of the standard module gate (the pattern the live `to-pay` page uses) — stronger than the older sygma-payroll single gate, right for salary data.\n'
'   - **El Atico payroll (`/m/atico-payroll`) → passcode** (`tier=passcode`, passcode **`atico526`** — the SAME code as the live `el-atico-finances` page, so María/gerente uses one code for both El Atico private pages). María handles Atico payroll + already has the Drive PDFs, so page access is intended.')
assert old_g in body, "gating block not found"
body=body.replace(old_g,new_g)

# B) "what to pay" — add the no-integration note (anchor on the headline Coste Empresa line)
old_w='   - Plus the headline **Coste Empresa** (total cost) = Σ of the three. *(Audit: lumping these overstated a single "pay now" figure and hid that SS is next-month and IRPF is quarterly.)*'
new_w=old_w+'\n   - ⚠ **Self-contained — do NOT wire this into the Finance-hub `to-pay` / payables module** (Pete, 27 Jun re-audit: "no need to do any need-to-pay stuff with this"). The "what to pay" view lives ONLY inside the payroll pages; payroll obligations are not pushed into `lib/finance/data.ts` / the To Pay dashboard.'
assert old_w in body, "what-to-pay anchor not found"
body=body.replace(old_w,new_w)

# C) Phase 2 owner-gate wording
old_p='`subsection` = `Internal`**, `tier=private`; owner gate. **No `lib/sections.ts` edit** (areas already in `SECTION_ORDER`). NOT the Finance section.'
new_p='`subsection` = `Internal`**; **per-entity tier** (CD `tier=private` + `requireOwner()`; Atico `tier=passcode` = `atico526`). **No `lib/sections.ts` edit** (areas already in `SECTION_ORDER`). Stays under the entity areas — **NOT the (now-existing) Finance section** (Pete re-confirmed 27 Jun: "payroll stays in the entity"; consistent with how `cd-finance`/`el-atico-finances` are placed).'
assert old_p in body, "phase2 anchor not found"
body=body.replace(old_p,new_p)

# D) Decisions "Resolved" line
old_d='**Resolved (27 Jun):** Access → **Pete-only / owner-private** · Placement → **each under its own business area** (CD→Canary Detect, Atico→El Atico; DB module-row `section`/`subsection`, no code change) · Sources → Romero Del Mas + MVP Lanzarote (both checked) · Backfill → Jan–Jun 2026, sourced.'
new_d='**Resolved (27 Jun, incl. the 27 Jun re-audit):** Access → **CD owner-private (`tier=private` + `requireOwner()`); Atico passcode (`atico526`, shared with `el-atico-finances`)** · "What to pay" → **self-contained in the payroll pages, NOT wired into the `to-pay` payables module** · Placement → **each under its own business area, NOT the new Finance section** (CD→Canary Detect, Atico→El Atico; DB module-row `section`/`subsection`, no code change) · Sources → Romero Del Mas + MVP Lanzarote (both checked) · Backfill → Jan–Jun 2026, sourced.'
assert old_d in body, "decisions anchor not found"
body=body.replace(old_d,new_d)

# E) Re-audit addendum at the end of the Audit section's risk list (append a dated re-audit block before ## Notes)
anchor='## Notes\n'
readd=('## Re-audit (27 Jun 2026, 2nd pass) — vs live CC after 89 commits since the plan baseline\n'
'Re-checked against the current HEAD (`212089a`; the plan was first verified at `088cf27` — **89 commits / 233 files have landed since**). From several angles:\n'
'- ✅ **Template unchanged & safe** — none of the 89 commits touched `lib/payroll/db.ts`, `app/m/sygma-payroll/`, `lib/access.ts` or `lib/sections.ts`. The copy targets are exactly as described; pooler-only salary security intact.\n'
'- ✅ **No collision** — `payroll_es` schema does not exist; `/m/cd-payroll` + `/m/atico-payroll` routes are free. Build genuinely not started.\n'
'- ✅ **Placement holds** — `Canary Detect` + `El Atico` still in `SECTION_ORDER` with an `Internal` subsection; `cd-finance` (live, `tier=private`, `Canary Detect/Internal`, DB row + `[slug]` route) is the exact working precedent. No `lib/sections.ts` edit needed.\n'
'- 🟠 **Gating model moved on → upgraded** — the newest owner-private module (`to-pay`) uses `requireOwner()` as a double-gate over the standard module gate; CD payroll now adopts that (stronger than the sygma-payroll single gate). `tier=private` confirmed = owner-only.\n'
'- 🟢 **Atico gating decided = passcode** (`atico526`) so María can use the page (she runs Atico payroll + has the Drive PDFs); CD stays owner-private. (The split mirrors the live `el-atico-finances` passcode vs `cd-finance` private.)\n'
'- 🟢 **New Finance hub noted, deliberately not used** — a `Finance` section now exists (`to-pay`, `bank-details`, `lib/finance/data.ts`). Pete: payroll stays in the entity areas and does NOT feed the To Pay payables module. The "what to pay" view stays self-contained.\n'
'- ℹ️ **Freshest precedent to crib from** — `el-atico-finances` was built into a full ~19-file native sub-app since the plan (layout.tsx, actions.ts, reports, transactions). Useful reference for the El Atico-area page shape, though the payroll security model still copies the `payroll` pooler pattern, not `lib/ea/data.ts`.\n\n')
assert anchor in body
body=body.replace(anchor, readd+anchor, 1)

# update the baseline line in Notes
body=body.replace('Verified against live code (`088cf27`)','Verified against live code (`088cf27`; re-audited vs `212089a` on 27 Jun — see Re-audit section)')

path="/tmp/pbs/Projects/PA-Command-Centre/files/cd-atico-payroll-plan-2026-06-27.md"
with open(path,"w") as f: f.write(body)
print("edits applied:", orig!=body, "| new length:", len(body), "| file:", path)
