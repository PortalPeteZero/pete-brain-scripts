#!/usr/bin/env python3
"""ee-facts.py — the Enquiry Engine FACTS INDEX lookup (plan §4A; hardening plan P1).

The retrieval backbone: facts are LOOKED UP deterministically, phrasing is retrieved from the
worked-reply corpus (never facts from examples). This helper is the "looked up" half.

The facts index IS the Portal `public.courses` table — code · name · duration_days ·
max_delegates(cap) · location_type · agenda_url · aliases · family · cert_line · model_note ·
supporting_links · facts_provenance. Since the 2026-07-10 P1 migration this tool holds ZERO
course facts in code: everything comes off the DB row, so a Portal edit changes the answer with
no code change (the C017-class drift is structurally impossible).

Resolution is a deterministic longest-alias match, NOT fuzzy semantic search. Two guards:
- AMBIGUITY: if the enquiry contains a variant word (refresher / endorsed / plus / combined) the
  matched course doesn't carry, the tool says "qualify with the customer" instead of answering.
- FACTS INCOMPLETE: a matched course whose family/cert_line columns are NULL prints a loud
  banner — silent Nones never reach a draft.

**PRICES are live from the Portal DB** (since 2026-07-10, superseding the old §4A.7 "prices in the
CC notes / never Portal" rule): `public.price_list` (standard) + `public.customer_pricing` (honour +
negotiated overrides). `lookup()` returns a live `price` book; `resolve_line(item_key, thread_id=…,
contact_ref=…)` resolves one line incl. overrides. Empty `price` = DB unreadable → never quote blind.

Usage:
  VAULT=/tmp/pbs python3 ee-facts.py "EUSR Cat 1 & 2, private on-site, 6 delegates"
  VAULT=/tmp/pbs python3 ee-facts.py "proqual cat 1" --json
"""
import os, sys, json, re, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SB_TOKEN = (os.environ.get("SUPABASE_TOKEN") or "").strip() or open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
PORTAL_REF = "rsczwfstwkthaybxhszy"

# Variant words that MUST be reflected by the matched course (name, or family for 'combined').
# If the customer says one and the matched course doesn't carry it, we qualify instead of guessing.
VARIANT_WORDS = ("refresher", "endorsed", "plus", "combined")

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
    return portal_q(
        "SELECT code, name, duration_days, max_delegates, location_type, agenda_url, aliases, "
        "family, cert_line, model_note, supporting_links "
        "FROM public.courses WHERE is_active AND aliases IS NOT NULL AND cardinality(aliases) > 0")

def _variant_gap(text, row):
    """Return the variant words the enquiry carries that the matched course does NOT."""
    nt_words = set(_norm(text).split())
    nname = _norm(row.get("name"))
    gaps = []
    for w in VARIANT_WORDS:
        if w in nt_words:
            ok = w in nname or (w == "combined" and (row.get("family") == "combined"))
            if not ok:
                gaps.append(w)
    return gaps

def _sql_str(v):
    """Escape a string for safe inline SQL (internal values only: item_keys, our own thread/uuid ids)."""
    return "'" + str(v).replace("'", "''") + "'"

# ── PRICING — live from the Portal price_list / customer_pricing tables (the SSOT since 2026-07-10) ──
# Replaces the old ee-pricing/ee-customer-rates NOTE-scraping. Prices are DATA in the DB, resolved here.
def price_book():
    """Current standard price list, live from public.price_list. {item_key: {amount, unit, category, label}}.
    Empty dict on DB failure — callers must treat empty as 'SSOT unreadable' and fall back, never quote blind."""
    try:
        rows = portal_q("SELECT item_key, amount, unit, category, label FROM price_list "
                        "WHERE course_ref IS NULL AND now() <@ tstzrange(effective_from, effective_to, '[)')")
        return {r["item_key"]: {"amount": float(r["amount"]), "unit": r["unit"],
                                "category": r["category"], "label": r["label"]} for r in rows}
    except Exception:
        return {}

