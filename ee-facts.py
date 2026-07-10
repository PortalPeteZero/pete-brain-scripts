#!/usr/bin/env python3
"""ee-facts.py — the Enquiry Engine FACTS INDEX lookup (plan §4A).

The retrieval backbone: facts are LOOKED UP deterministically, phrasing is retrieved from the
worked-reply corpus (never facts from examples). This helper is the "looked up" half.

The facts index IS the Portal `public.courses` table (§4A.2) — code · name · duration_days ·
max_delegates(cap) · location_type, plus the M4-added `agenda_url` + `aliases`. This tool resolves
a customer's wording → the right course code (deterministic longest-alias match, NOT fuzzy semantic
search — that's what bled a neighbouring course/stale price in the 2026-07-07 test) → returns the
EXACT current facts + the agenda link + the course-model family.

**PRICES are NOT here** (§4A.7): they live in the CC (`ee-pricing` course defaults + the
`ee-customer-rates` exceptions layer). This tool returns a `price_ref` pointer, never a number.

Usage:
  VAULT=/tmp/pbs python3 ee-facts.py "EUSR Cat 1 & 2, private on-site, 6 delegates"
  VAULT=/tmp/pbs python3 ee-facts.py "proqual cat 1" --json
"""
import os, sys, json, re, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SB_TOKEN = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
PORTAL_REF = "rsczwfstwkthaybxhszy"

# --- §4A.3 course model (verified with Pete 2026-07-07 — do NOT deviate without a re-confirm).
# Highest-consequence data in the system: a wrong family quotes the wrong course. Keyed by course
# code. cert £ figures per §4A.7 (EUSR £34 flat / ProQual L2 £35 / Sygma in-house £0).
MODEL = {
    "C004":       {"family": "cat1",         "cert": "EUSR Cat 1 (£34pp reg)",                 "note": "EUS/EUSR Category 1 — the Cat 1 family base day."},
    "C001":       {"family": "cat1",         "cert": "Sygma in-house (£0)",                    "note": "Genny & CAT / HSG47 / cable avoidance — one CAT & Genny day serving the family."},
    "C049":       {"family": "cat1",         "cert": "Sygma in-house (£0)",                    "note": "HSG47 Locating Underground Services — CAT & Genny family."},
    "C001-VSCAN": {"family": "cat1",         "cert": "Sygma in-house (£0)",                    "note": "Vscan — genuinely separate course; own agenda to be built."},
    "C013":       {"family": "cat1",         "cert": "ProQual Accredited Certificate (£35pp — Pete 2026-07-10)", "note": "ProQual Cat 1 — SAME DELIVERY as EUS Cat 1 (C004); different agenda + cert. Zero history — draft from this model. Agenda: build pending (reworded EUS Cat 1 replica)."},
    "C008":       {"family": "cat2",         "cert": "EUSR Cat 2 (£34pp reg)",                 "note": "EUS/EUSR Category 2 (safe digging) — the Cat 2 family day. Cat 2 on-site → also link https://sygma-solutions.com/agendas/cat2-delivery-and-site-requirements."},
    "C015":       {"family": "cat2",         "cert": "ProQual Accredited Certificate (£35pp — Pete 2026-07-10)", "note": "ProQual Cat 2 — SAME DELIVERY as EUS Cat 2 (C008); different agenda + cert."},
    "C009":       {"family": "combined",     "cert": "EUSR — CONDITIONAL (D1, Pete 2026-07-09): back-to-back days = ONE £34pp; split/staggered days = 2 × £34pp", "note": "EUS Cat 1 & 2 Combined — NOT a third product: the Cat 1 day + the Cat 2 day, priced as two days. ⚠ Cert fee depends on the dates: consecutive = one £34pp, split = two courses = 2×£34pp."},
    "C010":       {"family": "combined",     "cert": "EUSR — CONDITIONAL (D1): back-to-back = ONE £34pp; split days = 2 × £34pp", "note": "EUS Cat 1 & 2 Combined (variant of C009). ⚠ Same conditional cert fee as C009."},
    "C036":       {"family": "super-user",   "cert": "Sygma in-house (£0)",                    "note": "Super User Utility Location Coach (2-day) — coaching/assessing/supervisor competency. NOT a train-the-trainer product. ⚠ ON-SITE ONLY (never a public/open course), max 6 delegates (Pete, 2026-07-07)."},
    "C037":       {"family": "super-user",   "cert": "EUSR-endorsed (£34pp)",                  "note": "Super User Coach (EUSR-endorsed) — agenda covers both C036 + C037. ⚠ ON-SITE ONLY (never a public/open course), max 6 delegates."},
    "C017":       {"family": "cat1-award",   "cert": "ProQual L2 Award (£35pp)",               "note": "ProQual Level 2 Award (full 2-day) — CAT & Genny family (NOT Cat 2). Day 1 = the CAT & Genny day; Day 2 = award completers."},
    "C051":       {"family": "cat1-award",   "cert": "ProQual L2 Award (£35pp)",               "note": "ProQual Level 2 Award 1-day refresher — rides the standard CAT & Genny day (a delivery/scheduling matter, no own agenda)."},
}
# supporting links dropped into replies alongside the agenda (not a course agenda itself)
SUPPORTING = {"C008": ["https://sygma-solutions.com/agendas/cat2-delivery-and-site-requirements"]}

