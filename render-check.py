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

How it measures (7 Aug 2026 -- this used to be a lie). Headless Chrome's --dump-dom cannot hand
back a JS return value, so the probe below is run inside a throwaway wrapper page that loads the
target in an iframe, reads it cross-frame, and writes its findings back into the wrapper's own
DOM as base64. --dump-dom then carries them out. Before this, the probe was written to a temp
file that was never passed to Chrome and deleted unread: contrast and overflow were dead code and
the tool printed "no mechanical faults" for pages it had never measured. If the probe cannot run
the exit code is 2 (could not check) -- never 0. A gate that silently passes is worse than none.

The 500px floor: headless Chrome will not make its window narrower than 500 CSS px, whatever
--window-size says (measured -- 320/390/480 all lay out at 500). So a phone width is rendered at
its true size in an iframe inside a 500px canvas, which reflows genuinely -- media queries fire
against the real width -- and the leftover strip is hatched and labelled in the screenshot so it
cannot be mistaken for the page. Screenshotting a 500px layout into a 390px frame, as this once
did, just crops 110px off and reads as a real bug.

Usage:
  render-check.py page.html                  # exit 0 clean / 1 faults found / 2 could not run
  render-check.py page.html --shot out.png   # keep the screenshot (default /tmp/render-check.png)
  render-check.py page.html --width 390      # check a phone width
  render-check.py page.html --json
