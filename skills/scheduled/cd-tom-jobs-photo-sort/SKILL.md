---
name: cd-tom-jobs-photo-sort
description: DISABLED 2026-05-28 — migrated to native launchd at ~/Library/LaunchAgents/com.peterashcroft.cd-tom-jobs-photo-sort.plist. Same schedule (18:00 Atlantic/Canary). Logs at ~/Library/Logs/cd-tom-jobs-photo-sort.{out,err}.log. Re-enable only if rolling back.
---

Run the daily photo-sort task for Tom's field shots.

## Execution -- READ THIS FIRST

**You MUST invoke this script via Desktop Commander (`mcp__Desktop_Commander__start_process`), not workspace bash.** The script typically runs 3-5 minutes (Drive API + Odoo pulls + per-folder iteration). Workspace bash has a hard 45-second sandbox cap that will kill it mid-`managed-folders-prefetched`. Desktop Commander runs natively on Pete's Mac with no cap. Even DC's own session tracker may drop your read mid-run, but the Python process keeps running -- so DON'T rely on `read_process_output` staying attached.

**Pattern:**

```python
# 1. Launch in background (returns immediately)
mcp__Desktop_Commander__start_process(
    command='cd "/Users/peterashcroft/Second Brain/Library/processes/scripts" && '
            'nohup python3 -u cd-tom-jobs-photo-sort.py '
            '> /tmp/photosort-cron.log 2>&1 & '
            'echo "PID=$!"',
    timeout_ms=10000,
)
# Capture the PID from the output.

# 2. Poll completion. Don't try to keep a session attached -- the runtime is too long.
#    Use pgrep + log file size + grep for "run-complete" to detect finish.
mcp__Desktop_Commander__start_process(
    command='ps -p {PID} -o pid= 2>/dev/null && echo ALIVE || echo DONE; '
            'grep "run-complete" /tmp/photosort-cron.log | tail -1',
    timeout_ms=10000,
)
# Repeat with sleep between calls until "DONE" appears.

# 3. When done, read the run summary from the log:
mcp__Desktop_Commander__start_process(
    command='grep -E "run-complete|map-written|readme-written|per-month-maps|swept" '
            '/tmp/photosort-cron.log',
    timeout_ms=10000,
)
```

The script appends its own block to `Daily/YYYY-MM-DD.md` (see `_persist_run_state` in the script), so you don't need to write the daily note yourself -- just verify the entry landed.

## What the script does (no arguments needed)

