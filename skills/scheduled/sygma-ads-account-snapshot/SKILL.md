---
name: sygma-ads-account-snapshot
description: Daily refresh of the live Sygma Google Ads account state into the vault doc (campaigns / ad groups / keywords / ads / sitelinks / negatives / conversion actions / 30d metrics). Quiet unless a structural change is detected.
---

> [!info] Recovery mirror
> Canonical cron prompt at `~/Documents/Claude/Scheduled/sygma-ads-account-snapshot/SKILL.md`. Vault recovery mirror at `Library/skills/scheduled/sygma-ads-account-snapshot/SKILL.md`. Keep both in lockstep.

## Execution -- READ THIS FIRST

The pull hits the Google Ads API and takes ~60-120s, which exceeds the 45-second workspace bash cap. Run the script via Desktop Commander, not the Bash tool:

```
mcp__Desktop_Commander__start_process with command:
  cd "/Users/peterashcroft/Second Brain" && nohup python3 Library/processes/scripts/ads-snapshot.py > /tmp/sygma-ads-snapshot-cron.log 2>&1 &

then poll /tmp/sygma-ads-snapshot-cron.log via mcp__Desktop_Commander__read_file every ~30s
until it contains "SNAPSHOT_COMPLETE" or "SNAPSHOT_FAILED".
```

The script does ALL the file work itself (refreshes `Properties/Sygma Solutions Website/data/google-ads-account.md` + `.json`, splices the AUTOGEN block, bumps the frontmatter `updated:` date). Your only jobs are: run it, then handle the daily-note line + any failure email based on what the log says.

## Goal

Keep `Properties/Sygma Solutions Website/data/google-ads-account.{md,json}` current with the live Google Ads account (advertiser 173-909-0181 under MCC 220-653-9186) so no session ever reports off a stale snapshot. Runs daily but stays quiet in the daily note unless the account structure actually changed.

## Steps

1. **Run the script via Desktop Commander** (pattern above). It pulls the full account, compares against the previous JSON snapshot, refreshes both vault files, and prints one of:
   - `NO_STRUCTURAL_CHANGES` -- nothing structural moved since yesterday.
   - `CHANGES_DETECTED: <summary>` -- e.g. `sitelinks added [HSG47 Explained→…]; keywords +2`.
   - `SNAPSHOT_FAILED: <error>` (on stderr) -- the pull or write failed.

2. **Poll the log** until `SNAPSHOT_COMPLETE` (success) or `SNAPSHOT_FAILED` appears.

3. **Daily-note line -- ONLY on change.** Read the log's `CHANGES_DETECTED:` / `NO_STRUCTURAL_CHANGES` line.
   - If `NO_STRUCTURAL_CHANGES`: **write nothing to the daily note.** The vault files were still refreshed silently; a no-op doesn't need a line. (This keeps the daily note quiet.)
   - If `CHANGES_DETECTED: <summary>`: append this section to `Daily/{today}.md` (create the file from the daily template if it doesn't exist yet -- a cron may run before the morning briefing):
     ```
     ## Sygma Ads Snapshot (Automated)
     - Structural change detected: {summary}. Vault doc refreshed → [[Properties/Sygma Solutions Website/data/google-ads-account]].
     ```

4. **On failure** (`SNAPSHOT_FAILED`, or no `SNAPSHOT_COMPLETE` after a reasonable poll window): send a fallback email via `Library/processes/scripts/gmail-api.py send` to `pete.ashcroft@sygma-solutions.com`, subject `Sygma Ads Snapshot FAILED {today}`, body = the error tail from the log. Also append a one-line `## Sygma Ads Snapshot (Automated)` failure note to the daily note so the next Resume sees it.

## Don'ts

- **Don't dispatch agents** -- single linear orchestration in one Desktop Commander process (website-adjacent work + the one-project rule).
- **Don't act on findings.** If the diff shows a wrong sitelink / new waste / structural drift, the daily-note line surfaces it for Pete -- do NOT change the account. Pete decides + applies. (Ads are Pete's remit, but this cron is a read-only mirror.)
- **Don't hand-edit `google-ads-account.md`'s AUTOGEN block** -- the script owns it. Manual notes go only in the `## Recent changes ledger` / `## Decisions` sections below the `<!-- AUTOGEN:END -->` marker.
- **Don't write a daily-note line when nothing changed.** Quiet-unless-changed is the whole point.

## Cross-references
- Script: `Library/processes/scripts/ads-snapshot.py`
- Vault doc: [[Properties/Sygma Solutions Website/data/google-ads-account]]
- Ads config: [[google-ads-api-configuration]]
- Sibling cron (emailed digest, fortnightly): `sygma-ads-fortnightly-report`
- Registry: [[scheduled-tasks]]