def resolve_line(item_key, thread_id=None, contact_ref=None):
    """Resolve ONE priced line to its amount. A matched customer_pricing override (thread OR contact,
    per-line, honour + negotiated rates) wins over the standard price_list; else the list at now().
    Returns (amount: float|None, source: str). PER-LINE by design so an open-course honour can never
    bleed onto a different course/item the same customer later enquires about."""
    try:
        keys = []
        if thread_id:   keys.append("thread_id = %s"   % _sql_str(thread_id))
        if contact_ref: keys.append("customer_ref = %s" % _sql_str(contact_ref))
        if keys:
            ov = portal_q(
                "SELECT agreed_amount, discount_pct FROM customer_pricing WHERE item_key=%s AND (%s) "
                "AND now() <@ tstzrange(effective_from, effective_to, '[)') "
                "ORDER BY (thread_id = %s) DESC, (course_ref IS NOT NULL) DESC, (agreed_amount IS NOT NULL) DESC, "
                "effective_from DESC, created_at DESC LIMIT 1"
                % (_sql_str(item_key), " OR ".join(keys), _sql_str(thread_id or "")))
            if ov and ov[0].get("agreed_amount") is not None:
                return float(ov[0]["agreed_amount"]), "customer-override"
        st = portal_q("SELECT amount FROM price_list WHERE item_key=%s AND course_ref IS NULL "
                      "AND now() <@ tstzrange(effective_from, effective_to, '[)') LIMIT 1" % _sql_str(item_key))
        if st:
            return float(st[0]["amount"]), "list"
    except Exception:
        pass
    return None, "unreadable"

def lookup(text):
    """Deterministic resolve: the LONGEST alias that appears in the enquiry wins — so
    'cat 1 and 2 combined' matches the combined course (alias 'cat 1 and 2'), never Cat 1 alone.
    Returns a dict; on a variant mismatch returns {'ambiguous': True, ...}."""
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
    gaps = _variant_gap(text, r)
    if gaps:
        return {"ambiguous": True, "code": code, "name": r["name"], "matched_alias": matched,
                "variant_words": gaps,
                "action": "qualify with the customer — the enquiry mentions a variant the matched course does not carry"}
    return {
        "code": code, "name": r["name"], "duration_days": r["duration_days"],
        "max_delegates": r["max_delegates"], "cap": r["max_delegates"], "location_type": r["location_type"],
        "family": r.get("family"), "cert": r.get("cert_line"), "model_note": r.get("model_note"),
        "facts_incomplete": (not r.get("family")) or (not r.get("cert_line")),
        "agenda_url": r.get("agenda_url"), "agenda_status": "live" if r.get("agenda_url") else "build-pending",
        "supporting_links": r.get("supporting_links") or [],
        "matched_alias": matched,
        "price": price_book(),  # live standard figures from public.price_list (the SSOT)
        "price_note": ("Standard figures above are live from public.price_list. For a customer/thread that "
                       "has an honour or negotiated rate, resolve each line with resolve_line(item_key, "
                       "thread_id=…, contact_ref=…). Empty 'price' = DB unreadable → do NOT quote blind."),
    }

def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__); sys.exit(0)
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    if not args:
        print("usage: ee-facts.py \"<enquiry wording>\" [--json]"); sys.exit(2)
    res = lookup(" ".join(args))
    if as_json:
        print(json.dumps(res, indent=1)); return
    if not res:
        print("no course matched — qualify with the customer, or add an alias to public.courses"); sys.exit(1)
    if res.get("ambiguous"):
        print(f"⚠ AMBIGUOUS — the enquiry mentions {', '.join(res['variant_words'])!r} but the closest match "
              f"({res['code']} {res['name']}, via alias \"{res['matched_alias']}\") does not carry it.")
        print("  → qualify with the customer before quoting; do not guess between the base course and the variant.")
        sys.exit(1)
    print(f"■ {res['code']} — {res['name']}")
    if res.get("facts_incomplete"):
        print("  ⛔ FACTS INCOMPLETE — this course's family/cert columns are NULL in public.courses.")
        print("     Do NOT draft from this result; fill the columns (or ask Pete) first.")
    print(f"  family:   {res['family']}   (matched on \"{res['matched_alias']}\")")
    print(f"  duration: {res['duration_days']} day(s)   cap: {res['cap']}   {res['location_type'] or ''}")
    print(f"  cert:     {res['cert']}")
    print(f"  agenda:   {res['agenda_url'] or '(no agenda page yet — do NOT promise one; ask Pete before quoting an agenda)'}  [{res['agenda_status']}]")
    if res["supporting_links"]:
        print(f"  also:     {', '.join(res['supporting_links'])}")
    pb = res.get("price") or {}
    if pb:
        figs = " · ".join(f"{k} £{v['amount']:g}/{v['unit']}" for k, v in sorted(pb.items()))
        print(f"  price:    {figs}   [live from public.price_list]")
        print(f"            honour/negotiated → resolve_line(item_key, thread_id=…, contact_ref=…)")
    else:
        print(f"  price:    ⛔ price_list unreadable — do NOT quote blind")
    print(f"  model:    {res['model_note']}")

if __name__ == "__main__":
    main()
