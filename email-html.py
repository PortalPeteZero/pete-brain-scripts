#!/usr/bin/env python3
"""email-html.py — the ONE house renderer for outbound email. Plain text in, styled HTML out.

WHY THIS EXISTS (Pete, 7 Aug 2026): "every time you send an email, do it in this nice HTML format."
He'd just seen a hand-built email to Xhale — section headings with a rule, monospace panels for
literal blocks, a scannable labelled list — and wanted it as the default rather than a one-off.

Wired in at ONE place: gmail-api.py's `_apply_signature` choke point, which `send()` and
`create_draft()` both call and `reply_thread()` funnels through. So every path (ee-send, triage, the
CLI, a library call, a cron) is covered without a single call site knowing. Same argument the
signature code already makes.

ONLY PLAIN TEXT IS TOUCHED. A body that is already HTML passes through untouched, so the 46 report
generators that build their own markup are unaffected.

BACKWARDS COMPATIBILITY IS THE WHOLE DESIGN. On 2026-07-07 Pete rejected a heavy template for
enquiry replies ("a normal email, not a designed newsletter"). Every EE reply ever sent uses `## `
for its headings, so `## ` still renders as exactly what it rendered as then: a bold line. The
bigger treatment is opt-in on `# `. Measured against real approved quotes in Sent before shipping;
the only change to a live EE quote was numbered lists becoming real lists.

Markup understood (a deliberately small set, not a markdown engine):
    # Heading        uppercase navy section heading with a rule under it
    ## Heading       a bold line               <- unchanged since 2026-07-07, EE depends on it
    ### Label        monospace navy label, for a scannable list of named things
    > quoted         tinted panel with a navy left rule
    ```fenced```     monospace panel, newlines preserved (4-space indent does the same)
    - item           bullets
    1. item          numbered list
    **bold**  `code`  [label](url)  bare urls  £1,234  ->  inline styling

Usage:  import email_html; email_html.to_html(text)      or:  email-html.py < body.txt
"""
import re
import html as _h

# Sygma navy, taken from Pete's own Gmail signature so body and sign-off are one design.
# INK #1a1a2e and NAVY #003366 are also the two legacy markers ee-send.py sniffs to prove a reply
# was house-formatted, so keeping these exact values keeps that check passing.
NAVY, INK, PANEL, RULE, MUTED = "#003366", "#1a1a2e", "#f4f6f8", "#e1e6ea", "#5c6b7a"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,Helvetica,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Courier New',monospace"
BODY = f"font-family:{SANS};font-size:15px;line-height:1.62;color:{INK};"

# The honest marker. te-log.py used to prove formatting by sniffing "margin:0 0 12px;" — an
# incidental style value that any restyle would silently break. This says what it means.
MARKER = "<!--house-format-v2-->"

class VoiceViolation(ValueError):
    """A forbidden dash reached the send path. Deliberately NOT a plain Exception: gmail-api's
    _house_format swallows errors so a formatting problem can never block a send, and this one
    MUST get through that.

    `is_voice_violation` exists because every tool in this repo loads its neighbours BY PATH with
    importlib, and two such loads produce two distinct module objects — so two distinct copies of
    this class, and `isinstance` across them is False. ee-html loading email-html separately from
    gmail-api is exactly that case. Catchers must duck-type on the attribute, never on identity:

        except Exception as e:
            if getattr(e, "is_voice_violation", False): raise
    """
    is_voice_violation = True


# The three characters [[voice-principles]] forbids in Pete's voice. Note " -- " is the SPACED
# double dash; a bare "--" is a CLI flag and must not trip.
_FORBIDDEN = (("—", "em dash"), ("–", "en dash"), (" -- ", "spaced double dash"))
_CODE_REGIONS = re.compile(r"```.*?```|^ {4,}\S.*$|`[^`]+`", re.S | re.M)


