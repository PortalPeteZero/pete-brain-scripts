#!/usr/bin/env python3
"""clancy-dn-pages.py — render the Depotnet Damages micro-app from the CC store.

Reads public.clancy_dn_incidents + public.clancy_dn_actions (populated by clancy-dn-import.py)
and renders the whole shared section as self-contained HTML pages stored in module_content under
module clancy-depotnet-damages (tier passcode strive2030, subsection External → served via /raw,
neutral OG card). Re-run after every import so the pages always match the store.

Pages: index (hub: Damages / Data Dive) · fy dashboards (26/27 + 25/26 full, 24/25 + 23/24
summary) · incidents (all-years interactive register) · actions (actions centre) · insights
(trends / improvements / capture quality).

Design: data-dense dashboard language; palettes validated with the dataviz six-checks
(one Clancy chartreuse for single-series bars; utilities
Gas #b45309 · Water #2563eb · Electric #dc2626 · Comms #15803d + grey Other).

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-pages.py [--local DIR] [--publish]
  --local DIR   write the pages to DIR for preview
  --publish     upsert module row + module_content (the live pages)
"""
import os, sys, json, re, argparse, datetime, urllib.request, html as H
from collections import Counter, defaultdict
import clancy_dn_ui as ui

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
MK = "clancy-depotnet-damages"

def _urlopen_retry(req, timeout=120, tries=6):
    """Supabase answers 429 under load — a heavy filing run or 22 page writes in a row will
    hit it. Without backoff the caller dies mid-publish and leaves the section half-updated.
    Retries on 429 and 5xx with exponential backoff; anything else raises immediately."""
    import time as _t
    for n in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 30))
        except Exception:
            if n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 30))


def rest(path, method="GET", body=None, headers=None):
    h = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 data=(json.dumps(body).encode() if body is not None else None),
                                 headers=h, method=method)
    with _urlopen_retry(req, timeout=120) as r:
        t = r.read().decode()
        return json.loads(t) if t else None

def rest_all(path, page=1000):
    """Every row, not the first 1,000.

    PostgREST enforces a server-side max-rows (1,000 here) and SILENTLY ignores a larger `limit`
    in the query string. Found 1 Aug 2026: the answers query asked for 20,000, got 1,000, and the
    per-damage pages had been rendering the Q&A for only the first 13 incidents of 535 — with no
    error anywhere. Anything that can exceed 1,000 rows must come through here.
    """
    out = []
    while True:
        lo = len(out)
        got = rest(path, headers={"Range-Unit": "items", "Range": f"{lo}-{lo + page - 1}"})
        if not got:
            break
        out.extend(got)
        if len(got) < page:
            break
    return out


# ---------------------------------------------------------------- data

def load():
    # NOT select=* any more. raw_api holds the whole Depotnet payload (4.3MB across the year)
    # and would be serialised into every page. PostgREST has no "exclude", so the wanted columns
    # are listed. A column added later is simply absent here until it is added — deliberate, so
    # nothing new reaches a customer page without somebody deciding it should.
    INC_COLS = ("id,date_raised,incident_date,category,subcategory,raised_by,job_id,job_ref,"
                "contract,contract_family,contract_number,workstream,business_unit,location,"
                "severity,subcontractor,description,status,fy,utility_class,utility_keyword,"
                "utility_confirmed,pdf_captured_at,capture_drive_folder,strike_category,"
                "strike_subcategory,depth_mm,lat,lon,environment,caused_by_person,caused_by_plant,"
                "service_interrupted,reported_to_owner_at,root_cause,underlying_cause,"
                "lessons_learnt,incident_summary,capture_incident,capture_actions,capture_note,"
                "actions_captured_at,sygma_summary,sygma_findings,sygma_next_actions,"
                "sygma_reviewed_at,sygma_narrative,sygma_report_url,sygma_operatives,"
                "sygma_supervisor,sygma_stage_note,sygma_cause,sygma_shareable,sygma_drive_folder,"
                "timeline,report_submitted_at,report_submitted_by,include_investigation")
    inc = rest_all(f"clancy_dn_incidents?select={INC_COLS}&order=incident_date.desc")
    act = rest_all("clancy_dn_actions?select=*&order=date_raised.desc")
    for r in inc:
        r.pop("embedding", None)
    for a in act:
        a.pop("embedding", None)
    for r in inc:
        r["d"] = r["incident_date"][:10] if r["incident_date"] else None
        r["month"] = r["d"][:7] if r["d"] else None
        r["sev"] = {"HIGH - Category 1": "High (Cat 1)", "MEDIUM - Category 2": "Medium (Cat 2)",
                    "LOW - Category 3": "Low (Cat 3)"}.get(r["severity"], r["severity"] or "Unstated")
        u = r["utility_class"] or "Unclassified"
        r["ugroup"] = ("Electric" if u.startswith("Electric") else
                       "Gas" if u == "Gas" else
                       "Water" if u == "Water" else
                       "Comms / fibre" if u.startswith("Comms") else "Other")
    for a in act:
        a["due"] = a["due_date"][:10] if a["due_date"] else None
    # Sygma's own review of a damage is merged onto the incident itself (the sygma_* fields), so
    # this reads the register rather than the retired clancy_damages table. Same shape out.
    enr = rest("clancy_dn_incidents?select=id,job_ref,status,sygma_stage_note,sygma_summary,"
               "sygma_findings,sygma_next_actions,sygma_drive_folder,sygma_report_url"
               "&sygma_reviewed_at=not.is.null")
    enrich = {e["id"]: {"dn_id": e["id"], "job_ref": e["job_ref"], "status": e["status"],
                        "stage_note": e["sygma_stage_note"], "summary": e["sygma_summary"],
                        "key_findings": e["sygma_findings"], "next_actions": e["sygma_next_actions"],
                        "drive_folder": e["sygma_drive_folder"], "report_url": e["sygma_report_url"]}
              for e in enr}
    # Everything the capture pulled off Depotnet for each damage: the incident PDF, the photos and
    # any documents attached to it. These live in their own Drive folder, which is NOT the same
    # folder as the Sygma panel-review material, so a page that renders only the review folder shows
    # none of them.
    files = rest_all("clancy_dn_files?select=incident_id,action_id,kind,name,drive_id,drive_folder&order=incident_id,kind,name")
    # The full investigation Q&A. 2,404 rows were being captured and then never shown anywhere —
    # the richest material we hold, invisible on the page. (Pete, 31 Jul: ensure everything the
    # agent pulled in is actually there.)
    # Unanswered rows are now KEPT. The API returns the full question set with answer:null,
    # so "this mandatory question is blank" is recordable for the first time — that is the
    # difference between "no investigation" (which we cannot know) and "the investigation
    # report section has not been completed" (which we can).
    answers = rest_all("clancy_dn_answers?select=incident_id,section,q_no,question,answer,mandatory,answered&order=incident_id,section,q_no")
    aby = defaultdict(list)
    for x in answers:
        aby[x["incident_id"]].append(x)
    fby = defaultdict(list)
    for f in files:
        fby[f["incident_id"]].append(f)
    return inc, act, enrich, dict(fby), dict(aby)

FAM_ORDER = ["Southern Water", "Anglian Water", "South East Water", "Scottish Water", "UKPN", "SGN"]
# Contract families no longer carry their own hue: the "By contract" chart is a single series
# (a count per contract) and giving each bar a different colour encoded nothing the label was not
# already saying. Kept as a name list only.
UTIL_ORDER = ["Gas", "Water", "Electric", "Comms / fibre", "Other"]
UTIL_COLORS = {"Gas": "#b45309", "Water": "#2563eb", "Electric": "#dc2626",
               "Comms / fibre": "#15803d", "Other": "#737373"}
SEV_COLORS = {"High (Cat 1)": "#b91c1c", "Medium (Cat 2)": "#d97706", "Low (Cat 3)": "#16a34a"}
STATUS_COLORS = {"Closed": "#15803d", "Open": "#b45309", "Complete with Outstanding Actions": "#64748b",
                 "Overdue": "#dc2626"}

def fam(r):
    f = r.get("contract_family") or "Unstated"
    return f if f in FAM_ORDER else "Other"

FYS = ["FY23/24", "FY24/25", "FY25/26", "FY26/27"]
FY_LABEL = {"FY23/24": "FY 2023/24", "FY24/25": "FY 2024/25", "FY25/26": "FY 2025/26", "FY26/27": "FY 2026/27"}
FY_PAGE = {"FY23/24": "fy-2023-24.html", "FY24/25": "fy-2024-25.html",
           "FY25/26": "fy-2025-26.html", "FY26/27": "fy-2026-27.html"}
FY_MONTHS = {  # month sequence Apr..Mar
    "FY23/24": [f"2023-{m:02d}" for m in range(4, 13)] + [f"2024-{m:02d}" for m in range(1, 4)],
    "FY24/25": [f"2024-{m:02d}" for m in range(4, 13)] + [f"2025-{m:02d}" for m in range(1, 4)],
    "FY25/26": [f"2025-{m:02d}" for m in range(4, 13)] + [f"2026-{m:02d}" for m in range(1, 4)],
    "FY26/27": [f"2026-{m:02d}" for m in range(4, 13)] + [f"2027-{m:02d}" for m in range(1, 4)],
}
MON = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]

# ---------------------------------------------------------------- svg charts

def esc(s):
    return H.escape(str(s if s is not None else ""), quote=True)

def vbar_months(series, prior=None, width=920, height=240, color="#97D700", prior_color="#ccd3da",
                label="", prior_label=""):
    """Monthly vertical bars (Apr..Mar) with optional prior-FY ghost bars behind."""
    n = 12
    pad_l, pad_b, pad_t = 34, 26, 14
    W, Hh = width, height
    plot_w, plot_h = W - pad_l - 8, Hh - pad_b - pad_t
    mx = max([1] + series + (prior or []))
    step = plot_w / n
    bw = min(30, step * 0.34)
    out = [f'<svg viewBox="0 0 {W} {Hh}" role="img" class="chart">']
    for g in range(0, 5):
        v = mx * g / 4
        y = pad_t + plot_h * (1 - g / 4)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W-8}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l-6}" y="{y+4:.1f}" class="tick" text-anchor="end">{v:.0f}</text>')
    for i in range(n):
        cx = pad_l + step * i + step / 2
        if prior:
            ph = plot_h * prior[i] / mx
            out.append(f'<rect x="{cx-bw+2:.1f}" y="{pad_t+plot_h-ph:.1f}" width="{bw:.1f}" height="{max(ph,0):.1f}" rx="3" fill="{prior_color}"><title>{prior_label} {MON[i]}: {prior[i]}</title></rect>')
        h_ = plot_h * series[i] / mx
        xoff = 2 if prior else -bw / 2
        out.append(f'<rect x="{cx+xoff:.1f}" y="{pad_t+plot_h-h_:.1f}" width="{bw:.1f}" height="{max(h_,0):.1f}" rx="3" fill="{color}" class="bar"><title>{label} {MON[i]}: {series[i]}</title></rect>')
        if series[i]:
            out.append(f'<text x="{cx+xoff+bw/2:.1f}" y="{pad_t+plot_h-h_-5:.1f}" class="dlabel" text-anchor="middle">{series[i]}</text>')
        out.append(f'<text x="{cx:.1f}" y="{Hh-8}" class="tick" text-anchor="middle">{MON[i]}</text>')
    out.append("</svg>")
    return "".join(out)

def hbar(items, width=440, row_h=30, color=None, colors=None, maxw=None, fmt="{}"):
    """Horizontal bars: items = [(label, value)], colors optional dict by label."""
    mx = maxw or max([1] + [v for _, v in items])
    Hh = row_h * len(items) + 6
    lw = 150
    out = [f'<svg viewBox="0 0 {width} {Hh}" role="img" class="chart">']
    for i, (lab, v) in enumerate(items):
        y = i * row_h + 4
        c = (colors or {}).get(lab, color or "#97D700")
        bw = (width - lw - 54) * v / mx
        out.append(f'<text x="{lw-8}" y="{y+row_h/2+4:.1f}" class="blabel" text-anchor="end">{esc(lab)}</text>')
        out.append(f'<rect x="{lw}" y="{y+5}" width="{max(bw,2):.1f}" height="{row_h-12}" rx="3" fill="{c}" class="bar"><title>{esc(lab)}: {fmt.format(v)}</title></rect>')
        out.append(f'<text x="{lw+max(bw,2)+6:.1f}" y="{y+row_h/2+4:.1f}" class="dlabel">{fmt.format(v)}</text>')
    out.append("</svg>")
    return "".join(out)

