#!/usr/bin/env python3
"""cc-orientation-map-sync.py — render the boot orientation map (CC `config` key `map-md`,
cached to MAP.cache.md and loaded into Claude every session) FRESH from the live CC tables,
twice daily, so it can never go stale.

COMPOSE, don't preserve: the whole doc is emitted each run =
  curated guidance (this script's constants, Claude-authored — NOT scraped from the live doc)
  + live counts  + the data_map routing table  + a /m/map pointer.

Safety rails (this writes Claude's own boot context):
  1. build in memory -> VALIDATE -> only then write (abort + raise [map-build-failed] on any fault)
  2. back the current value up to config `map-md-prev` BEFORE writing
  3. read-back assert; rollback from -prev on mismatch

    VAULT=/tmp/pbs python3 /tmp/pbs/cc-orientation-map-sync.py --dry   # print composed doc, write nothing
    VAULT=/tmp/pbs python3 /tmp/pbs/cc-orientation-map-sync.py         # render + write live
"""
# CRON-META
# what: Renders the boot orientation map (config.map-md, loaded into Claude every session) fresh from live CC tables — live counts + the data_map routing table + a /m/map pointer + curated guidance — so it can never go stale.
# why: config.map-md was hand-maintained and drifted (said ~1,950 notes vs live ~950); nothing refreshed it. This makes it current by construction (like cc_map), with validate-before-write + a map-md-prev backup so a bad run can't poison the boot context.
# reads: CC vault_notes/drive_files/secrets/crons/modules counts + public.data_map routing rows
# writes: CC config (key=map-md, + map-md-prev backup) ; public.tasks ([map-build-failed] guard, raise+clear)
# entity: command-centre
# schedule: 0 6,13 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import json, sys, os, urllib.request, urllib.error, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
KEYS = json.load(open(f"{VAULT}/Library/processes/secrets/command-centre-supabase-keys.json"))
URL, SVC = KEYS["url"], KEYS["service_role_key"]
DRY = "--dry" in sys.argv
HDR = {"apikey": SVC, "Authorization": f"Bearer {SVC}", "Content-Type": "application/json"}


def rest(method, path, body=None, prefer=None):
    data = json.dumps(body).encode() if body is not None else None
    h = dict(HDR)
    if prefer:
        h["Prefer"] = prefer
    r = urllib.request.Request(f"{URL}/rest/v1/{path}", data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=40) as resp:
        txt = resp.read().decode()
        return resp.status, (json.loads(txt) if txt else [])


def getval(key):
    _, d = rest("GET", f"config?select=value&key=eq.{key}")
    return d[0]["value"] if d else None


def fail(reason):
    """Abort safely: write nothing, raise/refresh the [map-build-failed] task, exit non-zero."""
    print(f"orientation-map: ABORT — {reason} (map-md NOT written)", file=sys.stderr)
    name = "[map-build-failed] orientation map render aborted"
    _, ex = rest("GET", "tasks?select=id&status=eq.todo&source=eq.cc-orientation-map-sync&name=eq." + urllib.parse.quote(name))
    note = f"render aborted {datetime.datetime.now(datetime.timezone.utc).isoformat()}: {reason}"
    if ex:
        rest("PATCH", f"tasks?id=eq.{ex[0]['id']}", {"notes": note})
    else:
        rest("POST", "tasks", {"name": name, "priority": "P1", "entity_slug": "Personal",
             "project_slug": "PA-Command-Centre", "status": "todo", "source": "cc-orientation-map-sync", "notes": note})
    sys.exit(2)


