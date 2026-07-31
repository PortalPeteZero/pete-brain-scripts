#!/usr/bin/env python3
"""
clancy-damages-sync — ON-DEMAND reconcile of Clancy damage EMAILS against the Depotnet register.
NOT a cron. Read-only.

Sweeps recent Clancy threads that look like a damage/strike/close-out, extracts what is verifiable
(subject, date, an 8-digit job ref, a location), and matches each against public.clancy_dn_incidents
plus public.clancy_unmapped_damages. It tells you which damages the inbox is talking about that the
Depotnet register does not hold — which is a finding worth having, because a damage discussed by
email and never logged on Depotnet is exactly the gap this partnership is about.

IT NO LONGER WRITES. Until 31 Jul 2026 --apply inserted thin rows into `clancy_damages`, a Sygma
register that ran in parallel to Depotnet. That table has been retired: its records were merged
onto clancy_dn_incidents, and the SIX that turned out to have no Depotnet counterpart at all had
to be moved to clancy_unmapped_damages because there was nowhere else for them to go. Hand-created
damage rows are how that happened. Damages come from the Depotnet import; this tool reports the
discrepancy and a human decides what to do about it.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-damages-sync.py            # report
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-damages-sync.py --days 90  # widen the window
"""
import sys, os, re, json, argparse, importlib.util, urllib.request

KEYS = json.load(open(os.path.expanduser("~/.config/pete-secrets/command-centre-supabase-keys.json")))
SRK = KEYS["service_role_key"]; U = KEYS["url"] + "/rest/v1"
H = {"apikey": SRK, "Authorization": f"Bearer {SRK}", "Content-Type": "application/json"}


def _cc(method, path, body=None, prefer=None):
    h = dict(H)
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(f"{U}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers=h)
    txt = urllib.request.urlopen(req, timeout=30).read()
    return json.loads(txt) if txt else None


def gmail():
    spec = importlib.util.spec_from_file_location("gmail_api", os.path.join(os.environ.get("VAULT", "/tmp/pbs"), "gmail-api.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.GmailAPI()


# a Clancy thread is "damage-shaped" if its subject carries one of these + is not admin noise
DAMAGE_RE = re.compile(r"(?i)\b(strike|damage|close ?out|cable strike|service (strike|damage)|utility damage)\b")
# admin/calendar noise + non-incident chatter (events, meetings, our own outbound, general discussion)
NOISE_RE = re.compile(r"(?i)(^(out of office|automatic reply|accepted:|declined:|tentative:|canceled:|cancelled:))"
                      r"|(community event|refresh project|zero strike|mentioned today|conf call|"
                      r"damage support|damage prevention|panel review$|data review ahead|monthly review|"
                      r"strategy board|board meeting|training|competency|previous panel reviews)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--apply", action="store_true",
                    help="retired — this tool no longer writes; see the module docstring")
    args = ap.parse_args()

    g = gmail()
    # Match against the Depotnet register AND the unmapped store, so a damage we already know
    # about in either place is not reported as new.
    existing = _cc("GET", "clancy_dn_incidents?select=id,job_ref,location,incident_date")
    unmapped = _cc("GET", "clancy_unmapped_damages?select=id,job_ref,town,location,damage_date")
    refs = {(r.get("job_ref") or "").lower() for r in existing + unmapped}
    refs |= {str(r.get("id")) for r in existing}          # the Depotnet number is a ref too
    locwords = " ".join((r.get("location") or "") for r in existing).lower() + " " + \
               " ".join((r.get("town") or "") + " " + (r.get("location") or "") for r in unmapped).lower()

    seen, candidates = set(), []
    for q in (f'from:theclancygroup.co.uk newer_than:{args.days}d',
              f'to:theclancygroup.co.uk newer_than:{args.days}d'):
        for t in g.search_threads(q, max_results=40):
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            th = g.get_thread(t["id"])
            m = th["messages"][0]
            hdr = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
            subj = hdr.get("subject", "")
            if NOISE_RE.search(subj) or not DAMAGE_RE.search(subj):
                continue
            # Two ref shapes matter: Clancy's 8-digit job number and Depotnet's own 5-6 digit
            # incident id. Matching only the 8-digit one reported damages as "new" that were
            # sitting on the register under the number quoted in the subject line.
            jobref = (re.search(r"\b(\d{8})\b", subj) or [None, None])[1]
            dnid = None
            for cand in re.findall(r"\b(\d{5,6})\b", subj):
                if cand in refs:
                    dnid = cand
                    break
            # a rough location: words before a date or after 'at'
            loc = re.sub(r"(?i)(cable strike|close ?out|service (strike|damage)|strike|damage|-|\d{1,2}/\d{1,2}/\d{2,4}|fw:|re:|fwd:)", " ", subj)
            loc = re.sub(r"\s+", " ", loc).strip(" -,·")
            known = bool(dnid) or (jobref and jobref.lower() in refs) \
                or (loc and len(loc) > 4 and loc.lower() in locwords)
            candidates.append({"subject": subj[:70], "date": hdr.get("date", "")[:16],
                               "jobref": jobref or dnid, "loc": loc, "known": bool(known)})

    new = [c for c in candidates if not c["known"]]
    print(f"clancy-damages-sync — {len(candidates)} damage-shaped Clancy threads in {args.days}d · "
          f"{len(existing)} damages on the Depotnet register, {len(unmapped)} unmapped\n")
    print("ALREADY RECORDED:")
    for c in candidates:
        if c["known"]:
            print(f"  ✓ {c['date']}  {c['subject']}")
    print("\nNEW (not matched to a table row):")
    for c in new:
        print(f"  + {c['date']}  {c['subject']}  [ref={c['jobref'] or '—'} loc='{c['loc']}']")
    if not new:
        print("  (none — the register is current with the inbox)")
    print("\nRead this as a prompt, not a verdict: matching is by reference number and by an exact")
    print("location string, so a thread that writes an address differently from Depotnet will show")
    print("as new when it is not. Check each one before concluding a damage went unlogged.")

    if args.apply:
        print("\n--apply is retired. This tool does not create damage records any more.")
        print("A damage reaches the register by being captured from Depotnet — run:")
        print("  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-capture.py")
        print("If a thread above is a real damage with NO Depotnet record, that is the finding:")
        print("raise it with Clancy rather than inventing a row for it here.")


if __name__ == "__main__":
    sys.exit(main())
