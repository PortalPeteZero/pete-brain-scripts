#!/usr/bin/env python3
"""
property-heartbeat.py — the sweep's heartbeat, absence-aware (plan Pass-12).

A heartbeat written only on a *successful* sweep can't catch a *no-run* (machine off / cron broken):
nothing gets written and the silence reads as "all fine." So this reporter derives freshness from the
feed's OWN `generated` timestamp — a stale timestamp IS the no-run signal. Used two ways:

  python3 property-heartbeat.py                # print the heartbeat line(s) — for brain-resume / the
                                               # morning-briefing cron to surface (catches absence live)
  python3 property-heartbeat.py --write-daily  # upsert the '## Property sync (Automated)' section into
                                               # today's daily note (called at the end of property-sync.sh)

Fresh (<25h)  → "✅ Property sweep …" + anomaly lines.
Stale / missing → "⚠️ PROPERTY SWEEP STALE/MISSING …" so a dead nightly job can't pass as healthy.
Always exits 0 (it's a reporter, never a gate).
"""
import json, os, sys, re
from datetime import datetime, timezone, date
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = VAULT
FEED = os.path.join(VAULT, "Library/processes/property-state.json")
STALE_HOURS = 25   # nightly job is 00:05; >25h means at least one night was missed

def hours_since(stamp):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", stamp or "")
    if not m:
        return None
    g = datetime(*[int(x) for x in m.groups()], tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - g).total_seconds() / 3600

def lines():
    if not os.path.exists(FEED):
        return ["⚠️ **PROPERTY SWEEP MISSING** — no feed at all. The nightly job has never produced state "
                "(launchd `com.peterashcroft.property-sync`). Live-state is unknown — treat every card as unverified."]
    try:
        feed = json.load(open(FEED, encoding="utf-8"))
    except Exception as e:
        return [f"⚠️ **PROPERTY SWEEP FEED UNREADABLE** ({e}) — live-state can't be trusted until the next clean sweep."]
    gen = feed.get("generated", "?")
    h = hours_since(gen)
    unknown = sum(1 for p in feed.get("properties", []) if p.get("live") == "unknown")
    anoms = feed.get("anomalies", [])
    if h is None or h > STALE_HOURS:
        age = f"{h:.0f}h ago" if h is not None else "unparseable timestamp"
        out = [f"⚠️ **PROPERTY SWEEP STALE** — last sweep {gen} ({age}). The nightly job may be dead "
               f"(launchd `com.peterashcroft.property-sync`); live-state may be wrong. Re-run "
               f"`Library/processes/scripts/property-sync.sh` and check the launchd job."]
        if anoms:
            out.append("- Last-known anomalies (now stale): " + "; ".join(a["name"] for a in anoms))
        return out
    out = [f"✅ **Property sweep** {gen} ({h:.0f}h ago) · {feed.get('count','?')} properties · "
           f"{feed.get('up','?')} live · {unknown} unknown · {len(anoms)} anomal{'y' if len(anoms)==1 else 'ies'}"]
    if anoms:
        out.append("- **⚠️ Needs a human:** " + "; ".join(f"{a['name']} ({', '.join(a['drift'])})" for a in anoms))
    else:
        out.append("- No anomalies — nothing down, no undeployed commits, every card has its service block.")
    return out

def write_daily():
    today = date.today().isoformat()
    note = os.path.join(VAULT, "Daily", f"{today}.md")
    section = "## Property sync (Automated)\n\n" + "\n".join(lines()) + "\n"
    existing = open(note, encoding="utf-8").read() if os.path.exists(note) else f"# {today}\n"
    existing = re.sub(r"\n*## Property sync \(Automated\).*?(?=\n## |\Z)", "", existing, flags=re.S)  # idempotent replace
    open(note, "w", encoding="utf-8").write(existing.rstrip() + "\n\n" + section)
    print("daily-note heartbeat written ->", note)

if __name__ == "__main__":
    if "--write-daily" in sys.argv:
        write_daily()
    else:
        print("\n".join(lines()))
    sys.exit(0)