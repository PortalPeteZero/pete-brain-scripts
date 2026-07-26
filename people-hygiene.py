#!/usr/bin/env python3
# CRON-META
# what: Report-only people-store hygiene check — probable duplicates (shared phone/email, or a bare first name that is a subset of a fuller record) and half-finished records (no email AND no phone)
# why: cc-locator-audit reconciles THINGS against data_map; it cannot see a PROCESS that was skipped. Skipping the people system leaves no unhomed object — but it does leave a trace in the data: duplicates and half-finished records. This is that trace, measured.
# reads: public.google_contacts (the CC mirror of Google Contacts)
# writes: its own report line to daily_log (cron_name='people-hygiene'); NO domain data, ever
# entity: PA-Command-Centre
# report: stdout
# secrets: none beyond the CC keys
# schedule: MANUAL — not deployed. Pete decides whether this earns a cron.
# timezone: Atlantic/Canary
# CRON-META-END
"""people-hygiene.py — is the people system actually being followed?

Built 26 Jul 2026 after Pete asked "i thought we built cc locator to stop you ignoring [systems]".
It doesn't, and can't: the locator reconciles things against `data_map`, so a skipped PROCESS is
invisible to it — nothing goes unhomed when a tool simply isn't used.

The evidence of a skipped people process is in the DATA, and that is what this measures:

  (a) SHARED CONTACT POINT — two records carrying the same email or the same phone. Either a
      duplicate, or (per the whois rule) an organisation number that must never be merged.
  (b) SUBSET NAME — a bare "Freya" sitting alongside "Freya Finch". This is the exact 26 Jul 2026
      failure: the part-name record was invisible to an exact-name search, so a second record got
      created on top of it.
  (c) HALF-FINISHED — a record with neither an email nor a phone, so it cannot actually be used.
      Pete's touch-it-tidy-it rule exists to burn these down as they are encountered.

REPORT-ONLY, the house pattern (like connection-parity.py / cc-locator-audit.py): prints, records
to daily_log, mutates nothing, and exits 0 whenever it RAN. Finding drift is this tool working.

Usage:  VAULT=/tmp/pbs python3 /tmp/pbs/people-hygiene.py [--json]
        exit 0 = it ran (whatever it found).  exit 99 = it could NOT check, which is itself a gap.
"""
import os, sys, json, re, subprocess, collections

VAULT = os.environ.get("VAULT", "/tmp/pbs")
AS_JSON = "--json" in sys.argv


def sql(q):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", q], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr)[:300])
    out = r.stdout.strip()
    return json.loads(out) if out.startswith("[") else []


def norm_phone(p):
    d = re.sub(r"\D", "", p or "")
    return d[-9:] if len(d) >= 9 else ""


def main():
    try:
        rows = sql("SELECT resource_name, display_name, emails, phones FROM google_contacts")
    except Exception as e:
        msg = f"PEOPLE HYGIENE — ABORTED, could not read google_contacts: {e}"
        print(msg)
        if AS_JSON:
            print(json.dumps({"aborted": True, "gaps": 1, "error": str(e)[:200]}))
        return 99

    by_email, by_phone = collections.defaultdict(list), collections.defaultdict(list)
    names = {}
    half = []
    self_dupes = []
    for r in rows:
        nm = (r.get("display_name") or "").strip()
        names[r["resource_name"]] = nm
        # DEDUPE WITHIN THE RECORD FIRST. Without this, one contact listing its own number or
        # address twice looked like two contacts sharing it — which is how this tool reported
        # "28 shared emails" and "24 shared numbers" on 26 Jul 2026 when the true cross-record
        # figures were 0 and 5. A tool that over-reports is worse than no tool: Pete was about to
        # go and merge contacts that were never duplicates.
        emails = sorted({e.strip().lower() for e in (r.get("emails") or []) if e and e.strip()})
        phones = sorted({norm_phone(p) for p in (r.get("phones") or []) if norm_phone(p)})
        raw_p = [norm_phone(p) for p in (r.get("phones") or []) if norm_phone(p)]
        raw_e = [(e or "").strip().lower() for e in (r.get("emails") or []) if e and e.strip()]
        if len(raw_p) != len(phones) or len(raw_e) != len(emails):
            self_dupes.append(nm or r["resource_name"])
        for e in emails:
            by_email[e].append(r)
        for p in phones:
            by_phone[p].append(r)
        if nm and not emails and not any(phones):
            half.append(nm)

    # count DISTINCT records, never repeated entries on one record
    shared_email = {k: v for k, v in by_email.items()
                    if len({x["resource_name"] for x in v}) > 1}
    shared_phone = {k: v for k, v in by_phone.items()
                    if len({x["resource_name"] for x in v}) > 1}

    # subset names: a bare "Freya" alongside "Freya Finch"
    tok = {rn: {t for t in re.split(r"[\s,.]+", n.lower()) if len(t) > 1}
           for rn, n in names.items() if n}
    subsets = []
    bare = [(rn, t) for rn, t in tok.items() if len(t) == 1]
    for rn, t in bare:
        for rn2, t2 in tok.items():
            if rn2 != rn and len(t2) > 1 and t < t2:
                subsets.append((names[rn], names[rn2]))
    gaps = len(shared_email) + len(shared_phone) + len(subsets)

    lines = [f"PEOPLE HYGIENE — {len(rows)} contact records checked"]
    lines.append(f"  probable duplicates: {len(shared_email)} shared email(s), "
                 f"{len(shared_phone)} shared number(s), {len(subsets)} part-name overlap(s)")
    lines.append(f"  half-finished (no email AND no phone): {len(half)}")
    lines.append(f"  records repeating their OWN number/address (untidy, NOT a duplicate person): "
                 f"{len(self_dupes)}")
    for e, v in list(shared_email.items())[:8]:
        lines.append(f"    ✉ {e} -> " + " | ".join(names[x['resource_name']] for x in v))
    for p, v in list(shared_phone.items())[:8]:
        lines.append(f"    ☎ ...{p} -> " + " | ".join(names[x['resource_name']] for x in v)
                     + "   (a SHARED number means same ORGANISATION — never merge)")
    for a, b in subsets[:8]:
        lines.append(f"    ~ '{a}' may be the same person as '{b}' — tidy, do not duplicate")
    if gaps == 0 and not half:
        lines.append("  clean — no duplicates and nothing half-finished.")
    lines.append("  Fix with: people-api.py update <resource> name|email|phone VALUE   "
                 "(touch it, tidy it — Pete's rule)")

    report = "\n".join(lines)
    print(report)
    try:
        sql("INSERT INTO daily_log (date, cron_name, content) VALUES "
            "(current_date, 'people-hygiene', $r$" + report + "$r$)")
    except Exception as e:
        print(f"  ⚠ could not record to daily_log: {str(e)[:120]}")
    if AS_JSON:
        print(json.dumps({"records": len(rows), "gaps": gaps, "half_finished": len(half)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
