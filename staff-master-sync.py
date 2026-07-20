#!/usr/bin/env python3
"""
staff-master-sync.py -- Sygma staff snapshot + trainer roster refresh (on-demand / cron-runnable).

Rewritten 2026-07-03 (Item 9 of plan-pete-brain-scripts-local-vault-remediation-2026-07-02).

SOURCE OF TRUTH: the Sygma Platform hub schema (rsczwfstwkthaybxhszy) — staff are added/edited at
sygmaportal.com/hub/directory. This script READS:
  hub.staff_directory, hub.staff_hr, hub.staff_leave, hub.staff_leave_entitlement, hub.fleet

The Hub Staff Master Google Sheet (1o04hBPhGzyyD3q2kHusLG5cHgAIOfsD0v2zajoEgtf8) remains the source
ONLY for what the hub schema does not carry (a known gap, do not "fix" by dropping it):
  - cross-system IDs: google_calendar_id, jotform_canonical_name, asana_user_gid,
    xero_employee_id, odoo_employee_id, soldo_cardholder_ref, garmin_athlete_id
  - the 2024 / 2025 leave-history tabs (pre-Platform years)

OUTPUTS (all cloud — nothing permanent is written locally):
  1. reports.snapshots key `staff-master-cache` — the joined staff snapshot; also the baseline the
     next run diffs against (replaces the old local Staff Master.json cache).
  2. Library/processes/secrets/sygma-trainer-roster.yaml regenerated (hub rows + sheet IDs MERGED
     with the CURATED alias sidecar sygma-trainer-aliases.yaml), then published to
     (a) the CC `secrets` table (the durable home both bootstraps materialise from) and
     (b) the Railway jotform-training-eval-sync service env var SECRETFILE__sygma-trainer-roster__yaml
     so the weekly jotform-normalise.py run reads the fresh roster.
     The sidecar is hand-curated and NEVER auto-generated (2026-06-08 silent-wipe lesson).
  3. Diff lines vs the previous snapshot -> CC daily_log (cron_name 'staff-master-sync').
  4. Two consecutive failures -> undated P1 CC task (raise_p2).

RETIRED here (the old sheet-to-vault direction — consumers gone with the 24 Jun cutover):
  - Businesses/sygma-solutions/people/*.md regen (vault retired; hub IS the directory)
  - Library/sy-hr/Staff Master.json local cache (replaced by reports.snapshots)
  - local Daily/ note writes (replaced by CC daily_log)
  - Vercel dashboard JSON caches (dead since 2026-06-19)
  - inbound sheet->hub load (staff-hub-load.py, disabled 2026-06-10 — platform owns the data)

# CRON-META
# what: Staff snapshot + trainer-roster refresh — reads the Platform hub schema (+ sheet for cross-system IDs), publishes the joined snapshot to reports.snapshots, regenerates sygma-trainer-roster.yaml (CC secrets + Railway env), logs diffs to daily_log
# why: keeps the trainer roster (JotForm matching) and the staff snapshot current from the Platform, which is the source of truth
# reads: Portal hub.staff_directory/staff_hr/staff_leave/staff_leave_entitlement/fleet; Hub Staff Master sheet (IDs + 2024/25 leave history)
# writes: CC reports.snapshots (staff-master-cache); CC secrets (sygma-trainer-roster.yaml); Railway jotform env var; CC daily_log
# entity: sygma
# schedule: 30 5 * * *
# timezone: Europe/London
# CRON-META-END
"""

import json, os, sys, datetime, importlib.util, urllib.request, urllib.parse, urllib.error
import subprocess
import yaml  # PyYAML — also used by jotform-normalise.py

# === Config ===

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = f"{VAULT}/Library/processes/secrets"
HUB_STAFF_MASTER_ID = "1o04hBPhGzyyD3q2kHusLG5cHgAIOfsD0v2zajoEgtf8"
ROSTER_YAML = f"{SEC}/sygma-trainer-roster.yaml"          # generated (local copy is ephemeral)
ALIASES_YAML = f"{SEC}/sygma-trainer-aliases.yaml"        # curated, NEVER auto-generated
PORTAL_REF = "rsczwfstwkthaybxhszy"
SNAPSHOT_KEY = "staff-master-cache"
SUB_REF_BASE = 9001   # synthetic employee_ref band for subcontractors (staff-hub-load convention)

