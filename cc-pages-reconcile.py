#!/usr/bin/env python3
"""cc-pages-reconcile.py -- reconcile the CC `modules` menu against the command-centre repo.

ON-DEMAND ONLY. Deliberately NOT wired into the daily locator cron.

WHY NOT DAILY (settled 19 Jul 2026 by actually reading the route):
  93 modules, 50 page directories. The naive reading is "43 modules have no page" -- and that is
  wrong twice over. 24 are served by the `app/m/[slug]` catch-all from a `module_content` row,
  and the remaining 19 do NOT 404: `app/m/[slug]/page.tsx` falls through to a "Moving in"
  placeholder for any module with neither. They are unfinished ports, which is a known and
  deliberate state, not drift. A daily check would report 19 non-problems every morning for ever
  -- precisely the flood this whole line of work exists to avoid.

  So this answers "what is the state of the CC pages?" when someone asks, and stays quiet otherwise.

JOIN KEYS THAT BIT (all three cost a wrong answer during the build):
  - `modules.slug` is the URL; `modules.module_key` is what `module_content` joins on. They are
    equal today for every row, but they are DIFFERENT COLUMNS -- join on module_key.
  - `module_content.module_key` also holds asset sub-paths ("slug/assets/report.css"), so a naive
    set difference against `modules` shows dozens of phantom "orphans".
  - Some slugs are handled by explicit redirects inside the route (e.g. clancy-incidents ->
    /m/clancy-cockpit?tab=damages), so "no dir + no content" does not mean unreachable.

Usage:
  REPO=/tmp/cc-repo VAULT=/tmp/pbs python3 /tmp/pbs/cc-pages-reconcile.py [--json]
  (clone first: git clone --depth 1 https://<pat>@github.com/PortalPeteZero/command-centre.git /tmp/cc-repo)
"""
import os, sys, json, subprocess, time, re

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REPO = os.environ.get("REPO", "/tmp/cc-repo")


def q(sql, _retry=True):
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       env={**os.environ, "VAULT": VAULT}, capture_output=True, text=True, timeout=90)
    # cc-sql.py prints errors to STDOUT, not stderr.
    if r.returncode != 0 or (r.stdout or "").lstrip().startswith("ERROR"):
        if _retry:
            time.sleep(1.5)
            return q(sql, _retry=False)
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def main():
    as_json = "--json" in sys.argv
    mdir = os.path.join(REPO, "app", "m")
    if not os.path.isdir(mdir):
        print(f"cc-pages-reconcile: no repo at {REPO} — clone it first (see the docstring). "
              f"NOT reporting clean: this check did not run.", file=sys.stderr)
        sys.exit(2)

    mods = q("SELECT slug, module_key, coalesce(title,'') AS title, coalesce(status,'') AS status "
             "FROM modules")
    content = q("SELECT module_key FROM module_content")
    if mods is None or content is None:
        print("cc-pages-reconcile: CC query failed — status UNKNOWN, not reported clean", file=sys.stderr)
        sys.exit(2)

    dirs = {d for d in os.listdir(mdir)
            if os.path.isdir(os.path.join(mdir, d)) and not d.startswith("[")}
    # module_content also stores asset sub-paths (slug/assets/x.css) — keep only whole keys
    served = {c["module_key"] for c in content if "/" not in c["module_key"]}

    # explicit redirects hard-coded in the route
    redirected = set()
    route = os.path.join(mdir, "[slug]", "page.tsx")
    if os.path.isfile(route):
        redirected = set(re.findall(r'mod\.slug\s*===\s*"([^"]+)"\s*\)\s*redirect', open(route).read()))

    native = sorted(m["slug"] for m in mods if m["slug"] in dirs)
    embedded = sorted(m["slug"] for m in mods if m["slug"] not in dirs and m["module_key"] in served)
    redirects = sorted(m["slug"] for m in mods if m["slug"] not in dirs
                       and m["module_key"] not in served and m["slug"] in redirected)
    placeholder = sorted(m["slug"] for m in mods if m["slug"] not in dirs
                         and m["module_key"] not in served and m["slug"] not in redirected)
    # a page directory with no menu row: genuinely unreachable from the menu
    unlisted = sorted(dirs - {m["slug"] for m in mods})

    out = {"modules": len(mods), "page_dirs": len(dirs),
           "native": native, "embedded": embedded, "redirects": redirects,
           "placeholder": placeholder, "unlisted_dirs": unlisted}
    if as_json:
        print(json.dumps(out, indent=2))
        sys.exit(0)

    print(f"CC PAGES — {len(mods)} modules vs {len(dirs)} page directories\n")
    print(f"  {len(native):>3}  native page       — its own directory under app/m/")
    print(f"  {len(embedded):>3}  embedded         — rendered by the [slug] catch-all from module_content")
    print(f"  {len(redirects):>3}  redirected       — the route sends them elsewhere on purpose")
    print(f"  {len(placeholder):>3}  \"Moving in\"      — no page yet; renders the placeholder, does NOT 404")
    print(f"  {len(unlisted):>3}  unlisted dir     — a page directory with no menu row")
    if placeholder:
        print(f"\n  Still to port ({len(placeholder)}):")
        for s in placeholder:
            print(f"    · {s}")
    if unlisted:
        print(f"\n  ⚠ Page directories with no menu row ({len(unlisted)}) — unreachable from the menu:")
        for s in unlisted:
            print(f"    · {s}")
    print("\nNothing here is drift by itself: a placeholder is an unfinished port, which is a "
          "deliberate state. This is a picture, not a to-do list.")


if __name__ == "__main__":
    main()
