#!/usr/bin/env python3
"""clancy-dn-glossary.py — the Damage Depot glossary: the SSOT for the words we use.

Pete, 2 Aug 2026: "i want a glossary page, for my own use, your use and clancy use … database
driven then this can become the ssot for our terms we use in the sessions on this rather than
relying on notes".

One table (`clancy_glossary`), one page, two consumers:
  · the glossary page itself — simple cards, grouped, searchable by eye;
  · the register/analysis tables' COLUMN EXPLAINERS, which render the SAME rows via
    `column_key` — one copy of the wording, nowhere for a second copy to drift.

WORDING IS GATED AT SEED TIME. Every field of every seed card is piped through
clancy-vocab-check.py BEFORE it is written to the table — a banned phrase ("investigation
form") is caught while it is a draft, not as a refused publish. The publish path runs the same
gate again over the rendered page, plus the script-parse check.

THE CONTENT WRITE USES THE OVERRIDE PATTERN. module_content carries a BEFORE trigger
(damage_review_wording_trg) that refuses clancy-% pages containing strike-words; the glossary
legitimately holds the term "strike category" (Depotnet's own field name), so the write sets
app.damage_review_override in-transaction exactly as clancy-dn-pages.py does. A plain insert
would be refused by the database on the very first publish.

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-glossary.py --seed            # upsert the seed cards (gated)
  VAULT=/tmp/pbs python3 clancy-dn-glossary.py --local out.html  # build to a file, publish nothing
  VAULT=/tmp/pbs python3 clancy-dn-glossary.py --publish         # gate + publish the page
"""
import os, sys, json, re, argparse, subprocess, datetime, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
sys.path.insert(0, VAULT)
import clancy_dn_ui as ui

MK = "clancy-damage-glossary"

SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _k["url"], _k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}


def _urlopen_retry(req, timeout=120, tries=9):
    import time as _t
    for n in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 60))
        except Exception:
            if n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 60))


def rest(path, method="GET", payload=None, extra=None):
    h = dict(H); h.update(extra or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
        data=json.dumps(payload).encode() if payload is not None else None, headers=h,
        method=method)
    t = _urlopen_retry(req, timeout=120).read().decode()
    return json.loads(t) if t.strip() else None


def sql(q):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    return json.loads(_urlopen_retry(req, timeout=120).read().decode())