# The 7 cross-system ID columns the sheet remains authoritative for (hub schema has no home for them)
ID_FIELDS = ["google_calendar_id", "jotform_canonical_name", "asana_user_gid",
             "xero_employee_id", "odoo_employee_id", "soldo_cardholder_ref", "garmin_athlete_id"]
SHEET_ID_HEADERS = {"Ref #": "employee_ref", "Full Name": "full_name",
                    "Google Cal ID": "google_calendar_id", "JotForm Name": "jotform_canonical_name",
                    "Asana User GID": "asana_user_gid", "Xero ID": "xero_employee_id",
                    "Odoo ID": "odoo_employee_id", "Soldo Ref": "soldo_cardholder_ref",
                    "Garmin ID": "garmin_athlete_id"}

# === Generic helpers ===

def load_helper(module_name):
    _p = f"{VAULT}/{module_name}.py"
    if not os.path.exists(_p): _p = f"{VAULT}/Library/processes/scripts/{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, _p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def _cc():
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        d = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
        url, key = d["url"], d["service_role_key"]
    return url.rstrip("/"), key

def cc_rest(method, path, body=None, prefer=None, profile=None):
    base, key = _cc()
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    if profile:
        h["Accept-Profile"] = profile
        h["Content-Profile"] = profile
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}/rest/v1/{path}", data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=45) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip() else None

def portal_rest(path):
    d = json.load(open(f"{SEC}/sygma-portal-supabase-keys.json"))
    key = d.get("service_role") or d["service_role_key"]
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Accept-Profile": "hub"}
    req = urllib.request.Request(f"{d['url'].rstrip('/')}/rest/v1/{path}", headers=h)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())

# === Step 1: pull the Platform hub schema (source of truth) ===

def pull_hub():
    """Read the 5 hub tables; join staff_directory + staff_hr on employee_ref into unified rows."""
    directory = portal_rest("staff_directory?select=*&order=employee_ref")
    hr = {r["employee_ref"]: r for r in portal_rest("staff_hr?select=*")}
    leave = portal_rest("staff_leave?select=*")
    entitlement = portal_rest("staff_leave_entitlement?select=*")
    fleet = portal_rest("fleet?select=*")
    rows = []
    for d in directory:
        row = dict(d)
        for k, v in (hr.get(d["employee_ref"]) or {}).items():
            row.setdefault(k, v)   # directory fields win on collision
        rows.append(row)
    return {"directory": rows, "leave": leave, "leave_entitlement": entitlement, "fleet": fleet}

# === Step 2: slim sheet pull — cross-system IDs + 2024/25 leave history ONLY ===

def _fetch_values(tok, tab):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{HUB_STAFF_MASTER_ID}"
           f"/values/{urllib.parse.quote(tab, safe='')}?valueRenderOption=FORMATTED_VALUE")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    return json.load(urllib.request.urlopen(req)).get("values", [])

def pull_sheet_ids():
    """Directory tab -> {full_name: {id fields}} (+ employee_ref when present);
    2024/2025 tabs raw for the leave history. Everything else on the sheet is ignored —
    the Platform owns it now."""
    sh = load_helper("sheets-api")
    tok = sh.get_token()
    vals = _fetch_values(tok, "Directory")
    ids_by_name, ids_by_ref = {}, {}
    if vals:
        headers = vals[0]
        for raw in vals[1:]:
            row = {SHEET_ID_HEADERS[h]: (raw[i] if i < len(raw) else "")
                   for i, h in enumerate(headers) if h in SHEET_ID_HEADERS}
            name = (row.get("full_name") or "").strip()
            if not name: continue
            ids = {f: row.get(f, "") for f in ID_FIELDS}
            ids_by_name[name] = ids
            ref = str(row.get("employee_ref") or "").strip()
            if ref: ids_by_ref[ref] = ids
    history = {y: _fetch_values(tok, y) for y in ("2024", "2025")}
    return ids_by_name, ids_by_ref, history

def join_ids(hub_rows, ids_by_name, ids_by_ref):
    """Enrich hub-sourced rows with the sheet's cross-system IDs (ref first, name fallback)."""
    for r in hub_rows:
        ids = ids_by_ref.get(str(r.get("employee_ref") or "")) or ids_by_name.get((r.get("full_name") or "").strip()) or {}
        for f in ID_FIELDS:
            r[f] = ids.get(f, "") or r.get(f, "") or ""
    return hub_rows

# === Step 3: regen sygma-trainer-roster.yaml (alias sidecar merge preserved byte-for-byte) ===

