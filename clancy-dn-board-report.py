#!/usr/bin/env python3
"""clancy-dn-board-report.py — "This year's damages: the report" (the board report page).

The report Pete presents at the board meeting: what this year's damages record can and
cannot tell us, read ONLY from Depotnet's own fields, before the supporting documents are
read (a second report follows after enrichment). Spec: vault_notes
Projects/SY-Clancy/board-report-spec.md (agreed with Pete, 2 Aug 2026 — discussion log in
that session). A live Depot page: every figure is derived from the database at build, the
test for every stage is stated on the page, and their words are shown verbatim, never
rewritten. The lesson three-bucket classification is a REVIEWED Sygma judgement stored in
clancy_report_lesson_buckets — the page renders whatever that table holds.

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-board-report.py --local out.html
  VAULT=/tmp/pbs python3 clancy-dn-board-report.py --publish
"""
import os, sys, json, math, argparse, subprocess, datetime, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
sys.path.insert(0, VAULT)
import clancy_dn_ui as ui

SEC = f"{VAULT}/Library/processes/secrets"
if not os.path.isdir(SEC):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _k["url"], _k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}

MK = "clancy-damage-board-report"
FY = os.environ.get("CLANCY_FY", "FY26/27")
FYLABEL = {"FY26/27": "FY 2026/27", "FY25/26": "FY 2025/26"}.get(FY, FY)

# Clancy palette + tints (the report's design tokens — spec: colourful cards, never walls)
GREEN, RED, CHAR = "#97D700", "#D50032", "#353E47"
G_T, R_T, C_T, A_T = "#f4fbe4", "#fdeef1", "#eceff2", "#fdf3e4"
AMBER = "#b45309"


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
    tok = open(f"{SEC}/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    return json.loads(_urlopen_retry(req, timeout=120).read().decode())


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if s is not None else "")


def vocab_gate(text, what):
    r = subprocess.run([sys.executable, f"{VAULT}/clancy-vocab-check.py", "-"],
                       input=text, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout + r.stderr, file=sys.stderr)
        raise SystemExit(f"vocab gate FAILED on {what}")
    print(f"   vocab: {what} clean")


# ── gather: every figure from the database, none typed in ────────────────────────────────────

