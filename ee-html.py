#!/usr/bin/env python3
"""ee-html.py — format an EE reply as a nicely-designed, SECTIONED HTML email, in the house style of
Pete's Morning Briefing: navy header card, an intro card, then a card per section with a coloured
header bar. Links are PROPER WORDS ([text](url)), £ figures bolded.

Standing rule (Pete, 2026-07-07): EE emails must look designed, broken into sections, not a wall of
prose. Write the reply with `## Section title` lines to split it; text before the first `##` is the
intro. Pete's Gmail signature is appended by gmail-api (footer), so this adds none — keep "Best, Pete".

Usage (library): m.to_html(text) → HTML body string.
"""
import re, html as _h

# ONE blue everywhere (header, section bars, buttons, links) — matches Pete's signature navy. (Pete, 2026-07-07)
NAVY = BLUE = "#003366"
INK, MUTE, PAGE, LINE = "#1e293b", "#c7d3e0", "#f4f6f8", "#e2e8f0"
FONT = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"

def _button(label, url):
    return (f'<a href="{url}" style="display:inline-block;padding:10px 20px;background:{BLUE};'
            f'color:#ffffff;border-radius:5px;text-decoration:none;font-weight:700;font-size:14px;">'
            f'{_h.escape(label)} &rarr;</a>')

def _inline(s):
    # [[label](url)] → a clear BUTTON ; [text](url) → an underlined worded link ; bare url → underlined link
    parts = re.split(r"(\[\[[^\]]+\]\(https?://[^)]+\)\]|\[[^\]]+\]\(https?://[^)]+\))", s)
    out = []
    for seg in parts:
        mb = re.match(r"\[\[([^\]]+)\]\((https?://[^)]+)\)\]", seg)
        mi = re.match(r"\[([^\]]+)\]\((https?://[^)]+)\)", seg)
        if mb:
            out.append(_button(mb.group(1), mb.group(2)))
        elif mi:
            out.append(f'<a href="{mi.group(2)}" style="color:{BLUE};font-weight:600;text-decoration:underline;">{_h.escape(mi.group(1))}</a>')
        else:
            t = _h.escape(seg)
            t = re.sub(r"(https?://[^\s<]+)", rf'<a href="\1" style="color:{BLUE};font-weight:600;text-decoration:underline;">\1</a>', t)
            t = re.sub(r"(£[\d,]+(?:\.\d+)?(?:\s*\+\s*VAT)?)", rf'<strong style="color:{NAVY};">\1</strong>', t)
            out.append(t)
    return "".join(out)

def _body(text):
    out, para, bullets = [], [], []
    def fp():
        if para:
            out.append(f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:{INK};">' + "<br>".join(_inline(l) for l in para) + "</p>"); para.clear()
    def fb():
        if bullets:
            lis = "".join(f'<li style="margin:0 0 7px;font-size:15px;line-height:1.55;color:{INK};">{_inline(l)}</li>' for l in bullets)
            out.append(f'<ul style="margin:2px 0 10px;padding-left:20px;">{lis}</ul>'); bullets.clear()
    for raw in (text or "").strip().splitlines():
        l = raw.rstrip()
        if not l.strip(): fb(); fp(); continue
        if re.match(r"^\s*\[\[[^\]]+\]\(https?://[^)]+\)\]\s*$", l):     # standalone button → its own block
            fb(); fp(); out.append(f'<div style="margin:4px 0 14px;">{_inline(l.strip())}</div>')
        elif l.lstrip().startswith("- "): fp(); bullets.append(l.lstrip()[2:])
        else: fb(); para.append(l)
    fb(); fp()
    return "".join(out)

def _card(inner, pad="18px 22px"):
    return f'<div style="background:#ffffff;border:1px solid {LINE};border-radius:10px;margin:0 0 14px;padding:{pad};">{inner}</div>'

def _section(title, content):
    header = f'<div style="background:{BLUE};color:#ffffff;font-size:13.5px;font-weight:700;padding:10px 16px;letter-spacing:.3px;">{_h.escape(title)}</div>'
    return (f'<div style="background:#ffffff;border:1px solid {LINE};border-radius:10px;margin:0 0 14px;overflow:hidden;">'
            f'{header}<div style="padding:14px 16px;">{_body(content)}</div></div>')

def to_html(text, title="Sygma Solutions", subtitle="Utility Location &amp; Avoidance Training"):
    # split on "## Section title" lines
    chunks = re.split(r"(?m)^\s*##\s+(.+?)\s*$", (text or "").strip())
    intro = chunks[0].strip()
    sections = [(chunks[i].strip(), chunks[i+1].strip()) for i in range(1, len(chunks)-1, 2)]
    parts = [f'<div style="background:{NAVY};color:#ffffff;border-radius:10px;padding:16px 20px;margin-bottom:14px;">'
             f'<div style="font-size:19px;font-weight:800;letter-spacing:.2px;">{title}</div>'
             f'<div style="color:{MUTE};font-size:13px;margin-top:3px;">{subtitle}</div></div>']
    if intro:
        parts.append(_card(_body(intro)))
    for t, c in sections:
        parts.append(_section(t, c))
    return f'<div style="background:{PAGE};padding:20px 0;{FONT}"><div style="max-width:640px;margin:0 auto;">{"".join(parts)}</div></div>'

if __name__ == "__main__":
    import sys
    print(to_html(sys.stdin.read()))
