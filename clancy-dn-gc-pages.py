#!/usr/bin/env python3
"""clancy-dn-gc-pages.py -- render the Genny & CAT review section from the CC store.

Sister of clancy-dn-pages.py (damages). Reads the four clancy_dn_gc_* tables (populated
weekly by clancy-dn-gc-import.py) and regenerates the section's DATA-BEARING pages so the
site always matches the store:

  actions-register.html      -- wholly regenerated from clancy_dn_gc_actions
  inspections-register.html  -- wholly regenerated from clancy_dn_gc_inspections (+ actions join)
  operative-coverage.html    -- wholly regenerated from clancy_dn_gc_coverage (latest snapshot)
  7 person pages             -- narrative shell (gc-pages-shells.json) + record-by-record section
                                regenerated from clancy_dn_gc_inspections x clancy_dn_gc_findings
                                (incl. platform-comparison blocks stored in findings.comparison)
  landing / findings / people-- narrative shells re-published as-is (numbers inside them are
                                refreshed by simple token swaps where marked)

Narrative/prose lives in gc-pages-shells.json (same directory, git-versioned): EDIT THE SHELLS
to change wording, re-run with --publish to go live. Numbers always come from the DB.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-gc-pages.py [--local DIR] [--publish]

The renderer is deterministic: same store + same shells = byte-identical pages.
"""
import os, sys, json, re, argparse, datetime, statistics, urllib.request, html as H
from collections import Counter, defaultdict
import clancy_dn_ui as ui

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
SHELLS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gc-pages-shells.json")

IMG = f"{URL}/storage/v1/object/public/cc-report-media/depotnet-reviews/"
PDFP = "/raw/clancy-genny-cat-reviews/pdfs/"
MODULE = "clancy-genny-cat-reviews"

def rest(path, method="GET", body=None, headers=None):
    h = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 data=(json.dumps(body).encode() if body is not None else None),
                                 headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        t = r.read().decode()
        return json.loads(t) if t else None

def fetch_all(table, order="id"):
    out, page = [], 0
    while True:
        h = {"apikey": SR, "Authorization": f"Bearer {SR}", "Range": f"{page*1000}-{page*1000+999}"}
        req = urllib.request.Request(f"{URL}/rest/v1/{table}?select=*&order={order}", headers=h)
        with urllib.request.urlopen(req, timeout=120) as r:
            batch = json.loads(r.read().decode())
        out += batch
        if len(batch) < 1000: return out
        page += 1

def dt(v):
    if not v: return None
    return datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))

def esc(s): return H.escape(str(s), quote=False)

def norm_name(v):
    """An inspector's name without its payroll suffix — the store holds both forms for one person."""
    return re.sub(r"\s*\(\d+\)\s*$", "", str(v or "")).strip()

# ---------------------------------------------------------------- data pulls
def load_store():
    ins = fetch_all("clancy_dn_gc_inspections")
    act = fetch_all("clancy_dn_gc_actions", "id,question_id")
    cov = fetch_all("clancy_dn_gc_coverage", "operative")
    fnd = fetch_all("clancy_dn_gc_findings", "inspection_id")
    snap = max(c["snapshot_date"] for c in cov)
    cov = [c for c in cov if c["snapshot_date"] == snap]
    return ins, act, cov, fnd, snap

# ---------------------------------------------------------------- SVG helpers (house style)
NAVY = "#1c2a6e"; RED = "#c0281e"; MUTED = "#5b6770"; GREEN = "#1e7a46"; LINE = "#e3e6ea"