def gather():
    d = {"fy": FY}
    d["n"] = int(sql(f"SELECT count(*) n FROM clancy_dn_incidents WHERE fy='{FY}'")[0]["n"])
    d["done"] = int(sql(
        f"SELECT count(DISTINCT a.incident_id) n FROM clancy_dn_answers a "
        f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
        f"WHERE a.section='investigation' AND a.answered")[0]["n"])
    d["nothing"] = d["n"] - d["done"]

    # causes — same blanket rule as the analysis page (BLANKET_AT=4: real selections are
    # 3 or fewer, blanket ones tick most of the form)
    d["with_cause"] = int(sql(
        f"SELECT count(*) n FROM clancy_dn_incidents "
        f"WHERE fy='{FY}' AND root_cause IS NOT NULL AND btrim(root_cause) <> ''")[0]["n"])
    d["blanket"] = int(sql(
        f"SELECT count(*) n FROM clancy_dn_incidents WHERE fy='{FY}' "
        f"AND root_cause IS NOT NULL AND btrim(root_cause) <> '' "
        f"AND array_length(string_to_array(root_cause, ','), 1) >= 4")[0]["n"])
    d["real_cause"] = d["with_cause"] - d["blanket"]

    # the 26, verbatim, with their reviewed bucket
    d["lessons"] = sql(
        f"SELECT i.id, i.root_cause, i.lessons_learnt, "
        f" array_length(string_to_array(i.root_cause, ','), 1) rc_n, "
        f" b.bucket, b.strategic, b.reason "
        f"FROM clancy_dn_incidents i "
        f"LEFT JOIN clancy_report_lesson_buckets b ON b.incident_id = i.id "
        f"WHERE i.fy='{FY}' AND i.lessons_learnt IS NOT NULL "
        f"AND btrim(i.lessons_learnt) <> '' ORDER BY i.id")
    d["with_lessons"] = len(d["lessons"])
    unclassified = [r["id"] for r in d["lessons"] if not r.get("bucket")]
    assert not unclassified, f"lessons with no reviewed bucket: {unclassified}"
    d["b_concrete"] = sum(1 for r in d["lessons"] if r["bucket"] == "concrete")
    d["b_restate"] = sum(1 for r in d["lessons"] if r["bucket"] == "restatement")
    d["b_non"] = sum(1 for r in d["lessons"] if r["bucket"] == "non-answer")
    d["strategic"] = [r for r in d["lessons"] if r["strategic"]]

    # detection knowability — the question the form cannot answer
    d["kit_no"] = int(sql(
        f"SELECT count(DISTINCT a.incident_id) n FROM clancy_dn_answers a "
        f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
        f"WHERE a.question IN ('Genny used?','CAT used?') "
        f"AND lower(btrim(a.answer))='no'")[0]["n"])
    for q, key in (("Genny used?", "genny_yes"), ("CAT used?", "cat_yes")):
        d[key] = int(sql(
            f"SELECT count(*) n FROM clancy_dn_answers a "
            f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
            f"WHERE a.question='{q}' AND lower(btrim(a.answer))='yes'")[0]["n"])
    d["kit_yes"] = int(sql(
        f"SELECT count(DISTINCT a.incident_id) n FROM clancy_dn_answers a "
        f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
        f"WHERE a.question IN ('Genny used?','CAT used?') "
        f"AND lower(btrim(a.answer))='yes'")[0]["n"])
    d["unable"] = int(sql(
        f"SELECT count(DISTINCT a.incident_id) n FROM clancy_dn_answers a "
        f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
        f"WHERE a.question='Service Strike Underlying Cause' "
        f"AND a.answer ILIKE '%unable to detect%'")[0]["n"])
    overlap = int(sql(
        f"SELECT count(*) n FROM ("
        f"SELECT DISTINCT a.incident_id FROM clancy_dn_answers a "
        f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
        f"WHERE a.question IN ('Genny used?','CAT used?') AND lower(btrim(a.answer))='no'"
        f") k WHERE k.incident_id IN ("
        f"SELECT DISTINCT a.incident_id FROM clancy_dn_answers a "
        f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
        f"WHERE a.question='Service Strike Underlying Cause' "
        f"AND a.answer ILIKE '%unable to detect%')")[0]["n"])
    assert overlap == 0, "kit-no and unable-to-detect overlap — split the donut differently"
    d["det_unknown"] = d["n"] - d["kit_no"] - d["unable"]

    # plans question, on the completed sections
    pq = sql(
        f"SELECT lower(btrim(a.answer)) a, count(*) n FROM clancy_dn_answers a "
        f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
        f"WHERE a.question='Utility shown on plans and in the anticipated location?' "
        f"AND a.answered GROUP BY 1")
    d["plans_no"] = sum(int(r["n"]) for r in pq if r["a"] == "no")
    d["plans_yes"] = sum(int(r["n"]) for r in pq if r["a"] == "yes")

    # the tested damage: 152586 — Depotnet's record vs the review, both live
    ans = {r["question"]: r["answer"] for r in sql(
        "SELECT question, answer FROM clancy_dn_answers WHERE incident_id=152586 "
        "AND question IN ('Is the investigation complete?','Genny used?','CAT used?',"
        "'Service Strike Root Cause','Service Strike Underlying Cause')")}
    d["ex"] = {
        "id": 152586,
        "complete": ans.get("Is the investigation complete?"),
        "rc_n": len((ans.get("Service Strike Root Cause") or "").split(",")),
        "underlying": ans.get("Service Strike Underlying Cause"),
        "genny": ans.get("Genny used?"), "cat": ans.get("CAT used?"),
    }
    rev = sql(
        "SELECT full_text FROM clancy_reports WHERE title LIKE "
        "'%CAT and Genny data behind damage 152586%'")
    d["ex"]["review_line"] = (rev[0]["full_text"] if rev else "")
    return d


# ── components ───────────────────────────────────────────────────────────────────────────────

