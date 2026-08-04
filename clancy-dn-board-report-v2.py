#!/usr/bin/env python3
"""clancy-dn-board-report-v2.py — the second report: the same year, with the documents read.

The v1 report (`clancy-damage-board-report`, presented to the STRIVE board on 3 Aug 2026) says on
its own face that the supporting documents "have NOT yet been read. A second report follows once
that work is done." This is that second report.

It is a REPLICA, not a re-render: v1 is never touched. Pete, 3 Aug 2026 — "we keep what we have as
v1, depotnet only sourced, then copy everything exactly to a v2". v1 is the evidence of how thin
the Depotnet-only account was; overwriting it would destroy the finding.

Same visual language as v1 (bands, big stats, stated tests) so the two read as a pair. Every
figure is derived live from the reading records at build. The v1 side of every comparison comes
from `clancy_dn_baseline_pre_enrichment`, frozen before enrichment ran.

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-board-report-v2.py --local out.html
  VAULT=/tmp/pbs python3 clancy-dn-board-report-v2.py --publish
"""
import os, sys, json, argparse, datetime, subprocess, urllib.request, urllib.error
import html as H

VAULT = os.environ.get("VAULT", "/tmp/pbs")
sys.path.insert(0, VAULT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clancy_dn_ui as ui

FY = os.environ.get("CLANCY_FY", "FY26/27")
FYLABEL = {"FY26/27": "FY 2026/27", "FY25/26": "FY 2025/26"}.get(FY, FY)
MK = "clancy-damage-board-report-v2"
PARSER = "e1-2026-08-03"
GREEN, RED, CHAR = "#97D700", "#D50032", "#353E47"
G_T, R_T, C_T = "#f4fbe8", "#fdf0f3", "#f5f6f8"


def _retry(req, timeout=180, tries=9):
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
    with _retry(req) as r:
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


def gather():
    d = {}
    d["n"] = sql(f"SELECT count(*) n FROM clancy_dn_incidents WHERE fy='{FY}'")[0]["n"]
    d["read"] = sql(f"""
      SELECT count(*) files,
             count(*) FILTER (WHERE l.status IN ('read','read+vision')) docs,
             count(*) FILTER (WHERE l.status='not-held') not_held,
             coalesce(sum(l.chars_out),0) chars
      FROM clancy_dn_enrich_ledger l JOIN clancy_dn_incidents i
        ON i.id=l.incident_id AND i.fy='{FY}'
      WHERE l.parser_version='{PARSER}'""")[0]
    d["img"] = sql(f"""
      SELECT count(*) n, count(*) FILTER (WHERE has_text) text,
             count(DISTINCT incident_id) damages
      FROM clancy_dn_image_readings r JOIN clancy_dn_incidents i
        ON i.id=r.incident_id AND i.fy='{FY}'
      WHERE r.parser_version='{PARSER}'""")[0]
    d["cmp"] = sql(f"""
      SELECT count(*) damages,
        count(*) FILTER (WHERE b.lessons_learnt IS NULL OR btrim(b.lessons_learnt)='') v1_blank,
        count(*) FILTER (WHERE i.doc_lessons IS NOT NULL) v2_lessons,
        count(*) FILTER (WHERE i.doc_conclusions IS NOT NULL) v2_cause,
        count(*) FILTER (WHERE i.doc_method_failures IS NOT NULL) v2_fail,
        count(*) FILTER (WHERE (b.lessons_learnt IS NULL OR btrim(b.lessons_learnt)='')
                           AND i.doc_lessons IS NOT NULL) recovered,
        count(*) FILTER (WHERE i.doc_lessons IS NULL AND i.doc_conclusions IS NULL) still_silent
      FROM clancy_dn_incidents i
      LEFT JOIN clancy_dn_baseline_pre_enrichment b ON b.id=i.id
      WHERE i.fy='{FY}'""")[0]
    d["fail"] = sql(f"""
      SELECT unnest(doc_method_failures) f, count(*) n
      FROM clancy_dn_incidents WHERE fy='{FY}' AND doc_method_failures IS NOT NULL
      GROUP BY 1 ORDER BY 2 DESC, 1""")
    d["pairs"] = sql(f"""
      SELECT i.id, i.location, b.lessons_learnt v1, i.doc_conclusions c, i.doc_lessons l
      FROM clancy_dn_incidents i
      JOIN clancy_dn_baseline_pre_enrichment b ON b.id=i.id
      WHERE i.fy='{FY}' AND (i.doc_conclusions IS NOT NULL OR i.doc_lessons IS NOT NULL)
        AND (b.lessons_learnt IS NULL OR length(btrim(b.lessons_learnt)) <= 40)
      ORDER BY length(coalesce(btrim(b.lessons_learnt),'')), i.id""")
    d["classes"] = sql(f"""
      SELECT doc_class, count(*) n FROM clancy_dn_doc_extracts x
      JOIN clancy_dn_incidents i ON i.id=x.incident_id AND i.fy='{FY}'
      WHERE x.parser_version='{PARSER}' AND doc_class NOT IN ('photo','video')
      GROUP BY 1 ORDER BY 2 DESC""")
    return d


def stat(n, label, col=CHAR, tint="#fff"):
    return (f'<div class="bigstat" style="background:{tint};border-top:5px solid {col}">'
            f'<div class="bn" style="color:{col}">{esc(n)}</div>'
            f'<div class="bl">{label}</div></div>')


def funnel(stages, total):
    rows = []
    for n, title, test in stages:
        w = max(8, round(n / max(total, 1) * 100))
        rows.append(f'<div class="fr"><div class="fnum">{n}</div>'
                    f'<div class="fbarwrap"><div class="fbar" style="width:{w}%"></div>'
                    f'<div class="ft"><b>{title}</b> <span class="fx">{test}</span></div></div></div>')
    return f'<div class="funnel">{"".join(rows)}</div>'


CSS = f"""
.rwrap{{max-width:1180px;margin:0 auto;padding:34px 22px}}
.band{{border-top:1px solid #e5e8ec}}
.band h2{{font-size:24px;letter-spacing:-.022em;margin-bottom:6px;display:flex;
 align-items:baseline;gap:10px;flex-wrap:wrap}}
.band h2 .tag{{font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
 color:#fff;border-radius:20px;padding:4px 11px}}
.band .sub{{color:#5a6572;font-size:14.5px;margin-bottom:22px;max-width:72ch;line-height:1.65}}
.statrow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin:6px 0 4px}}
.bigstat{{border-radius:15px;padding:16px 18px;box-shadow:var(--sh-1)}}
.bigstat .bn{{font-size:34px;font-weight:800;letter-spacing:-.03em;line-height:1.05}}
.bigstat .bl{{font-size:13px;color:#5a6572;margin-top:5px;line-height:1.5}}
.callout{{border-left:5px solid {GREEN};background:#fff;border-radius:0 14px 14px 0;
 padding:15px 19px;font-size:14.5px;line-height:1.7;box-shadow:var(--sh-1)}}
.split2{{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start}}
.funnel{{margin:14px 0}}
.fr{{display:grid;grid-template-columns:60px 1fr;gap:13px;align-items:center;margin-bottom:11px}}
.fnum{{font-size:27px;font-weight:800;text-align:right;color:{CHAR};font-variant-numeric:tabular-nums}}
.fbarwrap{{position:relative}}
.fbar{{height:32px;background:linear-gradient(90deg,{GREEN},#b9e94a);border-radius:9px}}
.ft{{font-size:13.5px;margin-top:5px;color:#41505f}} .ft .fx{{color:#8794a3}}
.pair{{background:#fff;border:1px solid #e5e8ec;border-radius:14px;margin:13px 0;overflow:hidden;
 box-shadow:var(--sh-1)}}
.pair .ph{{padding:10px 17px;border-bottom:1px solid #eef0f3;font-size:14px}}
.pair .pb{{display:grid;grid-template-columns:38% 1fr}}
.pair .pb>div{{padding:14px 17px}}
.pair .l{{background:{C_T};border-right:1px solid #eef0f3}}
.pair h5{{margin:0 0 7px;font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:#8794a3}}
.pair .quote{{font-size:14.5px;line-height:1.6}} .pair .quote em{{color:#9aa6b2;font-style:normal}}
.pair ul{{margin:0;padding-left:17px}} .pair li{{font-size:13.8px;line-height:1.55;margin-bottom:4px}}
.chips{{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0}}
.chip{{background:#fff;border:1px solid #e5e8ec;border-radius:20px;padding:5px 12px;font-size:12.5px}}
.asof{{color:#8794a3;font-size:12.5px;margin-top:16px}}
@media(max-width:820px){{.split2,.pair .pb{{grid-template-columns:1fr}}
 .pair .l{{border-right:0;border-bottom:1px solid #eef0f3}}}}
"""


def build():
    d = gather()
    n, r, im, c = d["n"], d["read"], d["img"], d["cmp"]
    today = datetime.datetime.now().strftime("%d %B %Y")
    when = datetime.datetime.now().strftime("%d %B %Y %H:%M")
    chars = int(r["chars"] or 0)
    b = []

    b.append(f"""
<div class="band"><div class="rwrap">
<h2><span class="tag" style="background:{CHAR}">The headline</span>
The second report: what the file already knew</h2>
<div class="sub">The first report read this year&#8217;s {n} damages from Depotnet&#8217;s own
fields, and said on its face that the supporting documents had not yet been read. They have now
been read &mdash; every one. Nothing here comes from a new investigation or a site visit. It comes
from material Clancy&#8217;s own teams and panels attached to these damages and nobody opened.</div>
<div class="statrow">
{stat(f'{r["files"]:,}', "files opened, every one accounted for", CHAR)}
{stat(f'{r["docs"]:,}', "documents read in full", GREEN, G_T)}
{stat(f'{im["n"]:,}', "photographs and scanned pages looked at, one by one", GREEN, G_T)}
{stat(f'{chars/1_000_000:.1f}m', "characters of text recovered from the file", CHAR)}
</div>
<div class="asof">Built {today} from the reading records &middot; {FYLABEL} &middot; the first
report is unchanged at <a href="/m/clancy-damage-board-report">this year&#8217;s damages: the
report</a></div>
</div></div>

<div class="band" style="background:{R_T}"><div class="rwrap">
<h2><span class="tag" style="background:{RED}">Part one</span> What the reading recovered</h2>
<div class="sub">The first report&#8217;s central finding was that the investigation field is
blank on a large share of damages. That finding stands: this is what the record itself holds.
What changes is that the blank is no longer the end of the story.</div>
<div class="statrow">
{stat(c["v1_blank"], "damages where Depotnet recorded no lesson at all", RED, "#fff")}
{stat(c["recovered"], "of those where an attached document states one", GREEN, G_T)}
{stat(c["v2_cause"], "damages where a document states a cause", GREEN, G_T)}
{stat(c["still_silent"], "damages where the file is genuinely silent too", CHAR, "#fff")}
</div>
<div class="callout" style="margin-top:20px"><b>The material was never missing.</b> On
{c["recovered"]} damages the answer to &ldquo;what did we learn&rdquo; was sitting in an attached
file the whole time, written by Clancy&#8217;s own panel. The gap in the record was never a gap in
the knowledge. It was a gap in reading.</div>
</div></div>""")

    if d["fail"]:
        b.append(f"""
<div class="band"><div class="rwrap">
<h2><span class="tag" style="background:{RED}">Part two</span> The same failures, counted</h2>
<div class="sub">One panel naming a failure is an anecdote. The same failure named across the year
is something you can act on. Each of these is counted from the documents&#8217; own words, not from
a box anyone ticked.</div>
{funnel([(x["n"], esc(x["f"]).capitalize(), "stated in an attached document") for x in d["fail"][:9]], n)}
<div class="asof">A damage can carry more than one. These are floors, not ceilings: a failure no
document mentions cannot be counted here.</div>
</div></div>""")

    if d["pairs"]:
        b.append(f"""
<div class="band" style="background:{C_T}"><div class="rwrap">
<h2><span class="tag" style="background:{CHAR}">Part three</span> The same damage, told twice</h2>
<div class="sub">Left is the record as Depotnet holds it. Right is what the attached document
says about the same damage. Neither side is rewritten, and where they disagree both are
shown.</div>""")
        for p in d["pairs"][:8]:
            v1 = (p.get("v1") or "").strip()
            b.append(f'<div class="pair"><div class="ph"><b>Damage {p["id"]}</b> '
                     f'<span style="color:#8794a3">{esc(p.get("location") or "")}</span></div>'
                     f'<div class="pb"><div class="l"><h5>Depotnet&#8217;s record</h5>'
                     f'<div class="quote">{("&ldquo;"+esc(v1)+"&rdquo;") if v1 else "<em>Left blank</em>"}</div></div>'
                     f"<div><h5>What the attached document says</h5>")
            for xs, lab in ((p.get("c") or [], "Cause"), (p.get("l") or [], "Lessons")):
                if xs:
                    b.append(f'<h5 style="margin-top:2px">{lab}</h5><ul>'
                             + "".join(f"<li>{esc(x)}</li>" for x in xs[:4]) + "</ul>")
            b.append("</div></div></div>")
        if len(d["pairs"]) > 8:
            b.append(f'<div class="asof">Eight shown of {len(d["pairs"])} damages where the '
                     f'Depotnet lesson was blank or a few words and the file held more.</div>')
        b.append("</div></div>")

    b.append(f"""
<div class="band"><div class="rwrap">
<h2><span class="tag" style="background:{GREEN};color:#25320a">Part four</span>
The evidence that was never searchable</h2>
<div class="sub">{im["n"]:,} images across {im["damages"]} damages were opened and described.
{im["text"]:,} of them carry readable text: permits, plans, locator readouts, whiteboards,
signage. That text is now transcribed, so paperwork that only ever existed as a photograph can be
searched, counted and quoted like any other record.</div>""")
    if d["classes"]:
        b.append('<div class="chips">' + "".join(
            f'<span class="chip"><b>{x["n"]}</b> {esc(x["doc_class"].replace("-"," "))}</span>'
            for x in d["classes"]) + "</div>")
    b.append("</div></div>")

    b.append(f"""
<div class="band" style="background:{C_T}"><div class="rwrap">
<h2><span class="tag" style="background:{CHAR}">The close</span> What this changes</h2>
<div class="sub">Three things follow from reading the file rather than the form.</div>
<div class="split2">
<div class="callout"><b>The investigation quality question is now answerable.</b> Where a panel
sat and wrote a proper cause, we can show it. Where the form was filled in but the panel&#8217;s
own document says something different, we can show that too. That comparison was impossible
before.</div>
<div class="callout" style="border-left-color:{RED}"><b>The record still needs to carry it.</b>
A lesson that lives only in an attachment cannot be searched, counted or briefed to the next
team. Reading recovers it once; getting it into the record is what stops it being lost
again.</div>
</div>
<div class="callout" style="margin-top:18px;border-left-color:{CHAR}"><b>And the honest limit.</b>
Reading recovers what somebody wrote down. On {c["still_silent"]} damages this year the file says
no more than the form did. Those are not reading failures. They are damages where nobody recorded
what happened, and no amount of reading will change that.</div>
<div class="asof">Every figure on this page is derived at build time from the reading records.
Nothing is typed in. The first report remains exactly as presented.</div>
</div></div>""")

    return (ui.head(f"The second report: with the documents read | Genny&#8217;s Damage Depot", CSS)
            + ui.navbar("report")
            + ui.crumbs(("Damage Depot", "/m/clancy-depotnet"),
                        (ui.nav_label("report"), "/m/clancy-damage-board-report"),
                        "With the documents read")
            + ui.hero(kicker=f"{FYLABEL} &middot; the second report",
                      title="This year&#8217;s damages, with the documents read",
                      sub="The report the first one promised.",
                      body="Every file attached to this year&#8217;s damages, opened and read: "
                           "panel reviews, statements, permits, locator data and photographs.")
            + ui.page_switch("report", FY, "v2")
            + "".join(b) + ui.foot(when) + ui.TAIL)


def publish(html):
    vocab_gate(html)
    mod = {"module_key": MK, "slug": MK,
           "title": "The second report &mdash; with the documents read",
           "section": "Customers", "subsection": "External", "area": "Clancy",
           "tier": "passcode", "passcode": "strive2030", "unlock_group": "clancy-depotnet",
           "icon": "📘", "accent": GREEN, "status": "live", "enabled": True, "sort": 16,
           "groups": ["clancy", "clancy-external"],
           "tags": ["clancy", "customer", "report", "enrichment"]}
    k = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))
    req = urllib.request.Request(
        f"{k['url']}/rest/v1/modules?on_conflict=module_key",
        data=json.dumps([mod]).encode(),
        headers={"apikey": k["service_role_key"],
                 "Authorization": f"Bearer {k['service_role_key']}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates"}, method="POST")
    _retry(req)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # No apostrophes: interpolated into a single-quoted SQL literal (see the v2 analysis publisher).
    reason = ("v2 board report quotes Depotnet fields and the attached documents verbatim - "
              "the wording rules own the verbatim-source exception")
    assert "'" not in reason
    assert "$b2$" not in html
    sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
        f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
        f"('{MK}', $b2${html}$b2$, '{now}') "
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
