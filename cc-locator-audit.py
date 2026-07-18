#!/usr/bin/env python3
# CRON-META
# what: Report-only CC Locator drift check — unhomed populated tables + dead/stale data_map homes
# why: Keeps the locator (data_map / whereis) self-maintaining — flags a new unhomed kind or a rotted home automatically, so Pete never has to remind
# reads: information_schema + public.data_map (+ a count per public table)
# writes: its own report line to daily_log (cron_name='cc-locator-audit') so the briefing/closeout can read it; NO domain data, ever
# entity: PA-Command-Centre
# report: stdout (and the CC locator-audit surface)
# secrets: SUPABASE_TOKEN
# schedule: 30 6 * * *
# timezone: Atlantic/Canary
# CRON-META-END
"""cc-locator-audit.py — the CC Locator self-maintaining drift check (Pillar B / B2).

REPORT-ONLY (the house pattern, like connection-parity.py): it prints a report, records that
report to daily_log so the briefing/closeout can read it, and ALWAYS exits 0 when it ran.
It never creates tasks and never mutates domain data. Finding drift is this tool WORKING.

Answers Requirement #2 ("keeps itself updated, no reminders"): every day it reconciles the LIVE
system against data_map (the locator's SSOT) and flags drift, so a new kind or a rotted home is
caught automatically instead of waiting for someone to notice.

Checks:
  (a) COMPLETENESS — every populated public base table + view is either HOMED (its name appears
      in a data_map row's home/notes/backing_ref) or on the explicit INFRA allow-list. A new,
      unhomed, populated table is drift.
  (d) DEAD / STALE HOME — every data_map backing_ref of form `table:public.X` points at a table
      that EXISTS and is NON-EMPTY (existence alone misses the Daily-notes class = real-but-empty).

Usage:  VAULT=/tmp/pbs python3 /tmp/pbs/cc-locator-audit.py [--json]
        exit 0  = the report RAN (whatever it found).  exit 99 = it could not check, so it
        refused to report at all. Read the gap count from --json ("gaps" — an INT), never from $?. An abort emits valid JSON too,
        with "aborted": true and gaps=1, because being unable to check IS a gap.
"""
import os, sys, json, subprocess, re, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# What this check does NOT yet cover. Emitted as info[] so a "0 gaps" result can never be
# mistaken for total coverage — the report states its own boundary.
SCOPE_NOTE = ("covers CC public-schema tables/views, the app schemas in the same database, skills, "
              "helpers, projects, storage buckets, properties (websites/apps), entities and "
              "connectors, and NEW top-level Drive folders. NOT yet covered: CC pages, Railway crons, and the "
              "other databases (Sygma hub / CD-Leak / Odoo)")

def q(sql):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       env={**os.environ, "VAULT": VAULT}, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"[cc-locator-audit] query failed: {r.stderr[:160]}\n")
        return None            # None = errored (distinct from [] empty) so we never mis-report
    try:
        return json.loads(r.stdout)
    except Exception:
        return []

# Engine-internal tables that answer no "where does X live" question — intentionally NOT homed.
# (Membership tables of homed subsystems are covered by the subsystem's data_map text, not here.)
INFRA_ALLOW = {
    # pure engine-internal
    "access_audit", "agent_cron_prompts", "agent_jobs", "app_settings", "cc_map", "cron_events",
    "cron_state", "drive_change_tokens", "gtask_tombstones", "memory_chunks", "module_user_grants",
    "note_links", "profiles", "raw_captures", "tags", "tasks_premig_20260701", "user_groups",
    "groups", "triage_sync_actions",
    # the locator's own registries (covered by dedicated resolver blocks, not the data_map text)
    "data_map", "property_declarations", "property_state", "staff_directory",
    # members of homed subsystems (the anchor row homes the subsystem; these are covered by it)
    "account_config", "account_deliverables", "account_documents", "account_kpi", "account_meetings",
    "account_obligations", "account_risks", "account_state",                 # KAM (anchor: account_people)
    "ee_public_courses",                                                       # EE (anchor: enquiry_touches)
    "garmin_weekly_recovery",                                                  # Garmin view (anchor: garmin_daily)
    "triage_cases", "triage_templates",                                        # triage engine internals
    "damage_review_rules",                                                     # clancy (anchor: clancy_damages)
    "bank_account_history",                                                    # banking (anchor: bank_accounts)
    "training_rep", "training_session_code_map", "training_weekly_totals", "training_weekly_volume",  # training (anchor: training_session)
    "health_config", "health_planned_session", "health_weekly",               # PF (anchors: health_journal/feedback)
    "module_content",                                                          # pages (anchor: modules)
}