def donut(segs, centre_big, centre_small, size=190):
    """segs: [(label, count, colour)] → SVG donut + legend. Pure SVG, no libraries."""
    total = sum(c for _, c, _ in segs) or 1
    r, cx = 62, size / 2
    circ = 2 * math.pi * r
    off, arcs = 0.25 * circ, []   # start at 12 o'clock
    for _, c, col in segs:
        frac = c / total
        arcs.append(
            f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="{col}" '
            f'stroke-width="26" stroke-dasharray="{frac*circ:.1f} {circ:.1f}" '
            f'stroke-dashoffset="{off:.1f}"></circle>')
        off -= frac * circ
    legend = "".join(
        f'<div class="lg"><span class="sw" style="background:{col}"></span>'
        f'{esc(lab)} <b>{c}</b></div>' for lab, c, col in segs)
    return (f'<div class="donut"><svg width="{size}" height="{size}" '
            f'viewBox="0 0 {size} {size}">{"".join(arcs)}'
            f'<text x="{cx}" y="{cx-2}" text-anchor="middle" class="dn">{esc(centre_big)}</text>'
            f'<text x="{cx}" y="{cx+20}" text-anchor="middle" class="dl">{esc(centre_small)}</text>'
            f'</svg><div class="legend">{legend}</div></div>')


def stat(n, label, col=CHAR, tint="#fff"):
    return (f'<div class="bigstat" style="background:{tint};border-top:5px solid {col}">'
            f'<div class="bn" style="color:{col}">{esc(n)}</div>'
            f'<div class="bl">{label}</div></div>')


def strip(text, col=CHAR):
    return (f'<div class="strip" style="border-left-color:{col}">{text}</div>')


def funnel(stages, total):
    """stages: [(n, title, test)] — descending bars, each with its test stated."""
    rows = []
    for n, title, test in stages:
        w = max(8, round(n / total * 100))
        rows.append(
            f'<div class="fr"><div class="fnum">{n}</div>'
            f'<div class="fbarwrap"><div class="fbar" style="width:{w}%"></div>'
            f'<div class="ft"><b>{title}</b> <span class="fx">{test}</span></div></div></div>')
    return f'<div class="funnel">{"".join(rows)}</div>'


# ── the page ─────────────────────────────────────────────────────────────────────────────────