def hbars(data, maxw=560, rowh=34, color=NAVY, valfmt=lambda v: f"{v:,}", highlight=None, lx=170):
    mx = max(v for _, v in data) or 1
    h = rowh * len(data)
    parts = [f'<svg viewBox="0 0 780 {h}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">']
    for i, (label, v) in enumerate(data):
        y = i * rowh
        w = max(2, v / mx * maxw)
        c = highlight(label, v) if highlight else color
        parts.append(f'<text x="{lx-10}" y="{y+rowh/2+4}" text-anchor="end" font-size="13" fill="#1f2933">{esc(label)}</text>')
        parts.append(f'<rect x="{lx}" y="{y+7}" width="{w:.1f}" height="{rowh-14}" rx="4" fill="{c}"/>')
        parts.append(f'<text x="{lx+w+8:.1f}" y="{y+rowh/2+4}" font-size="12.5" font-weight="700" fill="#1f2933">{valfmt(v)}</text>')
    parts.append("</svg>")
    return "".join(parts)

def monthly_combo(monthly):
    W, Hh, pad, padb = 780, 300, 44, 46
    n = len(monthly)
    bw = (W - pad * 2) / n * 0.62
    mx = max(d[1] for d in monthly) or 1
    parts = [f'<svg viewBox="0 0 {W} {Hh}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">']
    for gv in range(50, mx + 1, 50):
        gy = Hh - padb - gv / mx * (Hh - padb - 30)
        parts.append(f'<line x1="{pad}" y1="{gy:.0f}" x2="{W-pad}" y2="{gy:.0f}" stroke="{LINE}" stroke-width="1"/>')
        parts.append(f'<text x="{pad-6}" y="{gy+4:.0f}" text-anchor="end" font-size="10" fill="{MUTED}">{gv}</text>')
    pts = []
    MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    for i, (ym, rows, ss, pct) in enumerate(monthly):
        x = pad + (i + 0.5) * (W - pad * 2) / n
        bh = rows / mx * (Hh - padb - 30)
        parts.append(f'<rect x="{x-bw/2:.1f}" y="{Hh-padb-bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{NAVY}" opacity="0.85"/>')
        yy, mm = ym.split("-")
        parts.append(f'<text x="{x:.0f}" y="{Hh-padb+16}" text-anchor="middle" font-size="10.5" fill="{MUTED}">{MON[int(mm)-1]}</text>')
        if mm == "01" or i == 0:
            parts.append(f'<text x="{x:.0f}" y="{Hh-padb+30}" text-anchor="middle" font-size="10" font-weight="700" fill="{MUTED}">{yy}</text>')
        py = 30 + (100 - pct) / 100 * (Hh - padb - 30) * 0.92
        pts.append((x, py, pct))
    poly = " ".join(f"{x:.0f},{y:.0f}" for x, y, _ in pts)
    parts.append(f'<polyline points="{poly}" fill="none" stroke="{RED}" stroke-width="2.5"/>')
    for x, y, pct in pts:
        parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="3.4" fill="{RED}"/>')
        parts.append(f'<text x="{x:.0f}" y="{y-8:.0f}" text-anchor="middle" font-size="9.5" font-weight="700" fill="{RED}">{pct}%</text>')
    parts.append(f'<rect x="{pad}" y="6" width="12" height="12" rx="3" fill="{NAVY}" opacity="0.85"/><text x="{pad+18}" y="16" font-size="11.5" fill="#1f2933">actions raised</text>')
    parts.append(f'<line x1="{pad+130}" y1="12" x2="{pad+154}" y2="12" stroke="{RED}" stroke-width="2.5"/><circle cx="{pad+142}" cy="12" r="3" fill="{RED}"/><text x="{pad+160}" y="16" font-size="11.5" fill="#1f2933">closed in the same second (%)</text>')
    parts.append("</svg>")
    return "".join(parts)

