#!/usr/bin/env python3
# CRON-META
# what: Report-only CC Locator drift check — unhomed tables/app-schemas, dead or empty data_map homes, unregistered skills/helpers, projects + entities with no home, incomplete property declarations, connectors whose secret is missing, unhomed storage buckets, new top-level Drive folders, and Drive path integrity
# why: Keeps the locator (data_map / whereis) self-maintaining — flags a new unhomed kind or a rotted home automatically, so Pete never has to remind
# reads: pg_class/information_schema, public.data_map, skills, helpers, projects, entities, property_declarations, connectors, secrets, storage.buckets, drive_files (+ a row count per table)
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
import os, sys, json, subprocess, re, datetime, time

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# What this check does NOT yet cover. Emitted as info[] so a "0 gaps" result can never be
# mistaken for total coverage — the report states its own boundary.
SCOPE_NOTE = ("covers CC public-schema tables/views, the app schemas in the same database, skills, "
              "helpers, projects, storage buckets, properties (websites/apps), entities and "
              "connectors, and NEW top-level folders WITHIN THE ALREADY-INDEXED drives (a brand-new shared drive is not seen until it is indexed). Properties are checked for DECLARATION COMPLETENESS only — a site never declared is NOT detected (closeout creates the declaration). "
              "NOT yet covered: CC pages, Railway crons, and the "
              "other databases (Sygma hub / CD-Leak / Odoo)")

def q(sql, _retry=True):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       env={**os.environ, "VAULT": VAULT}, capture_output=True, text=True)
    if r.returncode != 0 and _retry:
        # One retry before crying wolf. This fires dozens of queries per run, so without it a
        # single transient blip produced a HIGH "could not check" alarm and a gap count that
        # wobbled run to run. Same pattern whereis.py already uses.
        import time as _t; _t.sleep(1.5)
        return q(sql, _retry=False)
    if r.returncode != 0:
        # cc-sql.py prints its error to STDOUT ("ERROR 400 ..."), so stderr alone is usually
        # EMPTY — a dead login, a dropped table and a rate-limit blip all looked identical.
        _why = (r.stderr or "").strip() or (r.stdout or "").strip()
        sys.stderr.write(f"[cc-locator-audit] query failed: {_why[:220]}\n")
        return None            # None = errored (distinct from [] empty) so we never mis-report
    try:
        return json.loads(r.stdout)
    except Exception:
        # NOT []: an unreadable reply is "could not check", never "nothing found". Returning []
        # here turned a garbled response into a false all-clear across every check.
        sys.stderr.write(f"[cc-locator-audit] unreadable reply: {(r.stdout or '')[:160]}\n")
        return None

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


# The 22 tables that today are only mentioned informally in the map prose. Grandfathered so
# tightening to a qualified match does not false-alarm on them. A NEW table gets no such pass.
GRANDFATHERED = {
    "bank_account_history", "bank_statement_lines", "clancy_reports", "cron_events",
    "damage_review_rules", "ee_catalogue", "ee_customer_rates", "ee_edits",
    "ee_phrases", "ee_rates", "ee_rules", "health_config",
    "health_feedback", "health_planned_session", "health_weekly", "module_content",
    "training_rep", "training_session_code_map", "training_weekly_totals", "training_weekly_volume",
    "triage_decisions", "triage_digests",
}


def _homed_table(name, dm_text):
    """A table counts as homed only if the map names it QUALIFIED (public.x / table:public.x).
    Bare-word matching meant a new table called 'reports' or 'customers' matched unrelated prose
    and reported as filed. Measured 18 Jul: 53 matched bare, only 31 qualified."""
    n = name.lower()
    # word-boundary, NOT a plain substring: "public.task" is inside "public.tasks", so a new
    # table named as the stem of an existing one read as filed.
    return _homed(f"public.{n}", dm_text) or n in GRANDFATHERED


def _homed_bucket(name, dm_rows):
    """Buckets have no qualified form, so require the name to appear in map text that is actually
    ABOUT buckets — otherwise a new bucket with a common name matches any stray prose."""
    n = name.lower()
    # Third attempt, and the first two were both no-ops. Pipe-splitting could never work (the
    # text is space-joined). '"bucket" in dm_text' was global — the word appears SOMEWHERE in the
    # map, so it was true for every bucket, always. Match PER ROW: the name must appear in a row
    # that itself mentions buckets.
    for r in dm_rows:
        seg = ((r.get("home") or "") + " " + (r.get("notes") or "") + " " + (r.get("backing_ref") or "")).lower()
        if "bucket" in seg and _homed(name, seg):
            return True
    return False


