#!/usr/bin/env python3
"""Publish the Vault & Systems Health snapshot to the Command Centre.

Two signals on one page (CC module: Ops Centre, PRIVATE / owner-only, key `vault-health`):
  1. Live booking-engine markers — curls the Lanzarote Lates villa + booking-thanks pages
     and checks the SuperControl markers are still injected (the same check the daily
     lanza-lates-sc-marker-monitor cron makes). Green = bookings working.
  2. Vault drift — the headline from the most recent monthly vault-drift-check audit,
     with the individual issues behind a click-to-expand disclosure.

Run standalone to refresh; also called by the sc-marker monitor (daily) + drift-check (monthly).
The underlying crons keep emailing/alerting on FAIL exactly as before — this is additive.
"""
import os, glob, re, html, datetime, importlib.util, urllib.request
from pathlib import Path
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = VAULT
SCRIPT_DIR = Path(__file__).resolve().parent
AUDITS = f"{VAULT}/Library/audits"

# (label, url, required tokens)
CHECKS = [
    ("Casa Calma", "https://www.lanzarotelates.com/accommodation/casa-calma/", ["embed.js", "se=55492151"]),
    ("Casa Rubicon", "https://www.lanzarotelates.com/accommodation/casa-rubicon/", ["embed.js", "se=55492151"]),
    ("Villa Grace", "https://www.lanzarotelates.com/accommodation/villa-grace/", ["embed.js", "se=55492151"]),
    ("Booking thanks", "https://www.lanzarotelates.com/booking-thanks/", ["summary.js", "se=55492151"]),
]

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "cc-vault-health/1.0", "Pragma": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        return f"__ERR__ {e}"

def drift_info():
    """Return (ok, message, issues) — issues is a list of (category, text) from the latest audit."""
    files = sorted(glob.glob(f"{AUDITS}/*drift*.md") + glob.glob(f"{AUDITS}/*vault-drift*.md"))
    if not files:
        return None, "No drift audit on file yet (runs monthly, 1st).", []
    latest = files[-1]
    txt = open(latest).read()
    m = re.search(r"(\d+)\s+issues?\s+found", txt) or re.search(r"(\d+)\s+issue", txt)
    date = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(latest))
    when = date.group(1) if date else "?"
    if "no issues" in txt.lower() or (m and m.group(1) == "0"):
        return True, f"Clean at last run ({when}).", []
    issues, cur = [], None
    for line in txt.splitlines():
        h = re.match(r"^##\s+(.*?)(?:\s+\(\d+\))?\s*$", line)
        if h:
            cur = h.group(1).strip()
            if cur.lower().startswith("next steps"):
                cur = None
            continue
        if cur and line.lstrip().startswith("- "):
            issues.append((cur, line.lstrip()[2:].strip()))
    n = m.group(1) if m else str(len(issues))
    return False, f"{n} issue(s) at last run ({when}).", issues

def build_html():
    results = []
    for label, url, tokens in CHECKS:
        body = _get(url)
        if body.startswith("__ERR__"):
            results.append((label, "error", body[8:80]))
        else:
            ok = all(t in body for t in tokens)
            results.append((label, "pass" if ok else "fail", "markers present" if ok else "MARKERS MISSING"))
    n_ok = sum(1 for _, s, _ in results if s == "pass")
    allgood = n_ok == len(results)
    drift_ok, drift_msg, drift_issues = drift_info()
    def dot(state):
        c = {"pass": "#16a34a", "fail": "#dc2626", "error": "#d97706"}.get(state, "#94a3b8")
        return f"<span style='display:inline-block;width:9px;height:9px;border-radius:50%;background:{c};margin-right:8px'></span>"
    rows = "".join(
        f"<tr style='border-bottom:1px solid #eef2f7'><td style='padding:9px 12px'>{dot(s)}{label}</td>"
        f"<td style='padding:9px 12px;color:#475569'>{msg}</td></tr>" for label, s, msg in results)
    head_colour = "#16a34a" if allgood else "#dc2626"
    head = "All booking markers present — SuperControl bookings working." if allgood else f"{len(results)-n_ok} check(s) failing — bookings may be broken."
    dcol = {True: "#16a34a", False: "#dc2626", None: "#64748b"}[drift_ok]
    if drift_issues:
        items = "".join(
            f"<li style='margin:5px 0'><span style='color:#94a3b8'>{html.escape(cat)}</span> — {html.escape(it)}</li>"
            for cat, it in drift_issues)
        drift_block = (
            f"<details style='margin:0'>"
            f"<summary style='color:{dcol};font-weight:600;cursor:pointer;list-style:revert'>{drift_msg} <span style='color:#94a3b8;font-weight:400'>— click to see them</span></summary>"
            f"<ul style='margin:10px 0 0;padding-left:20px;color:#475569;font-size:13px;line-height:1.55'>{items}</ul>"
            f"</details>")
    else:
        drift_block = f"<p style='margin:0;color:{dcol}'>{drift_msg}</p>"
    html_out = (f"<div style='font:14px/1.55 -apple-system,Segoe UI,sans-serif;padding:18px;color:#0b1220'>"
            f"<h2 style='margin:0 0 4px'>Vault &amp; systems health</h2>"
            f"<p style='margin:0 0 14px;color:{head_colour};font-weight:600'>{head}</p>"
            f"<div style='font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px'>Booking engine (SuperControl markers)</div>"
            f"<table style='width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e6f0;border-radius:10px;overflow:hidden'><tbody>{rows}</tbody></table>"
            f"<div style='font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.04em;margin:18px 0 6px'>Vault drift</div>"
            f"{drift_block}"
            f"<p style='margin:14px 0 0;color:#94a3b8;font-size:12px'>Checked {datetime.datetime.now():%Y-%m-%d %H:%M}. The sc-marker monitor + drift-check crons keep alerting on failure as before.</p></div>")
    return html_out, allgood

def publish_vault_health():
    html_out, allgood = build_html()
    spec = importlib.util.spec_from_file_location("cc_publish", str(SCRIPT_DIR / "cc_publish.py"))
    cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
    today = datetime.date.today().isoformat()
    ok = cc.publish("vault-health", today, {"subject": f"Vault & systems health — {'all green' if allgood else 'ATTENTION'}", "html": html_out})
    print(f"CC: vault-health snapshot {'published' if ok else 'FAILED'} ({today}, allgood={allgood})")
    return ok

if __name__ == "__main__":
    publish_vault_health()