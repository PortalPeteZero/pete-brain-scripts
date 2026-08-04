#!/usr/bin/env python3
"""clancy-dn-analysis-v2.py — "What the data tells us" read a second way: with the documents open.

WHY A SEPARATE PAGE RATHER THAN A SECOND EDITION. Pete, 3 Aug 2026: "we keep what we have as v1,
depotnet only sourced, then copy everything exactly to a v2". The v1 pages are the evidence that
the Depotnet-only picture was thin; re-rendering them would destroy that evidence. So v1
(`clancy-damage-analysis`) is never touched by this tool, and this page stands beside it.

WHAT IT IS. The same damages, the same year, the same questions — answered from the material
Clancy already held and nobody had read: 543 files, 224 documents, 986 photographs and scanned
pages. Every figure is computed live at build from `clancy_dn_doc_extracts`,
`clancy_dn_image_readings` and the `doc_*` columns; the v1 side of every comparison comes from
`clancy_dn_baseline_pre_enrichment`, the snapshot frozen before any enrichment ran.

THE RULE THIS PAGE OBEYS: it never rewrites either side. Depotnet's words are quoted as Depotnet
wrote them, and the documents' words are quoted as Clancy's own panels wrote them. Where the two
disagree, the page shows both and says so. Disagreement visible rather than silently merged is
the whole point.

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-analysis-v2.py --local out.html
  VAULT=/tmp/pbs python3 clancy-dn-analysis-v2.py --publish
"""
import os, sys, json, argparse, datetime, subprocess, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
sys.path.insert(0, VAULT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clancy_dn_ui as ui
import html as H

FY = os.environ.get("CLANCY_FY", "FY26/27")
FYLABEL = {"FY26/27": "FY 2026/27", "FY25/26": "FY 2025/26"}.get(FY, FY)
MK = {"FY26/27": "clancy-damage-analysis-v2"}[FY]
PARSER = "e1-2026-08-03"
GREEN, RED, CHAR = ui.GREEN, ui.RED, ui.CHAR


def _urlopen_retry(req, timeout=180, tries=9):
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


def sql(q):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    with _urlopen_retry(req) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def esc(v):
    return H.escape(str(v if v is not None else ""), quote=False)


def vocab_gate(text):
    r = subprocess.run([sys.executable, f"{VAULT}/clancy-vocab-check.py", "-"],
                       input=text, capture_output=True, text=True)
    if r.returncode != 0:
        print("VOCAB GATE FAILED:\n" + r.stdout + r.stderr)
        sys.exit(1)


# ────────────────────────────────────────────────────────────────────────── gather

def gather():
    d = {}
    d["n"] = sql(f"SELECT count(*) n FROM clancy_dn_incidents WHERE fy='{FY}'")[0]["n"]

    # what the READING actually covered — measured from the ledger, not asserted
    d["files"] = sql(f"""
        SELECT count(*) files,
               count(*) FILTER (WHERE l.status IN ('read','read+vision')) docs_read,
               count(*) FILTER (WHERE l.status='not-held') not_held,
               sum(l.chars_out) chars
        FROM clancy_dn_enrich_ledger l
        JOIN clancy_dn_incidents i ON i.id=l.incident_id AND i.fy='{FY}'
        WHERE l.parser_version='{PARSER}'""")[0]
    d["images"] = sql(f"""
        SELECT count(*) images, count(*) FILTER (WHERE has_text) with_text,
               count(DISTINCT incident_id) damages
        FROM clancy_dn_image_readings r
        JOIN clancy_dn_incidents i ON i.id=r.incident_id AND i.fy='{FY}'
        WHERE r.parser_version='{PARSER}'""")[0]
    d["classes"] = sql(f"""
        SELECT doc_class, count(*) n FROM clancy_dn_doc_extracts x
        JOIN clancy_dn_incidents i ON i.id=x.incident_id AND i.fy='{FY}'
        WHERE x.parser_version='{PARSER}' AND doc_class NOT IN ('photo','video')
        GROUP BY 1 ORDER BY 2 DESC""")

    # the v1 / v2 comparison, from the FROZEN baseline against the promoted columns
    d["cmp"] = sql(f"""
        SELECT
          count(*) damages,
          count(*) FILTER (WHERE b.lessons_learnt IS NULL OR btrim(b.lessons_learnt)='') v1_no_lesson,
          count(*) FILTER (WHERE b.root_cause IS NULL OR btrim(b.root_cause)='')        v1_no_cause,
          count(*) FILTER (WHERE i.doc_lessons IS NOT NULL)                              v2_lessons,
          count(*) FILTER (WHERE i.doc_conclusions IS NOT NULL)                          v2_cause,
          count(*) FILTER (WHERE i.doc_method_failures IS NOT NULL)                      v2_failures,
          count(*) FILTER (WHERE (b.lessons_learnt IS NULL OR btrim(b.lessons_learnt)='')
                             AND i.doc_lessons IS NOT NULL)                              recovered_lesson,
          round(avg(length(b.lessons_learnt)) FILTER (WHERE btrim(coalesce(b.lessons_learnt,''))<>'')) v1_avg,
          round(avg(array_length(i.doc_lessons,1)) FILTER (WHERE i.doc_lessons IS NOT NULL),1) v2_avg_lessons
        FROM clancy_dn_incidents i
        LEFT JOIN clancy_dn_baseline_pre_enrichment b ON b.id=i.id
        WHERE i.fy='{FY}'""")[0]

    # the counted method failures — the cross-damage prize
    d["failures"] = sql(f"""
        SELECT unnest(doc_method_failures) failure, count(*) damages
        FROM clancy_dn_incidents WHERE fy='{FY}' AND doc_method_failures IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC, 1""")

    # the head-to-head: thinnest v1 lessons that the documents answered
    d["heads"] = sql(f"""
        SELECT i.id, i.location, i.incident_date,
               b.lessons_learnt v1_lesson, b.root_cause v1_cause,
               i.doc_conclusions, i.doc_lessons, i.doc_method_failures,
               i.doc_sources
        FROM clancy_dn_incidents i
        JOIN clancy_dn_baseline_pre_enrichment b ON b.id=i.id
        WHERE i.fy='{FY}' AND i.doc_lessons IS NOT NULL
          AND (b.lessons_learnt IS NULL OR length(btrim(b.lessons_learnt)) <= 60)
        ORDER BY length(coalesce(btrim(b.lessons_learnt),'')) , i.id""")

    # what the photographs evidence, counted by tag
    d["tags"] = sql(f"""
        SELECT lower(t) tag, count(*) n, count(DISTINCT r.incident_id) damages
        FROM clancy_dn_image_readings r
        JOIN clancy_dn_incidents i ON i.id=r.incident_id AND i.fy='{FY}',
             unnest(r.shows) t
        WHERE r.parser_version='{PARSER}'
        GROUP BY 1 HAVING count(*) >= 4 ORDER BY 2 DESC LIMIT 28""")

    # what the PHOTOGRAPHS alone evidence — findings no document states, only looking finds.
    # Framed as an observation, never a conclusion: a photograph shows what was visible at the
    # moment it was taken, and excavation itself disturbs the ground above a service.
    d["seen"] = sql(f"""
        SELECT lower(t) tag, count(*) images, count(DISTINCT r.incident_id) damages
        FROM clancy_dn_image_readings r
        JOIN clancy_dn_incidents i ON i.id=r.incident_id AND i.fy='{FY}',
             unnest(r.shows) t
        WHERE r.parser_version='{PARSER}'
          AND lower(t) IN ('no marker tape','marker tape','no warning tape','concrete tile',
                           'hand dig','mechanical excavation','cat tool','genny','marker paint',
                           'standing water','night working','open trench')
        GROUP BY 1 ORDER BY 3 DESC""")

    # the paperwork nobody could read before: photographed/scanned documents with text
    d["paper"] = sql(f"""
        SELECT r.incident_id, r.label, r.description, r.transcription
        FROM clancy_dn_image_readings r
        JOIN clancy_dn_incidents i ON i.id=r.incident_id AND i.fy='{FY}'
        WHERE r.parser_version='{PARSER}' AND r.has_text
          AND length(coalesce(r.transcription,'')) >= 400
        ORDER BY length(r.transcription) DESC LIMIT 12""")

    # coverage: which damages the reading reached, and which hold nothing to read
    d["cover"] = sql(f"""
        SELECT count(*) FILTER (WHERE f.n > 0) with_files,
               count(*) FILTER (WHERE coalesce(f.n,0) = 0) no_files,
               count(*) FILTER (WHERE f.docs > 0) with_documents
        FROM clancy_dn_incidents i
        LEFT JOIN (
          SELECT incident_id, count(*) n,
                 count(*) FILTER (WHERE doc_class NOT IN ('photo','video')) docs
          FROM clancy_dn_doc_extracts WHERE parser_version='{PARSER}' GROUP BY 1
        ) f ON f.incident_id=i.id
        WHERE i.fy='{FY}'""")[0]
    return d


# ────────────────────────────────────────────────────────────────────────── render

CSS = f"""
.wrap{{max-width:1120px;margin:0 auto;padding:22px 20px 70px}}
.sec{{margin:34px 0 0}}
.sec h2{{font-size:23px;letter-spacing:-.025em;margin:0 0 4px}}
.sec .lede{{color:var(--faint);font-size:14.5px;max-width:78ch;line-height:1.6}}
.tag{{display:inline-block;color:#fff;font-size:11px;font-weight:800;letter-spacing:.06em;
 text-transform:uppercase;border-radius:20px;padding:3px 11px;margin-right:9px;vertical-align:3px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:13px;margin:18px 0 6px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:15px;padding:15px 17px;box-shadow:var(--sh-1)}}
.card .n{{font-size:31px;font-weight:800;letter-spacing:-.03em;line-height:1.05}}
.card .l{{font-size:12.5px;color:var(--faint);margin-top:3px;line-height:1.45}}
.card.g .n{{color:{GREEN}}} .card.r .n{{color:{RED}}}
.vs{{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid var(--line);border-radius:15px;
 overflow:hidden;margin:16px 0;box-shadow:var(--sh-1);background:#fff}}
.vs>div{{padding:15px 18px}}
.vs .a{{background:#fbfbfc;border-right:1px solid var(--line)}}
.vs h4{{font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--faint);margin:0 0 8px}}
.vs .q{{font-size:14.5px;line-height:1.6}}
.vs .q em{{color:#9aa;font-style:normal}}
.vs ul{{margin:0;padding-left:17px}} .vs li{{font-size:14px;line-height:1.55;margin-bottom:4px}}
.dmg{{background:#fff;border:1px solid var(--line);border-radius:15px;margin:14px 0;box-shadow:var(--sh-1);overflow:hidden}}
.dmg .hd{{padding:11px 18px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:baseline;
 background:linear-gradient(180deg,#fcfdfb,#fff)}}
.dmg .hd b{{font-size:15px}} .dmg .hd span{{color:var(--faint);font-size:13px}}
.bars{{margin:16px 0}}
.bar{{display:grid;grid-template-columns:minmax(210px,42%) 1fr 52px;gap:11px;align-items:center;
 padding:6px 0;font-size:13.5px}}
.bar .t{{color:var(--mid)}}
.bar .track{{background:#eef0f2;border-radius:7px;height:14px;overflow:hidden}}
.bar .fill{{height:100%;background:{RED};border-radius:7px}}
.bar .v{{text-align:right;font-weight:700;font-variant-numeric:tabular-nums}}
.chips{{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0}}
.chip{{background:#fff;border:1px solid var(--line);border-radius:20px;padding:4px 11px;font-size:12.5px}}
.chip b{{color:{CHAR}}}
.paper{{background:#fff;border:1px solid var(--line);border-radius:15px;padding:15px 18px;margin:13px 0;
 box-shadow:var(--sh-1)}}
.paper .lbl{{font-size:12.5px;color:var(--faint);margin-bottom:7px}}
.paper pre{{background:#fafbfa;border:1px solid var(--line);border-radius:10px;padding:11px 13px;
 font-size:12.5px;line-height:1.5;white-space:pre-wrap;overflow-x:auto;margin:8px 0 0;max-height:330px}}
.note{{color:var(--faint);font-size:13px;margin-top:10px;line-height:1.6}}
.callout{{border-left:4px solid {GREEN};background:#fbfef5;border-radius:0 12px 12px 0;padding:14px 18px;
 margin:18px 0;font-size:14.5px;line-height:1.65}}
@media(max-width:760px){{.vs{{grid-template-columns:1fr}}.vs .a{{border-right:0;border-bottom:1px solid var(--line)}}
 .bar{{grid-template-columns:1fr 60px}}.bar .track{{display:none}}}}
"""


def card(n, label, cls=""):
    return f'<div class="card {cls}"><div class="n">{n}</div><div class="l">{label}</div></div>'


def bars(rows, key, val, total):
    out = ['<div class="bars">']
    mx = max([r[val] for r in rows], default=1) or 1
    for r in rows:
        pct = 100 * r[val] / mx
        out.append(f'<div class="bar"><div class="t">{esc(r[key])}</div>'
                   f'<div class="track"><div class="fill" style="width:{pct:.1f}%"></div></div>'
                   f'<div class="v">{r[val]}</div></div>')
    out.append("</div>")
    return "".join(out)


def build():
    d = gather()
    f, im, c = d["files"], d["images"], d["cmp"]
    when = datetime.datetime.now().strftime("%d %B %Y %H:%M")
    chars = int(f["chars"] or 0)

    b = ['<div class="wrap">']

    # ── the headline
    b.append(f"""
<div class="sec"><h2><span class="tag" style="background:{CHAR}">The headline</span>
The answers were already in the file</h2>
<div class="lede">Nothing here comes from a new investigation, a site visit or a conversation.
Every word below was already sitting in Clancy&#8217;s own Depotnet records &mdash; attached to the
damages, uploaded by Clancy&#8217;s own teams and panels, and never read. This page is the same
year and the same damages as
<a href="/m/clancy-damage-analysis">what the data tells us</a>, answered a second time with those
files open.</div></div>""")
    b.append('<div class="cards">'
             + card(f'{f["files"]:,}', f'files opened, every one accounted for')
             + card(f'{f["docs_read"]:,}', 'documents read in full', 'g')
             + card(f'{im["images"]:,}', 'photographs and scanned pages looked at', 'g')
             + card(f'{im["with_text"]:,}', 'of those carried readable text, transcribed')
             + card(f'{chars/1_000_000:.1f}m', 'characters of text recovered')
             + "</div>")

    b.append(f"""
<div class="callout"><b>What that bought.</b> Depotnet&#8217;s own investigation field was blank on
<b>{c["v1_no_lesson"]}</b> of this year&#8217;s {c["damages"]} damages. Reading the attached documents
recovered a stated lesson on <b>{c["recovered_lesson"]}</b> of those, and a stated primary cause on
<b>{c["v2_cause"]}</b> damages in total. The material was never missing. It was unread.</div>""")

    # ── coverage / honesty first
    cov = d["cover"]
    b.append(f"""
<div class="sec"><h2><span class="tag" style="background:{CHAR}">First</span>
What was read, and what there was nothing to read</h2>
<div class="lede">This page can only speak for damages that hold something. Of
{c["damages"]} damages this year, <b>{cov["with_files"]}</b> hold files and
<b>{cov["with_documents"]}</b> hold at least one document rather than photographs alone.
<b>{cov["no_files"]}</b> hold nothing at all &mdash; and on those, the Depotnet record remains the
only account there is.</div>""")
    if d["classes"]:
        b.append('<div class="chips">' + "".join(
            f'<span class="chip"><b>{r["n"]}</b> {esc(r["doc_class"].replace("-"," "))}</span>'
            for r in d["classes"]) + "</div>")
    b.append(f'<div class="note">Every one of the {f["files"]:,} files carries a reading record: how it '
             f'was opened, how much came out of it, or the reason it could not be read. '
             f'{f["not_held"]} file(s) could not be read and are named rather than quietly dropped.</div>'
             "</div>")

    # ── v1 vs v2 head to head
    b.append(f"""
<div class="sec"><h2><span class="tag" style="background:{RED}">The comparison</span>
The same damages, told twice</h2>
<div class="lede">Left is what Depotnet held before any document was opened, taken from the
snapshot frozen on 31 July. Right is what the attached documents say. Neither side is rewritten.
Where they disagree, both are shown.</div></div>""")

    for r in (d["heads"] or [])[:10]:
        v1 = (r.get("v1_lesson") or "").strip()
        loc = esc(r.get("location") or "")
        dt = (r.get("incident_date") or "")[:10]
        concl = r.get("doc_conclusions") or []
        less = r.get("doc_lessons") or []
        srcs = (r.get("doc_sources") or {}).get("documents") or []
        srcname = esc(srcs[0]["name"]) if srcs else "an attached document"
        b.append(f'<div class="dmg"><div class="hd"><b>Damage {r["id"]}</b>'
                 f'<span>{loc}{" · " if loc else ""}{dt}</span></div>'
                 '<div class="vs" style="margin:0;border:0;border-radius:0;box-shadow:none">'
                 '<div class="a"><h4>What Depotnet recorded</h4>'
                 f'<div class="q">{("&ldquo;" + esc(v1) + "&rdquo;") if v1 else "<em>Left blank</em>"}</div></div>'
                 f'<div><h4>What the documents say &mdash; {srcname}</h4>')
        if concl:
            b.append("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in concl[:4]) + "</ul>")
        if less:
            b.append('<div style="font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;'
                     'color:var(--faint);margin:9px 0 5px">Lessons their panel wrote</div>')
            b.append("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in less[:4]) + "</ul>")
        b.append("</div></div></div>")

    if len(d["heads"] or []) > 10:
        b.append(f'<div class="note">Ten shown of {len(d["heads"])} damages where the Depotnet '
                 f'lesson was blank or a single line and the documents carried more.</div>')

    # ── the counted pattern — the prize
    if d["failures"]:
        b.append(f"""
<div class="sec"><h2><span class="tag" style="background:{RED}">The pattern</span>
The same failures, counted across the year</h2>
<div class="lede">A named failure in one panel review is an anecdote. The same failure counted
across the year is a finding you can act on. These counts come from the documents&#8217; own
wording, not from a category anyone ticked on a form.</div>""")
        b.append(bars(d["failures"], "failure", "damages", c["damages"]))
        b.append('<div class="note">Counted where a document states it in its own words. A damage '
                 'can carry more than one. These are floors, not ceilings: a failure that no '
                 'document mentions cannot be counted here.</div></div>')

    # ── the photographs
    if im["images"]:
        b.append(f"""
<div class="sec"><h2><span class="tag" style="background:{GREEN};color:#25320a">The photographs</span>
{im["images"]:,} images, looked at one by one</h2>
<div class="lede">Every photograph attached to a damage this year was opened and described, across
{im["damages"]} damages. {im["with_text"]:,} of them carry readable text &mdash; permits, plans,
locator readouts, whiteboards and signage &mdash; and that text is transcribed, so paperwork that
only existed as a photograph is now searchable evidence.</div>""")
        if d["tags"]:
            b.append('<div class="chips">' + "".join(
                f'<span class="chip"><b>{r["n"]}</b> {esc(r["tag"])}</span>' for r in d["tags"])
                + "</div>")
        b.append("</div>")

    # ── what only looking finds
    if d.get("seen"):
        b.append(f"""
<div class="sec"><h2><span class="tag" style="background:{RED}">Only looking finds this</span>
What the photographs show that no document says</h2>
<div class="lede">These are not claims from anybody&#8217;s report. They are counts of what the
photographs themselves show, recorded while each image was being looked at. No form asks these
questions, so this evidence has never been counted before.</div>""")
        b.append(bars(d["seen"], "tag", "damages", c["damages"]))
        b.append('<div class="note"><b>Read these as observations, not verdicts.</b> A photograph '
                 'shows what was visible at the moment it was taken, and the excavation itself '
                 'disturbs whatever sat above the service. Where the count says no marker tape was '
                 'visible, that is exactly what it says &mdash; not proof that none was ever laid. '
                 'It is a question worth putting to the teams, on a scale nobody could see '
                 'before.</div></div>')

    # ── the paperwork recovered
    if d["paper"]:
        b.append(f"""
<div class="sec"><h2><span class="tag" style="background:{CHAR}">Recovered</span>
Paperwork that was only ever a photograph</h2>
<div class="lede">These are the longest transcriptions the reading recovered: forms, permits and
system printouts that were attached as images and therefore invisible to every search anyone has
ever run on this data. Shown as transcribed, not summarised.</div>""")
        for p in d["paper"][:6]:
            t = (p.get("transcription") or "")[:2200]
            b.append(f'<div class="paper"><div class="lbl">Damage {p["incident_id"]} &middot; '
                     f'{esc(p["label"])}</div>'
                     f'<div style="font-size:13.5px;line-height:1.6">{esc((p.get("description") or "")[:420])}</div>'
                     f"<pre>{esc(t)}</pre></div>")
        b.append("</div>")

    # ── what it still cannot tell you
    b.append(f"""
<div class="sec"><h2><span class="tag" style="background:{CHAR}">The limit</span>
What reading the file still cannot tell you</h2>
<div class="lede">Reading recovers what somebody wrote down. It cannot recover what nobody
recorded. Where a panel never sat, where a form was left blank and no document was attached, or
where a damage holds nothing at all, this page is as silent as the last one &mdash; and it says so
rather than filling the gap. Every figure above is derived at build time from the reading records;
none of it is typed in.</div></div>""")

    b.append("</div>")
    page = (ui.head(f"What the data tells us &mdash; with the documents read | Genny&#8217;s Damage Depot", CSS)
            + ui.navbar("analysis")
            + ui.crumbs(("Damage Depot", "/m/clancy-depotnet"),
                        ("What the data tells us", "/m/clancy-damage-analysis"),
                        "With the documents read")
            + ui.hero(kicker=f"{FYLABEL} &middot; second reading",
                      title="What the data tells us, with the documents read",
                      sub="The same damages. The same year. The files opened.",
                      body="Clancy&#8217;s own panels, statements, permits and photographs, read in "
                           "full and set beside what Depotnet&#8217;s fields recorded.")
            + ui.page_switch("analysis", FY, "v2")
            + "".join(b) + ui.foot(when) + ui.TAIL)
    return page


def publish(html):
    vocab_gate(html)
    mod = {"module_key": MK, "slug": MK,
           "title": "What the data tells us &mdash; with the documents read",
           "section": "Customers", "subsection": "External", "area": "Clancy",
           "tier": "passcode", "passcode": "strive2030", "unlock_group": "clancy-depotnet",
           "icon": "📑", "accent": GREEN, "status": "live", "enabled": True, "sort": 11,
           "groups": ["clancy", "clancy-external"],
           "tags": ["clancy", "customer", "analysis", "enrichment"]}
    k = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))
    req = urllib.request.Request(
        f"{k['url']}/rest/v1/modules?on_conflict=module_key",
        data=json.dumps([mod]).encode(),
        headers={"apikey": k["service_role_key"],
                 "Authorization": f"Bearer {k['service_role_key']}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates"}, method="POST")
    _urlopen_retry(req)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # No apostrophes in this string: it is interpolated into a single-quoted SQL literal, and
    # "Clancy's" broke the whole publish with a syntax error (3 Aug 2026).
    reason = ("v2 analysis quotes Depotnet fields and the panel documents verbatim - "
              "the wording rules own the verbatim-source exception")
    assert "'" not in reason
    assert "$a2$" not in html
    sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
        f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
        f"('{MK}', $a2${html}$a2$, '{now}') "
        f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, updated_at=EXCLUDED.updated_at")
    print(f"published {MK} ({len(html):,} bytes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    html = build()
    if a.local:
        open(a.local, "w").write(html)
        print(f"wrote {a.local} ({len(html):,} bytes)")
    if a.publish:
        publish(html)


if __name__ == "__main__":
    main()
