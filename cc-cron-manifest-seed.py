#!/usr/bin/env python3
"""cc-cron-manifest-seed.py — one-off: generate the FIRST crons-manifest.json from the data we already have.

Base layer  = public.processes (name / description / trigger / entity / active) — the existing 45-cron seed.
Host layer  = the 20-Jun freeze ledger (which list a cron is in → its host + migration status).
Enrich layer= hand-authored {why, produces, cron, depends_on, doc_link, timezone} keyed by cron-key, merged on top.
Plus the two crons not in `processes`: the live Railway data-map-cron + the kept-alive drive-changes-watch capture cron.

After this runs once, crons-manifest.json is the DURABLE source — maintained by editing it, then `cc-cron-sync.py`.
Usage: python3 cc-cron-manifest-seed.py   (writes ../crons-manifest.json; never overwrites if it already exists unless --force)
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DUMP = "/tmp/processes_dump.json"
OUT = os.path.join(HERE, "..", "crons-manifest.json")

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

# --- host map from the 20-Jun freeze ledger (membership → host) ---
LAUNCHD = {"account-clancy-sync","account-email-ingest","account-weekly-review","ads-cc-publish",
    "backlinks-weekly-report","cc-map","cd-tom-jobs-photo-sort","garmin-daily-pull","gcal-twice-daily-sync",
    "oconnors-seo-report","payroll-backup","property-sync","training-soldo-cc-publish","utilisation-cc-publish",
    "vault-drive-sync"}
CLAUDE_CODE = {"sygma-ads-fortnightly-report","lanza-lates-sc-marker-monitor","sygma-daily-google-report",
    "lanza-lates-daily-owner-report"}
COWORK = {"calendar-colour-coder","cd-daily-briefing-sunday","cd-daily-briefing-weekdays","cd-monthly-finance-email",
    "cd-tom-jobs-calendar-sync-evening","cd-tom-jobs-calendar-sync-noon","cd-week-ahead-sunday","cd-weekly-finance-email",
    "consolidate-lessons-monthly","consolidate-memory-monthly","daily-briefing",
    "helper-script-registry-refresh","hub-reconcile","jotform-training-eval-sync","lanzarote-competitor-monitor",
    "monthly-nights-away-reminder","pf-journal-reminder","remittance-to-xero","staff-master-sync",
    "sygma-ads-account-snapshot","team-sygma-joke","training-kpi-snapshot","utilisation-tracker-refresh",
    "vault-drift-check","weekly-training-audit"}
# crons still genuinely firing despite the freeze
MAC_ACTIVE = {"garmin-daily-pull","drive-changes-watch"}
RETIRED = {"vault-drive-sync","sygma-seo-fortnightly-recheck","lanzarote-competitor-monitor","calendar-colour-coder"}

ENTITY = {"Canary Detect":"canary-detect","Sygma":"sygma","Customers":"customers","Finance":"finance",
    "One System":"one-system","Ops / System":"command-centre","Personal":"personal"}

def host_of(key):
    if key in LAUNCHD: return "mac-launchd"
    if key in CLAUDE_CODE: return "claude-code"
    if key in COWORK: return "cowork-app"
    return "unknown"

def migration_of(key, host, active):
    if key in RETIRED: return "retired"
    if key in MAC_ACTIVE: return "mac-active"
    if host == "mac-launchd": return "mac-frozen"
    if host == "claude-code": return "mac-frozen"
    if host == "cowork-app": return "cowork-pending"     # frozen by quitting the app; may still fire if app open
    return "unknown"

def status_of(migration, active):
    if migration == "retired": return "retired"
    if migration in ("mac-frozen","cowork-pending"): return "frozen"
    if migration == "mac-active": return "ok"
    return "unknown"

TZ = {"mac-launchd":"Atlantic/Canary","claude-code":"Atlantic/Canary","cowork-app":"Atlantic/Canary",
      "railway":"UTC","unknown":None}

# --- ENRICH overlay: the human layer. why = plain-English reason; produces = what it writes; cron = expr; tz override ---
ENRICH = {
  "property-sync": {"why":"So no session ever reports off a stale website-state. Every property card carries a verified live-state block (host/DNS/deploy/Supabase) refreshed nightly.","produces":"Each Properties/*/README LIVE-STATE block · property-state.json feed · properties dashboard · daily-note heartbeat","cron":"5 0 * * *","doc_link":"property-state-and-capability-system-plan","depends_on":[]},
  "garmin-daily-pull": {"why":"Pete's recovery/sleep/HRV data underpins the morning briefing's training framing and the PF journal. Pulled 4×/day so the 07:30 briefing reads fresh numbers.","produces":"Drive: My Drive/Health/garmin/{date}.md · daily-note Garmin line","cron":"0 7,10,17,22 * * *","feeds":["daily-briefing"]},
  "drive-changes-watch": {"why":"Keeps the 150k-file drive_files index current — the index the whole Business OS uses to answer 'where is X' instead of walking the vault tree. The one capture cron kept alive through the migration.","produces":"CC Supabase drive_files (the file index)","cron":"*/15 * * * *","doc_link":"business-os-master-plan-2026-06-20"},
  "data-map-cron": {"why":"Keeps the data-map (where every kind of Pete's data lives) current in the CC so Claude and the Ask page always know the homes. First cron proven on Railway (22 Jun).","produces":"CC Supabase data_map (the 21 data-homes)","cron":"0 5 * * *","doc_link":"railway-infra-2026-06-22"},
  "daily-briefing": {"why":"Pete's single morning operating email — PF lesson, Replies tray, due tasks, calendar, Garmin, GA4 — so the day starts from one place.","produces":"Email to Pete · daily-note ## Daily Briefing block","cron":"30 7 * * *","depends_on":["garmin-daily-pull"]},
  "hub-reconcile": {"why":"Keeps the Sygma Hub Drive tidy and Claude-aware — refreshes folder indexes, sorts the daily change delta against locked conventions, nudges convention offenders.","produces":"hub-content-index.md · Hub folder READMEs · email digest (only on human changes)","cron":"30 17 * * *"},
  "cd-daily-briefing-weekdays": {"why":"The CD field team needs tomorrow's jobs (customer/address/brief from Odoo) the evening before. Pete gets his personal calendar too.","produces":"2 emails (team + Pete) · daily-note block","cron":"15 18 * * 1-5","depends_on":["cd-tom-jobs-calendar-sync-evening"]},
  "cd-daily-briefing-sunday": {"why":"Sunday-evening version covering Monday's CD jobs.","produces":"2 emails (team + Pete) · daily-note block","cron":"15 18 * * 0"},
  "cd-week-ahead-sunday": {"why":"Friday planning-horizon view so the CD team sees the whole upcoming week before the weekend.","produces":"2 emails (team week-ahead + Pete) · daily-note block","cron":"0 18 * * 5"},
  "cd-weekly-finance-email": {"why":"Pete + CD finance see weekly turnover from Odoo without anyone running a report.","produces":"2 markdown reports + HTML email","cron":"0 18 * * 2"},
  "cd-monthly-finance-email": {"why":"Monthly CD turnover (after a week's late-invoice grace) — the fuller report with top customers + payment state.","produces":"Monthly markdown report + HTML email","cron":"0 18 10 * *"},
  "cd-tom-jobs-calendar-sync-noon": {"why":"Mirrors Odoo CRM jobs into Tom's Google Calendar (catches morning bookings) — replaces Odoo's broken built-in Google sync.","produces":"Tom's Google Calendar events","cron":"30 12 * * *"},
  "cd-tom-jobs-calendar-sync-evening": {"why":"Evening half of the Odoo→Tom calendar mirror; this run also writes the daily-note summary.","produces":"Tom's Google Calendar events · daily-note block","cron":"0 18 * * *","feeds":["cd-daily-briefing-weekdays"]},
  "cd-tom-jobs-photo-sort": {"why":"Auto-files Tom's job photos into per-job folders by GPS+EXIF matched to Odoo leads, so site photos are findable without manual sorting.","produces":"Drive Pictures/tom/ sorted folders + _MAP.md · notification email","cron":"0 18 * * *"},
  "payroll-backup": {"why":"Nightly safety copy of payroll data — the one thing you never want to lose.","produces":"Payroll backup files","cron":"0 2 * * *"},
  "remittance-to-xero": {"why":"Pushes remittance data into Xero on workdays so finance reconciliation stays current.","produces":"Xero entries","cron":"1 9 * * 1-5"},
  "training-soldo-cc-publish": {"why":"Publishes training Soldo spend to the CC so the cost picture stays live.","produces":"CC repo data → a CC page","cron":"15 6 * * *"},
  "utilisation-cc-publish": {"why":"Surfaces trainer utilisation in the CC (days booked vs available per trainer) — the CC monitors what the Portal owns.","produces":"CC repo data/utilisation.json → /m/sygma-training/utilisation","cron":"20 17 * * *","depends_on":["utilisation-tracker-refresh"]},
  "utilisation-tracker-refresh": {"why":"Recomputes trainer utilisation from the 5 trainer calendars and rewrites the Hub utilisation sheet — the source the CC then publishes.","produces":"Drive: utilisation report.xlsx · Management chat post","cron":"0 17 * * *","feeds":["utilisation-cc-publish"]},
  "ads-cc-publish": {"why":"Builds the native Sygma Ads dashboard in the CC from the live Google Ads account + GAQL time-series.","produces":"CC repo data/ads.json → /m/sygma-ads","cron":"45 6 * * *","depends_on":["sygma-ads-account-snapshot"]},
  "sygma-ads-account-snapshot": {"why":"Keeps the vault Google Ads doc auto-current daily so no session reports off stale ad state (a stale snapshot once caused a false wasted-spend flag).","produces":"Properties/Sygma…/data/google-ads-account.{md,json} · daily-note (only on change)","cron":"30 6 * * *","feeds":["ads-cc-publish"]},
  "sygma-ads-fortnightly-report": {"why":"Fortnightly emailed Ads digest — spend/CPA deltas, new wasted-spend candidates, Quality-Score regressions.","produces":"Email digest · ads fortnightly-history trend file","cron":"0 8 1,15 * *"},
  "sygma-daily-google-report": {"why":"Daily Google (Ads/GA4/GSC) pulse for Sygma so performance shifts surface fast.","produces":"Report / email","cron":"0 7 * * *"},
  "backlinks-weekly-report": {"why":"Weekly backlink movement for Sygma (Appear Online owns the off-site work; this is the visibility on it).","produces":"CC page / report","cron":"45 7 * * 1"},
  "staff-master-sync": {"why":"Keeps the Sygma Staff Master in sync — the operational source of truth for who works there.","produces":"Staff Master sheet / Hub","cron":"31 5 * * *"},
  "training-kpi-snapshot": {"why":"Weekly training throughput KPI (courses delivered, run-rate, top customers) from the bookings master sheet.","produces":"Businesses/…/training/kpis.md · daily-note block","cron":"6 7 * * 1"},
  "weekly-training-audit": {"why":"Cross-checks the bookings master against 11 trainer calendars + booking forms to catch missing/mismatched training records.","produces":"Audit report (vault + Drive) · Diary Management chat post","cron":"8 7 * * 1"},
  "jotform-training-eval-sync": {"why":"Pulls training course evaluations from JotForm into the system.","produces":"Eval data","cron":"34 6 * * 1"},
  "monthly-nights-away-reminder": {"why":"Reminds the 6 card-holding trainers to log nights worked away (last Friday of the month).","produces":"Emails to trainers · daily-note line","cron":"0 8 * * 5"},
  "team-sygma-joke": {"why":"Team morale — posts a quality joke to the Team Sygma chat thrice on weekdays.","produces":"Google Chat post (Team Sygma)","cron":"0 8,12,16 * * 1-5"},
  "oconnors-seo-report": {"why":"Weekly SEO position report for the O'Connors property.","produces":"CC page / report","cron":"0 8 * * 1"},
  "lanza-lates-daily-owner-report": {"why":"Daily owner report for the Lanzarote Lates (One System) property.","produces":"Owner report / email","cron":"0 6 * * *"},
  "lanza-lates-sc-marker-monitor": {"why":"Monitors service-charge markers for Lanzarote Lates.","produces":"Monitor output / alert","cron":"15 7 * * *"},
  "account-clancy-sync": {"why":"Keeps the Clancy account store in the CC reconciled with Asana (deliverables + actions) — the account-customer merge rule.","produces":"CC account_clancy store · daily-note line","cron":"20 7 * * *"},
  "account-email-ingest": {"why":"Sweeps Clancy mail into the account store (contacts, docs, reply-owed).","produces":"CC account store · daily-note line","cron":"10 7 * * *"},
  "account-weekly-review": {"why":"Weekly Clancy account review digest.","produces":"Review digest / email","cron":"0 17 * * 5"},
  "cc-map": {"why":"Regenerates the CC module map (every module by area · who-can-see-what · access history) so the CC's own structure stays documented.","produces":"Properties/Pete Command Centre/cc-map.md","cron":"30 8 * * *"},
  "gcal-twice-daily-sync": {"why":"Twice-daily Google Calendar sync — Xhale training events into the diary + colour-coding.","produces":"Google Calendar events + colours · daily-note block","cron":"0 7,18 * * *"},
  "pf-journal-reminder": {"why":"6pm nudge for Pete's Passion-Fit journal practice, carrying yesterday's lesson.","produces":"Reminder (email/notification)","cron":"10 18 * * *"},
  "vault-drift-check": {"why":"Monthly check that the vault helper-first discipline hasn't drifted (orphan helpers, connector-supersession, filesystem-shape Drive reads).","produces":"Drift report · daily-note","cron":"0 7 1 * *"},
  "vault-drive-sync": {"why":"RETIRED for the migration — vault↔Drive hourly sync; stays off so it doesn't fight the Drive cutover.","produces":"(was: vault↔Drive file sync)","cron":"0 * * * *"},
  "consolidate-lessons-monthly": {"why":"Monthly consolidation of Library/lessons into the index.","produces":"Lessons index","cron":"30 8 1 * *"},
  "consolidate-memory-monthly": {"why":"Monthly consolidation/trim of the auto-memory index.","produces":"MEMORY.md / memory files","cron":"0 8 1 * *"},
  "helper-script-registry-refresh": {"why":"Weekly regen of the auto-generated helper-script registry in external-service-routing (so helper-first discipline has a current map).","produces":"external-service-routing helper registry","cron":"2 7 * * 1"},
  "sygma-seo-fortnightly-recheck": {"why":"DECOMMISSIONED 8 Jun — was the main Surfer-quota consumer; fortnightly SEO position re-check.","produces":"(was: SEO delta email + trend file)","cron":"0 8 6,20 * *"},
}

def main():
    if os.path.exists(OUT) and "--force" not in sys.argv:
        print(f"{OUT} already exists — refusing to overwrite (pass --force). This is a one-off seed."); return
    rows = json.load(open(DUMP))
    crons = []
    seen = set()
    for r in rows:
        key = slug(r["name"]); seen.add(key)
        host = host_of(key)
        mig = migration_of(key, host, r.get("active"))
        e = ENRICH.get(key, {})
        crons.append({
            "key": key, "title": r["name"], "host": host,
            "entity_slug": ENTITY.get(r.get("entity_slug"), slug(r.get("entity_slug") or "cross")),
            "what": r.get("description") or "", "why": e.get("why",""),
            "impact_if_down": e.get("impact_if_down",""),
            "schedule": e.get("cron"), "schedule_human": r.get("trigger") or "",
            "timezone": e.get("timezone", TZ.get(host)),
            "command": e.get("command",""), "produces": e.get("produces",""),
            "consumes": e.get("consumes",""), "depends_on": e.get("depends_on",[]),
            "feeds": e.get("feeds",[]), "migration_status": mig,
            "migration_target": "railway" if mig in ("mac-frozen","cowork-pending") else "",
            "enabled": bool(r.get("active")) and mig != "retired",
            "status": status_of(mig, r.get("active")),
            "doc_link": e.get("doc_link","scheduled-tasks"),
            "tags": [host, r.get("entity_slug") or "cross"],
        })
    # the two crons not in processes
    for key, host, ent in [("data-map-cron","railway","command-centre"),("drive-changes-watch","mac-launchd","command-centre")]:
        if key in seen: continue
        e = ENRICH.get(key, {})
        mig = "railway-live" if host=="railway" else "mac-active"
        crons.append({
            "key": key, "title": key, "host": host, "entity_slug": ent,
            "what": e.get("what",""), "why": e.get("why",""), "impact_if_down": e.get("impact_if_down",""),
            "schedule": e.get("cron"), "schedule_human": e.get("schedule_human",""),
            "timezone": e.get("timezone", TZ.get(host)), "command": e.get("command",""),
            "produces": e.get("produces",""), "consumes": e.get("consumes",""),
            "depends_on": e.get("depends_on",[]), "feeds": e.get("feeds",[]),
            "migration_status": mig, "migration_target": "",
            "enabled": True, "status": "ok" if host=="railway" else "ok",
            "doc_link": e.get("doc_link","scheduled-tasks"), "tags": [host, ent],
        })
    crons.sort(key=lambda c:(c["entity_slug"], c["key"]))
    json.dump({"_meta":{"note":"Canonical cron registry. Edit this, then run cc-cron-sync.py to push to CC Supabase public.crons.","count":len(crons)},"crons":crons}, open(OUT,"w"), indent=2, ensure_ascii=False)
    print(f"wrote {OUT} — {len(crons)} crons")
    by_host={}
    for c in crons: by_host[c["host"]]=by_host.get(c["host"],0)+1
    print("by host:", json.dumps(by_host))

if __name__=="__main__": main()