"""
import base64, binascii, json, os, re, subprocess, sys, tempfile
from pathlib import Path

CHROME = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Chromium.app/Contents/MacOS/Chromium",
          "/usr/bin/google-chrome", "/usr/bin/chromium")

MIN_CANVAS = 500   # headless Chrome's floor. Measured, not folklore -- see the docstring.
CANVAS_H = 4000    # tall window so one screenshot catches the whole page, as it always has
MARK = "RC_" + "BEGIN"          # split so the probe source cannot match its own sentinel
ENDMARK = "RC_" + "END"

PROBE = r"""
function rcProbe(doc, win) {
  const gcs = el => win.getComputedStyle(el);
  const lum = c => {
    const m = c && c.match(/[\d.]+/g); if (!m || m.length < 3) return null;
    const [r,g,b] = m.slice(0,3).map(Number);
    const a = m.length > 3 ? Number(m[3]) : 1;
    if (a < 0.1) return null;
    const f = v => { v/=255; return v <= .03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4); };
    return .2126*f(r) + .7152*f(g) + .0722*f(b);
  };
  // First opaque background going up. Bail to null if anything in the chain paints an image or
  // gradient -- we cannot sample those, and "unmeasurable" must not be reported as "fine".
  const bgOf = el => {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = gcs(n);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;
      const l = lum(s.backgroundColor);
      if (l !== null) return l;
    }
    return 1;                                   // nothing opaque anywhere: canvas is white
  };
  const label = el => {
    const c = (el.className && el.className.toString ? el.className.toString() : '')
                .trim().split(/\s+/)[0];
    return (el.tagName.toLowerCase() + (el.id ? '#'+el.id : c ? '.'+c : '')).slice(0,44);
  };
  // "Is this actually painted?" -- NOT gcs(el).display !== 'none'. A descendant of a display:none
  // ancestor still computes its own display (block, inline-block...) while rendering nothing and
  // measuring 0x0, so the naive test reports every hidden-language string as collapsed. The
  // drain-survey template's Spanish half is exactly that. checkVisibility walks the ancestors;
  // it does NOT look at size, so genuinely collapsed elements still get caught below.
  const rendered = el => {
    if (typeof el.checkVisibility === 'function')
      return el.checkVisibility({contentVisibilityAuto: true, opacityProperty: true,
                                 visibilityProperty: true});
    const s = gcs(el);
    if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity < .05) return false;
    return el.getClientRects().length > 0;      // display:none subtree yields no rects at all
  };
  // Inside something that clips or scrolls sideways = contained, not overflowing the viewport.
  const clipped = el => {
    for (let n = el.parentElement; n && n.nodeType === 1; n = n.parentElement) {
      const ox = gcs(n).overflowX;
      if (ox === 'hidden' || ox === 'auto' || ox === 'scroll' || ox === 'clip') return true;
    }
    return false;
  };

  const SKIP = {SCRIPT:1, STYLE:1, HEAD:1, LINK:1, META:1, TITLE:1, BASE:1, NOSCRIPT:1};
  const out = {textNodes:0, unreadable:[], zeroHeight:[], overflow:[],
               bodyH:0, visibleChars:0, unmeasuredText:0, viewport:0};
  const de = doc.documentElement;
  out.viewport = de.clientWidth;
  // The frame is deliberately taller than most pages, so scrollHeight would just report the
  // frame back at us and a blank page would look 4000px tall. Body's own box is the real height.
  out.bodyH = Math.round(doc.body ? doc.body.getBoundingClientRect().height : 0);
  const vw = out.viewport;
  const over = [];

  doc.querySelectorAll('*').forEach(el => {
    if (SKIP[el.tagName]) return;
    const s = gcs(el), r = el.getBoundingClientRect(), vis = rendered(el);

    if (vis && s.position !== 'fixed' && r.width > 4 && r.right > vw + 2 && !clipped(el))
      over.push({el: el, px: Math.round(r.right - vw), name: label(el)});

    if (el.children.length || !vis) return;
    const t = (el.textContent || '').trim(); if (!t) return;
    out.textNodes++;
    if (r.height < 1 && t.length > 2) { out.zeroHeight.push({text: t.slice(0,44), tag: label(el)}); return; }
    if (r.width < 1) return;
    out.visibleChars += t.length;

    // transparent text is invisible -- unless it is the background-clip gradient-text trick
    const clip = s.webkitBackgroundClip || s.backgroundClip;
    const fg = lum(s.color);
    if (fg === null) {
      if (clip !== 'text')
        out.unreadable.push({text: t.slice(0,44), ratio: 0, color: s.color, tag: label(el)});
      return;
    }
    if (clip === 'text') return;
    const bg = bgOf(el);
    if (bg === null) { out.unmeasuredText++; return; }
    const hi = Math.max(fg,bg), lo = Math.min(fg,bg);
    const ratio = +(((hi + .05) / (lo + .05)).toFixed(2));
    if (ratio < 3)
      out.unreadable.push({text: t.slice(0,44), ratio: ratio, color: s.color, tag: label(el)});
  });

  // Report only the outermost offender per subtree, else one wide table reports itself and
  // every cell inside it.
  const set = new Set(over.map(o => o.el));
  out.overflow = over.filter(o => {
      for (let n = o.el.parentElement; n; n = n.parentElement) if (set.has(n)) return false;
      return true;
    })
    .sort((a,b) => b.px - a.px).slice(0,12).map(o => ({el: o.name, px: o.px}));
  out.unreadable = out.unreadable.sort((a,b) => a.ratio - b.ratio).slice(0,12);
  out.zeroHeight = out.zeroHeight.slice(0,12);
  return out;
}
"""

# The wrapper. Loads the target in an iframe sized to the width the caller actually asked for,
# probes it cross-frame on load, and parks the result in its own DOM where --dump-dom finds it.
WRAPPER = r"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  html,body{margin:0;padding:0;overflow:hidden;background:#fff}
  #rcwrap{display:flex;align-items:flex-start;min-height:100vh}
  #rcframe{width:__FRAME_W__px;height:__CANVAS_H__px;border:0;display:block;background:#fff;flex:0 0 auto}
  #rcgutter{flex:1 1 auto;align-self:stretch;box-sizing:border-box;padding:10px 8px;color:#d8d8e4;
    font:11px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;
    background:repeating-linear-gradient(45deg,#26262e,#26262e 9px,#31313b 9px,#31313b 18px)}
  #rcout{display:none}
</style></head><body>
<div id="rcwrap">
  <iframe id="rcframe" src="__PAGE_URL__" scrolling="no"></iframe>
__GUTTER__
</div>
<div id="rcout"></div>
<script>
__PROBE__
(function () {
  const emit = obj => {
    const bytes = new TextEncoder().encode(JSON.stringify(obj));
    let bin = ''; bytes.forEach(b => { bin += String.fromCharCode(b); });
    document.getElementById('rcout').textContent = '__MARK__' + btoa(bin) + '__ENDMARK__';
  };
  const go = () => {
    const f = document.getElementById('rcframe');
    let doc = null, win = null;
    try { doc = f.contentDocument; win = f.contentWindow; } catch (e) { doc = null; }
    if (!doc || !doc.body || !win) {
      emit({rc_error: 'cross-frame access denied - the page could not be measured'}); return;
    }
    // Grow the frame to the whole page. A frame shorter than its content gets a scrollbar, and
    // the scrollbar steals viewport width, which would fake a horizontal overflow. Never shrink
    // below the canvas height, so 100vh keeps meaning what it did before the frame existed.
    for (let i = 0; i < 3; i++)
      f.style.height = Math.max(doc.documentElement.scrollHeight || 0,
                                doc.body.scrollHeight || 0, __CANVAS_H__) + 'px';
    try { emit(rcProbe(doc, win)); }
    catch (e) { emit({rc_error: 'probe threw: ' + ((e && e.message) || e)}); }
  };
  if (document.readyState === 'complete') go();
  else window.addEventListener('load', go);
})();
</script></body></html>
"""

