---
name: garmin-daily-pull
description: DISABLED 2026-05-28 — migrated to native launchd at ~/Library/LaunchAgents/com.peterashcroft.garmin-daily-pull.plist. Same schedule (07:00 + 17:00 Atlantic/Canary). Logs at ~/Library/Logs/garmin-daily-pull.{out,err}.log. Re-enable only if rolling back.
---

> [!info] Recovery mirror
> Canonical cron prompt at `~/Documents/Claude/Scheduled/garmin-daily-pull/SKILL.md`. Vault recovery mirror at `Library/skills/scheduled/garmin-daily-pull/SKILL.md`. Keep both in lockstep.

Run the Garmin daily pull.

## Execution, READ THIS FIRST

Use Desktop Commander, NOT workspace bash (workspace bash has a 45s cap; Garmin auth + git push can exceed it). Per [[Library/lessons/2026-05-02-scheduled-task-skill-md-uses-dc]].

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/garmin-daily-pull.py" > /tmp/garmin-daily-pull.log 2>&1 &
  timeout_ms: 5000
```

Then poll `/tmp/garmin-daily-pull.log` every 10-20s (via `mcp__Desktop_Commander__read_file`) until it prints the final summary line (`- {date}: Sleep ... steps`, or a run/error line).

## What the script does

1. Pulls **yesterday + today** from Garmin (sleep, HRV, body battery, training readiness, daily stats, activities, **training status / ACWR / VO2**, **race predictions**); writes `Personal/health/garmin/{date}.md` + `data/{date}.json` for each. **Garmin-native dating: file `D` = the sleep you woke from on D's morning + D's morning HRV/readiness + D's daytime activity — no shift, matches Garmin Connect; the dashboard's latest card is today.** Each activity carries: training effect (aerobic + anaerobic + label), HR-zone breakdown (Z1-Z5 seconds), training load, splits, and sport-specific stats (run pace/cadence, swim SWOLF/fastest-100, bike power/TSS/IF). See [[garmin-api-configuration]]. Each file also gets a `signoff` estimate (last Claude/Cowork session activity the night before — shows on the dashboard as "Finished work ~HH:MM last night"); Pete confirms/corrects it at the morning Resume, and the cron preserves a confirmed value across re-runs.
2. Writes weekly PF snapshot JSON(s) under `data/weekly/`.
3. **Sync-first git push**: `git fetch + git pull --rebase --autostash origin main` BEFORE staging today's data, then commit + push → Vercel auto-deploys (https://pete-health-dashboard.vercel.app). The rebase-first ordering is essential because the clone is shared with interactive Claude Code sessions that may push UI commits between cron runs. A push failure now surfaces as ` | PUSH FAILED (…)` in the daily-note summary line — never silently swallowed. Per [[Library/lessons/2026-05-25-garmin-daily-pull-must-rebase-before-push]].
4. Appends a status line to today's daily note under `## Garmin daily pull (Automated)`.

## Why twice daily (07:00 + 17:00)

- **07:00** captures last night's sleep + this morning's HRV / readiness / signoff — primary "start of day" snapshot.
- **17:00** picks up the day's training **activities** (most workouts done by 5pm), refreshes **training status**, **ACWR ratio**, **recovery time**, and the Z2-discipline check while the dashboard is still useful to look at. Late-evening activities completed after 5pm still get picked up by tomorrow's 07:00.
- Idempotent: a 5pm re-run that finds no new data prints `No JSON changes to push` and the dashboard doesn't churn. A confirmed sign-off from the morning is preserved (never overwritten by the 5pm detect).

## After the run

Read the log; confirm the headline numbers (sleep score, HRV status, readiness, activities) AND whether it printed `Pushed JSON snapshot to dashboard repo` or `No JSON changes to push`. Surface both in the run report so the notification is useful.

If the script errored (token expired, Cloudflare bounce, rate-limit), log under the daily note section and do NOT retry — Pete will see it next session. Most common fix: `pip install --upgrade --break-system-packages garminconnect curl_cffi ua-generator`.

Source of truth for auth + library + date semantics + workload→sleep analysis alignment: `/Users/peterashcroft/Second Brain/Library/processes/garmin-api-configuration.md`.

Downstream consumers (don't break the JSON contract): brain Resume (last-night line), pf-journal (Cowork session reads the vault file as ground truth), pf-weekly-loop (Monday 7-day roll-up), and **pete-health-dashboard** (the live Vercel site — consumes `data/garmin/*.json` + `data/weekly/*.json`).

## Related lessons

- [[Library/lessons/2026-05-26-garmin-splits-use-lap-endpoint-not-typed-summaries]] — Garmin splits API: use `/splits`, not `splitSummaries` or `/typedsplits`. Applies to any new helper field added to the Garmin pull.