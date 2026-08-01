#!/usr/bin/env python3
"""clancy_dn_ui.py — the ONE design system for everything under Genny's Damage Depot.

WHY THIS EXISTS: the section grew as three separate generators (hub, damages pages, reviews) and
each carried its own copy of the CSS, so the look drifted and there was no shared navigation.
Pete, 31 Jul 2026: "navigation is terrible its hard to get back anywhere properly and not cclear,
no breadcrumbs" and "think a top navbar specific to this section and open on all these sections
would be good too for quick access". So: one navbar, one breadcrumb bar, one visual language,
imported by every generator. Change it here and it changes everywhere.

Brand colours are The Clancy Group's own, sampled from their logo, not invented:
  #97D700 chartreuse · #D50032 red · #353E47 charcoal.

Assets are served from the Next app's /public/clancy/ (deployed with the CC), NOT embedded, so a
page stays small and the browser caches the logos once across the whole section.

Nothing in here talks to the database. It renders chrome; the callers supply the content.
"""

import re
import html as H

# ── brand ────────────────────────────────────────────────────────────────────────────────────
GREEN = "#97D700"     # Clancy chartreuse
RED = "#D50032"       # Clancy red
CHAR = "#353E47"      # Clancy charcoal
INK = "#182230"
MUTED = "#6b7784"

HUB = "clancy-depotnet"
DAMAGES = "clancy-depotnet-damages"
REVIEWS = "clancy-genny-cat-reviews"
REPORTS = "clancy-damage-reports"
# The analysis page ("What the damage data tells us"). One per financial year; the nav points at
# the current one. Its absence here broke the hub's fourth door on 1 Aug 2026.
ANALYSIS = "clancy-damage-analysis"
BOT = "clancy-bot"

A_SYGMA = "/clancy/sygma-white.png"
A_CLANCY = "/clancy/clancy-white.png"
A_GENNY = "/clancy/genny.png?v=hivis1"
A_HERO = "/clancy/hero-works-v3.jpg"