def portal_q(sql):
    req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{PORTAL_REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {SB_TOKEN}", "Content-Type": "application/json", "User-Agent": "curl/8.7.1"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())

def _norm(s):
    # &→"and" so "Cat 1 & 2" matches the "cat 1 and 2" combined alias (and beats "cat 1"/"eusr cat 1"
    # on length — the longest-alias rule then deterministically picks combined over the single course).
    s = (s or "").lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()

def load_index():
    rows = portal_q("SELECT code, name, duration_days, max_delegates, location_type, agenda_url, aliases "
                    "FROM public.courses WHERE is_active AND aliases IS NOT NULL AND cardinality(aliases) > 0")
    return rows

def lookup(text):
    """Deterministic resolve: the LONGEST alias that appears in the enquiry wins — so
    'cat 1 and 2 combined' matches the combined course (alias 'cat 1 and 2'), never Cat 1 alone."""
    nt = _norm(text)
    best = None  # (alias_len, code, row, matched_alias)
    for r in load_index():
        for al in (r.get("aliases") or []):
            na = _norm(al)
            if na and na in nt:
                cand = (len(na), r["code"], r, al)
                if best is None or cand[0] > best[0]:
                    best = cand
    if not best:
        return None
    _, code, r, matched = best
    m = MODEL.get(code, {})
    return {
        "code": code, "name": r["name"], "duration_days": r["duration_days"],
        "max_delegates": r["max_delegates"], "cap": r["max_delegates"], "location_type": r["location_type"],
        "family": m.get("family"), "cert": m.get("cert"), "model_note": m.get("note"),
        "agenda_url": r.get("agenda_url"), "agenda_status": "live" if r.get("agenda_url") else "build-pending",
        "supporting_links": SUPPORTING.get(code, []),
        "matched_alias": matched,
        "price_ref": "ee-pricing (course default) + ee-customer-rates (exceptions) — §4A.7; NEVER from public.courses",
    }

def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    if not args:
        print("usage: ee-facts.py \"<enquiry wording>\" [--json]"); sys.exit(2)
    res = lookup(" ".join(args))
    if as_json:
        print(json.dumps(res, indent=1)); return
    if not res:
        print("no course matched — qualify with the customer, or add an alias to public.courses"); sys.exit(1)
    print(f"■ {res['code']} — {res['name']}")
    print(f"  family:   {res['family']}   (matched on \"{res['matched_alias']}\")")
    print(f"  duration: {res['duration_days']} day(s)   cap: {res['cap']}   {res['location_type'] or ''}")
    print(f"  cert:     {res['cert']}")
    print(f"  agenda:   {res['agenda_url'] or '(no agenda page yet — do NOT promise one; ask Pete before quoting an agenda)'}  [{res['agenda_status']}]")
    if res["supporting_links"]:
        print(f"  also:     {', '.join(res['supporting_links'])}")
    print(f"  price:    {res['price_ref']}")
    print(f"  model:    {res['model_note']}")

if __name__ == "__main__":
    main()
