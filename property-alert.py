#!/usr/bin/env python3
"""property-alert.py — email Pete when the nightly sweep finds NEW problems.

Runs from property-sync.sh straight after the sweep writes property-state.json.
Compares the current needs-attention set against a local ledger so each problem
alerts ONCE (and clears from the ledger when it recovers, so a relapse re-alerts).
Respects the Command Centre toggle Settings → Notifications → "Property DOWN alerts"
(public.app_settings key `notify_property_down`).

Added 2026-06-11 (Command Centre audit item: alert hook for DOWN properties).
"""
import json, pathlib, subprocess, sys, urllib.request
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")
sys.path.insert(0, f"{VAULT}/Library/processes/scripts")

FEED = pathlib.Path(f"{VAULT}/Library/processes/property-state.json")
LEDGER = pathlib.Path.home() / ".cc-property-alert-ledger.json"
GMAIL = f"{VAULT}/Library/processes/scripts/gmail-api.py"
TO = "pete.ashcroft@sygma-solutions.com"
KEYS = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))


def alerts_enabled() -> bool:
    try:
        req = urllib.request.Request(
            KEYS["url"] + "/rest/v1/app_settings?key=eq.notify_property_down&select=value",
            headers={"apikey": KEYS["service_role_key"], "Authorization": f"Bearer {KEYS['service_role_key']}"})
        rows = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return bool(rows[0]["value"]) if rows else True
    except Exception:
        return True  # never let a settings hiccup silence a real outage


def main():
    if not FEED.exists():
        print("no feed; skip"); return
    feed = json.loads(FEED.read_text())
    current = {}
    for p in feed.get("properties", []):
        drift = p.get("drift") or []
        if drift:
            current[p.get("name", "?")] = "; ".join(str(d) for d in drift)[:300]

    previous = {}
    if LEDGER.exists():
        try: previous = json.loads(LEDGER.read_text())
        except Exception: previous = {}

    new = {k: v for k, v in current.items() if k not in previous}
    recovered = [k for k in previous if k not in current]
    LEDGER.write_text(json.dumps(current, indent=1))

    # heartbeat to the Automations Log page (non-fatal)
    try:
        import cc_publish
        total = len(feed.get("properties", []))
        cc_publish.pulse("property-sweep", f"{total} properties checked · {len(current)} need attention · {len(new)} new · {len(recovered)} recovered")
    except Exception as e:
        print(f"pulse failed (non-fatal): {e}")

    if not new:
        print(f"no new problems ({len(current)} ongoing, {len(recovered)} recovered)"); return
    if not alerts_enabled():
        print(f"{len(new)} new problem(s) but alerts are OFF in Settings → Notifications"); return

    lines = [f"The nightly property sweep found {len(new)} new problem(s):", ""]
    for name, d in sorted(new.items()):
        lines.append(f"• {name}: {d}")
    if recovered:
        lines.append("")
        lines.append("Recovered since last alert: " + ", ".join(sorted(recovered)))
    lines += ["", "Full picture: https://commandcentre.info/m/properties (Needs attention filter)",
              "", "Turn these alerts off: https://commandcentre.info/settings/notifications"]
    subject = f"Property alert: {len(new)} new problem(s) — {', '.join(sorted(new)[:3])}{'…' if len(new) > 3 else ''}"
    r = subprocess.run([sys.executable, GMAIL, "send", TO, subject, "\n".join(lines)],
                       capture_output=True, text=True, timeout=120)
    print("alert sent" if r.returncode == 0 else f"ALERT SEND FAILED: {r.stderr[:200]}")


if __name__ == "__main__":
    main()