# The 5 project-routing labels resolve to these entity slugs. Verified live 18 Jul 2026:
# projects.entity_slug holds LABELS ("Canary Detect"), entities.slug holds SLUGS ("camello-blanco").
# Without this map a naive slug-equality check rejects every legitimate project.
LABEL_TO_SLUG = {"Personal": "personal", "One System": "one-system", "El Atico": "el-atico",
                 "Canary Detect": "camello-blanco", "Sygma": "sygma-solutions"}


def _homed(name, dm_text):
    """Word-boundary match. A raw substring test lets a short generic name (a table or bucket
    called 'events', 'state', 'rules') match unrelated prose in the map and read as FILED when
    it is not. Used by BOTH the table check and the bucket check."""
    return re.search(r"(?<![a-z0-9_])" + re.escape(name.lower()) + r"(?![a-z0-9_])", dm_text) is not None


def _summarise(items, n=3):
    return ", ".join(items[:n]) + (f" (+{len(items) - n} more)" if len(items) > n else "")


def _disk(kind):
    """Files/dirs present in the repo. Returns None on failure — never an empty set, which
    would read as 'nothing on disk' and wrongly flag every registry row as stale."""
    try:
        if kind == "skills":
            d = os.path.join(VAULT, "skills")
            return {n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n, "SKILL.md"))}
        # helpers.name keeps the .py extension (verified: 'account_store.py'); skills.name does not.
        return {n for n in os.listdir(VAULT) if n.endswith(".py")}
    except Exception:
        return None


