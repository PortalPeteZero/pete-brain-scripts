#!/usr/bin/env python3
"""Publish the Sygma Training KPIs + Weekly Audit tabs to the Command Centre.

Repointed 2026-07-03 (Item 10 of plan-pete-brain-scripts-local-vault-remediation-2026-07-02) —
the old local-file inputs (kpis.md / audits/*.md in the vault) were retired with the 24 Jun
cutover and had been silently no-op'ing since ~20 Jun. Real sources now:
  training-kpis   <- Portal hub.training_kpis, latest row (written by training-kpi-snapshot.py)
  training-audit  <- newest {date}-weekly-audit.md on Drive (Sygma Hub / Reports / Daily Audits,
                     written by training-audit.py) — the bounded no-schema-change option; a
                     hub.training_audit table (Option A) remains open if Pete prefers it later.

Soldo + Evaluations tabs were REMOVED from the CC on 2026-06-14 (they live on the Sygma Platform:
/hub/cost-base + /hub/training-evaluation); their dead publish functions were deleted 2026-07-03.
"""
import re, json, datetime, importlib.util, urllib.request
from pathlib import Path
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

SCRIPT_DIR = Path(__file__).resolve().parent
DRIVE_AUDIT_FOLDER_ID = "18-sO2NfiTEVImpov6e_YBomCeQPN9cWG"  # Sygma Hub / Reports / Daily Audits 2026

