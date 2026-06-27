#!/usr/bin/env python3
"""te-log.py -- the Enquiry Engine (EE) triple-write helper.

ONE commit point for an enquiry touch, writing three homes in lockstep so they never drift:
  1. Portal CRM   (Supabase rsczwfstwkthaybxhszy) -- the FACTS: contact + activity + tags + stage
  2. CC vault_notes                                -- the INTELLIGENCE: the searchable knowledge note
  3. CC public.tasks                               -- the CHASE: a real task when a follow-up is due

Per [[training-enquiries-cc-cockpit-plan-2026-06-26]]. Dry-run by DEFAULT; --apply writes.
Rule #1: no duplicate contacts -- dedupe on email (then mobile/phone, then exact name) before any insert.
Everything the engine creates is tagged (activities created_by_name='Enquiry Engine') and emitted to a
manifest (--manifest) so a run is fully reversible.

Usage:
  VAULT=/tmp/pbs python3 te-log.py --in enquiry.json            # dry run, one enquiry
  VAULT=/tmp/pbs python3 te-log.py --in batch.json --apply      # apply (json may be one obj or a list)
  cat enquiry.json | VAULT=/tmp/pbs python3 te-log.py --apply --manifest /tmp/ee-manifest.jsonl

Payload shape (one enquiry):
{
  "full_name": "Jane Smith", "email": "jane@acme.co.uk", "phone": "...", "mobile": "...",
  "company_name": "Acme", "job_title": "...",
  "source": "web-enquiry",                       # or referral / phone / email
  "stage": "New",                                # New | Quoted | Customer | Lost (optional; default New on create)
  "tags": ["CAT & Genny", "EUSR"],               # course-cluster (mandatory) + optional intent/routing
  "activity": {                                  # the touch to log
     "kind": "enquiry",                          # enquiry|reply|quote|chase|handoff|correction|note (mapped to CRM vocab)
     "subject": "EUSR Cat 1 & 2 enquiry",
     "body": "What we sent / what happened ...",
     "outcome": "sent",                          # optional
     "occurred_at": "2026-06-26T09:00:00Z",      # optional; default now
     "follow_up_at": "2026-07-01"                # optional; sets CRM follow_up + a CC chase task
  },
  "knowledge": "Free-text thread summary + corrections to learn from (optional; defaults to activity.body)",
  "thread_id": "gmail-thread-id",                # optional; stored for triage reply-recognition
  "drive_url": "https://drive..."                # optional; sent collateral
}
"""
import os, sys, json, re, time, datetime as dt, urllib.request, urllib.parse, urllib.error, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SECRETS = f"{VAULT}/Library/processes/secrets"
PORTAL = json.load(open(f"{SECRETS}/sygma-portal-supabase-keys.json"))
PORTAL_URL, PORTAL_KEY = PORTAL["url"], PORTAL["service_role"]
CC_REF = "zhexcaflgahdcbzvbyfq"
SB_TOKEN = open(f"{SECRETS}/supabase-token").read().strip()

# CRM activity_type is CHECK-constrained to these only -- map our richer kinds onto them,
# carrying the real kind in the subject so nothing is lost. (Verified live 27 Jun.)
ACTIVITY_MAP = {"enquiry": "email", "reply": "email", "quote": "email", "email": "email",
                "chase": "task", "handoff": "note", "correction": "note", "note": "note",
                "call": "call", "meeting": "meeting", "booked": "note"}
PROJECT_SLUG = "SY-Training-Enquiries"
BUCKET = "Enquiries"
ENGINE = "Enquiry Engine"

def now_iso(): return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def slugify(s): return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "enquiry"

