#!/usr/bin/env python3
"""
seo-volume-refresh.py -- THE one place a search volume comes from.

WHY THIS EXISTS (2 Aug 2026). Pete, repeatedly: "every time I ask about anything to do with this I
get a different number." For demand figures he was exactly right, and it was a bug, not judgement:

  `seo_keyword_map.priority` is a HAND-SET RANKING FIELD. Four separate tools read it and printed it
  as "monthly searches" -- seo-term.py, seo-movement.py, seo-section-report.py and (until 1 Aug)
  seo-week-snapshot.py. Confirmed 1 Aug: 482 of 482 seo_term_weekly rows had priority == volume.
  The value was set once when the map was built and never refreshed, so it could only drift. A live
  pull of all 482 mapped keywords found 268 out by more than 25%, with total mapped demand
  overstated by 67% (9,707 stored vs 5,804 live).

Fixing one tool on 1 Aug and leaving its three siblings is what made it recur the next morning.
So volume now lives ON THE MAP, which is the SSOT for what Sygma targets, in its own column, with
the timestamp of when it was checked. Every tool reads `seo_keyword_map.volume`. Nothing reads
`priority` for demand ever again -- priority is a ranking field and means what it says.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/seo-volume-refresh.py [--property KEY] [--apply] [--max-age-days 30]

  no --apply  : report drift, write nothing
  --apply     : pull live Ahrefs GB volume for every mapped keyword and write it to the map

Library:
  from seo_volume import volumes_for(prop)     # {keyword: volume}, raises if the map is unrefreshed
"""
import os, sys, json, subprocess, importlib.util, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")
DEFAULT_PROP = "sygma-solutions-website"
DRIFT = 0.25          # report anything more than 25% out
MIN_ANSWERED = 0.80   # refuse to write a partial refresh


def sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=240)
    if r.returncode != 0:
        raise SystemExit("cc-sql FAILED: " + (r.stderr or r.stdout)[:300])
    return json.loads(r.stdout or "[]")


def q(s):
    return "$x$" + (s or "") + "$x$"


def live_volumes(keywords, caller="seo-volume-refresh"):
    """Live Ahrefs GB monthly volume. Raises rather than guessing or falling back."""
    spec = importlib.util.spec_from_file_location("ahrefs", f"{VAULT}/ahrefs-api.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    api = m.AhrefsAPI(caller=caller)
    out = {}
    for i in range(0, len(keywords), 100):
        for row in api.keywords_overview(keywords[i:i + 100], country="gb"):
            out[row["keyword"]] = row.get("volume_monthly")
    answered = sum(1 for v in out.values() if v is not None)
    if answered < len(keywords) * MIN_ANSWERED:
        raise SystemExit(
            f"FAIL: Ahrefs answered for only {answered}/{len(keywords)} keywords. Refusing to write -- "
            f"a partial refresh silently reinstates the stale numbers this tool exists to remove.")
    return out, answered


def volumes_for(prop=DEFAULT_PROP, max_age_days=45):
    """THE accessor every report must use. Never falls back to priority.

    Returns {keyword: volume|None}. Raises if the map has never been refreshed, so a report cannot
    quietly print stale demand -- which is the whole failure this module exists to prevent."""
    rows = sql(f"SELECT keyword, volume, volume_checked_at FROM seo_keyword_map "
               f"WHERE property_key={q(prop)}")
    if not rows:
        raise SystemExit(f"no mapped keywords for {prop}")
    checked = [r["volume_checked_at"] for r in rows if r["volume_checked_at"]]
    if not checked:
        raise SystemExit(
            f"REFUSING to report demand for {prop}: seo_keyword_map.volume has never been refreshed.\n"
            f"  Run: VAULT={VAULT} python3 {VAULT}/seo-volume-refresh.py --apply")
    newest = max(checked)[:10]
    age = (datetime.date.today() - datetime.date.fromisoformat(newest)).days
    if age > max_age_days:
        print(f"  ⚠ volumes last refreshed {newest} ({age} days ago) -- re-run seo-volume-refresh.py --apply",
              file=sys.stderr)
    return {r["keyword"]: r["volume"] for r in rows}


def main():
    a = sys.argv[1:]
    prop = a[a.index("--property") + 1] if "--property" in a else DEFAULT_PROP
    apply_ = "--apply" in a

    mapped = [r["keyword"] for r in sql(
        f"SELECT keyword FROM seo_keyword_map WHERE property_key={q(prop)} ORDER BY keyword")]
    cur = {r["keyword"]: r["volume"] for r in sql(
        f"SELECT keyword, volume FROM seo_keyword_map WHERE property_key={q(prop)}")}
    prio = {r["keyword"]: r["priority"] for r in sql(
        f"SELECT keyword, priority FROM seo_keyword_map WHERE property_key={q(prop)}")}
    print(f"{prop}: {len(mapped)} mapped keywords")

    live, answered = live_volumes(mapped)
    print(f"Ahrefs answered for {answered}/{len(mapped)}")

    drift = [(k, cur.get(k), live.get(k)) for k in mapped
             if cur.get(k) is not None and live.get(k) is not None
             and abs(cur[k] - live[k]) / max(cur[k], live[k], 1) > DRIFT]
    same_as_priority = sum(1 for k in mapped if cur.get(k) is not None and cur[k] == prio.get(k))
    print(f"drift >{int(DRIFT*100)}% vs what the map currently holds: {len(drift)}")
    print(f"map rows whose volume still equals priority (the old bug's signature): {same_as_priority}")
    for k, s, l in sorted(drift, key=lambda r: -max(r[1], r[2]))[:15]:
        print(f"   {k:<40}{s:>7} -> {l:>6}")

    if not apply_:
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        return

    vals = [(k, v) for k, v in live.items() if v is not None]
    for i in range(0, len(vals), 200):
        chunk = vals[i:i + 200]
        cases = " ".join(f"WHEN {q(k)} THEN {v}" for k, v in chunk)
        keys = ",".join(q(k) for k, _ in chunk)
        sql(f"UPDATE seo_keyword_map SET volume = CASE keyword {cases} END, volume_checked_at = now() "
            f"WHERE property_key={q(prop)} AND keyword IN ({keys})")
    print(f"\nwrote {len(vals)} live volumes to seo_keyword_map")

    chk = sql(f"SELECT count(*) n, count(volume) withvol, max(volume_checked_at)::date d, "
              f"sum(volume) total FROM seo_keyword_map WHERE property_key={q(prop)}")[0]
    print(f"verify: {chk['withvol']}/{chk['n']} rows carry a volume · total {chk['total']}/mo · "
          f"checked {chk['d']}")


if __name__ == "__main__":
    main()
