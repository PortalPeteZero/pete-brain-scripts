#!/usr/bin/env python3
"""Cloudinary per-page-folder audit for sygma-solutions-nextjs.

Walks the repo, collects every Cloudinary public_id reference per source page,
queries Cloudinary for each asset's actual folder, and reports violations of
the per-page-folder policy:

  - Each `src/app/{path}/page.tsx` should reference images that live in
    `sygma-solutions/pages/{equivalent-folder}` on Cloudinary.
  - Cross-page reuse is allowed if the asset is tagged `shared-asset`.
  - Loose uploads in `sygma-solutions/Uploads` are violations regardless of source.

Usage:
  python3 cloudinary-audit.py [--repo /path/to/sygma-solutions-nextjs] [--fix]

Default repo path: /tmp/sygma-fresh
With --fix: prints suggested asset-update calls (does not execute).

Per-page policy doc: Library/processes/cloudinary-per-page-policy.md
"""
import argparse, json, re, sys, urllib.request, urllib.parse, pathlib

CLOUDINARY_API_BASE = "https://api.cloudinary.com/v1_1/dqf1mp7en"
# Note: Admin API requires CLOUDINARY_API_KEY + CLOUDINARY_API_SECRET env vars.
# Without those, the script falls back to using the MCP-search results saved to
# /tmp/cld-folders.json (build that via a one-off MCP search-assets call).

PATTERNS = [
    re.compile(r'<Image[^>]+src=["\']([a-z0-9-]+)["\']'),
    re.compile(r'\bimage=["\']([a-z0-9-]+)["\']'),
    re.compile(r'\bthumb\s*:\s*["\']([a-z0-9-]+)["\']'),
]
PUBLIC_ID_PREFIXES = ('cat-', 'gpr-', 'mala-', 'safe-', 'new-landscape-', 'sygma-')


def expected_folder(srcfile: pathlib.Path, root: pathlib.Path) -> str | None:
    rel = srcfile.relative_to(root).as_posix()
    parts = rel.split('/')
    if len(parts) == 1 and parts[0] == 'page.tsx':
        return 'sygma-solutions/pages/home'
    if len(parts) == 2 and parts[1] == 'page.tsx':
        slug = parts[0]
        if slug == 'courses': return 'sygma-solutions/pages/courses-index'
        if slug == 'knowledge-hub': return 'sygma-solutions/pages/knowledge-hub/index'
        if slug == 'locations': return 'sygma-solutions/pages/locations/index'
        if slug.startswith('cable-avoidance-training-'):
            return f'sygma-solutions/pages/landing/{slug}'
        return f'sygma-solutions/pages/{slug}'
    if len(parts) >= 3 and parts[-1] == 'page.tsx':
        section, slug = parts[0], parts[1]
        if section in ('courses', 'knowledge-hub', 'training', 'locations'):
            return f'sygma-solutions/pages/{section}/{slug}'
    return None


def collect_refs(root: pathlib.Path) -> dict[str, list[str]]:
    refs = {}
    for f in root.rglob('page.tsx'):
        src = f.read_text()
        ids = set()
        for pat in PATTERNS:
            for m in pat.finditer(src):
                pid = m.group(1)
                if any(pid.startswith(p) for p in PUBLIC_ID_PREFIXES):
                    ids.add(pid)
        if ids:
            refs[str(f.relative_to(root))] = sorted(ids)
    return refs


def load_folders_cache() -> dict[str, dict]:
    """Load Cloudinary public_id -> {asset_folder, tags} cache from /tmp.

    This file is built by the operator running an MCP search-assets call and
    saving the results. The script doesn't make API calls itself by default
    because the MCP is the auth path in this environment.
    """
    p = pathlib.Path('/tmp/cld-folders-full.json')
    if p.exists():
        return json.loads(p.read_text())
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='/tmp/sygma-fresh', help='path to sygma-solutions-nextjs repo')
    ap.add_argument('--fix', action='store_true', help='print suggested asset-update calls')
    args = ap.parse_args()

    root = pathlib.Path(args.repo) / 'src' / 'app'
    if not root.exists():
        print(f"ERROR: {root} not found. Pass --repo if your clone is elsewhere.", file=sys.stderr)
        sys.exit(2)

    refs = collect_refs(root)
    folders = load_folders_cache()
    if not folders:
        print("WARN: /tmp/cld-folders-full.json not found.", file=sys.stderr)
        print("  Build it via the MCP: search-assets {expression: 'public_id:cat-* OR public_id:gpr-*', max_results: 500, fields: 'public_id,asset_folder,tags'}", file=sys.stderr)
        print("  Save .resources to that path as a flat dict {public_id: {asset_folder, tags}}.", file=sys.stderr)
        sys.exit(2)

    violations, missing, ok, shared_ok = [], [], 0, 0
    for src, ids in refs.items():
        exp = expected_folder(root / src, root)
        if not exp:
            continue
        for pid in ids:
            entry = folders.get(pid)
            if entry is None:
                missing.append((src, pid))
                continue
            actual = entry.get('asset_folder', '')
            tags = entry.get('tags', []) or []
            if actual == exp:
                ok += 1
            elif 'shared-asset' in tags:
                shared_ok += 1  # legitimate cross-page use, pass
            else:
                violations.append((src, pid, exp, actual))

    print(f"OK in correct folder:     {ok}")
    print(f"OK shared-asset cross-folder: {shared_ok}")
    print(f"VIOLATIONS:               {len(violations)}")
    print(f"MISSING from Cloudinary:  {len(missing)}")
    if violations:
        print()
        print("=== Violations ===")
        for src, pid, exp, actual in violations:
            print(f"  {src}")
            print(f"    {pid}  -- in {actual}")
            print(f"    expected: {exp}")
            if args.fix:
                # We don't have asset_id here — print MCP shape
                print(f"    FIX: asset-update with asset_folder='{exp}' (look up asset_id by public_id={pid})")
                print(f"         OR if intentional cross-page use: asset-update with tags=['shared-asset']")
    if missing:
        print()
        print("=== Missing (referenced in repo, not on Cloudinary) ===")
        for src, pid in missing[:20]:
            print(f"  {src}: {pid}")
        if len(missing) > 20:
            print(f"  ... ({len(missing) - 20} more)")
    sys.exit(0 if not violations and not missing else 1)


if __name__ == '__main__':
    main()
