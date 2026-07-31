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
(contract families #2f5fd0 #d97706 #0e9594 #7c5cd6 #c2417f #4d7c0f + grey Other; utilities
Gas #b45309 · Water #2563eb · Electric #dc2626 · Comms #15803d + grey Other).

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-pages.py [--local DIR] [--publish]
  --local DIR   write the pages to DIR for preview
  --publish     upsert module row + module_content (the live pages)
"""
import os, sys, json, re, argparse, datetime, urllib.request, html as H
from collections import Counter, defaultdict

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
MK = "clancy-depotnet-damages"

def rest(path, method="GET", body=None, headers=None):
    h = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 data=(json.dumps(body).encode() if body is not None else None),
                                 headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        t = r.read().decode()
        return json.loads(t) if t else None

# ---------------------------------------------------------------- data

def load():
    inc = rest("clancy_dn_incidents?select=*&order=incident_date.desc&limit=10000")
    act = rest("clancy_dn_actions?select=*&order=date_raised.desc&limit=10000")
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
    enr = rest("clancy_damages?select=dn_id,job_ref,status,stage_note,summary,key_findings,next_actions,drive_folder,report_url&dn_id=not.is.null")
    enrich = {e["dn_id"]: e for e in enr}
    return inc, act, enrich

FAM_ORDER = ["Southern Water", "Anglian Water", "South East Water", "Scottish Water", "UKPN", "SGN"]
FAM_COLORS = {"Southern Water": "#2f5fd0", "Anglian Water": "#d97706", "South East Water": "#0e9594",
              "Scottish Water": "#7c5cd6", "UKPN": "#c2417f", "SGN": "#4d7c0f", "Other": "#737373"}
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

def vbar_months(series, prior=None, width=920, height=240, color="#2f5fd0", prior_color="#b6c3e8",
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
        c = (colors or {}).get(lab, color or "#2f5fd0")
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
:root{--bg:#f4f6f9;--card:#ffffff;--ink:#182230;--muted:#5b6774;--soft:#8property5;--border:#e4e8ee;
--navy:#1c2a6e;--navy2:#2f3f8f;--red:#c0281e;--accent:#2f5fd0;--green:#15803d;--amber:#b45309;
--shadow:0 1px 2px rgba(16,24,40,.05),0 4px 16px rgba(16,24,40,.07)}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
background:var(--bg);color:var(--ink);line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px 70px}
.mast{background:linear-gradient(135deg,#16215c 0%,#1c2a6e 55%,#27358a 100%);color:#fff;margin:0 0 26px}
.mast .wrap{padding:26px 22px 24px}
.mast .brand{display:flex;align-items:center;gap:10px;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#c6cdf0}
.mast .brand .dot{width:7px;height:7px;border-radius:50%;background:#f0b429}
.mast h1{font-size:27px;letter-spacing:-.01em;margin:10px 0 4px}
.mast .sub{font-size:14px;color:#c6cdf0;max-width:80ch}
.nav{display:flex;gap:6px;flex-wrap:wrap;margin-top:18px}
.nav a{font-size:13px;font-weight:600;color:#dfe4fa;text-decoration:none;padding:7px 13px;border-radius:8px;background:rgba(255,255,255,.08);transition:background .2s}
.nav a:hover{background:rgba(255,255,255,.18)}
.nav a.on{background:#fff;color:var(--navy)}
.nav.subnav{margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,.14)}
.nav.subnav a{font-size:12.5px;padding:6px 12px;background:rgba(255,255,255,.05)}
.nav.subnav a.on{background:#f0b429;color:#1c2a6e}
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
th{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#7b8694;text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none}
th .arr{opacity:.45;font-size:9px}
td{padding:9px 10px;border-bottom:1px solid #edf0f4;vertical-align:top}
tr.row{cursor:pointer}
tr.row:hover td{background:#f7f9fc}
tr.det td{background:#f9fafc;border-bottom:1px solid var(--border);padding:14px 16px}
.pill{display:inline-block;font-size:11px;font-weight:700;padding:3px 9px;border-radius:999px;white-space:nowrap}
.pill.sev-h{background:#fdecea;color:#b91c1c}.pill.sev-m{background:#fdf3e7;color:#b45309}.pill.sev-l{background:#ecf7f0;color:#15803d}
.pill.st-open{background:#fdf3e7;color:#b45309}.pill.st-closed{background:#ecf7f0;color:#15803d}
.pill.st-out{background:#eef1f5;color:#5b6774}.pill.st-over{background:#fdecea;color:#b91c1c}
.pill.uc{background:#eef2fb;color:#2f5fd0}
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
  if(q) q.addEventListener('input',apply);
  sels.forEach(s=>s.addEventListener('change',apply));
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

def shell(title, body, active, sub="", fykey=None, subactive=None):
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
    links += f'<a href="/m/clancy-genny-cat-reviews" style="margin-left:auto">Data Dive ↗</a>'
    if fykey:
        yp = year_pages(fykey)
        sub_items = [(yp["dash"], "Dashboard"), (yp["incidents"], "Incidents"),
                     (yp["actions"], "Actions"), (yp["insights"], "Insights")]
        links += '</div><div class="nav subnav">' + "".join(
            f'<a href="/raw/{MK}/{h}"{" class=\"on\"" if k == subactive else ""}>{t}</a>'
            for (h, t), k in zip(sub_items, ["dash", "incidents", "actions", "insights"]))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>{esc(title)} | Sygma Solutions × The Clancy Group</title>
<style>{CSS}</style>
</head>
<body>
<div class="mast"><div class="wrap">
<div class="brand"><span class="dot"></span> Sygma Solutions × The Clancy Group — Depotnet</div>
<h1>{esc(title)}</h1>
<div class="sub">{sub}</div>
<div class="nav">{links}</div>
</div></div>
<div class="wrap">
{body}
<div class="foot"><span>Source: Depotnet Incident Manager exports (Incident Register + Action Report), imported {datetime.date.today().strftime('%-d %b %Y')}.</span><span>Prepared by Sygma Solutions.</span></div>
</div>
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

def incident_table(inc, act_by_inc, tid, fy_filter=True):
    fams = sorted({fam(r) for r in inc})
    utils = [u for u in UTIL_ORDER if any(r["ugroup"] == u for r in inc)]
    sevs = ["High (Cat 1)", "Medium (Cat 2)", "Low (Cat 3)"]
    stats = sorted({r["status"] or "" for r in inc})
    fys = [f for f in FYS[::-1] if any(r["fy"] == f for r in inc)]
    sel = []
    if fy_filter and len(fys) > 1:
        sel.append(('fy', 'FY', fys))
    sel += [('fam', 'Contract', fams), ('ugroup', 'Utility', utils), ('sev', 'Severity', sevs), ('status', 'Status', stats)]
    selects = "".join(
        f'<select data-filter-for="{tid}" data-key="{key}"><option value="">{lab}: all</option>' +
        "".join(f'<option>{esc(o)}</option>' for o in opts) + "</select>"
        for key, lab, opts in sel)
    rows_html = []
    for r in sorted(inc, key=lambda x: (x["d"] or ""), reverse=True):
        acts = act_by_inc.get(r["id"], [])
        search = " ".join(str(r.get(f) or "").lower() for f in
                          ["id", "location", "description", "contract", "raised_by", "subcontractor", "job_ref"])
        date_h = f'<td class="mono" data-v="{r["d"] or ""}">{r["d"] or "—"}</td>'
        overdue = sum(1 for a in acts if a["status"] == "Overdue")
        act_h = (f'{len(acts)}' + (f' <span class="pill st-over">{overdue} overdue</span>' if overdue else "")) if acts else '<span class="muted">0</span>'
        detail = f'/raw/{MK}/{year_pages(r["fy"])["dash"][:-5]}-damage.html?id={r["id"]}' if r["fy"] in FY_PAGE else ""
        rows_html.append(
            f'<tr class="row" data-href="{detail}" data-search="{esc(search)}" data-fy="{esc(r["fy"] or "")}" data-fam="{esc(fam(r))}" '
            f'data-ugroup="{esc(r["ugroup"])}" data-sev="{esc(r["sev"])}" data-status="{esc(r["status"] or "")}">'
            f'<td class="mono">{r["id"]}</td>{date_h}'
            f'<td>{esc(fam(r))}<div class="small muted">{esc(r["contract"] or "")}</div></td>'
            f'<td>{esc((r["location"] or "—")[:60])}<div class="small muted">{esc((r["description"] or "")[:90])}</div></td>'
            f'<td><span class="pill uc">{esc(r["ugroup"])}</span></td>'
            f'<td data-v="{["High","Medium","Low"].index(r["sev"].split(" ")[0]) if r["sev"].split(" ")[0] in ["High","Medium","Low"] else 3}">{sev_pill(r["sev"])}</td>'
            f'<td>{status_pill(r["status"] or "—")}</td>'
            f'<td class="mono" data-v="{len(acts)}">{act_h}</td></tr>')
    return f"""
<div class="filters"><input type="search" id="{tid}-q" placeholder="Search location, description, ID…">{selects}<span class="count" id="{tid}-count"></span></div>
<div class="card" style="padding:6px 10px;overflow-x:auto"><table id="{tid}"><thead><tr>
<th data-col="0" data-num="1">ID <span class="arr">↕</span></th><th data-col="1">Date <span class="arr">↕</span></th>
<th data-col="2">Contract <span class="arr">↕</span></th><th data-col="3">Location <span class="arr">↕</span></th>
<th data-col="4">Utility <span class="arr">↕</span></th><th data-col="5" data-num="1">Severity <span class="arr">↕</span></th>
<th data-col="6">Status <span class="arr">↕</span></th><th data-col="7" data-num="1">Actions <span class="arr">↕</span></th>
</tr></thead><tbody>{"".join(rows_html)}</tbody></table></div>
<p class="small muted" style="margin-top:8px">Click any row to open the damage in full — every Depotnet field, the timeline, its corrective actions and any Sygma material.</p>
<script>{TABLE_JS}initTable("{tid}");</script>"""

# ---------------------------------------------------------------- semantic search partial

FY_STEM_JS = json.dumps({f: FY_PAGE[f][:-5] for f in FYS})

def search_box(fy=None):
    """Semantic search over the whole store (or one FY), calling the gated CC API."""
    fyq = f",fy:{json.dumps(fy)}" if fy else ""
    scope = f"this year's damages" if fy else "every damage, every year"
    return f"""
<div class="card" style="padding:14px 18px;margin-bottom:18px">
 <div class="searchrow">
  <input type="search" id="sem-q" placeholder="Search {scope} by meaning — e.g. 'hit a gas main while breaking out concrete'">
  <button id="sem-go" class="sem-btn">Search</button>
 </div>
 <div id="sem-res"></div>
</div>
<script>
(function(){{
 const STEM={FY_STEM_JS};
 const box=document.getElementById('sem-res'), inp=document.getElementById('sem-q');
 async function go(){{
  const q=inp.value.trim(); if(!q){{box.innerHTML='';return;}}
  box.innerHTML='<p class="small muted" style="margin-top:10px">Searching…</p>';
  try{{
   const r=await fetch('/api/clancy-dn-search',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{q:q{fyq}}})}});
   if(!r.ok) throw 0;
   const d=await r.json();
   if(!d.results||!d.results.length){{box.innerHTML='<p class="small muted" style="margin-top:10px">Nothing close — try different words.</p>';return;}}
   box.innerHTML='<div class="sem-list">'+d.results.map(x=>{{
    const stem=STEM[x.fy]||'fy-2026-27';
    const badge=x.kind==='action'?'<span class="pill st-out">action</span> ':'';
    return '<a class="sem-hit" href="/raw/{MK}/'+stem+'-damage.html?id='+x.incident_id+'">'
      +'<span class="mono">'+x.incident_id+'</span> · '+(x.incident_date||'')+' · <b>'+(x.contract||'')+'</b> · '
      +(x.location||'').slice(0,50)+' '+badge+'<span class="small muted">'+(x.snippet||'').slice(0,130)+'…</span></a>';
   }}).join('')+'</div>';
  }}catch(e){{box.innerHTML='<p class="small muted" style="margin-top:10px">Search is unavailable right now.</p>';}}
 }}
 document.getElementById('sem-go').addEventListener('click',go);
 inp.addEventListener('keydown',e=>{{if(e.key==='Enter')go();}});
}})();
</script>"""

# ---------------------------------------------------------------- per-damage detail page

def fy_detail_page(inc, act, enrich, fykey):
    """One page per year that renders ANY of the year's damages in full from embedded JSON
    (?id=N): every register field, every action in full, the timeline, and the Sygma layer."""
    label = FY_LABEL[fykey]
    yp = year_pages(fykey)
    rows = [r for r in inc if r["fy"] == fykey]
    year_ids = {r["id"] for r in rows}
    acts = [a for a in act if a["incident_id"] in year_ids]
    data = {
        "incidents": {str(r["id"]): r for r in rows},
        "actions": defaultdict(list),
        "enrich": {str(k): v for k, v in enrich.items() if k in year_ids},
    }
    for a in acts:
        data["actions"][str(a["incident_id"])].append(a)
    data["actions"] = dict(data["actions"])
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    body = f"""
{search_box(fykey)}
<div id="dmg"></div>
<script>
const D={payload};
const FLD=[["id","Depotnet ID"],["incident_date","Incident date"],["date_raised","Logged on Depotnet"],
 ["category","Category"],["contract","Contract"],["contract_family","Contract group"],
 ["contract_number","Contract number"],["workstream","Workstream"],["business_unit","Business unit"],
 ["job_id","Job ID"],["job_ref","Job ref"],["location","Location"],["severity","Severity"],
 ["status","Status"],["raised_by","Raised by"],["subcontractor","Subcontractor"]];
