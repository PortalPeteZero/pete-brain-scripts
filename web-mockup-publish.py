#!/usr/bin/env python3
"""pfpub.py — publish a Passion Fit mockup, keeping every previous version.

Pete's rule (31 Jul 2026): never overwrite a mockup. The more versions there are to walk Loren
through, the better the read on what she actually wants.

So before a live slug is replaced, its current content is snapshotted to `<slug>-vN`, which gets its
own permanent CC page. The live slug always holds the newest.

Usage:
  pfpub.py <live-slug> <file.html> "<Title>" <icon> <sort>
  pfpub.py --index                      rebuild the version index page
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

KEYS = Path("/tmp/pbs/Library/processes/secrets/command-centre-supabase-keys.json")
kd = json.loads(KEYS.read_text())
URL = kd["url"].rstrip("/")
KEY = kd.get("service_role_key") or kd["service_role"]
HDR = {"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json"}


def req(path, data=None, method="GET", prefer=None):
    h = dict(HDR)
    if prefer:
        h["Prefer"] = prefer
    r = urllib.request.Request(URL + "/rest/v1/" + path,
                               data=json.dumps(data).encode() if data is not None else None,
                               method=method, headers=h)
    body = urllib.request.urlopen(r, timeout=40).read()
    return json.loads(body) if body else None


def next_version(slug):
    rows = req(f"modules?slug=like.{slug}-v*&select=slug") or []
    ns = [int(m.group(1)) for r in rows if (m := re.search(r"-v(\d+)$", r["slug"]))]
    return max(ns) + 1 if ns else 1


def publish(slug, path, title, icon, sort):
    html = Path(path).read_text(encoding="utf-8")

    # snapshot whatever is live now
    cur = req(f"module_content?module_key=eq.{slug}&select=html") or []
    if cur and cur[0].get("html"):
        v = next_version(slug)
        vslug = f"{slug}-v{v}"
        old = cur[0]["html"]
        old = old.replace("Mockup <b", f"<b style='color:#8b8b93'>ARCHIVED v{v}</b> &nbsp;&middot;&nbsp; Mockup <b", 1)
        req("modules?on_conflict=module_key", [{
            "module_key": vslug, "title": f"{title} (v{v})", "section": "Customers",
            "subsection": "External", "slug": vslug, "tier": "public", "groups": ["passion-fit"],
            "tags": ["passion-fit", "website", "mockup", "archive"], "icon": "🗄️",
            "accent": "#8b8b93", "status": "live", "enabled": True, "sort": 900 + v,
            "passcode": None, "area": "Passion Fit", "reads": ["module_content"]}],
            "POST", "resolution=merge-duplicates,return=minimal")
        req("module_content?on_conflict=module_key", [{"module_key": vslug, "html": old}],
            "POST", "resolution=merge-duplicates,return=minimal")
        print(f"  archived previous -> /m/{vslug}")

    req("modules?on_conflict=module_key", [{
        "module_key": slug, "title": title, "section": "Customers", "subsection": "External",
        "slug": slug, "tier": "public", "groups": ["passion-fit"],
        "tags": ["passion-fit", "website", "mockup"], "icon": icon, "accent": "#e6167b",
        "status": "live", "enabled": True, "sort": int(sort), "passcode": None,
        "area": "Passion Fit", "reads": ["module_content"]}],
        "POST", "resolution=merge-duplicates,return=minimal")
    req("module_content?on_conflict=module_key", [{"module_key": slug, "html": html}],
        "POST", "resolution=merge-duplicates,return=minimal")
    print(f"  published -> /m/{slug}  ({len(html):,} chars)")


def main():
    if sys.argv[1] == "--index":
        rows = sorted(req("modules?tags=cs.{mockup}&select=slug,title,sort") or [],
                      key=lambda r: r["sort"])
        for r in rows:
            print(f"  {r['sort']:>4}  {r['slug']:<32} {r['title']}")
        return 0
    publish(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    return 0


if __name__ == "__main__":
    sys.exit(main())