# ── the shared stylesheet ────────────────────────────────────────────────────────────────────
# Split deliberately in two:
#
#   CHROME  — the navbar, breadcrumbs, brand bar and compact masthead. Safe to drop into ANY page
#             in the section, including the older generators that carry their own stylesheet, so
#             every colour here is a literal hex rather than a var(): a host page that defines its
#             own --green must not be able to re-tint the section navigation.
#   CSS     — CHROME plus the full page system (tokens, hero, KPI row, doors, notes, footer) for
#             pages written against this design system from the start.
#
# Class names in the page system are prefixed where they would otherwise collide with the older
# generators' own classes (.dnote, not .note).
CHROME = """
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
 -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}

/* ── section navbar — present on EVERY page in the Depot, always reachable ── */
.dnav{position:sticky;top:0;z-index:60;background:#353E47;
 border-bottom:1px solid rgba(255,255,255,.10);box-shadow:0 1px 0 rgba(0,0,0,.18)}
.dnav-in{max-width:1160px;margin:0 auto;padding:0 18px;height:54px;
 display:flex;align-items:center;gap:16px}
.dnav-brand{display:flex;align-items:center;gap:9px;text-decoration:none;color:#fff;
 font-size:14.5px;letter-spacing:-.01em;white-space:nowrap;flex-shrink:0}
.dnav-brand img{width:28px;height:28px;border-radius:50%;object-fit:cover;
 border:1.5px solid #97D700;flex-shrink:0}
.dnav-brand b{font-weight:800;color:#97D700}
.dnav-links{display:flex;gap:2px;align-items:center;margin-left:6px;overflow-x:auto;
 scrollbar-width:none;-ms-overflow-style:none}
.dnav-links::-webkit-scrollbar{display:none}
.dnav-links a{display:block;padding:7px 12px;border-radius:8px;text-decoration:none;
 color:#c8ced6;font-size:13.5px;font-weight:600;white-space:nowrap;background:none;
 transition:background .18s,color .18s}
.dnav-links a:hover{background:rgba(255,255,255,.09);color:#fff}
.dnav-links a.on{background:rgba(151,215,0,.16);color:#97D700}
.dnav-ask{margin-left:auto;flex-shrink:0;display:inline-flex;align-items:center;gap:7px;
 background:#97D700;color:#1d2b00;font-size:13.5px;font-weight:800;text-decoration:none;
 padding:8px 15px;border-radius:9px;border:0;cursor:pointer;font-family:inherit;
 transition:transform .18s,box-shadow .18s}
.dnav-ask:hover{transform:translateY(-1px);box-shadow:0 6px 16px rgba(151,215,0,.4)}
.dnav-ask .dot{width:7px;height:7px;border-radius:50%;background:#2f7a00;
 box-shadow:0 0 0 3px rgba(47,122,0,.22)}
@media(max-width:720px){.dnav-brand span{display:none}.dnav-in{gap:8px}}

/* ── breadcrumbs — sticky under the navbar so the way back never scrolls off ── */
.crumbs{position:sticky;top:54px;z-index:55;background:rgba(255,255,255,.93);
 backdrop-filter:saturate(1.6) blur(10px);-webkit-backdrop-filter:saturate(1.6) blur(10px);
 border-bottom:1px solid #e4e8ee}
.crumbs-in{max-width:1160px;margin:0 auto;padding:9px 22px;font-size:12.5px;color:#8b95a3;
 display:flex;align-items:center;gap:7px;flex-wrap:wrap;overflow-x:auto;white-space:nowrap}
.crumbs a{color:#6b7784;text-decoration:none;font-weight:600}
.crumbs a:hover{color:#5f8b00;text-decoration:underline}
.crumbs .sep{opacity:.5}
.crumbs .here{color:#182230;font-weight:700}

/* ── the co-branded bar: Sygma left, Clancy right. Both logos are white-on-transparent. ── */
.logos{display:flex;align-items:center;justify-content:space-between;gap:20px}
.logos img.sy{height:34px;width:auto}
.logos img.cl{height:30px;width:auto}
.logos .mid{font-size:11px;font-weight:800;letter-spacing:.2em;text-transform:uppercase;
 color:rgba(255,255,255,.62);text-align:center}
@media(max-width:640px){.logos .mid{display:none}.logos img.sy{height:26px}.logos img.cl{height:23px}}

/* ── THE hero. Identical on every page in the Depot: photograph, both logos, Genny with her
      nameplate, the name and the strapline. Only the page line underneath changes. ── */
.hero{position:relative;color:#fff;overflow:hidden;background:#353E47;
 border-bottom:3px solid #97D700}
.hero-bg{position:absolute;inset:0;max-width:1160px;margin:0 auto;
 background-image:url('/clancy/hero-works-v3.jpg');background-size:cover;
 background-position:center 34%;filter:saturate(.72);
 -webkit-mask-image:linear-gradient(90deg,transparent 0,#000 6%,#000 94%,transparent 100%);
 mask-image:linear-gradient(90deg,transparent 0,#000 6%,#000 94%,transparent 100%)}
.hero-bg::after{content:"";position:absolute;inset:0;
 background:linear-gradient(100deg,rgba(28,34,41,.90) 0%,rgba(28,34,41,.68) 44%,rgba(28,34,41,.42) 74%,rgba(28,34,41,.52) 100%),
 linear-gradient(180deg,rgba(28,34,41,.28) 0%,rgba(28,34,41,.04) 34%,rgba(28,34,41,.50) 100%)}
.hero-in{position:relative;max-width:1160px;margin:0 auto;padding:20px 22px 34px}
.hero-mid{display:flex;align-items:center;gap:30px;margin-top:26px}
@media(max-width:820px){.hero-mid{flex-direction:column;text-align:center;gap:20px}}
.gennywrap{position:relative;flex-shrink:0;padding-bottom:16px}
.gennywrap img{width:132px;height:132px;border-radius:50%;object-fit:cover;
 object-position:center 20%;border:4px solid #97D700;box-shadow:0 14px 36px rgba(0,0,0,.5);
 display:block;background:#2a323a}
@media(max-width:560px){.gennywrap img{width:96px;height:96px}}
.nameplate{position:absolute;left:50%;bottom:0;transform:translateX(-50%);background:#97D700;
 color:#1d2b00;font-size:12px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;
 padding:5px 15px;border-radius:20px;white-space:nowrap;box-shadow:0 5px 16px rgba(0,0,0,.4);
 display:flex;align-items:center;gap:7px}
.nameplate .live{width:7px;height:7px;border-radius:50%;background:#1d2b00;
 animation:pulse 2.4s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.hero h1{font-size:40px;line-height:1.08;letter-spacing:-.03em;font-weight:800;margin-bottom:8px;
 color:#fff}
.hero h1 .g{color:#97D700}
@media(max-width:640px){.hero h1{font-size:29px}}
.hero .strap{font-size:16.5px;color:#d3dae2;font-style:italic;letter-spacing:.01em}
.hero .pageline{margin-top:18px;padding-left:14px;border-left:3px solid #97D700}
@media(max-width:820px){.hero .pageline{border-left:0;border-top:3px solid #97D700;
 padding:12px 0 0;margin-left:auto;margin-right:auto;display:inline-block}}
.hero .pageline .kick{font-size:10.5px;font-weight:800;letter-spacing:.16em;
 text-transform:uppercase;color:#97D700;display:block;margin-bottom:3px}
.hero .pageline .pt{font-size:21px;font-weight:800;letter-spacing:-.02em;color:#fff;
 line-height:1.2}
.hero .pageline .ps{font-size:13.5px;color:#c3cbd4;margin-top:4px;max-width:74ch}
.hero .says{margin-top:16px;font-size:14.5px;color:#c3cbd4;max-width:56ch;
 border-left:3px solid #97D700;padding-left:14px}
@media(max-width:820px){.hero .says{border-left:0;border-top:3px solid #97D700;
 padding:12px 0 0;margin-left:auto;margin-right:auto}}
.chips{display:flex;gap:9px;flex-wrap:wrap;margin-top:18px}
@media(max-width:820px){.chips{justify-content:center}}
.chip{background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.20);border-radius:99px;
 padding:6px 14px;font-size:12.5px;color:#e4e9ee;font-weight:600}
.chip b{color:#97D700;font-variant-numeric:tabular-nums}
.hero .yearnav{margin-top:20px}
@media print{.dnav,.crumbs{display:none}}
"""