# ---------------------------------------------------------------- stats
def actions_stats(act):
    S = {}
    S["total"] = len(act)
    S["inspections"] = len(set(a["id"] for a in act))
    S["inspectors"] = len(set(a["inspector"] for a in act))
    S["contracts"] = len(set(a["contract"] for a in act))
    raised = [dt(a["date_raised"]) for a in act if a["date_raised"]]
    S["period"] = (min(raised).strftime("%d %b %Y"), max(raised).strftime("%d %b %Y"))
    closed = [a for a in act if a["date_raised"] and a["date_closed"]]
    S["closed_n"] = len(closed)
    S["ss"] = sum(1 for a in closed if a["same_second_close"])
    S["self"] = sum(1 for a in closed if a["action_owner"] == a["inspector"])
    gaps = [(dt(a["date_closed"]) - dt(a["date_raised"])).total_seconds() for a in closed]
    S["dist"] = [("Same second", sum(1 for g in gaps if g == 0)),
                 ("Under 10 min", sum(1 for g in gaps if 0 < g < 600)),
                 ("10 min - 24 h", sum(1 for g in gaps if 600 <= g < 86400)),
                 ("1 - 7 days", sum(1 for g in gaps if 86400 <= g < 604800)),
                 ("Over 7 days", sum(1 for g in gaps if g >= 604800))]
    mon = defaultdict(lambda: [0, 0, 0])
    for a in act:
        d = dt(a["date_raised"])
        if not d: continue
        key = d.strftime("%Y-%m")
        mon[key][0] += 1
        if a["date_closed"]:
            mon[key][1] += 1
            if a["same_second_close"]: mon[key][2] += 1
    S["monthly"] = [(m, v[0], v[2], round(v[2] / v[1] * 100) if v[1] else 0) for m, v in sorted(mon.items())]
    S["modes"] = Counter(a["mode"] or "Other" for a in act).most_common()
    S["status"] = Counter(a["action_status"] for a in act)
    S["open_by_owner"] = Counter(str(a["action_owner"] or a["inspector"]) for a in act
                                 if a["action_status"] in ("Open", "Overdue")).most_common()
    return S

def inspections_stats(ins, act):
    S = {}
    S["total"] = len(ins)
    S["inspectors"] = len(set(i["inspector"] for i in ins))
    S["contracts"] = len(set(i["contract"] for i in ins))
    created = [dt(i["date_created"]) for i in ins if i["date_created"]]
    S["period"] = (min(created).strftime("%d %b %Y"), max(created).strftime("%d %b %Y"))
    S["zero"] = sum(1 for i in ins if (i["points"] or 0) == 0 and (i["percentage"] or 0) == 0)
    actioned = set(a["id"] for a in act)
    S["with_action"] = sum(1 for i in ins if i["id"] in actioned)
    per = defaultdict(lambda: [0, 0])
    for i in ins:
        per[i["inspector"]][0] += 1
        if i["id"] in actioned: per[i["inspector"]][1] += 1
    S["zero_action_inspectors"] = sorted(((k, v[0]) for k, v in per.items() if v[1] == 0 and v[0] >= 10),
                                         key=lambda x: -x[1])
    S["zero_action_all"] = sum(1 for v in per.values() if v[1] == 0)
    S["zero_action_total_ins"] = sum(v[0] for v in per.values() if v[1] == 0)
    S["monthly"] = sorted(Counter(d.strftime("%Y-%m") for d in created).items())
    return S

def coverage_stats(cov):
    S = {}
    S["total"] = len(cov)
    act = [c for c in cov if c["active"]]
    S["active"] = len(act)
    days = [c["days_since"] for c in act if c["days_since"] is not None]
    S["median"] = statistics.median(days) if days else 0
    S["over90"] = sum(1 for d in days if d > 90)
    S["over180"] = sum(1 for d in days if d > 180)
    S["worst"] = max(days) if days else 0
    S["buckets"] = [("Within 30 days", sum(1 for d in days if d <= 30)),
                    ("31-90 days", sum(1 for d in days if 30 < d <= 90)),
                    ("91-180 days", sum(1 for d in days if 90 < d <= 180)),
                    ("Over 180 days", sum(1 for d in days if d > 180))]
    S["oldest"] = sorted(act, key=lambda c: -(c["days_since"] or 0))[:15]
    return S

# ---------------------------------------------------------------- person record sections
def fmt_d(v):
    d = dt(v)
    return d.strftime("%d/%m/%Y %H:%M") if d else ""