def regen_roster(directory_rows):
    """Write sygma-trainer-roster.yaml from Sygma Training rows, MERGING the
    curated alias sidecar (sygma-trainer-aliases.yaml).

    The hub carries canonical/preferred/status; the sheet IDs carry calendar + jotform names;
    the sidecar carries the free-text `aliases` + `bare_aliases` per trainer plus the top-level
    `multi_trainer_separators` / `ambiguous_bare` that jotform-normalise.py needs
    to canonicalise free-text trainer names. The sidecar is hand-curated and is
    NEVER auto-generated, so roster regen can no longer wipe the aliases.
    (Fix for the 2026-06-08 silent-wipe: the old regen emitted only the 5 sheet
    fields and clobbered the in-roster aliases, zeroing trainer attribution.)"""
    # WHO IS A TRAINER is answered by holding a trainer record, NOT by which sub-business they sit in.
    # Filtering on sub_business == "Sygma Training" silently dropped Paul Baxter (Sygma GPR) and
    # Steve Scales (Sygma Solutions), both of whom hold a trainer_id — so the roster carried 9 people
    # instead of 11 and their evaluations could never resolve to a canonical name. (Fixed 20 Jul 2026.)
    rows = [r for r in directory_rows
            if r.get("trainer_id") and (r.get("employment_status") or "").strip() != "Left"]
    sidecar = {}
    if os.path.exists(ALIASES_YAML):
        try:
            sidecar = yaml.safe_load(open(ALIASES_YAML).read()) or {}
        except Exception as e:
            print(f"  WARN: could not read alias sidecar ({e}); roster will have no aliases")
    talias = sidecar.get("trainer_aliases", {}) or {}
    trainers = []
    for t in rows:
        canonical = t.get("jotform_canonical_name") or t.get("full_name", "")
        entry = {
            "canonical": canonical,
            "full_name": t.get("full_name", ""),
            "preferred_name": t.get("preferred_name", "") or "",
            "google_calendar_id": t.get("google_calendar_id", "") or "",
            # The calendar address. PROVEN 20 Jul 2026: all 11 trainer diaries read successfully via
            # work_email. Prefer this over google_calendar_id, which is blank for trainers (Kevin
            # Morley, Steve Mellor) whose diaries read fine — a blank there means nothing.
            "work_email": t.get("work_email", "") or "",
            "employment_status": t.get("employment_status", "") or "",
        }
        a = talias.get(canonical) or talias.get(t.get("full_name", "")) or {}
        if a.get("aliases"): entry["aliases"] = list(a["aliases"])
        if a.get("bare_aliases"): entry["bare_aliases"] = list(a["bare_aliases"])
        trainers.append(entry)
    roster = {"trainers": trainers}
    if sidecar.get("multi_trainer_separators"):
        roster["multi_trainer_separators"] = list(sidecar["multi_trainer_separators"])
    if sidecar.get("ambiguous_bare"):
        roster["ambiguous_bare"] = sidecar["ambiguous_bare"]
    header = ("# Auto-regenerated by staff-master-sync.py — DO NOT EDIT BY HAND\n"
              f"# Last run: {datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","")}Z\n"
              "# Platform hub.staff_directory rows (+ sheet cross-system IDs) MERGED with curated\n"
              "# aliases from sygma-trainer-aliases.yaml — edit THAT file (CC secrets table), not this one.\n")
    os.makedirs(os.path.dirname(ROSTER_YAML), exist_ok=True)
    text = header + yaml.safe_dump(roster, sort_keys=False, allow_unicode=True, default_flow_style=False)

    # ---- SAFETY GATE (added 20 Jul 2026) -------------------------------------------------------
    # This roster is PUBLISHED to the CC secrets store and to the Railway env var the Monday 06:34
    # evaluation sync reads. A bad regen therefore reaches a live consumer unattended. Two rules:
    #   1. Never silently lose a trainer. A shrink is either a real leaver (rare, and worth a human
    #      look) or a bug in the filter/source — both cases deserve a stop, not a quiet publish.
    #   2. Keep the previous roster, so a bad regen can be put back without re-deriving it.
    # Override for a genuine leaver with ROSTER_ALLOW_SHRINK=1.
    prev_names = set()
    if os.path.exists(ROSTER_YAML):
        try:
            prev = yaml.safe_load(open(ROSTER_YAML).read()) or {}
            prev_names = {t.get("canonical") for t in (prev.get("trainers") or []) if t.get("canonical")}
            bak = ROSTER_YAML + ".prev"
            with open(bak, "w") as f:
                f.write(open(ROSTER_YAML).read())
            print(f"  roster: previous copy kept at {bak}")
        except Exception as e:
            print(f"  WARN: could not read/snapshot previous roster ({e})")
    new_names = {t["canonical"] for t in trainers if t.get("canonical")}
    lost = prev_names - new_names
    if lost and os.environ.get("ROSTER_ALLOW_SHRINK") != "1":
        raise SystemExit(
            f"REFUSING TO PUBLISH: the regenerated roster LOSES {len(lost)} trainer(s): "
            f"{', '.join(sorted(lost))}.\n"
            f"  was {len(prev_names)} -> now {len(new_names)}.\n"
            "  If this is a genuine leaver, re-run with ROSTER_ALLOW_SHRINK=1. Otherwise the source "
            "or the filter is wrong — do NOT publish a short roster to the live evaluation sync.")
    if lost:
        print(f"  roster: shrink ALLOWED by ROSTER_ALLOW_SHRINK — losing {', '.join(sorted(lost))}")

    # Addresses, not just people. A head-count cannot see a trainer with no way to open their diary.
    # The calendar address is the WORK EMAIL (proven 20 Jul: all 11 diaries read via work_email).
    # Deliberately NOT gated on google_calendar_id — it is empty for trainers whose diaries read fine.
    no_addr = [t["canonical"] for t in trainers if not (t.get("work_email") or "").strip()]
    if no_addr:
        print(f"  WARN: {len(no_addr)} trainer(s) have NO work_email, so their diary cannot be swept: "
              f"{', '.join(sorted(no_addr))}")
    # ---------------------------------------------------------------------------------------------

    with open(ROSTER_YAML, "w") as f:
        f.write(text)
    return len(trainers), text