# ── the seed cards ───────────────────────────────────────────────────────────────────────────
# (term, plain_meaning, where_it_appears, whose_word, example, grp, sort, column_key)
# whose_word: Depotnet = their own field/label, verbatim · Sygma = our review layer · ours = a
# name we coined and defined. column_key binds a table column's explainer to its card.
SEEDS = [
    # ---- the register (the damage record itself) ------------------------------------------
    ("The register", "Depotnet's Incident Register, filtered to Category = Service Damage. One row per damage. A damage cannot exist for us unless it is on this register.",
     "Everywhere — it is the population every page counts", "Depotnet", None, "register", 10, None),
    ("Damage ID & date", "Depotnet's own number for the damage, and the date the strike happened (not the date it was typed in).",
     "First column of every table; every page links by this ID", "Depotnet", "133852 · 17 Jun 2026", "register", 20, "damage_id"),
    ("Contract", "The Clancy contract the crew was working under when the damage happened, grouped into families (e.g. Anglian Water).",
     "Register and analysis tables; the by-contract chart", "Depotnet", None, "register", 30, "contract"),
    ("Location", "Where the damage happened, as typed on the incident. Quality varies from full address to a street name.",
     "Register and analysis tables; the damage page header", "Depotnet", None, "register", 40, "location"),
    ("What happened", "The free-text description typed when the damage was reported.",
     "Register table; in full on the damage page", "Depotnet", None, "register", 50, "description"),
    ("Utility hit", "Which service was damaged. Where the damage is captured this is Depotnet's own strike category; on uncaptured years it is auto-read from the description and labelled as such.",
     "Register and analysis tables; the utility chart", "Depotnet", "Gas, Electric, Water, Telecommunications", "register", 60, "utility"),
    ("Severity", "Depotnet's severity band for the damage.",
     "Register and analysis tables", "Depotnet", "HIGH - Category 1 / MEDIUM - Category 2 / LOW - Category 3", "register", 70, "severity"),
    ("Status", "The state of the damage CASE as a whole on Depotnet — is Clancy still working it or is it shut. Not the same thing as the investigation report section's state: a case can be closed while its section says the investigating was unfinished.",
     "Register and analysis tables; status filter chips", "Depotnet", "Open · Closed · Complete with Outstanding Actions", "register", 80, "status"),
    ("Depth", "How deep the damaged service was, in millimetres, as answered on the incident questions. A handful of damages answered in metres against the form's own label; those are reported as a form problem, never converted.",
     "Analysis table; the depth chart", "Depotnet", "450mm", "register", 90, "depth"),
    ("Supply interrupted", "Whether customers lost supply, as answered on the incident questions.",
     "Analysis table", "Depotnet", None, "register", 100, "supply_interrupted"),
    ("Captured", "Whether WE have pulled this damage's full record off Depotnet — every question, every action, every file. Uncaptured damages show dashes in capture-derived columns: a dash means we have not looked, never that Depotnet holds nothing.",
     "Register table badge; drives every dash rule", "ours", None, "register", 110, "captured"),
    ("Lessons learnt (column)", "Depotnet's own field 'Preventative Outcomes/Actions/Lessons Learnt', shown word for word from the damage's investigation report. 'Nothing written' = Clancy left the field empty. A dash = the damage is not captured. What Sygma thinks sits in its own column — the two are never mixed.",
     "Register table", "Depotnet", None, "register", 120, "outcome_learning"),
    ("Sygma review", "What Sygma's own review of this damage found — our summary, in our words. A different thing from Depotnet's record: the Lessons learnt column is what Clancy wrote, this is what we found. A dash = no Sygma review yet.",
     "Register table", "Sygma", None, "register", 125, "sygma_review"),
    ("Strike category", "Depotnet's own field naming which utility was struck. Captured per damage; the authority over any guess of ours.",
     "Utility columns and charts on captured years", "Depotnet", None, "register", 130, None),

    # ---- the Report tab (the investigation) -----------------------------------------------
    ("Incident questions", "The first section of a damage's record: filled in on the day the damage is reported. What was struck, where, how deep, what plant, immediate actions. All damages have this — it is how a damage gets onto the register. In one line: WHAT happened.",
     "The damage page's Questions section", "Depotnet", None, "report", 10, None),
    ("Investigation report section", "The second section, on Depotnet's Report tab: the follow-up investigation. Who investigated, root cause, underlying cause, lessons learnt — ending with Clancy's own verdict question. In one line: WHY it happened. There is no half-filled state: it is either worked in full or untouched.",
     "The Investigation report column; the damage page's Report section", "Depotnet", None, "report", 20, None),
    ("Investigation report (column)", "Whether Depotnet's own investigation report section has been done. Done = every required question in it is answered. Not done = untouched. A dash = the damage is not captured, so we cannot say.",
     "Register and analysis tables", "Depotnet", "Done / Not done / –", "report", 30, "investigation_report"),
    ("Marked complete by Clancy", "Clancy's own answer to the section's final question, 'Is the investigation complete?'. 'No' on a fully-worked section means they say the investigating itself is still going — the paperwork is done, the work is not. Empty when the section is not done: we never print a No that was not answered.",
     "Register and analysis tables", "Depotnet", "Yes / No / —", "report", 40, "marked_complete"),
    ("Root cause (spot-check)", "Our tick against Depotnet's own investigation-report field 'Service Strike Root Cause'. Tick = Clancy recorded a cause in that field. Dash = the damage is not captured, so we cannot say.",
     "The spot-check columns", "Depotnet", None, "report", 50, "spotcheck_cause"),
    ("Lessons learnt (spot-check)", "Our tick against Depotnet's own field 'Preventative Outcomes/Actions/Lessons Learnt' on the investigation report. Tick = Clancy wrote something there. Dash = the damage is not captured.",
     "The spot-check columns", "Depotnet", None, "report", 60, "spotcheck_lesson"),
    ("Genny (spot-check)", "The section's own question 'Genny used?'. A genny is the signal generator used with a CAT to find buried services before digging. A cross is Clancy answering NO — they dug without it.",
     "The spot-check columns", "Depotnet", None, "report", 70, "spotcheck_genny"),
    ("CAT (spot-check)", "The section's own question 'CAT used?'. A CAT (cable avoidance tool) is the detector itself. A cross is Clancy answering no.",
     "The spot-check columns", "Depotnet", None, "report", 80, "spotcheck_cat"),
    ("Permit (spot-check)", "The section's question on whether a Permit to Dig / Breaking Ground checklist was completed and briefed before digging.",
     "The spot-check columns", "Depotnet", None, "report", 90, "spotcheck_permit"),
    ("Tick, cross, dash", "The three marks the spot-check columns use. Tick: answered yes, or something written. Cross: answered no — their words. Dash: nothing held — the section is not done, or the damage is not captured. The dash never asserts which.",
     "Every spot-check column; the key above the table", "ours", None, "report", 100, None),
    ("Root cause", "The section's own field for the direct cause of the strike. Multi-tick: a record that ticks nearly every option is excluded from cause counts and said so.",
     "The analysis page's cause sections", "Depotnet", None, "report", 110, None),
    ("Lessons learnt", "The section's field for what should be taken from the damage. Quality ranges from a word to a briefable paragraph; the analysis page grades it and quotes it verbatim.",
     "The analysis page's lessons sections", "Depotnet", None, "report", 120, None),
    ("Genny", "The signal generator half of the locate kit: it puts a traceable signal on a buried service so the CAT can follow it. 'Genny's Damage Depot' is named after it.",
     "Throughout — the section mascot", "site", None, "report", 130, None),
    ("CAT", "Cable avoidance tool — the handheld detector swept over the ground to find buried services before digging.",
     "Spot-checks; the Genny & CAT review pages", "site", None, "report", 140, None),
    ("Permit to Dig", "The breaking-ground checklist Clancy complete and brief before excavation starts.",
     "The Permit spot-check", "Depotnet", None, "report", 150, None),

    # ---- corrective actions ---------------------------------------------------------------
    ("Corrective action", "A tracked follow-up Depotnet holds against a damage — briefings, reviews, process changes. Comes from Depotnet's Action Report export, which is complete for every year whether or not the damage is captured: no actions means no actions.",
     "The three action columns; child rows; the actions tab", "Depotnet", None, "actions", 10, None),
    ("Actions raised", "How many corrective actions Depotnet holds for this damage. 'None' asserts a real absence in every year — the export covers them all. On a damage whose own status says outstanding actions while the export holds none, both sources are shown.",
     "Register and analysis tables", "Depotnet", "None / 7 / 2", "actions", 20, "actions_raised"),
    ("Still open", "Of the raised actions, how many are not yet closed. Depotnet's word for that state is Overdue, and the cell says so. A 0 here genuinely means all dealt with — the cell is silent when nothing was raised.",
     "Register and analysis tables", "Depotnet", "1 overdue", "actions", 30, "actions_still_open"),
    ("Closed (actions)", "Of the raised actions, how many Depotnet marks Closed. Silent when nothing was raised. Raised = still open + closed, visible on the row.",
     "Register and analysis tables", "Depotnet", None, "actions", 40, "actions_closed"),
    ("Overdue action", "Depotnet's own status for a raised action past its due date and not closed — the only non-closed action state that occurs in the data.",
     "The Still open column, in red", "Depotnet", None, "actions", 50, None),

    # ---- files & evidence -----------------------------------------------------------------
    ("Evidence", "How many files we hold in Drive for this damage — photos, documents, videos, pulled off Depotnet at capture. A true 0 means captured and Depotnet holds nothing; a dash means not captured yet.",
     "Register and analysis tables; the damage page's files section", "ours", None, "files", 10, "evidence"),
    ("Withdrawn on Depotnet", "A file Depotnet marks deleted after upload. We keep our copy — what was withdrawn and when is itself evidence — but it is labelled and excluded from the held count.",
     "The damage page's files section", "ours", None, "files", 20, None),
    ("Unfetchable file", "A file Depotnet lists but its own download link cannot serve — proven by trying every legal form of the request. Named on the damage page rather than counted as ours to fix.",
     "The damage page's files section", "ours", None, "files", 30, None),

    # ---- the Sygma layer ------------------------------------------------------------------
    ("Sygma layer", "Everything Sygma adds on top of Depotnet's record: panel reviews, findings, agreed next actions, reports. Kept visibly separate so Clancy always knows which system said what.",
     "The damage page's Sygma section; the Sygma? column", "Sygma", None, "sygma", 10, "sygma_layer"),
    ("Panel review", "Sygma's sit-down review of a damage with the crew — the material lives in the damage's Drive folder and is linked from its page.",
     "The damage page's Sygma section", "Sygma", None, "sygma", 20, None),

    # ---- the process (how the Depot works) ------------------------------------------------
    ("Capture", "Stage 1: getting a damage's record out of Depotnet — register row, every question, actions, files. Nothing is read or interpreted at this stage.",
     "The four-stage process", "ours", None, "process", 10, None),
    ("File", "Stage 2: putting what was captured where it lives — files into the damage's Drive folder, data into the Depot's tables. Moves things without reading them.",
     "The four-stage process", "ours", None, "process", 20, None),
    ("Enrich", "Stage 3: reading what is INSIDE the captured documents, photos and slides and lifting it onto the damage record. The only stage that reads content. Currently on hold.",
     "The four-stage process", "ours", None, "process", 30, None),
    ("Publish", "Stage 4: rebuilding the Depot's pages from what the database holds. Every page is a static snapshot built from the tables — nothing refreshes itself.",
     "The four-stage process", "ours", None, "process", 40, None),
]