CSS = CHROME + """
:root{
 --green:#97D700;--green-d:#5f8b00;--red:#D50032;--char:#353E47;--char-2:#2a323a;
 --ink:#182230;--mid:#3c4757;--muted:#6b7784;--faint:#8b95a3;
 --line:#e4e8ee;--bg:#f4f6f9;--card:#fff;
 --sh-1:0 1px 2px rgba(16,24,40,.05);
 --sh-2:0 1px 2px rgba(16,24,40,.05),0 8px 26px rgba(16,24,40,.08);
 --sh-3:0 18px 40px rgba(16,24,40,.16);
 --r:16px;--nav-h:54px;
}
body{background:var(--bg);color:var(--ink);line-height:1.5}
.wrap{max-width:1160px;margin:0 auto;padding:0 22px}

/* ── cards, doors, stats ── */
.body{padding-bottom:74px}
.grid{display:grid;gap:16px}
.g2{grid-template-columns:1fr 1fr}
.g3{grid-template-columns:repeat(3,1fr)}
.g4{grid-template-columns:repeat(4,1fr)}
@media(max-width:980px){.g3,.g4{grid-template-columns:1fr 1fr}}
@media(max-width:680px){.g2,.g3,.g4{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);
 box-shadow:var(--sh-1)}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-top:-34px;position:relative;z-index:2}
@media(max-width:1000px){.kpis{grid-template-columns:repeat(3,1fr)}}
@media(max-width:620px){.kpis{grid-template-columns:1fr 1fr}}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:17px 17px 15px;
 box-shadow:var(--sh-2);position:relative;overflow:hidden;
 transition:transform .2s cubic-bezier(.2,.7,.3,1),box-shadow .2s}
.kpi::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--green)}
.kpi:has(.n.red)::before{background:var(--red)}
.kpi:hover{transform:translateY(-3px);box-shadow:var(--sh-3)}
.kpi .n{font-size:29px;font-weight:800;line-height:1.05;letter-spacing:-.02em;
 font-variant-numeric:tabular-nums}
.kpi .n.red{color:var(--red)}.kpi .n.grn{color:var(--green-d)}
.kpi .l{font-size:12px;color:var(--muted);margin-top:5px;line-height:1.35}
.door{display:block;background:var(--card);border:1px solid var(--line);border-radius:18px;
 padding:24px 26px 22px;text-decoration:none;color:var(--ink);box-shadow:var(--sh-2);
 transition:transform .2s cubic-bezier(.2,.7,.3,1),box-shadow .2s;position:relative;overflow:hidden}
.door:hover{transform:translateY(-4px);box-shadow:var(--sh-3)}
.door::before{content:"";position:absolute;inset:0 auto 0 0;width:5px;background:var(--char)}
.door.a::before{background:var(--green)}
.door.b::before{background:var(--red)}
.door.c::before{background:#2f5fd0}
.door .kicker{font-size:10.5px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;
 color:var(--faint)}
.door .t{font-size:21px;font-weight:800;margin:7px 0 8px;letter-spacing:-.015em}
.door .d{font-size:13.5px;color:var(--mid);min-height:60px}
.door .figs{display:flex;gap:18px;margin:14px 0 4px;padding-top:13px;border-top:1px solid #eef1f5}
.door .figs .n{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.1}
.door .figs .l{font-size:11px;color:var(--faint);margin-top:2px}
.door .go{margin-top:12px;font-size:13px;font-weight:800;color:var(--green-d)}
.door:hover .go{text-decoration:underline}
h2.sec{font-size:13px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;
 color:var(--faint);margin:36px 0 13px}
.dnote{background:var(--card);border:1px solid var(--line);border-left:4px solid #b45309;
 border-radius:0 12px 12px 0;padding:15px 19px;margin-top:18px;font-size:13.5px;color:var(--mid);
 box-shadow:var(--sh-1)}
.dnote b{color:var(--ink)}
.foot{margin-top:38px;font-size:12px;color:var(--faint);border-top:1px solid var(--line);
 padding-top:15px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
@media(prefers-reduced-motion:reduce){
 *{animation:none!important;transition:none!important;scroll-behavior:auto!important}
 .door:hover{transform:none}
}
""".replace("IMG_HERO", A_HERO)