def donut(items, colors, size=190, thick=26):
    """items = [(label, value)] — donut with centre total; legend rendered separately in HTML."""
    import math
    total = sum(v for _, v in items) or 1
    r = (size - thick) / 2 - 4
    cx = cy = size / 2
    out = [f'<svg viewBox="0 0 {size} {size}" role="img" class="chart donut">']
    a0 = -90.0
    for lab, v in items:
        if not v:
            continue
        frac = v / total
        a1 = a0 + frac * 360
        # 2px gap: shrink each arc slightly
        g = 1.2 if frac < 1 else 0
        x0 = cx + r * math.cos(math.radians(a0 + g)); y0 = cy + r * math.sin(math.radians(a0 + g))
        x1 = cx + r * math.cos(math.radians(a1 - g)); y1 = cy + r * math.sin(math.radians(a1 - g))
        large = 1 if (a1 - a0) > 180 else 0
        out.append(f'<path d="M {x0:.2f} {y0:.2f} A {r:.2f} {r:.2f} 0 {large} 1 {x1:.2f} {y1:.2f}" fill="none" stroke="{colors.get(lab, "#737373")}" stroke-width="{thick}" class="arc"><title>{esc(lab)}: {v} ({v/total*100:.0f}%)</title></path>')
        a0 = a1
    out.append(f'<text x="{cx}" y="{cy-2}" text-anchor="middle" class="donut-n">{total}</text>')
    out.append(f'<text x="{cx}" y="{cy+16}" text-anchor="middle" class="donut-l">total</text>')
    out.append("</svg>")
    return "".join(out)

def legend(items, colors):
    lis = "".join(
        f'<span class="lg"><i style="background:{colors.get(l, "#737373")}"></i>{esc(l)} <b>{v}</b></span>'
        for l, v in items if v)
    return f'<div class="legend">{lis}</div>'

# ---------------------------------------------------------------- html shell

CSS = """
/* Palette is The Clancy Group's own, sampled from their logo: #97D700 chartreuse, #D50032 red,
   #353E47 charcoal. The variable NAMES are historic (--navy dates from the first blue build) but
   the values are the brand's, so the register matches the rest of the Damage Depot. */
:root{--bg:#f4f6f9;--card:#ffffff;--ink:#182230;--muted:#5b6774;--soft:#8b95a3;--border:#e4e8ee;
--navy:#353E47;--navy2:#4a5560;--red:#D50032;--accent:#5f8b00;--green:#15803d;--amber:#b45309;
--shadow:0 1px 2px rgba(16,24,40,.05),0 4px 16px rgba(16,24,40,.07)}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
background:var(--bg);color:var(--ink);line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px 70px}
.wrap.wide{max-width:1760px}
.mast .wrap{padding:18px 22px 22px}
.mast .row{display:flex;align-items:center;gap:22px;margin-top:18px}
.mast .gw{position:relative;flex-shrink:0;padding-bottom:13px}
.mast .gw img{width:88px;height:88px;border-radius:50%;object-fit:cover;object-position:center 20%;border:3px solid #97D700;box-shadow:0 10px 26px rgba(0,0,0,.5);display:block;background:#2a323a}
@media(max-width:560px){.mast .gw img{width:60px;height:60px}}
.mast .np{position:absolute;left:50%;bottom:0;transform:translateX(-50%);background:#97D700;color:#1d2b00;font-size:10px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;padding:3px 10px;border-radius:20px;white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,.4)}
.mast h1{font-size:27px;font-weight:800;letter-spacing:-.02em;margin:14px 0 4px}
.mast .sub{font-size:14px;color:#b9c1ca;max-width:80ch}
.nav{display:flex;gap:6px;flex-wrap:wrap;margin-top:18px}
.nav a{font-size:13px;font-weight:600;color:#c8ced6;text-decoration:none;padding:7px 13px;border-radius:8px;background:rgba(255,255,255,.08);transition:background .2s,color .2s}
.nav a:hover{background:rgba(255,255,255,.18);color:#fff}
.nav a.on{background:#97D700;color:#1d2b00;font-weight:800}
.nav.subnav{margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,.14)}
.nav.subnav a{font-size:12.5px;padding:6px 12px;background:rgba(255,255,255,.05)}
.nav.subnav a.on{background:#fff;color:#353E47}
h2{font-size:15px;font-weight:700;color:var(--navy);letter-spacing:.02em;margin:0 0 4px}
.h2row{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:10px}
.h2row .note{font-size:12.5px;color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:13px;padding:14px 16px 12px;box-shadow:var(--shadow)}
.kpi .n{font-size:27px;font-weight:800;color:var(--navy);line-height:1.05;font-variant-numeric:tabular-nums}
.kpi .n.red{color:var(--red)} .kpi .n.green{color:var(--green)} .kpi .n.amber{color:var(--amber)}
.kpi .l{font-size:12px;color:var(--muted);margin-top:5px;line-height:1.3}
.kpi .delta{font-size:12px;font-weight:700;margin-top:3px}
.kpi .delta.down{color:var(--green)} .kpi .delta.up{color:var(--red)}
a.kpi{display:block;text-decoration:none;color:inherit;cursor:pointer;transition:transform .15s,box-shadow .15s}
a.kpi:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(16,24,40,.12)}
.kpi-go{font-size:11.5px;font-weight:700;color:var(--accent);margin-top:6px;opacity:.55;transition:opacity .15s}
a.kpi:hover .kpi-go{opacity:1}
.grid{display:grid;gap:16px;margin-bottom:16px}
.grid.c2{grid-template-columns:1fr 1fr}
.grid.c3{grid-template-columns:1fr 1fr 1fr}
@media(max-width:860px){.grid.c2,.grid.c3{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);padding:18px 20px;min-width:0}
.card.flat{box-shadow:none}
.chart{width:100%;height:auto;display:block}
.grid line.grid,line.grid{stroke:#eceff4;stroke-width:1}
text.tick{font-size:10.5px;fill:#8b95a3;font-family:inherit}
text.dlabel{font-size:10.5px;font-weight:700;fill:#44506033;fill:#445060;font-variant-numeric:tabular-nums}
text.blabel{font-size:12px;fill:#44506
0;fill:#445060}
.bar{transition:opacity .15s}.bar:hover{opacity:.75;cursor:default}
.arc:hover{opacity:.8}
.donut-n{font-size:26px;font-weight:800;fill:var(--navy);font-variant-numeric:tabular-nums}
.donut-l{font-size:11px;fill:#8b95a3}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:10px}
.legend .lg{font-size:12.5px;color:var(--muted);display:inline-flex;align-items:center;gap:6px}
.legend i{width:10px;height:10px;border-radius:3px;display:inline-block}
.legend b{color:var(--ink);font-variant-numeric:tabular-nums}
.doors{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:4px 0 22px}
@media(max-width:760px){.doors{grid-template-columns:1fr}}
.door{display:block;background:var(--card);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);padding:22px 24px;text-decoration:none;color:var(--ink);transition:transform .15s,box-shadow .15s;position:relative;overflow:hidden}
.door:hover{transform:translateY(-2px);box-shadow:0 6px 22px rgba(16,24,40,.12);cursor:pointer}
.door::before{content:"";position:absolute;inset:0 auto 0 0;width:5px;background:var(--accent)}
.door.dive::before{background:var(--red)}
.door .t{font-size:18px;font-weight:800;color:var(--navy);margin-bottom:4px}
.door .d{font-size:13.5px;color:var(--muted);max-width:52ch}
.door .go{margin-top:12px;font-size:13px;font-weight:700;color:var(--accent)}
.door.dive .go{color:var(--red)}
.fytiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:22px}
.fyt{display:block;background:var(--card);border:1px solid var(--border);border-radius:13px;padding:16px 18px;text-decoration:none;color:var(--ink);box-shadow:var(--shadow);transition:transform .15s}
.fyt:hover{transform:translateY(-2px);cursor:pointer}
.fyt .y{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.fyt .n{font-size:30px;font-weight:800;color:var(--navy);font-variant-numeric:tabular-nums;margin:2px 0}
.fyt .s{font-size:12px;color:var(--muted)}
.fyt.cur{outline:2px solid var(--accent);outline-offset:-2px}
.fyt .s a{color:var(--accent);text-decoration:none;font-weight:600}
.fyt .s a:hover{text-decoration:underline}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.filters input[type=search]{font:inherit;font-size:13.5px;padding:8px 12px;border:1px solid var(--border);border-radius:9px;background:#fff;min-width:210px}
.filters select{font:inherit;font-size:13px;padding:8px 10px;border:1px solid var(--border);border-radius:9px;background:#fff;color:var(--ink);cursor:pointer}
.filters .count{font-size:12.5px;color:var(--muted);margin-left:auto;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13.5px}
td .clamp2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.b{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap}
.b.yes{background:#ecf7f0;color:#15803d}.b.no{background:#f1f3f6;color:#8b95a3}
.b.warn{background:#fdecea;color:#b91c1c}
.caps{display:inline-flex;gap:4px}
.caps i{width:22px;height:22px;border-radius:6px;font-style:normal;font-size:11px;font-weight:800;
 display:inline-flex;align-items:center;justify-content:center;cursor:help;border:1px solid transparent}
.caps i.cap-ok{background:#ecf7f0;color:#15803d;border-color:#c8e6d3}
.caps i.cap-part{background:#fdf3e7;color:#b45309;border-color:#f0dcc0}
.caps i.cap-bad{background:#fdecea;color:#b91c1c;border-color:#f3cfcb}
.caps i.cap-na{background:#eef1f5;color:#64748b;border-color:#e0e5ec}
.caps i.cap-none{background:#fff;color:#c3cad3;border-color:#e4e8ee}
.ol .fl{margin-bottom:1px}
.ol .t{font-size:12.5px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ol .t.muted{color:#9aa3ad}
tbody tr.row:nth-child(even) td{background:#fafbfd}
tbody tr.row:hover td{background:#f2f6fc}
th{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#7b8694;text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none}
th .arr{opacity:.45;font-size:9px}
td{padding:9px 10px;border-bottom:1px solid #edf0f4;vertical-align:top}
tr.row{cursor:pointer}
tr.row:hover td{background:#f7f9fc}
tr.det td{background:#f9fafc;border-bottom:1px solid var(--border);padding:14px 16px}
.pill{display:inline-block;font-size:11px;font-weight:700;padding:3px 9px;border-radius:999px;white-space:nowrap}
.pill.sev-h{background:#fdecea;color:#b91c1c}.pill.sev-m{background:#fdf3e7;color:#b45309}.pill.sev-l{background:#ecf7f0;color:#15803d}
.pill.st-open{background:#fdf3e7;color:#b45309}.pill.st-closed{background:#ecf7f0;color:#15803d}
.pill.st-out{background:#eef1f5;color:#5b6774}.qa{margin-top:6px;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.qarow{display:grid;grid-template-columns:minmax(200px,42%) 1fr;gap:0;border-top:1px solid #eef1f5}
.qarow:first-child{border-top:0}
.qaq{padding:8px 12px;background:#f7f8fa;font-size:12.5px;color:#4a5561}
.qaa{padding:8px 12px;font-size:13.5px;color:#22303f;white-space:pre-wrap}
@media(max-width:640px){.qarow{grid-template-columns:1fr}.qaq{border-bottom:1px solid #eef1f5}}
.invbox{border:1px solid var(--line);border-left:4px solid #97D700;border-radius:0 10px 10px 0;padding:12px 16px;margin-top:10px;background:#fbfcfd}
.invbox.invnone{border-left-color:#c7ccd3;background:#f7f8fa}
.invbox.invthin{border-left-color:#b45309;background:#fff9f0}
.invl{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#6b7784}
.invv{margin-top:5px;font-size:14.5px;line-height:1.55;color:#22303f;white-space:pre-wrap}
.invnote{margin-top:6px;font-size:12px;color:#a35f00}
.pill.act-yes{background:#eef7dc;color:#4a6b00;border-color:#cfe39a}
.pill.act-no{background:#f4f6f9;color:#7b8694;border-color:#e2e6ec}
.pill.act-unk{background:#fff6e8;color:#a35f00;border-color:#f0d9b5}
.dgrps{display:flex;flex-direction:column;gap:12px}
.dgrp{background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden;
 box-shadow:var(--sh-1)}
.dgh{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;
 padding:13px 16px;background:#f7f9fc;border-bottom:1px solid var(--line);font-size:14px}
.dgh a{color:var(--green-d);font-weight:800;text-decoration:none}
.dgh a:hover{text-decoration:underline}
.dgl{list-style:none;margin:0;padding:4px 0}
.dgl li{padding:10px 16px;border-bottom:1px solid #f0f3f6;font-size:13.5px;
 display:flex;flex-wrap:wrap;gap:10px;align-items:baseline}
.dgl li:last-child{border-bottom:0}
.dgl li .small{flex-basis:100%;margin-top:2px}
.pill.st-over{background:#fdecea;color:#b91c1c}
.pill.uc{background:#f0f7e0;color:#4e7300}
.mono{font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
.small{font-size:12.5px}
.det .desc{font-size:13.5px;max-width:90ch;white-space:pre-wrap}
.det .meta{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12.5px;color:var(--muted);margin-top:8px}
.det .acts{margin-top:12px;border-top:1px dashed var(--border);padding-top:10px}
.det .acts h4{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:#7b8694;margin-bottom:6px}
.det .act{padding:7px 0;border-bottom:1px solid #eef1f5;font-size:13px}
.det .act:last-child{border-bottom:0}
.insight{border-left:4px solid var(--accent);padding:14px 18px;background:var(--card);border-radius:0 13px 13px 0;border-top:1px solid var(--border);border-right:1px solid var(--border);border-bottom:1px solid var(--border);box-shadow:var(--shadow);margin-bottom:14px}
.insight.warn{border-left-color:var(--red)}
.insight.good{border-left-color:var(--green)}
.insight h3{font-size:14.5px;margin-bottom:4px;color:var(--navy)}
.insight p{font-size:13.5px;color:#3c4757;max-width:96ch}
.insight .ev{font-size:12.5px;color:var(--muted);margin-top:5px}
.foot{margin-top:34px;font-size:12px;color:#8b95a3;border-top:1px solid var(--border);padding-top:14px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
.backbar{margin-bottom:14px}
.backbar a{display:inline-block;font-size:14px;font-weight:700;color:#fff;background:var(--navy);padding:10px 18px;border-radius:10px;text-decoration:none;box-shadow:var(--shadow);transition:background .15s}
.backbar a:hover{background:var(--navy2)}
.card.searching{border-color:#c9d6f0;box-shadow:0 0 0 3px rgba(47,95,208,.09),var(--shadow)}
.semhead{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-top:14px;padding-top:12px;border-top:2px solid var(--border)}
.semhead b{font-size:14px;color:var(--navy)}
.semclear{margin-left:auto;font:inherit;font-size:12px;font-weight:700;padding:5px 12px;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--muted);cursor:pointer}
.semclear:hover{background:#f2f6fc;color:var(--navy)}
.searchrow{display:flex;gap:8px}
.searchrow input{flex:1;font:inherit;font-size:14px;padding:10px 14px;border:1px solid var(--border);border-radius:10px;background:#fff}
.sem-btn{font:inherit;font-size:13.5px;font-weight:700;padding:10px 18px;border:0;border-radius:10px;background:var(--navy);color:#fff;cursor:pointer;transition:background .15s}
.sem-btn:hover{background:var(--navy2)}
.sem-list{margin-top:12px;display:flex;flex-direction:column}
.sem-hit{display:block;padding:9px 6px;border-bottom:1px solid #edf0f4;font-size:13.5px;color:var(--ink);text-decoration:none;border-radius:6px}
.sem-hit:hover{background:#f7f9fc}
.sem-hit:last-child{border-bottom:0}
.fgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px 18px}
.f .fl,.fl{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:#7b8694;font-weight:700}
.f .fv{font-size:13.5px;margin-top:2px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.tl{border-left:2px solid var(--border);margin-left:6px;padding-left:18px}
.tle{position:relative;padding:7px 0;font-size:13.5px}
.tle::before{content:"";position:absolute;left:-24px;top:14px;width:9px;height:9px;border-radius:50%;background:var(--accent);border:2px solid #fff}
.tle .tld{display:inline-block;min-width:126px;color:var(--muted)}
.acard{border:1px solid var(--border);border-radius:11px;padding:14px 16px;margin-top:12px;background:#fafbfd}
ul.kf{margin:6px 0 0 18px;font-size:13.5px}
ul.kf li{margin-bottom:5px}
@media print{.mast{background:#1c2a6e}.nav{display:none}}
"""
# fix accidental typos in CSS custom prop above
CSS = CSS.replace("--soft:#8property5;", "--soft:#8b95a3;").replace("fill:#44506033;fill:#445060", "fill:#445060").replace("fill:#44506\n0;fill:#445060", "fill:#445060")

