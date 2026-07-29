#!/usr/bin/env python3
"""md_html.py -- shared Obsidian-flavoured markdown -> clean inline-styled HTML.

WHY THIS EXISTS (plan 'make the CC SEO report DB-driven', gap 2): report generators read
owner-facing prose from vault_notes, whose bodies carry [[wikilinks]] and > [!type] callouts.
Rendered naively, owners see raw `[[...]]` and broken blockquotes. This is the ONE sanctioned
converter so every generator renders notes the same way.

Scope (deliberately small): headings, bold/italic, [[wikilink]] / [[target|label]] -> plain
styled text (reports are outside the vault; links would 404), > [!type] callouts -> styled
boxes, bullet/numbered lists, tables, paragraphs. NOT a full markdown engine.
"""
import re, html as _html

_CALLOUT_COLORS = {
    "important": ("#8a4b08", "#fdf3e7"), "warning": ("#8a4b08", "#fdf3e7"),
    "danger": ("#b3261e", "#fbeaea"), "tip": ("#1a6b3c", "#e9f5ec"),
    "success": ("#1a6b3c", "#e9f5ec"), "info": ("#1a3c5e", "#eef3f8"),
    "question": ("#5b5470", "#f0edf7"), "todo": ("#1a3c5e", "#eef3f8"),
}

def _inline(s):
    s = _html.escape(s, quote=False)
    s = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r'<span style="color:#1a3c5e;font-weight:600">\2</span>', s)
    s = re.sub(r"\[\[([^\]]*)\]\]", lambda m: '<span style="color:#1a3c5e;font-weight:600">%s</span>' % m.group(1).split("/")[-1], s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r'<code style="background:#f0f2f4;padding:0 4px;border-radius:3px;font-size:12px">\1</code>', s)
    s = re.sub(r"==([^=]+)==", r'<mark>\1</mark>', s)
    return s

def md_to_html(md):
    out, lines = [], md.splitlines()
    i, in_list, in_table, callout = 0, False, False, None

    def close_list():
        nonlocal in_list
        if in_list: out.append("</ul>"); in_list = False

    def close_callout():
        nonlocal callout
        if callout: out.append("</div>"); callout = None

    while i < len(lines):
        ln = lines[i]
        m = re.match(r">\s*\[!(\w+)\]\s*(.*)", ln)
        if m:
            close_list(); close_callout()
            kind = m.group(1).lower(); ink, bg = _CALLOUT_COLORS.get(kind, ("#1a3c5e", "#eef3f8"))
            out.append(f'<div style="background:{bg};border-left:4px solid {ink};border-radius:6px;padding:10px 14px;margin:10px 0;font-size:13px">')
            if m.group(2).strip(): out.append(f'<div style="font-weight:700;color:{ink};margin-bottom:4px">{_inline(m.group(2).strip())}</div>')
            callout = kind; i += 1; continue
        if callout is not None:
            if ln.startswith(">"):
                body = ln.lstrip("> ").rstrip()
                if body: out.append(f"<div>{_inline(body)}</div>")
                i += 1; continue
            close_callout()
        if re.match(r"\s*\|.*\|\s*$", ln):
            if re.match(r"\s*\|[\s:|-]+\|\s*$", ln): i += 1; continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if not in_table:
                close_list(); out.append('<table style="width:100%;border-collapse:collapse;font-size:13px;margin:8px 0">')
                out.append("<tr>" + "".join(f'<th style="background:#1a3c5e;color:#fff;padding:5px 8px;text-align:left">{_inline(c)}</th>' for c in cells) + "</tr>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f'<td style="padding:4px 8px;border-bottom:1px solid #e3e7ea">{_inline(c)}</td>' for c in cells) + "</tr>")
            i += 1; continue
        elif in_table:
            out.append("</table>"); in_table = False
        m = re.match(r"(#{1,4})\s+(.*)", ln)
        if m:
            close_list(); close_callout()
            lvl = min(len(m.group(1)) + 1, 4)
            size = {2: 17, 3: 15, 4: 13}.get(lvl, 13)
            out.append(f'<h{lvl} style="font-size:{size}px;color:#1a3c5e;margin:20px 0 8px">{_inline(m.group(2))}</h{lvl}>')
            i += 1; continue
        m = re.match(r"\s*[-*]\s+(.*)", ln)
        if m:
            close_callout()
            if not in_list: out.append('<ul style="margin:6px 0 6px 20px;font-size:13px">'); in_list = True
            out.append(f"<li>{_inline(m.group(1))}</li>"); i += 1; continue
        if ln.strip() == "":
            close_list(); i += 1; continue
        close_list()
        out.append(f'<p style="font-size:13px;margin:6px 0">{_inline(ln.strip())}</p>')
        i += 1
    close_list(); close_callout()
    if in_table: out.append("</table>")
    return "\n".join(out)

if __name__ == "__main__":
    import sys
    print(md_to_html(open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()))