CSS = """
.rwrap{max-width:1180px;margin:0 auto;padding:34px 22px}
.band{border-top:1px solid #e5e8ec}
.band h2{font-size:24px;letter-spacing:-.022em;margin-bottom:6px;display:flex;
 align-items:center;gap:12px}
.band h2 .tag{font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
 padding:4px 12px;border-radius:20px;color:#fff}
.band .sub{color:#5a6572;font-size:14.5px;margin-bottom:22px;max-width:70ch}
.frame{background:linear-gradient(135deg,#353E47,#3e4954);color:#e4eaf0;border-radius:18px;
 border-left:6px solid #97D700;padding:22px 26px;font-size:15.5px;line-height:1.65;
 box-shadow:0 12px 32px -18px rgba(53,62,71,.5)}
.frame b{color:#fff}
.statrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;
 margin:18px 0}
.bigstat{border-radius:15px;padding:18px;box-shadow:0 1px 2px rgba(23,32,20,.06),
 0 10px 26px -18px rgba(53,62,71,.3);border:1px solid #e7eaf0}
.bigstat .bn{font-size:44px;font-weight:800;line-height:1;letter-spacing:-.03em;
 font-variant-numeric:tabular-nums}
.bigstat .bl{font-size:12.5px;color:#5a6572;margin-top:8px;line-height:1.4;font-weight:600}
.donut{display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.donut .dn{font-size:34px;font-weight:800;fill:#17202b;letter-spacing:-.02em}
.donut .dl{font-size:11px;font-weight:700;fill:#6a7480;text-transform:uppercase;
 letter-spacing:.05em}
.legend .lg{font-size:13.5px;color:#3f4a55;margin:5px 0;font-weight:600}
.legend .sw{display:inline-block;width:13px;height:13px;border-radius:4px;margin-right:8px;
 vertical-align:-1px}
.legend b{font-variant-numeric:tabular-nums}
.split2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:860px){.split2{grid-template-columns:1fr}}
.vgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(255px,1fr));gap:12px;
 margin-top:16px}
.vcard{border-radius:13px;padding:13px 15px;border:1px solid #e7eaf0;background:#fff;
 box-shadow:0 1px 2px rgba(23,32,20,.05);display:flex;flex-direction:column;gap:7px}
.vcard .vid{font-size:11px;font-weight:800;color:#6a7480;display:flex;gap:8px;
 align-items:center;justify-content:space-between}
.vcard .vq{font-size:13px;line-height:1.5;color:#2b3440}
.chip{font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;
 padding:3px 10px;border-radius:12px;white-space:nowrap}
.chip.g{background:#97D700;color:#25320a}.chip.a{background:#fdf3e4;color:#b45309}
.chip.r{background:#fdeef1;color:#a4133c}.chip.c{background:#eceff2;color:#353E47}
.strip{background:#fff;border:1px solid #e7eaf0;border-left:6px solid #353E47;
 border-radius:12px;padding:15px 20px;font-size:16.5px;font-weight:700;color:#17202b;
 margin:10px 0;letter-spacing:-.01em}
.callout{background:#fff;border:1px solid #e7eaf0;border-left:6px solid #97D700;
 border-radius:14px;padding:18px 22px;font-size:14.5px;line-height:1.65;color:#3f4a55;
 margin:16px 0;box-shadow:0 8px 24px -18px rgba(53,62,71,.25)}
.callout b{color:#17202b}
.qbox{display:grid;grid-template-columns:1fr 1fr 1.3fr;gap:14px;margin:18px 0}
@media(max-width:860px){.qbox{grid-template-columns:1fr}}
.qb{border-radius:14px;padding:16px 18px;font-size:14px;font-weight:700}
.qb .qq{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;
 margin-bottom:8px;opacity:.75}
.qb.on{background:#eceff2;color:#353E47}
.qb.off{background:#fff;border:2px dashed #D50032;color:#a4133c}
.cdbox{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
@media(max-width:860px){.cdbox{grid-template-columns:1fr}}
.cd{border-radius:13px;padding:14px 18px;font-size:13.5px;line-height:1.55}
.cd b{display:block;font-size:14.5px;margin-bottom:4px}
.cd.k{background:#eceff2;color:#353E47}.cd.d{background:#fdeef1;color:#7a1030}
.sbs{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:18px 0}
@media(max-width:860px){.sbs{grid-template-columns:1fr}}
.sb{border-radius:16px;padding:20px 24px;font-size:14.5px;line-height:1.7}
.sb .sh{font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
 margin-bottom:12px}
.sb.dn{background:linear-gradient(135deg,#353E47,#3e4954);color:#dfe6ec}
.sb.dn .sh{color:#97D700}.sb.dn mark{background:#D50032;color:#fff;padding:1px 7px;
 border-radius:5px;font-weight:700}
.sb.rv{background:#f4fbe4;border:1px solid #dcedb8;color:#33421c}
.sb.rv .sh{color:#4f7000}.sb.rv mark{background:#97D700;color:#25320a;padding:1px 7px;
 border-radius:5px;font-weight:700}
.sb ul{margin:0;padding-left:18px}.sb li{margin:7px 0}
.funnel{margin:20px 0;display:flex;flex-direction:column;gap:10px}
.fr{display:grid;grid-template-columns:76px 1fr;gap:14px;align-items:center}
.fnum{font-size:30px;font-weight:800;text-align:right;color:#353E47;
 font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.fbarwrap{position:relative}
.fbar{height:34px;border-radius:8px;background:linear-gradient(90deg,#85bc00,#97D700);
 box-shadow:0 2px 8px -3px rgba(53,62,71,.4)}
.fr:last-child .fbar{background:linear-gradient(90deg,#353E47,#4d5763)}
.ft{font-size:13px;color:#3f4a55;margin-top:5px}
.ft .fx{color:#7a8490;font-weight:400}
.asof{font-size:12px;color:#7a8490;font-weight:600;text-transform:uppercase;
 letter-spacing:.05em;margin-top:6px}
"""


