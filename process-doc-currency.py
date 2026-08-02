#!/usr/bin/env python3
"""process-doc-currency.py — refuse to end a turn that changed how a thing works without
recording it in that thing's process doc.

Pete, 1 Aug 2026: "can you set some kind of gate that after every turn up updat e hte process
doc , edit , add , ammend , delete whatever , ths hsould be done on every turn as we learn and
adapt".

THE FAILURE THIS EXISTS FOR. Across 1 Aug the Clancy work learned a dozen things that only
existed in the chat: that Depotnet's tab is called Report and not "the investigation form",
that the incident PDF carries no action tabs, that the Incident Register export has 16 columns
and none of them touch the forms. Every one of those would have been re-derived — or worse,
re-guessed — by the next session. The process docs were only updated because Pete asked twice.

WHY EFFECTS AND NOT TEXT. The first design scanned the transcript's tool-call text for tool
names. On 2 Aug it false-fired three times in three turns: on an audit WORKFLOW whose prompts
mentioned the tools, on a git COMMIT MESSAGE that discussed them, and on the TEST FIXTURE that
proved the previous fix — each time demanding a doc update for a change that never happened.
Text about a change is not a change. So the gate now asks the DATABASE what actually moved:

  · a domain's pages changed        -> max(updated_at) over its module_content keys
  · a domain's captured data moved  -> max capture timestamps on its tables
  · a domain's code was edited      -> Edit/Write tool calls whose file_path (the structured
    field, never free text) matches the domain's files

and fires only when a real change in THIS session postdates the doc's last write. A read-only
session, a question, an audit, a chat: silent — however loudly they discuss the tools.

  echo '{"transcript_path": "..."}' | python3 process-doc-currency.py
  python3 process-doc-currency.py --test <transcript.jsonl>   # measure BEFORE trusting it
  python3 process-doc-currency.py --domains                   # what is guarded
"""
import json, os, re, sys, datetime, urllib.request

# A domain is guarded where (a) a process doc exists and (b) its changes leave OBSERVABLE
# effects. "effects" are (table, timestamp-column, PostgREST filter) triples; "files" matches
# Edit/Write file_path values. Add a domain by adding a row.
DOMAINS = [
    {
        "name": "Clancy Depotnet capture",
        "doc": "Customers/SY-Clancy/clancy-depotnet-capture.md",
        "records": "how a damage is pulled off Depotnet: the tabs, the exports, the traps",
        "effects": [
            ("clancy_dn_incidents", "raw_api_at", None),
            ("clancy_dn_files", "captured_at", None),
        ],
        "files": r"clancy-dn-(?:import|capture|pdf|ingest|files|verify|drive-audit)\.py$",
    },
    {
        "name": "Genny's Damage Depot pages",
        "doc": "Customers/SY-Clancy/clancy-depotnet-damages.md",
        "records": "what each page is, how it refreshes, and the wording rules it publishes under",
        "effects": [
            ("module_content", "updated_at", "module_key=like.clancy-*"),
        ],
        "files": r"(?:clancy-dn-(?:pages|hub|analysis|reports|gc-pages|unmapped|publish)"
                 r"|clancy_dn_ui|clancy-vocab-check)\.py$",
    },
]


def secrets():
    sec = os.path.expanduser("~/.config/pete-secrets")
    if not os.path.exists(f"{sec}/command-centre-supabase-keys.json"):
        sec = os.environ.get("VAULT", "/tmp/pbs") + "/Library/processes/secrets"
    return json.load(open(f"{sec}/command-centre-supabase-keys.json"))


