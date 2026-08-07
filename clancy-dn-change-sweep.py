#!/usr/bin/env python3
"""clancy-dn-change-sweep.py — read Depotnet's own audit trail into the CC change ledger.

WHY. Depotnet's export sheets carry no last-modified field (verified live 3 Aug 2026 against
GetImIncidentRegisterGrid: no such column at list level), so an edit to an old record is
invisible at the overview. But every per-incident payload (`GetImIncident`) carries a
`timeline`: a full audit trail — timestamp, author, a typed entry ("Incident Amended"), and
the change spelled out INCLUDING the old value. This tool reads every stored payload's
timeline into `public.clancy_dn_change_ledger`, idempotently, so each re-capture of an
incident automatically banks whatever changed since we last looked.

The ledger is the evidence base: who changed what, when, from what — queryable forever,
even after Depotnet's current values overwrite our mirror. Born of damage 152586 (closed
and amended within days of the review that found its record incorrect).

The lean watch routine (no cron — runs when we work):
  1. Fresh register export -> clancy-dn-import.py   (catches NEW damages + overview edits
     on ALL records; the importer writes its field diffs to this ledger too)
  2. Re-pull the per-incident payloads for the recent window + watched damages
     (browser step, see [[depotnet-api]]) -> clancy-dn-ingest.py
  3. This sweep -> new timeline entries land in the ledger
  4. --report to see what moved

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-change-sweep.py               # sweep all stored payloads
  VAULT=/tmp/pbs python3 clancy-dn-change-sweep.py --incident 152586
  VAULT=/tmp/pbs python3 clancy-dn-change-sweep.py --report [--since 2026-08-01]
  VAULT=/tmp/pbs python3 clancy-dn-change-sweep.py --amended-only --report
"""
import os, sys, json, argparse, hashlib, urllib.request, urllib.parse
import datetime as _dt

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _k["url"], _k["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}


def rest(path, method="GET", body=None, headers=None):
    h = dict(H)
    h.update(headers or {})
    req = urllib.request.Request(f"{URL}/rest/v1/{path}",
                                 data=(json.dumps(body).encode() if body is not None else None),
                                 headers=h, method=method)
    with urllib.request.urlopen(req, timeout=180) as r:
        t = r.read().decode()
        return json.loads(t) if t else None


def timeline_rows(incident_id, raw):
    """One ledger row per timeline entry that records ACTIVITY worth watching.
    Every entry is kept (photos and documents added are activity too); the typed
    'Incident Amended' entries are the edit trail proper."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    data = raw.get("data") or raw
    tl = data.get("timeline") or []
    out = []
    for e in tl:
        typ = (e.get("imIncidentHistoryType") or {}).get("imIncidentHistoryTypeName") \
              or (f"type {e.get('imIncidentHistoryTypeId')}" if e.get("imIncidentHistoryTypeId") is not None else None)
        details = (e.get("details") or "").strip()
        if not details:
            if e.get("photo"):
                details = "Photo added"
            elif e.get("document"):
                doc = e["document"] or {}
                name = (doc.get("path") or "").split("/")[-1].split("?")[0]
                details = f"Document added: {urllib.parse.unquote(name)}" if name else "Document added"
            elif e.get("video"):
                details = "Video added"
            else:
                details = typ or "Activity"
        who = (e.get("createdByUser") or {}).get("fullName") or e.get("createdByName")
        when = e.get("dateCreated")
        out.append({
            "incident_id": incident_id,
            "source": "timeline",
            "history_type": typ,
            "detail": details[:4000],
            "changed_by": who,
            "changed_at": when,
            "detail_hash": hashlib.md5(f"{details}|{when}".encode()).hexdigest(),
        })
    return out


def sweep(incident_id=None):
    q = "clancy_dn_incidents?select=id,raw_api&raw_api=not.is.null&order=id"
    if incident_id:
        q += f"&id=eq.{incident_id}"
    incidents = rest(q)
    print(f"payloads to sweep: {len(incidents)}")
    total_new, total_seen = 0, 0
    batch = []
    for inc in incidents:
        rows = timeline_rows(inc["id"], inc["raw_api"])
        total_seen += len(rows)
        batch.extend(rows)
    # idempotent: on conflict on the unique key, do nothing
    new_rows = []
    for i in range(0, len(batch), 400):
        chunk = batch[i:i + 400]
        res = rest("clancy_dn_change_ledger?on_conflict=incident_id,source,detail_hash",
                   method="POST", body=chunk,
                   headers={"Prefer": "resolution=ignore-duplicates,return=representation"})
        new_rows.extend(res or [])
    total_new = len(new_rows)
    print(f"timeline entries seen: {total_seen}; new ledger rows: {total_new}")

    # RECORD THAT WE LOOKED, not just what we found (added 7 Aug 2026).
    #
    # `spotted_at` is stamped on a ledger row when a change is FIRST SEEN, so on a quiet day this
    # sweep runs, correctly finds nothing, and the newest spotted_at stays where it was. Anything
    # reading spotted_at as "when did we last look" therefore reports the mirror as stale forever
    # until Clancy happen to change something — which is exactly what clancy-dd-workflow.py step 2
    # and clancy-dd-recapture.py were doing. A gate that cries wolf on a quiet day gets ignored,
    # and this section already learned that lesson once.
    #
    # So the run is recorded separately. spotted_at keeps its own meaning, unchanged.
    if incident_id is None:                       # a full sweep only; a one-incident sweep is not
        try:                                      # evidence the whole register was looked at
            rest("cron_state?on_conflict=cron_key,item_key", "POST",
                 [{"cron_key": "clancy-dn-change-sweep", "item_key": "last_run",
                   "value": {"seen": total_seen, "new_rows": total_new},
                   "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}],
                 {"Prefer": "resolution=merge-duplicates,return=minimal"})
        except Exception as e:                    # never fail a good sweep on a bookkeeping write
            print(f"  (note: could not record the sweep time — {e})")
    if new_rows:
        # Pete's standing rule (3 Aug 2026): every NEW change found gets reported in chat.
        # This block IS the report source — the session relays it verbatim.
        print("\nNEW CHANGES FOUND — report these to Pete in chat:")
        for r in sorted(new_rows, key=lambda x: (x.get("changed_at") or ""), reverse=True)[:60]:
            when = (r.get("changed_at") or "?")[:16].replace("T", " ")
            who = r.get("changed_by") or "sheet import"
            typ = f" [{r['history_type']}]" if r.get("history_type") else ""
            print(f"  DAMAGE {r['incident_id']} · {when} · {who}{typ}: {(r.get('detail') or '')[:150]}")
        if total_new > 60:
            print(f"  ...and {total_new - 60} more (clancy-dn-change-sweep.py --report for the rest)")
    amended = rest("clancy_dn_change_ledger?select=id&history_type=eq.Incident%20Amended&limit=1",
                   headers={"Prefer": "count=exact"})
    return total_new


def report(since=None, amended_only=False):
    q = ("clancy_dn_change_ledger?select=incident_id,history_type,detail,changed_by,changed_at"
         "&order=changed_at.desc.nullslast&limit=200")
    if since:
        q += f"&changed_at=gte.{since}"
    if amended_only:
        q += "&history_type=eq.Incident%20Amended"
    rows = rest(q)
    if not rows:
        print("no ledger entries match.")
        return
    cur = None
    for r in rows:
        if r["incident_id"] != cur:
            cur = r["incident_id"]
            print(f"\nDAMAGE {cur}")
        when = (r["changed_at"] or "")[:16].replace("T", " ")
        who = r["changed_by"] or "?"
        typ = f" [{r['history_type']}]" if r.get("history_type") else ""
        print(f"  {when}  {who}{typ}: {r['detail'][:160]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incident", type=int, help="sweep one incident's stored payload")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--since", help="report filter, ISO date")
    ap.add_argument("--amended-only", action="store_true", help="report only typed edits")
    a = ap.parse_args()
    if a.report:
        report(a.since, a.amended_only)
    else:
        sweep(a.incident)
        if a.amended_only:
            report(a.since, True)


if __name__ == "__main__":
    main()