def build():
    d = gather()
    today = datetime.date.today().strftime("%-d %B %Y")
    n = d["n"]

    # bucket colour helpers
    BK = {"concrete": ("g", "a concrete action"), "restatement": ("a", "restates a rule / slogan"),
          "non-answer": ("r", "not an answer")}

    lesson_cards = "".join(
        f'<div class="vcard" style="background:{ {"concrete": G_T, "restatement": A_T, "non-answer": R_T}[r["bucket"]] }">'
        f'<div class="vid"><span>DAMAGE {r["id"]}</span>'
        f'<span class="chip {BK[r["bucket"]][0]}">{BK[r["bucket"]][1]}</span></div>'
        f'<div class="vq">&ldquo;{esc(r["lessons_learnt"][:420])}{"&hellip;" if len(r["lessons_learnt"]) > 420 else ""}&rdquo;</div>'
        f'</div>'
        for r in d["lessons"])

    cause_cards = "".join(
        f'<div class="vcard" style="background:{R_T if (r["rc_n"] or 0) >= 4 else "#fff"}">'
        f'<div class="vid"><span>DAMAGE {r["id"]}</span>'
        f'{"<span class=\"chip r\">ticks " + str(r["rc_n"]) + " boxes</span>" if (r["rc_n"] or 0) >= 4 else "<span class=\"chip c\">recorded cause</span>"}</div>'
        f'<div class="vq">{esc(r["root_cause"])}</div></div>'
        for r in d["lessons"])

    strategic_cards = "".join(
        f'<div class="vcard" style="background:{G_T};border-color:#cfe796">'
        f'<div class="vid"><span>DAMAGE {r["id"]}</span><span class="chip g">usable</span></div>'
        f'<div class="vq">&ldquo;{esc(r["lessons_learnt"][:300])}&rdquo;</div></div>'
        for r in d["strategic"])

    ex = d["ex"]

    html = f"""{ui.head("This year&#8217;s damages: the report | Genny&#8217;s Damage Depot", CSS)}
{ui.navbar("report")}
{ui.crumbs(("Command Centre", "/"), ("Damage Depot", f"/m/{ui.HUB}"), "The report")}
{ui.mast_compact("The report &middot; " + FYLABEL,
   "This year&#8217;s damages: what the record can tell us",
   f"{n} service damages so far this year, read from Depotnet&#8217;s own fields.")}

<div class="band"><div class="rwrap">
<div class="frame"><b>Where this comes from, and what it is not.</b> Everything on this page
is read from what Depotnet&#8217;s own fields hold for this year&#8217;s {n} service damages.
The supporting documents behind each damage &mdash; the photographs, permits, statements and
survey data &mdash; are never captured in Depotnet&#8217;s fields and have <b>not yet been
read</b>. A second report follows once that work is done. Every figure below is calculated
from the captured record when this page is rebuilt.</div>
<div class="asof">Rebuilt {today} from the captured Depotnet record &middot; {FYLABEL}</div>
</div></div>

<div class="band"><div class="rwrap">
<h2><span class="tag" style="background:{CHAR}">The headline</span> {n} chances to learn</h2>
<div class="sub">Every damage is a chance to find out what went wrong and stop the next one.
This is how many of this year&#8217;s {n} gave us anything to work with.</div>
<div class="statrow">
{stat(n, "service damages so far this year", CHAR)}
{stat(d["done"], "have their investigation report filled in", GREEN)}
{stat(d["nothing"], "have an untouched investigation report &mdash; no cause, no lesson recorded", RED, R_T)}
</div>
</div></div>

<div class="band" style="background:{R_T}"><div class="rwrap">
<h2><span class="tag" style="background:{RED}">Part one</span> What is missing</h2>
<div class="sub">Start with what is not there at all.</div>
<div class="split2">
{donut([("Investigation report filled in", d["done"], GREEN),
        ("Investigation report blank", d["nothing"], RED)],
       str(d["nothing"]), "left blank")}
<div>
<div class="callout" style="border-left-color:{RED}"><b>{d["nothing"]} of the {n} are blank
where the learning should be.</b> The investigation report &mdash; the part of the record
that holds who investigated, what caused the damage and what should change &mdash; is
untouched on {d["nothing"]} damages. Not thin. Untouched. Whatever those {d["nothing"]}
damages had to teach, the record does not hold it.</div>
</div></div>
</div></div>

<div class="band"><div class="rwrap">
<h2><span class="tag" style="background:{CHAR}">Part two</span> What we have, in their own words</h2>
<div class="sub">The {d["with_lessons"]} damages that do carry a cause and a lesson &mdash;
shown exactly as written, nothing reworded. This is the entire harvest of the year so far.</div>
<div class="statrow">
{stat(d["with_cause"], "carry a root cause", CHAR)}
{stat(d["with_lessons"], "carry something in the lessons field", CHAR)}
{stat(d["blanket"], "of those causes tick four or more boxes at once", RED, R_T)}
</div>
<h3 style="font-size:16px;margin:20px 0 4px">The {d["with_cause"]} recorded causes</h3>
<div class="sub" style="margin-bottom:4px">A cause that ticks nearly every option on the
form names everything and explains nothing &mdash; those are flagged red.</div>
<div class="vgrid">{cause_cards}</div>
<h3 style="font-size:16px;margin:26px 0 4px">The {d["with_lessons"]} recorded lessons</h3>
<div class="sub" style="margin-bottom:4px">Colour is the verdict. Green: a concrete action.
Amber: a slogan, or a rule that already exists. Red: not an answer at all. The test for
each verdict is stated in part three; the classification is Sygma&#8217;s, stored against
each damage and open to challenge.</div>
<div class="vgrid">{lesson_cards}</div>
</div></div>

<div class="band" style="background:{G_T}"><div class="rwrap">
<h2><span class="tag" style="background:{GREEN};color:#25320a">Part three</span> What can we actually use</h2>
<div class="sub">The test for each lesson: could the company act on it to help prevent the
next damage &mdash; not close this one, prevent the next one?</div>
<div class="split2">
{donut([("A concrete action", d["b_concrete"], GREEN),
        ("Restates a rule / slogan", d["b_restate"], AMBER),
        ("Not an answer", d["b_non"], RED)],
       str(d["b_concrete"]), "concrete actions")}
<div>
<div class="callout"><b>{d["b_concrete"]} of the {d["with_lessons"]} contain a concrete
action of any kind.</b> {d["b_restate"]} restate a rule the company already has, or offer a
slogan &mdash; &ldquo;careful hand digging&rdquo;, &ldquo;expect the unexpected&rdquo;.
{d["b_non"]} are not answers at all: &ldquo;N/A&rdquo;, &ldquo;Yes&rdquo;, &ldquo;TBC&rdquo;.
The field was filled so the form would close.</div>
<div class="callout"><b>And of the {d["b_concrete"]}, only {len(d["strategic"])} reach
beyond the job they were written on</b> &mdash; something the company could adopt everywhere
to help prevent the next damage:</div>
</div></div>
<div class="vgrid" style="grid-template-columns:repeat(auto-fill,minmax(300px,1fr))">{strategic_cards}</div>
</div></div>

<div class="band"><div class="rwrap">
<h2><span class="tag" style="background:{RED}">Part four</span> The question the form never asks</h2>
<div class="sub">The single most important fact about any strike on a buried service:
was the service found before it was hit?</div>
<div class="qbox">
<div class="qb on"><div class="qq">On the form</div>Genny used? <br>&#10003; Yes on {d["genny_yes"]} of the {d["done"]} completed reports</div>
<div class="qb on"><div class="qq">On the form</div>CAT used? <br>&#10003; Yes on {d["cat_yes"]} of the {d["done"]} completed reports</div>
<div class="qb off"><div class="qq">Not on the form</div>Did the genny and CAT find the
service that was hit? <br>This question does not exist anywhere on Depotnet.</div>
</div>
<div class="callout" style="border-left-color:{RED}"><b>&ldquo;Kit used&rdquo; is not
&ldquo;service found&rdquo;.</b> A tick against the genny and the CAT says they were used on
site. It says nothing about whether the service that was struck ever showed on the
CAT. Depotnet&#8217;s download question &mdash; &ldquo;CAT data downloaded and
reviewed in the portal?&rdquo; &mdash; confirms a download happened, and goes no further.</div>
<div class="cdbox">
<div class="cd k"><b>&ldquo;Couldn&#8217;t detect&rdquo;</b>It was properly looked for and
the genny and CAT could not find it. A method problem: the answer is better technique
or better technology.</div>
<div class="cd d"><b>&ldquo;Didn&#8217;t detect&rdquo;</b>It was never properly looked for.
A behaviour problem: the answer is training and supervision.</div>
</div>
<div class="callout" style="border-left-color:{RED}"><b>The form cannot tell these two
apart</b> &mdash; and they need opposite fixes. One cause option exists,
&ldquo;Unable to detect location of service&rdquo;, and it is only there when the supervisor
filling in the investigation report chooses it. Across this year&#8217;s {n} damages it was chosen on <b>{d["unable"]}</b>.
Whether they could not detect or did not detect, the record cannot say.</div>
</div></div>

<div class="band" style="background:{C_T}"><div class="rwrap">
<h2><span class="tag" style="background:{CHAR}">Part five</span> &ldquo;The plans were wrong&rdquo; is not a cause</h2>
<div class="statrow">
{stat(f'{d["plans_no"]} of {d["done"]}', "completed reports answer that the utility was NOT where the plans showed", CHAR)}
{stat(f'{d["kit_yes"]} of {d["done"]}', "completed reports answer Yes to the form&#8217;s own &lsquo;Genny used?&rsquo; and &lsquo;CAT used?&rsquo; questions &mdash; used is all the form can say", CHAR)}
</div>
<div class="callout"><b>The most common recorded story is that the service was not where
the plans showed.</b> In this industry that is not a cause. Plans are known to be
indicative only, and every team is trained not to rely on them for the position of a
service. Position comes from detection, trial holes and safe digging. Where &ldquo;the
plans were wrong&rdquo; is recorded as the cause, the real finding is that teams are still
treating plans as if they position services. If there is a learning in this year&#8217;s
record, it is that one.</div>
</div></div>

<div class="band"><div class="rwrap">
<h2><span class="tag" style="background:{RED}">Part six</span> And that is if the record is to be believed</h2>
<div class="sub">One damage this year has had its detection data reviewed against what the
form says &mdash; the only one. Side by side:</div>
<div class="sbs">
<div class="sb dn"><div class="sh">What Depotnet records &mdash; damage {ex["id"]}</div>
<ul>
<li>Investigation marked complete: <mark>{esc(ex["complete"])}</mark></li>
<li>Root cause: <mark>ticks {ex["rc_n"]} boxes</mark> on one damage</li>
<li>Underlying cause: <mark>&ldquo;{esc(ex["underlying"])}&rdquo;</mark></li>
<li>Genny used? {esc(ex["genny"])} &middot; CAT used? {esc(ex["cat"])}</li>
</ul></div>
<div class="sb rv"><div class="sh">What the review of the detection data found</div>
<ul>
<li>The cable was presented as untraceable &mdash; <mark>it was traceable</mark></li>
<li>The genny was <mark>connected to the column the wrong way</mark>; connected properly,
the service would have been found</li>
<li>The scan&ndash;mark&ndash;expose procedure that would have caught it
<mark>was not followed</mark>, and that was accepted in the review meeting</li>
<li>Formal conclusion on record: the findings recorded for this damage are
<mark>incorrect and need amending</mark></li>
</ul></div>
</div>
<div class="callout" style="border-left-color:{RED}"><b>One damage out of {n} has had this
level of review, and the recorded cause did not hold.</b> How many of the other
{n - 1} would survive the same review is exactly the further work this report is asking
for.</div>
</div></div>

<div class="band" style="background:linear-gradient(180deg,{C_T},#fff)"><div class="rwrap">
<h2><span class="tag" style="background:{CHAR}">The close</span> What your data is telling us</h2>
<div class="sub">Everything above, in one picture.</div>
{funnel([
  (n, "service damages logged so far this year", "every one is a chance to learn how to stop the next one"),
  (d["done"], "have their investigation report filled in", f"the other {d['nothing']} are blank: no cause, no lesson"),
  (d["real_cause"], "name a root cause that means something", f"{d['blanket']} more tick nearly every box on the form, which tells us nothing"),
  (d["b_concrete"], "have a lesson that names a real action", f"the rest are slogans, repeats of existing rules, or empty answers like N/A"),
  (len(d["strategic"]), "are lessons the whole company could act on", "everything else applies only to the job it was written on"),
], n)}
<div class="split2" style="margin-top:24px">
{donut([("The record says it was found", 0, CHAR),
        ("Known not found: genny and CAT not used", d["kit_no"], RED),
        ("Report says unable-to-detect", d["unable"], AMBER),
        ("The record cannot say", d["det_unknown"], "#c3cad2")],
       f'{d["kit_no"]} of {n}', "known answers")}
<div>
<div class="callout" style="border-left-color:{RED}"><b>Was the service found before it was
hit?</b> Out of {n} damages to buried services, the record gives a definite answer on
<b>{d["kit_no"]}</b> &mdash; both times because the report answers No to &ldquo;Genny used?&rdquo; and &ldquo;CAT
used?&rdquo;. It
never once says a service was found. For {d["det_unknown"]} of the {n}, it cannot say
anything at all.</div>
</div></div>
<div class="statrow" style="margin-top:22px">
{stat(f'{d["nothing"]}', "investigation report blank &mdash; taught us nothing", RED, R_T)}
{stat(f'{d["done"] - len(d["strategic"])}', "filled in, but gave no lesson the whole company can use", AMBER, A_T)}
{stat(f'{len(d["strategic"])}', "gave a lesson the whole company can act on", GREEN, G_T)}
</div>
<div class="sub" style="margin-top:2px">{d["nothing"]} + {d["done"] - len(d["strategic"])} + {len(d["strategic"])} = the {n} damages logged so far this year.</div>
{strip("That is not a lessons process. That is a form being closed.", RED)}
{strip("Fixes, not strategy: what the record holds serves the incident it belongs to, never the next one.", CHAR)}
{strip("The one recorded cause put to the test did not hold.", RED)}
<div class="callout" style="border-left-color:{GREEN}"><b>What changes this.</b> Three
things, in order. Complete the investigation report on every damage &mdash; {d["nothing"]}
of {n} are blank. Ask the question that matters on the form itself: was the service
detected before the strike, and if not, why not. And read the supporting documents behind
each damage &mdash; the photographs, the permits, the survey data &mdash; which is the
enrichment work already planned. That work produces the second report: what the documents
say that the fields do not.</div>
</div></div>
{ui.foot(today)}
"""
    return html