def check_rows(gaps, dm_text):
    """ROW-granular checks. What Pete adds day to day is a ROW in an existing table, not a new
    table — so table-level completeness alone never sees it. READ-ONLY: this reconciles and
    reports, it never registers, writes or deletes anything."""
    def add(rule, subject, detail, severity="medium"):
        gaps.append({"rule": rule, "subject": subject, "detail": detail, "severity": severity})

    # --- skills / helpers: a file on disk with no registry row is INVISIBLE to whereis + the map
    for kind, table, label in (("skills", "skills", "skill"), ("helpers", "helpers", "helper")):
        rows = q(f"SELECT name FROM {table}")
        disk = _disk(kind)
        if rows is None:
            add("couldnt-check", table, f"{table} registry query ERRORED — could not reconcile against disk", "high"); continue
        if disk is None:
            add("couldnt-check", table, f"could not list {label} files on disk — could not reconcile", "high"); continue
        missing = sorted(disk - {r["name"] for r in rows})
        stale = sorted({r["name"] for r in rows} - disk)
        if missing:
            add(f"unregistered-{label}", _summarise(missing), f"{len(missing)} {label}(s) exist on disk with NO {table} row — invisible to the map and to whereis")
        if stale:
            add(f"stale-{label}-row", _summarise(stale), f"{len(stale)} {table} row(s) with no file on disk — the registry points at something gone", "low")

    # --- projects: an active project with no Drive folder has nowhere to file its documents
    projs = q("SELECT slug, entity_slug, drive_folder_id FROM projects WHERE coalesce(status,'') <> 'archived'")
    if projs is None:
        add("couldnt-check", "projects", "projects query ERRORED — could not check project homes", "high")
    else:
        homeless = sorted(p["slug"] for p in projs if not p.get("drive_folder_id"))
        if homeless:
            add("project-no-home", _summarise(homeless), f"{len(homeless)} active project(s) with no Drive folder — nowhere to file their documents")
        ents = q("SELECT slug FROM entities")
        if ents is None:
            add("couldnt-check", "entities", "entities query ERRORED — could not validate project owners", "high")
        else:
            known = {e["slug"] for e in ents} | set(LABEL_TO_SLUG)
            unknown = sorted({(p.get("entity_slug") or "(none)") for p in projs} - known)
            if unknown:
                add("project-unknown-owner", _summarise(unknown), f"{len(unknown)} project owner value(s) match no entity and no known routing label — those projects would be filed to the wrong drive")

    # --- NEW DRIVE AREAS (R1): validating known homes never spots a folder nobody wrote down.
    # drive_files.indexed_at is set when a row is FIRST seen and is not touched by later updates
    # (verified 18 Jul), so a recent indexed_at on a TOP-LEVEL folder means a genuinely new area.
    newdirs = q("SELECT drive, name FROM drive_files WHERE is_folder AND path NOT LIKE '%/%' "
                "AND indexed_at > now() - interval '7 days' ORDER BY indexed_at DESC LIMIT 20")
    if newdirs is None:
        add("couldnt-check", "drive top-levels", "new-Drive-folder query ERRORED — could not check for new areas", "high")
    elif newdirs:
        add("new-drive-area", _summarise([f"{d['name']} ({d['drive']})" for d in newdirs]),
            f"{len(newdirs)} new top-level Drive folder(s) appeared in the last 7 days — decide if each needs a home, or ignore it", "low")

    # --- CONNECTORS (R4): a connection whose named secret does not exist cannot authenticate.
    # "Reconcile against the registry" was circular — connectors IS a registry. The answerable
    # question is whether each connector's secret actually exists in the one safe.
    bad = q("SELECT name, secret FROM connectors WHERE coalesce(secret,'') <> '' "
            "AND secret NOT IN (SELECT name FROM secrets) ORDER BY name")
    if bad is None:
        add("couldnt-check", "connectors", "connector/secret query ERRORED — could not verify connections", "high")
    elif bad:
        add("connector-missing-secret", _summarise([b["name"] for b in bad]),
            f"{len(bad)} connector(s) name a secret that does not exist in public.secrets — they cannot authenticate")

    # --- NON-PUBLIC SCHEMAS: Pete's own app schemas live in the SAME database and were invisible.
    # auth/storage/realtime/etc are Supabase's own plumbing, not his data — excluded deliberately.
    SUPABASE_OWN = "('auth','storage','realtime','extensions','graphql','graphql_public','vault','net','cron','pgbouncer','supabase_migrations','supabase_functions','pgsodium','pgsodium_masks')"
    nps = q("SELECT n.nspname AS schema, c.relname AS name FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','v','m') "
            f"AND n.nspname NOT IN ('public','pg_catalog','information_schema','pg_toast') "
            f"AND n.nspname NOT IN {SUPABASE_OWN} ORDER BY n.nspname, c.relname")
    if nps is None:
        add("couldnt-check", "non-public schemas", "schema scan ERRORED — could not check app schemas", "high")
    elif nps:
        unhomed = sorted({f"{r['schema']}.{r['name']}" for r in nps
                          if not _homed(r["schema"], dm_text) and not _homed(r["name"], dm_text)})
        if unhomed:
            schemas = sorted({u.split(".")[0] for u in unhomed})
            add("unhomed-app-schema", _summarise(schemas),
                f"{len(unhomed)} table(s) across {len(schemas)} app schema(s) in this database have no data_map home ({', '.join(schemas)})", "low")

    # --- PROPERTIES (websites/apps): the locator's original purpose. A declaration that cannot
    # answer "where does its code live and who serves it" is how the wrong-repo clone happened.
    props = q("SELECT name, coalesce(f->>'github','') AS github, coalesce(f->>'hosting','') AS hosting "
              "FROM property_declarations WHERE coalesce(f->>'status','')='active'")
    if props is None:
        add("couldnt-check", "property_declarations", "property query ERRORED — could not check site/app declarations", "high")
    else:
        # wordpress/lovable-style hosting legitimately has no git repo — do not cry wolf there.
        NO_REPO_HOSTING = {"wordpress", "lovable", "squarespace", "wix"}
        no_repo = sorted(p["name"] for p in props
                         if not p["github"] and p["hosting"].lower() not in NO_REPO_HOSTING)
        no_host = sorted(p["name"] for p in props if not p["hosting"])
        if no_repo:
            add("property-no-repo", _summarise(no_repo), f"{len(no_repo)} active propert(ies) with no repo declared — nobody can tell where the code lives")
        if no_host:
            add("property-no-hosting", _summarise(no_host), f"{len(no_host)} active propert(ies) with no hosting declared — nobody can tell who serves it")

    # --- ENTITIES: a live company with no Drive home has nowhere to file its documents.
    ents = q("SELECT slug, coalesce(drive_home,'') AS home FROM entities WHERE coalesce(status,'')='active'")
    if ents is None:
        add("couldnt-check", "entities", "entities query ERRORED — could not check company homes", "high")
    else:
        homeless = sorted(e["slug"] for e in ents if not e["home"])
        if homeless:
            add("entity-no-home", _summarise(homeless), f"{len(homeless)} active entit(ies) with no Drive home — nowhere to file their documents")

    # --- Drive path integrity: renaming/moving a folder silently strands every descendant's
    # stored path (drive_files.path is denormalised and never recomputed). Delegated to
    # drive-path-rebuild.py so there is ONE definition of the check.
    try:
        r = subprocess.run(["python3", os.path.join(VAULT, "drive-path-rebuild.py"), "--json"],
                           env={**os.environ, "VAULT": VAULT}, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            add("couldnt-check", "drive-paths", f"drive-path-rebuild exited {r.returncode} — Drive path integrity NOT verified", "high")
        else:
            n = json.loads(r.stdout).get("gaps", 0)
            if n:
                add("drive-path-drift", f"{n} file path(s)", "stored Drive paths disagree with the live folder tree — a rename or move stranded them. Fix: drive-path-rebuild.py --apply", "high")
    except Exception as e:
        add("couldnt-check", "drive-paths", f"drive-path-rebuild did not run ({e}) — Drive path integrity NOT verified", "high")

    # --- storage buckets: data_map homes buckets, so a NEW bucket must be homed too
    bks = q("SELECT name FROM storage.buckets")
    if bks is None:
        add("couldnt-check", "storage.buckets", "bucket list query ERRORED — could not check bucket homes", "high")
    else:
        unhomed = sorted(b["name"] for b in bks if not _homed(b["name"], dm_text))
        if unhomed:
            add("unhomed-bucket", _summarise(unhomed), f"{len(unhomed)} storage bucket(s) with no data_map home")


def main():
    as_json = "--json" in sys.argv
    gaps = []

    dm = q("SELECT domain, home, access, notes, backing_ref FROM data_map ORDER BY sort")
    tbls = q("SELECT c.relname AS name, c.relkind AS kind FROM pg_class c "
             "JOIN pg_namespace n ON n.oid=c.relnamespace "
             "WHERE n.nspname='public' AND c.relkind IN ('r','v','m') ORDER BY c.relname")
    if dm is None or tbls is None or not tbls:
        why = ("a lookup ERRORED" if (dm is None or tbls is None) else
               "the table list came back EMPTY — impossible for this database, so the lookup lied")
        msg = f"cc-locator-audit: {why} — aborting (not reporting false drift). Re-run."
        if as_json:
            # An abort must still be VALID JSON, or a consumer breaks at exactly the moment the
            # check failed. And it must NOT read as 'gaps: 0' — being unable to check IS a gap.
            print(json.dumps({"gaps": 1, "gap_types": ["aborted"],
                              "findings": [{"rule": "aborted", "subject": "cc-locator-audit",
                                            "detail": msg, "severity": "high"}],
                              "info": [], "aborted": True}, indent=1))
        else:
            print(msg)
        sys.exit(99)

    # (a) COMPLETENESS
    dm_text = " ".join((r.get("home") or "") + " " + (r.get("notes") or "") + " " + (r.get("backing_ref") or "")
                       for r in dm).lower()
    for t in tbls:
        name = t["name"]
        if name in INFRA_ALLOW:
            continue
        if _homed(name, dm_text):            # word-boundary match, not a raw substring
            continue
        # populated? (skip genuinely empty internal tables)
        cnt = q(f'SELECT count(*) AS n FROM public."{name}"')
        if cnt is None:                      # errored ≠ empty: never silently treat as "nothing here"
            gaps.append({"rule": "couldnt-check", "subject": name, "detail": f"row-count query ERRORED — cannot say whether it is homed; NOT counted as clean", "severity": "high"})
            continue
        n = (cnt[0]["n"] if cnt else 0)
        if n and n > 0:
            gaps.append({"rule": "unhomed-table", "subject": name, "detail": f"{n} rows, populated but has NO data_map home and is not on the infra allow-list", "severity": "medium"})

    # (d) DEAD / STALE HOME — backing_ref table:public.X must exist AND be non-empty
    known = {t["name"] for t in tbls}
    for r in dm:
        ref = (r.get("backing_ref") or "")
        m = re.match(r"table:public\.([a-z_0-9]+)$", ref)
        if not m:
            continue
        tn = m.group(1)
        # existence comes from the catalogue we already fetched — NOT from "the count query errored",
        # which conflates a retired table with a transient failure.
        if tn not in known:
            gaps.append({"rule": "dead-home", "subject": r["domain"], "detail": f"backing_ref {ref}, but that table does not exist (retired?)", "severity": "high"})
            continue
        cnt = q(f'SELECT count(*) AS n FROM public."{tn}"')
        if cnt is None:                      # errored ≠ dead: say so, never guess
            gaps.append({"rule": "couldnt-check", "subject": r["domain"], "detail": f"{ref}: row-count query ERRORED — status unknown, NOT reported clean", "severity": "high"})
        elif not cnt or cnt[0]["n"] == 0:
            gaps.append({"rule": "empty-home", "subject": r["domain"], "detail": f"backing_ref {ref}, but that table is EMPTY (home points at a table with no data)", "severity": "high"})

    # (r) ROW-GRANULAR — the everyday adds (a skill, a helper, a project, a bucket)
    check_rows(gaps, dm_text)

    ordered = sorted(gaps, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x["severity"], 3))
    info = [{"subject": "coverage", "detail": SCOPE_NOTE}]

    if as_json:
        # THE HOUSE CONSUMER CONTRACT — drift-check.py reads exactly this shape from
        # connection-parity.py: gaps=INT (not a list), gap_types[], findings[{rule,subject}], info[].
        # Emitting a list as "gaps" made the weekly digest print a garbled count.
        print(json.dumps({
            "gaps": len(gaps),
            "gap_types": sorted({g["rule"] for g in gaps}),
            "findings": ordered,
            "info": info,
        }, indent=1))
        return                      # consumers only read; they must not trigger the daily_log record

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # Railway sets CRON_SCRIPT on the service; a hand-run does not. Without this stamp a person
    # running the tool leaves a fresh-looking row that hides a silently-dead 06:30 cron.
    source = "scheduled" if os.environ.get("CRON_SCRIPT") else "manual"
    body = f"CC LOCATOR {stamp} [{source}] — " + (f"⚠ {len(gaps)} gap(s)" if gaps else "✓ all homed")
    for g in ordered:
        body += f"\n  [{g['severity']:6}] {g['rule']}: {g['subject']} — {g['detail']}"
    if not gaps:
        body += "\n  clean — every populated table is homed, and no data_map home points at a dead/empty table."
    body += f"\n  (scope: {SCOPE_NOTE})"
    print(body)

    # Record the result so the morning briefing / closeout can READ it in milliseconds instead of
    # re-running this check (~50s on the cron). Writing its own report line is exactly what
    # drift-check.py does — it still mutates no domain data.
    today = datetime.date.today().isoformat()
    safe = body.replace("$$", "")
    if q(f"INSERT INTO daily_log (date, cron_name, content) VALUES ('{today}','cc-locator-audit',$$%s$$)" % safe) is None:
        sys.stderr.write("[cc-locator-audit] WARNING: ran fine but could NOT record the result to daily_log\n")

    # A successful REPORT always exits 0, however many gaps it found. Finding drift is the tool
    # working, not the tool failing — exiting non-zero made Railway stamp the cron FAILED and
    # emailed Pete a "crash" for a healthy run. Consumers read the count from --json, never $?.
    # Non-zero is reserved for a genuine abort (99 above), so the two can't be confused.
    sys.exit(0)

if __name__ == "__main__":
    main()
