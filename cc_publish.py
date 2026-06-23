#!/usr/bin/env python3
"""Publish a report snapshot to the Command Centre (reports.snapshots).

Usage (library):
    from cc_publish import publish
    publish("cd-finance-weekly", "2026-06-09", {"gross": 3086.95, ...})

The CC page for the report renders the newest row per period; emails keep
sending separately — this is the single-source-of-truth write (build plan
decision 12/18; register: Library/processes/cc-data-feeds.md).
Non-fatal by design: callers should warn loudly but never die on publish failure.
"""
import json, os, urllib.request
from pathlib import Path

# $VAULT-aware (set by railway-bootstrap on the cloud); falls back to the vault layout locally.
_SECRETS = (Path(os.environ["VAULT"]) / "Library/processes/secrets") if os.environ.get("VAULT") else (Path(__file__).resolve().parents[1] / "secrets")
_KEYS = json.load(open(_SECRETS / "command-centre-supabase-keys.json"))
_SRK = _KEYS["service_role_key"]
_BASE = _KEYS["url"] + "/rest/v1"

def publish(report_key: str, period_date: str, payload: dict) -> bool:
    """Insert one immutable snapshot row. Returns True on success."""
    body = json.dumps({"report_key": report_key, "period_date": period_date, "payload": payload}).encode()
    req = urllib.request.Request(f"{_BASE}/snapshots", method="POST", data=body, headers={
        "apikey": _SRK, "Authorization": f"Bearer {_SRK}", "Content-Type": "application/json",
        "Accept-Profile": "reports", "Content-Profile": "reports", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 201, 204)
    except Exception as e:
        print(f"  CC PUBLISH FAILED ({report_key} {period_date}): {e}")
        return False

if __name__ == "__main__":
    import sys
    ok = publish(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]))
    print("published" if ok else "FAILED"); sys.exit(0 if ok else 1)


def pulse(source: str, summary: str) -> bool:
    """Minor-automation heartbeat -> the Automations Log page (one snapshot per day,
    latest pulse list per source). Appends source+summary to today's log payload."""
    import datetime, json as _json, urllib.request as _rq
    period = datetime.date.today().isoformat()
    # read today's existing payload (if any) and append
    try:
        req = _rq.Request(f"{_BASE}/snapshots?report_key=eq.automations-log&period_date=eq.{period}&select=payload&order=published_at.desc&limit=1",
            headers={"apikey": _SRK, "Authorization": f"Bearer {_SRK}", "Accept-Profile": "reports"})
        rows = _json.loads(_rq.urlopen(req, timeout=20).read())
        entries = rows[0]["payload"].get("entries", []) if rows else []
    except Exception:
        entries = []
    now = datetime.datetime.now().strftime("%H:%M")
    entries.append({"t": now, "source": source, "summary": summary[:400]})
    rows_html = "".join(f"<tr><td style='padding:6px 10px;color:#667;white-space:nowrap'>{e['t']}</td><td style='padding:6px 10px;font-weight:700'>{e['source']}</td><td style='padding:6px 10px'>{e['summary']}</td></tr>" for e in entries[-100:])
    html = ("<div style='font:14px/1.5 -apple-system,sans-serif;padding:14px'><h2 style='margin:0 0 10px'>Automation pulses — " + period + "</h2>"
            "<table style='border-collapse:collapse;width:100%;background:#fff;border:1px solid #e2e6f0;border-radius:10px'>" + rows_html + "</table></div>")
    return publish("automations-log", period, {"entries": entries, "subject": f"Automation pulses {period}", "html": html})