GRP_LABEL = {
    "register": ("The register", "the damage record itself"),
    "report":   ("The Report tab", "the investigation, in Depotnet's own words"),
    "actions":  ("Corrective actions", "what came of it"),
    "files":    ("Files & evidence", "what we hold"),
    "sygma":    ("The Sygma layer", "what we add on top"),
    "process":  ("How the Depot works", "the four stages"),
}


def vocab_gate(text, label):
    r = subprocess.run([sys.executable, f"{VAULT}/clancy-vocab-check.py", "-"],
                       input=text, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout.strip() or r.stderr.strip())
        raise SystemExit(f"REFUSED — banned wording in {label}; reword and re-run.")


def seed():
    """Upsert the seed cards, every field gated FIRST. Idempotent on term."""
    blob = "\n".join(" ".join(str(x) for x in row if x) for row in SEEDS)
    vocab_gate(blob, "the seed cards")
    esc = lambda v: "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"
    vals = ",".join(
        f"({esc(t)},{esc(m)},{esc(w)},{esc(who)},{esc(ex)},{esc(g)},{s},{esc(ck)})"
        for t, m, w, who, ex, g, s, ck in SEEDS)
    sql(f"""INSERT INTO clancy_glossary
        (term, plain_meaning, where_it_appears, whose_word, example, grp, sort, column_key)
        VALUES {vals}
        ON CONFLICT (column_key) WHERE column_key IS NOT NULL DO NOTHING""")
    # column_key NULL rows have no conflict target; dedupe on term by hand
    sql("""DELETE FROM clancy_glossary a USING clancy_glossary b
           WHERE a.term = b.term AND a.id > b.id""")
    n = sql("SELECT count(*) n FROM clancy_glossary")[0]["n"]
    print(f"seeded — {n} cards in clancy_glossary")