def record_card(f, imap):
    rid = f["inspection_id"]
    tier = f["capture_tier"]
    badges = []
    if tier == "live-platform-data": badges.append('<span class="badge2 live">Live C.A.T Manager data on screen &ndash; pull &amp; compare first</span>')
    if tier == "live-form-capture": badges.append('<span class="badge2 form">Live Depotnet form capture held</span>')
    if f["flag_group"]: badges.append('<span class="badge2 target">On the data-pull target list</span>')
    if not badges: badges.append('<span class="badge2 pdfonly">Document only</span>')
    sub = f' &middot; {esc(f["subcontractor"])}' if f["subcontractor"] else ""
    figs = "".join(imap.get(rid, []))
    shots = f'<div class="shots">{figs}</div>' if figs else ""
    comp = ""
    cj = f.get("comparison")
    if cj:
        c = cj if isinstance(cj, dict) else json.loads(cj)
        if c.get("html"):
            comp = c["html"]
        else:
            comp = ('<div class="compare"><b>Sygma platform comparison:</b> data pull in progress for this record.</div>')
    elif f["flag_group"]:
        comp = ('<div class="compare"><b>Sygma platform comparison:</b> awaiting the C.A.T&nbsp;Manager data pull for this operative, kit and window &ndash; will be added here.</div>')
    return f"""
  <div class="rec" id="rec-{rid}">
    <h3>{esc(f["usage_window"])} &middot; {esc(f["operatives_extracted"])}</h3>
    <div class="meta">Record {rid}{sub} &middot; CAT serial: <b>{esc(f["cat_plant"])}</b> &middot; Genny serial: <b>{esc(f["genny_plant"])}</b></div>
    {"".join(badges)}
    <div class="facts">Scan answer on the form: <b>{esc(f["scan_answer"])}</b> &middot; actions raised: <b>{esc(f["actions_raised"] or "none")}</b></div>
    <a class="pdfbtn" href="{PDFP}{rid}.pdf" target="_blank">Open the original inspection (PDF)</a>
    {shots}
    {comp}
  </div>"""

def person_section(name, fnd, ins, imap):
    ids = {i["id"]: i for i in ins if name.split()[0] in (i["inspector"] or "") and name.split()[1] in (i["inspector"] or "")}
    rows = [f for f in fnd if f["inspection_id"] in ids and f["sampled"]]
    rows.sort(key=lambda f: dt(ids[f["inspection_id"]]["date_created"]) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc))
    n_live = sum(1 for f in rows if f["capture_tier"] in ("live-platform-data", "live-form-capture"))
    n_tgt = sum(1 for f in rows if f["flag_group"])
    cards = "".join(record_card(f, imap) for f in rows)
    return f"""
  <div class="card"><h2 class="navy">The inspections sampled &ndash; record by record</h2>
    <p>Every one of the {len(rows)} sampled records, oldest first. Each carries its original document, the screenshots that belong to that exact record, and the operative, kit and window details needed to pull the matching data from our platform. <b>{n_live}</b> record(s) here hold live screen captures and <b>{n_tgt}</b> are on the <a href="https://drive.google.com/drive/folders/1o7S2GV27Y-larF1xDJD0o7xvg2iBpHta">data-pull target list</a>; green means we hold the platform&rsquo;s own numbers for comparison.</p>
  </div>
{cards}
  """

# ---------------------------------------------------------------- image map (curated figures per record)
# Captions are narrative: they live here (git-versioned) not in the DB.
def build_image_map():
    def fig(src, cap):
        plain = H.unescape(re.sub(r"<[^>]+>", "", cap)).replace('"', "'")
        return f'<figure><img src="{IMG}{src}" alt="{H.escape(plain, quote=True)}" loading="lazy"><figcaption>{cap}</figcaption></figure>'
    M = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gc-pages-figures.json")))
    return {int(rid): [fig(s, c) for s, c in items] for rid, items in M.items()}

