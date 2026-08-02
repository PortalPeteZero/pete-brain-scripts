#!/usr/bin/env python3
"""process-doc-currency.py — refuse to end a turn that changed how a thing works without
recording it in that thing's process doc.

Pete, 1 Aug 2026: "can you set some kind of gate that after every turn up updat e hte process
doc , edit , add , ammend , delete whatever , ths hsould be done on every turn as we learn and
adapt".

THE FAILURE THIS EXISTS FOR. Across 1 Aug the Clancy work learned a dozen things that only
existed in the chat: that Depotnet's tab is called Report and not "the investigation form",
that the incident PDF carries no action tabs, that the Incident Register export has 16 columns
and none of them touch the forms, that changing a Depotnet `#/` URL does not reload the page.
Every one of those would have been re-derived — or worse, re-guessed — by the next session. The
process docs were only updated because Pete asked twice.

WHY A GATE AND NOT A RULE. A rule to "keep the docs current" is a wish; nothing refuses. This
refuses. It fires ONLY when the session actually changed something in a guarded domain — edited
its code, published its pages, ran its import — and that domain's process doc has not been
written to since the session began. A read-only session, a question, a chat: silent.

The bar is deliberately "was it touched this session", not "is it good". Nothing can measure
good. But a doc that was never opened on a day its subject changed is provably stale.

  echo '{"transcript_path": "..."}' | python3 process-doc-currency.py
  python3 process-doc-currency.py --test <transcript.jsonl>   # measure BEFORE trusting it
  python3 process-doc-currency.py --domains                   # what is guarded
"""
import json, os, re, sys, datetime, urllib.request

# A domain is guarded only where (a) a process doc exists and (b) a change to the domain is
# unambiguous from the tool calls. Add a domain by adding a row — nothing else to change.
DOMAINS = [
    {
        "name": "Clancy Depotnet capture",
        "doc": "Customers/SY-Clancy/clancy-depotnet-capture.md",
        # a CHANGE to how capture works, not merely reading it.
        # These names must track the tools that ACTUALLY run. The original list was
        # import|capture|pdf — the Chrome-era tools — so once capture moved to the API path
        # (ingest / files / verify / drive-audit) the gate matched nothing and the capture doc
        # could never be flagged stale, which is the exact rot it exists to prevent. Caught by
        # the FY25/26 plan audit, 2 Aug 2026.
        "changed": r"clancy-dn-(?:import|capture|pdf|ingest|files|verify|drive-audit)\.py"
                   r"|clancy_dn_answers\?|--queue\b|--relink\b",
        "records": "how a damage is pulled off Depotnet: the tabs, the exports, the traps",
    },
    {
        "name": "Genny's Damage Depot pages",
        "doc": "Customers/SY-Clancy/clancy-depotnet-damages.md",
        # clancy-dn-publish.py runs all six publishers in one command, so a publish that goes
        # through it matched none of the per-tool patterns below.
        "changed": r"clancy-dn-(?:pages|hub|analysis|reports|gc-pages|unmapped)\.py\s+.*--publish"
                   r"|clancy-dn-publish\.py|clancy-vocab-check\.py",
        "records": "what each page is, how it refreshes, and the wording rules it publishes under",
    },
]

# Only these count as changing something. A grep, a SELECT, a screenshot do not.
MUTATING = re.compile(r"--publish\b|--apply\b|\bWrite\b|\bEdit\b|UPDATE |INSERT |DELETE |PATCH", re.I)


def secrets():
    sec = os.path.expanduser("~/.config/pete-secrets")
    if not os.path.exists(f"{sec}/command-centre-supabase-keys.json"):
        sec = os.environ.get("VAULT", "/tmp/pbs") + "/Library/processes/secrets"
    return json.load(open(f"{sec}/command-centre-supabase-keys.json"))


