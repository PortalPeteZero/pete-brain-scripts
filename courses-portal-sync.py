#!/usr/bin/env python3
"""
courses-portal-sync.py -- catalogue -> Portal public.courses reconcile-by-code.

Course-system plan piece B (Projects/SY-Portal-Development/files/course-system-plan-2026-06-08.md):
drive the Portal's course list from the one master (_course-map.yaml). Reconcile by C-code:
  - course in YAML but not in Portal  -> ADD (named "C0XX Name", active)
  - code present but name differs     -> RENAME to the YAML name
  - NEVER deletes or deactivates      -> bookings/pricing/venue hang off existing rows
Hub-side standard_courses names are kept in step for codes it carries (slug/code stay put).

Usage:
  python3 courses-portal-sync.py            # dry run (default) -- prints the diff, writes nothing
  python3 courses-portal-sync.py --apply    # apply the adds/renames

Run after each catalogue edit (or whenever drift is suspected). Safe to re-run; idempotent.
"""

import json
import sys
import urllib.request

import yaml
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
YAML_PATH = f"{VAULT}/Businesses/sygma-solutions/training/courses/_course-map.yaml"
TOKEN_PATH = f"{VAULT}/Library/processes/secrets/supabase-token"
PROJECT_REF = "rsczwfstwkthaybxhszy"


def q(sql: str):
    token = open(TOKEN_PATH).read().strip()
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "curl/8.7.1",
        },
        data=json.dumps({"query": sql}).encode(),
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def main():
    apply = "--apply" in sys.argv

    doc = yaml.safe_load(open(YAML_PATH))
    yaml_courses = {}
    for c in doc.get("courses", []):
        code, name = c.get("code"), (c.get("name") or "").strip()
        status = (c.get("status") or "active").lower()
        if not code or not name:
            continue
        if status in ("retired", "binned"):
            continue  # never push retired codes into the Portal
        yaml_courses[code] = f"{code} {name}"

    rows = q("select id::text, name, is_active from public.courses where name ~ '^C[0-9]'")
    portal = {}
    for r in rows:
        code = r["name"].split(" ", 1)[0]
        portal[code] = r

    adds, renames, in_sync = [], [], 0
    for code, expected in sorted(yaml_courses.items()):
        if code not in portal:
            adds.append(expected)
        elif portal[code]["name"] != expected:
            renames.append((portal[code]["id"], portal[code]["name"], expected))
        else:
            in_sync += 1
    portal_only = sorted(set(portal) - set(yaml_courses))

    print(f"catalogue codes: {len(yaml_courses)} | portal C-coded: {len(portal)} | in sync: {in_sync}")
    print(f"ADD ({len(adds)}):")
    for a in adds:
        print(f"  + {a}")
    print(f"RENAME ({len(renames)}):")
    for _, old, new in renames:
        print(f"  ~ {old}  ->  {new}")
    if portal_only:
        print(f"PORTAL-ONLY codes (left alone — never deleted): {', '.join(portal_only)}")

    if not apply:
        print("\ndry run — nothing written. Re-run with --apply to write.")
        return

    for a in adds:
        slug = a.lower().replace("&", "and")
        slug = "".join(ch if ch.isalnum() else "-" for ch in slug)
        while "--" in slug:
            slug = slug.replace("--", "-")
        q(f"insert into public.courses (name, slug, is_active) values ({lit(a)}, {lit(slug.strip('-'))}, true)")
        # keep the hub catalogue mirror in step
        code = a.split(" ", 1)[0]
        bare = a.split(" ", 1)[1] if " " in a else a
        q(
            "insert into hub.standard_courses (id, name, slug, code, active, source, created_at, updated_at) "
            f"values (gen_random_uuid(), {lit(bare)}, {lit(code.lower())}, {lit(code)}, true, 'catalogue', now(), now()) "
            "on conflict do nothing"
        )
    for cid, _, new in renames:
        q(f"update public.courses set name = {lit(new)}, updated_at = now() where id = '{cid}'")
        code = new.split(" ", 1)[0]
        bare = new.split(" ", 1)[1] if " " in new else new
        q(f"update hub.standard_courses set name = {lit(bare)}, updated_at = now() where code = {lit(code)}")
    print(f"\napplied: {len(adds)} adds, {len(renames)} renames.")


if __name__ == "__main__":
    main()