# ---------------------------------------------------------------- assemble + publish
def figures(I, A, C, n_findings):
    """Every live figure the narrative shells can quote, in one place.

    A shell writes {{inspections}}; this decides what that means. Numbers embedded in a card's
    LABEL are tokens too — "170 inspectors, 22 contracts" sat frozen inside a label for months
    while the big number above it was being refreshed.
    """
    lo, hi = I["period"]
    months = (datetime.datetime.strptime(hi, "%d %b %Y") - datetime.datetime.strptime(lo, "%d %b %Y")).days / 30.44
    return {
        "inspections": f"{I['total']:,}",
        "inspectors": f"{I['inspectors']:,}",
        "contracts": f"{I['contracts']:,}",
        "months": f"{months:.0f}",
        "zero_pct": f"{I['zero'] / I['total'] * 100:.0f}%" if I["total"] else "0%",
        "gc_actions": f"{A['total']:,}",
        "same_second_pct": f"{A['ss'] / A['closed_n'] * 100:.1f}%" if A["closed_n"] else "0%",
        "median_days": f"{C['median']:.0f}",
        "over90": f"{C['over90']:,}",
        "pdfs": f"{n_findings:,}",
        "with_action": f"{I['with_action']:,}",
    }


def fill(html, figs):
    """Substitute {{tokens}}. An unknown token is a hard error, not a silent placeholder: a page
    that shipped a literal {{inspections}} to a Clancy reader would be worse than a stale number."""
    def sub(m):
        k = m.group(1)
        if k not in figs:
            raise KeyError(f"shell uses {{{{{k}}}}} but figures() does not define it")
        return figs[k]
    return re.sub(r"\{\{(\w+)\}\}", sub, html)



def vocab_gate(html):
    """Refuse to publish wording the section's rules ban. Fail closed - same gate, same
    reason as pages/hub/analysis; these publishers shipped ungated for a month and only
    passed by luck (round-3 plan audit, 2 Aug 2026)."""
    import subprocess as _sp, sys as _s
    r = _sp.run([_s.executable, f"{VAULT}/clancy-vocab-check.py", "-"],
                input=html, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        raise SystemExit("REFUSED to publish - reword the phrases above and re-run.")

def publish(key, htmlpage, local, do_publish):
    # Every page in this section goes through here, so this is where the shared section chrome
    # (navbar + breadcrumbs) is retrofitted onto shells that predate the design system. The
    # injector is idempotent, so re-running the generator cannot stack two navbars.
    # Reskin first, then inject: the chrome is already on brand, so reskinning after would be
    # scanning colours that were never navy. reskin() is a no-op once nothing navy is left, so
    # re-running the generator is safe.
    htmlpage = ui.inject(ui.reskin(htmlpage), "reviews")
    if local:
        fn = os.path.join(local, key.replace("/", "__") + (".html" if not key.endswith(".html") else ""))
        open(fn, "w").write(htmlpage)
    if do_publish:
        vocab_gate(htmlpage)
        assert "$GCP$" not in htmlpage
        import subprocess
        # The override matters here too: module_content's wording trigger refuses clancy-%
        # pages containing strike-words, and Genny & CAT review content can legitimately
        # quote them (they review service strikes).
        _reason = "Genny and CAT review pages quote incident material verbatim"
        r = subprocess.run(["python3", f"{VAULT}/cc-sql.py",
            f"SELECT set_config('app.damage_review_override', '{_reason}', true);\n"
            f"INSERT INTO module_content (module_key,html,updated_at) VALUES ('{key}',$GCP${htmlpage}$GCP$,now()) "
            f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html,updated_at=now()"],
            capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
        if r.returncode != 0:
            sys.stderr.write(f"publish {key} FAILED: {(r.stderr or r.stdout)[:300]}\n"); sys.exit(1)
    print(f"rendered {key}: {len(htmlpage):,} bytes")


# ---------------------------------------------------------------- the three register pages
# Ported 2 Aug 2026 (edits-plan stage 1). These replaced hand-built Chrome-era pages that no
# script could regenerate — the section could never fully republish until now.

REG_CSS = """<style>
.rwrap{max-width:1120px;margin:0 auto;padding:24px 20px 60px}
.rk{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.rk .k{background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:14px 16px;
 box-shadow:0 1px 2px rgba(31,41,51,.05)}
.rk .n{font-size:26px;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.rk .l{font-size:12px;color:#5b6770;margin-top:3px;line-height:1.35}
.rk .warn .n{color:#D50032}
table.reg{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e6ea;
 border-radius:12px;overflow:hidden;font-size:13px}
table.reg th{background:#f5f6f8;text-align:left;padding:9px 10px;font-size:11px;
 text-transform:uppercase;letter-spacing:.05em;color:#5b6770;white-space:nowrap}
table.reg th.n,table.reg td.n{text-align:right;font-variant-numeric:tabular-nums}
table.reg td{padding:8px 10px;border-top:1px solid #eef1f5;vertical-align:top}
.regnote{font-size:12.5px;color:#5b6770;margin:10px 0 18px}
.scrollx{overflow-x:auto;border-radius:12px}
.pill{display:inline-block;border-radius:20px;padding:2px 9px;font-size:11.5px;font-weight:700}
.pill.open{background:#fdecea;color:#D50032}.pill.closed{background:#ecf7f0;color:#1e7a46}
h2.rh{font-size:18px;margin:26px 0 8px;letter-spacing:-.02em}
</style>"""


def _reg_head(title, sub):
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)} – Genny &amp; CAT | Sygma Solutions</title>{REG_CSS}</head>'
            f'<body><div class="rwrap"><h1 style="font-size:24px;letter-spacing:-.02em">'
            f'{esc(title)}</h1><p class="regnote">{sub}</p>')