GUTTER = ('  <div id="rcgutter">NOT THE PAGE<br><br>Headless Chrome cannot make a canvas '
          'narrower than %dpx, so the page is laid out at its real __FRAME_W__px in the frame '
          'on the left and this strip fills the rest. It is not clipping.</div>' % MIN_CANVAS)


def chrome():
    for c in CHROME:
        if os.path.exists(c):
            return c
    return None


def parse_args(argv):
    """Same CLI as always: page, --width N, --shot PATH, --json."""
    page, width, shot, as_json = None, 1280, "/tmp/render-check.png", False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            as_json = True
        elif a == "--width":
            i += 1
            if i >= len(argv) or not argv[i].lstrip("-").isdigit():
                die("--width needs a number, e.g. --width 390")
            width = int(argv[i])
        elif a == "--shot":
            i += 1
            if i >= len(argv):
                die("--shot needs a path")
            shot = argv[i]
        elif a.startswith("--width="):
            width = int(a.split("=", 1)[1])
        elif a.startswith("--shot="):
            shot = a.split("=", 1)[1]
        elif a in ("-h", "--help"):
            print(__doc__); sys.exit(0)
        elif a.startswith("--"):
            die(f"unknown option: {a}")
        elif page is None:
            page = a
        i += 1
    return page, width, shot, as_json


def die(msg):
    print(f"render-check: {msg}", file=sys.stderr)
    sys.exit(2)


