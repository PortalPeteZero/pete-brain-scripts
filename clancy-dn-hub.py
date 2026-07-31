#!/usr/bin/env python3
"""clancy-dn-hub.py — the ONE front door for everything Depotnet: Genny's Damage Depot.

WHY: the Depotnet work grew as separate sections — the damages register, the Genny & CAT data
dive, the reports Sygma has produced, and Genny herself. Pete wants one place to land that then
takes him into any of them. This renders that landing page from live database counts, so it can
never drift from what the sections hold.

Deliberately its own script, not part of clancy-dn-pages.py, so a long backfill run publishing the
damages pages and a hub refresh can never collide. The chrome (navbar, breadcrumbs, brand bar,
tokens) comes from clancy_dn_ui.py, which every generator in this section imports.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-hub.py [--local out.html] [--publish]
"""
import os, json, argparse, datetime, urllib.request
import clancy_dn_ui as ui

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
MK = ui.HUB


def sql(q):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=120).read().decode())


PAGE_CSS = """
.chartcard{background:#fff;border:1px solid var(--line);border-radius:16px;padding:18px 20px 16px;
 box-shadow:var(--sh-1)}
.chartcard h3{font-size:14.5px;font-weight:800;letter-spacing:-.01em;margin-bottom:3px}
.chartcard .cap{font-size:11.5px;color:var(--faint);margin-bottom:14px}
.cols{display:flex;align-items:flex-end;gap:9px;height:132px;padding-top:6px}
.cols .c{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;
 gap:6px;height:100%}
.cols .bar{width:100%;max-width:54px;background:var(--green);border-radius:5px 5px 0 0;
 border-bottom:2px solid #fff;min-height:3px;transform-origin:bottom;animation:grow .6s cubic-bezier(.2,.7,.3,1) both}
@keyframes grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@keyframes slide{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.cols .c.dim .bar{background:#c9dfa6}
.cols .v{font-size:14px;font-weight:800;font-variant-numeric:tabular-nums}
.cols .k{font-size:11px;color:var(--faint);font-weight:600;white-space:nowrap}
.hbars{display:flex;flex-direction:column;gap:9px}
.hb{display:grid;grid-template-columns:118px 1fr 30px;align-items:center;gap:10px}
.hb .k{font-size:12px;color:var(--mid);text-align:right;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap}
.hb .t{height:15px;background:#eef1f5;border-radius:4px;overflow:hidden}
.hb .t i{display:block;height:100%;background:var(--green);border-radius:4px;
 border-right:2px solid #fff;transform-origin:left;animation:slide .6s cubic-bezier(.2,.7,.3,1) both}
.hb .v{font-size:12.5px;font-weight:800;font-variant-numeric:tabular-nums;text-align:right}
.mini{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.mini a{display:block;background:#fff;border:1px solid var(--line);border-radius:13px;
 padding:14px 16px;text-decoration:none;box-shadow:var(--sh-1);
 transition:transform .18s,box-shadow .18s}
.mini a:hover{transform:translateY(-2px);box-shadow:var(--sh-2)}
.mini .y{font-size:10.5px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
 color:var(--faint)}
.mini .n{font-size:25px;font-weight:800;font-variant-numeric:tabular-nums;margin:2px 0;
 letter-spacing:-.02em}
.mini .s{font-size:12px;color:var(--faint)}
.mini a.cur{border-color:var(--green);box-shadow:0 0 0 2px rgba(151,215,0,.25),var(--sh-1)}
"""


def cols(rows, dim_last=False):
    """A column chart. rows = [(label, value)]. One series only, so every column carries its own
    value and no legend is needed — the card's title names what is being counted."""
    mx = max([v for _, v in rows] + [1])
    out = []
    for i, (lab, v) in enumerate(rows):
        cls = " dim" if dim_last and i == len(rows) - 1 else ""
        h = max(round(100 * v / mx), 3)
        out.append(f'<div class="c{cls}"><div class="v">{v}</div>'
                   f'<div class="bar" style="height:{h}%"></div><div class="k">{lab}</div></div>')
    return f'<div class="cols">{"".join(out)}</div>'