def actions_register_page(act, A):
    """The corrective-actions register, wholly from clancy_dn_gc_actions."""
    open_n = sum(1 for a in act if a["action_status"] in ("Open", "Overdue"))
    kpis = (f'<div class="rk">'
            f'<div class="k"><div class="n">{A["total"]:,}</div><div class="l">action rows, '
            f'{A["period"][0]} &ndash; {A["period"][1]}</div></div>'
            f'<div class="k{" warn" if open_n else ""}"><div class="n">{open_n:,}</div>'
            f'<div class="l">open or overdue now</div></div>'
            f'<div class="k"><div class="n">{A["closed_n"]:,}</div><div class="l">closed</div></div>'
            f'<div class="k warn"><div class="n">{A["ss"]:,}</div><div class="l">closed in the '
            f'same second they were raised</div></div>'
            f'<div class="k"><div class="n">{A["inspectors"]}</div><div class="l">inspectors '
            f'raising them</div></div></div>')
    dist = hbars(A["dist"], color="#353E47")
    recent = sorted((a for a in act if a["date_raised"]), key=lambda a: a["date_raised"],
                    reverse=True)[:150]
    rows = "".join(
        f'<tr><td style="white-space:nowrap">{fmt_d(a["date_raised"])}</td>'
        f'<td>{esc(a["contract_family"] or a["contract"] or "")}</td>'
        f'<td>{esc((a["question"] or a["defect_comments"] or "")[:110])}</td>'
        f'<td>{esc(a["inspector"] or "")}</td>'
        f'<td><span class="pill {"closed" if a["date_closed"] else "open"}">'
        f'{esc(a["action_status"] or ("Closed" if a["date_closed"] else "Open"))}</span></td>'
        f'<td style="white-space:nowrap">{fmt_d(a["date_closed"]) if a["date_closed"] else "&mdash;"}</td></tr>'
        for a in recent)
    return (_reg_head("The Actions Register",
            "Every corrective action raised from a Genny &amp; CAT inspection, from Depotnet&#8217;s "
            "own export. Rebuilt from the database on every publish.")
            + kpis
            + '<h2 class="rh">How fast actions close</h2>' + dist
            + f'<h2 class="rh">The most recent {len(recent)} actions</h2>'
            f'<div class="regnote">Of {A["total"]:,} in total &mdash; the counts above cover '
            f'every row, the table shows the newest.</div>'
            f'<div class="scrollx"><table class="reg"><tr><th>Raised</th><th>Contract</th>'
            f'<th>What it concerns</th><th>Inspector</th><th>Status</th><th>Closed</th></tr>'
            + rows + "</table></div></div><script src='/clancy/genny-widget.js?v=20260731c' defer></script></body></html>")