def measure(ch, page_url, width, shot):
    """One Chrome run: screenshot and probe the same render. Returns (probe_dict, canvas_width)."""
    frame_w = width
    canvas_w = max(width, MIN_CANVAS)
    gutter = GUTTER if canvas_w > frame_w else ""
    doc = (WRAPPER.replace("__PROBE__", PROBE)
                  .replace("__GUTTER__", gutter)          # gutter carries __FRAME_W__ itself
                  .replace("__FRAME_W__", str(frame_w))
                  .replace("__CANVAS_H__", str(CANVAS_H))
                  .replace("__MARK__", MARK)
                  .replace("__ENDMARK__", ENDMARK)
                  .replace("__PAGE_URL__", page_url))     # last: a path must not be re-scanned

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                     encoding="utf-8") as f:
        f.write(doc); wrapper = f.name
    try:
        r = subprocess.run([ch, "--headless", "--disable-gpu", "--no-sandbox",
                            "--allow-file-access-from-files", "--hide-scrollbars",
                            "--virtual-time-budget=8000", "--dump-dom",
                            f"--screenshot={shot}", f"--window-size={canvas_w},{CANVAS_H}",
                            Path(wrapper).as_uri()],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        die("Chrome did not finish in 120s — this is 'unchecked', not 'passed'.")
    finally:
        try: os.unlink(wrapper)
        except OSError: pass

    m = re.search(MARK + r"([A-Za-z0-9+/=]*)" + ENDMARK, r.stdout or "")
    if not m:
        die("the probe did not report back — the page could not be measured. "
            "This is 'unchecked', not 'passed'.\n"
            + (r.stderr or "")[-400:])
    try:
        probe = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
    except (ValueError, binascii.Error) as e:
        die(f"the probe's report was unreadable ({e}) — 'unchecked', not 'passed'.")
    if probe.get("rc_error"):
        die(f"{probe['rc_error']} — 'unchecked', not 'passed'.")
    return probe, canvas_w


def main():
    page, width, shot, as_json = parse_args(sys.argv[1:])
    if not page:
        print(__doc__); sys.exit(2)
    if width < 1:
        die("--width must be positive")
    page = os.path.abspath(page)
    if not os.path.exists(page):
        print(f"No such file: {page}", file=sys.stderr); sys.exit(2)

    ch = chrome()
    if not ch:
        die("No Chrome/Chromium found — cannot render. This is 'unchecked', not 'passed'.")

    # clear any previous shot, so a Chrome that writes nothing cannot pass off a stale image
    if os.path.exists(shot):
        try: os.unlink(shot)
        except OSError: pass

    probe, canvas_w = measure(ch, Path(page).as_uri(), width, shot)

    chars = probe.get("visibleChars", 0)
    faults = []
    if probe.get("bodyH", 0) < 40:
        faults.append(("BLANK", f"the page rendered only {probe.get('bodyH', 0)}px tall"))
    if chars < 60:
        faults.append(("NO TEXT", f"only {chars} characters of text rendered"))
    for u in probe.get("unreadable", []):
        ratio = "transparent" if not u["ratio"] else f"{u['ratio']}:1"
        faults.append(("UNREADABLE TEXT",
                       f"{ratio}  {u['tag']}  \"{u['text']}\"  {u['color']}"))
    for o in probe.get("overflow", []):
        faults.append(("HORIZONTAL OVERFLOW",
                       f"+{o['px']}px past the {probe.get('viewport', width)}px viewport  {o['el']}"))
    for z in probe.get("zeroHeight", []):
        faults.append(("ZERO HEIGHT", f"{z['tag']} holds text but is 0px tall  \"{z['text']}\""))

    size = os.path.getsize(shot) if os.path.exists(shot) else 0
    if size == 0:
        faults.append(("NO SCREENSHOT", "Chrome wrote no image"))

    res = {"page": page, "screenshot": shot, "screenshot_bytes": size,
           "rendered_text_chars": chars,
           "viewport": {"requested": width, "laid_out": probe.get("viewport"),
                        "canvas": canvas_w, "clamped": canvas_w != width},
           "body_height": probe.get("bodyH"),
           "unreadable": probe.get("unreadable", []),
           "overflow": probe.get("overflow", []),
           "zero_height": probe.get("zeroHeight", []),
           "text_on_images_unmeasured": probe.get("unmeasuredText", 0),
           "faults": [{"code": c, "detail": d} for c, d in faults]}

    if as_json:
        print(json.dumps(res, indent=1))
    else:
        print(f"render-check — {os.path.basename(page)}  @{width}px")
        if canvas_w != width:
            print(f"  canvas     : laid out at {width}px in a {canvas_w}px window (Chrome's floor); "
                  f"the hatched\n               {canvas_w - width}px strip right of the page is not "
                  "part of it, and not clipping")
        print(f"  screenshot : {shot} ({size:,} bytes)")
        print(f"  measured   : {chars:,} characters visible · {probe.get('bodyH', 0):,}px tall · "
              f"viewport {probe.get('viewport')}px")
        if probe.get("unmeasuredText"):
            print(f"  note       : {probe['unmeasuredText']} text elements sit on an image or "
                  "gradient — contrast not measurable there")
        last = None
        for c, d in faults:
            print(f"  ✗ {c}: {d}" if c != last else f"      {' ' * len(c)}{d}")
            last = c
        if not faults:
            print("  no mechanical faults.")
        print()
        print("  LOOK AT THE SCREENSHOT. This checks that it rendered, not that it is any good.")

    sys.exit(1 if faults else 0)


if __name__ == "__main__":
    main()
