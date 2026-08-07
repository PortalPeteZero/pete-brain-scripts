#!/usr/bin/env python3
"""clancy-dd-recapture.py — on connecting to Depotnet, what do we need to fetch?

Pete, 5 Aug 2026: "there's a difference between capturing the initial investigation and then the
recapture... the recapture might happen three or four times. Every time we connect up on it, we
have to have a look: has anything been added, has anything been updated? ... it's taking you two
days to recapture two records."

It took two days because working that out was done by hand every time. It does not need to be.

THE TWO KINDS OF CAPTURE
  INITIAL CAPTURE  the first time a damage is pulled. One-off. Everything comes across.
  RECAPTURE        every reconnect after that. A Clancy damage is a LIVING record: the
                   investigation is filled in over days, the report is submitted and then amended,
                   photographs and documents keep arriving, actions are raised and closed. Damage
                   152586 was amended twice in one afternoon; 153523's report was submitted four
                   days after the strike. A capture is a snapshot of a moving thing.

HOW STALENESS IS DECIDED — and why it needs no Depotnet call
  Depotnet exposes no last-modified at list level (verified 3 Aug 2026 against
  GetImIncidentRegisterGrid), so there is nothing to poll. But its per-incident TIMELINE is an
  audit trail, and clancy-dn-change-sweep.py already mirrors it into clancy_dn_change_ledger.

  So: our copy of a damage is STALE when the ledger holds an event dated after the moment we last
  captured it (`raw_api_at`). That is a comparison between two columns we already have. This tool
  is a query, not a scrape — it runs before you sign in, and tells you exactly what to go and get.

WHAT IT REPORTS, in the order you should work
  1 NEVER CAPTURED      no raw payload at all.
  2 CHANGED SINCE       ledger events after our capture — listed verbatim, because a change to a
                        closed record is exactly what Pete wants surfaced, never silently banked.
  3 INVESTIGATION OPEN  captured, but Depotnet's own "is the investigation complete" is not yes,
                        or required answers are blank. These WILL change again; they are the ones
                        worth re-checking on every connect.
  4 RECENT              raised inside --days (default 30), the standing "last month's worth" sweep.

  VAULT=/tmp/pbs python3 clancy-dd-recapture.py                  # the work list for FY26/27
  VAULT=/tmp/pbs python3 clancy-dd-recapture.py --days 14
  VAULT=/tmp/pbs python3 clancy-dd-recapture.py --all-years --json

Exit 0 = nothing to fetch. Exit 1 = there is work (the list is the work). Exit 2 = could not run.
"""
import argparse, json, os, sys, datetime, urllib.request, urllib.error, urllib.parse
from collections import defaultdict

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_K = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _K["url"], _K["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}"}

# An upload is an ADDITION and is already listed with its date in the evidence section; an
# amendment or a deletion is a CHANGE to something we have already read and reported on. Both
# mean our snapshot is behind, but the second is the one that can silently invalidate a finding.
MATERIAL = {"Incident Amended", "Report Amended", "Report Submitted", "Question Response Updated",
            "Action Amended", "Action Closed", "Action Created", "Photo Deleted",
            "Document Deleted"}


def get(path):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(URL + "/rest/v1/" + path, headers=H), timeout=240).read().decode())


