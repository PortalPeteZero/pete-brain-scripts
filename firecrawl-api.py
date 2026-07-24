#!/usr/bin/env python3
"""
Firecrawl API helper -- the ONE sanctioned path for reading a web page we cannot fetch ourselves.

WHY THIS EXISTS (24 Jul 2026): competitor analysis kept dead-ending. morson-nexus.com -- the page
ranking #1 for Sygma's head term -- serves a Cloudflare bot challenge to `curl`, to WebFetch and to a
plain automated browser fetch. A whole day of SEO work was spent theorising about that page instead of
reading it. Firecrawl renders the page properly and returns clean markdown.

⚠ SCOPE -- read PUBLIC pages only. This is for public marketing/competitor pages that a person could
open in a browser. It is NOT for anything behind a login, a paywall, or a CAPTCHA a human must solve.
If a page needs credentials, stop and ask Pete; do not authenticate through this helper.

Auth:   Bearer token, secret 'firecrawl-api-key' (pointer-only, local file wins).
Config: [[firecrawl-api-configuration]].  API base: https://api.firecrawl.dev/v2

Cost: Firecrawl bills per request, so this helper LOGS every call to public.seo_api_usage (service
'firecrawl') the same way ahrefs-api.py and surfer-api.py do. There is no free tier assumption here --
do not put it in a cron without asking Pete.

CLI:
  VAULT=/tmp/pbs python3 /tmp/pbs/firecrawl-api.py scrape <url> [out.md]
  VAULT=/tmp/pbs python3 /tmp/pbs/firecrawl-api.py search "<query>" [limit]
  VAULT=/tmp/pbs python3 /tmp/pbs/firecrawl-api.py compare <url-a> <url-b>   # SEO page-shape diff
"""
import os, sys, json, re, urllib.request, urllib.error, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
BASE = "https://api.firecrawl.dev/v2"


def _key():
    """Local materialised file first (matches every other helper); DB as fallback."""
    p = f"{VAULT}/Library/processes/secrets/firecrawl-api-key"
    if os.path.exists(p):
        return open(p).read().strip()
    r = subprocess.run(["python3", "cc-sql.py",
                        "SELECT value FROM public.secrets WHERE name='firecrawl-api-key'"],
                       cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=30)
    return json.loads(r.stdout)[0]["value"].strip()


def _log(endpoint, status, note, property_key=None):
    try:
        subprocess.run(["python3", "cc-sql.py",
            "INSERT INTO public.seo_api_usage (service,endpoint,units,cached,http_status,caller,property_key,note) "
            f"VALUES ('firecrawl',$x${endpoint[:200]}$x$,1,false,{status if status else 'NULL'},"
            f"'firecrawl-api',{'$x$'+property_key+'$x$' if property_key else 'NULL'},$x${(note or '')[:200]}$x$)"],
            cwd=VAULT, capture_output=True, text=True, env={**os.environ, "VAULT": VAULT}, timeout=20)
    except Exception:
        pass


class FirecrawlError(RuntimeError):
    pass


def call(path, body, property_key=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {_key()}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            out = json.loads(r.read().decode())
            _log(path, r.status, str(body.get("url") or body.get("query"))[:120], property_key)
            return out
    except urllib.error.HTTPError as e:
        try:
            reason = e.read().decode()[:250]
        except Exception:
            reason = ""
        _log(path, e.code, reason[:120], property_key)
        tag = ("AUTH -- key rejected" if e.code == 401 else
               "PAYMENT/QUOTA -- credits exhausted" if e.code in (402, 429) else f"HTTP {e.code}")
        raise FirecrawlError(f"[{tag}] {reason}")


def scrape(url, property_key=None):
    """Clean markdown for a public page. Works on pages that block curl/WebFetch."""
    d = call("/scrape", {"url": url, "formats": ["markdown"], "onlyMainContent": True}, property_key)
    return (d.get("data") or {}).get("markdown", "")


def search(query, limit=5):
    return call("/search", {"query": query, "limit": limit})


def page_shape(md):
    """The SEO-relevant SHAPE of a page: what it is built to DO, not what it is about.

    Born from the 24 Jul comparison of Sygma's cat-and-genny page (position 26) against the page at
    position 1. Both were ~3,000 words -- so 'more content' was never the difference. What differed was
    shape: 15 headings vs 50, 2 question-headings vs 12, 85 'book' mentions vs 6, a published price and
    course dates vs neither. Word count hides that completely; this exposes it.
    """
    heads = re.findall(r"^#{1,3}\s+(.+)$", md, re.M)
    txt = re.sub(r"\s+", " ", md)
    prices = sorted(set(re.findall(r"£\s?[\d,]+(?:\.\d{2})?(?:\s?\+\s?VAT)?", md)))
    n = lambda w: len(re.findall(r"\b" + w, txt, re.I))
    return {"words": len(txt.split()), "headings": len(heads),
            "question_headings": sum(1 for h in heads if h.strip().rstrip("*").endswith("?")),
            "prices": prices, "book": n("book"), "date": n("date"), "venue": n("venue"),
            "online": n("online"), "course": n("course"), "heading_list": heads}


def compare(a, b):
    ma, mb = scrape(a), scrape(b)
    sa, sb = page_shape(ma), page_shape(mb)
    print(f"  {'':22}{a.split('/')[2][:24]:>26}{b.split('/')[2][:24]:>26}")
    print("  " + "-" * 74)
    for k in ["words", "headings", "question_headings", "book", "date", "venue", "online", "course"]:
        print(f"  {k:22}{str(sa[k]):>26}{str(sb[k]):>26}")
    print(f"  {'prices':22}{(', '.join(sa['prices']) or 'none'):>26}{(', '.join(sb['prices']) or 'none'):>26}")
    print("\n  Read SHAPE, not length: two pages of the same word count can be a booking page and a "
          "reference document.\n  Headings, in order:")
    for lbl, s in ((a, sa), (b, sb)):
        print(f"\n   {lbl}")
        for h in s["heading_list"][:18]:
            print(f"     - {re.sub(r'[*_]', '', h)[:70]}")
    return sa, sb


def _cli():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    try:
        if a[0] == "scrape":
            md = scrape(a[1])
            if len(a) > 2:
                open(a[2], "w").write(md); print(f"{len(md)} chars -> {a[2]}")
            else:
                print(md[:4000])
        elif a[0] == "search":
            print(json.dumps(search(a[1], int(a[2]) if len(a) > 2 else 5), indent=1)[:4000])
        elif a[0] == "compare":
            compare(a[1], a[2])
        else:
            print(f"unknown command: {a[0]}\n{__doc__}")
    except FirecrawlError as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    _cli()
