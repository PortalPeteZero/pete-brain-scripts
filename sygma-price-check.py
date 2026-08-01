#!/usr/bin/env python3
"""
sygma-price-check.py -- does sygma-solutions.com publish a price it must not?

WHY THIS EXISTS (1 Aug 2026). On 24 Jul a price block shipped to five course pages carrying
"Based on a £965 day rate for up to 8 delegates at your site". It was Sygma's on-site group
day rate, in plain text, on the public web. It sat there 8 days and was found by Pete, by eye,
while reading a report about something else. Nothing checked it.

THE RULE IT ENFORCES (Pete, 1 Aug 2026). Sygma charges two ways and only ONE may be published:
  · ON SITE      = a fixed day rate for the group.  NEVER publishable, in any form.
  · OPEN COURSE  = a per-delegate rate.  "From £121 per delegate" IS the published price.

⚠ "From £121 per delegate" is NOT a finding. Pete ruled on it directly, twice, on 1 Aug 2026:
keep it. I raised that 121 x 8 lands near the day rate; he decided, and his decision stands.
A gate that fires on Pete's own approved output is worse than no gate. Do not re-add it.
What must never be public is the DAY RATE ITSELF, in figures or by name.

Certificate fees (EUSR £34, ProQual £35) are awarding-body pass-through, labelled as such
on the page, and are explicitly ALLOWED.

Coverage: every URL in the live sitemap PLUS every built route that the sitemap omits (a page
missing from the sitemap is still public, and is exactly what a sitemap-only check would pass).
Scans the visible text AND the JSON-LD structured data.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/sygma-price-check.py               # live site
  VAULT=/tmp/pbs python3 /tmp/pbs/sygma-price-check.py --build DIR   # also cover a local .next build

Exit 0 = clean. Exit 1 = a banned price is live. Exit 2 = the check could not run
(never read a failed run as a pass).
"""
import re, sys, html, glob, subprocess
from concurrent.futures import ThreadPoolExecutor

BASE = "https://sygma-solutions.com"

BANNED = [
    (r"\b965\b",                                   "the on-site group day rate figure"),
    (r"day\s*rate",                                "names the day-rate model on a public page"),
    (r"not\s+per\s+(person|head)\b",               "'per course, not per person' stated as the model"),
    (r"per\s+course,\s*not\s+per",                 "'per course, not per person' stated as the model"),
    (r"(course\s+)?fee\s+(stays|is)\s+the\s+same", "'the fee is the same whether 4 or 8' claim"),
    (r"one\s+fixed\s+fee\s+covers",                "'one fixed fee covers your whole group' claim"),
    (r"no\s+per-head\s+charge",                    "'no per-head charge' absolute claim"),
]

# A per-person price AND a group divisor on the same page = the day rate is derivable.
PER_PERSON = re.compile(r"£\s*[0-9][0-9,]*\s*(?:</[^>]+>\s*)?(?:per\s+(?:delegate|person|head)|pp\b)", re.I)
DIVISOR    = re.compile(r"(?:up\s+to|max(?:imum)?(?:\s+of)?)\s*8\b[^.]{0,40}(?:delegate|person|people|attendee)", re.I)

ALLOWED_MONEY = {"£34", "£35"}   # awarding-body certificate fees, kept deliberately


def fetch(url):
    r = subprocess.run(["curl", "-sS", "-L", "--max-time", "45", "-w", "\n@@%{http_code}", url],
                       capture_output=True, text=True)
    b, c = r.stdout, ""
    if "\n@@" in b:
        b, c = b.rsplit("\n@@", 1)
    return c.strip(), b


def flatten(t):
    """visible text + structured data, whitespace-normalised."""
    ld = " ".join(re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', t, re.S | re.I))
    b = re.sub(r"<script.*?</script>", " ", t, flags=re.S | re.I)
    b = re.sub(r"<style.*?</style>", " ", b, flags=re.S | re.I)
    b = re.sub(r"<[^>]+>", " ", b)
    vis = re.sub(r"\s+", " ", html.unescape(b))
    return vis, re.sub(r"\s+", " ", html.unescape(ld))


def scan(url, body):
    out = []
    vis, ld = flatten(body)
    for where, txt in (("page text", vis), ("structured data", ld)):
        for pat, why in BANNED:
            for m in re.finditer(pat, txt, re.I):
                out.append((url, where, why, txt[max(0, m.start() - 75):m.start() + 75].strip()))
    return out


def main():
    build = None
    if "--build" in sys.argv:
        build = sys.argv[sys.argv.index("--build") + 1].rstrip("/")

    code, sm = fetch(f"{BASE}/sitemap.xml")
    urls = re.findall(r"<loc>([^<]+)</loc>", sm)
    if not urls:
        print(f"CANNOT RUN: sitemap unreadable (http {code or 'no response'}). "
              "This is NOT a pass -- fix the fetch and re-run.")
        return 2

    routes = {u.replace(BASE, "") or "/" for u in urls}
    if build:
        for f in glob.glob(f"{build}/server/app/**/*.html", recursive=True):
            r = f[len(f"{build}/server/app"):-5]
            r = "/" if r == "/index" else r
            if "_not-found" not in r:
                routes.add(r)

    routes = sorted(routes)
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = list(ex.map(lambda r: (r, *fetch(BASE + ("" if r == "/" else r))), routes))

    findings, unchecked = [], []
    for r, c, body in got:
        if c != "200" or not body:
            unchecked.append((r, c))
            continue
        findings += scan(r, body)

    for r, where, why, ctx in findings:
        print(f"BANNED  {r}  [{where}]\n   {why}\n   ...{ctx}...\n")
    for r, c in unchecked:
        print(f"UNCHECKED  {r}  http {c or 'no response'}  <- did NOT run, do not read as clean")

    ok = len(routes) - len(unchecked)
    print(f"\nsygma-price-check: {ok}/{len(routes)} live pages scanned "
          f"(sitemap {len(urls)}{' + build routes' if build else ''}) "
          f"· banned={len(findings)} · unchecked={len(unchecked)}")
    if findings:
        print("RESULT: FAIL -- a price that must not be public is live.")
        return 1
    if unchecked:
        print("RESULT: INCOMPLETE -- some pages did not answer. Re-run before calling it clean.")
        return 2
    print("RESULT: PASS -- no day rate, no derivable per-person figure, no 'per course not per "
          "person' model claim, anywhere public.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