def _homed(name, dm_text):
    """Word-boundary match. A raw substring test lets a short generic name (a table or bucket
    called 'events', 'state', 'rules') match unrelated prose in the map and read as FILED when
    it is not. Used by BOTH the table check and the bucket check."""
    # Boundary excludes letters/digits/underscore/HYPHEN but NOT the dot: homes are written
    # qualified ("public.daily_log"), so a preceding dot must still match, while a hyphen must
    # NOT (bucket names are always hyphenated: "cc-modules" must not match "cc-modules-archive").
    return re.search(r"(?<![a-z0-9_\-])" + re.escape(name.lower()) + r"(?![a-z0-9_\-])", dm_text) is not None


def _summarise(items, n=12):
    """Name them ALL (up to a sane cap). Truncating at 3 meant a 4th unfiled site or connector
    only moved a counter — Pete could see the number rise but never which one it was."""
    return ", ".join(items[:n]) + (f" (+{len(items) - n} more)" if len(items) > n else "")


def _disk(kind):
    """Files/dirs present in the repo. Returns None on failure — never an empty set, which
    would read as 'nothing on disk' and wrongly flag every registry row as stale."""
    try:
        if kind == "skills":
            d = os.path.join(VAULT, "skills")
            return {n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n, "SKILL.md"))}
        # helpers.name keeps the extension (verified: 'account_store.py'); skills.name does not.
        # Walk SUB-FOLDERS and include shell scripts: a top-level .py-only scan missed
        # account/account-log.py and apple-pass-type-id-csr-gen.sh, both genuinely unregistered.
        # Compared on BASENAME, which is how helpers.name is stored.
        SKIP = {"skills", ".git", "Library", "__pycache__", "node_modules", ".github"}
        found = set()
        for root, dirs, files in os.walk(VAULT):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
            if root.count(os.sep) - VAULT.count(os.sep) > 1:   # one level deep is enough
                dirs[:] = []
            for n in files:
                if n.endswith((".py", ".sh")):
                    found.add(n)
        return found
    except Exception:
        return None