# ── chrome builders ──────────────────────────────────────────────────────────────────────────
NAV_ITEMS = [
    ("overview", "Overview", f"/m/{HUB}"),
    ("damages", "Damages", f"/m/{DAMAGES}"),
    ("analysis", "What the data tells us", f"/m/{ANALYSIS}"),
    ("reports", "Reports", f"/m/{REPORTS}"),
    ("reviews", "Genny &amp; CAT", f"/m/{REVIEWS}"),
]


def navbar(active=""):
    """The section navbar. `active` is one of the NAV_ITEMS keys, or "" for none.

    The Ask button opens the on-page widget when it is mounted and only falls back to her own
    page when it is not — following a link away from the page throws the open thread away.
    """
    links = "".join(
        f'<a href="{href}"{" class=\"on\"" if key == active else ""}>{label}</a>'
        for key, label, href in NAV_ITEMS)
    return (
        '<nav class="dnav"><div class="dnav-in">'
        f'<a class="dnav-brand" href="/m/{HUB}"><img src="{A_GENNY}" alt="Genny">'
        "<span>Genny&#8217;s <b>Damage Depot</b></span></a>"
        f'<div class="dnav-links">{links}</div>'
        '<button class="dnav-ask" type="button" '
        "onclick=\"if(window.gennyOpen){window.gennyOpen()}else{location.href='/m/" + BOT + "'}\">"
        '<span class="dot"></span>Ask Genny</button>'
        "</div></nav>")


def crumbs(*trail):
    """Breadcrumbs. Pass (label, href) tuples; the last item may be a bare string for "here"."""
    out = []
    for i, item in enumerate(trail):
        if i:
            out.append('<span class="sep">&rsaquo;</span>')
        if isinstance(item, tuple):
            out.append(f'<a href="{item[1]}">{item[0]}</a>')
        else:
            out.append(f'<span class="here">{item}</span>')
    return f'<div class="crumbs"><div class="crumbs-in">{"".join(out)}</div></div>'


def logos():
    return (f'<div class="logos"><img class="sy" src="{A_SYGMA}" alt="Sygma Solutions">'
            '<div class="mid">Service Damage Partnership</div>'
            f'<img class="cl" src="{A_CLANCY}" alt="The Clancy Group"></div>')


def hero(kicker="", title="", sub="", body="", extra=""):
    """THE hero. The same block on every page in the Depot.

    Pete, 31 Jul 2026: "i want this hero on all pages in the damage depot" — and then, when inner
    pages got a compact variant instead: "you havent copied the fucking hero, its not the same,
    doesnt say gennys damage depot, every damage oppurtunity". So the identity is fixed and
    identical everywhere: the works photograph, both logos, Genny with her nameplate, the name,
    and the strapline. What varies is the line UNDERNEATH, which says where you are.

      body   — the landing page's own introduction and chips, in place of the page line.
      extra  — anything that belongs inside the hero after it, e.g. the register's year tabs.
    """
    page = ""
    if kicker or title or sub:
        page = ('<div class="pageline">'
                + (f'<span class="kick">{kicker}</span>' if kicker else "")
                + (f'<div class="pt">{title}</div>' if title else "")
                + (f'<div class="ps">{sub}</div>' if sub else "")
                + "</div>")
    return (f'<div class="hero"><div class="hero-bg"></div><div class="hero-in">{logos()}'
            '<div class="hero-mid">'
            f'<div class="gennywrap"><img src="{A_GENNY}" alt="Genny">'
            '<div class="nameplate"><span class="live"></span>Genny</div></div>'
            '<div style="min-width:0">'
            '<h1>Genny&#8217;s <span class="g">Damage Depot</span></h1>'
            '<div class="strap">Where every damage becomes an opportunity.</div>'
            f"{body}{page}</div></div>{extra}</div></div>")


