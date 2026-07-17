#!/usr/bin/env python3
# CRON-META: none (interactive end-of-session gate; not scheduled)
"""entity-enrich-signoff.py -- the customer-enrichment sign-off gate.

Pete's standing rule (2026-07-17): whenever we deal with a customer / supplier /
project that HAS a CC presence (a vault_notes knowledge home), that home must get
enriched with the substantive new facts from the exchange -- automatically, without
Pete having to remind. This gate makes it enforceable instead of a behaviour note.

It runs at the end of triage / the Replies walker / the Enquiry Engine / closeout and
answers ONE question: for every CC-present entity we had a SUBSTANTIVE touch with this
session, was its knowledge section updated?  A substantive touch = a triage decision
whose ask is reply/decision/review/rsvp OR whose verb is Reply/Task/Route/Hand-to on a
Customers|Suppliers|Projects filing label, OR an Enquiry-Engine touch (reply/quote/enquiry).
Pure info-only/none File actions (routine filing) are NOT substantive and are ignored.

"Enriched" = at least one vault_notes note under that entity's home (vault_path prefix,
or matched via also_known_as/slug for an EE company) has source_updated/updated_at at or
after the session cutoff. It does NOT count a `vault-enricher` Drive email-extract -- that
is the raw email, not the knowledge section.

Exit 0 when every substantive CC-present touch is enriched (or there were none); exit 2
with a list when any is outstanding. So it is a runnable gate:
    VAULT=/tmp/pbs python3 /tmp/pbs/entity-enrich-signoff.py --since today ; echo $?

Usage:
    entity-enrich-signoff.py [--since today|<ISO-ts>] [--json]
"""
import os, sys, json, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")


def q(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       env={**os.environ, "VAULT": VAULT},
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"[entity-enrich-signoff] query failed: {r.stderr[:200]}\n")
        return []
    out = (r.stdout or "").strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except Exception:
        return []


def lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    since_sql = "current_date"           # start of today (server tz); good enough for a session gate
    if "--since" in args:
        v = args[args.index("--since") + 1]
        since_sql = "current_date" if v == "today" else lit(v)

    findings = []   # {entity, home, kind, enriched(bool), detail}

    # ---- 1. Triage / Replies-walker substantive touches -------------------------------
    tri = q(f"""
        SELECT DISTINCT final_label, min(created_at) AS first_touch
        FROM triage_decisions
        WHERE created_at >= {since_sql}
          AND apply_status = 'applied'
          AND final_label IS NOT NULL
          AND (final_label LIKE 'Customers/%' OR final_label LIKE 'Suppliers/%' OR final_label LIKE 'Projects/%')
          AND ( final_ask IN ('reply','decision','review','rsvp')
                OR final_verb LIKE 'Reply%' OR final_verb LIKE 'Task%'
                OR final_verb LIKE 'Hand to%' )
          AND final_verb <> 'Route'   -- Route = EE handoff; enrichment is the EE corpus, judged by the EE arm below
        GROUP BY final_label
    """)
    for row in tri:
        label = row.get("final_label")
        if not label:
            continue
        # entity home = vault_notes under this label's path
        homes = q(f"""
            SELECT type, count(*) AS n,
                   max(source_updated) AS last_src, max(updated_at) AS last_upd
            FROM vault_notes
            WHERE type IN ('customer','supplier','project')
              AND (vault_path = {lit(label + '/README.md')} OR vault_path LIKE {lit(label + '/%')})
            GROUP BY type
        """)
        if not homes:
            findings.append({"entity": label, "home": None, "kind": "triage",
                             "enriched": None,
                             "detail": "touched but NO CC knowledge home found (consider creating one, or ignore)"})
            continue
        # enriched if ANY note under the home was updated since the cutoff
        upd = q(f"""
            SELECT count(*) AS n
            FROM vault_notes
            WHERE (vault_path = {lit(label + '/README.md')} OR vault_path LIKE {lit(label + '/%')})
              AND (source_updated >= {since_sql} OR updated_at >= {since_sql})
        """)
        n_upd = (upd[0]["n"] if upd else 0)
        findings.append({"entity": label, "home": label, "kind": "triage",
                         "enriched": n_upd > 0,
                         "detail": (f"knowledge updated ({n_upd} note(s)) this session"
                                    if n_upd > 0 else
                                    "filed/replied this session but knowledge section NOT updated -- enrich it")})

    # ---- 2. Enquiry-Engine touches ----------------------------------------------------
    ee = q(f"""
        SELECT DISTINCT slug
        FROM enquiry_touches
        WHERE created_at >= {since_sql}
          AND kind IN ('reply','quote','enquiry')
          AND slug IS NOT NULL
    """)
    for row in ee:
        slug = row.get("slug")
        if not slug:
            continue
        # company tokens from the enquiry slug, dropping generic filler + the EE plumbing words
        STOP = {"enquiry", "reply", "ltd", "limited", "the", "and", "group",
                "contracts", "services", "training", "quote", "chase"}
        tokens = [t for t in slug.replace("-", " ").split()
                  if len(t) > 2 and t.lower() not in STOP]
        if not tokens:
            findings.append({"entity": f"EE:{slug}", "home": None, "kind": "ee",
                             "enriched": None, "detail": "EE touch -- no distinctive company token; skipped"})
            continue
        # STRICT match: the company token must appear in the CURATED also_known_as field
        # (not a loose title LIKE) so a brand-new contact never false-matches an existing customer.
        conds = " OR ".join(
            [f"v.frontmatter->>'also_known_as' ILIKE {lit('%' + t + '%')}" for t in tokens]
            + [f"v.frontmatter->>'trading_name' ILIKE {lit('%' + t + '%')}" for t in tokens]
        )
        home = q(f"""
            SELECT v.vault_path,
                   (SELECT count(*) FROM vault_notes w
                     WHERE w.vault_path LIKE regexp_replace(v.vault_path,'/README.md$','') || '/%'
                       AND (w.source_updated >= {since_sql} OR w.updated_at >= {since_sql})) AS n_upd,
                   (v.source_updated >= {since_sql} OR v.updated_at >= {since_sql}) AS self_upd
            FROM vault_notes v
            WHERE v.type IN ('customer','supplier') AND ({conds})
            LIMIT 1
        """)
        if not home:
            findings.append({"entity": f"EE:{slug}", "home": None, "kind": "ee",
                             "enriched": None,
                             "detail": "EE touch -- no customer home matched (new contact or unmapped; skipped)"})
            continue
        h = home[0]
        enriched = bool(h.get("self_upd")) or (h.get("n_upd") or 0) > 0
        findings.append({"entity": f"EE:{slug}", "home": h["vault_path"], "kind": "ee",
                         "enriched": enriched,
                         "detail": ("knowledge updated this session" if enriched
                                    else "EE reply captured but customer knowledge section NOT updated -- enrich it")})

    outstanding = [f for f in findings if f["enriched"] is False]

    if as_json:
        print(json.dumps({"findings": findings, "outstanding": len(outstanding)}, indent=1))
    else:
        print(f"=== entity-enrich sign-off (since {since_sql}) ===")
        if not findings:
            print("  no substantive customer/supplier/project touches this session -- nothing to enrich.")
        for f in findings:
            mark = "✓" if f["enriched"] else ("–" if f["enriched"] is None else "✗")
            print(f"  {mark} {f['entity']:<32} {f['detail']}")
        if outstanding:
            print(f"\nOUTSTANDING: {len(outstanding)} CC-present entity(ies) touched but NOT enriched.")
            print("  Update each one's knowledge section with the substantive new facts, then re-embed:")
            print("    (edit the vault_notes home note + run cc-embedder.py), then re-run this gate.")
        else:
            print("\nAll substantive customer touches enriched. ✓")

    sys.exit(2 if outstanding else 0)


if __name__ == "__main__":
    main()
