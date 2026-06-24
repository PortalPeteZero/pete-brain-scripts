---
name: cd-tom-jobs-calendar-sync-noon
description: Twice-daily Odoo -> Tom Google Calendar sync (noon run, 12:30). One-way mirror with full enrichment + jobtype-coloured events.
---

Run the noon Odoo -> Tom Google Calendar sync (catches morning bookings).

## Execution -- READ THIS FIRST

**You MUST invoke this script via Desktop Commander (`mcp__Desktop_Commander__start_process`), not workspace bash.** Run typically takes 60-120 seconds (Odoo events + lead pull + Google Calendar list + per-event hash check). Workspace bash has a hard 45-second cap that will kill it. Desktop Commander runs natively on Pete's Mac with no cap.

**Pattern:**

```python
mcp__Desktop_Commander__start_process(
    command='cd "/Users/peterashcroft/Second Brain/Library/processes/scripts" && '
            'nohup python3 -u cd-tom-jobs-calendar-sync.py '
            '> /tmp/calsync-noon-cron.log 2>&1 & '
            'echo "PID=$!"',
    timeout_ms=10000,
)
# Capture the PID, then poll until ps -p $PID returns nothing.
# Final summary: grep "run-summary" /tmp/calsync-noon-cron.log
```

The noon run is silent in the daily vault note (only the 18:00 evening run appends a summary block). It DOES write to its per-day log file at `Library/processes/scripts/_logs/cd-tom-jobs-calendar-sync-{YYYY-MM-DD}.log`.

## Behaviour
- Pulls Tom's calendar events from Odoo for the rolling window (today − 14 days .. today + 60 days, partner_id 12, active=true).
- Resolves each event's CRM lead and geocodes the lead's address via Google Geocoding API (cached at `Library/processes/scripts/_cache/geocode-calendar-sync.json`, separate from the photo-sort cache so the two daily 18:00 runs don't race).
- Pulls existing managed events on Tom's primary Google Calendar (filter: `extendedProperties.private.synced_by = cd-tom-jobs-calendar-sync`).
- For each Odoo event, builds a Google event payload with:
  - **Title:** clean normalised form for folder-worthy job types (e.g. `VLS - Rachel Hicks - Tias`); raw Odoo title preserved for non-job entries (fiestas, ITV, "keep clear", reminders).
  - **Description:** Customer / Contact / Phone / **Address** (see "Address sources" below) / one-tap Google Maps link / CRM stage / lead notes (HTML stripped) / Lead-in-Odoo URL / fallback `[odoo:N]` marker.
  - **Location:** the chosen site address string (so iOS / Android Calendar shows a map preview).
  - **Start/End:** Odoo UTC timestamps converted to `Atlantic/Canary` local. All-day events handled.
  - **colorId:** mapped from job type per the [colour map](#colour-coding).
  - **extendedProperties.private:** `odoo_event_id`, `odoo_lead_id`, `synced_by`, `first_synced`, `last_synced`, `content_hash` (SHA-256 of normalised payload, used to skip pointless PATCHes).
- Computes `content_hash` and either CREATEs (no existing event), PATCHes (hash changed), or skips (hash unchanged). The description deliberately contains NO per-run timestamp so hashes are stable across runs.
- Anything left in `existing_by_odoo_id` after processing -- i.e. events we managed last run but no longer in Odoo -- gets deleted (orphan cleanup).
- Manual events Tom may have added himself (no `synced_by` extended property) are never touched.

Authentication is service account + Domain-Wide Delegation impersonating `tom.robertson@canary-detect.com` -- handled inside `calendar-api.py`.

Source of truth = Odoo calendar.event. Tom's Google Calendar is a read-only mirror.

## Classifier rule (updated 2026-05-04)

`classify_jobtype` returns the **rightmost** matching jobtype keyword in the title (was first-match-wins until 2026-05-04). Supports Nicola's title-accumulation convention ([[cd-calendar-event-naming-convention]]) where one CRM lead spawns a chain of events whose titles accumulate the previous jobtypes:

- `VLS - Customer` -> VLS
- `VLS - Repair - Customer` -> Repair
- `VLS - Repair - Reinstatement - Customer` -> Reinstatement (Tomato colour, "Reinstatement - Customer - Location" cleaned title)

Backwards-compatible with single-jobtype titles (the historic shape) -- when only one keyword is in the title, rightmost-match equals first-match.

## Address sources (updated 2026-05-04)

Structured `street/street2/city/zip` on the lead are the INVOICE address and are no longer used.

Two sources are consulted: `lead.x_studio_char_field_3qWjM` (Studio "Location (Survey)" field) and `calendar.event.location` (the free-text event Location). The merge rule (`pick_site_address` in the script):

- both empty -> blank, no Maps link
- only one populated -> that one
- both populated, one contains the other (case/whitespace-insensitive) -> the more specific (longer) one (handles `"PB"` vs `"Calle X, PB"` abbreviation cases)
- both populated, Survey is short (≤8 chars) and Calendar is longer -> Calendar wins (handles `"PB"` vs `"50 La Carabela"`)
- both populated, both substantial, neither contains the other -> Survey wins as primary; description renders a `Calendar:` line with `(differs)` so Tom sees the disagreement.

The chosen site address is what gets geocoded for the Maps link and what's written to the calendar event's `location` field.

Spec doc: `Library/processes/tom-jobs-calendar-sync.md`

Pair task: `cd-tom-jobs-photo-sort` (daily 18:00, sorts Tom's field photos into per-job folders).
Sister task: `cd-tom-jobs-calendar-sync-evening` (18:00 run, same script).

## Colour-coding (jobtype → Google colorId)

| Colour | id | Used for |
|---|---|---|
| Blueberry | 9 | VLS, PLS, Drain Survey, Community Survey |
| Tomato | 11 | Repair, Reinstatement |
| Basil | 10 | LeakGuard Install, LeakGuard Check |
| Graphite | 8 | Initial Visit |
| Banana | 5 | EcoFinish |
| Tangerine | 6 | Epoxy |
| Lavender | 1 | Pump, Civils, Site Clear, Admin |
| Sage | 2 | Fiesta, ITV, Keep clear, Holiday, Reminder |

## Daily note

The 18:00 (evening) run appends a one-line summary block to today's `Daily/YYYY-MM-DD.md` -- the noon run is silent in the vault note (its summary lives in the per-day log file at `Library/processes/scripts/_logs/cd-tom-jobs-calendar-sync-{YYYY-MM-DD}.log`).

If the script errors out, capture the error and email Pete with the traceback.

## Steady state

After first-run completed at 19:29 on 2026-05-02 (76 events created, 7 leftover Odoo built-in sync events wiped, Odoo's built-in Google sync disabled), normal runs should typically show `created=0 patched=0 deleted=0 skipped_unchanged=N`. Any non-zero create / patch / delete count means real Odoo activity.