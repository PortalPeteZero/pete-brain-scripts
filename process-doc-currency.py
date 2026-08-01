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
        # a CHANGE to how capture works, not merely reading it
        "changed": r"clancy-dn-(?:import|capture|pdf)\.py|clancy_dn_answers\?|--queue\b",
        "records": "how a damage is pulled off Depotnet: the tabs, the exports, the traps",
    },
    {
        "name": "Genny's Damage Depot pages",
        "doc": "Customers/SY-Clancy/clancy-depotnet-damages.md",
        "changed": r"clancy-dn-(?:pages|hub|analysis|reports|gc-pages|unmapped)\.py\s+.*--publish"
                   r"|clancy-vocab-check\.py",
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
    """Session start, and every tool call made in it."""
    start, calls = None, []
    try:
        with open(path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if start is None and e.get("timestamp"):
                    start = e["timestamp"]
                m = e.get("message") or {}
                if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                    for c in m["content"]:
                        if c.get("type") == "tool_use":
                            calls.append(c.get("name", "") + " " +
                                         json.dumps(c.get("input", ""))[:4000])
    except Exception:
        pass
    return start, calls


def evaluate(start, calls):
    """Which guarded domains did this session CHANGE without touching their doc?"""
    blob = "\n".join(calls)
    out = []
    for d in DOMAINS:
        hits = [c for c in calls if re.search(d["changed"], c)]
        if not hits or not any(MUTATING.search(c) for c in hits):
            continue                      # read-only in this domain, or untouched
        # was the doc itself written this session?
        if re.search(re.escape(d["doc"]), blob) or re.search(
                re.escape(d["doc"].rsplit("/", 1)[-1]), blob):
            # the path appearing in a WRITE, not just a SELECT
            wrote = [c for c in calls if d["doc"].rsplit("/", 1)[-1] in c
                     and MUTATING.search(c)]
            if wrote:
                continue
        upd = doc_updated_at(d["doc"])
        if upd == "UNCHECKABLE":
            continue                      # never block on our own inability to check
        if upd and start and upd >= start:
            continue                      # written since the session began — fine
        out.append({**d, "updated": upd or "never", "example": hits[0][:120]})
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
        f"  Doc:  {d['doc']}   (last updated {d['updated']})\n"
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