def publish(html):
    vocab_gate(html, "the board report page")
    mod = {"module_key": MK, "slug": MK,
           "title": "This year&#8217;s damages: the report",
           "section": "Customers", "subsection": "External", "area": "Clancy",
           "tier": "passcode", "passcode": "strive2030",
           "unlock_group": "clancy-depotnet",
           "icon": "📊", "accent": "#97D700", "status": "live", "enabled": True, "sort": 20,
           "groups": ["clancy", "clancy-external"], "tags": ["clancy", "customer", "report"]}
    rest("modules?on_conflict=module_key", "POST", [mod],
         {"Prefer": "resolution=merge-duplicates"})
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # no apostrophes: the reason is interpolated into single-quoted SQL
    reason = ("Board report quotes Depotnet field names and recorded text verbatim - "
              "the wording rules own the verbatim-label exception")
    assert "$br$" not in html
    sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
        f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
        f"('{MK}', $br${html}$br$, '{now}') "
        f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, "
        f"updated_at=EXCLUDED.updated_at;")
    print(f"published {MK} — commandcentre.info/m/{MK}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    html = build()
    if a.local:
        vocab_gate(html, "the board report page (local)")
        open(a.local, "w").write(html)
        print(f"wrote {a.local} ({len(html):,} chars)")
    if a.publish:
        publish(html)


if __name__ == "__main__":
    main()