Behaviour:
- Pulls Tom's calendar events from Odoo for the rolling window (today − 60 days .. today + 14 days, partner_id 12).
- Resolves each event's CRM lead and geocodes the **lead's site address** via Google Geocoding API (cached at `Library/processes/scripts/_cache/geocode-photo-sort.json`). The site address comes from `lead.x_studio_char_field_3qWjM` (Studio "Location (Survey)" field) preferred, falling back to the first non-empty `location` field across the lead's calendar events. Structured `street/street2/city/zip` are the **invoice address** and are no longer used for GPS matching (this was causing photos to fail to match because the invoice address often points to the customer's HQ, not the site). Updated 2026-05-04.
- **Classifier rule (updated 2026-05-04):** `classify_jobtype` returns the **rightmost** matching jobtype keyword in the title, supporting Nicola's title-accumulation convention ([[cd-calendar-event-naming-convention]]). A title like `VLS - Repair - Reinstatement - Customer` classifies as Reinstatement (the *current* job), not VLS.
- **Anchor-month locking (updated 2026-05-04):** when a per-lead folder is created, it goes into the **earliest** event's month. Once created, the folder NEVER moves between months -- later events update the folder name but the folder stays in its anchor month forever. This avoids cross-month-move races and preserves Tom's mental model. Log line `folder-stays-anchored` records cases where a later event would have moved the folder under the old behaviour but the new logic keeps it in place.
- **Per-lead GPS radius based on geocode precision (added 2026-05-04 evening):** when a lead's geocode resolves to `APPROXIMATE` precision (Google's signal that the address only matched a town centre / area, not a street), the GPS-match radius widens from `1000m` (`GPS_NEAREST_MAX_M`) to `3000m` (`GPS_NEAREST_MAX_M_APPROX`). Catches photos taken at villas whose lead's only address is "PB", "Tias", etc. Filtered at candidate-collection time so the existing ratio + far-fallback logic still prevents misattribution when a tighter-geocoded lead is also nearby.
- **Folder resurrection for swept leads (added 2026-05-04 evening):** if a photo matches a lead via GPS+date, but the lead's folder doesn't exist (because a previous sweep deleted the empty folder of a closed-stage lead and added the lead to `do_not_recreate`), the script now **resurrects** the folder fresh and files the photo into it. The arrival of a new photo for that lead is itself the signal that the job is "live again" -- e.g. Tom uploaded photos a week after invoicing. Without this, photos for swept leads were silently dropped. Counter: `folders_resurrected`. First live run with this fix: 7 folders resurrected, 73 previously-stuck photos filed.
- **Eager `_unmapped/` per month (added 2026-05-04 evening):** every existing `NN MMM YY` folder under tom/ gets a `_unmapped/` subfolder pre-created at run start, even if no photo has been dropped in unmapped for that month yet. This makes the empty-vs-full state of `_unmapped/` the meaningful signal for "everything filed" (empty) vs "needs manual review" (non-empty). Previously the absence of `_unmapped/` was ambiguous (no photo unmapped vs no photos at all).
- **Markdown dedup at upload time (added 2026-05-04 evening):** `_upload_md` finds ALL files matching the target name in the parent folder, sorts by createdTime, keeps the OLDEST as the keeper (deterministic), PATCHes its content, and trashes any duplicates. This auto-cleans up the README/MAP duplicate-accumulation bug caused by Drive eventual-consistency in `files.list` (where rapid back-to-back runs created parallel copies because the lookup didn't see the first). One-time cleanup on 2026-05-04: 96 duplicate `README.md` siblings trashed at tom/ root. Counter: `md_dupes_trashed`.
- **Odoo writeback (Step 5c, added 2026-05-04):** for every managed Drive folder, write back to the linked `crm.lead`:
  - `x_studio_photos_link` -- the Drive folder URL (`https://drive.google.com/drive/folders/{id}`), so Nicola can jump to photos from the lead view
  - `x_studio_photos_uploaded` -- `'yes'` once at least one non-folder child exists in the folder. Latching: once `'yes'`, never reverts to `'no'`. Detects manual uploads automatically because the count is taken from Drive on every run, regardless of whether the photo got there via the cron or via a staff drag-drop.
  Idempotent -- only writes when the value would actually change. Counters added: `photos_link_written`, `photos_uploaded_flipped`.
- Manages a folder per lead inside Drive at `Pictures/tom/{MM Mon YY}/` with canonical multi-event names (e.g. `Repair + VLS - Karl Fuchs (Karl) - CT - 2026-04-08, 2026-04-22, 2026-04-29`).
- Walks every photo across `Pictures/tom/`, each month folder, and per-month `_unmapped` buckets.
- **Round 1 (tight, ±1 day):** matches each photo to a lead by EXIF GPS within 1000m AND EXIF date within ±1 day of any of that lead's events. Confident same-day matches.
- **Round 2 (wide, ±14 days):** for whatever's still in per-month `_unmapped`, retries with a 2-week window either side of the photo date. GPS distance still capped at 1000m so this only catches follow-up visits to the same property.
- Per-month `_unmapped/` buckets created on demand inside each month folder (NOT a single root-level bucket).
- **Folder colour:** `_step_recolour` runs every cycle on EVERY managed folder. ORANGE (`#ff7537`) when folder has any content, GREY (`#8f8f8f`) when empty. Manually-dropped photos get caught the next run -- folder flips grey→orange.
- **Sweep:** empty folders are trashed when (a) the lead is in CLOSED stage AND >28 days past latest event, OR (b) the lead has been deleted from Odoo entirely. Swept lead IDs persist in `do_not_recreate_leads_*` chunked appProperties on `tom/` so they don't auto-recreate.
- **Files written every run** at `Pictures/tom/`:
  - `README.md` -- plain-English overview of the folder system (for Tom / Nicola / future readers)
  - `_MAP.md` (root) -- master index across all months with per-month tables of folders, photo counts, and notes
  - `{MM Mon YY}/_MAP.md` (per-month) -- slice of the root map covering only that month, including managed folders + legacy/unmanaged folders + unmapped queue
- **Photo allow-list:** the script only treats files matching the `_PHOTO_EXTS` set (jpg/jpeg/png/heic/heif/gif/tiff/webp/bmp/dng/raw/mov/mp4/m4v/3gp/avi) as photos. README.md / _MAP.md / .DS_Store / .txt etc are skipped. **Don't drop unrecognised file types into the photo folders** -- they'll just be ignored. If you need to add a new photo extension, edit `_PHOTO_EXTS` near the top of the script.
- Sends a single notification email to Pete + Tom + Nicola listing any new photos that landed in any per-month `_unmapped/` bucket on this run (one mail per photo, deduped via `unmapped_email_sent_at` appProperty).

Source of truth = Odoo calendar.event + crm.lead + Drive photo EXIF (Drive's `imageMediaMetadata.location/time`, populated automatically when iPhone uploads). The geocode cache is rebuildable at any time by deleting the cache file.

Spec doc: `Library/processes/tom-jobs-photo-workflow.md`

Pair task: `cd-tom-jobs-calendar-sync` (twice daily, mirrors Odoo events to Tom's Google Calendar).

## Daily note

If the script completes ok, append a short block to today's `Daily/YYYY-MM-DD.md`:

```
## Tom's photo sort (Automated)
- Run at 18:00 Atlantic/Canary
- Folders: created N, renamed N, moved N, swept N
- Photos: round-1 sorted N, round-2 sorted N, unmapped N
- _MAP.md: refreshed
```

If the script errors out, do not retry silently. Capture the error, append a failure note to today's daily note, and email Pete with the traceback.

## Tuning principles

- **GPS thresholds** live as constants near the top of `cd-tom-jobs-photo-sort.py`: `GPS_NEAREST_MAX_M=1000`, `GPS_RATIO_MULTIPLIER=3`, `GPS_FAR_FALLBACK_M=2000`, `GPS_DATE_TOLERANCE_DAYS_TIGHT=1`, `GPS_DATE_TOLERANCE_DAYS_WIDE=14`. Don't loosen without spot-check evidence that you're catching a missed correct match -- a too-loose window starts mis-attributing photos to neighbouring properties on the same week.
- **Vision is intentionally OFF.** Every Tom-job photo looks the same to Cloud Vision (pressure gauges, pipes, water meters), so content-match was matching the same generic keywords against every lead. GPS + date is the only reliable signal.
- **Sweep is opinionated.** Empty + closed-stage + >28 days = trash. Folder gets re-created if the same lead later gains a future event; otherwise it stays gone.
- **Date matching is EXIF-based,** never filename-based. `imageMediaMetadata.time` from Drive (which parses iPhone's EXIF DateTimeOriginal) is the authoritative photo date.