def assert_voice(text):
    """Refuse a body carrying an em dash, en dash or spaced double dash.

    [[2026-05-24-outbound-html-emails-pre-flight-dash-grep]] put this check in each script that
    built a draft, which meant every new script had to remember it. This is the one place that
    sees every plain-text body on its way out, so it belongs here.

    Code is exempt -- a shell command or a snippet is not Pete's voice, and a literal " -- " in a
    CLI example is legitimate. Fenced blocks, 4-space indents and inline backticks are stripped
    before the scan.

    MEASURED BEFORE IT WAS ARMED (7 Aug 2026, 250 sent messages over 90 days): 34 carried a
    forbidden dash and every single one was an HTML body, which this path never touches. Block
    rate against real approved plain-text output: 0. A fail-closed rule that stops Pete's own
    sends is worse than no rule, so the scope was chosen from that measurement, not from taste.
    """
    prose = _CODE_REGIONS.sub(" ", text or "")
    hits = [(name, prose.count(ch)) for ch, name in _FORBIDDEN if ch in prose]
    if not hits:
        return
    detail = ", ".join(f"{n} x{c}" for n, c in hits)
    ctx = []
    for ch, name in _FORBIDDEN:
        i = prose.find(ch)
        if i >= 0:
            ctx.append("    ..." + " ".join(prose[max(0, i - 60):i + 60].split()) + "...")
    raise VoiceViolation(
        f"voice-principles: {detail} in the email body. Pete does not use these.\n"
        + "\n".join(ctx)
        + "\n    Rewrite with a comma, a full stop or brackets. Code samples are already exempt."
    )


_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[\[?([^\]]+)\]\((https?://[^)]+)\)\]?")
_URL = re.compile(r"(?<![\"'>=])(https?://[^\s<)]+)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MONEY = re.compile(r"(£[\d,]+(?:\.\d+)?(?:\s*\+\s*VAT)?)")


def _chip(t):
    return (f'<span style="font-family:{MONO};font-size:13px;background:{PANEL};'
            f'padding:1px 5px;border-radius:2px;">{t}</span>')


def _inline(s):
    """Inline styling. Order matters: backticked code is pulled out FIRST so a URL or a ** pair
    inside it is left alone, and links are resolved before bare-URL autolinking so a worded link
    is not linkified twice."""
    held = []

    def hold(m):
        held.append(_chip(_h.escape(m.group(1))))
        return f"\x00{len(held) - 1}\x00"

    s = _CODE.sub(hold, s)

    out = []
    for seg in re.split(r"(\[\[?[^\]]+\]\(https?://[^)]+\)\]?)", s):
        m = _LINK.match(seg)
        if m:
            out.append(f'<a href="{_h.escape(m.group(2), quote=True)}" '
                       f'style="color:{NAVY};text-decoration:underline;">{_h.escape(m.group(1))}</a>')
            continue
        t = _h.escape(seg)
        t = _URL.sub(rf'<a href="\1" style="color:{NAVY};text-decoration:underline;">\1</a>', t)
        t = _BOLD.sub(r"<strong>\1</strong>", t)
        t = _MONEY.sub(r"<strong>\1</strong>", t)
        out.append(t)
    t = "".join(out)
    return re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], t)


def _panel(inner, style, bottom=16):
    """Left-ruled tint panel. Table-based because Outlook drops background-color on a bare div."""
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="border-collapse:collapse;margin:0 0 {bottom}px;"><tr>'
            f'<td style="background:{PANEL};border-left:3px solid {NAVY};padding:14px 18px;{style}">'
            f"{inner}</td></tr></table>")