def inspections_register_page(ins, act, I):
    """Completed inspections, wholly from clancy_dn_gc_inspections."""
    kpis = (f'<div class="rk">'
            f'<div class="k"><div class="n">{I["total"]:,}</div><div class="l">inspections, '
            f'{I["period"][0]} &ndash; {I["period"][1]}</div></div>'
            f'<div class="k"><div class="n">{I["inspectors"]}</div><div class="l">inspectors</div></div>'
            f'<div class="k"><div class="n">{I["with_action"]:,}</div><div class="l">raised at '
            f'least one action</div></div>'
            f'<div class="k warn"><div class="n">{I["zero"]:,}</div><div class="l">scored zero '
            f'points and zero percent</div></div></div>')
    monthly = hbars([(m, n) for m, n in I["monthly"][-12:]], color="#353E47",
                    valfmt=lambda v: f"{v:,}")
    actioned = set(a["id"] for a in act)
    recent = sorted((i for i in ins if i["date_created"]), key=lambda i: i["date_created"],
                    reverse=True)[:150]
    rows = "".join(
        f'<tr><td style="white-space:nowrap">{fmt_d(i["date_created"])}</td>'
        f'<td>{esc(i["inspection"] or i["type"] or "")}</td>'
        f'<td>{esc(i["inspector"] or "")}</td>'
        f'<td>{esc(i["contract_family"] or i["contract"] or "")}</td>'
        f'<td>{esc((i["location"] or "")[:60])}</td>'
        f'<td class="n">{i["percentage"] if i["percentage"] is not None else "&mdash;"}</td>'
        f'<td>{"Yes" if i["id"] in actioned else "&mdash;"}</td></tr>'
        for i in recent)
    return (_reg_head("Completed Inspections",
            "Every Genny &amp; CAT inspection Depotnet holds, from its own export. Rebuilt from "
            "the database on every publish.")
            + kpis
            + '<h2 class="rh">Inspections per month, last 12</h2>' + monthly
            + f'<h2 class="rh">The most recent {len(recent)} inspections</h2>'
            f'<div class="regnote">Of {I["total"]:,} in total.</div>'
            f'<div class="scrollx"><table class="reg"><tr><th>Date</th><th>Inspection</th>'
            f'<th>Inspector</th><th>Contract</th><th>Location</th><th class="n">Score %</th>'
            f'<th>Action raised</th></tr>' + rows + "</table></div></div><script src='/clancy/genny-widget.js?v=20260731c' defer></script></body></html>")