def publish_roster(text):
    """Push the regenerated roster to its two cloud homes: the CC secrets table (durable; both
    bootstraps materialise it) and the Railway jotform service env var (the weekly cron's copy)."""
    status = []
    # (a) CC secrets table
    try:
        cc_rest("POST", "secrets?on_conflict=name",
                [{"name": "sygma-trainer-roster.yaml", "value": text,
                  "description": "GENERATED trainer roster (hub.staff_directory + sheet IDs + alias sidecar) — regenerated by staff-master-sync.py; read by jotform-normalise.py.",
                  "category": "sygma"}],
                prefer="resolution=merge-duplicates,return=minimal")
        status.append("cc-secrets OK")
    except Exception as e:
        status.append(f"cc-secrets FAILED ({e})")
    # (b) Railway env var on the jotform service
    try:
        tok = (cc_rest("GET", "secrets?select=value&name=eq.railway-token") or [{}])[0].get("value")
        PROJECT = "b2d89898-cc67-43a7-b900-af2c2c8e4a66"
        ENVN = "7b0fd4ed-0f4a-41a4-8eb0-86e713397380"
        def rw(q, v):
            req = urllib.request.Request("https://backboard.railway.app/graphql/v2",
                data=json.dumps({"query": q, "variables": v}).encode(), method="POST",
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                         "User-Agent": "staff-master-sync/2.0"})
            out = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
            if out.get("errors"): raise RuntimeError(json.dumps(out["errors"])[:200])
            return out["data"]
        d = rw('query($p:String!){ project(id:$p){ services{ edges{ node{ id name } } } } }', {"p": PROJECT})
        sid = next((e["node"]["id"] for e in d["project"]["services"]["edges"]
                    if e["node"]["name"] == "jotform-training-eval-sync"), None)
        if sid:
            rw('mutation($i:VariableUpsertInput!){ variableUpsert(input:$i) }',
               {"i": {"projectId": PROJECT, "environmentId": ENVN, "serviceId": sid,
                      "name": "SECRETFILE__sygma-trainer-roster__yaml", "value": text}})
            status.append("railway-env OK")
        else:
            status.append("railway-env SKIPPED (jotform service not found)")
    except Exception as e:
        status.append(f"railway-env FAILED ({e})")
    return " | ".join(status)

# === Step 4: snapshot + diff into the CC ===

def read_prev_snapshot():
    try:
        rows = cc_rest("GET", f"snapshots?report_key=eq.{SNAPSHOT_KEY}&select=payload&order=published_at.desc&limit=1",
                       profile="reports")
        return rows[0]["payload"] if rows else {}
    except Exception:
        return {}