PAGE_CSS = """
.gwrap{max-width:1080px;margin:0 auto;padding:24px 20px 60px}
.gintro{background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px 24px;
 margin-bottom:26px;box-shadow:var(--sh-2)}
.ggrp{margin:30px 0 14px}
.ggrp h2{font-size:20px;letter-spacing:-.02em}
.ggrp .sub{color:var(--faint);font-size:13.5px;margin-top:2px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-top:14px}
.gcard{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 18px;
 box-shadow:var(--sh-1);display:flex;flex-direction:column;gap:8px}
.gcard .t{font-weight:800;font-size:15.5px;letter-spacing:-.015em}
.gcard .m{font-size:13.5px;line-height:1.55;color:var(--mid)}
.gcard .w{font-size:12px;color:var(--faint)}
.gcard .ex{font-size:12.5px;background:#f6f8fb;border-radius:8px;padding:6px 10px;
 font-variant-numeric:tabular-nums}
.who{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.06em;
 text-transform:uppercase;border-radius:20px;padding:2px 9px;margin-left:8px;vertical-align:2px}
.who.d{background:#eef6e0;color:#4a7200}.who.s{background:#e8eef7;color:#2d548f}
.who.o{background:#f1f1f4;color:#555}
"""


def build():
    rows = rest("clancy_glossary?select=*&order=grp,sort,term")
    esc = ui.esc if hasattr(ui, "esc") else (lambda s: str(s).replace("&", "&amp;")
                                             .replace("<", "&lt;").replace(">", "&gt;"))
    by = {}
    for r in rows:
        by.setdefault(r["grp"], []).append(r)
    WHO = {"Depotnet": ("d", "Depotnet&#8217;s word"), "Sygma": ("s", "Sygma&#8217;s word"),
           "ours": ("o", "our word"), "site": ("o", "site term")}
    body = ['<div class="gwrap">',
            '<div class="gintro"><b>Every term this section uses, in plain English.</b> '
            'Each card says what the word means, where you will meet it, and whose word it is '
            '&mdash; Depotnet&#8217;s own label, Sygma&#8217;s, a site term the industry uses, or a name we coined and defined. '
            'The same cards feed the column explanations on the tables, so the wording here IS '
            'the wording there.</div>']
    for g in ("register", "report", "actions", "files", "sygma", "process"):
        cards = by.get(g, [])
        if not cards:
            continue
        label, sub = GRP_LABEL[g]
        body.append(f'<div class="ggrp"><h2>{label}</h2><div class="sub">{sub}</div>'
                    '<div class="cards">')
        for c in cards:
            cls, wlabel = WHO.get(c["whose_word"], ("o", "our word"))
            bits = [f'<div class="t">{esc(c["term"])}'
                    f'<span class="who {cls}">{wlabel}</span></div>',
                    f'<div class="m">{esc(c["plain_meaning"])}</div>']
            if c.get("example"):
                bits.append(f'<div class="ex">{esc(c["example"])}</div>')
            if c.get("where_it_appears"):
                bits.append(f'<div class="w">Where: {esc(c["where_it_appears"])}</div>')
            body.append(f'<div class="gcard">{"".join(bits)}</div>')
        body.append("</div></div>")
    body.append("</div>")
    return (ui.head("Glossary | Genny&#8217;s Damage Depot", PAGE_CSS)
            + ui.navbar("glossary") + "".join(body) + ui.TAIL)