def coverage_page(cov, C, snap):
    """Operative coverage, wholly from clancy_dn_gc_coverage (latest snapshot)."""
    kpis = (f'<div class="rk">'
            f'<div class="k"><div class="n">{C["active"]}</div><div class="l">active operatives '
            f'(snapshot {esc(str(snap))})</div></div>'
            f'<div class="k"><div class="n">{C["median"]:.0f}d</div><div class="l">median days '
            f'since last inspection</div></div>'
            f'<div class="k warn"><div class="n">{C["over90"]}</div><div class="l">not inspected '
            f'in over 90 days</div></div>'
            f'<div class="k warn"><div class="n">{C["over180"]}</div><div class="l">over 180 '
            f'days</div></div></div>')
    buckets = hbars(C["buckets"], color="#353E47")
    rows = "".join(
        f'<tr><td>{esc(c["operative"] or "")}</td>'
        f'<td style="white-space:nowrap">{fmt_d(c["last_inspected"]) if c["last_inspected"] else "never"}</td>'
        f'<td>{esc(c["last_inspected_by"] or "&mdash;")}</td>'
        f'<td class="n">{c["days_since"] if c["days_since"] is not None else "&mdash;"}</td></tr>'
        for c in sorted((c for c in cov if c["active"]),
                        key=lambda c: -(c["days_since"] or 0)))
    return (_reg_head("Operative coverage",
            "How recently each active operative was inspected, from the latest coverage snapshot. "
            "Rebuilt from the database on every publish.")
            + kpis
            + '<h2 class="rh">How the workforce spreads</h2>' + buckets
            + '<h2 class="rh">Every active operative, least recently inspected first</h2>'
            f'<div class="scrollx"><table class="reg"><tr><th>Operative</th><th>Last inspected</th>'
            f'<th>By</th><th class="n">Days since</th></tr>' + rows + "</table></div>"
            "</div><script src='/clancy/genny-widget.js?v=20260731c' defer></script></body></html>")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local"); ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    if a.local: os.makedirs(a.local, exist_ok=True)
    shells = json.load(open(SHELLS_PATH))
    ins, act, cov, fnd, snap = load_store()
    A, I, C = actions_stats(act), inspections_stats(ins, act), coverage_stats(cov)
    print(f"store: {I['total']} inspections, {A['total']} action rows, {C['total']} coverage rows (snapshot {snap}), {len(fnd)} findings")
    print(f"key figures: same-second {A['ss']}/{A['closed_n']} ({A['ss']/A['closed_n']*100:.1f}%) | zero-scored {I['zero']}/{I['total']} | with-action {I['with_action']} ({I['with_action']/I['total']*100:.1f}%) | median cover {C['median']:.0f}d")
    imap = build_image_map()
    # person pages: shell head + regenerated record section + tail
    for slug, shell in ((s, v) for s, v in shells.items() if s.startswith("person/")):
        name = shell["inspector"]
        # The inspector field carries a payroll number on some rows and not others — "Jack
        # Blowers (10009565)" and "Adam Bailey" both occur, and the SAME person appears in both
        # forms. An exact match counted Jack Blowers as 0 and Adam Bailey as 1; normalising the
        # suffix away gives 83 and 44, which is what the shells were written from.
        mine = sum(1 for i in ins if norm_name(i["inspector"]) == norm_name(name))
        page = fill(shell["head"] + person_section(name, fnd, ins, imap) + shell["tail"],
                    {**figures(I, A, C, len(fnd)), "my_reviews": f"{mine:,}"})
        publish(f"{MODULE}/{slug.split('/')[1]}.html", page, a.local, a.publish)
    # narrative shells (landing/findings/people): republished as stored, with every LIVE figure
    # filled from the store. This used to be a find-and-replace on the literal numbers ("5,403" ->
    # the current total), which only ever covered three figures and silently did nothing once a
    # shell was edited. Tokens are explicit: a {{name}} with no value fails loudly below rather
    # than shipping a placeholder to a customer.
    figs = figures(I, A, C, len(fnd))
    for keyname, slug in (("landing", MODULE), ("findings", f"{MODULE}/findings.html"),
                          ("people", f"{MODULE}/people.html")):
        publish(slug, fill(shells[keyname]["html"], figs), a.local, a.publish)
    # the three registers — DB-rendered since 2 Aug 2026; the whole section now regenerates
    # from this one script (the Chrome-era "v1 generators" are gone and nothing else could
    # rebuild these pages)
    publish(f"{MODULE}/actions-register.html", actions_register_page(act, A), a.local, a.publish)
    publish(f"{MODULE}/inspections-register.html", inspections_register_page(ins, act, I),
            a.local, a.publish)
    publish(f"{MODULE}/operative-coverage.html", coverage_page(cov, C, snap), a.local, a.publish)

if __name__ == "__main__":
    main()