# ---- Portal CRM (PostgREST, service-role) -------------------------------------------------
def _preq(method, path, body=None, prefer=None):
    url = f"{PORTAL_URL}/rest/v1/{path}"
    h = {"apikey": PORTAL_KEY, "Authorization": f"Bearer {PORTAL_KEY}", "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=45) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt.strip() else []

def portal_get(table, **filt):
    qs = "&".join([f"{k}={v}" for k, v in filt.items()])
    return _preq("GET", f"{table}?{qs}")
def portal_insert(table, row):
    return _preq("POST", table, [row], prefer="return=representation")
def portal_patch(table, row, **filt):
    qs = "&".join([f"{k}={v}" for k, v in filt.items()])
    return _preq("PATCH", f"{table}?{qs}", row, prefer="return=representation")

# ---- CC (Supabase Management API: raw SQL) ------------------------------------------------
def cc_sql(sql):
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{CC_REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {SB_TOKEN}", "Content-Type": "application/json", "User-Agent": "curl/8.7.1"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())
def lit(s):
    if s is None: return "NULL"
    return "'" + str(s).replace("'", "''") + "'"

# ---- stages / tags cache ------------------------------------------------------------------
_STAGES = None
def stage_id(name):
    global _STAGES
    if _STAGES is None:
        _STAGES = {r["name"].lower(): r["id"] for r in portal_get("pipeline_stages", select="id,name")}
    return _STAGES.get((name or "New").lower(), _STAGES.get("new"))

def ensure_tag(name, apply, manifest):
    rows = portal_get("tags", select="id,name", name=f"eq.{urllib.parse.quote(name)}")
    if rows: return rows[0]["id"]
    if not apply:
        print(f"   [dry] would CREATE tag '{name}' (category=marketing)"); return None
    tid = portal_insert("tags", {"name": name, "category": "marketing", "colour": None})[0]["id"]
    manifest and manifest.write(json.dumps({"kind": "tag", "id": tid, "name": name}) + "\n")
    return tid

# ---- dedupe lookup (Rule #1) --------------------------------------------------------------
def find_contact(p):
    if p.get("email"):
        r = portal_get("contacts", select="id,full_name,email,stage_id", email=f"eq.{urllib.parse.quote(p['email'])}")
        if r: return r[0], "email"
    for fld in ("mobile", "phone"):
        if p.get(fld):
            digits = re.sub(r"\D", "", p[fld])[-9:]
            if len(digits) >= 7:
                r = portal_get("contacts", select="id,full_name,%s,stage_id" % fld, **{fld: f"ilike.*{digits}*"})
                if r: return r[0], fld
    if p.get("full_name"):
        r = portal_get("contacts", select="id,full_name,stage_id", full_name=f"eq.{urllib.parse.quote(p['full_name'])}")
        if r: return r[0], "name"
    return None, None

# ---- knowledge note (CC vault_notes via md -> ingest -> embed) -----------------------------
def write_knowledge(p, contact_id, apply):
    slug = f"enquiry-{slugify(p.get('company_name') or p.get('full_name') or p.get('email'))}-{slugify(p.get('activity',{}).get('kind','touch'))}"
    rel = f"Library/projects/SY-Training-Enquiries/enquiries/{slug}.md"
    a = p.get("activity", {})
    tags = ["SY-Training-Enquiries", "training-enquiries", "enquiry"] + [slugify(t) for t in p.get("tags", [])]
    body = (f"# Enquiry — {p.get('full_name') or p.get('company_name') or p.get('email')}\n\n"
            f"- **Contact:** {p.get('full_name','')} · {p.get('company_name','')} · {p.get('email','')} · {p.get('phone') or p.get('mobile') or ''}\n"
            f"- **CRM contact_id:** `{contact_id}` · **stage:** {p.get('stage','New')} · **source:** {p.get('source','web-enquiry')}\n"
            f"- **Course cluster / tags:** {', '.join(p.get('tags', [])) or '—'}\n"
            f"- **Touch:** {a.get('kind','touch')} — {a.get('subject','')}\n"
            + (f"- **Drive:** {p['drive_url']}\n" if p.get('drive_url') else "")
            + (f"- **Gmail thread:** `{p['thread_id']}`\n" if p.get('thread_id') else "")
            + f"\n## What we sent / what happened\n{a.get('body','')}\n"
            + f"\n## Knowledge / corrections to learn from\n{p.get('knowledge') or a.get('body','')}\n"
            + "\nLinked to the live Portal CRM contact; the lifecycle (stage, activities, booking) lives there, "
              "the learning lives here. Part of the [[training-enquiries-cc-cockpit-plan-2026-06-26|Enquiry Engine]].\n")
    if not apply:
        print(f"   [dry] would WRITE knowledge note {rel}"); return rel
    path = f"{VAULT}/{rel}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # YAML frontmatter (the ingest parser is YAML, not JSON) — key: value, lists as [a, b]
    fm_lines = [
        "type: enquiry",
        f"date: {(a.get('occurred_at') or now_iso())[:10]}",
        'project: "[[README|SY-Training-Enquiries]]"',
        "status: active",
        f"tags: [{', '.join(tags)}]",
        f"contact_id: {contact_id}",
    ]
    if p.get("thread_id"): fm_lines.append(f"thread_id: {p['thread_id']}")
    with open(path, "w") as f:
        f.write("---\n" + "\n".join(fm_lines) + "\n---\n\n" + body)
    return rel

# ---- the commit point ---------------------------------------------------------------------
def log_enquiry(p, apply, manifest):
    name = p.get("full_name") or p.get("company_name") or p.get("email") or "(unknown)"
    print(f"\n■ {name}  <{p.get('email','no-email')}>  [{p.get('activity',{}).get('kind','touch')}]")
    existing, by = find_contact(p)
    if existing:
        cid = existing["id"]
        print(f"   ↳ MATCH on {by}: contact {cid} ({existing.get('full_name')}) — append activity, no duplicate")
        if apply:
            upd = {"updated_at": now_iso()}
            for k in ("phone", "mobile", "company_name", "job_title"):
                if p.get(k) and not existing.get(k): upd[k] = p[k]
            portal_patch("contacts", upd, id=f"eq.{cid}")
    else:
        print(f"   ↳ NEW contact (stage {p.get('stage','New')}, marketing_consent=false)")
        if apply:
            row = {"full_name": p.get("full_name"), "email": p.get("email"), "phone": p.get("phone"),
                   "mobile": p.get("mobile"), "company_name": p.get("company_name"), "job_title": p.get("job_title"),
                   "type": "lead", "source": p.get("source", "web-enquiry"), "stage_id": stage_id(p.get("stage", "New")),
                   "marketing_consent": False, "notes": "Created by Enquiry Engine"}
            cid = portal_insert("contacts", {k: v for k, v in row.items() if v is not None})[0]["id"]
            manifest and manifest.write(json.dumps({"kind": "contact", "id": cid, "name": name}) + "\n")
        else:
            cid = "(new)"
    # tags
    for t in p.get("tags", []):
        tid = ensure_tag(t, apply, manifest)
        if apply and tid:
            existing_link = portal_get("contact_tags", select="tag_id", contact_id=f"eq.{cid}", tag_id=f"eq.{tid}")
            if not existing_link:
                portal_insert("contact_tags", {"contact_id": cid, "tag_id": tid})
                manifest and manifest.write(json.dumps({"kind": "contact_tag", "contact_id": cid, "tag_id": tid}) + "\n")
        print(f"   • tag {t}")
    # stage move (on an existing contact, if specified and different)
    if existing and p.get("stage") and apply:
        portal_patch("contacts", {"stage_id": stage_id(p["stage"])}, id=f"eq.{cid}")
    # activity
    a = p.get("activity", {})
    if a:
        at = ACTIVITY_MAP.get(a.get("kind", "note"), "note")
        print(f"   • activity [{a.get('kind')}→{at}] {a.get('subject','')}" + (f"  ⏰follow-up {a['follow_up_at']}" if a.get("follow_up_at") else ""))
        if apply:
            arow = {"contact_id": cid, "activity_type": at, "subject": f"[{a.get('kind','note')}] {a.get('subject','')}".strip(),
                    "body": a.get("body"), "outcome": a.get("outcome"), "occurred_at": a.get("occurred_at") or now_iso(),
                    "created_by_name": ENGINE, "follow_up_at": a.get("follow_up_at"),
                    "follow_up_done": False if a.get("follow_up_at") else None}
            aid = portal_insert("contact_activities", {k: v for k, v in arow.items() if v is not None})[0]["id"]
            manifest and manifest.write(json.dumps({"kind": "activity", "id": aid, "contact_id": cid}) + "\n")
    # knowledge note
    rel = write_knowledge(p, cid, apply)
    # chase task lifecycle (CC public.tasks): the LATEST touch defines the current chase. First close any
    # open chase for this contact (so they never pile up stale / never more than one open per enquiry), then
    # set a fresh one only if a follow-up is due. Tasks carry [no-sync-close] so email-task-sync leaves them
    # to the Engine (they're work-chases, not email-Reply-tray items) — avoids double-handling.
    if apply and cid not in ("(new)", None):
        closed = cc_sql(f"""update tasks set status='done', completed_at=now()
                            where source='enquiry-engine' and status='todo' and notes like {lit('%CRM contact '+str(cid)+'%')}
                            returning id""")
        if isinstance(closed, list) and closed:
            print(f"   • closed {len(closed)} prior open chase(s) for this enquiry")
    if a.get("follow_up_at"):
        tname = f"Chase enquiry — {name} ({a.get('subject','')})".replace("'", "")[:120]
        print(f"   • chase task → public.tasks (due {a['follow_up_at']}, project {PROJECT_SLUG}/{BUCKET})")
        if apply:
            cc_sql(f"""insert into tasks (id,name,priority,due_on,entity_slug,project_slug,bucket,status,source,tags,notes)
                       values (gen_random_uuid(),{lit(tname)},'P3',{lit(a['follow_up_at'])},'Sygma',{lit(PROJECT_SLUG)},{lit(BUCKET)},
                       'todo','enquiry-engine',array['enquiry']::text[],{lit('[no-sync-close] CRM contact '+str(cid))})""")
    return rel

def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    inpath = None; manpath = None
    for i, x in enumerate(args):
        if x == "--in": inpath = args[i + 1]
        if x == "--manifest": manpath = args[i + 1]
    raw = open(inpath).read() if inpath else sys.stdin.read()
    payload = json.loads(raw)
    items = payload if isinstance(payload, list) else [payload]
    print(f"=== te-log: {len(items)} enquiry touch(es) — {'APPLY (writing live)' if apply else 'DRY RUN (no writes)'} ===")
    manifest = open(manpath, "a") if (apply and manpath) else None
    notes = []
    for it in items:
        try:
            notes.append(log_enquiry(it, apply, manifest))
        except urllib.error.HTTPError as e:
            print(f"   ✖ HTTP {e.code}: {e.read().decode()[:200]}")
        except Exception as e:
            print(f"   ✖ {type(e).__name__}: {e}")
    if manifest: manifest.close()
    # ingest + embed the knowledge notes (walk the enquiries dir — reliable vs passing file paths)
    if apply and any(notes):
        print("\n=== ingesting + embedding knowledge notes ===")
        env = {**os.environ, "VAULT": VAULT}
        subprocess.run(["python3", f"{VAULT}/cc-knowledge-ingest.py", "Library/projects/SY-Training-Enquiries/enquiries/"], cwd=VAULT, env=env)
        subprocess.run(["python3", f"{VAULT}/cc-knowledge-embed-backfill.py"], cwd=VAULT, env=env)
    print(f"\n=== done: {len([n for n in notes if n])} note(s) {'written' if apply else 'planned'} ===")

if __name__ == "__main__":
    main()
