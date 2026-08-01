#!/usr/bin/env python3
"""
seo-movement.py -- did a term actually MOVE, or is it inside its own noise?

Pete, 1 Aug 2026: "this is seo work, the key work is getting the pages to rank higher for key terms."
The only honest test of that work is whether a term moved. The trap is that positions bounce hard:
for the search term "cat and genny training", impression-weighted GSC daily positions across the
28 days to 30 Jul 2026 ranged 5.6 to 88.0. A term can look ten places better on noise alone.

So this REFUSES to call a movement real unless it clears the term's own observed volatility.

THE TEST, stated once so it cannot drift:
  noise band = the widest weekly (best..worst) spread the term showed across the compared weeks.
  A change in the impression-weighted position counts as REAL only when it is larger than half
  that band AND the term had impressions in both weeks. Otherwise it is NOISE, and says so.
  A term with fewer than 3 days of data in either week is INSUFFICIENT — never scored.

This is deliberately conservative. A test that calls noise a win is worse than no test: it makes
every future claim unfalsifiable, which is precisely the failure this whole evening was about.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/seo-movement.py [--property KEY] [--min-vol 20] [--term "..."]
"""
import os, sys, json, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PROP = "sygma-solutions-website"


def sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=120)
    if r.returncode != 0:
        raise SystemExit("cc-sql FAILED: " + (r.stderr or r.stdout)[:300])
    return json.loads(r.stdout or "[]")


def verdict(cur, prev):
    """(label, delta, band) — lower position is better, so delta>0 means improved."""
    if not cur or not prev:
        return ("NO PRIOR WEEK", None, None)
    if (cur["days_with_data"] or 0) < 3 or (prev["days_with_data"] or 0) < 3:
        return ("INSUFFICIENT DATA", None, None)
    if cur["wpos"] is None or prev["wpos"] is None:
        return ("INSUFFICIENT DATA", None, None)
    spreads = []
    for r in (cur, prev):
        if r["best_pos"] is not None and r["worst_pos"] is not None:
            spreads.append(float(r["worst_pos"]) - float(r["best_pos"]))
    band = max(spreads) if spreads else 0.0
    delta = float(prev["wpos"]) - float(cur["wpos"])          # + = better
    if abs(delta) <= band / 2:
        return ("NOISE", delta, band)
    return (("IMPROVED" if delta > 0 else "DECLINED"), delta, band)


def main():
    a = sys.argv[1:]
    prop = a[a.index("--property") + 1] if "--property" in a else PROP
    minvol = int(a[a.index("--min-vol") + 1]) if "--min-vol" in a else 20
    only = a[a.index("--term") + 1].strip().lower() if "--term" in a else None

    weeks = [r["w"] for r in sql(f"SELECT DISTINCT week_ending::text w FROM seo_term_weekly "
                                 f"WHERE property_key='{prop}' ORDER BY w DESC")]
    if len(weeks) < 2:
        raise SystemExit("need at least two weekly snapshots — run seo-week-snapshot.py")
    cw, pw = weeks[0], weeks[1]
    rows = sql(f"SELECT * FROM seo_term_weekly WHERE property_key='{prop}' "
               f"AND week_ending IN ('{cw}','{pw}')")
    by = {}
    for r in rows:
        by.setdefault(r["keyword"].lower(), {})[r["week_ending"]] = r

    print(f"\nMOVEMENT  ·  week ending {cw}  vs  {pw}  ·  {prop}")
    print("  A change counts as REAL only if it beats half the term's own weekly best-to-worst spread.")
    print(f"\n  {'term':<42}{'vol':>5}{'now':>7}{'was':>7}{'move':>7}{'band':>7}  verdict")
    out = []
    for k, d in by.items():
        cur, prev = d.get(cw), d.get(pw)
        base = cur or prev
        if only and k != only:
            continue
        if not only and (base.get("volume") or 0) < minvol:
            continue
        lab, delta, band = verdict(cur, prev)
        out.append((lab, delta or 0, base, cur, prev, band))
    order = {"IMPROVED": 0, "DECLINED": 1, "NOISE": 2, "INSUFFICIENT DATA": 3, "NO PRIOR WEEK": 4}
    out.sort(key=lambda x: (order.get(x[0], 9), -abs(x[1])))
    for lab, delta, base, cur, prev, band in out[:40]:
        now = cur["wpos"] if cur and cur["wpos"] is not None else "-"
        was = prev["wpos"] if prev and prev["wpos"] is not None else "-"
        mv = f"{delta:+.1f}" if lab in ("IMPROVED", "DECLINED", "NOISE") else "-"
        bd = f"{band:.1f}" if band is not None else "-"
        print(f"  {base['keyword'][:41]:<42}{base.get('volume') or 0:>5}{str(now):>7}{str(was):>7}"
              f"{mv:>7}{bd:>7}  {lab}")
    counts = {}
    for lab, *_ in out:
        counts[lab] = counts.get(lab, 0) + 1
    print("\n  " + " | ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("  'move' is the change in impression-weighted position (GSC, Google UK); + is better.")
    print("  'band' is the term's own weekly best-to-worst spread — the bar a real move must clear.")


if __name__ == "__main__":
    main()