def ts(v):
    return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def plan(fy, days):
    fyq = urllib.parse.quote(fy, safe="")
    inc = get(f"clancy_dn_incidents?select=id,location,incident_date,raw_api_at,capture_incident,"
              f"capture_actions,report_submitted_at&fy=eq.{fyq}&order=id.asc&limit=4000")
    if not inc:
        return None
    ids = [r["id"] for r in inc]
    inlist = "in.(" + ",".join(str(i) for i in ids) + ")"
    led, last = [], None
    while True:                                    # keyset on the ledger's unique id
        q = (f"clancy_dn_change_ledger?select=id,incident_id,history_type,changed_at,changed_by,"
             f"detail&incident_id={inlist}&order=id.asc&limit=1000")
        if last is not None:
            q += f"&id=gt.{last}"
        b = get(q)
        if not b:
            break
        led += b
        last = b[-1]["id"]
        if len(b) < 1000:
            break

    by = defaultdict(list)
    for e in led:
        by[e["incident_id"]].append(e)

    # THE LEDGER IS A MIRROR. It can only tell you about changes it has actually LOOKED for, and
    # clancy-dn-change-sweep.py is what makes it look. If the sweep has not run since our newest
    # capture, an empty "changed since" list means the mirror never looked, not that Depotnet has
    # been quiet — and this tool would hand back a work list saying there is nothing to fetch.
    # Caught 5 Aug 2026 with the sweep 25.5h stale while Clancy were amending a damage that hour.
    swept = get("clancy_dn_change_ledger?select=spotted_at&order=spotted_at.desc&limit=1")
    swept_at = ts(swept[0]["spotted_at"]) if swept else None
    newest_cap = max([ts(r.get("raw_api_at")) for r in inc if r.get("raw_api_at")], default=None)
    stale = None
    if not swept_at:
        stale = "the change ledger is EMPTY - nothing here can be trusted"
    elif newest_cap and swept_at < newest_cap:
        stale = (f"the change sweep last ran {swept_at.isoformat()[:16]}, BEFORE our newest capture "
                 f"at {newest_cap.isoformat()[:16]}. It has not looked since, so section 2 below "
                 f"is NOT a clean bill of health. Run clancy-dn-change-sweep.py first.")
    elif (now := datetime.datetime.now(datetime.timezone.utc)) and \
            (now - swept_at).total_seconds() / 3600 > 6:
        stale = (f"the change sweep last ran {round((now - swept_at).total_seconds()/3600,1)}h ago. "
                 f"Anything Clancy has done since is invisible here.")
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)

    never, changed, open_inv, recent = [], [], [], []
    for r in inc:
        cap = ts(r.get("raw_api_at"))
        loc = (r.get("location") or "")[:34]
        if not cap:
            never.append({"id": r["id"], "loc": loc, "why": "no raw payload captured"})
            continue
        after = [e for e in by[r["id"]]
                 if ts(e["changed_at"]) and ts(e["changed_at"]) > cap]
        material = [e for e in after if e["history_type"] in MATERIAL]
        if after:
            changed.append({
                "id": r["id"], "loc": loc, "captured": cap.isoformat()[:16],
                "events": len(after), "material": len(material),
                "detail": [f"{ts(e['changed_at']).isoformat()[:16]}  {e['history_type']}"
                           + (f" ({e['changed_by']})" if e.get("changed_by") else "")
                           + (f"  {e['detail'][:70]}" if e.get("detail") else "")
                           for e in sorted(after, key=lambda x: x["changed_at"], reverse=True)[:8]],
            })
        if r.get("capture_incident") != "full":
            open_inv.append({"id": r["id"], "loc": loc,
                             "why": f"capture_incident={r.get('capture_incident')}"})
        d = ts(r.get("incident_date"))
        if d and d > cutoff:
            recent.append({"id": r["id"], "loc": loc, "raised": d.isoformat()[:10]})
    return {"fy": fy, "damages": len(inc), "never": never, "changed": changed,
            "investigation_open": open_inv, "recent": recent, "stale": stale}