def _cc():
    spec = importlib.util.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def md2html(md):
    out, in_tbl = [], False
    for ln in md.splitlines():
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_tbl:
                out.append("<table style='border-collapse:collapse;width:100%;font-size:13px;margin:6px 0;background:#fff'>"); in_tbl = True
            out.append("<tr>" + "".join(f"<td style='border:1px solid #e2e6f0;padding:5px 8px'>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_tbl:
            out.append("</table>"); in_tbl = False
        ln = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", ln)
        ln = re.sub(r"`([^`]+)`", r"<code>\1</code>", ln)
        if ln.startswith("### "): out.append(f"<h4 style='margin:12px 0 4px'>{ln[4:]}</h4>")
        elif ln.startswith("## "): out.append(f"<h3 style='margin:14px 0 4px;color:#1B2340'>{ln[3:]}</h3>")
        elif ln.startswith("# "): out.append(f"<h2 style='margin:0 0 6px'>{ln[2:]}</h2>")
        elif ln.startswith("> "): out.append(f"<p style='margin:4px 0;color:#667;border-left:3px solid #e2e6f0;padding-left:10px'>{ln[2:]}</p>")
        elif ln.strip() == "": out.append("")
        else: out.append(f"<p style='margin:3px 0'>{ln}</p>")
    if in_tbl: out.append("</table>")
    return "<div style='font:14px/1.5 -apple-system,Segoe UI,sans-serif;padding:16px;color:#0b1220'>" + "\n".join(out) + "</div>"

def _strip_fm(txt):
    if txt.startswith("---"):
        e = txt.find("\n---", 4)
        if e != -1: return txt[e + 4:]
    return txt

def _portal_rest(path):
    d = json.load(open(f"{VAULT}/Library/processes/secrets/sygma-portal-supabase-keys.json"))
    key = d.get("service_role") or d["service_role_key"]
    req = urllib.request.Request(f"{d['url'].rstrip('/')}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Accept-Profile": "hub"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())

def publish_kpis(cc):
    rows = _portal_rest("training_kpis?select=generated,payload&order=generated.desc&limit=1")
    if not rows: print("  hub.training_kpis empty — skip"); return
    gen, p = rows[0]["generated"], rows[0]["payload"]
    h = p.get("headline", {})
    def kv(label, val): return (f"<tr style='border-bottom:1px solid #eef2f7'>"
        f"<td style='padding:7px 10px;color:#475569'>{label}</td>"
        f"<td style='padding:7px 10px;font-weight:700'>{val}</td></tr>")
    hrows = (kv("Courses YTD", h.get("ytd_courses", "?")) + kv("Delegates YTD", h.get("ytd_delegates", "?"))
             + kv("Avg courses / month", h.get("avg_courses_per_month", "?"))
             + kv("Avg delegates / month", h.get("avg_delegates_per_month", "?"))
             + kv("Annual run rate (courses)", h.get("annual_run_rate_courses", "?"))
             + kv("Annual run rate (delegates)", h.get("annual_run_rate_delegates", "?")))
    mrows = "".join(
        f"<tr style='border-bottom:1px solid #eef2f7'><td style='padding:6px 10px'>{m.get('month','')}</td>"
        f"<td style='padding:6px 10px;text-align:right'>{m.get('courses','')}</td>"
        f"<td style='padding:6px 10px;text-align:right'>{m.get('delegates','')}</td>"
        f"<td style='padding:6px 10px;color:#94a3b8'>{m.get('status','')}</td></tr>"
        for m in p.get("months", []))
    crows = "".join(
        f"<tr style='border-bottom:1px solid #eef2f7'><td style='padding:6px 10px'>{c.get('customer','')}</td>"
        f"<td style='padding:6px 10px;text-align:right'>{c.get('courses','')}</td></tr>"
        for c in p.get("top_customers", [])[:10])
    html = (f"<div style='font:14px/1.55 -apple-system,Segoe UI,sans-serif;padding:18px;color:#0b1220'>"
            f"<h2 style='margin:0 0 4px'>Sygma Training KPIs</h2>"
            f"<p style='margin:0 0 14px;color:#667'>Snapshot {p.get('snapshot', gen)} · source: Platform hub.training_kpis.</p>"
            f"<h3 style='margin:8px 0 4px;color:#1B2340'>Headline</h3>"
            f"<table style='width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e6f0;border-radius:8px;overflow:hidden'>{hrows}</table>"
            f"<h3 style='margin:16px 0 4px;color:#1B2340'>By month</h3>"
            f"<table style='width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e6f0;border-radius:8px;overflow:hidden'>"
            f"<thead><tr style='background:#f8fafc;font-size:12px;color:#64748b;text-transform:uppercase'>"
            f"<th style='text-align:left;padding:6px 10px'>Month</th><th style='text-align:right;padding:6px 10px'>Courses</th>"
            f"<th style='text-align:right;padding:6px 10px'>Delegates</th><th style='text-align:left;padding:6px 10px'>Status</th></tr></thead>"
            f"<tbody>{mrows}</tbody></table>"
            + (f"<h3 style='margin:16px 0 4px;color:#1B2340'>Top customers</h3>"
               f"<table style='width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e6f0;border-radius:8px;overflow:hidden'>{crows}</table>" if crows else "")
            + "</div>")
    return cc.publish("training-kpis", datetime.date.today().isoformat(),
                      {"subject": f"Sygma Training KPIs — snapshot {p.get('snapshot', gen)}", "html": html})

def publish_audit(cc):
    spec = importlib.util.spec_from_file_location("drive_api", str(SCRIPT_DIR / "drive-api.py"))
    da = importlib.util.module_from_spec(spec); spec.loader.exec_module(da)
    files = da.api("GET", "/files", {
        "q": f"'{DRIVE_AUDIT_FOLDER_ID}' in parents and name contains 'weekly-audit' and trashed = false",
        "fields": "files(id,name)", "orderBy": "name desc", "pageSize": "1",
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "corpora": "allDrives",
    }).get("files", [])
    if not files: print("  no audit on Drive — skip"); return
    latest = files[0]
    req = urllib.request.Request(
        f"https://www.googleapis.com/drive/v3/files/{latest['id']}?alt=media&supportsAllDrives=true",
        headers={"Authorization": f"Bearer {da.get_token()}"})
    md = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
    d = re.search(r"(\d{4}-\d{2}-\d{2})", latest["name"])
    return cc.publish("training-audit", (d.group(1) if d else datetime.date.today().isoformat()),
        {"subject": f"Weekly training audit — {d.group(1) if d else ''}", "html": md2html(_strip_fm(md))})

def main():
    cc = _cc()
    # Feeds the two CC-unique tabs only: training KPIs + Weekly Audit. (Soldo + Evaluations
    # live on the Sygma Platform since 2026-06-14; their dead functions were deleted 2026-07-03.)
    for name, fn in [("kpis", publish_kpis), ("audit", publish_audit)]:
        try:
            ok = fn(cc); print(f"  {name}: {'published' if ok else 'skipped/failed'}")
        except Exception as e:
            print(f"  {name}: ERROR {e}")

if __name__ == "__main__":
    main()