const AFLD=[["id","Action ID"],["date_raised","Raised"],["raised_by","Raised by"],["due_date","Due"],
 ["assigned_to","Assigned to"],["status","Status"],["incident_status","Incident status at export"],
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
 // timeline across both sheets
 const ev=[];
 if(r.incident_date)ev.push([r.incident_date,'Damage occurred','']);
 if(r.date_raised)ev.push([r.date_raised,'Logged on Depotnet','by '+(r.raised_by||'—')]);
 acts.forEach(a=>{{
  if(a.date_raised)ev.push([a.date_raised,'Action '+a.id+' raised','assigned to '+((a.assigned_to||'—').split(' (')[0])]);
  if(a.due_date)ev.push([a.due_date,'Action '+a.id+' due',a.status==='Overdue'?'STILL OVERDUE':(a.status||'')]);
 }});
 ev.sort((x,y)=>String(x[0]).localeCompare(String(y[0])));
 h+='<div class="card" style="margin-bottom:16px"><div class="h2row"><h2>Timeline</h2><span class="note">every date across both sheets</span></div><div class="tl">'
   +ev.map(e=>'<div class="tle"><span class="mono tld">'+fmtTs(e[0])+'</span><b>'+e[1]+'</b> <span class="muted small">'+e[2]+'</span></div>').join('')+'</div></div>';
 // actions in full
 h+='<div class="card" style="margin-bottom:16px"><div class="h2row"><h2>Corrective actions'+(acts.length?' ('+acts.length+')':'')+'</h2>'
   +'<span class="note">'+(acts.length?'Action Report rows, in full':'')+'</span></div>';
 if(!acts.length){{h+='<p class="small muted">None — no row in the Action Report references this damage.</p>';}}
 else acts.forEach(a=>{{
  h+='<div class="acard"><div class="h2row"><b>'+((a.assigned_to||'Unassigned').split(' (')[0])+'</b><span>'+statusPill(a.status)+'</span></div>'
    +'<div class="fgrid">'+AFLD.map(([k,l])=>{{const v=k.includes('date')?fmtTs(a[k]):a[k];return v?'<div class="f"><div class="fl">'+l+'</div><div class="fv">'+v+'</div></div>':'';}}).join('')+'</div>'
    +(a.description?'<div class="fl" style="margin-top:8px">What was asked</div><div class="det desc">'+a.description+'</div>':'')
    +(a.corrective_measure?'<div class="fl" style="margin-top:8px">What was done</div><div class="det desc">'+a.corrective_measure+'</div>':'<div class="small muted" style="margin-top:8px">No corrective measure recorded.</div>')
    +'</div>';
 }});
 h+='</div>';
 // Sygma layer
 h+='<div class="card"><div class="h2row"><h2>Sygma material</h2><span class="note">panel reviews, findings, documents</span></div>';
 if(en){{
  h+=(en.summary?'<p class="det desc">'+en.summary+'</p>':'');
  if(en.key_findings&&en.key_findings.length)h+='<div class="fl" style="margin-top:10px">Key findings</div><ul class="kf">'+en.key_findings.map(k=>'<li>'+k+'</li>').join('')+'</ul>';
  if(en.next_actions&&en.next_actions.length)h+='<div class="fl" style="margin-top:10px">Agreed next actions</div><ul class="kf">'+en.next_actions.map(k=>'<li>'+k+'</li>').join('')+'</ul>';
  const links=[];
  if(en.drive_folder)links.push('<a href="'+en.drive_folder+'">Drive folder — documents & transcripts</a>');
  if(en.report_url)links.push('<a href="'+en.report_url+'">Report</a>');
  if(links.length)h+='<div class="legend" style="margin-top:10px">'+links.map(l=>'<span class="lg">'+l+'</span>').join('')+'</div>';
  h+='<p class="small muted" style="margin-top:8px">Sygma status: '+(en.status||'—')+(en.stage_note?' · '+en.stage_note:'')+'</p>';
 }} else h+='<p class="small muted">Nothing linked yet — panel reviews, findings and documents appear here once Sygma material is tied to this damage.</p>';
 h+='</div><p style="margin-top:14px"><a href="/raw/{MK}/{yp["incidents"]}">&larr; back to the {label} register</a></p>';
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
    prior_leg = f'<div class="legend"><span class="lg"><i style="background:#2f5fd0"></i>{label}</span>' + \
                (f'<span class="lg"><i style="background:#b6c3e8"></i>{FY_LABEL[prior_key]} (same month)</span>' if prior_key else "") + "</div>"
    body.append(f'<div class="card"><div class="h2row"><h2>Damages by month</h2><span class="note">financial year, April to March</span></div>'
                + vbar_months(mser, pser, label=label, prior_label=FY_LABEL.get(prior_key, "")) + prior_leg + "</div>")
    fams = fam_split(rows)
    utils = util_split(rows)
    sevs = sev_split(rows)
    body.append('<div class="grid c3" style="margin-top:16px">')
    body.append(f'<div class="card"><div class="h2row"><h2>By contract</h2></div>{hbar(fams, colors=FAM_COLORS)}</div>')
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
<a class="fyt" href="/raw/{MK}/{yp['actions']}"><div class="y">Actions</div><div class="n">{len(fy_act)}</div><div class="s">{overdue} overdue — who owes what</div></a>
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
    cards = [
        dict(n=len(inc), l="service damages on the Depotnet register (Apr 2023 → today)",
             href=f"/raw/{MK}/all-incidents.html"),
        dict(n=len(cur), l="so far this financial year (from 1 Apr 2026)",
             href=f"/raw/{MK}/fy-2026-27.html"),
        dict(n=f"{pct:+.0f}%", cls="green" if pct < 0 else "red",
             l=f"vs the same months last year ({curn} v {same})",
             href=f"/raw/{MK}/fy-2026-27-insights.html"),
        dict(n=open_n, cls="amber", l="incidents still open, all years",
             href=f"/raw/{MK}/all-incidents.html?status=Open"),
        dict(n=overdue, cls="red", l="corrective actions overdue, all years",
             href=f"/raw/{MK}/all-actions.html?status=Overdue"),
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
             + '<div class="legend"><span class="lg"><i style="background:#2f5fd0"></i>FY 2026/27</span><span class="lg"><i style="background:#b6c3e8"></i>FY 2025/26</span></div></div>')
    sub = "The whole register across every year — the per-year sections are where the detail lives."
    return shell("Depotnet Damages — all years", search_box(None) + kpis_html(cards) + doors + fys_html + trend, "overview.html", sub)

def fy_incidents_page(inc, act, fykey):
    rows = [r for r in inc if r["fy"] == fykey]
    label = FY_LABEL[fykey]
    act_by_inc = defaultdict(list)
    for a in act:
        act_by_inc[a["incident_id"]].append(a)
    body = [search_box(fykey)]
    body.append(f'<div class="h2row"><h2>Every damage in {label}</h2><span class="note">{len(rows)} incidents — filter by contract, utility, severity or status; click a row to open it in full</span></div>')
    body.append(incident_table(rows, act_by_inc, f"ti{fykey.replace('/', '')}", fy_filter=False))
    return shell(f"Incidents — {label}", "\n".join(body), FY_PAGE[fykey],
                 f"{label} · every Depotnet Incident Register row for the year, captured in full",
                 fykey=fykey, subactive="incidents")

def all_incidents_page(inc, act):
    # Not in the nav — the landing page's cross-year cards (total / still open) deep-link here.
    act_by_inc = defaultdict(list)
    for a in act:
        act_by_inc[a["incident_id"]].append(a)
    body = [search_box(None), f'<div class="h2row"><h2>The full register, all years</h2><span class="note">{len(inc)} service damages, April 2023 to today — reached from the Overview cards; each year also has its own register</span></div>']
    body.append(incident_table(inc, act_by_inc, "tall"))
    return shell("All incidents — every year", "\n".join(body), "overview.html",
                 f"{len(inc)} service damages · the whole register in one table")

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
    cards = [
        dict(n=len(act), l=f"corrective actions raised ({len({a['incident_id'] for a in act})} of {n_dam} damages have at least one)"),
        dict(n=len(closed), cls="green", l="closed", href="?status=Closed"),
        dict(n=len(overdue), cls="red", l="overdue right now", href="?status=Overdue"),
        dict(n=f"{max(ages) if ages else 0}d", cls="red" if ages else "", l="oldest overdue action (days past due)", href="?status=Overdue"),
        dict(n=f"{med_lag}d", l=f"median time from incident to action raised (slowest 10%: {p90}+ days)"),
    ]
    byass = Counter((a["assigned_to"] or "Unassigned").split(" (")[0] for a in act).most_common(10)
    od_fam = Counter((a["contract_family"] or a["contract"] or "Unstated") for a in overdue).most_common(10)
    body = [search_box(fykey), kpis_html(cards), '<div class="grid c2">']
    body.append(f'<div class="card"><div class="h2row"><h2>Who holds the actions</h2><span class="note">all {len(act)} actions</span></div>{hbar(byass, color="#2f5fd0")}</div>')
    body.append(f'<div class="card"><div class="h2row"><h2>Overdue, by contract</h2><span class="note">{len(overdue)} overdue</span></div>{hbar(od_fam, color="#dc2626")}</div>')
    body.append('</div>')
    # actions table
    rows_html = []
    for a in sorted(act, key=lambda x: (x["status"] != "Overdue", x["due"] or "9999")):
        r = inc_by_id.get(a["incident_id"])
        age = ""
        if a["status"] == "Overdue" and a["due"]:
            age = f'<span class="pill st-over">{(today - datetime.date.fromisoformat(a["due"])).days}d late</span>'
        search = " ".join(str(x or "").lower() for x in [a["assigned_to"], a["description"], a["corrective_measure"], a["contract"], (r or {}).get("location")])
        rows_html.append(
            f'<tr class="row" data-search="{esc(search)}" data-status="{esc(a["status"] or "")}" data-fam="{esc(a["contract_family"] or "")}">'
            f'<td class="mono">{a["id"]}</td>'
            f'<td class="mono" data-v="{a["due"] or ""}">{a["due"] or "—"} {age}</td>'
            f'<td>{esc((a["assigned_to"] or "Unassigned").split(" (")[0])}</td>'
            f'<td>{esc(a["contract_family"] or a["contract"] or "—")}</td>'
            f'<td>{esc(((r or {}).get("location") or "—")[:44])}</td>'
            f'<td>{status_pill(a["status"] or "—")}</td></tr>')
        det = (f'<div class="desc"><b>Asked:</b> {esc((a["description"] or "—"))}</div>'
               + (f'<div class="desc" style="margin-top:8px"><b>Done:</b> {esc(a["corrective_measure"])}</div>' if a["corrective_measure"] else '<div class="small muted" style="margin-top:8px">No corrective measure recorded.</div>')
               + f'<div class="meta"><span>Incident {a["incident_id"]}{(" — " + esc((r or {}).get("location") or "")) if r else ""}</span><span>Raised by {esc(a["raised_by"] or "—")}</span><span>Incident status: {esc(a["incident_status"] or "—")}</span></div>')
        rows_html.append(f'<tr class="det" style="display:none"><td colspan="6"><div class="det">{det}</div></td></tr>')
    fams_a = sorted({a["contract_family"] or "" for a in act if a["contract_family"]})
    selects = (f'<select data-filter-for="tact" data-key="status"><option value="">Status: all</option><option>Overdue</option><option>Closed</option></select>'
               f'<select data-filter-for="tact" data-key="fam"><option value="">Contract: all</option>' +
               "".join(f"<option>{esc(f)}</option>" for f in fams_a) + "</select>")
    body.append(f'<div class="h2row" style="margin-top:26px"><h2>Every action</h2><span class="note">overdue first — click a row for what was asked and what was done</span></div>')
    body.append(f'<div class="filters"><input type="search" id="tact-q" placeholder="Search assignee, action text…">{selects}<span class="count" id="tact-count"></span></div>')
    body.append(f'<div class="card" style="padding:6px 10px;overflow-x:auto"><table id="tact"><thead><tr>'
                f'<th data-col="0" data-num="1">ID <span class="arr">↕</span></th><th data-col="1">Due <span class="arr">↕</span></th>'
                f'<th data-col="2">Assigned to <span class="arr">↕</span></th><th data-col="3">Contract <span class="arr">↕</span></th>'
                f'<th data-col="4">Incident location <span class="arr">↕</span></th><th data-col="5">Status <span class="arr">↕</span></th>'
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()
    inc, act, enrich = load()
    print(f"loaded {len(inc)} incidents, {len(act)} actions, {len(enrich)} enriched link(s)")
    pages = {"overview.html": hub(inc, act),
             "all-incidents.html": all_incidents_page(inc, act),
             "all-actions.html": actions_page(inc, act, fykey=None)}
    # this year IS the landing: the module index serves the current-FY dashboard
    pages["index.html"] = fy_dashboard(inc, act, "FY26/27", full=True)
    for f in FYS:
        yp = year_pages(f)
        pages[yp["dash"]] = fy_dashboard(inc, act, f, full=True)
        pages[yp["incidents"]] = fy_incidents_page(inc, act, f)
        pages[yp["actions"]] = actions_page(inc, act, fykey=f)
        pages[yp["insights"]] = fy_insights_page(inc, act, f)
        pages[f"{yp['dash'][:-5]}-damage.html"] = fy_detail_page(inc, act, enrich, f)
    if args.local:
        os.makedirs(args.local, exist_ok=True)
        for name, htm in pages.items():
            open(os.path.join(args.local, name), "w").write(htm)
        print(f"wrote {len(pages)} pages to {args.local}")
    if args.publish:
        mod = {
            "module_key": MK, "slug": MK, "title": "Depotnet Damages",
            "section": "Customers", "subsection": "External", "area": "Clancy",
            "tier": "passcode", "passcode": "strive2030", "icon": "📊", "accent": "#1c2a6e",
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
            return urllib.request.urlopen(req, timeout=120).read().decode()
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
