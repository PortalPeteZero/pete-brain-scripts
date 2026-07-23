---
type: plan
title: EE Remediation Plan — make banked rules actually enforce (from 80%-right to send-ready)
project: "[[SY-Training-Enquiries]]"
tags: [training-enquiries, enquiry-engine, plan, remediation]
status: completed
date: 2026-07-22
---

<!-- PLAN-LIFECYCLE-BANNER -->

## OUTCOME (22 Jul 2026)
Executed in full. The gate now ENFORCES banked rules fail-closed. Proof: **11 of 12 real past on-site quotes would now be blocked** for missing the cert recommendation / booking CTA / cap / upsell (the exact recurring gaps); tonight's corrected Wheal Jane quote passes. Selftest 6/6. Commits `5b8440e` (enforcement + dates lint) and `b8574fc` (capture reliability) pushed to pete-brain-scripts.

## Root cause (4-lens audit, 22 Jul 2026)
The behavioural rules exist in `ee_rules`/`ee_phrases` but are **inert**: `ee-facts.py` surfaces facts only; `ee-draft-gate.py` merely PRINTS rules ("banked rules honoured", truncated to 90 chars, after it already passed) and never reads the classified `scenario` again. 0 of 9 behavioural rules are mechanically enforced. So scenario logic (cert steer, up-to-8, upsell, booking close) fires only when the drafter remembers it → same corrections recur → 80%-right drafts, Pete fixes the last 20%.

## Gap register
**A. Missing knowledge (bank it):**
- cert-recommendation rule (in-house sufficient + CITB; accredited only if client/site demands) — HIGHEST VOLUME, taught 2x, unbanked.
- offer/arrange dates, don't ask — unbanked + `qualifying_checklist` phrase says "preferred dates" + contract Step 3 says "ask for rough dates".
- don't put diary dates in a reply — unbanked.
- Super User on-site-only/cap 6 — in notes, not a rule.
- proposal-chase never offers a call — in notes, not a rule.

**B. Present but toothless (enforce):** up-to-8 + fill-upsell (sub-8 on-site); booking-CTA close; cap-of-8. All 9 doc-rules advisory-only.

**C. Reliability:** send+capture non-atomic, zero retry (tonight's half-send); CRM activity no dedupe (re-run duplicates); filing runs on partial writes; ee-send no terminal verdict.

## Phases (each with a proof-gate)
- [x] **B1** fixed `qualifying_checklist` + `sign_off` phrases (removed "ask for dates") via `ee-learn`; logged to `ee_edits`.
- [x] **B2** verified Bryony NOT double-logged (exactly 3 activities: enquiry, reply, one quote).
- [x] **1a** added `scenarios/applies_when/require_pattern/fail_hint` to `ee_rules`.
- [x] **1b** banked `cert_recommendation` (highest-volume gap), `onsite_cap_statement`, `booking_cta_close`; tagged `onsite_fill_upsell`; folded super-user (on-site only/cap 6) + chase-no-call into their rule bodies.
- [x] **1c** `_rules_for` + fail-closed enforcement in `gate()`; cap bound numerically from ee-facts; full scenario-filtered bodies shown (no more 90-char stub). Proof: bad draft BLOCKS on 4 rules, good WJ PASSES, selftest 6/6.
- [x] **1d** fixed workflow-design "ask for rough dates" line + added never-ask-for-dates lint. NOTE: the `ee-signoff` "block on un-banked correction" is behavioural (bank via `ee-learn` same session) backed by the now-biting enforcement — a reliable mechanical detector of "a correction Pete said" isn't feasible, so not faked.
- [x] **3** capture: retry transients in te-log `_preq`+`cc_sql` (SSL/URL/timeout/5xx; 4xx fails fast); message_id set before capture (re-run safe); ee-send loud half-capture verdict. Proof: retry recovers a transient, 4xx raises immediately, both compile + run live.
- [x] **4** Frank-style proof: 11/12 real past on-site quotes would now BLOCK for the exact recurring gaps; corrected WJ passes.
- [ ] **Deferred (noted, low-risk):** hard banned-pattern enforcement for super-user open-course + chase-call (bodies updated + surfaced now; enforce later if they recur).

## Files touched
- `ee-draft-gate.py` — rule resolver + fail-closed enforcement + full-body display (commit 5b8440e)
- `ee-lint.py` — never-ask-for-dates check (5b8440e)
- `te-log.py` — `_http_retry` on both network primitives (b8574fc)
- `ee-send.py` — message_id-before-capture + terminal verdict (b8574fc)
- DB: `ee_rules` (4 enforced rules + columns), `ee_phrases` (2 fixed), `workflow-design` note (line fixed), `ee-manifest` note (enforcement documented)