TABLE_JS = """
function initTable(tid){
  const t=document.getElementById(tid), tb=t.querySelector('tbody');
  const q=document.getElementById(tid+'-q');
  const sels=[...document.querySelectorAll('[data-filter-for="'+tid+'"]')];
  const cnt=document.getElementById(tid+'-count');
  // Deep links: ?status=Open&fy=FY26/27&sev=High%20(Cat%201)&q=... preselect the filters
  const P=new URLSearchParams(location.search); let deep=false;
  sels.forEach(s=>{const v=P.get(s.dataset.key); if(v){ s.value=v; if(s.value===v) deep=true; }});
  if(P.get('q')&&q){q.value=P.get('q');deep=true;}
  function rows(){return [...tb.querySelectorAll('tr.row')];}
  function apply(){
    const needle=(q?q.value:'').toLowerCase().trim();
    let shown=0;
    rows().forEach(r=>{
      let ok=true;
      if(needle) ok=r.dataset.search.includes(needle);
      if(ok) for(const s of sels){const v=s.value; if(v && r.dataset[s.dataset.key]!==v){ok=false;break;}}
      r.style.display=ok?'':'none'; shown+=ok?1:0;
      const d=r.nextElementSibling;
      if(d&&d.classList.contains('det')) d.style.display='none';
      r.classList.remove('openrow');
    });
    if(cnt) cnt.textContent=shown+' shown';
  }
  const wipeSearch=()=>{const sr=document.getElementById('sem-res');
    if(sr&&sr.innerHTML){sr.innerHTML='';sr.parentElement.classList.remove('searching');
      const si=document.getElementById('sem-q'); if(si) si.value='';}};
  if(q) q.addEventListener('input',()=>{wipeSearch();apply();});
  sels.forEach(s=>s.addEventListener('change',()=>{wipeSearch();apply();}));
  rows().forEach(r=>{r.addEventListener('click',()=>{
    if(r.dataset.href) location.href=r.dataset.href;
  });});
  t.querySelectorAll('th[data-col]').forEach((th,i)=>{th.addEventListener('click',()=>{
    const idx=+th.dataset.col, num=th.dataset.num==='1', asc=th.dataset.asc!=='1';
    th.dataset.asc=asc?'1':'0';
    const pairs=rows().map(r=>[r,r.nextElementSibling&&r.nextElementSibling.classList.contains('det')?r.nextElementSibling:null]);
    pairs.sort((a,b)=>{let x=a[0].children[idx].dataset.v??a[0].children[idx].textContent,
      y=b[0].children[idx].dataset.v??b[0].children[idx].textContent;
      if(num){x=+x||0;y=+y||0;} return (x<y?-1:x>y?1:0)*(asc?1:-1);});
    pairs.forEach(p=>{tb.appendChild(p[0]); if(p[1])tb.appendChild(p[1]);});
  });});
  apply();
  if(deep){ t.closest('.card').scrollIntoView({behavior:'auto',block:'start'}); window.scrollBy(0,-80); }
}
"""

def year_pages(fykey):
    """The four pages of one year's section."""
    stem = FY_PAGE[fykey][:-5]  # fy-2026-27
    return {"dash": f"{stem}.html", "incidents": f"{stem}-incidents.html",
            "actions": f"{stem}-actions.html", "insights": f"{stem}-insights.html"}

def shell(title, body, active, sub="", fykey=None, subactive=None, wide=False):
    nav = [
        ("fy-2026-27.html", "This year"),
        ("fy-2025-26.html", "FY 2025/26"),
        ("fy-2024-25.html", "FY 2024/25"),
        ("fy-2023-24.html", "FY 2023/24"),
        ("overview.html", "All years"),
    ]
    # the top tab stays lit for every page inside its year
    links = "".join(
        f'<a href="/raw/{MK}/{h}"{" class=\"on\"" if h == active else ""}>{t}</a>'
        for h, t in nav)
    if fykey:
        yp = year_pages(fykey)
        sub_items = [(yp["dash"], "Dashboard"), (yp["incidents"], "Incidents"),
                     (yp["actions"], "Actions"), (yp["insights"], "Insights")]
        links += '</div><div class="nav subnav">' + "".join(
            f'<a href="/raw/{MK}/{h}"{" class=\"on\"" if k == subactive else ""}>{t}</a>'
            for (h, t), k in zip(sub_items, ["dash", "incidents", "actions", "insights"]))
    wrapcls = "wrap wide" if wide else "wrap"
    # Breadcrumbs are built from what the shell already knows about the page, so every page in the
    # register gets a real trail back to the Depot without each caller having to hand-write one.
    YLBL = {"FY26/27": "FY 2026/27", "FY25/26": "FY 2025/26",
            "FY24/25": "FY 2024/25", "FY23/24": "FY 2023/24"}
    SUBLBL = {"dash": "Dashboard", "incidents": "Incidents",
              "actions": "Actions", "insights": "Insights"}
    trail = [("Command Centre", "/"), ("Damage Depot", f"/m/{ui.HUB}")]
    if fykey:
        trail.append(("Damages", f"/m/{MK}"))
        yhref = f'/raw/{MK}/{year_pages(fykey)["dash"]}'
        if subactive and subactive != "dash":
            trail.append((YLBL.get(fykey, fykey), yhref))
            trail.append(SUBLBL.get(subactive, subactive))
        else:
            trail.append(YLBL.get(fykey, fykey))
    else:
        trail.append("Damages")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>{esc(title)} | Genny&#8217;s Damage Depot</title>
<style>{ui.CHROME}{CSS}</style>
</head>
<body>
{ui.navbar("damages")}
{ui.crumbs(*trail)}
{ui.hero(kicker=FY_LABEL.get(fykey, "The register"), title=esc(title), sub=sub,
         extra=f'''<div class="yearnav"><div class="nav">{links}</div></div>''')}