def hbars(rows):
    """Horizontal bars for ranked categories, longest first, value direct-labelled."""
    mx = max([v for _, v in rows] + [1])
    out = "".join(
        f'<div class="hb"><div class="k" title="{lab}">{lab}</div>'
        f'<div class="t"><i style="width:{max(round(100 * v / mx), 2)}%"></i></div>'
        f'<div class="v">{v}</div></div>' for lab, v in rows)
    return f'<div class="hbars">{out}</div>'


def build():
    d = sql("""SELECT
      (SELECT count(*) FROM clancy_dn_incidents) AS damages,
      (SELECT count(*) FROM clancy_dn_incidents WHERE fy='FY26/27') AS cur,
      (SELECT count(*) FROM clancy_dn_incidents WHERE fy='FY26/27' AND status='Open') AS cur_open,
      (SELECT count(*) FROM clancy_dn_actions) AS actions,
      (SELECT count(*) FROM clancy_dn_actions WHERE status='Overdue') AS overdue,
      (SELECT count(*) FROM clancy_dn_incidents WHERE pdf_captured_at IS NOT NULL) AS captured,
      (SELECT count(*) FROM clancy_dn_incidents WHERE fy='FY26/27'
         AND pdf_captured_at IS NOT NULL) AS captured_cur,
      (SELECT count(*) FROM clancy_dn_answers) AS answers,
      (SELECT count(*) FROM clancy_dn_files) AS files,
      -- The Genny & CAT store is clancy_dn_gc_*. There is an older clancy_dn_inspections holding
      -- 83 rows — one inspector, one contract, and every row of it also present here. Reading that
      -- one made the hub report 83 where the section itself reports 5,403.
      (SELECT count(*) FROM clancy_dn_gc_inspections) AS inspections,
      (SELECT count(*) FROM clancy_dn_gc_actions) AS gc_actions,
      (SELECT count(*) FROM clancy_dn_gc_coverage) AS gc_operatives,
      (SELECT min(date_raised)::date FROM clancy_dn_actions) AS act_first,
      -- The 22 with no root cause are NOT uninvestigated: count how many of them carry a
      -- completed Depotnet form, and the range of questions answered, so the page can say so.
      (SELECT count(*) FROM clancy_dn_incidents i WHERE i.fy='FY26/27'
         AND (i.lessons_learnt IS NULL OR btrim(i.lessons_learnt)='')
         AND (i.root_cause IS NULL OR btrim(i.root_cause)='')
         AND EXISTS (SELECT 1 FROM clancy_dn_answers x WHERE x.incident_id=i.id)) AS inv_form,
      (SELECT min(n) FROM (SELECT count(*) n FROM clancy_dn_answers x
         JOIN clancy_dn_incidents i ON i.id=x.incident_id WHERE i.fy='FY26/27'
         AND (i.lessons_learnt IS NULL OR btrim(i.lessons_learnt)='')
         AND (i.root_cause IS NULL OR btrim(i.root_cause)='') GROUP BY x.incident_id) z) AS inv_lo,
      (SELECT max(n) FROM (SELECT count(*) n FROM clancy_dn_answers x
         JOIN clancy_dn_incidents i ON i.id=x.incident_id WHERE i.fy='FY26/27'
         AND (i.lessons_learnt IS NULL OR btrim(i.lessons_learnt)='')
         AND (i.root_cause IS NULL OR btrim(i.root_cause)='') GROUP BY x.incident_id) z) AS inv_hi,
      (SELECT count(*) FROM clancy_reports) AS reports,
      (SELECT count(*) FROM clancy_unmapped_damages) AS unmapped,
      (SELECT count(*) FROM clancy_dn_incidents WHERE sygma_reviewed_at IS NOT NULL) AS sygma_rev,
      (SELECT count(*) FROM clancy_dn_incidents WHERE fy='FY26/27'
         AND (lessons_learnt IS NULL OR btrim(lessons_learnt)='')
         AND (root_cause IS NULL OR btrim(root_cause)='')) AS no_inv,
      -- reviews-section figures are derived LIVE too: person pages from the page registry,
      -- evidence PDFs from the Drive index.
      (SELECT count(*) FROM module_content WHERE module_key LIKE 'clancy-genny-cat-reviews/%'
         AND module_key NOT LIKE '%register%' AND module_key NOT LIKE '%findings%'
         AND module_key NOT LIKE '%people%' AND module_key NOT LIKE '%coverage%') AS person_pages,
      (SELECT count(*) FROM drive_files WHERE path LIKE '%depotnet-inspection-reviews%'
         AND lower(name) LIKE '%.pdf') AS review_pdfs,
      -- The two registers come from separate exports and drift apart. Surface the drift on the
      -- page rather than letting a reader assume an empty actions list means none were raised.
      (SELECT max(date_raised)::date FROM clancy_dn_actions) AS act_latest,
      (SELECT max(incident_date)::date FROM clancy_dn_incidents) AS inc_latest,
      (SELECT count(*) FROM clancy_dn_incidents
        WHERE incident_date > (SELECT max(date_raised) FROM clancy_dn_actions)) AS after_cut""")[0]

    fmt = lambda v: (datetime.datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%-d %b %Y")
                     if v else "unknown")
    d["act_latest"], d["inc_latest"] = fmt(d["act_latest"]), fmt(d["inc_latest"])

    months = sql("""SELECT to_char(incident_date,'Mon') m, min(incident_date) o, count(*) n
                    FROM clancy_dn_incidents WHERE fy='FY26/27' GROUP BY 1 ORDER BY o""")
    utils = sql("""SELECT coalesce(nullif(btrim(utility_class),''),'Unclassified') u, count(*) n
                   FROM clancy_dn_incidents WHERE fy='FY26/27'
                   GROUP BY 1 ORDER BY n DESC, u LIMIT 6""")
    conts = sql("""SELECT coalesce(nullif(btrim(contract_family),''),'Other') c, count(*) n
                   FROM clancy_dn_incidents WHERE fy='FY26/27'
                   GROUP BY 1 ORDER BY n DESC, c LIMIT 7""")
    fys = sql("SELECT fy, count(*) n FROM clancy_dn_incidents GROUP BY fy ORDER BY fy")

    LBL = {"FY26/27": "FY 2026/27", "FY25/26": "FY 2025/26",
           "FY24/25": "FY 2024/25", "FY23/24": "FY 2023/24"}
    SHORT = {"FY26/27": "26/27", "FY25/26": "25/26", "FY24/25": "24/25", "FY23/24": "23/24"}
    STEM = {"FY26/27": "fy-2026-27", "FY25/26": "fy-2025-26",
            "FY24/25": "fy-2024-25", "FY23/24": "fy-2023-24"}
    known = [r for r in fys if r["fy"] in STEM]
    tiles = "".join(
        f'<a class="{"cur" if r["fy"] == "FY26/27" else ""}" '
        f'href="/raw/{ui.DAMAGES}/{STEM[r["fy"]]}.html">'
        f'<div class="y">{LBL[r["fy"]]}</div><div class="n">{r["n"]}</div>'
        f'<div class="s">{"this year, still running" if r["fy"] == "FY26/27" else "damages"}</div></a>'
        for r in sorted(known, key=lambda r: r["fy"], reverse=True))

    today = datetime.date.today()

    ch_months = cols([(r["m"], r["n"]) for r in months], dim_last=True)
    ch_utils = hbars([(r["u"], r["n"]) for r in utils])
    ch_conts = hbars([(r["c"], r["n"]) for r in conts])
    ch_fys = cols([(SHORT[r["fy"]], r["n"]) for r in known], dim_last=True)

    return f"""{ui.head("Genny&#8217;s Damage Depot | Sygma Solutions &times; The Clancy Group", PAGE_CSS)}
{ui.navbar("overview")}
{ui.crumbs(("Command Centre", "/"), ("Clancy", "/m/clancy-cockpit"), "Damage Depot")}

{ui.hero(body=f'''<div class="says">Hello, I am Genny and this is my depot. I have read every
  damage on here, every investigation, every corrective action and every document behind them.
  Ask me anything and I will tell you what the record actually says.</div>
  <div class="chips">
   <span class="chip"><b>{d['damages']:,}</b> damages</span>
   <span class="chip"><b>{d['answers']:,}</b> investigation answers</span>
   <span class="chip"><b>{d['files']}</b> documents &amp; photos</span>
   <span class="chip"><b>{d['reports']}</b> reports</span>
  </div>''')}

<div class="wrap body">
<div class="kpis">
 <div class="kpi"><div class="n">{d['cur']}</div>
  <div class="l">damages so far<br>this financial year</div></div>
 <div class="kpi"><div class="n {'red' if d['cur_open'] else 'grn'}">{d['cur_open']}</div>
  <div class="l">still open<br>this year</div></div>
 <div class="kpi"><div class="n red">{d['no_inv']}</div>
  <div class="l">this year with no root cause<br>or lessons recorded</div></div>
 <div class="kpi"><div class="n">{d['captured_cur']}/{d['cur']}</div>
  <div class="l">of this year deep-captured<br>from Depotnet</div></div>
 <div class="kpi"><div class="n {'red' if d['overdue'] else 'grn'}">{d['overdue']}</div>
  <div class="l">corrective actions<br>overdue</div></div>
</div>

<h2 class="sec">This year at a glance</h2>
<div class="grid g2">
 <div class="chartcard"><h3>Damages month by month</h3>
  <div class="cap">Financial year 2026/27. The current month is still running.</div>{ch_months}</div>
 <div class="chartcard"><h3>Which utility was hit</h3>
  <div class="cap">This year, by the utility class on the damage record.</div>{ch_utils}</div>
 <div class="chartcard"><h3>Which contract</h3>
  <div class="cap">This year, top {len(conts)} by number of damages.</div>{ch_conts}</div>
 <div class="chartcard"><h3>Four years on the register</h3>
  <div class="cap">Every service damage Depotnet holds. 2026/27 is part-year.</div>{ch_fys}</div>
</div>

<h2 class="sec">Where do you want to go</h2>
<div class="grid g2">
 <a class="door a" href="/m/{ui.DAMAGES}">
  <div class="kicker">The register</div>
  <div class="t">Damages</div>
  <div class="d">Every service damage on Depotnet, group-wide, by financial year, with the incident
  record, its corrective actions, the full investigation where one exists, the documents captured
  against it and Sygma&#8217;s own findings on top.</div>
  <div class="figs">
   <div><div class="n">{d['cur']}</div><div class="l">this year</div></div>
   <div><div class="n">{d['captured_cur']}/{d['cur']}</div><div class="l">this year deep-captured</div></div>
   <div><div class="n">{d['files']}</div><div class="l">documents</div></div>
  </div>
  <div class="go">Open the register &rarr;</div>
 </a>
 <a class="door b" href="/m/{ui.REPORTS}">
  <div class="kicker">What Sygma produced</div>
  <div class="t">Reports &amp; Reviews</div>
  <div class="d">Every report, review, business case and panel pack written for Clancy, in one
  searchable place. Damage reviews link back to the damage they belong to, so you can read the
  finding and the record side by side.</div>
  <div class="figs">
   <div><div class="n">{d['reports']}</div><div class="l">in the library</div></div>
   <div><div class="n">{d['sygma_rev']}</div><div class="l">damages Sygma reviewed</div></div>
   <div><div class="n">{d['files']}</div><div class="l">documents behind them</div></div>
  </div>
  <div class="go">Open the library &rarr;</div>
 </a>
 <a class="door c" href="/m/{ui.REVIEWS}">
  <div class="kicker">The data dive</div>
  <div class="t">Genny &amp; CAT Reviews</div>
  <div class="d">How Genny &amp; CAT inspections are actually being carried out: what the scores
  really mean, how the actions get closed, which operatives are being reviewed and which
  are not.</div>
  <div class="figs">
   <div><div class="n">{d['person_pages']}</div><div class="l">named reviews</div></div>
   <div><div class="n">{d['review_pdfs']}</div><div class="l">reports read line by line</div></div>
   <div><div class="n">{d['inspections']:,}</div><div class="l">usage inspections</div></div>
  </div>
  <div class="go">Open the review &rarr;</div>
 </a>
 <a class="door a" href="/m/{ui.BOT}">
  <div class="kicker">Ask instead of hunt</div>
  <div class="t">Ask Genny</div>
  <div class="d">She reads the whole store: every damage and its investigation, the corrective
  actions, the evidence held against each one, the usage inspections and Sygma&#8217;s findings.
  Named after the instrument that finds what is buried. She sits on every page in this section,
  bottom right.</div>
  <div class="figs">
   <div><div class="n">{d['damages']:,}</div><div class="l">damages she reads</div></div>
   <div><div class="n">{d['answers']:,}</div><div class="l">answers</div></div>
   <div><div class="n">{d['reports']}</div><div class="l">reports</div></div>
  </div>
  <div class="go">Ask her a question &rarr;</div>
 </a>
</div>

<h2 class="sec">Straight to a year</h2>
<div class="mini">{tiles}</div>

<div class="dnote"><b>{d['unmapped']} Sygma records have no Depotnet counterpart.</b> Sygma kept
its own damage register alongside Depotnet. Those records have been merged in: the ones that
matched a Depotnet incident now carry their Sygma summary, findings, narrative and report on the
Depotnet record itself. The {d['unmapped']} that matched nothing are held separately, out of every
count on this site, because they are not Depotnet damages.
<a href="/m/clancy-unmapped-damages" style="color:var(--green-d);font-weight:800;
text-decoration:none">See the unmapped damages &rarr;</a></div>

<div class="dnote"><b>Note on coverage.</b> The register itself is complete for all four years and
fully searchable, and Genny reads the same store. Deep capture, meaning the investigation answers
and the documents pulled off each Depotnet record, has been done for this financial year and is all but finished there:
{d['captured_cur']} of this year's {d['cur']} damages carry it, and {d['captured']} across the
register as a whole. The Genny &amp; CAT review is a written
analysis with its evidence attached, sitting on {d['inspections']:,} usage inspections and
{d['gc_actions']:,} inspection actions in the database.</div>

<div class="dnote"><b>{d['no_inv']} of this year's {d['cur']} damages carry no root cause and no
lessons learnt.</b> That is not the same as no investigation: {d['inv_form']} of those
{d['no_inv']} do have a completed Depotnet investigation form against them, with
{d['inv_lo']} to {d['inv_hi']} questions answered. What is missing on every one is the pair of
fields that would say why it happened and what to do differently. The form is being filled in; the
conclusion is being left blank.</div>

<div class="dnote"><b>No corrective action has been raised on a service damage since
{d['act_latest']}.</b> We hold <b>{d['actions']}</b> Service Damage actions, running back to
{d['act_first']}, every one of them linked to a damage on this register. That is the complete set:
checked against Depotnet directly on 31 July 2026 with every filter cleared, where the Action
Report held 3,765 actions across seven incident categories and Service Damage was exactly the
{d['actions']} we hold. Over the same weeks Depotnet raised 160 actions in other categories
(Observation and Near Miss, Injury, Fleet, Security) right through to 31 July, so the system is in
daily use. Since {d['act_latest']}, <b>{d['after_cut']} service damages</b> have been logged,
running to {d['inc_latest']}, with not one corrective action raised against any of them. This is a
gap in the process, not a gap in the data.<br><span style="color:var(--faint);font-size:12.5px">
The 3,765 and the 160 are the only figures on this page not read live from the database: they
count Depotnet categories we do not import, so they are what was on screen on 31 July 2026.
Everything else updates itself.</span></div>

{ui.foot(today.strftime('%-d %b %Y'))}
</div>
{ui.TAIL}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    html = build()
    if a.local:
        open(a.local, "w").write(html)
        print(f"wrote {a.local} ({len(html):,} chars)")
    if a.publish:
        mod = {"module_key": MK, "slug": MK, "title": "Genny's Damage Depot",
               "section": "Customers", "subsection": "External", "area": "Clancy",
               "tier": "passcode", "passcode": "strive2030",
               # one section, one gate: every Depot page shares this group
               "unlock_group": "clancy-depotnet", "icon": "🦺", "accent": "#97D700",
               "status": "live", "enabled": True, "sort": 12,
               "groups": ["clancy", "clancy-external"], "tags": ["clancy", "customer", "depotnet"]}
        req = urllib.request.Request(f"{URL}/rest/v1/modules?on_conflict=module_key",
            data=json.dumps([mod]).encode(),
            headers={"apikey": SR, "Authorization": f"Bearer {SR}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates"}, method="POST")
        urllib.request.urlopen(req, timeout=60)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        reason = ("Depotnet hub quotes register descriptions verbatim - the wording rules own "
                  "verbatim-quote exception; Sygma prose says damage throughout")
        assert "$hub$" not in html
        sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
            f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
            f"('{MK}', $hub${html}$hub$, '{now}') "
            f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, "
            f"updated_at=EXCLUDED.updated_at;")
        print(f"published {MK} (passcode strive2030) — commandcentre.info/m/{MK}")


if __name__ == "__main__":
    main()
