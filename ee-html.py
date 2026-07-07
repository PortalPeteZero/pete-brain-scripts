#!/usr/bin/env python3
"""ee-html.py — render an EE reply as a CLEAN, SIMPLE email. No banners, no cards, no heavy template.

Pete, 2026-07-07: a normal email, not a designed newsletter. Just readable paragraphs, a bold line for
any `## heading`, simple bullets, worded underlined links, bold £ figures. Pete's Gmail signature is
appended by gmail-api (the footer), so this adds none — end the body with "Many thanks" / no name.

Usage: m.to_html(text) → simple HTML body string.
"""
import re, html as _h

NAVY, INK = "#003366", "#1a1a2e"
FONT = "font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:%s;" % INK

def _inline(s):
    # [[label](url)] and [text](url) both → a simple underlined worded link (no buttons)
    parts = re.split(r"(\[\[?[^\]]+\]\(https?://[^)]+\)\]?)", s)
    out = []
    for seg in parts:
        m = re.match(r"\[\[?([^\]]+)\]\((https?://[^)]+)\)\]?", seg)
        if m:
            out.append(f'<a href="{m.group(2)}" style="color:{NAVY};text-decoration:underline;">{_h.escape(m.group(1))}</a>')
        else:
            t = _h.escape(seg)
            t = re.sub(r"(https?://[^\s<]+)", rf'<a href="\1" style="color:{NAVY};text-decoration:underline;">\1</a>', t)
            t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
            t = re.sub(r"(£[\d,]+(?:\.\d+)?(?:\s*\+\s*VAT)?)", r"<strong>\1</strong>", t)
            out.append(t)
    return "".join(out)

def to_html(text, **_):
    out, para, bullets = [], [], []
    def fp():
        if para:
            out.append(f'<p style="margin:0 0 12px;">' + "<br>".join(_inline(l) for l in para) + "</p>"); para.clear()
    def fb():
        if bullets:
            lis = "".join(f"<li style='margin:0 0 4px;'>{_inline(l)}</li>" for l in bullets)
            out.append(f"<ul style='margin:0 0 12px;padding-left:22px;'>{lis}</ul>"); bullets.clear()
    for raw in (text or "").strip().splitlines():
        l = raw.rstrip()
        if not l.strip(): fb(); fp(); continue
        hm = re.match(r"^\s*##\s+(.+?)\s*$", l)
        if hm:
            fb(); fp(); out.append(f'<p style="margin:16px 0 4px;"><strong>{_h.escape(hm.group(1))}</strong></p>')
        elif l.lstrip().startswith("- "):
            fp(); bullets.append(l.lstrip()[2:])
        else:
            fb(); para.append(l)
    fb(); fp()
    return f'<div style="{FONT}">' + "".join(out) + "</div>"

if __name__ == "__main__":
    import sys
    print(to_html(sys.stdin.read()))