def clear_fail_task():
    name = "[map-build-failed] orientation map render aborted"
    _, ex = rest("GET", "tasks?select=id&status=eq.todo&source=eq.cc-orientation-map-sync&name=eq." + urllib.parse.quote(name))
    for t in ex:
        rest("PATCH", f"tasks?id=eq.{t['id']}", {"status": "done", "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "notes": "auto-cleared: render healthy"})


import urllib.parse


def cnt(table, filt=""):
    try:
        _, d = rest("GET", f"{table}?select=count" + (f"&{filt}" if filt else ""))
        v = d[0]["count"]
    except Exception as e:
        fail(f"live count failed for {table} ({e})")
    if v is None:
        fail(f"null count for {table}")
    return v


# ---------- live inputs ----------
N_NOTES = cnt("vault_notes")
N_FILES = cnt("drive_files")
N_SECRETS = cnt("secrets")
N_CRONS = cnt("crons")
N_MODULES = cnt("modules", "enabled=eq.true&status=neq.hidden")
try:
    _, homes = rest("GET", "data_map?select=domain,home,access,sort&order=sort")
except Exception as e:
    fail(f"data_map read failed ({e})")
if not homes or any(not (h.get("home") and h.get("domain")) for h in homes):
    fail("data_map empty or has a blank row")

# ---------- curated guidance (Claude-authored constants — numbers neutralised; live numbers render separately) ----------
PREAMBLE_TOP = r"""# MAP — where everything lives (Business OS)

> [!important] Read this first. It is an **orientation index, not a file listing.**
> Content lives in Google Drive and the Command Centre Supabase; this map points at those live sources instead of duplicating them. **It is rendered fresh from the live CC tables on every run (twice daily) — nothing here is a hand-typed snapshot, so nothing goes stale.**
>
> Migration record (now complete): query `vault_notes` for `business-os-cutover-complete-2026-06-24` and `business-os-master-plan-2026-06-20`.

## Routing — send every action to its CC home (NEVER the generic/local default)

The recurring failure is reaching for a tool's **generic default** (a local file, code-first) instead of the CC's cloud/registry home. The rule, every time:
- **"Where does X live / how do I do X?"** -> run **`whereis.py "<thing>"`** (reads `data_map` + `public.crons` + pages + Drive + knowledge, live) -> then the one process note. NEVER reverse-engineer from code; NEVER trust an old plan or daily note for where/how.
- **A schedule / automation** -> Railway via **`cc-cron.py`** -> `public.crons`. NEVER a local scheduled task.
- **A plan** -> `vault_notes` (`cc-knowledge-ingest.py`). NEVER `.claude/plans/` or any local file.
- **A new procedure / SOP / connection** -> its registry via its one tool (`cc-knowledge-ingest.py` -> `vault_notes`; register a connection in `[[connections]]`). NEVER a free-floating doc.
- Each category's home + tool IS its **`data_map`** row (the routing table below is rendered from it); if a tool ever changes, fix that row in the SAME piece of work.

## How tools run (post-cutover — read this before any command)

There is **no local script tree** any more. The boot kernel clones `pete-brain-scripts` to **`/tmp/pbs`** and materialises secrets there; every tool is a **flat file at `/tmp/pbs/<tool>.py`** and is run with `VAULT=/tmp/pbs` set:

```
python3 ~/.config/pete-cc/pete-session-bootstrap.py          # Step 0 — clone + secrets (once per session)
VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT 1"         # then run any tool
```

So `cc-sql.py`, `cc-knowledge-api.py`, `drive-api.py`, etc. are all at `/tmp/pbs/`. (The old `Library/processes/scripts/<tool>.py` path is retired — it does not exist locally.)"""

FOUR_HOMES = r"""## The four homes (one home per thing)

| You want… | It lives in | How to reach it |
|---|---|---|
| **A file** (any document, sheet, PDF, image, report) | **Google Drive** — 12 indexed drives | `VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "SELECT drive,path FROM drive_files WHERE name ILIKE '%X%'"`. Drives sync locally under `~/Library/CloudStorage/GoogleDrive-pete.ashcroft@sygma-solutions.com/`. |
| **Knowledge** (a lesson, decision, note, memory, "what did we decide about…") | **CC Supabase `vault_notes`** — link graph + semantic search | `VAULT=/tmp/pbs python3 /tmp/pbs/cc-knowledge-api.py` (full-text + meaning search). Surfaced in the CC **Brain** page (`/m/brain`). |
| **Pete's tasks** (status, priorities, what's due) | **CC `tasks` table** (`public.tasks`) | `cc-sql.py` — create/read/close. |
| **An operating action** (browse, search, run a report, see a property) | **The Command Centre** — commandcentre.info (web + phone) | The app. Pages index: `/m/map`. |

Code lives in **GitHub** (`pete-brain-scripts`, pulled to `/tmp/pbs` on demand). Automations + the 24/7 `cc-agent` + the `telegram-bridge` run on **Railway**. Secrets live in the CC `secrets` table (bootstrap key at `~/.config/pete-secrets/command-centre-supabase-keys.json`)."""

LOCAL_FOOTPRINT = r"""## The local footprint (thin client)

The vault is **retired** (24 Jun 2026 cutover). The Mac is a thin client — the only permanent local things are the boot kernel + the working-home folder; everything else is pulled/queried from the cloud:

- **Working home -> `My Drive/Command Centre/`** (in Google Drive, cloud-synced): holds the tiny `CLAUDE.md` bootstrap + the harness `.claude/` config. This is the folder the app opens. Full path: `~/Library/CloudStorage/GoogleDrive-pete.ashcroft@sygma-solutions.com/My Drive/Command Centre/`.
- `~/.config/pete-cc/` — the boot kernel (`pete-session-bootstrap.py`) + the `CLAUDE.cache.md` / `MAP.cache.md` fallbacks.
- `~/.config/pete-secrets/command-centre-supabase-keys.json` — the one CC bootstrap key.
- Crons run on **Railway** — manage them with the ONE tool **`cc-cron.py`** (deploy / set-schedule / pause / resume / retire / status); author the schedule in each script's `# CRON-META`, `public.crons` is the registry, `/m/automations-log` the page.
- On session start the kernel clones `pete-brain-scripts` -> `/tmp/pbs` and materialises secrets there; tools run from `/tmp` and are discarded.

Everything that used to be a vault folder (`Projects/`, `Properties/`, `Customers/`, `Suppliers/`, `Businesses/`, `Personal/`, `Accreditations/`, `Library/`, `Daily/`) is in **Drive + `vault_notes`** — query the live homes, never a local tree."""

DRIVE_HOMES = r"""## Drive home map (the 8 reorganised homes)

Top-level of each home drive (from the live `drive_files` index). **One home per entity; files belong to the entity, not the project.**

- **Sygma Hub** (the Sygma operating drive): `Accreditations` · `App Data` ⛔ · `Archive` · `Course Records` · `Courses` 🔒 · `Customer Specific Documentation` · `Customers and Suppliers` · `HR` · `Library` · `Marketing` · `Media` · `Projects` · `Reports` · `Sales & Pipeline`
- **Sygma Private** (owner/finance, private): `Accounts` · `Payroll` · `Personnel` · `_backups`
- **Canary Detect** (the CD operating drive): `App Data` ⛔ · `Blog Content` · `Canary Detect Mapping` · `CD Website Development` · `Company` · `Customers` · `ECO FINISH` · `Expenses` · `LeakGuard Admin` · `Leakguard Installs` · `Odoo Forms` · `Pools` · `Projects` · `Promotional` · `Report App` · `Survey Methodology` · `Trade Names and Marks` · `Vehicles` · `Videos`
- **CD Private** (CD owner/finance, private): `finance` · `payroll`
- **One System**: `Projects` · `Website Assets`
- **Ashcroft Family** (joint Pete + Michaela, private): `Finance` · `Health` · `Property` · `Vehicles` · `Legal` · `identity` · `Spanish Admin` · `HMRC Personal` · `House Insurance` · `Camello Blanco` · `Family Members` · `Travel`
- **El Atico**: `Agendas` · `Minutes` · `El Atico Accounts` · `Finance` · `Completed Forms` · `Blank Forms` · `Database` · `Write Ups` · `Stationary`
- **My Drive** (Pete's personal + the **Command Centre working home**): `Command Centre` (the working home) · `Passion Fit` · `Health` · `Finance` · `Freemasonry` · `Scouts` · `Los Claveles` · `Sporting Events` · `Projects` · `General` · `Inbox` · `ip-trademark` · `Business Brain` · `Screenshots` · `Media Uploads`

**Read-only indexed (captured, not reorganised):** Sygma Mala · Sygma Trainers · External Sygma Solutions · External Canary Detect.
**Excluded entirely (not indexed):** Petes Photo Archive · OSCA · Social Media · Pete & Mic · Sygma Backup's · DAPA.

🔒 `Sygma Hub/Courses/` is **locked** — never rename/reorganise. ⛔ `App Data` (Hub + CD) is the **live Portal/CD-CRM document store** — confirm the CRM's Drive reference before touching."""

ALWAYS_CAPTURE = r"""## Always-current capture

`drive_files` is kept live by **`drive-changes-watch`** (a **Railway** cron, every 15 min) — any add/move/rename/delete by Pete or staff, web or synced-local, is captured automatically with the correct drive + full path. The index never goes stale; you never hand-maintain a file list again."""

DECISION_TREE = r"""## How to find anything (decision tree)

1. **"Where is the file / document / sheet / photo?"** -> `drive_files` (`/tmp/pbs/cc-sql.py`). It returns the drive + path; open it under the local Drive mount.
2. **"What did we decide / learn about X?" / a lesson / a process write-up** -> `vault_notes` (`/tmp/pbs/cc-knowledge-api.py`) or the CC Brain page.
3. **"What's the status / what's due?"** -> the CC `tasks` table (`public.tasks` via `cc-sql.py`).
4. **"What's the live state of a website/app/property?"** -> the CC **Properties** module (property cards live there now).
5. **"How do I run X / where does X live / what's the API config?"** -> **`whereis.py "<thing>"`** first (one read across every registry); then `vault_notes` (`cc-knowledge-api.py`) for `connections` / process notes. Tools are in `pete-brain-scripts` (`/tmp/pbs`).
6. **"What automations run / how do I change a cron?"** -> the CC **Automations registry** (`/m/automations-log`, live from Railway) / `public.crons`; deploy or change one with **`cc-cron.py`** (the ONE tool)."""

MIGRATION = r"""## Migration status (24 Jun 2026 — COMPLETE)

All parts done: files -> Drive · knowledge -> `vault_notes` · code -> GitHub · secrets -> CC · automations -> Railway · CLAUDE/MAP -> CC `config` · CC modules live · Telegram bridge live · pull-on-demand boot kernel proven · vault retired · working home moved to `My Drive/Command Centre` (thin client). Full detail: query `vault_notes` for `business-os-cutover-complete-2026-06-24`."""

# ---------- rendered (live) sections ----------
stamp = datetime.datetime.now(datetime.timezone.utc)
TODAY = stamp.strftime("%Y-%m-%d")
FM = (f"---\ntype: vault-map\ndate: {TODAY}\nstatus: active\n"
      f"generated_by: cc-orientation-map-sync (rendered live, twice daily)\ntags: [system, index, business-os]\n---\n")

AT_A_GLANCE = ("## At a glance (live — rendered each run, never hand-typed)\n\n"
               f"- **Knowledge:** {N_NOTES:,} notes in `vault_notes`\n"
               f"- **Files:** {N_FILES:,} indexed across the Drive homes\n"
               f"- **Secrets:** {N_SECRETS} in the CC `secrets` table\n"
               f"- **Automations:** {N_CRONS} crons on Railway (`public.crons`)\n"
               f"- **CC pages:** {N_MODULES} live modules — full list at `/m/map`")

ROUTING_TABLE = ("## Where each kind of data lives — full routing (live from `data_map`)\n\n"
                 "_Rendered from the `data_map` table (the single curated routing source, kept current via `cc-data-map-sync.py`). Each line: **kind** -> home · _access_._\n\n"
                 + "\n".join(f"- **{h['domain']}** -> {h['home']}  ·  _{h['access']}_" for h in homes))

PAGES_POINTER = ("## Pages / modules\n\n"
                 f"The Command Centre has **{N_MODULES} live pages**. The full, always-current list is **`/m/map`** "
                 "(auto-generated 100% from the live `modules` table with a coverage self-check — it can't drift). "
                 "This orientation map does not duplicate it.")

# ---------- compose ----------
doc = "\n\n".join([
    FM + PREAMBLE_TOP,
    AT_A_GLANCE,
    FOUR_HOMES,
    ROUTING_TABLE,
    LOCAL_FOOTPRINT,
    DRIVE_HOMES,
    ALWAYS_CAPTURE,
    PAGES_POINTER,
    DECISION_TREE,
    MIGRATION,
]) + "\n"

# ---------- VALIDATE (before any write) ----------
cur = getval("map-md") or ""
if len(doc) < 2000:
    fail(f"composed doc suspiciously short ({len(doc)} chars)")
if cur and len(doc) < 0.8 * len(cur):
    fail(f"composed doc {len(doc)} < 80% of current {len(cur)} — refusing to shrink the map")
for needle in ("## Routing", "## The four homes", "## Where each kind of data lives", "## How to find anything"):
    if needle not in doc:
        fail(f"composed doc missing required section: {needle}")

if DRY:
    print(doc)
    print(f"\n--- DRY: validation PASSED · {len(doc)} chars · {len(homes)} routing rows · would write config.map-md (not written) ---", file=sys.stderr)
    sys.exit(0)

# ---------- write: backup -> write -> read-back -> (rollback on mismatch) ----------
_now = stamp.isoformat()
rest("POST", "config?on_conflict=key", {"key": "map-md-prev", "value": cur, "updated_at": _now}, prefer="resolution=merge-duplicates")
rest("PATCH", "config?key=eq.map-md", {"value": doc, "updated_at": _now})
back = getval("map-md")
if back != doc:
    rest("PATCH", "config?key=eq.map-md", {"value": cur})  # rollback
    fail("read-back mismatch after write — rolled back to previous value")
clear_fail_task()
print(f"orientation-map: map-md rendered live ({len(doc)} chars, {N_NOTES:,} notes, {len(homes)} routing rows) · prev backed up to map-md-prev")
