---
name: cd-cost-report
description: |
  Generates the CD Cost-Base report — Canary Detect's monthly + weekly cost burn analysis with baseline (averaged-from-history) vs per-period actuals from Odoo, plus Sygma intercompany extras from Xero and manual cash items. Outputs polished HTML with cover summary + per-month tabs.

  Use this skill whenever Pete says: "run cd cost report", "run cd costs report for [period]", "cd cost-base", "what's our burn rate", "show me CD costs for [period]", "regenerate cost report", "publish the cost report", "what does CD need each week/month", or any variation thereof. Also use when Pete asks about CD fixed costs, break-even, or runway.

  Output: a single combined HTML file with tabs (cover + one per closed month), published to the Command Centre (commandcentre.info/m/cd-finance, Cost base tab — Private).
version: 1.0
trigger_phrases:
  - "run cd cost report"
  - "run cd costs report"
  - "cd cost report for"
  - "cd cost-base"
  - "cd cost base"
  - "what's our burn rate"
  - "show me cd costs"
  - "regenerate cost report"
  - "publish cost report"
  - "what does cd need each"
  - "cd fixed costs"
  - "cd break-even"
created: 2026-05-11
updated: 2026-05-11
---

<!-- external-service-routing pre-flight: before any Gmail / Drive / Calendar / Sheets / Docs / Xero / Odoo / GSC / GA4 / Ads / Vision / Geocoding / Sentry / Cloudflare / Vercel operation in this skill, see [[external-service-routing]]. Helper-first. -->

# CD Cost-Base Report Skill

The CD cost-base report answers: **"what does Canary Detect need every week / month / day to keep the lights on?"** and **"what did each closed month actually cost?"**.

It complements the revenue-side `cd-monthly-finance-email` cron — which is turnover-only — with a cost-side view, broken down by baseline (averaged) vs per-period (actuals).

---

## Files this skill owns

- `skills/cd-cost-report/SKILL.md` — this file
- `skills/cd-cost-report/scripts/build_data.py` — pulls Odoo + applies manual data → writes `/tmp/ytd_data.json`
- `skills/cd-cost-report/scripts/render_html.py` — reads `/tmp/ytd_data.json` → writes the HTML report
- `Businesses/canary-detect/finance/cost-base-reports/baseline-config.md` — locked baseline definition
- `Businesses/canary-detect/finance/cost-base-reports/2026-cost-base-YTD.html` — local rendered output
- Live mirror: **commandcentre.info/m/cd-finance** Cost base tab (`reports.snapshots` key `cd-cost-base`) — the Vercel project was retired 11 Jun 2026

---

## The model — read this first

### Two sides

**Baseline (steady-state)** — what CD needs every month with no jobs at all. Drawn from a fixed 4-month rolling window (currently Jan-Apr 2026). Same baseline applied to every monthly report so figures are comparable across months. Locked items:

