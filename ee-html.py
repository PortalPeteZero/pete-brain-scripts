#!/usr/bin/env python3
"""ee-html.py — enquiry-engine reply renderer. Now a thin delegate to the house renderer.

HISTORY, because the reversal matters. On 2026-07-07 Pete rejected a heavy template for enquiry
replies: "a normal email, not a designed newsletter. No banners, no cards." This file held that
plain renderer. On 2026-08-07, having seen the house style on a long technical email, he asked for
one format everywhere and explicitly chose to bring EE quotes in with it.

So the rendering now lives in email-html.py and this file just forwards. Kept as a file rather than
deleted because ee-send.py loads it BY PATH (`_load("eeh", f"{VAULT}/ee-html.py")`), as does the
te-log format check, and a dangling path is a silent breakage in the send path.

The July decision is not fully undone, and deliberately so. Every EE reply ever sent uses `## ` for
its headings, and `## ` still renders as exactly what it rendered as then — a bold line, no banner.
The heavier treatment is opt-in on `# `. Measured before shipping against the real approved quotes
in Sent: the only visible change to a live quote was numbered lists becoming real numbered lists.
"""
import importlib.util
import os

_HOUSE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email-html.py")

_spec = importlib.util.spec_from_file_location("email_html", _HOUSE)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

NAVY, INK = _m.NAVY, _m.INK
FONT = _m.BODY
MARKER = _m.MARKER


def to_html(text, **kw):
    """Render an EE reply. Signature unchanged from the 2026-07 version so every caller still works."""
    return _m.to_html(text, **kw)


def looks_formatted(html):
    """True if this HTML came from the house renderer (or the pre-Aug-2026 EE one)."""
    return _m.looks_formatted(html)


if __name__ == "__main__":
    import sys
    print(to_html(sys.stdin.read()))
