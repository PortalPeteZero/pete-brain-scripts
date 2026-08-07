#!/usr/bin/env python3
"""The Command Centre's REAL route list, and a gate that a sweep covered it.

Why this exists
---------------
On 6-7 Aug 2026 a contrast sweep reported "145 of 145 routes clean" twice while
Pete was looking at unreadable text. The measurement was honest; the LIST was
wrong. It came from one source — the `public.modules` page registry — but a page
can also exist purely as a file under `app/`, and 39 real routes existed only that
way: all 13 Health sub-pages, the El Atico finance pages, /settings/*, the auth
screens. The page Pete screenshotted was one of them.

The registry is the SSOT for what is REGISTERED, not for what RENDERS. So the
route list is the UNION of both creation paths, and this is the only thing that
should ever produce it.

Usage
-----
  cc-route-coverage.py list [--repo DIR]
      Print every route, one per line. Feed this to the sweeper.

  cc-route-coverage.py check FILE [--repo DIR]
      FILE is the list a sweep actually covered. Exits 1 and prints the missing
      routes if it is not the full set. Use this before quoting a coverage score.

  cc-route-coverage.py count [--repo DIR]
      Print the counts from each source and the union.

`--repo` defaults to $CC_REPO, then /tmp/cc-ui.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

DEFAULT_REPO = os.environ.get("CC_REPO", "/tmp/cc-ui")
VAULT = os.environ.get("VAULT", "/tmp/pbs")


def registry_routes():
    """Slugs from public.modules -> /m/<slug>."""
    q = ("SELECT slug FROM modules WHERE enabled IS NOT FALSE "
         "AND (status IS NULL OR status <> 'hidden') ORDER BY slug")
    r = subprocess.run(["python3", os.path.join(VAULT, "cc-sql.py")], input=q,
                       capture_output=True, text=True, cwd=VAULT,
                       env={**os.environ, "VAULT": VAULT})
    try:
        rows = json.loads(r.stdout.strip())
    except Exception:
        print("cc-route-coverage: could not read the modules registry:\n"
              + (r.stderr or r.stdout)[:300], file=sys.stderr)
        # A failed read is NOT an empty registry — refuse rather than under-report.
        sys.exit(2)
    return {"/m/" + row["slug"] for row in rows if row.get("slug")}


def filesystem_routes(repo):
    """Static page.tsx routes under app/. Dynamic segments are skipped: they need
    a real id to render, so they are listed separately by `count`."""
    app = pathlib.Path(repo) / "app"
    if not app.is_dir():
        print(f"cc-route-coverage: no app/ directory under {repo}", file=sys.stderr)
        sys.exit(2)
    static, dynamic = set(), set()
    for p in app.rglob("page.tsx"):
        rel = "/" + str(p.parent.relative_to(app))
        if rel == "/.":
            rel = "/"
        # Next route groups like (auth) are structural, not part of the URL, but
        # they are kept verbatim here because the sweeper resolves them fine and
        # dropping them silently would be exactly the bug this file exists to stop.
        (dynamic if "[" in rel else static).add(rel)
    return static, dynamic


def all_routes(repo):
    static, _ = filesystem_routes(repo)
    return sorted(registry_routes() | static)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["list", "check", "count"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    a = ap.parse_args()

    if a.mode == "count":
        reg = registry_routes()
        static, dynamic = filesystem_routes(a.repo)
        union = reg | static
        print(f"registry (public.modules) : {len(reg)}")
        print(f"filesystem (app/**/page.tsx): {len(static)} static, {len(dynamic)} dynamic")
        print(f"UNION                      : {len(union)}")
        only_fs = sorted(static - reg)
        if only_fs:
            print(f"\n{len(only_fs)} route(s) exist ONLY as files (invisible to the registry):")
            for r in only_fs:
                print("   ", r)
        return 0

    if a.mode == "list":
        for r in all_routes(a.repo):
            print(r)
        return 0

    # check
    if not a.file:
        print("cc-route-coverage: check needs the file the sweep covered", file=sys.stderr)
        return 2
    covered = {l.strip() for l in open(a.file) if l.strip()}
    expected = set(all_routes(a.repo))
    missing = sorted(expected - covered)
    extra = sorted(covered - expected)
    print(f"expected {len(expected)} route(s), swept {len(covered)}")
    if extra:
        print(f"{len(extra)} swept route(s) not in the expected set (stale or renamed):")
        for r in extra[:20]:
            print("   ", r)
    if missing:
        print(f"\nNOT COVERED — {len(missing)} route(s). Do not quote a coverage score:")
        for r in missing:
            print("   ", r)
        return 1
    print("\ncoverage complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