def doc_updated_at(vault_path):
    """When was this process doc last written? None if it does not exist."""
    try:
        k = secrets()
        q = ("vault_notes?select=updated_at&vault_path=eq."
             + urllib.request.quote(vault_path, safe=""))
        req = urllib.request.Request(f"{k['url']}/rest/v1/{q}",
                                     headers={"apikey": k["service_role_key"],
                                              "Authorization": f"Bearer {k['service_role_key']}"})
        rows = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        return rows[0]["updated_at"] if rows else None
    except Exception:
        return "UNCHECKABLE"


def read_transcript(path):
    """Session start, and every tool call WITH ITS TIMESTAMP.

    The timestamp matters. The first version of this gate asked "was the doc written since the
    session began", which is useless in a long session: this one ran 39 hours, the docs were
    written at hour 13, and the gate then passed for ever no matter how much changed afterwards.
    It is now "was the doc written since the last change IN THAT DOMAIN".
    """
    start, calls = None, []
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
                        if c.get("type") == "tool_use":
                            # A Workflow/Agent call CONTAINS pattern text without executing
                            # anything itself — an audit workflow whose prompts mention
                            # "clancy-dn-publish.py" fired this gate on 2 Aug 2026 while
                            # mutating nothing. Real mutations reach the transcript as
                            # Bash / Edit / Write / NotebookEdit calls, so only those count.
                            if c.get("name") in ("Workflow", "Agent", "Task"):
                                continue
                            # A git command is never a page change either — a COMMIT MESSAGE
                            # that discusses the watched tools fired this gate on 2 Aug 2026
                            # minutes after the Workflow fix: the message text matched the
                            # domain pattern, the word "Write" in it matched MUTATING. Pages
                            # change via the publishers and DB writes, never via git.
                            if c.get("name") == "Bash":
                                _cmd = (c.get("input") or {}).get("command", "")
                                if re.match(r"\s*git\b", _cmd):
                                    continue
                            calls.append((ts, c.get("name", "") + " " +
                                          json.dumps(c.get("input", ""))[:4000]))
    except Exception:
        pass
    return start, calls


def evaluate(start, calls):
    """Which guarded domains were CHANGED more recently than their doc was written?"""
    out = []
    for d in DOMAINS:
        hits = [(ts, c) for ts, c in calls
                if re.search(d["changed"], c) and MUTATING.search(c)]
        if not hits:
            continue                      # read-only in this domain, or untouched
        last_change = max(ts for ts, _ in hits if ts) if any(ts for ts, _ in hits) else start
        upd = doc_updated_at(d["doc"])
        if upd == "UNCHECKABLE":
            continue                      # never block on our own inability to check
        if upd and last_change and upd >= last_change:
            continue                      # the doc is newer than the last change — fine
        out.append({**d, "updated": upd or "never", "changed_at": last_change,
                    "example": hits[-1][1][:120]})
    return out


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    start, calls = read_transcript(payload.get("transcript_path", ""))
    if not calls:
        sys.exit(0)
    findings = evaluate(start, calls)
    if not findings:
        sys.exit(0)
    d = findings[0]
    sys.stderr.write(
        f"BLOCKED by process-doc-currency: this session changed **{d['name']}** but its process "
        f"doc has not been written to since the session began.\n"
        f"  Doc:  {d['doc']}\n"
        f"        last written {d['updated']} · domain last changed {d.get('changed_at')}\n"
        f"  It records {d['records']}.\n"
        f"  What triggered this: {d['example']}\n\n"
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
            print(f"{d['name']}\n  doc: {d['doc']}\n  fires on: {d['changed']}\n")
        sys.exit(0)
    if "--test" in sys.argv:
        p = sys.argv[sys.argv.index("--test") + 1]
        start, calls = read_transcript(p)
        print(f"session start: {start} · {len(calls)} tool calls")
        f = evaluate(start, calls)
        if not f:
            print("would NOT block — no guarded domain was changed without its doc being written")
        for x in f:
            print(f"would BLOCK [{x['name']}] — doc last updated {x['updated']}\n"
                  f"   trigger: {x['example']}")
        sys.exit(0)
    main()