def to_html(text, check=True, **_):
    """Render plain text to the house style. `check=True` (the default) refuses a body carrying a
    forbidden dash -- see assert_voice. Pass check=False only to render a preview of something you
    are not about to send."""
    if check:
        assert_voice(text)
    out = []
    para, bullets, numbers, quote, pre = [], [], [], [], []
    fenced = False
    # a ### block gets a rule above it to separate it from the previous one, but not when it is the
    # first thing under a heading — a rule directly beneath the heading's own rule looks like a fault
    label_open = False
    fresh_section = True

    def flush_para():
        if para:
            out.append(f'<p style="margin:0 0 16px;">' + "<br>".join(_inline(l) for l in para) + "</p>")
            para.clear()

    def flush_bullets():
        if bullets:
            lis = "".join(f'<li style="margin:0 0 5px;">{_inline(l)}</li>' for l in bullets)
            out.append(f'<ul style="margin:0 0 16px;padding-left:22px;">{lis}</ul>')
            bullets.clear()

    def flush_numbers():
        if numbers:
            lis = "".join(f'<li style="margin:0 0 5px;">{_inline(l)}</li>' for l in numbers)
            out.append(f'<ol style="margin:0 0 16px;padding-left:22px;">{lis}</ol>')
            numbers.clear()

    def flush_quote():
        if quote:
            out.append(_panel("<br>".join(_inline(l) for l in quote), BODY))
            quote.clear()

    def flush_pre():
        if pre:
            while pre and not pre[-1].strip():
                pre.pop()
            body = "<br>".join(_h.escape(l).replace("  ", "&nbsp;&nbsp;") for l in pre)
            out.append(_panel(body, f"font-family:{MONO};font-size:13px;line-height:1.7;color:{INK};"))
            pre.clear()

    def flush_all():
        flush_pre(); flush_quote(); flush_bullets(); flush_numbers(); flush_para()

    for raw in (text or "").strip().splitlines():
        line = raw.rstrip()

        if line.strip().startswith("```"):
            if fenced:
                flush_pre()
            else:
                flush_quote(); flush_bullets(); flush_numbers(); flush_para()
            fenced = not fenced
            continue
        if fenced:
            pre.append(raw)
            continue

        if not line.strip():
            flush_all()
            continue

        # 4-space indent is a literal block too, but only when it is not a wrapped list item
        if re.match(r"^ {4,}\S", raw) and not bullets and not numbers:
            flush_quote(); flush_para()
            pre.append(raw[4:] if raw.startswith("    ") else raw.lstrip())
            continue
        flush_pre()

        m = re.match(r"^\s*(#{1,3})\s+(.+?)\s*$", line)
        if m:
            flush_all()
            level, txt = len(m.group(1)), m.group(2)
            if level == 1:
                out.append(f'<p style="margin:34px 0 5px;font-family:{SANS};font-size:12px;'
                           f'font-weight:700;letter-spacing:.09em;text-transform:uppercase;'
                           f'color:{NAVY};">{_h.escape(txt)}</p>'
                           f'<div style="font-size:0;line-height:0;height:2px;background:{NAVY};'
                           f'width:34px;margin:0 0 17px;">&nbsp;</div>')
                label_open, fresh_section = False, True
            elif level == 2:
                out.append(f'<p style="margin:20px 0 6px;"><strong>{_inline(txt)}</strong></p>')
                fresh_section = False
            else:
                rule = ("" if fresh_section and not label_open
                        else f"border-top:1px solid {RULE};padding-top:16px;")
                out.append(f'<p style="margin:0 0 5px;{rule}font-family:{MONO};font-size:13px;'
                           f'font-weight:700;color:{NAVY};">{_h.escape(txt)}</p>')
                label_open, fresh_section = True, False
            continue

        if line.lstrip().startswith(("> ", ">")):
            flush_bullets(); flush_numbers(); flush_para()
            quote.append(re.sub(r"^\s*>\s?", "", line))
            continue

        b = re.match(r"^\s*[-*]\s+(.*)$", line)
        if b:
            flush_quote(); flush_numbers(); flush_para()
            bullets.append(b.group(1))
            continue

        n = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if n:
            flush_quote(); flush_bullets(); flush_para()
            numbers.append(n.group(1))
            continue

        flush_quote(); flush_bullets(); flush_numbers()
        para.append(line)

    flush_all()
    return f'{MARKER}<div style="{BODY}">' + "".join(out) + "</div>"


def looks_formatted(html):
    """True if this HTML came from the house renderer. Checked by te-log.py and ee-send.py.
    Accepts the pre-Aug-2026 ee-html signal too, so historical threads still validate."""
    h = html or ""
    return MARKER in h or "margin:0 0 12px;" in h


if __name__ == "__main__":
    import sys
    print(to_html(sys.stdin.read()))