# Every caller that asked for a masthead gets the hero, unchanged at the call site.
def mast_compact(kicker, title, sub=""):
    return hero(kicker, title, sub)


def head(title, extra_css=""):
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        '<meta name="robots" content="noindex, nofollow">'
        f"<title>{title}</title><style>{CSS}{extra_css}</style></head><body>")


def foot(when, note="Prepared by Sygma Solutions."):
    return (f'<div class="foot"><span>Live from the Command Centre store &middot; {when}</span>'
            f"<span>{note}</span></div>")


TAIL = '<script src="/clancy/genny-widget.js?v=20260731c" defer></script>\n</body></html>'


# ── reskin ───────────────────────────────────────────────────────────────────────────────────
# The Genny & CAT section was written before this design system, in a navy palette of its own.
# Pete, 31 Jul 2026: "reskin". These maps move it onto Clancy's colours WITHOUT touching anything
# that carries meaning: status green, amber and the pale-red "hot" stat all stay exactly as they
# were, because there the colour IS the information.
#
# SVG_RESKIN runs FIRST and is deliberately narrower than SKIN: the same navy was used both for
# headings and for chart marks, and those need to go different ways. Headings become charcoal so
# they stay readable; single-series chart marks become chartreuse, matching the damages register.
SVG_RESKIN = {
    'fill="#1c2a6e"': 'fill="#97D700"',
    'stroke="#1c2a6e"': 'stroke="#97D700"',
    'fill="#26328f"': 'fill="#5f8b00"',
    'stroke="#26328f"': 'stroke="#5f8b00"',
}
SKIN = {
    "#1c2a6e": "#353E47",   # --navy: headings, mastheads, emphasis
    "#26328f": "#2f3841",   # hero gradient, middle stop
    "#31408f": "#454f5a",   # hero gradient, end stop
    "#f3f6fd": "#f6faf0",   # --tint: the pale panel background
    "#cdd8f0": "#dbe4c9",   # the border that goes with --tint
    "#dfe6ff": "#d3dae2",   # body text on the dark hero
    "#9fc0ff": "#97D700",   # hero kicker -> brand chartreuse
    "#c9d6ff": "#b9c1ca",   # stat labels on the dark hero
    "#c0281e": "#D50032",   # --red -> Clancy's own red
}


def reskin(html):
    """Move a page written in the old navy palette onto Clancy's colours.

    Case-insensitive on the hex, because the shells mix `#1C2A6E` and `#1c2a6e`. Status colours
    (#1e7a46 green, #b45309 amber, #ffb3ab) are deliberately absent from the maps: they encode
    state, not brand, and repainting them would change what the page says.
    """
    for old, new in SVG_RESKIN.items():
        html = re.sub(re.escape(old), new, html, flags=re.I)
    for old, new in SKIN.items():
        html = re.sub(re.escape(old), new, html, flags=re.I)
    return html


def inject(page, active="", trail=None):
    """Retrofit the section chrome onto a page that was NOT built from this design system.

    The Genny & CAT section is rendered from stored narrative shells, each a complete HTML
    document. Rather than rewrite that generator, the navbar and breadcrumbs are injected at its
    single publish() choke point, so every page in the section — including shells nobody has
    opened in months — carries the same navigation.

    Idempotent: a page that already has the navbar is returned untouched, so re-running a
    generator can never stack two navbars on one page.
    """
    if 'class="dnav"' in page:
        return page
    if trail is None:
        # Derive the leaf from the page's own <title>, minus the site suffix, so a shell that was
        # never given an explicit crumb still lands somewhere honest.
        m = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
        leaf = re.split(r"\s*[|·]\s*", H.unescape(m.group(1)).strip())[0] if m else "Genny & CAT"
        trail = [("Command Centre", "/"), ("Damage Depot", f"/m/{HUB}")]
        if leaf.lower() not in ("genny & cat", "genny and cat"):
            trail.append(("Genny &amp; CAT", f"/m/{REVIEWS}"))
        trail.append(H.escape(leaf))
    chrome = navbar(active) + "\n" + crumbs(*trail)
    if "</head>" in page:
        page = page.replace("</head>", f"<style>{CHROME}</style></head>", 1)
    else:
        chrome = f"<style>{CHROME}</style>" + chrome
    m = re.search(r"<body[^>]*>", page, re.I)
    if m:
        return page[:m.end()] + "\n" + chrome + page[m.end():]
    return chrome + page
