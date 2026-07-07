#!/usr/bin/env python3
"""
property-link-health.py -- post-deploy link-health monitor for any Pete property.

Crawls a site's sitemap, extracts every link on every page, and checks each target's
HTTP status. Flags 4xx/5xx targets AND ranks them by how many pages link to them --
so a single broken link sitting in a shared template (nav/footer/form) can't hide across
the whole site again (the exact "one bug x 66 pages" class from the Sygma 2026-07 audit).

Usage:
  VAULT=/tmp/pbs python3 property-link-health.py https://sygma-solutions.com
  VAULT=/tmp/pbs python3 property-link-health.py https://sygma-solutions.com --external   # also check external links
  VAULT=/tmp/pbs python3 property-link-health.py https://sygma-solutions.com --json

Exit code 1 if any internal link is broken (usable as a cron/CI gate).
CRON-META (optional daily monitor -- deploy only on Pete's OK):
  # name: sygma-link-health
  # schedule: 0 8 * * *
  # entity: sygma
"""
import sys, re, json, urllib.request, urllib.error
from urllib.parse import urljoin, urlparse
from collections import defaultdict

UA = "Mozilla/5.0 (compatible; AhrefsSiteAudit/6.1; +http://ahrefs.com/robot/site-audit)"

def fetch(u, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=timeout).read().decode(errors="replace")

def status(u, timeout=25):
    try:
        return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=timeout).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return "ERR:" + type(e).__name__

def sitemap_urls(base):
    idx = fetch(urljoin(base, "/sitemap.xml"))
    locs = re.findall(r"<loc>([^<]+)</loc>", idx)
    child = [l for l in locs if l.endswith(".xml")]
    if child:
        out = []
        for c in child:
            out += re.findall(r"<loc>([^<]+)</loc>", fetch(c))
        return sorted(set(out))
    return sorted(set(locs))

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print("usage: property-link-health.py <base-url> [--external] [--json]"); sys.exit(2)
    base = args[0].rstrip("/")
    host = urlparse(base).netloc
    pages = sitemap_urls(base)
    # collect links: target -> set(pages linking to it)
    link_pages = defaultdict(set)
    for p in pages:
        try:
            html = fetch(p)
        except Exception as e:
            link_pages[f"[UNFETCHABLE PAGE] {p}"].add(p); continue
        for h in set(re.findall(r'href="([^"#]+)"', html)):
            if h.startswith(("mailto:", "tel:", "javascript:", "data:")):
                continue
            a = urljoin(p, h)
            if not a.startswith("http"):
                continue
            internal = urlparse(a).netloc.endswith(host)
            if internal or "--external" in flags:
                link_pages[a].add(p)
    # check each unique target once
    broken = []
    for target, srcs in link_pages.items():
        if target.startswith("[UNFETCHABLE"):
            broken.append(("PAGE", target, len(srcs))); continue
        code = status(target)
        if not (isinstance(code, int) and 200 <= code < 400):
            broken.append((code, target, len(srcs)))
    broken.sort(key=lambda x: -x[2])
    result = {
        "base": base, "pages_crawled": len(pages),
        "unique_links_checked": len([k for k in link_pages if not k.startswith("[UNFETCHABLE")]),
        "broken": [{"status": c, "url": u, "linked_from_pages": n} for c, u, n in broken],
    }
    if "--json" in flags:
        print(json.dumps(result, indent=2))
    else:
        print(f"Link health: {base}")
        print(f"  pages crawled: {result['pages_crawled']}   unique links checked: {result['unique_links_checked']}")
        if not broken:
            print("  ✓ NO broken links found.")
        else:
            print(f"  ✗ {len(broken)} broken target(s) (sitewide/shared links first):")
            for c, u, n in broken:
                print(f"     {c}  linked from {n} page(s)  {u}")
    sys.exit(1 if broken else 0)

if __name__ == "__main__":
    main()
