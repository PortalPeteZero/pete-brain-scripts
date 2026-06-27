---
name: skill-builder
version: v1.0
description: >
  Build, edit, package, and retire Pete's custom skills. Trigger phrases:
  "build a skill", "create a skill", "new skill", "author a skill", "edit this
  skill", "package a skill", "update a skill", "retire a skill".
---

# Skill Builder

The canonical how-to for creating, editing, packaging, and retiring Pete's custom skills.

## Where skills live

Source of truth: `pete-brain-scripts` repo, pulled to `/tmp/pbs` by the boot kernel.

```
/tmp/pbs/skills/
  {name}/
    SKILL.md        -- current operational instructions only (no history, no version banners)
    CHANGELOG.md    -- full version history (every edit, dated)
    references/     -- (optional) style guides, templates, reference files
    scripts/        -- (optional) helper scripts specific to this skill
  {name}.skill      -- zip archive of the folder's CONTENTS (built by package-skill.py)
```

Helper code (`{service}-api.py`, `cc-sql.py`, etc.) lives at the **repo root** — never inside a skill folder.

The CC surfaces skills in the Process Library → Skills tab (`/m/process-library?bucket=Skills`). This is powered by the `public.skills` table, kept in sync by `cc-skeleton-registry-sync.py`.

## Creating a new skill

1. **Scaffold the folder** — `mkdir /tmp/pbs/skills/{name}`

2. **Write SKILL.md** — frontmatter + body:

```yaml
---
name: {name}
version: v1.0
description: >
  One or two sentences. This is the trigger line — Claude matches it to "build a
  skill", "create a skill", etc. Include every verb Pete will actually say.
---

# {Name}

[Operational instructions — current state only. No "(NEW)" markers,
no version banners, no history. Write as if landing fresh.]
```

   Key rules for SKILL.md:
   - `description:` drives invocation — phrase it as what Pete will say, list the trigger verbs explicitly
   - **Current state only** — version history, inline `(vX.Y)` tags, `(NEW)` annotations all go in `CHANGELOG.md`
   - Start clean: every invocation loads the full SKILL.md so long preambles waste context budget

3. **Write CHANGELOG.md** — minimum first entry:

```markdown
# {Name} Changelog

## v1.0 — {YYYY-MM-DD}
- Initial version.
```

4. **Add to skills/README.md** Active Skills table — one row:

```markdown
| `{name}/` | v1.0 | Cowork + Claude Code | One-line purpose. Full history: [[CHANGELOG]]. |
```

5. **Package it** — `VAULT=/tmp/pbs python3 /tmp/pbs/package-skill.py {name}`
   This builds `/tmp/pbs/skills/{name}.skill` and delivers it to `~/Downloads/cc-skills-to-install/`.

6. **Install it** — open Cowork or the Claude Code skill installer, install `{name}.skill`, then empty the folder once installed.

7. **Wire verbs** — if the skill is invoked by a verb routed through the `brain` skill (e.g. `/brain resume`, `/brain compress`), add an entry to the routing table in `brain/SKILL.md`.

8. **Push** — `git -C /tmp/pbs add skills/{name}/ skills/{name}.skill skills/README.md && git -C /tmp/pbs commit -m "skill: add {name} v1.0" && git -C /tmp/pbs push`

9. **Sync the registry** — `VAULT=/tmp/pbs python3 /tmp/pbs/cc-skeleton-registry-sync.py`
   This populates the CC Process Library with the new skill (name, description, version, last-edited, content).

## Editing an existing skill

1. Edit `skills/{name}/SKILL.md` — operational instructions only (current state; no changelogs inline)
2. Bump version in SKILL.md frontmatter (`version: v1.0` → `v1.1`)
3. Add a CHANGELOG entry in `skills/{name}/CHANGELOG.md`
4. Update the version in `skills/README.md`
5. Repackage — `VAULT=/tmp/pbs python3 /tmp/pbs/package-skill.py {name}`
6. Re-install the delivered `.skill` in Cowork / Claude Code
7. Push + re-run registry sync (steps 8–9 above)

**Lockstep rule:** repackage in the same session as any SKILL.md edit. Source and `.skill` archive must always match.

## Packaging & delivery

The packager is `package-skill.py` in the repo root:

```
package-skill.py <name> [<name> ...]   # named skill(s)
package-skill.py --all                 # every skill
package-skill.py --changed             # only skills where source differs from archive
package-skill.py --no-deliver ...      # rebuild archive only, skip local delivery
```

What it does:
- Zips the folder's **contents** so `SKILL.md` sits at the archive root (never nested under `{name}/`)
- Uses a fixed timestamp so the zip is byte-stable — content change = archive change
- When `~/Downloads` exists (local Mac session), delivers the `.skill` to `~/Downloads/cc-skills-to-install/` and writes `_INSTALL-ME.md`
- On cloud/Railway: rebuilds the archive but skips local delivery (still keeps source + archive in lockstep)

**Never hand-zip a `.skill`.** Always use the packager.

## Retiring a skill

1. Move the folder — `mv /tmp/pbs/skills/{name}/ /tmp/pbs/skills/_previous/{name}-retired-{YYYY-MM-DD}/`
2. Remove the sibling archive — `rm /tmp/pbs/skills/{name}.skill`
3. Remove from `skills/README.md` Active Skills table; add to the Retired table
4. Update the brain routing table if the skill was a verb
5. Uninstall the `.skill` from Cowork / Claude Code
6. Push — the registry sync will auto-prune the row from `public.skills` on its next run

## Pointers

- Packager: `/tmp/pbs/package-skill.py`
- Registry sync: `/tmp/pbs/cc-skeleton-registry-sync.py`
- Skills index: `skills/README.md`
- Install folder: `~/Downloads/cc-skills-to-install/`
- CC Process Library: `/m/process-library?bucket=Skills`