def check_rows(gaps, dm_text, dm_rows):
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
    # LIMITATION, stated in the finding itself so it cannot mislead: this uses a 7-day window on
    # indexed_at. Measured 18 Jul — a 30-day window returns ALL 175 top-level folders, because the
    # index itself was bulk-built recently, so indexed_at only separates "new" from that build.
    # Reconciling all 175 against data_map instead would flood (most are not individually homed).
    # The correct fix needs a per-folder decision record (homed / deliberately ignored) and is a
    # declared open item — NOT half-built here. No LIMIT, so the count is honest.
    newdirs = q("SELECT drive, name FROM drive_files WHERE is_folder AND path NOT LIKE '%/%' "
                "AND indexed_at > now() - interval '7 days' ORDER BY indexed_at DESC")
    if newdirs is None:
        add("couldnt-check", "drive top-levels", "new-Drive-folder query ERRORED — could not check for new areas", "high")
    elif newdirs:
        add("new-drive-area", _summarise([f"{d['name']} ({d['drive']})" for d in newdirs]),
            f"{len(newdirs)} new top-level Drive folder(s) appeared in the last 7 days — decide if each needs a home, or ignore it. NOTE: this is a 7-DAY WINDOW, so an unactioned folder stops being reported after a week; it does not mean it got filed", "low")

    # --- CONNECTORS (R4): a connection whose named secret does not exist cannot authenticate.
    # "Reconcile against the registry" was circular — connectors IS a registry. The answerable
    # question is whether each connector's secret actually exists in the one safe.
    # connectors.secret is a LIST, not one name: comma-separated, sometimes slash-separated
    # alternatives, sometimes with a parenthetical note, and an em-dash for none. Comparing the
    # whole string flagged all 9 multi-secret connectors as broken when every secret existed.
    conns = q("SELECT name, coalesce(secret,'') AS secret FROM connectors ORDER BY name")
    known = q("SELECT name FROM secrets")
    if conns is None or known is None:
        add("couldnt-check", "connectors", "connector/secret query ERRORED — could not verify connections", "high")
    else:
        have = {k["name"] for k in known}
        missing = []
        for c in conns:
            raw = re.sub(r"\([^)]*\)", "", c["secret"])          # drop notes like "(CC secrets)"
            names = [n.strip() for n in re.split(r"[,/]", raw) if n.strip()]
            names = [n for n in names if n not in {"-", "—", "n/a", "N/A", "none"}]
            # An explicit none-marker is the NO-CREDENTIAL case, handled below with its own
            # exemptions — not a missing secret. Only a field that is purely a NOTE
            # ("(stored in Railway env)") documented a location while naming no secret.
            if c["secret"].strip() in {"-", "—", "n/a", "N/A", "none", ""}:
                continue
            if c["secret"].strip() and not names:
                missing.append(f"{c['name']} (secret field is only a note: {c['secret'].strip()[:40]})")
                continue
            gone = [n for n in names if n not in have]
            if gone:
                missing.append(f"{c['name']} ({', '.join(gone)})")
        if missing:
            add("connector-missing-secret", _summarise(missing),
                f"{len(missing)} connector(s) name a secret that does not exist in public.secrets — they cannot authenticate")
    # A connector with NO credential recorded at all passed clean before — nobody knows where its
    # access lives. 'browser' (Playwright) genuinely needs none; anything else is a real gap.
    # These genuinely hold no stored credential: browser = Playwright; MCP connectors
    # authenticate through the host app, not a key in the safe.
    NO_CREDENTIAL_OK = {"browser"}
    def _no_cred_ok(n): return n in NO_CREDENTIAL_OK or "(mcp)" in n.lower()
    blank = q("SELECT name FROM connectors WHERE btrim(coalesce(secret,'')) IN ('', '-', '—', 'n/a', 'N/A', 'none') ORDER BY name")
    if blank is None:
        add("couldnt-check", "connectors", "blank-credential query ERRORED", "high")
    else:
        unexplained = sorted(b["name"] for b in blank if not _no_cred_ok(b["name"]))
        if unexplained:
            add("connector-no-credential", _summarise(unexplained),
                f"{len(unexplained)} connector(s) record NO credential at all — nobody knows where their access lives")

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
        # Require the QUALIFIED schema.table to appear. Matching a bare word let 'payroll'
        # (a passing mention inside the Staff row) hide all 5 payroll tables, and 'reports'
        # hide another — 33 tables across 8 schemas were being reported as 26 across 6.
        unhomed = sorted({f"{r['schema']}.{r['name']}" for r in nps
                          if not _homed(f"{r['schema']}.{r['name']}", dm_text)})
        if unhomed:
            schemas = sorted({u.split(".")[0] for u in unhomed})
            add("unhomed-app-schema", _summarise(schemas),
                f"{len(unhomed)} table(s) across {len(schemas)} app schema(s) in this database have no data_map home ({', '.join(schemas)})", "low")

    # --- PROPERTIES (websites/apps): the locator's original purpose. A declaration that cannot
    # answer "where does its code live and who serves it" is how the wrong-repo clone happened.
    props = q("SELECT name, coalesce(f->>'github','') AS github, coalesce(f->>'hosting','') AS hosting, "
              "coalesce(f->>'front_door','') AS front_door "
              "FROM property_declarations WHERE lower(coalesce(f->>'status','')) NOT IN ('archived','retired','retiring')")
    if props is None:
        add("couldnt-check", "property_declarations", "property query ERRORED — could not check site/app declarations", "high")
    else:
        # wordpress/lovable-style hosting legitimately has no git repo — do not cry wolf there.
        # lovable REMOVED 18 Jul: both lovable sites (Sygma Mala, Sales-Hire) DO have repos,
        # so exempting it blinded the check on a hosting type that always has one.
        NO_REPO_HOSTING = {"wordpress", "squarespace", "wix"}
        no_repo = sorted(p["name"] for p in props
                         if not p["github"] and p["hosting"].lower() not in NO_REPO_HOSTING)
        no_host = sorted(p["name"] for p in props if not p["hosting"])
        if no_repo:
            add("property-no-repo", _summarise(no_repo), f"{len(no_repo)} live propert(ies) with no repo declared — nobody can tell where the code lives")
        if no_host:
            add("property-no-hosting", _summarise(no_host), f"{len(no_host)} live propert(ies) with no hosting declared — nobody can tell who serves it")

        # --- FRONT DOORS (added 19 Jul 2026). A property's front door is the read-this-first note.
        # Two distinct failures, reported separately because the fixes are different:
        #   no front_door recorded      -> write one / decide it does not warrant one
        #   front_door does not resolve -> the note was renamed, moved or deleted (the LeakGuard
        #                                  orphan: a front door nobody could walk to)
        # front_door holds a vault_path, NOT a [[slug]] — slugs are not unique (several notes are
        # slugged "README"), so a slug cannot address one note.
        no_door = sorted(p["name"] for p in props if not p["front_door"])
        doors = [p for p in props if p["front_door"]]
        if doors:
            want = sorted({p["front_door"] for p in doors})
            inlist = ",".join("'" + d.replace("'", "''") + "'" for d in want)
            found = q(f"SELECT vault_path FROM vault_notes WHERE vault_path IN ({inlist})")
            if found is None:
                add("couldnt-check", "front doors",
                    "front-door resolution query ERRORED — could not verify the read-first notes", "high")
            else:
                have = {r["vault_path"] for r in found}
                broken = sorted(p["name"] for p in doors if p["front_door"] not in have)
                if broken:
                    add("property-front-door-broken", _summarise(broken),
                        f"{len(broken)} propert(ies) name a front door that does NOT resolve — the note was moved, renamed or deleted", "high")
        if no_door:
            add("property-no-front-door", _summarise(no_door),
                f"{len(no_door)} live propert(ies) have no front door recorded — no read-this-first page, so every session starts by re-deriving what the site is", "low")

    # --- ENTITIES: a live company with no Drive home has nowhere to file its documents.
    ents = q("SELECT slug, coalesce(drive_home,'') AS home FROM entities WHERE lower(coalesce(status,'')) NOT IN ('archived','retired','dissolved','planned','related')")
    if ents is None:
        add("couldnt-check", "entities", "entities query ERRORED — could not check company homes", "high")
    else:
        homeless = sorted(e["slug"] for e in ents if not e["home"])
        if homeless:
            add("entity-no-home", _summarise(homeless), f"{len(homeless)} live entit(ies) with no Drive home — nowhere to file their documents")

    # --- DRIVE HOMES (added 19 Jul 2026). Pete did NOT reject this check — he rejected the FIRST
    # design, which validated Drive PATHS: "i was asking you to look at an alternative because what
    # you proposed woud give me 100 flags a day". He was right. drive_files.path is denormalised and
    # drifts, so a path-based check re-reports the same drift every morning, for ever.
    #
    # The alternative: resolve each home ONCE to its Drive folder ID, then ask Drive about the ID.
    # IDs survive renames and moves; paths do not. Steady state is therefore silent, and a flag
    # means the folder is genuinely gone — which is worth reporting every day until it is fixed.
    #
    # Scope is deliberately small: only backing_ref 'drive:' rows (4 today). 'external:' refs
    # (github, gmail, the Sygma platform, the CD-leak Supabase) are OUT OF SCOPE by decision —
    # a reachability probe would reintroduce exactly the noise that was refused, via flaky network.
    dhomes = q("SELECT domain, backing_ref FROM data_map WHERE backing_ref LIKE 'drive:%'")
    if dhomes is None:
        add("couldnt-check", "drive homes", "drive-home query ERRORED — could not verify the Drive homes", "high")
    else:
        unresolved, gone = [], []
        for r in dhomes:
            want = (r.get("backing_ref") or "")[len("drive:"):].strip()
            if not want:
                continue
            esc = want.replace("'", "''")
            hit = q("SELECT drive_file_id, is_folder FROM drive_files "
                    f"WHERE path = '{esc}' OR (drive || '/' || path) = '{esc}' LIMIT 1")
            if hit is None:
                add("couldnt-check", r["domain"], "drive-home index lookup ERRORED — status unknown", "high")
                continue
            if not hit:
                # not in the index at all — either never indexed, or the path in data_map is stale
                unresolved.append(f"{r['domain']} → {want}")
                continue
            fid = hit[0]["drive_file_id"]

            def _ask_drive(_fid, _retry=True):
                """Returns 'ok' | 'gone' | 'trashed' | None. None means COULD NOT CHECK.
                A failed lookup is NOT evidence of absence — only a positive 404/not-found is.
                Reporting 'gone' on any non-zero exit is precisely the cry-wolf bug this whole
                check exists to avoid (caught by a decoy run, 19 Jul 2026: a transient API failure
                reported a live Finance folder as deleted)."""
                try:
                    c = subprocess.run(["python3", f"{VAULT}/drive-api.py", "info", _fid],
                                       env={**os.environ, "VAULT": VAULT},
                                       capture_output=True, text=True, timeout=45)
                except Exception:
                    c = None
                if c is not None and c.returncode == 0:
                    o = (c.stdout or "").lower()
                    if "trashed: true" in o:
                        return "trashed"
                    if " id: " in o or o.strip().startswith("id:"):
                        return "ok"
                blob = ((c.stdout or "") + (c.stderr or "")).lower() if c is not None else ""
                if "404" in blob or "not found" in blob or "notfound" in blob:
                    return "gone"          # a POSITIVE absence signal from Drive
                if _retry:
                    time.sleep(1.5)
                    return _ask_drive(_fid, _retry=False)
                return None                # could not check — never "gone"

            verdict = _ask_drive(fid)
            if verdict == "gone":
                gone.append(f"{r['domain']} → {want}")
            elif verdict == "trashed":
                gone.append(f"{r['domain']} → {want} (in trash)")
            elif verdict is None:
                add("couldnt-check", r["domain"],
                    "Drive lookup for this home could not complete — status UNKNOWN, not reported clean", "high")
        if gone:
            add("drive-home-gone", _summarise(gone),
                f"{len(gone)} data_map Drive home(s) no longer exist in Drive — the folder was deleted or trashed", "high")
        if unresolved:
            add("drive-home-unindexed", _summarise(unresolved),
                f"{len(unresolved)} data_map Drive home(s) are not in the Drive index — either never indexed, or the recorded path is stale", "medium")

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
        unhomed = sorted(b["name"] for b in bks if not _homed_bucket(b["name"], dm_rows))
        if unhomed:
            add("unhomed-bucket", _summarise(unhomed), f"{len(unhomed)} storage bucket(s) with no data_map home")


