#!/bin/bash
# property-sync.sh — nightly: run the live-state probe (writes every property card +
# the dashboard feed), then push the feed to the dashboard repo so Vercel redeploys.
# Wired by launchd: com.peterashcroft.property-sync (00:05 daily). Mirrors garmin-daily-pull.

SCRIPTS="/Users/peterashcroft/Second Brain/Library/processes/scripts"
FEED="/Users/peterashcroft/Second Brain/Library/processes/property-state.json"
DASH="/Users/peterashcroft/code/command-centre"  # repointed 2026-06-11 (P3): dashboard lives in the Command Centre at /m/properties
PY=/opt/homebrew/bin/python3
LOG="/Users/peterashcroft/Library/Logs/property-sync.log"

echo "=== property-sync $(date '+%Y-%m-%d %H:%M') ===" >> "$LOG"

# --- connectivity preflight (added 2026-06-14) ---------------------------------------------
# A local network outage (overnight DNS drop, incident 2026-06-14) or the recurring Deco
# HomeShield "Web Protection" block of Vercel's 216.150.0.0 range (lesson 2026-06-11) makes
# EVERY probe fail → the whole dashboard goes red AND property-alert.py fires a false "all
# sites DOWN" email. The sites are up; the Mac's uplink isn't. Refuse the WHOLE sync (probe +
# alert + project-state + capability-registry) unless this host can reach the wider internet
# AND Vercel. (property-live-state.py carries a matching guard for standalone/hook runs.)
NET_OK=0
for a in https://github.com https://www.google.com https://1.1.1.1; do
  curl -s --max-time 10 -o /dev/null "$a" 2>/dev/null && { NET_OK=1; break; }
done
if [ "$NET_OK" = 0 ]; then
  echo "PREFLIGHT ABORT: no outbound connectivity — not running (a local network fault must not mark every site DOWN). Last-known-good preserved." >> "$LOG"
  echo "done $(date '+%H:%M') (preflight-abort: no-internet)" >> "$LOG"; exit 0
fi
if ! curl -s --max-time 10 -o /dev/null https://canary-detect.com 2>/dev/null; then
  echo "PREFLIGHT ABORT: internet OK but Vercel (216.150.0.0) unreachable from this host — almost certainly the local Deco HomeShield 'Web Protection' block (lesson 2026-06-11), NOT a site outage. Fix: Deco app → HomeShield → Web Protection → OFF. Not overwriting state." >> "$LOG"
  echo "done $(date '+%H:%M') (preflight-abort: vercel-range-blocked)" >> "$LOG"; exit 0
fi
echo "preflight OK — internet + Vercel reachable" >> "$LOG"

cd "$SCRIPTS" || exit 1
"$PY" property-live-state.py --apply >> "$LOG" 2>&1
echo "probe exit: $?" >> "$LOG"
# NEW-problem email alert (once per problem; toggle: commandcentre.info/settings/notifications)
"$PY" property-alert.py >> "$LOG" 2>&1
echo "alert exit: $?" >> "$LOG"
# FULL sweep (plan §Cadence "midnight cron — all properties + projects, also refreshes the registry")
"$PY" project-state.py --apply >> "$LOG" 2>&1
echo "project-state exit: $?" >> "$LOG"
"$PY" capability-registry.py --apply >> "$LOG" 2>&1
echo "capability-registry exit: $?" >> "$LOG"

# push the feed to the dashboard (only if it changed) -> Vercel auto-redeploys
if [ -f "$FEED" ] && [ -d "$DASH" ]; then
  cd "$DASH" || exit 1
  # rebase onto origin FIRST — other clones (manual code work) + the garmin cron
  # push to the same repo, so this clone falls behind and a bare push gets rejected
  # ("fetch first"). Without this the dashboard silently stops refreshing. (Fixed 12 Jun.)
  git fetch -q origin 2>>"$LOG" && git rebase -q origin/main >> "$LOG" 2>&1 || echo "WARN: rebase failed, continuing" >> "$LOG"
  cp "$FEED" "$DASH/data/property-state.json"
  if ! git diff --quiet data/property-state.json 2>/dev/null; then
    git add data/property-state.json
    git commit -q -m "nightly sync $(date +%F)" >> "$LOG" 2>&1
    if git push -q >> "$LOG" 2>&1; then
      echo "pushed feed -> dashboard redeploys" >> "$LOG"
    else
      echo "ERROR: feed push rejected (clone behind origin?) — dashboard not refreshed" >> "$LOG"
    fi
  else
    echo "feed unchanged, no push" >> "$LOG"
  fi
fi
# heartbeat + anomaly digest -> today's daily note (brain-resume + the morning briefing read it).
# Single source of the heartbeat line is property-heartbeat.py — absence-aware (Pass-12): a reader
# running it later derives staleness from the feed's own timestamp, so a dead nightly job is caught.
"$PY" "$SCRIPTS/property-heartbeat.py" --write-daily >> "$LOG" 2>&1   # absolute: cwd is $DASH here, not $SCRIPTS
echo "done $(date '+%H:%M')" >> "$LOG"