# The browser half, printed with the work list so nobody re-derives it. Worked out 7 Aug 2026 on
# damage 153523; the fetch cannot be scripted because the token lives encrypted in the app.
HOW = """
  ---- HOW TO FETCH ONE (Pete's signed-in Chrome, on the incident page) ---------------------
  Open  https://clancy.depotnet.co.uk/#/incidentmanager/imincident/<ID>  then location.reload()
  and CONFIRM the header reads "Incident #<ID>" — the app is hash-routed, so changing the URL
  alone does NOT change the record on screen.

  Do NOT try to read the bearer token: it is encrypted in localStorage (secure-ls), and using it
  raises "String contains non ISO-8859-1 code point" because you are sending ciphertext. Capture
  the app's OWN response instead. Paste this, then the navigation below it:

  window.__cap={};
  const XO=XMLHttpRequest.prototype.open, XS=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(m,u){this.__u=u;return XO.apply(this,arguments);};
  XMLHttpRequest.prototype.send=function(){this.addEventListener('load',function(){
    if(this.__u&&/GetImIncident\\b/i.test(this.__u)&&this.responseText)window.__cap.payload=this.responseText;});
    return XS.apply(this,arguments);};
  const of_=window.fetch; window.fetch=async function(...a){const r=await of_.apply(this,a);
    const u=(typeof a[0]==='string'?a[0]:a[0].url)||'';
    if(/GetImIncident\\b/i.test(u))window.__cap.payload=await r.clone().text(); return r;};

  // make the app re-fetch WITHOUT a reload (a reload wipes the hooks above)
  location.hash='#/incidentmanager/incidentregister'; await new Promise(r=>setTimeout(r,3000));
  location.hash='#/incidentmanager/imincident/<ID>';  await new Promise(r=>setTimeout(r,6000));

  // save it
  const b=new Blob([window.__cap.payload],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(b);
  a.download='dnapi__<ID>.json'; document.body.appendChild(a); a.click(); a.remove();

  Then, ON DISK — Chrome does NOT overwrite, so a second capture lands as 'dnapi__<ID> (1).json':
      ls -lt ~/Downloads/dnapi__<ID>*          <-- take the NEWEST BY TIMESTAMP
      VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-ingest.py <that file>
  The ingest refuses a payload older than the record it would overwrite, so a stale file is
  caught rather than silently banked. Finish with clancy-dn-change-sweep.py, then
  clancy-dd-workflow.py.
  ------------------------------------------------------------------------------------------"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fy", default="FY26/27")
    ap.add_argument("--all-years", action="store_true")
    ap.add_argument("--days", type=int, default=30, help="the recent-sweep window (default 30)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    years = ["FY26/27", "FY25/26"] if a.all_years else [a.fy]

    try:
        plans = [p for p in (plan(fy, a.days) for fy in years) if p]
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"clancy-dd-recapture: COULD NOT RUN — {e}\n  This is NOT 'nothing to do'.",
              file=sys.stderr)
        sys.exit(2)

    work = any(p["never"] or p["changed"] or p["investigation_open"] or p.get("stale")
               for p in plans)
    if a.json:
        print(json.dumps({"work": work, "plans": plans}, indent=1))
        sys.exit(1 if work else 0)

    for p in plans:
        print(f"\n=== RECAPTURE PLAN — {p['fy']} ({p['damages']} damages) ===")
        if p.get("stale"):
            print(f"\n  !! MIRROR STALE — {p['stale']}")
        if p["never"]:
            print(f"\n  1 NEVER CAPTURED — {len(p['never'])}")
            for x in p["never"]:
                print(f"      {x['id']}  {x['loc']:<34} {x['why']}")
        if p["changed"]:
            print(f"\n  2 CHANGED SINCE WE CAPTURED — {len(p['changed'])}"
                  f"  (our copy is behind Depotnet)")
            for x in sorted(p["changed"], key=lambda y: -y["material"]):
                print(f"      {x['id']}  {x['loc']:<34} captured {x['captured']}, "
                      f"{x['events']} event(s) since, {x['material']} material")
                for d in x["detail"]:
                    print(f"           {d}")
        if p["investigation_open"]:
            print(f"\n  3 INVESTIGATION NOT COMPLETE — {len(p['investigation_open'])}"
                  f"  (these will change again; re-check every connect)")
            for x in p["investigation_open"][:30]:
                print(f"      {x['id']}  {x['loc']:<34} {x['why']}")
            if len(p["investigation_open"]) > 30:
                print(f"      ... and {len(p['investigation_open']) - 30} more")
        if p["recent"]:
            print(f"\n  4 RAISED IN THE LAST {a.days} DAYS — {len(p['recent'])}"
                  f"  (the standing sweep)")
            for x in p["recent"]:
                print(f"      {x['id']}  {x['loc']:<34} raised {x['raised']}")
        if not (p["never"] or p["changed"] or p["investigation_open"]):
            print("  nothing to recapture — every damage is current with Depotnet's audit trail.")
    print("\nEvery change listed above is reported to Pete verbatim, never silently banked.")
    if work:
        print(HOW)
    sys.exit(1 if work else 0)


if __name__ == "__main__":
    main()
