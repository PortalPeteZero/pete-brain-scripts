#!/usr/bin/env python3
"""pack2.py — flatten a RENDERED DOM dump into one self-contained HTML file.

Why not the static export: these templates put their hero in a client component, so `next build`
prerenders an empty shell and the content only exists in the JS payload. Dumping the DOM from a
running dev server (`chrome --dump-dom`) captures the page after React has rendered it, which is
the only version worth showing.

What it does:
  - pulls every stylesheet the page links and inlines it
  - rewrites local /images/* to public Supabase brand URLs so the file stands alone
  - strips all script tags (the DOM is already rendered; scripts would only re-hydrate and fail)
  - pins entrance animations to their end state so nothing renders invisible
  - injects an identifying banner

Usage: pack2.py <dom.html> <base-url> <output.html> <banner-html>
"""
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BRAND = "https://sghmdrvtlatjijbkqfld.supabase.co/storage/v1/object/public/brand"
IMG = {
    "hero-main.jpg": "hero-team-1.jpeg", "program-header.jpg": "hero-team-2.jpeg",
    "grid-strength.jpg": "hero-team-1.jpeg", "grid-endurance.jpg": "hero-team-2.jpeg",
    "grid-power.jpg": "hero-team-2.jpeg", "grid-grit.jpg": "hero-team-1.jpeg",
    "og-image.jpg": "hero-team-1.jpeg",
}
PIN = """
<style id="pf-pin">
/* rendered-DOM export: land every entrance animation on its end state */
[class*="anim-"],[style*="animation"]{opacity:1!important;clip-path:none!important;
  animation:none!important;transform:none!important;visibility:visible!important}
/* safety net for reveal-on-scroll wrappers: the DOM is dumped unscrolled, so any section below the
   fold is still in its pre-reveal state. Two flavours to catch:
     - Tailwind classes (opacity-0 translate-y-8), rewritten below
     - an INLINE style set by React (style="opacity:0;transform:translateY(60px)"), which no class
       rule can beat without !important, and whose duration is often arbitrary (duration-[800ms]) */
[class*="transition"][style*="opacity:0"],
[class*="transition"][style*="opacity: 0"]{
  opacity:1!important;transform:none!important;filter:none!important;
  clip-path:none!important;visibility:visible!important}
html,body{overflow-x:hidden}
</style>
"""

# Tailwind reveal pairs, written literally in the DOM. Rewriting the class string is more precise
# than a CSS override and survives any later specificity fight.
REVEAL = re.compile(r"opacity-0(\s+(?:translate-[xy]-\d+|scale-9\d|blur-sm))*")


def fetch(url: str) -> str:
    try:
        return urllib.request.urlopen(url, timeout=20).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"    ! could not fetch {url}: {str(e)[:60]}")
        return ""


def main() -> int:
    dom, base, dest, banner = Path(sys.argv[1]), sys.argv[2].rstrip("/"), Path(sys.argv[3]), sys.argv[4]
    html = dom.read_text(encoding="utf-8")

    inlined = 0
    for m in list(re.finditer(r'<link[^>]*rel="stylesheet"[^>]*>', html)):
        tag = m.group(0)
        href = re.search(r'href="([^"]+)"', tag)
        if not href:
            html = html.replace(tag, "");  continue
        u = href.group(1)
        full = u if u.startswith("http") else base + ("" if u.startswith("/") else "/") + u
        css = fetch(full)
        inlined += len(css)
        html = html.replace(tag, f"<style>{css}</style>" if css else "")

    # next/image rewrites src to /_next/image?url=%2Fimages%2Ffoo.jpg&w=… — unwrap it back to the
    # real file first, otherwise the whole grid blanks out when the runtime is stripped.
    def unwrap(m):
        inner = urllib.parse.unquote(m.group(2))
        name = inner.rsplit("/", 1)[-1]
        return f'{m.group(1)}="{BRAND}/{IMG[name]}"' if name in IMG else f'{m.group(1)}="#"'

    html = re.sub(r'(src|srcset|href)="/_next/image\?url=([^&"]+)[^"]*"', unwrap, html)
    resolved = html.count(BRAND)

    for local, remote in IMG.items():
        html = html.replace(f"/images/{local}", f"{BRAND}/{remote}")
    html = re.sub(r'(src|href)="/_next/[^"]*"', r'\1="#"', html)
    html = re.sub(r'\ssrcset="[^"]*/_next/[^"]*"', "", html)

    reveals = len(REVEAL.findall(html))
    html = REVEAL.sub("opacity-100", html)

    inline = len(re.findall(r'style="[^"]*opacity: ?0[;"]', html))
    html = re.sub(r'opacity: ?0;(transform:[^;"]*;?)?', "", html)

    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    html = re.sub(r"<script[^>]*/?>", "", html)
    html = re.sub(r'<link[^>]*rel="preload"[^>]*>', "", html)

    html = html.replace("</head>", PIN +
                        '<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">'
                        '<meta name="robots" content="noindex, nofollow"></head>', 1)
    html = re.sub(r"(<body[^>]*>)", r"\1" + banner, html, count=1)

    dest.write_text(html, encoding="utf-8")
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[html.find("<body"):]))
    print(f"  packed {dest.name}: {len(html):,} chars | css {inlined:,} | visible text {len(txt):,}")
    print(f"  unresolved local refs: {len(re.findall(chr(34) + '/(?!/)', html))}")
    print(f"  reveal wrappers un-hidden: {reveals}")
    print(f"  next/image srcs resolved: {resolved}")
    dead = len(re.findall(r'src="#"', html))
    print(f"  dead image srcs: {dead}" + ("  <-- CHECK" if dead else ""))
    print(f"  inline opacity:0 cleared: {inline}")
    # count on the body only: the injected <style> block talks ABOUT opacity-0 in a comment, and a
    # gate that cries wolf gets ignored
    body = html[html.find("<body"):]
    left = (len(re.findall(r"opacity-0(?![\d.])", body))
            + len(re.findall(r'style="[^"]*opacity: ?0[;"]', body)))
    print(f"  still hidden after fix: {left}" + ("  <-- CHECK" if left else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
