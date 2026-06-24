#!/usr/bin/env python3
"""Publish the operational Sygma staff directory (non-salary) to the Command Centre.

Reads the vault person.md cards (Businesses/sygma-solutions/people/) and publishes a
roster snapshot to reports.snapshots key `staff-directory` (CC module: Sygma > Internal,
PRIVATE / owner-only).

SAFETY: explicit allowlist of operational fields ONLY — name, job title, sub-business,
employment status, work email, reports-to. NEVER emits salary/HR (that lives in the
separate Payroll Master / hub.staff_hr, never in person.md), nor payroll refs, Soldo
card refs, or personal contact. See [[staff-data-routing]].

Run standalone to refresh, or call publish_staff_directory() from staff-master-sync.py.
"""
import os, glob, datetime
from pathlib import Path
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = VAULT
PEOPLE_DIR = f"{VAULT}/Businesses/sygma-solutions/people"
SCRIPT_DIR = Path(__file__).resolve().parent

# Operational fields safe to surface (already on the all-staff Hub directory tier).
ALLOW = ["name", "job_title", "sub_business", "employment_status", "work_email", "reports_to"]

def _fm(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 4)
    block = text[4:end] if end != -1 else ""
    fm = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"')
    return fm

def build_html():
    people = []
    for p in sorted(glob.glob(f"{PEOPLE_DIR}/*.md")):
        fm = _fm(open(p).read())
        if not fm.get("name"):
            continue
        people.append({k: fm.get(k, "") for k in ALLOW})
    # group by sub_business
    groups = {}
    for person in people:
        groups.setdefault(person.get("sub_business") or "Sygma Solutions", []).append(person)
    rows = ""
    for sub in sorted(groups):
        members = sorted(groups[sub], key=lambda x: x["name"])
        rows += (f"<tr><td colspan='4' style='padding:14px 12px 6px;font-weight:700;color:#0b1e50;"
                 f"border-bottom:2px solid #e2e6f0'>{sub} <span style='color:#94a3b8;font-weight:400'>· {len(members)}</span></td></tr>")
        for m in members:
            status = m.get("employment_status", "")
            badge = (f"<span style='font-size:11px;padding:2px 8px;border-radius:99px;background:"
                     f"{'#dcfce7' if status.lower()=='active' else '#f1f5f9'};color:"
                     f"{'#15803d' if status.lower()=='active' else '#64748b'}'>{status or '—'}</span>")
            email = m.get("work_email", "")
            mail = f"<a href='mailto:{email}' style='color:#225aea'>{email}</a>" if email else "—"
            rows += (f"<tr style='border-bottom:1px solid #eef2f7'>"
                     f"<td style='padding:9px 12px;font-weight:600'>{m['name']}</td>"
                     f"<td style='padding:9px 12px;color:#475569'>{m.get('job_title') or '—'}</td>"
                     f"<td style='padding:9px 12px'>{mail}</td>"
                     f"<td style='padding:9px 12px'>{badge}</td></tr>")
    total = len(people)
    html = (f"<div style='font:14px/1.55 -apple-system,Segoe UI,sans-serif;padding:18px;color:#0b1220'>"
            f"<h2 style='margin:0 0 4px'>Sygma staff directory</h2>"
            f"<p style='margin:0 0 14px;color:#667'>{total} people · operational contact only — salary &amp; HR are owner-private and never shown here.</p>"
            f"<table style='width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e6f0;border-radius:10px;overflow:hidden'>"
            f"<thead><tr style='background:#f8fafc;color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.04em'>"
            f"<th style='text-align:left;padding:9px 12px'>Name</th><th style='text-align:left;padding:9px 12px'>Role</th>"
            f"<th style='text-align:left;padding:9px 12px'>Work email</th><th style='text-align:left;padding:9px 12px'>Status</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")
    return html, total

def publish_staff_directory():
    import importlib.util
    html, total = build_html()
    spec = importlib.util.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
    cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
    today = datetime.date.today().isoformat()
    ok = cc.publish("staff-directory", today, {"subject": f"Sygma staff directory — {total} people", "html": html})
    print(f"CC: staff-directory snapshot {'published' if ok else 'FAILED'} ({today}, {total} people)")
    return ok

if __name__ == "__main__":
    publish_staff_directory()