def _get(k, path):
    req = urllib.request.Request(f"{k['url']}/rest/v1/{path}",
                                 headers={"apikey": k["service_role_key"],
                                          "Authorization": f"Bearer {k['service_role_key']}"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())


def doc_updated_at(k, vault_path):
    """When was this process doc last written? None if it does not exist."""
    rows = _get(k, "vault_notes?select=updated_at&vault_path=eq."
                + urllib.request.quote(vault_path, safe=""))
    return rows[0]["updated_at"] if rows else None


def latest_effect(k, table, col, flt):
    """The newest real change the domain left in the database. None if nothing there."""
    q = f"{table}?select={col}&order={col}.desc.nullslast&limit=1"
    if flt:
        q += "&" + flt
    rows = _get(k, q)
    return rows[0][col] if rows and rows[0].get(col) else None


def _norm(ts):
    """Timestamps arrive as '2026-08-02 06:01:04.965641+00' (Postgres) and
    '2026-08-02T06:01:04.965Z' (transcript). Normalise to aware datetimes."""
    if not ts:
        return None
    t = str(ts).replace(" ", "T").replace("Z", "+00:00")
    if re.search(r"\+\d\d$", t):
        t += ":00"
    try:
        return datetime.datetime.fromisoformat(t)
    except Exception:
        return None


def read_transcript(path):
    """Session start, plus every Edit/Write call's (timestamp, file_path).

    ONLY the structured file_path field is read — never free text. Command strings, commit
    messages, workflow scripts and heredocs can all legitimately DISCUSS the guarded tools;
    discussing a change is not making one.
    """
    start, edits = None, []
    try:
        with open(path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                ts = e.get("timestamp")
                if start is None and ts:
                    start = ts
                m = e.get("message") or {}
                if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                    for c in m["content"]:
                        if (c.get("type") == "tool_use"
                                and c.get("name") in ("Edit", "Write", "NotebookEdit")):
                            fp = (c.get("input") or {}).get("file_path") or ""
                            if fp:
                                edits.append((ts, fp))
    except Exception:
        pass
    return start, edits


def evaluate(start, edits):
    """Which guarded domains show a REAL change, in this session, newer than their doc?"""
    out = []
    start_dt = _norm(start)
    try:
        k = secrets()
    except Exception:
        return out                        # never block on our own inability to check
    for d in DOMAINS:
        candidates = []                   # (datetime, human description)
        # 1. database effects — what actually changed, whoever changed it
        for table, col, flt in d["effects"]:
            try:
                eff = latest_effect(k, table, col, flt)
            except Exception:
                continue
            dt = _norm(eff)
            if dt:
                candidates.append((dt, f"{table}.{col} = {eff}"))
        # 2. code edits via the structured file_path field only
        for ts, fp in edits:
            if re.search(d["files"], fp):
                dt = _norm(ts)
                if dt:
                    candidates.append((dt, f"edited {fp}"))
        if not candidates:
            continue
        # only THIS session's changes are this session's to record
        if start_dt:
            candidates = [c for c in candidates if c[0] >= start_dt]
        if not candidates:
            continue
        last_dt, example = max(candidates, key=lambda c: c[0])
        try:
            upd = doc_updated_at(k, d["doc"])
        except Exception:
            continue                      # never block on our own inability to check
        upd_dt = _norm(upd)
        if upd_dt and upd_dt >= last_dt:
            continue                      # the doc is newer than the last real change — fine
        out.append({**d, "updated": upd or "never",
                    "changed_at": last_dt.isoformat(), "example": example})
    return out


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    start, edits = read_transcript(payload.get("transcript_path", ""))
    if not start:
        sys.exit(0)
    findings = evaluate(start, edits)
    if not findings:
        sys.exit(0)
    d = findings[0]
    sys.stderr.write(
        f"BLOCKED by process-doc-currency: this session changed **{d['name']}** but its process "
        f"doc has not been written to since that change.\n"
        f"  Doc:  {d['doc']}\n"
        f"        last written {d['updated']} · domain last changed {d.get('changed_at')}\n"
        f"  It records {d['records']}.\n"
        f"  What changed: {d['example']}\n\n"
        f"  Update it now — edit, add, amend or delete, whatever the session actually learned — "
        f"then finish the turn. Read it first, change the parts that are now wrong, and do not "
        f"append a duplicate section.\n"
        f"  Save it back IN PLACE: PATCH vault_notes on its vault_path, and SET updated_at "
        f"yourself — there is no trigger, so a PATCH without it leaves the doc looking stale and "
        f"this gate firing again. Do NOT use cc-save.py: it files under Library/notes/ and leaves "
        f"two copies (that happened on 1 Aug 2026). Also null embedded_hash so it re-embeds.\n"
        f"  Pete, 1 Aug 2026: \"this hsould be done on every turn as we learn and adapt\".\n")
    sys.exit(2)


if __name__ == "__main__":
    if "--domains" in sys.argv:
        for d in DOMAINS:
            effs = ", ".join(f"{t}.{c}" for t, c, _ in d["effects"])
            print(f"{d['name']}\n  doc: {d['doc']}\n  effects: {effs}\n  files: {d['files']}\n")
        sys.exit(0)
    if "--test" in sys.argv:
        p = sys.argv[sys.argv.index("--test") + 1]
        start, edits = read_transcript(p)
        print(f"session start: {start} · {len(edits)} structured file edits")
        f = evaluate(start, edits)
        if not f:
            print("would NOT block — no guarded domain changed after its doc was written")
        for x in f:
            print(f"would BLOCK [{x['name']}] — doc last updated {x['updated']}\n"
                  f"   change: {x['example']}")
        sys.exit(0)
    main()
