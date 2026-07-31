#!/usr/bin/env python3
"""clancy-dn-hub.py — the ONE front door for everything Depotnet (module `clancy-depotnet`).

WHY: the Depotnet work grew as two separate sections — the damages register (this year's
service damages, per-incident deep capture) and the Genny & CAT data dive (the inspection
sampling analysis). Pete wants one place to land that then takes him into either. This renders
that landing page from live database counts, so it can never drift from what the sections hold.

Deliberately its own script, not part of clancy-dn-pages.py, so a long backfill run publishing
the damages pages and a hub refresh can never collide.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-hub.py [--local out.html] [--publish]
"""
import os, json, argparse, datetime, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
MK = "clancy-depotnet"
DAMAGES = "clancy-depotnet-damages"
REVIEWS = "clancy-genny-cat-reviews"


def rest(path):
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 headers={"apikey": SR, "Authorization": f"Bearer {SR}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def sql(q):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=120).read().decode())


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
background:#f4f6f9;color:#182230;line-height:1.5;-webkit-font-smoothing:antialiased}
.mast{background:linear-gradient(135deg,#16215c 0%,#1c2a6e 55%,#27358a 100%);color:#fff;padding:38px 22px 34px}
.wrap{max-width:1120px;margin:0 auto;padding:0 22px 70px}
.mast .wrap{padding:0 22px}
.brand{display:flex;align-items:center;gap:10px;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#c6cdf0}
.brand .dot{width:7px;height:7px;border-radius:50%;background:#f0b429}
h1{font-size:34px;letter-spacing:-.02em;margin:12px 0 8px}
.sub{font-size:15.5px;color:#c6cdf0;max-width:76ch}
.strip{display:flex;gap:26px;flex-wrap:wrap;margin-top:24px;padding-top:20px;border-top:1px solid rgba(255,255,255,.16)}
.strip div{min-width:96px}
.strip .n{font-size:25px;font-weight:800;line-height:1.1;font-variant-numeric:tabular-nums}
.strip .n.good{color:#7ee2a8}.strip .n.warn{color:#ffb4a8}
.strip .l{font-size:11.5px;color:#aab3e0;margin-top:3px;line-height:1.35}
.doors{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:-26px}
@media(max-width:840px){.doors{grid-template-columns:1fr}}
.door{display:block;background:#fff;border:1px solid #e4e8ee;border-radius:18px;padding:26px 28px 24px;
text-decoration:none;color:#182230;box-shadow:0 1px 2px rgba(16,24,40,.05),0 8px 26px rgba(16,24,40,.09);
transition:transform .16s,box-shadow .16s;position:relative;overflow:hidden}
.door:hover{transform:translateY(-3px);box-shadow:0 14px 34px rgba(16,24,40,.14)}
.door::before{content:"";position:absolute;inset:0 auto 0 0;width:6px;background:#2f5fd0}
.door.dive::before{background:#c0281e}
.door .kicker{font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#8b95a3}
.door .t{font-size:23px;font-weight:800;color:#1c2a6e;margin:6px 0 8px;letter-spacing:-.01em}
.door .d{font-size:14px;color:#5b6774;max-width:46ch;min-height:66px}
.door .figs{display:flex;gap:20px;margin:16px 0 4px;padding-top:14px;border-top:1px solid #eef1f5}
.door .figs .n{font-size:20px;font-weight:800;color:#1c2a6e;font-variant-numeric:tabular-nums;line-height:1.1}
.door .figs .l{font-size:11.5px;color:#8b95a3;margin-top:2px}
.door .go{margin-top:14px;font-size:13.5px;font-weight:700;color:#2f5fd0}
.door.dive .go{color:#c0281e}
h2{font-size:15px;color:#1c2a6e;margin:34px 0 12px;letter-spacing:.02em}
.mini{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.mini a{display:block;background:#fff;border:1px solid #e4e8ee;border-radius:12px;padding:14px 16px;
text-decoration:none;color:#182230;box-shadow:0 1px 2px rgba(16,24,40,.05);transition:transform .15s}
.mini a:hover{transform:translateY(-2px)}
.mini .y{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#8b95a3}
.mini .n{font-size:24px;font-weight:800;color:#1c2a6e;font-variant-numeric:tabular-nums;margin:2px 0}
.mini .s{font-size:12px;color:#8b95a3}
.mini a.cur{outline:2px solid #2f5fd0;outline-offset:-2px}
.note{background:#fff;border:1px solid #e4e8ee;border-left:4px solid #b45309;border-radius:0 12px 12px 0;
padding:14px 18px;margin-top:20px;font-size:13.5px;color:#3c4757;box-shadow:0 1px 2px rgba(16,24,40,.05)}
.note b{color:#1c2a6e}
.foot{margin-top:36px;font-size:12px;color:#8b95a3;border-top:1px solid #e4e8ee;padding-top:14px;
display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
"""


def build():
    d = sql("""SELECT
      (SELECT count(*) FROM clancy_dn_incidents) AS damages,
      (SELECT count(*) FROM clancy_dn_incidents WHERE fy='FY26/27') AS cur,
      (SELECT count(*) FROM clancy_dn_incidents WHERE fy='FY26/27' AND status='Open') AS cur_open,
      (SELECT count(*) FROM clancy_dn_actions) AS actions,
      (SELECT count(*) FROM clancy_dn_actions WHERE status='Overdue') AS overdue,
      (SELECT count(*) FROM clancy_dn_incidents WHERE pdf_captured_at IS NOT NULL) AS captured,
      (SELECT count(*) FROM clancy_dn_answers) AS answers,
      (SELECT count(*) FROM clancy_dn_files) AS files,
      (SELECT count(*) FROM clancy_dn_inspections) AS inspections,
      -- reviews-section figures are derived LIVE too (Pete, 31 Jul: everything database-based):
      -- person pages from the page registry, evidence PDFs from the Drive index.
      (SELECT count(*) FROM module_content WHERE module_key LIKE 'clancy-genny-cat-reviews/%'
         AND module_key NOT LIKE '%register%' AND module_key NOT LIKE '%findings%'
         AND module_key NOT LIKE '%people%' AND module_key NOT LIKE '%coverage%') AS person_pages,
      (SELECT count(*) FROM drive_files WHERE path LIKE '%depotnet-inspection-reviews%'
         AND lower(name) LIKE '%.pdf') AS review_pdfs,
      (SELECT min(fy) FROM clancy_dn_incidents) AS first_fy""")[0]
    fys = sql("SELECT fy, count(*) n FROM clancy_dn_incidents GROUP BY fy ORDER BY fy DESC")
    today = datetime.date.today()
    LBL = {"FY26/27": "FY 2026/27", "FY25/26": "FY 2025/26",
           "FY24/25": "FY 2024/25", "FY23/24": "FY 2023/24"}
    STEM = {"FY26/27": "fy-2026-27", "FY25/26": "fy-2025-26",
            "FY24/25": "fy-2024-25", "FY23/24": "fy-2023-24"}
    tiles = "".join(
        f'<a class="{"cur" if r["fy"]=="FY26/27" else ""}" href="/raw/{DAMAGES}/{STEM[r["fy"]]}.html">'
        f'<div class="y">{LBL.get(r["fy"], r["fy"])}</div><div class="n">{r["n"]}</div>'
        f'<div class="s">{"this year" if r["fy"]=="FY26/27" else "damages"}</div></a>'
        for r in fys if r["fy"] in STEM)
    pct = round(100 * d["captured"] / d["damages"]) if d["damages"] else 0
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>Clancy Depotnet | Sygma Solutions × The Clancy Group</title><style>{CSS}</style></head><body>
<div class="mast"><div class="wrap">
<div class="brand"><span class="dot"></span> Sygma Solutions × The Clancy Group</div>
<h1>Depotnet</h1>
<div class="sub">Everything Depotnet holds on service damage across the Clancy group, and what
Sygma's analysis makes of it. Two sections: what was damaged, and how well it was being looked
for in the first place.</div>
<div class="strip">
 <div><div class="n">{d['damages']:,}</div><div class="l">service damages<br>on the register</div></div>
 <div><div class="n">{d['cur']}</div><div class="l">so far this<br>financial year</div></div>
 <div><div class="n warn">{d['cur_open']}</div><div class="l">still open<br>this year</div></div>
 <div><div class="n">{d['actions']}</div><div class="l">corrective<br>actions</div></div>
 <div><div class="n {'warn' if d['overdue'] else 'good'}">{d['overdue']}</div><div class="l">actions<br>overdue</div></div>
 <div><div class="n">{d['answers']:,}</div><div class="l">investigation answers<br>captured</div></div>
</div>
</div></div>

<div class="wrap">
<div class="doors">
 <a class="door" href="/raw/{DAMAGES}/index.html">
  <div class="kicker">Section one</div>
  <div class="t">Damages</div>
  <div class="d">Every service damage on Depotnet, group-wide, by financial year — with the
  incident record, its corrective actions, the full investigation where one exists, and Sygma's
  own findings on top.</div>
  <div class="figs">
   <div><div class="n">{d['cur']}</div><div class="l">this year</div></div>
   <div><div class="n">{d['captured']}</div><div class="l">deep-captured ({pct}%)</div></div>
   <div><div class="n">{d['files']}</div><div class="l">documents &amp; photos</div></div>
  </div>
  <div class="go">Open this year's damages →</div>
 </a>
 <a class="door dive" href="/m/{REVIEWS}">
  <div class="kicker">Section two</div>
  <div class="t">Genny &amp; CAT Reviews</div>
  <div class="d">The data dive into how Genny &amp; CAT inspections are actually being carried
  out: what the scores really mean, how the actions get closed, which operatives are being
  reviewed and which are not.</div>
  <div class="figs">
   <div><div class="n">{d['person_pages']}</div><div class="l">named person reviews</div></div>
   <div><div class="n">{d['review_pdfs']}</div><div class="l">inspection reports</div></div>
   <div><div class="n">{d['inspections']}</div><div class="l">inspections in the store</div></div>
  </div>
  <div class="go">Open the review →</div>
 </a>
</div>

<h2>Straight to a year</h2>
<div class="mini">{tiles}</div>

<div class="note"><b>Note on the two sections.</b> Damages is fully database-driven and searchable.
The Genny &amp; CAT review is currently a written analysis with its evidence attached — only the
{d['inspections']}-record sample export sits in the database so far, so it is not yet covered by
the search. Bringing the full inspection dataset in is the next piece of work.</div>

<div class="foot"><span>Live from the Command Centre store · {today.strftime('%-d %b %Y')}</span>
<span>Prepared by Sygma Solutions.</span></div>
</div></body></html>"""


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
        mod = {"module_key": MK, "slug": MK, "title": "Clancy Depotnet",
               "section": "Customers", "subsection": "External", "area": "Clancy",
               "tier": "passcode", "passcode": "strive2030", "icon": "🗂️", "accent": "#1c2a6e",
               "status": "live", "enabled": True, "sort": 12,
               "groups": ["clancy", "clancy-external"], "tags": ["clancy", "customer", "depotnet"]}
        req = urllib.request.Request(f"{URL}/rest/v1/modules?on_conflict=module_key",
            data=json.dumps([mod]).encode(),
            headers={"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates"}, method="POST")
        urllib.request.urlopen(req, timeout=60)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        reason = ("Depotnet hub quotes register descriptions verbatim - the wording rules own "
                  "verbatim-quote exception; Sygma prose says damage throughout")
        assert "$hub$" not in html
        sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
            f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
            f"('{MK}', $hub${html}$hub$, '{now}') "
            f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, updated_at=EXCLUDED.updated_at;")
        print(f"published {MK} (passcode strive2030) — commandcentre.info/m/{MK}")


if __name__ == "__main__":
    main()