1. Wages (gross) — Odoo 640000 4-mo rolling
2. Employer social security — Odoo 642000 4-mo rolling, with April override €2,603.21 from nóminas (gestoría hadn't posted yet)
3. **Casual labour (regular)** — manual flat €1,733/mo (€400/wk). Anonymised label in public output; this is the regular ongoing casual labour cost. **No names. No "undeclared" or "in the black" language anywhere in the rendered HTML.**
4. Rent — Odoo 621000 net of IGIC 4-mo rolling (~€2,000/mo)
5. Van rental — manual flat €870/mo (3 × €290, NOT active Jan-Apr 2026 → showing as 0; toggle when first invoice posts)
6. Sygma intercompany rental — manual flat ~€1,070/mo (£920 × current GBP/EUR). Invoices stopped flowing Dec 2025; manual pencil-line until they resume
7. Fuel — Odoo 628000 fuel subset 4-mo rolling (Petroleos Marinos + Combustibles Canarios + Gasib + Domarmen + Comercial Fuelanza)
8. Utilities non-fuel — Odoo 628000 non-fuel subset 4-mo rolling (Orange, ASESORES extras, small)
9. Insurance — Odoo 625000 4-mo rolling (smooths quarterly billing)
10. Bank fees — Odoo 626000 4-mo rolling
11. Asesoría laboral (Alma Maria Perdomo) — 623000/629000 subset 4-mo rolling (~€180/mo)
12. Software subscriptions — flat €200/mo (Odoo SA + small)
13. Recurring marketing — RECURRING_MKT 4-mo rolling: David Gainford (Sep 2023 - Dec 2025, ~€129/mo) → ONBrand Solutions (Jan 2026+ at €150/mo, rebrand) + CANARY ISLAND IMPACT (Gazette Life, started Feb 2026, stepped from €749 → €321)
14. Quarterly marketing — smoothed €200/mo (Suelos Secor / Monster Radio quarterly ads)
15. Other taxes — Odoo 631000 4-mo rolling

**Per-period (actuals)** — everything else, pulled live from Odoo for the report period:

- Materials (600000) — Suministros Cabrera, Ferreteria Tías, Industriales, Würth, Carburos Metálicos (gas bottles routed here despite the name), etc.
- Other supplies ex-LeakGuard (602000 minus ITransformers)
- **LeakGuard hardware** — ITransformers Labs (602000 + 629003) + DIRECTRANS customs (when Thingslog imports happen). **Separate bucket per Pete's rule, NOT bundled with Materials.**
- Subcontractors (607000) — Daryl Edwards, Fabian Salazar, Hermanos Tavío Santana
- Vehicle repairs (622000) — TALLER TINAJUMA, Neumáticos Machín, ITV
- Property commissions (623001) — Property Management Solutions per-referral
- Transport / courier (624000)
- Uniforms (629001)
- One-off marketing (627000 minus recurring + quarterly)
- Other services (629000)
- Indemnities + fines (641000 + 678001)

### Per-period extras

- **Casual Labour Including Overtime (additional)** — bundled, anonymised label combining overtime + any project-specific casual workers for the period. Sits on top of the regular casual labour baseline line. Pete-provided per period. Current Jan-Apr 2026 composition (kept in `CASUAL_LABOUR_ADDITIONAL` dict in build_data.py): Jan-Mar 2026 = €800 overtime + €1,733 project-specific = €2,533/mo; Apr 2026 = €800 overtime only. **Internal distinction is real (regular vs additional) but PUBLIC LABEL IS BUNDLED: "Casual Labour Including Overtime". No names ever.**
- **Sygma intercompany extras (from Xero)** — pulled live from Xero `https://api.xero.com/api.xro/2.0/Invoices` for Sygma → CD invoices each month. The regular £920 rental sits in baseline; everything else is itemised and **distributed into its proper bucket as if purchased direct** (consultancy → Subcontractors, water meters → LeakGuard hardware, cameras → Equipment one-off, Festool bags → Materials, trademarks → IP / Legal one-off, van insurance annual → Vehicle insurance annual). **Pete's rule: don't lump Sygma extras together — route by what they actually are.**

### Auto-substitute rule

If an Odoo fixed-line actual is < 30% of baseline AND baseline > €500, the report substitutes the baseline value into the variance display (flagged with a "used baseline" chip). Insurance is excepted because its quarterly billing pattern means zero months are expected.

Specific known overrides:
- **April 2026 employer social**: gestoría hadn't posted, override with €2,603.21 from the 4 nóminas (Arturo €734.98 + Nicola €326.66 + Tom €881.29 + Kevin €660.28).

### Things NOT in scope

- El Atico costs (separate books — not on this Odoo)
- Sygma Solutions Ltd costs (separate entity)
- Pete and Dave salaries (owner-directors, no payroll draw)

---

## Vocabulary — important

- **Baseline window**: the 4 trailing closed months used to compute baseline. Currently 2026-01-01 → 2026-04-30. Update when running for a period beyond April once May closes.
- **Period**: the specific month or week the report covers.
- **Variable / per-period actuals**: Odoo data for the period.
- **Manual additions**: cash items that don't flow through Odoo.
- **Sygma extras**: Sygma → CD invoices from Xero on top of the rental.

---

## How to run

### When Pete asks for the standard YTD report

1. Confirm the locked baseline items (the skill's `scripts/baseline-config.md`).
2. **Ask Pete**: "Any cash overtime to add for these months beyond the €800/mo default? Any new manual lines (vans started? Sygma rental resumed billing? new cash workers)?" If silent, use the current defaults baked into `build_data.py`.
3. Run the skill's bundled generators (in this skill's `scripts/` directory):
   ```bash
   python3 scripts/build_data.py
   python3 scripts/render_html.py
   ```
4. Output saved to `/tmp/2026-cost-base-YTD.html`.
5. **Publish to the Command Centre** (the live mirror since 11 Jun 2026 — the old cd-cost-base.vercel.app project is DELETED; do not recreate it):
   ```bash
   python3 - <<'PY'
   import sys, datetime
   sys.path.insert(0, "/tmp/pbs")
   import cc_publish
   html = open("/tmp/2026-cost-base-YTD.html").read()
   period = datetime.date.today().isoformat()
   ok = cc_publish.publish("cd-cost-base", period, {"subject": f"CD cost base YTD — {period}", "html": html})
   print("published" if ok else "PUBLISH FAILED")
   PY
   ```
   The report renders at **commandcentre.info/m/cd-finance** (Cost base tab, Private). Verify: open the tab, confirm the new period chip appears and the figures match the local HTML.
6. Tell Pete: link to local HTML + the CC page (commandcentre.info/m/cd-finance?tab=cd-cost-base) + headline figures.

### When Pete asks for a single month

Same as above but adjust `MONTHS` in `build_data.py` to just that month. The baseline window stays Jan-Apr 2026 (the 4 trailing closed months).

### When Pete asks for a single week

Use the monthly baseline ÷ 4.33 for the weekly burn figure. For variable per-period actuals, narrow the period_lines fetch to that week's date range. Same render logic.

### When Pete extends the baseline window (e.g. now we're in June, baseline = Feb-May)

Update `BASELINE_START` and `BASELINE_END` constants at the top of `build_data.py`. Anything in the manual SYGMA_EXTRAS / JUAN_CASH_WAGES / CASH_OT dicts continues to apply per month as listed.

---

## Where the data comes from

### Odoo (camello-blanco-sl.odoo.com)

Single-company instance. All CD bookkeeping. Helper: `/tmp/pbs/odoo-api.py`. JSON-RPC. Auth via the API key in the CC `odoo-api-configuration` note (`VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py "odoo api configuration"`); the key is materialised at `/tmp/pbs/Library/processes/secrets/`.

Key accounts:
- 640000 Sueldos y Salarios
- 642000 Seguridad social a cargo de la empresa
- 621000 Arrendamientos y cánones (rent + leases)
- 622000 Reparaciones y conservación
- 623000 Servicios de profesionales independientes
- 623001 Gastos por comisiones
- 624000 Transportes
- 625000 Primas de seguros
- 626000 Servicios bancarios y similares
- 627000 Publicidad, propaganda y RRPP
- 628000 Suministros (fuel + utilities + Orange)
- 629000 Otros servicios
- 629001 Uniforms
- 629003 Subscripciones
- 631000 Otros tributos
- 600000 Compras de mercaderías (Materials)
- 602000 Compras de otros aprovisionamientos
- 607000 Trabajos realizados por otras empresas (Subcontractors)
- 641000 Indemnizaciones
- 678001 Gastos excepcionales — multas de tráfico
- 472700 Hacienda Pública, IGIC soportado (recoverable input tax — NOT a cost)
- 475100 Hacienda Pública, retenciones (withholding tax payable — not a cost line)

### Xero (Sygma Solutions Limited)

For Sygma → CD intercompany invoices. Helper: `/tmp/pbs/xero-api.py`. OAuth 2.0 refresh-token flow. Tokens at `/tmp/pbs/Library/processes/secrets/xero-tokens.json`. Org ID: `2cb5c90b-1f4e-4d5c-a820-39c8a01b81c8`.

Query pattern:
```python
url = f'https://api.xero.com/api.xro/2.0/Invoices?InvoiceNumbers={inv_num}'
# Or pull all by contact + date:
url = f'https://api.xero.com/api.xro/2.0/Invoices?where=Contact.Name=="CAMELLO BLANCO SL"&Date>=DateTime(2026,01,01)'
```

Each invoice has line items with descriptions — read these to bucket correctly. Examples seen Jan-Apr 2026:
- "Consultancy Fees (JW)" — recurring monthly consultancy → Subcontractors bucket
- "Consultancy Fees (WM)" / "Consultancy fees (Water M)" — LeakGuard water meters → LeakGuard hardware bucket
- "RM65 Rotamixer" / "Cameras for drain inspection" — Equipment one-off bucket
- "Festool 496186 Filter Bag" — Materials bucket
- "Trademark registration" — IP / Legal one-off bucket
- "Van insurance annual" — Vehicle insurance (annual) bucket

### Nóminas (Gmail from MVP Lanzarote)

Pete's gestoría is Laura @ MVP Lanzarote (laura@mvplanzarote.com / accounts@mvplanzarote.com). She emails Pete monthly PDFs of nóminas + tax filings.

**IMPORTANT**: MVP Lanzarote serves BOTH CD AND El Atico. Filter nóminas to CD-only employees (Arturo Martinez Castro, Brown Nicola Jane, Robertson Thomas Ian, Martinez Hidalgo Kevin Keni). EA-side nóminas (e.g. "Mark") are NOT CD.

Search pattern:
```bash
python3 gmail-api.py search 'from:mvplanzarote.com newer_than:60d'
```

April 2026 nóminas saved at: `Businesses/canary-detect/owner-private/payroll/2026-04/2026-04-camello-blanco-nominas.pdf`.

---

## Data corrections (one-time, locked-in)

These were resolved during initial build (2026-05-11) and now baked into `build_data.py`:

1. **Rent is €2,000 net (621000), not €2,140 gross**. €140 is IGIC (recoverable). Pete prefers the net figure for the burn-rate view.
2. **David Gainford = ONBrand Solutions** (rebrand Dec 2025 → Jan 2026). Treated as single continuous marketing line for averaging.
3. **CANARY ISLAND IMPACT (Gazette) stepped down** from €749/mo to €321/mo between March and April 2026.
4. **Sygma intercompany rental should be GBP → EUR converted**. Laura sometimes books at flat 1:1 (€920 instead of £920×FX). Use £920 × current FX (~1.16) = €1,070 in baseline.
5. **ITransformers Labs is the LeakGuard hardware supplier** (NOT a SaaS subscription, despite Odoo booking some lines on 629003 Subscripciones). Always bucket to "LeakGuard hardware".
6. **Pete and Dave Poxon are owner-directors, no salary** — CD payroll is 4 employees only.
7. **One trademark Sygma invoice (INV-11292 €1,199.44 March)** was excluded by Pete — don't add.
8. **INV-11144 (Feb €2,120.76)** is split: €1,420.76 trademark registrations + €700 annual van insurance.

---

## Output specifications

### Local HTML

Path: `Businesses/canary-detect/finance/cost-base-reports/2026-cost-base-YTD.html`

Structure:
- Header (navy gradient)
- Tab bar (YTD Summary + one per closed month)
- Cover page: headline YTD total + dual stat cards (baseline burn / YTD actual averages) + monthly breakdown table + monthly trend bar chart + info-grid explaining methodology
- Per-month pages: orange headline with pills + baseline burn table + fixed-line variance check + manual additions + variable per-period adds + dark grand-total bar

Styling: CSS variables for navy + orange palette. Tabular numerals throughout. Hover states on data rows. Mobile-responsive (collapses to single column under 640px).

### Live home (NOT Vercel)

The report lives ONLY in the Command Centre — **commandcentre.info/m/cd-finance** (Cost base tab, Private, owner-gated). The old `cd-cost-base.vercel.app` was **deleted 11 Jun 2026 — do NOT recreate it** (it was public; the cost base has cash-worker lines). Publish via step 5; render to `/tmp`, never a vault folder.

---

## Common questions Pete asks about the report

- **"Why is the daily figure different?"** → Two bases: calendar-day (÷30.4) or 5-working-day (÷21.67). Currently using 5-working-day per Pete's spec.
- **"Why is April under-budget?"** → April employer social was missing from Odoo at time of generation; substituted with €2,603 from nóminas. Headline figure includes this substitution.
- **"How many [items] did we order?"** → Pull `account.move.line` quantities from the relevant Odoo invoice. See "April LeakGuard" example: 12 × LPMLF-1104 loggers @ €235 each.
- **"What's this Sygma invoice for?"** → Pull line items from Xero via xero-api.py. The Description fields are descriptive.

---

## Anti-patterns

Do NOT:
- **Use any names of casual workers (Jose, Juan, anyone else) anywhere in the rendered HTML** — public label is "Casual Labour Including Overtime" only. Internal Python dicts can keep them anonymous (`CASUAL_LABOUR_ADDITIONAL[mn] = total_for_month`) but never surface names.
- Use "undeclared", "in the black", "cash-in-hand", or similar tax-sensitive descriptors in any rendered output. Internal CLAUDE.md / vault notes may mention; public HTML never.
- Bundle Sygma extras into a single "Sygma intercompany" bucket — route each line into its proper home (consultancy → Subcontractors, water meters → LeakGuard, etc.)
- Use gross rent (€2,140) — use net (€2,000 from 621000)
- Include MVP Lanzarote bookkeeping fees in CD baseline — Laura is not billed to CD
- Include David Gainford historic line AND ONBrand as separate items — they're the same continuous marketing line
- Use a per-month-shifting baseline window — use a single fixed window (currently Jan-Apr 2026) so all monthly reports compare against the same baseline
- Skip the cash items just because they're awkward — €1,733/mo cash worker + €800/mo cash overtime + €1,733/mo Juan (Jan-Mar) are real cash outflows
- Treat Pete's "Mark" or El Atico nóminas as CD — they're EA

---

## Future enhancements (not yet built)

- Add weekly report variant (period = Mon-Sun, baseline ÷ 4.33, ask Pete for cash overtime per-week)
- Add `--month` CLI parameter to `build_data.py` (currently hardcoded Jan-Apr 2026)
- Move SYGMA_EXTRAS / JUAN_CASH_WAGES / CASH_OT_MONTHLY data into a YAML manual-ledger file so it can be appended without editing Python
- Auto-pull Sygma extras live from Xero each run (currently hardcoded after one-time review with Pete)
- Add a weekly cron once the shape is stable

## Related lessons

- sister Sygma trainer-cost audit (monthly-soldo-audit): OCR every receipt, no exceptions. Same finance-discipline pattern.
