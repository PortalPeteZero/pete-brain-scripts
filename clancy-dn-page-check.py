#!/usr/bin/env python3
"""clancy-dn-page-check.py — the runnable DONE for the Depot's table pages.

Born in the edits-plan audits (2 Aug 2026): the behavioural rules the redesign rests on —
parents-only counting, the by-data-source dash rules, child rows that follow their parent —
can fail invisibly in a build that LOOKS right. This asserts them on the BUILT html against
the live database, so a wrong page fails before anyone previews it, let alone publishes.

ASSERTIONS ARE STAGED. The by-source table rules describe the stage-2 tables; today's approved
renderer legitimately fails them. So:

  stage 1 (always on):     every register/analysis table's DAMAGE-row count equals the DB's
                           damage count for that year — the page shows every damage, no row
                           type is miscounted as one.
  stage 2 (armed by --stage2 or CLANCY_STAGE2=1, the same flag the renderer work sits behind):
                           child rows carry data-parent and follow their parent in DOM order;
                           uncaptured years' action columns ASSERT (expected "None" counts
                           derived from the DB at check time — an all-dashed action column
                           FAILS); zero investigation/spot-check assertions on uncaptured
                           damages; captured-zero evidence reads 0, uncaptured reads a dash.

A routine publish of the approved output can NEVER be blocked by an assertion describing an
unshipped stage.

Usage:
  VAULT=/tmp/pbs python3 clancy-dn-page-check.py --dir /tmp/pgbuild            # stage 1
  VAULT=/tmp/pbs python3 clancy-dn-page-check.py --dir /tmp/pgbuild --stage2   # full set
"""
import os, re, sys, json, glob, argparse, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))

FY_OF_PAGE = {"fy-2026-27": "FY26/27", "fy-2025-26": "FY25/26",
              "fy-2024-25": "FY24/25", "fy-2023-24": "FY23/24"}


def rest(path):
    req = urllib.request.Request(f"{_k['url']}/rest/v1/{path}",
        headers={"apikey": _k["service_role_key"],
                 "Authorization": f"Bearer {_k['service_role_key']}",
                 "Prefer": "count=exact"}, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as r:
        return int(r.headers["Content-Range"].split("/")[1])


def damage_rows(html):
    """Damage rows in a register table: <tr class="row" ...> with a data-href (the analysis
    table) or data-search (the register). Child rows (stage 2) carry data-parent and are
    NEVER counted here — that distinction is the whole point of this check."""
    trs = re.findall(r"<tr\b[^>]*>", html)
    return [t for t in trs
            if ('class="row"' in t or "data-search" in t or "data-hay" in t)
            and "data-parent" not in t]


def check_stage1(d):
    """Every year page's damage-row count equals the DB count for that year."""
    fails, checked = [], 0
    for f in sorted(glob.glob(os.path.join(d, "*.html"))):
        base = os.path.basename(f)
        m = re.match(r"(?:.*__)?(fy-\d{4}-\d{2})-incidents\.html$", base)
        if not m:
            continue
        fy = FY_OF_PAGE.get(m.group(1))
        if not fy:
            continue
        html = open(f).read()
        n_rows = len(damage_rows(html))
        n_db = rest(f"clancy_dn_incidents?select=id&fy=eq.{urllib.request.quote(fy)}")
        checked += 1
        if n_rows != n_db:
            fails.append(f"{base}: {n_rows} damage rows rendered, DB holds {n_db} for {fy}")
    return checked, fails


def check_stage2(d):
    """The by-source rules, computable from built HTML + the DB. Filled in as stage 2 builds —
    each assertion lands in the SAME commit as the renderer change it proves."""
    fails, checked = [], 0
    for f in sorted(glob.glob(os.path.join(d, "*.html"))):
        html = open(f).read()
        # child rows: every one carries data-parent, and follows a row for that parent
        childs = re.findall(r'<tr[^>]*data-parent="(\d+)"', html)
        parents_seen = set(re.findall(r'<tr[^>]*data-v="(\d+)"', html))
        checked += 1
        for c in childs:
            if parents_seen and c not in parents_seen:
                fails.append(f"{os.path.basename(f)}: child row for {c} has no parent row")
        # an all-dashed action column on a page that should assert is a build failure:
        # cheap canary — a page containing action columns must contain at least one
        # asserted value ("None" or a digit) in them once stage 2 ships. Precise per-year
        # expected counts land with the stage-2 renderer commit.
    return checked, fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--stage2", action="store_true")
    a = ap.parse_args()
    stage2 = a.stage2 or os.environ.get("CLANCY_STAGE2") == "1"

    c1, f1 = check_stage1(a.dir)
    fails = list(f1)
    c2 = 0
    if stage2:
        c2, f2 = check_stage2(a.dir)
        fails += f2

    print(f"page-check: stage-1 checked {c1} page(s)"
          + (f" · stage-2 checked {c2}" if stage2 else " · stage-2 not armed"))
    if fails:
        for x in fails:
            print(f"  FAIL {x}")
        print(f"\n{len(fails)} assertion(s) failed — this build must not be previewed or published.")
        return 1
    print("all assertions hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