<div class="{wrapcls}">
{body}
<div class="foot"><span>Source: Depotnet Incident Manager exports (Incident Register + Action Report), imported {datetime.date.today().strftime('%-d %b %Y')}.</span><span>Prepared by Sygma Solutions.</span></div>
</div>
<script src="/clancy/genny-widget.js?v=20260731c" defer></script>
</body></html>"""

# ---------------------------------------------------------------- aggregations

def monthly_series(inc, fykey):
    c = Counter(r["month"] for r in inc if r["fy"] == fykey)
    return [c.get(m, 0) for m in FY_MONTHS[fykey]]

def kpis_html(cards):
    out = []
    for c in cards:
        cls = c.get("cls", "")
        delta = f'<div class="delta {c["dcls"]}">{c["delta"]}</div>' if c.get("delta") else ""
        inner = f'<div class="n {cls}">{c["n"]}</div>{delta}<div class="l">{c["l"]}</div>'
        if c.get("href"):
            out.append(f'<a class="kpi go" href="{c["href"]}">{inner}<div class="kpi-go">view →</div></a>')
        else:
            out.append(f'<div class="kpi">{inner}</div>')
    return f'<div class="kpis">{"".join(out)}</div>'

def sev_split(rows):
    c = Counter(r["sev"] for r in rows)
    return [(s, c.get(s, 0)) for s in ["High (Cat 1)", "Medium (Cat 2)", "Low (Cat 3)"]]

def util_split(rows):
    c = Counter(r["ugroup"] for r in rows)
    return [(u, c.get(u, 0)) for u in UTIL_ORDER]

def fam_split(rows):
    c = Counter(fam(r) for r in rows)
    items = [(f, c.get(f, 0)) for f in FAM_ORDER]
    other = sum(v for k, v in c.items() if k not in FAM_ORDER)
    if other:
        items.append(("Other", other))
    return [i for i in items if i[1]]

def status_split(rows):
    c = Counter(r["status"] or "Unstated" for r in rows)
    return sorted(c.items(), key=lambda x: -x[1])

def sev_pill(s):
    cls = "sev-h" if s.startswith("High") else "sev-m" if s.startswith("Medium") else "sev-l"
    return f'<span class="pill {cls}">{esc(s)}</span>'

def status_pill(s):
    cls = {"Open": "st-open", "Closed": "st-closed", "Overdue": "st-over"}.get(s, "st-out")
    return f'<span class="pill {cls}">{esc(s)}</span>'

# ---------------------------------------------------------------- incident table

def incident_table(inc, act_by_inc, tid, fy_filter=True, enrich=None):
    enrich = enrich or {}
    # There used to be an "unknown — not in our export" state here, on the theory that a damage
    # newer than the newest action we hold might have actions we cannot see. It was wrong: Pete,
    # 1 Aug 2026 — "there is no actions reports after this date on deponet, we are not missing
    # info". Both registers are imported to the same day. So no actions means no actions.
    fams = sorted({fam(r) for r in inc})
    utils = [u for u in UTIL_ORDER if any(r["ugroup"] == u for r in inc)]
    sevs = ["High (Cat 1)", "Medium (Cat 2)", "Low (Cat 3)"]
    stats = sorted({r["status"] or "" for r in inc})
    fys = [f for f in FYS[::-1] if any(r["fy"] == f for r in inc)]
    sel = []
    if fy_filter and len(fys) > 1:
        sel.append(('fy', 'FY', fys))
    sel += [('fam', 'Contract', fams), ('ugroup', 'Utility', utils), ('sev', 'Severity', sevs), ('status', 'Status', stats)]
    CAPOPTS = [("todo", "not captured yet"), ("part", "part captured"),
               ("missing", "tried — missing"), ("done", "fully captured")]
    ACTOPTS = [("open", "some still outstanding"), ("closed", "recorded, all closed"),
               ("no", "NONE recorded in Depotnet")]
    selects = "".join(
        f'<select data-filter-for="{tid}" data-key="{key}"><option value="">{lab}: all</option>' +
        "".join(f'<option>{esc(o)}</option>' for o in opts) + "</select>"
        for key, lab, opts in sel)
    selects += ('<select data-filter-for="' + tid + '" data-key="cap"><option value="">Captured: all</option>'
                + "".join(f'<option value="{v}">{esc(l)}</option>' for v, l in CAPOPTS) + "</select>")
    selects += ('<select data-filter-for="' + tid + '" data-key="acts"><option value="">Actions: all</option>'
                + "".join(f'<option value="{v}">{esc(l)}</option>' for v, l in ACTOPTS) + "</select>")
    rows_html = []
    for r in sorted(inc, key=lambda x: (x["d"] or ""), reverse=True):
        acts = act_by_inc.get(r["id"], [])
        search = " ".join(str(r.get(f) or "").lower() for f in
                          ["id", "location", "description", "contract", "raised_by", "subcontractor", "job_ref"])
        date_h = f'<td class="mono" data-v="{r["d"] or ""}">{r["d"] or "—"}</td>'
        overdue = sum(1 for a in acts if a["status"] == "Overdue")
        act_key = ("open" if overdue else "closed") if acts else "no"
        # Four DISTINCT states. "None outstanding" and "none recorded in Depotnet" are completely
        # different things and must never share a label (Pete, 31 Jul).
        if acts and overdue:
            act_h = (f'<span class="pill act-yes">{len(acts)} recorded</span>'
                     f' <span class="pill st-over">{overdue} still outstanding</span>')
        elif acts:
            act_h = (f'<span class="pill act-yes" title="Depotnet holds {len(acts)} corrective action(s) for this damage and all are closed">'
                     f'{len(acts)} recorded, all closed</span>')
        else:
            act_h = '<span class="pill act-no" title="Depotnet holds NO corrective action record for this damage at all. This is not the same as having none outstanding.">none recorded in Depotnet</span>'
        detail = f'/raw/{MK}/{year_pages(r["fy"])["dash"][:-5]}-damage.html?id={r["id"]}' if r["fy"] in FY_PAGE else ""
        ci, ca = r.get("capture_incident"), r.get("capture_actions")
        CI = {"full": ("cap-ok", "Incident PDF + investigation captured"),
              "no-investigation": ("cap-part", "PDF captured — Depotnet investigation is blank (not done)"),
              "missing": ("cap-bad", "Tried — could not retrieve the incident PDF")}
        CA = {"captured": ("cap-ok", "Action detail captured"),
              "none": ("cap-na", "Depotnet holds no actions for this damage"),
              "missing": ("cap-bad", "Tried — action detail could not be retrieved")}
        icls, itip = CI.get(ci, ("cap-none", "Not captured yet"))
        acls, atip = CA.get(ca, ("cap-none", "Not captured yet"))
        cap_key = ("done" if ci in ("full", "no-investigation") and ca in ("captured", "none")
                   else ("missing" if "missing" in (ci, ca) else ("part" if (ci or ca) else "todo")))
        cap_html = (f'<span class="caps"><i class="{icls}" title="Incident: {itip}">I</i>'
                    f'<i class="{acls}" title="Actions: {atip}">A</i></span>')
        en = enrich.get(r["id"])
        overdue_n = sum(1 for a in acts if a["status"] == "Overdue")
        if acts:
            act_b = (f'<span class="b warn">{len(acts)} · {overdue_n} overdue</span>' if overdue_n
                     else f'<span class="b yes">Yes · {len(acts)}</span>')
        else:
            act_b = '<span class="b no">None</span>'
        syg_b = '<span class="b yes">Yes</span>' if en else '<span class="b no">—</span>'
        # Outcome: Sygma summary first, else the latest corrective measure, else honest status
        if en and en.get("summary"):
            outcome = en["summary"]
        else:
            cms = [a["corrective_measure"] for a in acts if a.get("corrective_measure")]
            outcome = cms[-1] if cms else None
        learning = (en.get("key_findings") or [None])[0] if en else None
        ol = (f'<div class="ol"><div class="fl">Outcome</div><div class="t{"" if outcome else " muted"}">{esc(outcome) if outcome else "None recorded"}</div>'
              f'<div class="fl" style="margin-top:5px">Key learning</div><div class="t{"" if learning else " muted"}">{esc(learning) if learning else "None recorded"}</div></div>')
        rows_html.append(
            f'<tr class="row" data-href="{detail}" data-search="{esc(search)}" data-fy="{esc(r["fy"] or "")}" data-fam="{esc(fam(r))}" '
            f'data-ugroup="{esc(r["ugroup"])}" data-sev="{esc(r["sev"])}" data-status="{esc(r["status"] or "")}" data-cap="{cap_key}" data-acts="{act_key}">'
            f'<td class="mono" data-v="{r["id"]}">{r["id"]}<div class="small muted" data-v="{r["d"] or ""}">{r["d"] or "—"}</div></td>'
            f'<td>{esc(fam(r))}<div class="small muted">{esc(r["contract"] or "")}</div></td>'
            f'<td style="min-width:150px">{esc((r["location"] or "—")[:70])}</td>'
            f'<td style="min-width:200px"><div class="clamp2 small" style="font-size:13px">{esc((r["description"] or "—")[:220])}</div></td>'
            f'<td><span class="pill uc">{esc(r["ugroup"])}</span></td>'
            f'<td data-v="{["High","Medium","Low"].index(r["sev"].split(" ")[0]) if r["sev"].split(" ")[0] in ["High","Medium","Low"] else 3}">{sev_pill(r["sev"])}</td>'
            f'<td>{status_pill(r["status"] or "—")}</td>'
            f'<td data-v="{len(acts)}">{act_b}</td>'
            f'<td data-v="{1 if en else 0}">{syg_b}</td>'
            f'<td data-v="{ {"done":0,"part":1,"missing":2,"todo":3}[cap_key] }">{cap_html}</td>'
            f'<td style="min-width:230px">{ol}</td></tr>')
    return f"""
<div class="filters"><input type="search" id="{tid}-q" placeholder="Search location, description, ID…">{selects}<span class="count" id="{tid}-count"></span></div>
<div class="card" style="padding:6px 10px;overflow-x:auto"><table id="{tid}"><thead><tr>
<th data-col="0" data-num="1">ID / Date <span class="arr">↕</span></th>
<th data-col="1">Contract <span class="arr">↕</span></th><th data-col="2">Location <span class="arr">↕</span></th>
<th data-col="3">What happened <span class="arr">↕</span></th>
<th data-col="4">Utility <span class="arr">↕</span></th><th data-col="5" data-num="1">Severity <span class="arr">↕</span></th>
<th data-col="6">Status <span class="arr">↕</span></th><th data-col="7" data-num="1">Action? <span class="arr">↕</span></th>
<th data-col="8" data-num="1">Sygma? <span class="arr">↕</span></th>
<th data-col="9" data-num="1">Captured <span class="arr">↕</span></th><th data-col="10">Outcome &amp; learning</th>
</tr></thead><tbody>{"".join(rows_html)}</tbody></table></div>
<p class="small muted" style="margin-top:8px">Click any row to open the damage in full — every Depotnet field, the timeline, its corrective actions and any Sygma material. "None recorded" is honest: it means neither Depotnet nor Sygma holds an outcome or learning for that damage yet.</p>
<script>{TABLE_JS}initTable("{tid}");</script>"""

# ---------------------------------------------------------------- semantic search partial

FY_STEM_JS = json.dumps({f: FY_PAGE[f][:-5] for f in FYS})

def search_box(fy=None):
    """Hybrid search: an exact hit on a place, job ref or ID wins outright and is found in ANY
    year (labelled when it is outside the year you are in — the 'south drove' miss, 31 Jul);
    meaning-matches stay inside the year you are viewing and are labelled as related."""
    fyq = f",fy:{json.dumps(fy)}" if fy else ""
    scope = "this year" if fy else "every year"
    return f"""
<div class="card" style="padding:14px 18px;margin-bottom:18px">
 <div class="searchrow">
  <input type="search" id="sem-q" placeholder="Search a place, job ref, ID — or describe it: 'hit a gas main breaking out concrete'">
  <button id="sem-go" class="sem-btn">Search</button>
 </div>
 <div id="sem-res"></div>
</div>
<script>
(function(){{
 const STEM={FY_STEM_JS};
 const box=document.getElementById('sem-res'), inp=document.getElementById('sem-q');
 const LBL={{'FY26/27':'FY 2026/27','FY25/26':'FY 2025/26','FY24/25':'FY 2024/25','FY23/24':'FY 2023/24'}};
 async function go(){{
  const q=inp.value.trim(); if(!q){{box.innerHTML='';return;}}
  box.innerHTML='<p class="small muted" style="margin-top:10px">Searching…</p>';
  try{{
   const r=await fetch('/api/clancy-dn-search',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{q:q{fyq}}})}});
   if(!r.ok) throw 0;
   const d=await r.json();
   const res=d.results||[];
   if(!res.length){{box.innerHTML='<div class="semhead"><b>No match for &ldquo;'+q.replace(/[<>]/g,'')+'&rdquo;</b><span class="small muted"> — nothing in {scope} matches by name or meaning</span><button class="semclear" onclick="this.closest(\'.card\').querySelector(\'#sem-res\').innerHTML=\'\';this.closest(\'.card\').classList.remove(\'searching\')">Clear</button></div>';box.parentElement.classList.add('searching');return;}}
   const exact=res.filter(x=>x.match!=='meaning'), rel=res.filter(x=>x.match==='meaning');
   const row=x=>{{
    const stem=STEM[x.fy]||'fy-2026-27';
    const yr=('{fy or ""}'&&x.fy!=='{fy or ""}')?'<span class="b warn" style="margin-left:6px">'+(LBL[x.fy]||x.fy)+'</span>':'';
    const act=x.kind==='action'?'<span class="b no" style="margin-left:6px">action</span>':'';
    return '<a class="sem-hit" href="/raw/{MK}/'+stem+'-damage.html?id='+x.incident_id+'">'
      +'<span class="mono">'+x.incident_id+'</span> · '+(x.incident_date||'')+' · <b>'+(x.contract||'')+'</b> · '
      +(x.location||'').slice(0,55)+yr+act
      +'<span class="small muted" style="display:block">'+(x.snippet||'').slice(0,150)+'…</span></a>';
   }};
   let h='<div class="semhead"><b>Search results for &ldquo;'+q.replace(/[<>]/g,'')+'&rdquo;</b>'
     +'<span class="small muted"> — these are search hits, not the filtered list below</span>'
     +'<button class="semclear" id="sem-clear">Clear</button></div>';
   if(exact.length) h+='<div class="fl" style="margin-top:10px">Exact matches</div><div class="sem-list">'+exact.map(row).join('')+'</div>';
   if(rel.length) h+='<div class="fl" style="margin-top:'+(exact.length?'14px':'10px')+'">Related by meaning ({scope})</div><div class="sem-list">'+rel.map(row).join('')+'</div>';
   box.innerHTML=h;
   box.parentElement.classList.add('searching');
   document.getElementById('sem-clear').addEventListener('click',()=>{{box.innerHTML='';inp.value='';box.parentElement.classList.remove('searching');}});
  }}catch(e){{box.innerHTML='<p class="small muted" style="margin-top:10px">Search is unavailable right now.</p>';}}
 }}
 document.getElementById('sem-go').addEventListener('click',go);
 inp.addEventListener('keydown',e=>{{if(e.key==='Enter')go();}});
}})();
</script>"""

# ---------------------------------------------------------------- per-damage detail page

def fy_detail_page(inc, act, enrich, files_by_inc, answers_by_inc, fykey):
    """One page per year that renders ANY of the year's damages in full from embedded JSON
    (?id=N): every register field, every action in full, the timeline, and the Sygma layer."""
    label = FY_LABEL[fykey]
    yp = year_pages(fykey)
    rows = [r for r in inc if r["fy"] == fykey]
    year_ids = {r["id"] for r in rows}
    acts = [a for a in act if a["incident_id"] in year_ids]
    qtext, qmap = [], {}
    def qidx(q):
        if q not in qmap:
            qmap[q] = len(qtext); qtext.append(q)
        return qmap[q]

    data = {
        "incidents": {str(r["id"]): r for r in rows},
        "actions": defaultdict(list),
        "enrich": {str(k): v for k, v in enrich.items() if k in year_ids},
        "files": {str(k): v for k, v in files_by_inc.items() if k in year_ids},
        # Answers are the bulk of this page: 164 damages x ~87 questions, with the SAME question
        # text repeated on every damage — 2.45 MB of 4.07 MB, and a 413 Payload Too Large from
        # Supabase. The text is deduped into QTEXT and each answer references it by index; the
        # page rehydrates on load. Same data, roughly a quarter of the bytes.
        "answers": {str(k): [[qidx(a["question"]), a["answer"],
                              1 if a.get("mandatory") else 0,
                              1 if a.get("answered") else 0,
                              a["section"]]
                             for a in v]
                    for k, v in answers_by_inc.items() if k in year_ids},
        "capfolder": {str(r["id"]): r.get("capture_drive_folder") for r in rows
                      if r.get("capture_drive_folder")},
    }
    for a in acts:
        data["actions"][str(a["incident_id"])].append(a)
    data["actions"] = dict(data["actions"])
    data["qtext"] = qtext
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    body = f"""
<div class="backbar"><a href="/raw/{MK}/{yp["incidents"]}" id="backbtn">&larr; Back to the {label} list</a></div>
{search_box(fykey)}
<div id="dmg"></div>
<script>
(function(){{const b=document.getElementById('backbtn');
 if(document.referrer&&new URL(document.referrer).origin===location.origin&&history.length>1){{
  b.addEventListener('click',e=>{{e.preventDefault();history.back();}});
  b.innerHTML='&larr; Back to your place in the list';}}
}})();
</script>
<script>
const D={payload};
const FLD=[["id","Depotnet ID"],["incident_date","Incident date"],["date_raised","Logged on Depotnet"],
 ["category","Category"],["contract","Contract"],["contract_family","Contract group"],
 ["contract_number","Contract number"],["workstream","Workstream"],["business_unit","Business unit"],
 ["job_id","Job ID"],["job_ref","Job ref"],["location","Location"],["severity","Severity"],
 ["status","Status"],["raised_by","Raised by"],["subcontractor","Subcontractor"]];
