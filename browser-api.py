#!/usr/bin/env python3
"""Browser helper — Playwright-driven headless browser for scripted page checks.

Single canonical path for "Claude needs to actually look at a live page" work:
deploy verification, visual proof (desktop / mobile / dark screenshots), console
+ network error capture, rendered-string assertions, text extraction, and
HTML→PDF. Direct Playwright (no MCP) so every run is scriptable, reproducible,
and vault-versioned — the same helper-first pattern as gmail-api.py /
calendar-api.py / asana-api.py.

Engine:
  * Playwright (Python) installed in an ISOLATED venv OUTSIDE the vault:
        ~/.venvs/playwright/bin/python3
    Browser binaries live in the shared cache ~/Library/Caches/ms-playwright.
  * This file RE-EXECS itself under that venv automatically, so callers invoke
    it with plain `python3` exactly like every other helper — no venv path,
    no activation needed.
  * Setup / rebuild instructions: see [[browser-api-configuration]].

CLI usage:
  python3 browser-api.py audit <url> [--out DIR] [--wait MODE]
        # HTTP status + title + console errors/warnings + failed requests +
        # desktop/mobile/dark screenshots + JSON summary.
        # Exit 1 if HTTP>=400 or a page error fired. The deploy-verify workhorse.
  python3 browser-api.py screenshot <url> <out.png> [--viewport 1440x900]
        [--full-page] [--dark] [--wait networkidle|load|domcontentloaded]
  python3 browser-api.py check <url> --expect "str" [--expect ...] [--absent "str" ...]
        # assert strings present / absent in rendered HTML. Exit 1 on any failure.
        # Use after a deploy to prove the changed copy is actually live.
  python3 browser-api.py console <url>           # console + failed-network dump
  python3 browser-api.py text <url>              # visible innerText of <body>
  python3 browser-api.py pdf <url> <out.pdf> [--format A4]

Library usage (filename has a hyphen, so import via importlib):
  import importlib.util
  spec = importlib.util.spec_from_file_location(
      "browser_api", "Library/processes/scripts/browser-api.py")
  ba = importlib.util.module_from_spec(spec); spec.loader.exec_module(ba)
  res = ba.BrowserAPI().audit("https://sygma-solutions.com")
"""
import argparse
import json
import os
import sys
import time
from urllib.parse import urlparse

VENV_DIR = os.path.expanduser("~/.venvs/playwright")
VENV_PY = os.path.join(VENV_DIR, "bin", "python3")

# --- Re-exec under the Playwright venv so callers can use plain `python3` ----
# A venv's bin/python3 is a symlink to the base interpreter, so comparing
# executables via realpath collapses them — detect the venv by sys.prefix instead.
try:
    from playwright.sync_api import sync_playwright, Error as PWError
except ModuleNotFoundError:
    _in_venv = os.path.realpath(sys.prefix) == os.path.realpath(VENV_DIR)
    if not _in_venv and os.path.exists(VENV_PY):
        os.execv(VENV_PY, [VENV_PY, *sys.argv])
    sys.stderr.write(
        "browser-api.py: playwright not importable and the venv is missing.\n"
        f"  expected venv: {VENV_PY}\n"
        "  rebuild:\n"
        "    python3 -m venv ~/.venvs/playwright\n"
        "    ~/.venvs/playwright/bin/python3 -m pip install playwright\n"
        "    ~/.venvs/playwright/bin/python3 -m playwright install chromium\n"
        "  docs: Library/processes/browser-api-configuration.md\n"
    )
    sys.exit(3)


DEFAULT_TIMEOUT = 45000
DESKTOP_VP = {"width": 1440, "height": 900}
MOBILE_VP = {"width": 390, "height": 844}


def _host(url):
    return urlparse(url).netloc or "page"