def publish_snapshot(payload):
    cc = load_helper("cc_publish")
    return cc.publish(SNAPSHOT_KEY, datetime.date.today().isoformat(), payload)

def diff_and_log(snapshot, prev):
    """Diff directory rows vs the previous snapshot; write the digest to CC daily_log."""
    today_dir = {r.get("full_name"): r for r in snapshot.get("directory", []) if r.get("full_name")}
    prev_dir = {r.get("full_name"): r for r in (prev.get("directory") or []) if r.get("full_name")}

    diffs = []
    for name, row in today_dir.items():
        if name not in prev_dir:
            diffs.append(f"NEW: {name} ({row.get('employment_status','')})")
            continue
        for field in ["employment_status", "google_calendar_id", "job_title", "work_email"]:
            if (row.get(field) or "") != (prev_dir[name].get(field) or ""):
                diffs.append(f"CHANGED {name}.{field}: '{prev_dir[name].get(field) or ''}' → '{row.get(field) or ''}'")
    for name in prev_dir:
        if name not in today_dir:
            diffs.append(f"REMOVED: {name}")

    content = (f"Staff master sync: {len(today_dir)} directory rows | {len(diffs)} diffs since last run"
               + ("".join(f"\n- {d}" for d in diffs[:15]))
               + (f"\n- (+{len(diffs)-15} more — see reports.snapshots {SNAPSHOT_KEY})" if len(diffs) > 15 else ""))
    try:
        cc_rest("POST", "daily_log",
                [{"date": datetime.date.today().isoformat(), "cron_name": "staff-master-sync", "content": content}],
                prefer="return=minimal")
    except Exception as e:
        print(f"  WARN: daily_log write failed ({e})")
    return diffs

# === Step 5: failure handling ===

def raise_p2(reason):
    """Raise a CC task (public.tasks) on 2 consecutive failures — an undated P1 (failure alert =
    undated importance, not a dated PD)."""
    try:
        name = f"staff-master-sync.py FAILED, {datetime.date.today().isoformat()}".replace("'", "")
        notes = (f"staff-master-sync.py failed: {reason}. Check the Portal hub schema access "
                 "(sygma-portal-supabase-keys.json) and the Hub Sheet permissions.").replace("'", "").replace("\n", " ")
        sql = ("INSERT INTO tasks (id,name,priority,base_priority,due_on,entity_slug,project_slug,status,source,notes) "
               f"VALUES (gen_random_uuid(),'{name}','P1','P1',NULL,'Sygma','General','todo','staff-master-sync','{notes}')")
        subprocess.run([sys.executable, f"{VAULT}/cc-sql.py", sql],
                       env={**os.environ, "VAULT": VAULT}, capture_output=True, timeout=30)
    except Exception as e:
        sys.stderr.write(f"CC failure-task raise itself failed: {e}\n")

# === main ===

def main():
    try:
        hub = pull_hub()
        ids_by_name, ids_by_ref, history = pull_sheet_ids()
        directory = join_ids(hub["directory"], ids_by_name, ids_by_ref)

        snapshot = {
            "_pulled_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z"),
            "source": "hub schema (Platform) + sheet cross-system IDs",
            "directory": directory,
            "leave": hub["leave"], "leave_entitlement": hub["leave_entitlement"], "fleet": hub["fleet"],
            "leave_history_sheet": history,
        }

        prev = read_prev_snapshot()
        trainer_count, roster_text = regen_roster(directory)
        roster_status = publish_roster(roster_text)
        diffs = diff_and_log(snapshot, prev)
        publish_snapshot(snapshot)

        print(f"staff-master-sync OK: directory={len(directory)} rows (hub-sourced), "
              f"roster trainers={trainer_count} [{roster_status}], diffs={len(diffs)}")
        return 0
    except Exception as e:
        # Record fail. Two consecutive fails raise the CC failure task.
        marker = "/tmp/staff-master-sync-last-fail"
        prev_fail = os.path.exists(marker)
        with open(marker, "w") as f: f.write(datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n")
        sys.stderr.write(f"staff-master-sync FAILED: {e}\n")
        if prev_fail:
            raise_p2(str(e))
            try: os.unlink(marker)
            except Exception: pass
        return 1

if __name__ == "__main__":
    sys.exit(main())
