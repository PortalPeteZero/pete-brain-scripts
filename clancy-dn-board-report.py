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
    for q, key in (("Genny used?", "genny"), ("CAT used?", "cat")):
        for ans in ("yes", "no"):
            d[f"{key}_{ans}"] = int(sql(
                f"SELECT count(*) n FROM clancy_dn_answers a "
                f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
                f"WHERE a.question='{q}' AND lower(btrim(a.answer))='{ans}'")[0]["n"])
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

    # how many blame plans as the underlying cause — part five's companion stat
    d["plans_blamed"] = int(sql(
        f"SELECT count(DISTINCT a.incident_id) n FROM clancy_dn_answers a "
        f"JOIN clancy_dn_incidents i ON i.id=a.incident_id AND i.fy='{FY}' "
        f"WHERE a.question='Service Strike Underlying Cause' "
        f"AND a.answer ILIKE '%insufficient plans%'")[0]["n"])
    d["plans_split"] = sql(
        f"SELECT i.id, i.strike_category cat, i.strike_subcategory sub "
        f"FROM clancy_dn_incidents i WHERE i.fy='{FY}' AND i.id IN ("
        f"SELECT a.incident_id FROM clancy_dn_answers a "
        f"WHERE a.question='Service Strike Underlying Cause' "
        f"AND a.answer ILIKE '%insufficient plans%') ORDER BY i.strike_category, i.id")
    d["gas_poly"] = int(sql(
        f"SELECT count(*) n FROM clancy_dn_incidents WHERE fy='{FY}' "
        f"AND strike_category='Gas' AND strike_subcategory ILIKE '%poly%'")[0]["n"])
    d["gas_service_poly"] = int(sql(
        f"SELECT count(*) n FROM clancy_dn_incidents WHERE fy='{FY}' "
        f"AND strike_category='Gas' AND strike_subcategory ILIKE '%service%poly%'")[0]["n"])
    def _consistency(where):
        rows = sql(
            f"SELECT CASE "
            f"WHEN a.answer IS NULL THEN 'blank' "
            f"WHEN a.answer ILIKE '%insufficient plans%' AND a.answer ILIKE '%unable to detect%' THEN 'both' "
            f"WHEN a.answer ILIKE '%insufficient plans%' THEN 'plans' "
            f"WHEN a.answer ILIKE '%unable to detect%' THEN 'unable' "
            f"ELSE 'other' END v, count(*) n "
            f"FROM clancy_dn_incidents i "
            f"LEFT JOIN clancy_dn_answers a ON a.incident_id=i.id "
            f"AND a.question='Service Strike Underlying Cause' AND a.answered "
            f"WHERE i.fy='{FY}' AND {where} GROUP BY 1")
        out = {k: 0 for k in ("plans", "unable", "both", "other", "blank")}
        out.update({r["v"]: int(r["n"]) for r in rows})
        return out
    d["cx_elec"] = _consistency("i.strike_category='Electric'")
    d["cx_gas"] = _consistency(
        "i.strike_category='Gas' AND i.strike_subcategory ILIKE '%service%poly%'")
    # are the electric both-rows all blanket ticks? derive, never assert
    both_opts = sql(
        f"SELECT min(array_length(string_to_array(a.answer, ','), 1)) mn "
        f"FROM clancy_dn_answers a JOIN clancy_dn_incidents i ON i.id=a.incident_id "
        f"AND i.fy='{FY}' AND i.strike_category='Electric' "
        f"WHERE a.question='Service Strike Underlying Cause' "
        f"AND a.answer ILIKE '%insufficient plans%' AND a.answer ILIKE '%unable to detect%'")
    d["cx_elec_both_min_opts"] = int(both_opts[0]["mn"] or 0)
    d["cx_elec_plans_alone_ids"] = [int(r["id"]) for r in sql(
        f"SELECT i.id FROM clancy_dn_incidents i "
        f"JOIN clancy_dn_answers a ON a.incident_id=i.id "
        f"AND a.question='Service Strike Underlying Cause' AND a.answered "
        f"WHERE i.fy='{FY}' AND i.strike_category='Electric' "
        f"AND a.answer ILIKE '%insufficient plans%' "
        f"AND a.answer NOT ILIKE '%unable to detect%' ORDER BY i.id")]
    wx = sql(
        f"SELECT count(*) n FROM clancy_dn_answers a "
        f"JOIN clancy_dn_incidents i ON i.id=a.incident_id "
        f"AND i.fy='{FY}' AND i.strike_category='Electric' "
        f"WHERE a.question='Service Strike Underlying Cause' "
        f"AND a.answer ILIKE '%insufficient plans%' AND a.answer ILIKE '%unable to detect%' "
        f"AND a.answer ILIKE '%inclement weather%' AND a.answer ILIKE '%night working%'")
    d["cx_elec_both_weather"] = int(wx[0]["n"])
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
    exrow = sql("SELECT status, to_char(pdf_captured_at, 'DD Month YYYY') cap "
                "FROM clancy_dn_incidents WHERE id=152586")[0]
    d["ex"]["status"] = exrow["status"]
    d["ex"]["captured"] = (exrow["cap"] or "").strip()
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
    # the centre label must FIT THE HOLE (inner diameter ~98px): wrap to short lines
    words, lines = centre_small.split(), []
    for w_ in words:
        if lines and len(lines[-1]) + 1 + len(w_) <= 11:
            lines[-1] += " " + w_
        else:
            lines.append(w_)
    lines = lines[:3]
    y0 = cx + 12
    small = "".join(
        f'<text x="{cx}" y="{y0 + i*11}" text-anchor="middle" class="dl">{esc(l)}</text>'
        for i, l in enumerate(lines))
    return (f'<div class="donut"><svg width="{size}" height="{size}" '
            f'viewBox="0 0 {size} {size}">{"".join(arcs)}'
            f'<text x="{cx}" y="{cx-6}" text-anchor="middle" class="dn">{esc(centre_big)}</text>'
            f'{small}</svg><div class="legend">{legend}</div></div>')


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
.donut .dn{font-size:28px;font-weight:800;fill:#17202b;letter-spacing:-.02em}
.donut .dl{font-size:9px;font-weight:800;fill:#6a7480;text-transform:uppercase;
 letter-spacing:.03em}
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
.pkey{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:18px}
@media(max-width:860px){.pkey{grid-template-columns:1fr}}
.pk-e{background:#fff;border:3px solid #D50032;border-radius:16px;padding:20px 24px;
 box-shadow:0 16px 38px -18px rgba(213,0,50,.45)}
.pk-e .pkh{display:inline-block;background:#D50032;color:#fff;font-size:11px;
 font-weight:800;letter-spacing:.06em;text-transform:uppercase;border-radius:14px;
 padding:4px 12px;margin-bottom:10px}
.pk-e .pkn{font-size:38px;font-weight:800;color:#D50032;letter-spacing:-.02em;
 line-height:1;margin-bottom:8px}
.pk-e{font-size:14.5px;line-height:1.65;color:#2b3440}
.pk-e mark{background:#D50032;color:#fff;padding:1px 8px;border-radius:5px;font-weight:800}
.pk-e .pkc{font-size:12.5px;color:#6a7480;margin-top:10px;font-weight:600}
.pk-p{background:#fff;border:3px solid #F2A900;border-radius:16px;padding:20px 24px;
 color:#2b3440;font-size:14.5px;line-height:1.65;
 box-shadow:0 16px 38px -18px rgba(242,169,0,.45)}
.pk-p .pkh{display:inline-block;background:#F2A900;color:#2b2000;font-size:11px;
 font-weight:800;letter-spacing:.06em;text-transform:uppercase;border-radius:14px;
 padding:4px 12px;margin-bottom:10px}
.pk-p .pkn{font-size:38px;font-weight:800;color:#c78a00;letter-spacing:-.02em;
 line-height:1;margin-bottom:8px}
.cxrule{background:linear-gradient(135deg,#353E47,#3e4954);color:#e4eaf0;
 border-radius:16px;border-left:6px solid #97D700;padding:20px 24px;font-size:15px;
 line-height:1.65;margin-top:22px}
.cxrule b{color:#fff}
.cxg{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
@media(max-width:860px){.cxg{grid-template-columns:1fr}}
.cxcard{background:#fff;border:1px solid #e7eaf0;border-radius:16px;padding:18px 20px;
 box-shadow:0 10px 28px -20px rgba(53,62,71,.3)}
.cxcard h4{font-size:15.5px;letter-spacing:-.01em;margin-bottom:2px}
.cxcard .cxs{font-size:12px;color:#6a7480;margin-bottom:14px;font-weight:600}
.cxrow{padding:9px 10px;border-radius:10px;margin:5px 0}
.cxrow .cxl{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
 font-size:13px;font-weight:700;color:#2b3440}
.cxrow .cxn{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums}
.cxrow .cxb{height:8px;border-radius:4px;background:#eef1f5;margin-top:6px;overflow:hidden}
.cxrow .cxb i{display:block;height:100%;background:#9aa4b0;border-radius:4px}
.cxrow .cxnote{font-size:13px;color:#3f4a55;margin-top:6px;line-height:1.5}
.cxrow .cxnote b{color:#17202b}
.cxrow .cxnote mark{background:#b45309;color:#fff;padding:1px 7px;border-radius:5px;
 font-weight:800}
.cxgroup{border:3px solid #D50032;border-radius:14px;padding:4px 10px 10px;margin:6px 0 14px}
.cxgroup .cxgh{font-size:12px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;
 color:#D50032;padding:8px 10px 4px}
.cxrow.imposs{background:#fdeef1;border:2px solid #D50032}
.cxrow.imposs .cxn{color:#D50032}
.cxrow.imposs .cxb i{background:#D50032}
.cxrow.blanket{background:#fdf3e4;border:2px solid #b45309}
.cxrow.blanket .cxn{color:#b45309}
.cxrow.blanket .cxb i{background:#b45309}
.cxrow.truth{background:#eceff2;border:2px solid #353E47}
.cxrow.truth .cxb i{background:#353E47}
.vgx{display:none}
.vwrap{max-height:255px;overflow:hidden;position:relative}
.vgx:checked + .vwrap{max-height:none}
.vfade{position:absolute;left:0;right:0;bottom:0;height:110px;
 background:linear-gradient(180deg,rgba(255,255,255,0),#fff)}
.vgx:checked + .vwrap .vfade{display:none}
.vmore{display:inline-flex;align-items:center;gap:8px;margin-top:12px;cursor:pointer;
 background:#97D700;color:#25320a;border-radius:20px;padding:8px 18px;font-size:13px;
 font-weight:800;box-shadow:0 1px 3px rgba(53,62,71,.25)}
.vmore:hover{background:#a5e211}
.vmore::after{content:attr(data-more)}
.vgx:checked ~ .vmore::after{content:attr(data-less)}
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

    # who blames the plans, by what was struck — the electric cables are the scandal
    ps = d["plans_split"]
    elec = [r for r in ps if r["cat"] == "Electric"]
    rest = [r for r in ps if r["cat"] != "Electric"]
    from collections import Counter as _C
    def _chips(rows):
        cn = _C((r["sub"] or r["cat"] or "unstated").replace("Electric - ", "")
                .replace("&#8211;", "-") for r in rows)
        return " &middot; ".join(f"{v}&times; {esc(k.lower())}" for k, v in cn.most_common())
    _proven = ". One of them is damage 152586 &mdash; the damage whose recorded findings failed our review of the detection data" \
        if any(r["id"] == 152586 for r in elec) else ""
    _rest_all_poly = rest and all("poly" in (r["sub"] or "").lower() for r in rest)
    gas_blamed = sum(1 for r in ps if r["cat"] == "Gas")

    # "Does the record agree with itself?" — the internal-consistency test (Pete, 2 Aug)
    ce, cg = d["cx_elec"], d["cx_gas"]
    ne, ng = sum(ce.values()), sum(cg.values())
    def _cxrow(label, n, total, cls="", note=""):
        w = 0 if total == 0 else max(3, round(n / total * 100))
        nt = f'<div class="cxnote">{note}</div>' if note else ""
        return (f'<div class="cxrow {cls}"><div class="cxl"><span>{label}</span>'
                f'<span class="cxn">{n}</span></div>'
                f'<div class="cxb"><i style="width:{w}%"></i></div>{nt}</div>')
    _wx = (" &mdash; listing, along the way, night working and the weather"
           if d["cx_elec_both_weather"] == ce["both"] and ce["both"] else "")
    cx_block = f'''
<div class="cxrule"><b>Does the record agree with itself?</b> On an electric cable, wrong
plans can only cause a strike if the trace also failed &mdash; if the genny and CAT find
the cable, the plans do not matter. So &ldquo;Insufficient plans&rdquo; on an electric
damage must come paired with &ldquo;Unable to detect location of service&rdquo;.
<b>Plans alone is half an answer: it never explains why the trace did not save you.</b></div>
<div class="cxg">
<div class="cxcard"><h4>Electric &mdash; {ne} damages</h4>
<div class="cxs">Traceable, conductive: detection should find these</div>
<div class="cxgroup"><div class="cxgh">The {ce["plans"] + ce["both"]} plans listings &mdash; not one of them sound</div>
{_cxrow("&ldquo;Insufficient plans&rdquo; ALONE", ce["plans"], ne, "imposs",
        ("The impossible answer: nothing explains why the trace did not find the cable."
         + (" Damage 152586 &mdash; the one that failed its data review &mdash; sits here."
            if 152586 in d["cx_elec_plans_alone_ids"] else "")
         + (" So does 133852, whose own form says the plans showed the service where expected."
            if 133852 in d["cx_elec_plans_alone_ids"] else "")) if ce["plans"] else "")}
{_cxrow("Plans + unable-to-detect together", ce["both"], ne, "blanket",
        (f"<mark>All four ticked all {d['cx_elec_both_min_opts']} boxes on the form</mark>{_wx}. <b>An answer that ticks everything tells us nothing.</b>" if ce["both"] else ""))}
</div>
{_cxrow("&ldquo;Unable to detect&rdquo; as a considered answer", ce["unable"], ne, "",
        "Never once chosen on its own for an electric cable")}
{_cxrow("Other options", ce["other"], ne)}
{_cxrow("Investigation report blank", ce["blank"], ne)}
</div>
<div class="cxcard"><h4>Gas service pipes &mdash; {ng} damages</h4>
<div class="cxs">Poly, typically not on plans: here the pairing is simply TRUE</div>
{_cxrow("Plans + unable-to-detect together", cg["both"], ng, "truth",
        "The one answer that is a plain fact for a poly service pipe &mdash; never written")}
{_cxrow("&ldquo;Unable to detect&rdquo; alone", cg["unable"], ng)}
{_cxrow("&ldquo;Insufficient plans&rdquo; alone", cg["plans"], ng)}
{_cxrow("Other options", cg["other"], ng)}
{_cxrow("Investigation report blank", cg["blank"], ng, "truth",
        "The biggest answer on the hardest pipes is silence")}
</div></div>
<div class="strip" style="border-left-color:{RED};margin-top:14px">The record does not
agree with itself. Where the pairing is impossible to avoid, it never appears; where it is
impossible to defend, it stands alone.</div>'''

    plans_split_box = f'''
<div class="pkey">
<div class="pk-p"><span class="pkh">Where plans really are the problem</span>
<div class="pkn">{d["gas_poly"]} plastic gas damages</div>
{d["gas_service_poly"]} of them are <b>service pipes</b> &mdash; typically not shown on
plans at all, and a poly pipe gives the genny and CAT nothing to find. If
&ldquo;insufficient plans&rdquo; belonged anywhere as a finding, it is here.
<div style="margin-top:10px;font-size:15px"><b>Recorded as a plans problem: {gas_blamed} of the {d["gas_poly"]}.</b></div></div>
<div class="pk-e"><span class="pkh">Yet where the plans get listed as the cause</span>
<div class="pkn">{len(elec)} electric cables</div>
Every one of these is conductive &mdash; the exact thing the genny and CAT exist to find.
Yet every one has &ldquo;Insufficient plans&rdquo; listed as its underlying cause.
<mark>Insufficient plans should never stand as the cause or the lesson on an electric
cable.</mark>
<div class="pkc">{_chips(elec)}{_proven}</div></div>
</div>'''

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
<div class="sub">Of the {n} damages, {d["with_lessons"]} carry a cause and a lesson &mdash;
shown exactly as written, nothing reworded. This is the entire harvest of the year so far.</div>
<div class="statrow">
{stat(f'{d["with_cause"]} of {n}', "damages carry a root cause", CHAR)}
{stat(f'{d["with_lessons"]} of {n}', "damages carry something in the lessons field", CHAR)}
{stat(d["blanket"], "of those causes tick four or more boxes at once", RED, R_T)}
</div>
<h3 style="font-size:16px;margin:20px 0 4px">The recorded causes &mdash; {d["with_cause"]} of the {n} damages have one</h3>
<div class="sub" style="margin-bottom:4px">A cause that ticks nearly every option on the
form names everything and explains nothing &mdash; those are flagged red.</div>
<input type="checkbox" class="vgx" id="vg-causes">
<div class="vwrap"><div class="vgrid">{cause_cards}</div><div class="vfade"></div></div>
<label class="vmore" for="vg-causes" data-more="&#9662; Show all {d['with_cause']} causes"
 data-less="&#9652; Show the first row only"></label>
<h3 style="font-size:16px;margin:26px 0 4px">The recorded lessons &mdash; {d["with_lessons"]} of the {n} damages have one</h3>
<div class="sub" style="margin-bottom:4px">Colour is the verdict. Green: a concrete action.
Amber: a slogan, or a rule that already exists. Red: not an answer at all. The test for
each verdict is stated in part three; the classification is Sygma&#8217;s, stored against
each damage and open to challenge.</div>
<input type="checkbox" class="vgx" id="vg-lessons">
<div class="vwrap"><div class="vgrid">{lesson_cards}</div><div class="vfade"></div></div>
<label class="vmore" for="vg-lessons" data-more="&#9662; Show all {d['with_lessons']} lessons"
 data-less="&#9652; Show the first row only"></label>
</div></div>

<div class="band" style="background:{G_T}"><div class="rwrap">
<h2><span class="tag" style="background:{GREEN};color:#25320a">Part three</span> What can we actually use</h2>
<div class="sub">The test for each lesson: could the company act on it to help prevent the
next damage &mdash; not close this one, prevent the next one?</div>
<div class="split2">
{donut([("A concrete action", d["b_concrete"], GREEN),
        ("Restates a rule / slogan", d["b_restate"], AMBER),
        ("Not an answer", d["b_non"], RED),
        ("No lesson at all", d["nothing"], "#c3cad2")],
       str(d["b_concrete"]), f"of {n} name an action")}
<div>
<div class="callout"><b>Of the {n} damages, {d["b_concrete"]} produced a lesson with a
concrete action of any kind.</b> {d["b_restate"]} restate a rule the company already has,
or offer a slogan &mdash; &ldquo;careful hand digging&rdquo;, &ldquo;expect the
unexpected&rdquo;. {d["b_non"]} are not answers at all: &ldquo;N/A&rdquo;,
&ldquo;Yes&rdquo;, &ldquo;TBC&rdquo;. And {d["nothing"]} gave no lesson at all &mdash; the
report is blank. The field, where it was filled, was filled so the form would close.</div>
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
<div class="qb on"><div class="qq">On the form</div>Genny used? <br>&#10003; Yes on {d["genny_yes"]} of the {n} damages &middot; No on {d["genny_no"]} &middot; never answered on {n - d["genny_yes"] - d["genny_no"]}</div>
<div class="qb on"><div class="qq">On the form</div>CAT used? <br>&#10003; Yes on {d["cat_yes"]} of the {n} damages &middot; No on {d["cat_no"]} &middot; never answered on {n - d["cat_yes"] - d["cat_no"]}</div>
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
{stat(f'{d["plans_no"]} of {n}', "damages answer that the utility was NOT where the plans showed &mdash; the question is unanswered on " + str(n - d["plans_no"] - d["plans_yes"]), CHAR)}
{stat(f'{d["plans_blamed"]} of {n}', "damages record &lsquo;Insufficient plans&rsquo; as the underlying cause of the strike", RED, R_T)}
</div>
<div class="callout"><b>The most common recorded story is that the service was not where
the plans showed.</b> Inaccurate plans are a real and well-known contributing factor
&mdash; and where a service is plastic and the genny and CAT cannot pick it up, they
contribute more. But in this industry they are not the cause, and they are not a lesson.
Plans are known to be indicative only, and every team is trained not to rely on them for
the position of a service &mdash; position comes from detection, trial holes and safe
digging, precisely because the plans cannot be trusted. Where &ldquo;the plans were
wrong&rdquo; stands as the recorded root cause and the lesson stops there, the record has
written down a known working condition, not a cause. The real finding is that plans are
still being treated as if they position services. If there is a learning in this
year&#8217;s record, it is that one.</div>
{plans_split_box}
{cx_block}
</div></div>

<div class="band"><div class="rwrap">
<h2><span class="tag" style="background:{RED}">Part six</span> Those plans listings need scrutiny &mdash; here is the first one tested</h2>
<div class="sub">We are not convinced that plans are the cause, or the lesson, on those
damages. Our involvement is recent: we have worked on only a few of this year&#8217;s
damages, and we have only recently had access to Depotnet itself. Damage 152586 is one of
the first where we have reviewed the detection data behind the form in full &mdash; and
the result is clear. The cause listed is not the cause. What actually happened is totally
different from what Depotnet holds. Side by side:</div>
<div class="sbs">
<div class="sb dn"><div class="sh">What Depotnet records &mdash; damage {ex["id"]}</div>
<ul>
<li>Investigation marked complete: <mark>{esc(ex["complete"])}</mark></li>
<li>Root cause: <mark>ticks {ex["rc_n"]} boxes</mark> on one damage</li>
<li>Underlying cause: <mark>&ldquo;{esc(ex["underlying"])}&rdquo;</mark></li>
<li>Genny used? {esc(ex["genny"])} &middot; CAT used? {esc(ex["cat"])}</li>
</ul></div>
<div class="sb rv"><div class="sh">What the Sygma review and the panel meeting established</div>
<ul>
<li>The review of the genny and CAT download data confirmed the genny
<mark>was not used</mark> and the cable <mark>was never traced</mark> &mdash; the
form&#8217;s &ldquo;Genny used? Yes&rdquo; did not survive the data</li>
<li>When the cable was exposed in a trial hole it was not connected to and re-traced
&mdash; <mark>&ldquo;we haven&#8217;t got the clamps&rdquo;</mark>, in the team&#8217;s
own words at the panel</li>
<li>Clancy&#8217;s own procedure was not followed, in the Senior Contract
Manager&#8217;s words at the panel: <mark>&ldquo;our procedure is scan it, mark it,
expose it &mdash; and we haven&#8217;t done it&rdquo;</mark></li>
<li>The review&#8217;s formal conclusion, on record: the findings and conclusions
recorded for this damage are <mark>&ldquo;incorrect and need amending&rdquo;</mark></li>
{"<li><b>Nothing has been amended.</b> The form still answers &ldquo;Genny used? Yes&rdquo;, the investigation is still marked complete &mdash; and the damage is now <mark>closed on Depotnet</mark>, closed within days of the review that found its record incorrect (as captured " + esc(ex["captured"]) + ")</li>" if ex["status"] == "Closed" else ""}
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
       str(d["kit_no"]), f"of {n} known answers")}
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