class BrowserAPI:
    """Thin, reusable wrapper over Playwright's sync API. Headless, Chromium."""

    def __init__(self, timeout=DEFAULT_TIMEOUT):
        self.timeout = timeout

    def _collect(self, page):
        """Attach console / pageerror / requestfailed listeners; return buckets."""
        b = {"console_errors": [], "console_warnings": [], "page_errors": [], "failed_requests": []}
        page.on("console", lambda m: (
            b["console_errors"].append(m.text) if m.type == "error"
            else b["console_warnings"].append(m.text) if m.type == "warning"
            else None))
        page.on("pageerror", lambda e: b["page_errors"].append(str(e)))
        page.on("requestfailed", lambda r: b["failed_requests"].append(
            f"{r.method} {r.url} :: {r.failure or ''}"))
        return b

    def _goto(self, page, url, wait="networkidle"):
        # networkidle can hang on sites with long-poll / analytics beacons;
        # fall back to a plain load so the helper never wedges.
        try:
            return page.goto(url, wait_until=wait, timeout=self.timeout)
        except Exception:
            return page.goto(url, wait_until="load", timeout=self.timeout)

    def audit(self, url, out_dir="/tmp", wait="networkidle"):
        host = _host(url)
        os.makedirs(out_dir, exist_ok=True)
        shots = {}
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport=DESKTOP_VP).new_page()
            buckets = self._collect(page)
            t0 = time.time()
            resp = self._goto(page, url, wait)
            load_ms = int((time.time() - t0) * 1000)
            status = resp.status if resp else None
            title = page.title()
            d = os.path.join(out_dir, f"{host}-desktop.png"); page.screenshot(path=d); shots["desktop"] = d
            page.set_viewport_size(MOBILE_VP)
            m = os.path.join(out_dir, f"{host}-mobile.png"); page.screenshot(path=m); shots["mobile"] = m
            browser.close()
            # dark mode needs its own context (color scheme is context-level)
            browser = p.chromium.launch()
            page = browser.new_context(viewport=DESKTOP_VP, color_scheme="dark").new_page()
            self._goto(page, url, wait)
            k = os.path.join(out_dir, f"{host}-dark.png"); page.screenshot(path=k); shots["dark"] = k
            browser.close()
        return {
            "url": url, "http_status": status, "title": title, "load_ms": load_ms,
            **buckets, "screenshots": shots,
            "ok": bool(status and status < 400) and not buckets["page_errors"],
        }

    def screenshot(self, url, out, viewport=None, full_page=False, dark=False, wait="networkidle"):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(
                viewport=viewport or DESKTOP_VP,
                color_scheme="dark" if dark else "light").new_page()
            self._goto(page, url, wait)
            page.screenshot(path=out, full_page=full_page)
            browser.close()
        return out

    def check(self, url, expect=None, absent=None, wait="networkidle"):
        expect, absent = expect or [], absent or []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            resp = self._goto(page, url, wait)
            status = resp.status if resp else None
            html = page.content()
            browser.close()
        results = [{"assert": "present", "needle": s, "pass": s in html} for s in expect]
        results += [{"assert": "absent", "needle": s, "pass": s not in html} for s in absent]
        return {"url": url, "http_status": status,
                "all_pass": all(r["pass"] for r in results) if results else True,
                "results": results}

    def console(self, url, wait="networkidle"):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            buckets = self._collect(page)
            resp = self._goto(page, url, wait)
            status = resp.status if resp else None
            browser.close()
        return {"url": url, "http_status": status, **buckets}

    def text(self, url, wait="networkidle"):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            self._goto(page, url, wait)
            body = page.inner_text("body")
            browser.close()
        return body

    def pdf(self, url, out, fmt="A4", wait="networkidle"):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            self._goto(page, url, wait)
            page.pdf(path=out, format=fmt, print_background=True)
            browser.close()
        return out


def _parse_vp(s):
    w, h = s.lower().split("x")
    return {"width": int(w), "height": int(h)}


def main():
    ap = argparse.ArgumentParser(description="Playwright headless browser helper (direct, no MCP).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit"); a.add_argument("url"); a.add_argument("--out", default="/tmp"); a.add_argument("--wait", default="networkidle")
    s = sub.add_parser("screenshot"); s.add_argument("url"); s.add_argument("out")
    s.add_argument("--viewport"); s.add_argument("--full-page", action="store_true"); s.add_argument("--dark", action="store_true"); s.add_argument("--wait", default="networkidle")
    c = sub.add_parser("check"); c.add_argument("url"); c.add_argument("--expect", action="append"); c.add_argument("--absent", action="append"); c.add_argument("--wait", default="networkidle")
    co = sub.add_parser("console"); co.add_argument("url"); co.add_argument("--wait", default="networkidle")
    tx = sub.add_parser("text"); tx.add_argument("url"); tx.add_argument("--wait", default="networkidle")
    pd = sub.add_parser("pdf"); pd.add_argument("url"); pd.add_argument("out"); pd.add_argument("--format", default="A4"); pd.add_argument("--wait", default="networkidle")

    args = ap.parse_args()
    b = BrowserAPI()
    try:
        if args.cmd == "audit":
            res = b.audit(args.url, out_dir=args.out, wait=args.wait)
            print(json.dumps(res, indent=2))
            sys.exit(0 if res["ok"] else 1)
        if args.cmd == "screenshot":
            out = b.screenshot(args.url, args.out, viewport=_parse_vp(args.viewport) if args.viewport else None,
                               full_page=args.full_page, dark=args.dark, wait=args.wait)
            print(out); return
        if args.cmd == "check":
            res = b.check(args.url, expect=args.expect, absent=args.absent, wait=args.wait)
            print(json.dumps(res, indent=2))
            sys.exit(0 if res["all_pass"] else 1)
        if args.cmd == "console":
            print(json.dumps(b.console(args.url, wait=args.wait), indent=2)); return
        if args.cmd == "text":
            print(b.text(args.url, wait=args.wait)); return
        if args.cmd == "pdf":
            print(b.pdf(args.url, args.out, fmt=args.format, wait=args.wait)); return
    except PWError as e:
        # Site genuinely unreachable / render failed — report cleanly, never a traceback.
        msg = str(e).splitlines()[0]
        if args.cmd in ("audit", "check", "console"):
            print(json.dumps({"url": args.url, "ok": False, "error": msg}, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
