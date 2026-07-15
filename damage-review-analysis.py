#!/usr/bin/env python3
"""
Damage Review — CAT/Genny analysis + scan-chart generator.

Reusable engine for the Damage Review Report process (see vault_notes
[[damage-review-report-process]]). Takes a raw CAT device CSV (per-second log
with Log Reference, Time, Signal Strength, Sensitivity Control, Mode, GPS) and:
  - computes % idle per mode block (sustained >=4s where signal AND sensitivity
    both stop changing = CAT no longer being used to locate, logging time),
  - the mode order + per-block durations (the ~90s fingerprint),
  - GPS centroids + pairwise distances between surveys (don't overclaim
    "separate locations" if only ~330m apart),
  - one SVG per block: signal (mode colour) + sensitivity (purple dashed),
    every idle run shaded so blips read as blips.

Usage:
  python3 damage-review-analysis.py <cat.csv> [--out DIR] [--charts]

Idle rule: |dsignal|<2 AND |dsensitivity|<1 for a run of >=4 samples (seconds).
Mode palette matches the Clancy dashboard: Power red, Radio blue, Genny green,
Avoidance amber; sensitivity purple dashed.
"""
import csv, collections, math, os, sys, json

MODECOL = {"POWER": "#d92b2b", "RADIO": "#2563eb", "GENNY": "#2e8b40", "AVOIDANCE": "#d98a00"}
SENS, BORDER, SOFT = "#8b5cf6", "#e3e6ea", "#8a929b"


def load(csv_path):
    rows = list(csv.DictReader(open(csv_path)))
    def logkey(r): return int(r["Log Reference"].split("#")[1])
    def tsec(r):
        h, m, s = r["Time"].split(":"); return int(h) * 3600 + int(m) * 60 + int(s)
    bylog = collections.defaultdict(list)
    for r in rows: bylog[logkey(r)].append(r)
    for k in bylog: bylog[k].sort(key=tsec)
    return bylog


def idle_runs(rs, minrun=4):
    n = len(rs)
    sens = [float(r["Sensitivity Control"] or 0) for r in rs]
    sig = [float(r["Signal Strength"] or 0) for r in rs]
    idle = [False] * n
    for i in range(1, n):
        if abs(sig[i] - sig[i - 1]) < 2 and abs(sens[i] - sens[i - 1]) < 1:
            idle[i] = True
    runs, i = [], 0
    while i < n:
        if idle[i]:
            j = i
            while j < n and idle[j]: j += 1
            if j - i >= minrun: runs.append((i, j))
            i = j
        else:
            i += 1
    return runs, n


def idle_pct(rs):
    runs, n = idle_runs(rs)
    return round(100 * sum(b - a for a, b in runs) / n) if n else 0


def centroid(rs):
    pts = [(float(r["Latitude"]), float(r["Longitude"])) for r in rs
           if r.get("GPS Fix") == "GPS" and float(r.get("Latitude") or 0) != 0]
    if not pts: return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def dist_m(a, b):
    dy = (a[0] - b[0]) * 111320
    dx = (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def chart_svg(rs, w=300, h=150, big=False):
    n = len(rs)
    sens = [float(r["Sensitivity Control"] or 0) for r in rs]
    sig = [float(r["Signal Strength"] or 0) for r in rs]
    col = MODECOL.get(rs[0]["Mode"], "#2563eb")
    pl, pr, pt, pb = (38, 10, 14, 20) if big else (26, 6, 10, 16)
    iw, ih = w - pl - pr, h - pt - pb
    X = lambda i: pl + (i / max(n - 1, 1)) * iw
    Y = lambda v: pt + (1 - v / 100) * ih
    runs, _ = idle_runs(rs)
    bands = "".join(f'<rect x="{X(a):.1f}" y="{pt}" width="{X(b-1)-X(a):.1f}" height="{ih}" fill="#eceef2" opacity="0.85"/>' for a, b in runs)
    grid = "".join(f'<line x1="{pl}" y1="{Y(v):.1f}" x2="{w-pr}" y2="{Y(v):.1f}" stroke="{BORDER}"/>' for v in (0, 50, 100))
    area = f"M{pl:.1f},{Y(0):.1f} " + " ".join(f"L{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(sig)) + f" L{X(n-1):.1f},{Y(0):.1f} Z"
    sigline = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(sig))
    sensline = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(sens))
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="-apple-system,BlinkMacSystemFont,sans-serif"><rect width="{w}" height="{h}" fill="#fff"/>'
            f'{bands}{grid}<path d="{area}" fill="{col}" opacity="0.13"/>'
            f'<polyline points="{sigline}" fill="none" stroke="{col}" stroke-width="{2 if big else 1.5}" stroke-linejoin="round"/>'
            f'<polyline points="{sensline}" fill="none" stroke="{SENS}" stroke-width="{1.6 if big else 1.1}" '
            f'stroke-dasharray="4 2" stroke-linejoin="round" opacity="0.8"/></svg>')


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    csv_path = sys.argv[1]
    out = "."
    if "--out" in sys.argv: out = sys.argv[sys.argv.index("--out") + 1]
    do_charts = "--charts" in sys.argv
    bylog = load(csv_path)
    print(f"blocks: {len(bylog)}  ({os.path.basename(csv_path)})")
    print(f"{'log':<5}{'mode':<11}{'dur':<6}{'idle%'}")
    idle = {}
    for k in sorted(bylog):
        rs = bylog[k]; idle[k] = idle_pct(rs)
        print(f"#{k:<4}{rs[0]['Mode']:<11}{len(rs):<6}{idle[k]}%")
        if do_charts:
            big = False
            open(os.path.join(out, f"scan-{k:02d}.svg"), "w").write(chart_svg(rs, big=big))
    json.dump(idle, open(os.path.join(out, "idle.json"), "w"))
    # GPS
    cents = {k: centroid(bylog[k]) for k in sorted(bylog)}
    print("\nGPS centroids + first-vs-others distance:")
    ks = [k for k in cents if cents[k]]
    for k in ks:
        d = dist_m(cents[ks[0]], cents[k]) if cents[ks[0]] else 0
        print(f"  #{k}: {cents[k][0]:.5f},{cents[k][1]:.5f}  ({round(d)}m from #{ks[0]})")


if __name__ == "__main__":
    main()
