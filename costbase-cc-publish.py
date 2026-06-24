#!/usr/bin/env python3
"""costbase-cc-publish.py — feed the CC's NATIVE Trainer Cost Base view.

Reads the monthly Soldo-audit `*-final-data.json` (the same source
costbase-hub-load.py loads into the Hub) → normalises per-trainer category spend
+ nights-away analysis + totals → writes data/costbase.json into the Command
Centre repo (~/code/command-centre, PRIVATE) and commits/pushes, so the native
page /m/sygma-soldo/cost-base refreshes. Financial data → only the private repo +
the owner-only sygma-soldo module gate.

  python3 costbase-cc-publish.py            # parse latest + write+commit+push
  python3 costbase-cc-publish.py --print     # parse + print, no write
  python3 costbase-cc-publish.py --out PATH  # parse + write JSON to PATH, no git

Run as a step of the monthly Soldo audit (alongside costbase-hub-load.py).
"""
import os, sys, json, glob, subprocess, datetime, time
from pathlib import Path
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = VAULT
AUDIT_DIR = f"{VAULT}/Businesses/sygma-solutions/finance/audit-data"
HOME = Path(os.path.expanduser("~"))
CC_REPO = HOME / "code/command-centre"
MONTH_ORDER = ["january","february","march","april","may","june","july","august","september","october","november","december"]

def rnd(v):
    try: return round(float(v), 2)
    except (TypeError, ValueError): return None

def latest_final():
    files = sorted(glob.glob(f"{AUDIT_DIR}/*-final-data.json"))
    if not files: sys.exit("no *-final-data.json in audit-data")
    return files[-1]

def parse(path):
    raw = json.load(open(path))
    stem = os.path.basename(path).replace("-final-data.json", "")  # e.g. 2026-05
    claimed = raw.get("claimed", {})
    months = []
    for mkey in MONTH_ORDER:
        m = raw.get(mkey)
        if not isinstance(m, dict) or not m.get("by_tc"):
            continue
        by_tc = m.get("by_tc", {}); derived = m.get("derived", {})
        trainers = []
        for tname, cats in by_tc.items():
            d = derived.get(tname, {})
            categories = {c: rnd(a) for c, a in cats.items()}
            trainers.append({
                "trainer": tname,
                "categories": categories,
                "category_total": rnd(sum(v for v in cats.values() if isinstance(v, (int, float)))),
                "total": rnd(d.get("total")),
                "nights_claimed": claimed.get(mkey, {}).get(tname, d.get("claimed")),
                "hotel_spend": rnd(d.get("hot_total_spend")),
                "hotel_for_month": rnd(d.get("hot_for_month")),
                "food": rnd(d.get("food")),
                "cost_per_night": rnd(d.get("cpn")),
                "hotel_per_night": rnd(d.get("hpn")),
                "food_per_day": rnd(d.get("fpd")),
                "fwd_nights": d.get("fwd_n"),
            })
        trainers.sort(key=lambda t: -(t["total"] or 0))
        # all categories seen this month (for table columns), commonest first
        cols = {}
        for t in trainers:
            for c, a in t["categories"].items():
                cols[c] = cols.get(c, 0) + (a or 0)
        category_columns = [c for c, _ in sorted(cols.items(), key=lambda x: -x[1])]
        months.append({"month": mkey.title(), "trainers": trainers, "category_columns": category_columns})
    return {"generated": datetime.date.today().isoformat(), "source": stem, "months": months}

def git(repo, *a, retries=4):
    for i in range(retries):
        r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
        if r.returncode == 0: return r
        if i == retries - 1: raise RuntimeError(f"git {a[0]}: {r.stderr.strip()[:160]}")
        time.sleep(3)

def main():
    args = sys.argv[1:]
    data = parse(latest_final())
    if "--print" in args:
        print(json.dumps(data, indent=2)); return
    if "--out" in args:
        p = args[args.index("--out") + 1]; Path(p).write_text(json.dumps(data, indent=2)); print("wrote", p); return
    if not CC_REPO.exists(): print("CC repo missing — skip"); return
    git(CC_REPO, "fetch", "origin", "main"); git(CC_REPO, "pull", "--rebase", "--autostash", "origin", "main")
    (CC_REPO / "data").mkdir(exist_ok=True)
    (CC_REPO / "data/costbase.json").write_text(json.dumps(data, indent=2))
    subprocess.run(["git", "-C", str(CC_REPO), "add", "data/costbase.json"], check=True)
    if subprocess.run(["git", "-C", str(CC_REPO), "diff", "--cached", "--quiet"]).returncode == 0:
        print("costbase->CC: no change"); return
    git(CC_REPO, "commit", "-m", "data: refresh trainer cost base", retries=1)
    git(CC_REPO, "push", "origin", "main")
    print(f"costbase->CC: pushed ({len(data['months'])} months)")

if __name__ == "__main__":
    main()