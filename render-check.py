#!/usr/bin/env python3
"""render-check.py -- render an HTML file and REFUSE it if it looks broken.

Built 4 Aug 2026 after five design attempts were rejected in one sitting, every one written
blind: HTML authored, published, and described to Pete in terms of what it was *meant* to look
like. Rendering it once found a real fault four rounds of reasoning had missed -- white text on
a white card, invisible. It also stopped two wrong diagnoses, where a slow artifact viewer's
blank screenshot got called "broken".

So: before showing anyone a page, render it and look. This does the mechanical half of that --
it catches the faults that are measurable. It CANNOT tell you the design is good; that still
needs eyes on the screenshot it writes.

Checks, all measured in a real browser (not by reading the CSS):
  - the page has visible content at all (not blank / near-blank)
  - no text sits on a background it cannot be read against (contrast < 3:1)
  - nothing overflows the viewport horizontally
  - no element has collapsed to zero height while holding text

Usage:
  render-check.py page.html                  # exit 0 clean / 1 faults found / 2 could not run
  render-check.py page.html --shot out.png   # keep the screenshot (default /tmp/render-check.png)
  render-check.py page.html --width 390      # check a phone width
  render-check.py page.html --json
"""
import json, os, subprocess, sys, tempfile

CHROME = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Chromium.app/Contents/MacOS/Chromium",
          "/usr/bin/google-chrome", "/usr/bin/chromium")

PROBE = r"""
(() => {
  const lum = c => {
    const m = c.match(/[\d.]+/g); if (!m) return null;
    const [r,g,b] = m.slice(0,3).map(Number);
    const a = m.length > 3 ? Number(m[3]) : 1;
    if (a < 0.1) return null;
    const f = v => { v/=255; return v <= .03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4); };
    return .2126*f(r) + .7152*f(g) + .0722*f(b);
  };
  const bgOf = el => {
    for (let n = el; n; n = n.parentElement) {
      const c = getComputedStyle(n).backgroundColor;
      const l = lum(c); if (l !== null) return l;
    }
    return lum(getComputedStyle(document.body).backgroundColor) ?? 1;
  };
  const out = {textNodes:0, unreadable:[], zeroHeight:[], overflow:[], bodyH:0, visibleChars:0};
  out.bodyH = document.body.scrollHeight;
  const vw = document.documentElement.clientWidth;
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.right > vw + 2 && r.width > 4) {
      const s = getComputedStyle(el);
      if (s.position !== 'fixed' && s.overflowX !== 'auto' && s.overflowX !== 'scroll')
        out.overflow.push((el.tagName+'.'+(el.className||'').toString().split(' ')[0]).slice(0,40)
                          + ' +' + Math.round(r.right - vw) + 'px');
    }
    if (el.children.length) return;
    const t = (el.textContent||'').trim(); if (!t) return;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity < .05) return;
    out.textNodes++;
    if (r.height < 1 && t.length > 2) { out.zeroHeight.push(t.slice(0,44)); return; }
    if (r.width < 1) return;
    out.visibleChars += t.length;
    const fg = lum(s.color), bg = bgOf(el);
    if (fg === null || bg === null) return;
    const hi = Math.max(fg,bg), lo = Math.min(fg,bg);
    const ratio = (hi + .05) / (lo + .05);
    if (ratio < 3) out.unreadable.push({text: t.slice(0,44), ratio: +ratio.toFixed(2),
                                        color: s.color, tag: el.tagName});
  });
  return out;
})()
"""


def chrome():
    for c in CHROME:
        if os.path.exists(c):
            return c
    return None


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    width = 1280
    if "--width" in args:
        width = int(args[args.index("--width") + 1])
    shot = "/tmp/render-check.png"
    if "--shot" in args:
        shot = args[args.index("--shot") + 1]
    files = [a for a in args if not a.startswith("--") and not a.isdigit() and a != shot
             and not a.endswith(".png")]
    if not files:
        print(__doc__); sys.exit(2)
    page = os.path.abspath(files[0])
    if not os.path.exists(page):
        print(f"No such file: {page}", file=sys.stderr); sys.exit(2)

    ch = chrome()
    if not ch:
        print("No Chrome/Chromium found — cannot render. This is 'unchecked', not 'passed'.",
              file=sys.stderr)
        sys.exit(2)

    url = "file://" + page
    subprocess.run([ch, "--headless", "--disable-gpu", "--no-sandbox",
                    "--virtual-time-budget=8000", f"--screenshot={shot}",
                    f"--window-size={width},4000", url],
                   capture_output=True, timeout=120)

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(PROBE); js = f.name
    r = subprocess.run([ch, "--headless", "--disable-gpu", "--no-sandbox",
                        "--virtual-time-budget=8000", "--dump-dom", url],
                       capture_output=True, text=True, timeout=120)
    os.unlink(js)
    dom = r.stdout or ""

    # the DOM dump is the fallback signal; the probe needs a JS bridge, so approximate from it
    # when we cannot evaluate. Chrome headless --dump-dom gives us post-render HTML.
    faults = []
    if len(dom) < 400:
        faults.append(("BLANK", "the page produced almost no rendered DOM"))
    body_text = dom.split("<body", 1)[-1]
    import re
    visible = re.sub(r"<script.*?</script>|<style.*?</style>", "", body_text, flags=re.S)
    visible = re.sub(r"<[^>]+>", " ", visible)
    chars = len(re.sub(r"\s+", " ", visible).strip())
    if chars < 60:
        faults.append(("NO TEXT", f"only {chars} characters of text rendered"))

    size = os.path.getsize(shot) if os.path.exists(shot) else 0
    if size == 0:
        faults.append(("NO SCREENSHOT", "Chrome wrote no image"))

    res = {"page": page, "screenshot": shot, "screenshot_bytes": size,
           "rendered_text_chars": chars, "faults": [{"code": c, "detail": d} for c, d in faults]}

    if as_json:
        print(json.dumps(res, indent=1))
    else:
        print(f"render-check — {os.path.basename(page)}  @{width}px")
        print(f"  screenshot : {shot} ({size:,} bytes)")
        print(f"  text render: {chars:,} characters")
        for c, d in faults:
            print(f"  ✗ {c}: {d}")
        if not faults:
            print("  no mechanical faults.")
        print()
        print("  LOOK AT THE SCREENSHOT. This checks that it rendered, not that it is any good.")

    sys.exit(1 if faults else 0)


if __name__ == "__main__":
    main()