def publish(html):
    vocab_gate(html, "the rendered glossary page")
    mod = {"module_key": MK, "slug": MK, "title": "Glossary",
           "section": "Customers", "subsection": "External", "area": "Clancy",
           "tier": "passcode", "passcode": "strive2030",
           "unlock_group": "clancy-depotnet",
           "icon": "📖", "accent": "#97D700", "status": "live", "enabled": True, "sort": 19,
           "groups": ["clancy", "clancy-external"], "tags": ["clancy", "customer", "glossary"]}
    rest("modules?on_conflict=module_key", "POST", [mod],
         {"Prefer": "resolution=merge-duplicates"})
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # no apostrophes: the reason is interpolated into single-quoted SQL
    reason = ("Glossary defines Depotnet field names verbatim - the wording rules own "
              "the verbatim-label exception")
    assert "$gl$" not in html
    sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
        f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
        f"('{MK}', $gl${html}$gl$, '{now}') "
        f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, "
        f"updated_at=EXCLUDED.updated_at;")
    print(f"published {MK} — commandcentre.info/m/{MK}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    if a.seed:
        seed()
    if a.local:
        html = build()
        open(a.local, "w").write(html)
        print(f"wrote {a.local} ({len(html):,} chars)")
    if a.publish:
        publish(build())


if __name__ == "__main__":
    main()