def main():
    as_json = "--json" in sys.argv
    gaps = []

    dm = q("SELECT domain, home, access, notes, backing_ref FROM data_map ORDER BY sort")
    tbls = q("SELECT c.relname AS name, c.relkind AS kind FROM pg_class c "
             "JOIN pg_namespace n ON n.oid=c.relnamespace "
             "WHERE n.nspname='public' AND c.relkind IN ('r','v','m') ORDER BY c.relname")
    if dm is None or not dm or tbls is None or not tbls:
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
        if _homed_table(name, dm_text):      # QUALIFIED match (or grandfathered)
            continue
        # populated? (skip genuinely empty internal tables)
        cnt = q(f'SELECT count(*) AS n FROM public."{name}"')
        if cnt is None:                      # errored ≠ empty: never silently treat as "nothing here"
            gaps.append({"rule": "couldnt-check", "subject": name, "detail": f"row-count query ERRORED — cannot say whether it is homed; NOT counted as clean", "severity": "high"})
            continue
        if not cnt:                          # readable but shapeless -> status unknown, NOT zero rows
            gaps.append({"rule": "couldnt-check", "subject": name, "detail": "row count came back unreadable — cannot say whether this table is populated", "severity": "high"})
            continue
        n = cnt[0]["n"]
        if n and n > 0:
            gaps.append({"rule": "unhomed-table", "subject": name, "detail": f"{n} rows, populated but has NO data_map home and is not on the infra allow-list", "severity": "medium"})
        else:
            # An EMPTY unhomed table was passed silently — but a table created during a session is
            # empty at exactly the moment closeout runs, so the most likely "something new went
            # unfiled" case was the one case that stayed quiet. Low severity: it is a new thing to
            # home, not a fault. Adds no noise today — every currently-empty unhomed table is
            # already on the infra allow-list or grandfathered, so this loop never reaches them.
            gaps.append({"rule": "unhomed-table-empty", "subject": name, "detail": "no rows yet, but it has NO data_map home — a table created this session looks exactly like this", "severity": "low"})

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
        elif not cnt:                        # readable but shapeless -> could not check, NOT empty
            gaps.append({"rule": "couldnt-check", "subject": r["domain"], "detail": f"{ref}: row count came back unreadable — status unknown", "severity": "high"})
        elif cnt[0]["n"] == 0:
            gaps.append({"rule": "empty-home", "subject": r["domain"], "detail": f"backing_ref {ref}, but that table is EMPTY (home points at a table with no data)", "severity": "high"})

    # (r) ROW-GRANULAR — the everyday adds (a skill, a helper, a project, a bucket)
    check_rows(gaps, dm_text, dm)

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
    source = "scheduled" if os.path.basename(os.environ.get("CRON_SCRIPT", "")) == os.path.basename(__file__) else "manual"
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
        sys.stderr.write("[cc-locator-audit] the report did NOT record to daily_log — the briefing would re-serve a stale row as today's\n")
        print("  !! NOT RECORDED to daily_log — do not treat the briefing's newest row as today's")
        sys.exit(98)

    # A successful REPORT always exits 0, however many gaps it found. Finding drift is the tool
    # working, not the tool failing — exiting non-zero made Railway stamp the cron FAILED and
    # emailed Pete a "crash" for a healthy run. Consumers read the count from --json, never $?.
    # Non-zero is reserved for a genuine abort (99 above), so the two can't be confused.
    sys.exit(0)

if __name__ == "__main__":
    main()