// closed_at / closed_by / location exist ONLY behind the action's View modal on Depotnet —
// never in the Action Report export. Seven of this year's nine actions read "Closed" with no
// closure detail at all until they were captured on 1 Aug 2026.
const AFLD=[["id","Action ID"],["date_raised","Raised"],["raised_by","Raised by"],["due_date","Due"],
 ["assigned_to","Assigned to"],["status","Status"],["closed_at","Closed"],["closed_by","Closed by"],
 ["location","Location"],["incident_status","Incident status at export"],
 ["severity","Severity"],["action_classification","Classification"],["question","Question"]];
function fmtTs(v){{if(!v)return null;const s=String(v);return s.slice(0,10)+(s.length>10?' '+s.slice(11,16):'');}}
function pill(t,cls){{return '<span class="pill '+cls+'">'+t+'</span>';}}
function statusPill(s){{const m={{Open:'st-open',Closed:'st-closed',Overdue:'st-over'}};return pill(s||'—',m[s]||'st-out');}}
function render(){{
 const id=new URLSearchParams(location.search).get('id');
 const el=document.getElementById('dmg');
 const r=D.incidents[id];
 if(!r){{el.innerHTML='<div class="card"><p>Pick a damage from <a href="/raw/{MK}/{yp["incidents"]}">the {label} register</a> — or search above.</p></div>';return;}}
 document.title='Damage '+id+' — '+(r.location||'')+' | Depotnet Damages';
 const acts=D.actions[id]||[], en=D.enrich[id];
 let h='<div class="card" style="margin-bottom:16px"><div class="h2row"><h2>Damage '+id+' — '+(r.location||'location not stated')+'</h2>'
   +'<span>'+statusPill(r.status)+' '+pill(r.severity||'—',(r.severity||'').startsWith('HIGH')?'sev-h':(r.severity||'').startsWith('MEDIUM')?'sev-m':'sev-l')+'</span></div>'
   +'<p class="det desc" style="white-space:pre-wrap;max-width:96ch">'+(r.description||'No description recorded.')+'</p>'
   +'<div class="legend" style="margin-top:12px"><span class="lg">Utility: <b>'+(r.utility_class||'Unclassified')+'</b>'+(r.utility_confirmed?'':' (auto-read)')+'</span></div></div>';
 // every register field, verbatim
 h+='<div class="card" style="margin-bottom:16px"><div class="h2row"><h2>Everything Depotnet holds</h2><span class="note">Incident Register row, in full</span></div><div class="fgrid">'
   +FLD.map(([k,l])=>'<div class="f"><div class="fl">'+l+'</div><div class="fv">'+((k.includes('date')?fmtTs(r[k]):r[k])||'—')+'</div></div>').join('')+'</div></div>';
 // The investigation, one box per area. Pete, 31 Jul: these are the fields the whole damage
 // argument turns on, so they get their own section rather than a line in a field grid — and an
 // ABSENCE has to be as visible as a value, because on 45% of this year's damages there is none.
 const INV=[
   ["Root cause","root_cause"],
   ["Underlying cause","underlying_cause"],
   ["Investigation summary","incident_summary"],
   ["Lessons learnt","lessons_learnt"]
 ];
 const blank = r.capture_incident==='no-investigation';
 const anyInv = INV.some(([,k])=>r[k]);
 h+='<div class="card" style="margin-bottom:16px"><div class="h2row"><h2>Conclusions and lessons</h2>'
   +'<span class="note">'+(blank?'investigation report section not completed'
       :(anyInv?'from the investigation report section':'nothing recorded'))+'</span></div>';
 if(blank){{
   h+='<div class="invbox invnone"><b>The investigation report section has not been completed.</b> '
     +'The incident report is complete; the Report tab on Depotnet is empty. That is what the '
     +'record shows &mdash; it does not tell us whether anyone looked into the damage.</div>';
 }}
 INV.forEach(([lab,k])=>{{
   const v=(r[k]||'').toString().trim();
   const thin = v && v.length<=25;
   h+='<div class="invbox'+(v?(thin?' invthin':''):' invnone')+'">'
     +'<div class="invl">'+lab+'</div>'
     +(v?'<div class="invv">'+v.replace(/,/g,', ')+'</div>'
        +(thin?'<div class="invnote">Recorded, but that is all of it &mdash; '+v.length+' characters.</div>':'')
       :'<div class="invv muted">Not recorded on Depotnet.</div>')
     +'</div>';
 }});
 h+='</div>';
 // The full investigation, question by question — captured since the deep-capture began and never
 // shown until now. Grouped by section, blank answers dropped at load.
 // We now hold the FULL question set, including the ones nobody answered — the API returns
 // answer:null where the PDF simply omitted the row. So a blank required field is visible as a
 // blank required field, instead of being indistinguishable from a question never asked.
 const QT=D.qtext||[];
 const ans=((D.answers||{{}})[id]||[]).map(function(a){{
   return {{question:QT[a[0]], answer:a[1], mandatory:!!a[2], answered:!!a[3], section:a[4]}};
 }});
 const nAns=ans.filter(a=>a.answered).length, nBlank=ans.length-nAns;
 const nReqBlank=ans.filter(a=>a.mandatory&&!a.answered).length;
 h+='<div class="card" style="margin-bottom:16px"><div class="h2row"><h2>The investigation in full</h2>'
   +'<span class="note">'+(ans.length
      ? nAns+' of '+ans.length+' questions answered'+(nReqBlank?' \u00b7 '+nReqBlank+' required field'+(nReqBlank==1?'':'s')+' blank':'')
      : 'nothing captured')+'</span></div>';
 if(!ans.length){{
   h+='<p class="small muted">Nothing captured for this damage yet.</p>';
 }} else {{
   const SEC={{questions:'Incident report',investigation:'Investigation report section'}};
   const secs={{}}; ans.forEach(a=>{{(secs[a.section]=secs[a.section]||[]).push(a);}});
   ['questions','investigation'].forEach(sec=>{{
     const rows=secs[sec]; if(!rows||!rows.length) return;
     const ok=rows.filter(a=>a.answered).length;
     h+='<div class="fl" style="margin-top:14px">'+(SEC[sec]||sec)
       +' <span class="muted" style="font-weight:400">'+ok+' of '+rows.length+' answered</span></div>';
     if(!ok){{
       h+='<p class="small muted">Every one of these '+rows.length+' questions is blank on Depotnet.'
         +' Listed below so you can see exactly what was asked and never filled in.</p>';
     }}
     h+='<div class="qa">'+rows.map(a=>
        '<div class="qarow"><div class="qaq">'+a.question
          +(a.mandatory?' <span class="muted" title="Depotnet marks this required">*</span>':'')+'</div>'
        +'<div class="qaa'+(a.answered?'':' muted')+'">'
          +(a.answered?a.answer:'<i>blank'+(a.mandatory?' \u2014 required':'')+'</i>')+'</div></div>').join('')
       +'</div>';
   }});
 }}
 h+='</div>';
 // ── Timeline ────────────────────────────────────────────────────────────────────────
 // Depotnet's OWN audit trail, not our reconstruction from date fields. It is the only record
 // of amendments, reopenings, uploads and chasers — e.g. "Is the investigation complete?:
 // changed from 'No' to 'Yes'". Falls back to the derived version where we have not captured it.
 const dtl=r.timeline||[];
 if(dtl.length){{
  const ICON={{'Action Created':'+','Action Closed':'\u2713','Action Reopened':'\u21ba',
    'Action Amended':'\u270e','Report Amended':'\u270e','Report Submitted':'\u2713',
    'Document Uploaded':'\u2191','Photo Uploaded':'\u2191','Video Uploaded':'\u2191',
    'Photo Deleted':'\u2717','Action Reminder Email Sent':'\u2709','Action Assigned Email Sent':'\u2709'}};
  const KEY=['Action Reopened','Report Submitted','Action Closed','Action Created'];
  h+='<div class="card" style="margin-bottom:16px"><div class="h2row"><h2>Timeline</h2>'
    +'<span class="note">Depotnet\u2019s own audit trail \u00b7 '+dtl.length+' entries</span></div>'
    +'<div class="tl">'+dtl.map(function(e){{
      const k=KEY.indexOf(e.what)>=0?' style="font-weight:700"':'';
      return '<div class="tle"><span class="mono tld">'+fmtTs(e.at)+'</span>'
        +'<b'+k+'>'+(ICON[e.what]||'\u00b7')+' '+(e.what||'\u2014')+'</b> '
        +'<span class="muted small">'+(e.by||'')+'</span>'
        +(e.detail?'<div class="small muted" style="margin:2px 0 0 96px;white-space:pre-wrap">'
            +e.detail.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</div>':'')
        +(e.file?'<div class="small muted" style="margin:2px 0 0 96px">file: '+e.file+'</div>':'')
        +'</div>';}}).join('')+'</div></div>';
 }} else {{
  const ev=[];
  if(r.incident_date)ev.push([r.incident_date,'Damage occurred','']);
  if(r.date_raised)ev.push([r.date_raised,'Logged on Depotnet','by '+(r.raised_by||'\u2014')]);
  acts.forEach(a=>{{
   if(a.date_raised)ev.push([a.date_raised,'Action '+a.id+' raised','assigned to '+((a.assigned_to||'\u2014').split(' (')[0])]);
   if(a.due_date)ev.push([a.due_date,'Action '+a.id+' due',a.status==='Overdue'?'STILL OVERDUE':(a.status||'')]);
  }});
  ev.sort((x,y)=>String(x[0]).localeCompare(String(y[0])));
  h+='<div class="card" style="margin-bottom:16px"><div class="h2row"><h2>Timeline</h2>'
    +'<span class="note">derived from the dates we hold \u2014 Depotnet\u2019s own trail not captured for this damage</span></div><div class="tl">'
    +ev.map(e=>'<div class="tle"><span class="mono tld">'+fmtTs(e[0])+'</span><b>'+e[1]+'</b> <span class="muted small">'+e[2]+'</span></div>').join('')+'</div></div>';
 }}
 // actions in full
 h+='<div class="card" style="margin-bottom:16px"><div class="h2row"><h2>Corrective actions'+(acts.length?' ('+acts.length+')':'')+'</h2>'
   +'<span class="note">'+(acts.length?'Action Report rows, in full':'')+'</span></div>';
 if(!acts.length){{h+='<p class="small muted">None — no row in the Action Report references this damage.</p>';}}
 else acts.forEach(a=>{{
  h+='<div class="acard"><div class="h2row"><b>'+((a.assigned_to||'Unassigned').split(' (')[0])+'</b><span>'+statusPill(a.status)+'</span></div>'
    +'<div class="fgrid">'+AFLD.map(([k,l])=>{{
        // closed_at has no 'date' in its name but is very much a timestamp — matching on the
        // field NAME printed a raw 2026-07-08T12:29:59.191378+00:00 on every closed action.
        const raw=a[k];
        const v=(k.includes('date')||k.includes('_at'))?fmtTs(raw):raw;
        return v?'<div class="f"><div class="fl">'+l+'</div><div class="fv">'+v+'</div></div>':'';}}).join('')+'</div>'
    +(a.description?'<div class="fl" style="margin-top:8px">What was asked</div><div class="det desc">'+a.description+'</div>':'')
    +(a.corrective_measure?'<div class="fl" style="margin-top:8px">What was done</div><div class="det desc">'+a.corrective_measure+'</div>':'<div class="small muted" style="margin-top:8px">No corrective measure recorded.</div>')
    // The evidence an action was CLOSED on — permits, briefings, RAMS. It used to fall into the
    // general incident file pile, where a permit that closed one specific action read as a loose
    // incident document.
    +(function(){{
      const af=((D.files||{{}})[id]||[]).filter(f=>String(f.action_id)===String(a.id));
      if(!af.length) return '';
      return '<div class="fl" style="margin-top:10px">Evidence attached to this action ('+af.length+')</div>'
        +'<div class="small">'+af.map(f=>f.drive_id
          ?'<a href="https://drive.google.com/file/d/'+f.drive_id+'/view" target="_blank" rel="noopener">'+f.name+'</a>'
          :'<span class="muted">'+f.name+' <span style="opacity:.7">(on Depotnet, not yet in Drive)</span></span>').join(' &middot; ')+'</div>';
    }})()
    +'</div>';
 }});
 h+='</div>';
 // Everything pulled off Depotnet for this damage. Kept separate from the Sygma layer below
 // because they are two different Drive folders: this one is Depotnet's own attachments, that one
 // is the panel-review material. Showing only one of them is what made the record look empty.
 const FKIND={{pdf:['Incident record','the full Depotnet PDF'],photo:['Photos','site photographs attached on Depotnet'],document:['Documents','everything else attached to the incident']}};
 const fls=(D.files||{{}})[id]||[];
 const capf=(D.capfolder||{{}})[id];
 h+='<div class="card"><div class="h2row"><h2>Captured from Depotnet</h2><span class="note">'
   +(fls.length?fls.length+' file'+(fls.length==1?'':'s')+' held in Drive':'nothing captured yet')+'</span></div>';
 if(fls.length){{
  ['pdf','photo','document'].forEach(k=>{{
   const g=fls.filter(f=>f.kind===k); if(!g.length)return;
   h+='<div class="fl" style="margin-top:10px">'+FKIND[k][0]+' <span class="muted" style="font-weight:400">'+FKIND[k][1]+'</span></div>';
   // A row with no drive_id is a file we know the NAME of but do not hold. Rendering it as a link
   // gives a click that goes nowhere, which is worse than not listing it. Reported live 31 Jul 2026
   // after a migration carried 15 name-only entries in from the old Sygma records.
   h+='<ul class="kf">'+g.map(f=>f.drive_id
     ? '<li><a href="https://drive.google.com/file/d/'+f.drive_id+'/view" target="_blank" rel="noopener">'+f.name+'</a></li>'
     : '<li>'+f.name+' <span class="muted">(referenced, not held)</span></li>').join('')+'</ul>';
  }});
  if(capf)h+='<div class="legend" style="margin-top:12px"><span class="lg"><a href="'+capf+'">Open the Depotnet capture folder in Drive</a></span></div>';
 }} else h+='<p class="small muted">The Depotnet record for this damage has not been captured yet, so no PDF, photos or documents are held.</p>';
 h+='</div>';
 // Sygma layer
 h+='<div class="card"><div class="h2row"><h2>Sygma material</h2><span class="note">panel reviews, findings, documents</span></div>';
 if(en){{
  h+=(en.summary?'<p class="det desc">'+en.summary+'</p>':'');
  if(en.key_findings&&en.key_findings.length)h+='<div class="fl" style="margin-top:10px">Key findings</div><ul class="kf">'+en.key_findings.map(k=>'<li>'+k+'</li>').join('')+'</ul>';
  if(en.next_actions&&en.next_actions.length)h+='<div class="fl" style="margin-top:10px">Agreed next actions</div><ul class="kf">'+en.next_actions.map(k=>'<li>'+k+'</li>').join('')+'</ul>';
  const links=[];
  // ONE Drive link per damage. The panel-review material now lives INSIDE the damage folder
  // (moved 31 Jul 2026) — two links meant clicking the wrong one and finding transcripts where
  // you expected the incident PDF, which is exactly what happened.
  
  if(en.report_url)links.push('<a href="'+en.report_url+'">Report</a>');
  if(links.length)h+='<div class="legend" style="margin-top:10px">'+links.map(l=>'<span class="lg">'+l+'</span>').join('')+'</div>';
  h+='<p class="small muted" style="margin-top:8px">Sygma status: '+(en.status||'—')+(en.stage_note?' · '+en.stage_note:'')+'</p>';
 }} else h+='<p class="small muted">Nothing linked yet — panel reviews, findings and documents appear here once Sygma material is tied to this damage.</p>';
 h+='</div>';
 el.innerHTML=h;
}}
render();
</script>"""
    return shell(f"Damage detail — {label}", body, FY_PAGE[fykey],
                 f"{label} · click-through detail: the full Depotnet record, timeline, actions and Sygma material",
                 fykey=fykey, subactive="incidents")

# ---------------------------------------------------------------- pages

def fy_dashboard(inc, act, fykey, full=True):
    rows = [r for r in inc if r["fy"] == fykey]
    prior_key = FYS[FYS.index(fykey) - 1] if FYS.index(fykey) > 0 else None
    prior_rows = [r for r in inc if r["fy"] == prior_key] if prior_key else []
    fy_act = [a for a in act if a["incident_id"] in {r["id"] for r in rows}]
    with_actions = len({a["incident_id"] for a in fy_act})
    overdue = sum(1 for a in fy_act if a["status"] == "Overdue")
    open_n = sum(1 for r in rows if r["status"] == "Open")
    high = sum(1 for r in rows if r["sev"].startswith("High"))
    mser = monthly_series(inc, fykey)
    pser = monthly_series(inc, prior_key) if prior_key else None
    # same-period comparison for the running year
    today = datetime.date.today()
    yp = year_pages(fykey)
    def link(**params):
        base = f"/raw/{MK}/{yp['incidents']}"
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        return base + ("?" + qs if qs else "")
    cards = [dict(n=len(rows), l="service damages recorded", href=link())]
    if fykey == "FY26/27" and prior_key:
        months_elapsed = [m for m in FY_MONTHS[fykey] if m <= today.strftime("%Y-%m")]
        same = sum(Counter(r["month"] for r in prior_rows).get(m.replace("2026", "2025").replace("2027", "2026"), 0)
                   for m in months_elapsed)
        cur = len([r for r in rows if r["month"] in months_elapsed])
        if same:
            pct = (cur - same) / same * 100
            cards.append(dict(n=f"{pct:+.0f}%", cls="green" if pct < 0 else "red",
                              l=f"vs same months last year ({cur} v {same}, Apr–{today.strftime('%b')})",
                              delta=("improving" if pct < 0 else "worsening"), dcls="down" if pct < 0 else "up"))
    cards += [
        dict(n=open_n, cls="amber" if open_n else "", l="still open", href=link(status="Open")),
        dict(n=high, cls="red" if high else "", l="High (Cat 1)", href=link(sev="High (Cat 1)")),
        dict(n=f"{with_actions}/{len(rows)}", l="damages with corrective actions",
             href=f"/raw/{MK}/{yp['actions']}"),
        dict(n=overdue, cls="red" if overdue else "green", l="actions overdue right now",
             href=f"/raw/{MK}/{yp['actions']}?status=Overdue"),
    ]
    label = FY_LABEL[fykey]
    body = [search_box(fykey), kpis_html(cards)]
    # The "x/y damages with corrective actions" card reads like a broken import unless the page
    # says what it is. It is not: every action Depotnet holds for a service damage is in here and
    # linked. The ratio is low because actions stopped being raised, and after the last one was
    # raised no damage has had one at all. Every figure in this note is read from the data, so it
    # moves the moment a new action lands.
    raised = [a["date_raised"] for a in act if a.get("date_raised")]
    if raised:
        last_raised = max(raised)
        after = [r for r in rows if r["incident_date"] and str(r["incident_date"])[:10] > str(last_raised)[:10]]
        nice = datetime.datetime.strptime(str(last_raised)[:10], "%Y-%m-%d").strftime("%-d %B %Y")
        body.append(
            f'<div class="card" style="margin-bottom:16px;border-left:4px solid #b45309">'
            f'<p class="small"><b>Why only {with_actions} of {len(rows)}.</b> Every corrective '
            f'action Depotnet holds against a service damage is in here and linked to its damage: '
            f'{len(act)} of them, none unmatched. The ratio is low because actions stopped '
            f'being raised. The last one on any service damage was raised on <b>{nice}</b>, and '
            f'the {len(after)} damages logged since then carry none at all. This was checked '
            f'against Depotnet directly with every filter cleared, so it is a gap in the process, '
            f'not a gap in what we imported.</p></div>')
    prior_leg = f'<div class="legend"><span class="lg"><i style="background:#97D700"></i>{label}</span>' + \
                (f'<span class="lg"><i style="background:#b6c3e8"></i>{FY_LABEL[prior_key]} (same month)</span>' if prior_key else "") + "</div>"
    body.append(f'<div class="card"><div class="h2row"><h2>Damages by month</h2><span class="note">financial year, April to March</span></div>'
                + vbar_months(mser, pser, label=label, prior_label=FY_LABEL.get(prior_key, "")) + prior_leg + "</div>")
    fams = fam_split(rows)
    utils = util_split(rows)
    sevs = sev_split(rows)
    body.append('<div class="grid c3" style="margin-top:16px">')
    body.append(f'<div class="card"><div class="h2row"><h2>By contract</h2></div>{hbar(fams)}</div>')
    body.append(f'<div class="card"><div class="h2row"><h2>By utility hit</h2><span class="note">auto-read from descriptions</span></div>{donut([u for u in utils if u[1]], UTIL_COLORS)}{legend(utils, UTIL_COLORS)}</div>')
    body.append(f'<div class="card"><div class="h2row"><h2>By severity</h2></div>{donut([s for s in sevs if s[1]], SEV_COLORS)}{legend(sevs, SEV_COLORS)}</div>')
    body.append('</div>')
    # subcontractor + top towns
    subs = Counter((r["subcontractor"] or "Clancy direct") for r in rows).most_common(8)
    towns = Counter()
    for r in rows:
        loc = (r["location"] or "")
        m = re.search(r"([A-Za-z ]+?),?\s*[A-Z]{1,2}\d{1,2}[A-Z]?(\s?\d[A-Z]{2})?\s*$", loc)
        towns[(m.group(1).strip().title() if m else (loc.split(",")[-1].strip().title() or "Unstated"))[:28] or "Unstated"] += 1
    body.append('<div class="grid c2">')
    body.append(f'<div class="card"><div class="h2row"><h2>Delivered by</h2><span class="note">subcontractor on the incident record</span></div>{hbar(subs, color="#64748b")}</div>')
    body.append(f'<div class="card"><div class="h2row"><h2>Most-hit places</h2></div>{hbar(towns.most_common(8), color="#0e9594")}</div>')
    body.append('</div>')
    body.append(f"""<div class="grid c3" style="margin-top:20px">
<a class="fyt" href="/raw/{MK}/{yp['incidents']}"><div class="y">Incidents</div><div class="n">{len(rows)}</div><div class="s">every {label} damage — searchable, expandable</div></a>
<a class="fyt" href="/raw/{MK}/{yp['actions']}"><div class="y">Actions</div><div class="n">{len({a['incident_id'] for a in fy_act})} of {len(rows)}</div><div class="s">damages with any action raised &middot; {len(fy_act)} action lines, {overdue} overdue</div></a>
<a class="fyt" href="/raw/{MK}/{yp['insights']}"><div class="y">Insights</div><div class="n">{label.split()[-1]}</div><div class="s">trends, improvements, capture quality</div></a>
</div>""")
    sub = f"{label} · {len(rows)} service damages across {len(fams)} contract groups"
    return shell(f"Service damages — {label}", "\n".join(body), FY_PAGE[fykey], sub,
                 fykey=fykey, subactive="dash")

def hub(inc, act):
    today = datetime.date.today()
    cur = [r for r in inc if r["fy"] == "FY26/27"]
    prior = [r for r in inc if r["fy"] == "FY25/26"]
    months_elapsed = [m for m in FY_MONTHS["FY26/27"] if m <= today.strftime("%Y-%m")]
    same = sum(Counter(r["month"] for r in prior).get(m.replace("2026", "2025").replace("2027", "2026"), 0) for m in months_elapsed)
    curn = len([r for r in cur if r["month"] in months_elapsed])
    overdue = sum(1 for a in act if a["status"] == "Overdue")
    open_n = sum(1 for r in inc if r["status"] == "Open")
    pct = (curn - same) / same * 100 if same else 0
    cur_open = sum(1 for r in cur if r["status"] == "Open")
    cur_ids = {r["id"] for r in cur}
    cur_overdue = sum(1 for a in act if a["status"] == "Overdue" and a["incident_id"] in cur_ids)
    cards = [
        dict(n=len(inc), l="service damages on the Depotnet register (Apr 2023 → today) — pick a year below"),
        dict(n=len(cur), l="so far this financial year (from 1 Apr 2026)",
             href=f"/raw/{MK}/fy-2026-27-incidents.html"),
        dict(n=f"{pct:+.0f}%", cls="green" if pct < 0 else "red",
             l=f"vs the same months last year ({curn} v {same})",
             href=f"/raw/{MK}/fy-2026-27-insights.html"),
        dict(n=cur_open, cls="amber", l="still open THIS YEAR",
             href=f"/raw/{MK}/fy-2026-27-incidents.html?status=Open"),
        dict(n=cur_overdue, cls="red" if cur_overdue else "green", l="actions overdue THIS YEAR",
             href=f"/raw/{MK}/fy-2026-27-actions.html?status=Overdue"),
    ]
    doors = f"""
<div class="doors">
<a class="door" href="/raw/{MK}/fy-2026-27.html">
  <div class="t">Damages</div>
  <div class="d">The full service-damage record, straight from Depotnet's Incident Manager — dashboards for each financial year, every incident, every corrective action.</div>
  <div class="go">Open this year's dashboard →</div>
</a>
<a class="door dive" href="/m/clancy-genny-cat-reviews">
  <div class="t">Data Dive — Genny &amp; CAT reviews</div>
  <div class="d">The deep dive into Depotnet's Genny &amp; CAT inspection data: findings, inspection registers, operative coverage and the people pages.</div>
  <div class="go">Open the Depotnet Review →</div>
</a>
</div>"""
    tiles = []
    for f in FYS[::-1]:
        n = sum(1 for r in inc if r["fy"] == f)
        cls = " cur" if f == "FY26/27" else ""
        note = "running year — from 1 Apr 2026" if f == "FY26/27" else ("contract year before" if f == "FY25/26" else "earlier year")
        yp = year_pages(f)
        sub_links = (f'<div class="s" style="margin-top:8px"><a href="/raw/{MK}/{yp["dash"]}">Dashboard</a> · '
                     f'<a href="/raw/{MK}/{yp["incidents"]}">Incidents</a> · '
                     f'<a href="/raw/{MK}/{yp["actions"]}">Actions</a> · '
                     f'<a href="/raw/{MK}/{yp["insights"]}">Insights</a></div>')
        tiles.append(f'<div class="fyt{cls}"><a href="/raw/{MK}/{FY_PAGE[f]}" style="text-decoration:none;color:inherit"><div class="y">{FY_LABEL[f]}</div><div class="n">{n}</div><div class="s">{note}</div></a>{sub_links}</div>')
    fys_html = f'<div class="h2row"><h2>By financial year</h2><span class="note">years run 1 April – 31 March — each year has its own dashboard, incidents, actions and insights</span></div><div class="fytiles">{"".join(tiles)}</div>'
    mser = monthly_series(inc, "FY26/27")
    pser = monthly_series(inc, "FY25/26")
    trend = (f'<div class="card"><div class="h2row"><h2>This year against last, month by month</h2></div>'
             + vbar_months(mser, pser, label="FY 2026/27", prior_label="FY 2025/26")
             + '<div class="legend"><span class="lg"><i style="background:#97D700"></i>FY 2026/27</span><span class="lg"><i style="background:#ccd3da"></i>FY 2025/26</span></div></div>')
    sub = "The whole register across every year — the per-year sections are where the detail lives."
    return shell("Depotnet Damages — all years", search_box(None) + kpis_html(cards) + doors + fys_html + trend, "overview.html", sub)

def fy_incidents_page(inc, act, fykey, enrich=None):
    rows = [r for r in inc if r["fy"] == fykey]
    label = FY_LABEL[fykey]
    act_by_inc = defaultdict(list)
    for a in act:
        act_by_inc[a["incident_id"]].append(a)
    body = [search_box(fykey)]
    body.append(f'<div class="h2row"><h2>Every damage in {label}</h2><span class="note">{len(rows)} incidents — filter by contract, utility, severity or status; click a row to open it in full</span></div>')
    body.append(incident_table(rows, act_by_inc, f"ti{fykey.replace('/', '')}", fy_filter=False, enrich=enrich))
    return shell(f"Incidents — {label}", "\n".join(body), FY_PAGE[fykey],
                 f"{label} · every Depotnet Incident Register row for the year, captured in full",
                 fykey=fykey, subactive="incidents", wide=True)

def all_incidents_page(inc, act, enrich=None):
    # Not in the nav — the landing page's cross-year cards (total / still open) deep-link here.
    act_by_inc = defaultdict(list)
    for a in act:
        act_by_inc[a["incident_id"]].append(a)
    body = [search_box(None), f'<div class="h2row"><h2>The full register, all years</h2><span class="note">{len(inc)} service damages, April 2023 to today — reached from the Overview cards; each year also has its own register</span></div>']
    body.append(incident_table(inc, act_by_inc, "tall", enrich=enrich))
    return shell("All incidents — every year", "\n".join(body), "overview.html",
                 f"{len(inc)} service damages · the whole register in one table", wide=True)

def dmg_href(r):
    """Link to a damage's own record page, when its year has one."""
    if not r or r.get("fy") not in FY_PAGE:
        return ""
    return f'/raw/{MK}/{year_pages(r["fy"])["dash"][:-5]}-damage.html?id={r["id"]}'


def actions_page(inc, act, fykey=None):
    """Actions centre — year-scoped when fykey given (actions on that year's incidents),
    else the all-years view reached only from the Overview cards."""
    inc_by_id = {r["id"]: r for r in inc}
    if fykey:
        year_ids = {r["id"] for r in inc if r["fy"] == fykey}
        act = [a for a in act if a["incident_id"] in year_ids]
        n_dam = len(year_ids)
    else:
        n_dam = len(inc)
    overdue = [a for a in act if a["status"] == "Overdue"]
    closed = [a for a in act if a["status"] == "Closed"]
    today = datetime.date.today()
    ages = []
    for a in overdue:
        if a["due"]:
            ages.append((today - datetime.date.fromisoformat(a["due"])).days)
    lag = []
    for a in act:
        if a["date_raised"] and a["incident_date"]:
            lag.append((datetime.date.fromisoformat(a["date_raised"][:10]) -
                        datetime.date.fromisoformat(a["incident_date"][:10])).days)
    lag.sort()
    med_lag = lag[len(lag) // 2] if lag else 0
    p90 = lag[int(len(lag) * .9)] if lag else 0
    n_with = len({a["incident_id"] for a in act})
    cards = [
        dict(n=f"{n_with} of {n_dam}", cls="red" if n_dam and n_with * 4 < n_dam else "",
             l=f"damages with any corrective action raised &#8212; {len(act)} individual action "
               f"lines in total, so a damage can carry several"),
        dict(n=len(closed), cls="green", l="closed", href="?status=Closed"),
        dict(n=len(overdue), cls="red", l="overdue right now", href="?status=Overdue"),
        dict(n=f"{max(ages) if ages else 0}d", cls="red" if ages else "", l="oldest overdue action (days past due)", href="?status=Overdue"),
        dict(n=f"{med_lag}d", l=f"median time from incident to action raised (slowest 10%: {p90}+ days)"),
    ]
    byass = Counter((a["assigned_to"] or "Unassigned").split(" (")[0] for a in act).most_common(10)
    od_fam = Counter((a["contract_family"] or a["contract"] or "Unstated") for a in overdue).most_common(10)
    body = [search_box(fykey), kpis_html(cards), '<div class="grid c2">']
    body.append(f'<div class="card"><div class="h2row"><h2>Who holds the actions</h2><span class="note">all {len(act)} actions</span></div>{hbar(byass)}</div>')
    body.append(f'<div class="card"><div class="h2row"><h2>Overdue, by contract</h2><span class="note">{len(overdue)} overdue</span></div>{hbar(od_fam, color="#dc2626")}</div>')
    body.append('</div>')
    # actions table — GROUPED BY DAMAGE (Pete, 1 Aug 2026: "we need to group them by incident
    # report"). A flat list made 9 actions on 2 damages read as 9 separate pieces of work; the
    # only on-screen clue that seven shared a damage was the location repeating.
    rows_html = []
    for a in sorted(act, key=lambda x: (x["status"] != "Overdue", x["due"] or "9999")):
        r = inc_by_id.get(a["incident_id"])
        age = ""
        if a["status"] == "Overdue" and a["due"]:
            age = f'<span class="pill st-over">{(today - datetime.date.fromisoformat(a["due"])).days}d late</span>'
        search = " ".join(str(x or "").lower() for x in [a["assigned_to"], a["description"], a["corrective_measure"], a["contract"], (r or {}).get("location")])
        rows_html.append(
            f'<tr class="row" data-search="{esc(search)}" data-status="{esc(a["status"] or "")}" data-fam="{esc(a["contract_family"] or "")}" data-dmg="{a["incident_id"]}">'
            f'<td class="mono">{a["id"]}</td>'
            f'<td class="mono">' + (f'<a href="{dmg_href(r)}">{a["incident_id"]}</a>'
                                    if r and dmg_href(r) else str(a["incident_id"])) + '</td>'
            f'<td class="mono" data-v="{a["due"] or ""}">{a["due"] or "—"} {age}</td>'
            f'<td>{esc((a["assigned_to"] or "Unassigned").split(" (")[0])}</td>'
            f'<td>{esc(a["contract_family"] or a["contract"] or "—")}</td>'
            f'<td>{esc(((r or {}).get("location") or "—")[:44])}</td>'
            f'<td>{status_pill(a["status"] or "—")}</td></tr>')
        det = (f'<div class="desc"><b>Asked:</b> {esc((a["description"] or "—"))}</div>'
               + (f'<div class="desc" style="margin-top:8px"><b>Done:</b> {esc(a["corrective_measure"])}</div>' if a["corrective_measure"] else '<div class="small muted" style="margin-top:8px">No corrective measure recorded.</div>')
               + f'<div class="meta"><span>Incident {a["incident_id"]}{(" — " + esc((r or {}).get("location") or "")) if r else ""}</span><span>Raised by {esc(a["raised_by"] or "—")}</span><span>Incident status: {esc(a["incident_status"] or "—")}</span></div>')
        rows_html.append(f'<tr class="det" style="display:none"><td colspan="7"><div class="det">{det}</div></td></tr>')
    fams_a = sorted({a["contract_family"] or "" for a in act if a["contract_family"]})
    selects = (f'<select data-filter-for="tact" data-key="status"><option value="">Status: all</option><option>Overdue</option><option>Closed</option></select>'
               f'<select data-filter-for="tact" data-key="fam"><option value="">Contract: all</option>' +
               "".join(f"<option>{esc(f)}</option>" for f in fams_a) + "</select>")
    # ── by damage, because that is the unit that matters ────────────────────────────────────
    by_dmg = {}
    for a in act:
        by_dmg.setdefault(a["incident_id"], []).append(a)
    grp = []
    for iid, ga in sorted(by_dmg.items(),
                          key=lambda kv: (-sum(1 for a in kv[1] if a["status"] == "Overdue"),
                                          -len(kv[1]), -kv[0])):
        r = inc_by_id.get(iid)
        od = sum(1 for a in ga if a["status"] == "Overdue")
        href = dmg_href(r)
        head = (f'<a href="{href}">Damage {iid}</a>' if href else f'Damage {iid}')
        loc = esc(((r or {}).get("location") or "location not recorded"))
        famn = esc((r or {}).get("contract_family") or (ga[0].get("contract_family") or "—"))
        tally = (f'<span class="pill st-over">{od} outstanding</span> '
                 if od else '') + f'<span class="pill act-yes">{len(ga)} action{"s" if len(ga) != 1 else ""}</span>'
        lis = "".join(
            f'<li><span class="mono">{a["id"]}</span> '
            f'<span class="muted">due {a["due"] or "—"}</span> '
            f'{esc((a["assigned_to"] or "Unassigned").split(" (")[0])} '
            f'{status_pill(a["status"] or "—")}'
            f'<div class="small muted">{esc((a["description"] or "")[:150])}</div></li>'
            for a in sorted(ga, key=lambda x: (x["status"] != "Overdue", x["due"] or "9999")))
        grp.append(f'<div class="dgrp"><div class="dgh"><div><b>{head}</b> '
                   f'<span class="muted">{famn} &middot; {loc}</span></div><div>{tally}</div></div>'
                   f'<ul class="dgl">{lis}</ul></div>')
    body.append('<div class="h2row" style="margin-top:26px"><h2>By damage</h2>'
                f'<span class="note">{len(act)} action{"s" if len(act) != 1 else ""} across '
                f'{len(by_dmg)} damage{"s" if len(by_dmg) != 1 else ""} '
                f'&mdash; the other {n_dam - len(by_dmg)} have none recorded</span></div>')
    body.append('<div class="dgrps">' + "".join(grp) + '</div>')

    body.append(f'<div class="h2row" style="margin-top:26px"><h2>Every action</h2><span class="note">overdue first — click a row for what was asked and what was done</span></div>')
    body.append(f'<div class="filters"><input type="search" id="tact-q" placeholder="Search assignee, action text…">{selects}<span class="count" id="tact-count"></span></div>')
    body.append(f'<div class="card" style="padding:6px 10px;overflow-x:auto"><table id="tact"><thead><tr>'
                f'<th data-col="0" data-num="1">ID <span class="arr">↕</span></th><th data-col="1" data-num="1">Damage <span class="arr">↕</span></th><th data-col="2">Due <span class="arr">↕</span></th>'
                f'<th data-col="3">Assigned to <span class="arr">↕</span></th><th data-col="4">Contract <span class="arr">↕</span></th>'
                f'<th data-col="5">Incident location <span class="arr">↕</span></th><th data-col="6">Status <span class="arr">↕</span></th>'
                f'</tr></thead><tbody>{"".join(rows_html)}</tbody></table></div>')
    body.append(f'<script>{TABLE_JS}initTable("tact");</script>')
    if fykey:
        return shell(f"Actions — {FY_LABEL[fykey]}", "\n".join(body), FY_PAGE[fykey],
                     f"{FY_LABEL[fykey]} · {len(act)} corrective actions on the year's damages · {len(overdue)} overdue",
                     fykey=fykey, subactive="actions")
    return shell("Actions — every year", "\n".join(body), "overview.html",
                 f"{len(act)} corrective actions across all years · {len(overdue)} overdue · reached from the Overview cards")

def fy_insights_page(inc, act, fykey):
    today = datetime.date.today()
    label = FY_LABEL[fykey]
    yp = year_pages(fykey)
    rows = [r for r in inc if r["fy"] == fykey]
    prior_key = FYS[FYS.index(fykey) - 1] if FYS.index(fykey) > 0 else None
    prior = [r for r in inc if r["fy"] == prior_key] if prior_key else []
    year_ids = {r["id"] for r in rows}
    yact = [a for a in act if a["incident_id"] in year_ids]
    open_rows = [r for r in rows if r["status"] == "Open"]
    open_no_action = [r for r in open_rows if r["id"] not in {a["incident_id"] for a in yact}]
    overdue = [a for a in yact if a["status"] == "Overdue"]
    lag = sorted((datetime.date.fromisoformat(a["date_raised"][:10]) -
                  datetime.date.fromisoformat(a["incident_date"][:10])).days
                 for a in yact if a["date_raised"] and a["incident_date"])
    short_desc = sum(1 for r in rows if r["description"] and len(str(r["description"])) < 25)
    uncl = sum(1 for r in rows if (r["utility_class"] or "Unclassified") == "Unclassified")
    sl = sum(1 for r in rows if r["utility_class"] == "Electric — street lighting")
    fams = fam_split(rows)
    utils = [u for u in util_split(rows) if u[1]]
    sevs = Counter(r["sev"] for r in rows)
    ins = []
    def I(kind, h, p, ev=""):
        ins.append(f'<div class="insight {kind}"><h3>{h}</h3><p>{p}</p>' + (f'<div class="ev">{ev}</div>' if ev else "") + "</div>")

    # Year-on-year movement
    if fykey == "FY26/27" and prior_key:
        months_elapsed = [m for m in FY_MONTHS[fykey] if m <= today.strftime("%Y-%m")]
        same = sum(Counter(r["month"] for r in prior).get(m.replace("2026", "2025").replace("2027", "2026"), 0) for m in months_elapsed)
        curn = len([r for r in rows if r["month"] in months_elapsed])
        if same:
            pct = (curn - same) / same * 100
            I("good" if pct < 0 else "warn",
              f"The running year is {abs(pct):.0f}% {'below' if pct < 0 else 'above'} last year's pace",
              f"April to {today.strftime('%B')}: <b>{curn} damages</b> against <b>{same}</b> in the same months of {FY_LABEL[prior_key]}.",
              "Counted from Incident Date; the year has months to run, so this is pace, not a final score.")
    elif prior_key and prior:
        pct = (len(rows) - len(prior)) / len(prior) * 100
        I("good" if pct < 0 else "warn",
          f"{label} finished {abs(pct):.0f}% {'below' if pct < 0 else 'above'} {FY_LABEL[prior_key]}",
          f"<b>{len(rows)} damages</b> against <b>{len(prior)}</b> the year before.",
          "FY23/24 may partly reflect Depotnet adoption ramping up rather than genuinely fewer damages." if prior_key == "FY23/24" else "")
    # Actions discipline for the year
    if rows:
        with_a = len({a["incident_id"] for a in yact})
        I("warn" if with_a < len(rows) * 0.5 else "",
          f"{with_a} of {len(rows)} damages produced a corrective action",
          f"{len(yact)} actions were raised on {label}'s damages{f'; <b>{len(overdue)} are overdue right now</b>' if overdue else ''}"
          f"{f'; <b>{len(open_no_action)} open damages have no action at all</b>' if open_no_action else ''}.",
          f"<a href='/raw/{MK}/{yp['actions']}'>The year's actions centre</a> has the full list, overdue first.")
    if lag:
        I("warn" if lag[int(len(lag)*.9)] > 90 else "",
          "How fast the paperwork followed the incident",
          f"Half of {label}'s actions were raised within <b>{lag[len(lag)//2]} days</b> of the incident; the slowest 10% took "
          f"<b>{lag[int(len(lag)*.9)]}+ days</b>, the worst <b>{lag[-1]} days</b>." +
          (" Actions raised that late look like retrofitted paperwork, not learning." if lag[-1] > 90 else ""),
          "Date Raised (action) minus Incident Date.")
    # Where the year's damages concentrated
    if fams:
        top_f = fams[0]
        I("", f"{top_f[0]} took the biggest share of {label}",
          f"{top_f[1]} of {len(rows)} damages ({top_f[1]/len(rows)*100:.0f}%). Utilities: " +
          ", ".join(f"{u} {v}" for u, v in utils) + ".",
          f"Severity mix: " + " · ".join(f"{s.split(' ')[0]} {sevs.get(s,0)}" for s in ["High (Cat 1)", "Medium (Cat 2)", "Low (Cat 3)"]) + ".")
    if sl:
        I("", f"{sl} street-lighting cable damages in {label}",
          "The exact failure mode Sygma keeps seeing at panel reviews — outlier columns that never get hooked up to. "
          "A focused toolbox talk plus the genny guidance could take a visible bite out of a named number.",
          "Auto-classified from descriptions; the register has no utility field of its own.")
    # Capture quality for the year
    I("warn" if (short_desc or uncl) else "good",
      f"Capture quality in {label}",
      f"{short_desc} descriptions under 25 characters · {uncl} damages whose utility can't be read from the description · "
      "the Action Report carries no closure dates, so closure speed can't be measured in any year.",
      "These are the gaps Sygma's own per-damage layer (utility confirmed, root cause, panel findings, training response) will close.")
    if fykey == "FY26/27":
        I("", "The register and the STRIVE headline count differently",
          "STRIVE 2030's published trajectory (171 baseline, target ≤85 by 2029/30) does not match register totals "
          "(226 in FY24/25), so they measure different things. Worth agreeing with Clancy which count is the metric of record "
          "before these pages are quoted as 'the number'.",
          "Register counts here are Depotnet Incident Register rows, Category = Service Damage, by Incident Date.")
    body = [f'<div class="h2row"><h2>What {label} actually says</h2><span class="note">generated {today.strftime("%-d %b %Y")} from the year\'s register + actions</span></div>', "\n".join(ins)]
    return shell(f"Insights — {label}", "\n".join(body), FY_PAGE[fykey],
                 f"{label} · trends, improvements, and the places the record flatters itself",
                 fykey=fykey, subactive="insights")

# ---------------------------------------------------------------- publish


def vocab_gate(pages):
    """Refuse to publish a page that names Depotnet wrongly or claims an absence the data cannot
    support. clancy-dn-pages.py was NOT gated when the gate was built on 1 Aug 2026, which is
    exactly how "No investigation has been done." survived on the per-damage page. Fail closed."""
    import subprocess, sys as _s
    bad = 0
    for name, htm in pages.items():
        r = subprocess.run([_s.executable, f"{VAULT}/clancy-vocab-check.py", "-"],
                           input=htm, capture_output=True, text=True)
        if r.returncode:
            bad += 1
            print(f"\n### {name}\n{r.stdout}")
    if bad:
        raise SystemExit(f"REFUSED to publish — {bad} page(s) need rewording (see above).")
    print(f"vocab: {len(pages)} pages clean")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()
    inc, act, enrich, files_by_inc, answers_by_inc = load()
    print(f"loaded {len(inc)} incidents, {len(act)} actions, {len(enrich)} enriched link(s)")
    pages = {"overview.html": hub(inc, act)}
    # this year IS the landing: the module index serves the current-FY dashboard
    pages["index.html"] = fy_dashboard(inc, act, "FY26/27", full=True)
    for f in FYS:
        yp = year_pages(f)
        pages[yp["dash"]] = fy_dashboard(inc, act, f, full=True)
        pages[yp["incidents"]] = fy_incidents_page(inc, act, f, enrich)
        pages[yp["actions"]] = actions_page(inc, act, fykey=f)
        pages[yp["insights"]] = fy_insights_page(inc, act, f)
        pages[f"{yp['dash'][:-5]}-damage.html"] = fy_detail_page(inc, act, enrich, files_by_inc, answers_by_inc, f)
    vocab_gate(pages)
    if args.local:
        os.makedirs(args.local, exist_ok=True)
        for name, htm in pages.items():
            open(os.path.join(args.local, name), "w").write(htm)
        print(f"wrote {len(pages)} pages to {args.local}")
    if args.publish:
        mod = {
            "module_key": MK, "slug": MK, "title": "Depotnet Damages",
            "section": "Customers", "subsection": "External", "area": "Clancy",
            "tier": "passcode", "passcode": "strive2030",
               # one section, one gate: every Depot page shares this group
               "unlock_group": "clancy-depotnet", "icon": "📊", "accent": "#1c2a6e",
            "status": "live", "enabled": True, "sort": 14,
            "groups": ["clancy", "clancy-external"], "tags": ["clancy", "customer", "depotnet", "damages"],
        }
        rest("modules?on_conflict=module_key", "POST", [mod], {"Prefer": "resolution=merge-duplicates"})
        # module_content inserts go through the SQL endpoint so the damage-review wording guard's
        # sanctioned override can be declared in-transaction: the pages quote Depotnet register
        # descriptions VERBATIM (their own text contains "strike"), which is the rule's stated
        # exception. All Sygma-authored prose on these pages says "damage".
        tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
        def sql(q):
            req = urllib.request.Request(
                "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
                data=json.dumps({"query": q}).encode(),
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0"}, method="POST")
            return _urlopen_retry(req, timeout=120).read().decode()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        reason = ("Depotnet Damages pages quote Incident Register descriptions verbatim - "
                  "the wording rules own verbatim-quote exception; Sygma prose says damage throughout")
        for name, htm in pages.items():
            key = MK if name == "index.html" else f"{MK}/{name}"
            assert "$dn$" not in htm
            sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
                f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
                f"('{key}', $dn${htm}$dn$, '{now}') "
                f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, updated_at=EXCLUDED.updated_at;")
            print(f"  published {key}")
        # prune sub-pages this generator no longer produces (renamed/retired paths)
        keep = ",".join(f"'{MK}/{n}'" for n in pages if n != "index.html")
        gone = sql(f"DELETE FROM module_content WHERE module_key LIKE '{MK}/%' "
                   f"AND module_key NOT IN ({keep}) RETURNING module_key;")
        if gone and gone != "[]":
            print(f"  pruned stale pages: {gone}")
        print(f"published module {MK} + {len(pages)} content pages (passcode strive2030)")

if __name__ == "__main__":
    main()
