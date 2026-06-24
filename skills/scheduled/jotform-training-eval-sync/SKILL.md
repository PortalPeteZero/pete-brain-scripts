---
name: jotform-training-eval-sync
description: Weekly sync of Sygma's JotForm Training Evaluation Form (id 201324458767056) to the live dashboard at https://sygma-training-eval-dashboard.vercel.app
---

Run the Sygma JotForm Training Evaluation weekly sync.

# Execution — READ THIS FIRST

Run via Desktop Commander (NOT workspace bash — bash has a 45s cap that may be hit on busy weeks).

Use `mcp__Desktop_Commander__start_process` with this exact command:

```
/opt/homebrew/bin/python3 "/Users/peterashcroft/Second Brain/Library/processes/scripts/jotform-training-eval-sync.py"
```

Then poll the process output (`mcp__Desktop_Commander__read_process_output`) until completion (typically 10-30 seconds; up to a couple of minutes on weeks with many new submissions).

# What the script does (do NOT re-derive these steps — let the script handle them)

1. Reads the most recent cached submission timestamp from `Properties/Sygma Solutions Website/data/training-evaluations/submissions-*.json`.
2. Calls the JotForm API to fetch only submissions newer than that timestamp (incremental, dedup-aware).
3. Writes new rows into year-bucketed files.
4. Rebuilds `all-normalised.json` (re-applies the latest YAML normaliser rules).
5. Calls `jotform-training-eval-aggregate.py` to regenerate dashboard JSON files in `~/code/sygma-training-eval-dashboard/data/`.
6. `git push` the dashboard repo (SygmaSol/sygma-training-eval-dashboard) → Vercel auto-deploys.
7. Appends one line to today's daily note under `## JotForm Training Eval sync (Automated)`.

# After the script completes

- Read the last lines of the script output to confirm "Fetched N new submissions" and "Git: pushed" (or "no changes" if quiet week).
- The daily-note line is your audit trail — nothing more needed.

# Failure handling

- If the script errors out (network blip, JotForm API down, GitHub push failure):
  - Capture the error message and append a failure line to today's daily note under `## JotForm Training Eval sync (Automated)`: `- FAILED at <step>: <one-line error>`.
  - Do NOT email Pete; this is a low-urgency sync, next week's run will catch up.
  - The script is idempotent — partial state is safe; re-running picks up where it left off.

# Live URL

https://sygma-training-eval-dashboard.vercel.app (noindex, share-by-link)

# Source-of-truth files (do not edit; this task only invokes the script)

- Script: `Library/processes/scripts/jotform-training-eval-sync.py`
- Aggregator: `Library/processes/scripts/jotform-training-eval-aggregate.py`
- Normaliser + YAMLs: `Library/processes/scripts/jotform-normalise.py` + `sygma-trainer-roster.yaml` + `sygma-course-taxonomy.yaml`
- API helper: `Library/processes/scripts/jotform-api.py`
- Dashboard repo: `~/code/sygma-training-eval-dashboard` (GitHub: SygmaSol/sygma-training-eval-dashboard)

## Related lessons

- [[Library/lessons/2026-05-30-jotform-api-tz-is-dst-aware-us-eastern-not-fixed-utc5]] — JotForm API timestamps are DST-aware `America/New_York`, NOT fixed UTC-5. Fires on any date-window query or normaliser logic touching JotForm timestamps.
