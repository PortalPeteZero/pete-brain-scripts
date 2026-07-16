#!/usr/bin/env python3
"""
clancy-damages-sync — ON-DEMAND reconcile of Clancy damage emails ↔ the CC Damage section
(`public.clancy_damages`). NOT a cron — run it when you want the damages table brought
current from the inbox.

Sweeps recent Clancy threads that look like a damage/strike/close-out, extracts what is
verifiable (subject, date, an 8-digit job ref, a location), and matches each against the
table. Reports what is already recorded vs what is NEW. With --apply it inserts the genuinely
new ones as THIN rows flagged "AWAITING DETAIL" (it never fabricates operatives/depth/cause —
those come from the actual close-out data when Clancy shares it).

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-damages-sync.py            # report only (default)
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-damages-sync.py --days 90  # widen the window
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-damages-sync.py --apply    # add the new thin rows
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
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    g = gmail()
    existing = _cc("GET", "clancy_damages?select=id,job_ref,town,location,damage_date")
    refs = {(r.get("job_ref") or "").lower() for r in existing}
    locwords = " ".join((r.get("town") or "") + " " + (r.get("location") or "") for r in existing).lower()

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
            jobref = (re.search(r"\b(\d{8})\b", subj) or [None, None])[1]
            # a rough location: words before a date or after 'at'
            loc = re.sub(r"(?i)(cable strike|close ?out|service (strike|damage)|strike|damage|-|\d{1,2}/\d{1,2}/\d{2,4}|fw:|re:|fwd:)", " ", subj)
            loc = re.sub(r"\s+", " ", loc).strip(" -,·")
            known = (jobref and jobref.lower() in refs) or (loc and len(loc) > 4 and loc.lower() in locwords)
            candidates.append({"subject": subj[:70], "date": hdr.get("date", "")[:16], "jobref": jobref, "loc": loc, "known": bool(known)})

    new = [c for c in candidates if not c["known"]]
    print(f"clancy-damages-sync — {len(candidates)} damage-shaped Clancy threads in {args.days}d · "
          f"{len(existing)} incidents in table\n")
    print("ALREADY RECORDED:")
    for c in candidates:
        if c["known"]:
            print(f"  ✓ {c['date']}  {c['subject']}")
    print("\nNEW (not matched to a table row):")
    for c in new:
        print(f"  + {c['date']}  {c['subject']}  [ref={c['jobref'] or '—'} loc='{c['loc']}']")
    if not new:
        print("  (none — table is current with the inbox)")

    if args.apply and new:
        added = 0
        # --apply only auto-adds incidents that carry a job ref (safe); location-only candidates
        # are surfaced for a manual add so we never create junk rows from discussion threads.
        applicable = [c for c in new if c["jobref"]]
        skipped = [c for c in new if not c["jobref"]]
        for c in applicable:
            row = {"customer": "Clancy", "job_ref": c["jobref"] or ("email-" + re.sub(r"[^a-z0-9]+", "-", c["loc"].lower())[:30] or "unknown"),
                   "location": c["loc"] or c["subject"], "status": "From inbox — awaiting detail",
                   "summary": f"Auto-captured from Clancy email '{c['subject']}' ({c['date']}). AWAITING DETAIL — no CAT data / operatives / depth yet.",
                   "next_actions": ["Get the CAT/strike data from Clancy", "Decide if it warrants a Sygma data review"]}
            if row["job_ref"].lower() in refs:
                continue
            _cc("POST", "clancy_damages", row, prefer="return=minimal")
            refs.add(row["job_ref"].lower()); added += 1
            print(f"  added: {row['job_ref']}")
        print(f"\napplied: {added} new thin row(s) (job-ref incidents) — enrich once Clancy shares the data.")
        if skipped:
            print(f"NOT auto-added ({len(skipped)} location-only, no job ref — add manually if a real incident):")
            for c in skipped:
                print(f"  · {c['date']}  {c['subject']}")
    elif new:
        print("\n(run with --apply to add the new ones as thin rows flagged awaiting-detail)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
