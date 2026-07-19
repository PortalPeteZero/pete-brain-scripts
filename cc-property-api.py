#!/usr/bin/env python3
"""cc-property-api.py -- create + update a property's CC card (public.property_declarations).

The card is the LIVE source the SEO skills read (Ahrefs/Surfer/project_slug/GSC/GA4) — so it must be
writable by a tool, not by hand-editing a deleted README. Writes `property_declarations.f` (jsonb),
merging into `f.declared` (the frontmatter the nightly property-sync reads into property_state); a few
keys are mirrored to `f` top-level where the sync reads them there (gsc, ga4, surfer, ahrefs).

Usage:
  # create a new card
  cc-property-api.py --create "The Leaky Finders Website" --entity "Canary Detect" \
      [--domain theleakyfinders.com] [--github PortalPeteZero/theleakyfinders-nextjs]

  # set SEO/infra fields on an existing card (canonical keys below)
  cc-property-api.py --set "Sygma Solutions Website" \
      ahrefs=9613452 surfer=1312139 project_slug=SY-Website gsc=sc-domain:sygma-solutions.com

  # read a card back
  cc-property-api.py --get "Sygma Solutions Website"

Canonical --set keys (written clean, no quotes):
  ahrefs       -> f.declared.ahrefs_project_id  (+ f.ahrefs)
  surfer       -> f.declared.surfer_workspace   (+ f.surfer)
  project_slug -> f.declared.project_slug
  gsc          -> f.declared.gsc_property        (+ f.gsc)
  ga4          -> f.declared.ga4_property_id      (+ f.ga4)
  any other key=value is merged verbatim into f.declared.
"""
import os, sys, json, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REF = "zhexcaflgahdcbzvbyfq"
TOK = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()

# canonical --set key -> (declared-frontmatter key, optional f-top-level mirror key)
FIELD_MAP = {
    "ahrefs": ("ahrefs_project_id", "ahrefs"),
    "surfer": ("surfer_workspace", "surfer"),
    "project_slug": ("project_slug", None),
    "gsc": ("gsc_property", "gsc"),
    "ga4": ("ga4_property_id", "ga4"),
}
ENTITY_BIZ = {
    "Sygma": "[[Businesses/sygma-solutions]]", "Canary Detect": "[[Businesses/canary-detect]]",
    "Personal": "personal", "One System": "[[Businesses/one-system]]", "El Atico": "[[Businesses/el-atico]]",
}


def ccq(sql):
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{REF}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"SQL ERROR {e.code}: {e.read().decode()[:400]}\n"); raise


def lit(s):
    return "NULL" if s is None else "'" + str(s).replace("'", "''") + "'"


def jlit(obj):
    return "'" + json.dumps(obj).replace("'", "''") + "'::jsonb"


def get_card(name):
    """Resolve a property by its IMMUTABLE key first, then its display name, then a fuzzy match.

    The key never changes (the DB enforces it), so key lookups keep working across renames --
    which is why they are tried first. Name lookups still work for humans typing the label.
    """
    rows = ccq(f"SELECT name, key, f FROM property_declarations WHERE key = {lit(name)}")
    if rows:
        return rows[0]
    rows = ccq(f"SELECT name, key, f FROM property_declarations WHERE name = {lit(name)}")
    if rows:
        return rows[0]
    rows = ccq(f"SELECT name, key, f FROM property_declarations "
               f"WHERE name ILIKE {lit('%' + name + '%')} OR key ILIKE {lit('%' + name + '%')} LIMIT 2")
    return rows[0] if len(rows) == 1 else (None if not rows else "AMBIGUOUS")


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)

    if a[0] == "--get":
        card = get_card(a[1])
        if not card or card == "AMBIGUOUS":
            sys.exit(f"cc-property: '{a[1]}' {'not found' if not card else 'is ambiguous'}")
        print(json.dumps(card["f"], indent=2)); return

    if a[0] == "--create":
        name = a[1]
        opt = dict(kv.split("=", 1) for kv in a[2:] if "=" in kv)
        # flags --entity/--domain/--github
        ent = None
        for i, x in enumerate(a):
            if x == "--entity":
                ent = a[i + 1]
            elif x == "--domain":
                opt["domain"] = a[i + 1]
            elif x == "--github":
                opt["github"] = a[i + 1]
        if get_card(name) not in (None,):
            sys.exit(f"cc-property: a card named '{name}' already exists — use --set")
        declared = {"type": "property", "status": "active"}
        if opt.get("domain"):
            declared["domain"] = opt["domain"]
        if opt.get("github"):
            declared["github"] = opt["github"]; declared["github_repo"] = opt["github"]
        f = {"ptype": "marketing-site", "status": "active",
             "business": ENTITY_BIZ.get(ent, "") if ent else "",
             "domains": [opt["domain"]] if opt.get("domain") else [],
             "github": opt.get("github", ""), "declared": declared}
        ccq(f"INSERT INTO property_declarations (name, f, updated_at) VALUES ({lit(name)}, {jlit(f)}, now())")
        print(f"cc-property: created card '{name}'" + (f" ({ent})" if ent else "")); return

    if a[0] == "--set":
        name = a[1]
        card = get_card(name)
        if not card or card == "AMBIGUOUS":
            sys.exit(f"cc-property: '{name}' {'not found (use --create)' if not card else 'is ambiguous'}")
        f = card["f"] or {}
        declared = f.get("declared") or {}
        changed = []
        for kv in a[2:]:
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            dkey, ftop = FIELD_MAP.get(k, (k, None))
            declared[dkey] = v
            if ftop:
                f[ftop] = v
            changed.append(f"{dkey}={v}")
        f["declared"] = declared
        ccq(f"UPDATE property_declarations SET f = {jlit(f)}, updated_at = now() WHERE name = {lit(card['name'])}")
        print(f"cc-property: {card['name']} ← " + ", ".join(changed)); return

    sys.exit(__doc__)


if __name__ == "__main__":
    main()
