#!/usr/bin/env python3
"""ee-html.py — wrap a plain-text EE reply into a well-presented, professional HTML email body.

Standing rule (Pete, 2026-07-07): ALL Enquiry-Engine outbound goes out as well-formatted HTML, never
raw plain text — it adds the professional touch. This is the one formatter so every send looks the same.

Converts:  blank-line blocks → <p> · "- " lines → a real <ul> · bare URLs → clickable <a> ·
£ figures + "£x + VAT" bolded for scannability · single newlines inside a block → <br>.
Wrapped in a clean, readable container. Pete's Gmail signature is appended by gmail-api (signature=True),
so this does NOT add a sign-off/signature — keep the "Best, Pete" line in the plain text.

Usage (library):
  import importlib.util; s=importlib.util.spec_from_file_location('ee_html','/tmp/pbs/ee-html.py')
  m=importlib.util.module_from_spec(s); s.loader.exec_module(m); html = m.to_html(plain_text)
Then send with gmail-api html=True (or pass the HTML string as the `html` arg).
"""
import re, html as _h

FONT = ("font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        "font-size:15px;line-height:1.55;color:#1a1a1a;")
LINK = "color:#c0392b;text-decoration:underline;"

def _inline(s):
    s = _h.escape(s)
    s = re.sub(r"(https?://[^\s<]+)", rf'<a href="\1" style="{LINK}">\1</a>', s)      # clickable links
    s = re.sub(r"(£[\d,]+(?:\.\d+)?(?:\s*\+\s*VAT)?)", r"<strong>\1</strong>", s)      # bold £ figures
    return s

def to_html(text):
    out, para, bullets = [], [], []
    def flush_para():
        if para:
            out.append(f"<p style='margin:0 0 12px;'>{'<br>'.join(_inline(l) for l in para)}</p>"); para.clear()
    def flush_bullets():
        if bullets:
            items = "".join(f"<li style='margin:3px 0;'>{_inline(l)}</li>" for l in bullets)
            out.append(f"<ul style='margin:10px 0;padding-left:22px;'>{items}</ul>"); bullets.clear()
    for raw in (text or "").strip().splitlines():
        l = raw.rstrip()
        if not l.strip():
            flush_bullets(); flush_para(); continue
        if l.lstrip().startswith("- "):
            flush_para(); bullets.append(l.lstrip()[2:])
        else:
            flush_bullets(); para.append(l)
    flush_bullets(); flush_para()
    return f"<div style=\"{FONT}\">" + "".join(out) + "</div>"

if __name__ == "__main__":
    import sys
    print(to_html(sys.stdin.read()))
