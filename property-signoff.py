#!/usr/bin/env python3
"""property-signoff.py -- the PROPERTY session gate (the ee-signoff / triage-signoff twin).

Exits NON-ZERO while any property record is incomplete. Wired into the closeout skill.

WHY THIS EXISTS (Pete, 19 Jul 2026):
  Every other category already had a blocking gate -- enquiries (ee-signoff), triage
  (triage-signoff), customer/supplier knowledge (entity-enrich-signoff). Properties had
  NONE: closeout's I6 and I7 are explicitly "advisory CHECKS, not gates ... never block".
  So properties were the one thing whose correctness depended on Pete remembering to ask
  "have you checked this? have you checked that?". His words: "Every fucking time we do any
  work, I've got to ask ... it's fucking tiresome."

  This is the gate on the way out. The property-state hook is the link on the way in.

CHECKS (all blocking unless marked):
  P1. hosting declared      -- every live property says who serves it
  P2. repo declared         -- every live property says where its code lives
                               (wordpress/squarespace/wix legitimately have none)
  P3. front door resolves   -- where a front_door is recorded, the note actually exists.
                               A front door nobody can walk to is worse than none: it reads
                               as covered. (This is the LeakGuard orphan class.)
  P4. front door recorded   -- WARNING only, never blocking. Backfilling 14 properties is a
                               deliberate piece of work, not something to block a session on.

  front_door holds a vault_path, NOT a [[slug]] -- slugs are not unique (several notes are
  slugged "README"), so a slug cannot address one note.

SCOPE: --touched limits P1-P3 to properties named on the command line (what this session
  actually worked on), which is how closeout should call it. With no --touched it audits
  every live property.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/property-signoff.py
  VAULT=/tmp/pbs python3 /tmp/pbs/property-signoff.py --touched "Sygma Solutions Website"
  VAULT=/tmp/pbs python3 /tmp/pbs/property-signoff.py --json
"""
import os, sys, json, subprocess, time

VAULT = os.environ.get("VAULT", "/tmp/pbs")
EXCLUDED_STATUS = {"archived", "retired", "retiring"}
NO_REPO_HOSTING = {"wordpress", "squarespace", "wix"}


def q(sql, _retry=True):
    """Query the CC. Returns None on failure -- None means COULD NOT CHECK, never 'nothing
    found'. Retries once: the CC API 429-throttles under load and a single blip must not be
    reported as a clean pass."""
    r = subprocess.run(["python3", f"{VAULT}/cc-sql.py", sql],
                       env={**os.environ, "VAULT": VAULT},
                       capture_output=True, text=True, timeout=90)
    # cc-sql.py prints errors to STDOUT, not stderr -- check the payload, not just the code.
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
    args = sys.argv[1:]
    as_json = "--json" in args
    touched = []
    if "--touched" in args:
        i = args.index("--touched")
        touched = [a for a in args[i + 1:] if not a.startswith("--")]

    props = q("SELECT name, coalesce(f->>'status','') AS status, "
              "coalesce(f->>'hosting','') AS hosting, coalesce(f->>'github','') AS github, "
              "coalesce(f->>'front_door','') AS front_door, key FROM property_declarations")
    if props is None:
        print("BLOCK  could not read property_declarations -- status UNKNOWN, not reported clean")
        sys.exit(1)

    live = [p for p in props if p["status"].lower() not in EXCLUDED_STATUS]
    if touched:
        # Match on the IMMUTABLE key first, then the display name. A caller passing a renamed
        # property's OLD name would otherwise match nothing and the gate would pass on an empty
        # scope -- the exact false-clean this gate exists to prevent.
        want = {t.lower() for t in touched}
        scope = [p for p in live
                 if (p.get("key") or "").lower() in want or p["name"].lower() in want]
        matched = {(p.get("key") or "").lower() for p in scope} | {p["name"].lower() for p in scope}
        missing = sorted(want - matched)
        if missing:
            print(f"BLOCK  --touched named {len(missing)} propert(ies) that do not exist "
                  f"or are not live: {', '.join(missing)}")
            sys.exit(1)
    else:
        scope = live

    no_host = sorted(p["name"] for p in scope if not p["hosting"])
    no_repo = sorted(p["name"] for p in scope
                     if not p["github"] and p["hosting"].lower() not in NO_REPO_HOSTING)
    no_door = sorted(p["name"] for p in scope if not p["front_door"])

    # P3 -- do the recorded front doors actually resolve?
    broken, doors = [], [p for p in scope if p["front_door"]]
    if doors:
        want_paths = sorted({p["front_door"] for p in doors})
        inlist = ",".join("'" + d.replace("'", "''") + "'" for d in want_paths)
        found = q(f"SELECT vault_path FROM vault_notes WHERE vault_path IN ({inlist})")
        if found is None:
            print("BLOCK  could not verify front doors -- status UNKNOWN, not reported clean")
            sys.exit(1)
        have = {r["vault_path"] for r in found}
        broken = sorted(p["name"] for p in doors if p["front_door"] not in have)

    checks = [
        ("P1", "hosting declared", no_host, True),
        ("P2", "repo declared", no_repo, True),
        ("P3", "front door resolves", broken, True),
        ("P4", "front door recorded", no_door, False),
    ]
    blocking = sum(len(items) for _, _, items, block in checks if block)

    if as_json:
        print(json.dumps({
            "scope": [p["name"] for p in scope],
            "blocking": blocking,
            "checks": {cid: {"what": what, "offenders": items, "blocking": block}
                       for cid, what, items, block in checks},
        }, indent=2))
        sys.exit(0 if blocking == 0 else 1)

    scope_note = f"{len(scope)} propert(ies)" + (" touched this session" if touched else " live")
    print(f"PROPERTY SIGN-OFF -- {scope_note}\n")
    for cid, what, items, block in checks:
        if not items:
            print(f"  PASS   {cid} {what}")
        elif block:
            print(f"  BLOCK  {cid} {what} -- {len(items)}: {', '.join(items)}")
        else:
            print(f"  WARN   {cid} {what} -- {len(items)}: {', '.join(items)}")
    print()
    if blocking == 0:
        print("ALL CLEAR -- every property touched can answer where its code lives, who serves "
              "it, and where to read first.")
        sys.exit(0)
    print(f"NOT DONE -- {blocking} blocking item(s) above. Fix them, then re-run.")
    sys.exit(1)


if __name__ == "__main__":
    main()
