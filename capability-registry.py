#!/usr/bin/env python3
"""
capability-registry.py — §C of the property-state system plan.

Builds a machine-maintained capability inventory and writes it into connections.md
(between <!-- CAPABILITY-REGISTRY --> markers; the rest of the file is human-owned, untouched).

1. Inventories secrets/        -> each credential file, inferred service.
2. Inventories scripts/*-api.py -> each helper + its one-line purpose (docstring).
3. Scans Properties/ + Library/processes/ for credentials living OUTSIDE secrets/
   (the IONOS-in-a-README failure) and flags them (redacted) to be moved.

Safe: writes only between its markers, snapshots connections.md first, body-preserving, dry-run default.
Usage: python3 capability-registry.py [--apply]
"""
import os, re, sys, glob, shutil
from datetime import datetime, timezone
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
PROC = os.path.join(VAULT, "Library/processes")
SECRETS = os.path.join(PROC, "secrets")
SCRIPTS = os.path.join(PROC, "scripts")
CONN = os.path.join(PROC, "connections.md")
BACKUP = "/tmp/capability-registry-backup"
APPLY = "--apply" in sys.argv
MS, ME = "<!-- CAPABILITY-REGISTRY:START — machine-maintained by capability-registry.py, do not hand-edit -->", "<!-- CAPABILITY-REGISTRY:END -->"

def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

# service inference from a secrets filename
def infer_service(fn):
    f = fn.lower()
    for key, name in [
        ("github", "GitHub"), ("vercel", "Vercel"), ("stripe", "Stripe"), ("supabase", "Supabase"),
        ("canary-detect", "Canary Detect site"), ("canary-report", "CD Leak Report"),
        ("lanzarotelates", "Lanzarote Lates"), ("oconnors", "O'Connor's"), ("passion-fit", "Passion Fit"),
        ("garmin", "Garmin"), ("google-ads", "Google Ads"), ("google-seo", "Google SEO (GSC/GA4/GTM SA)"),
        ("google-maps", "Google Maps/Places"), ("geocod", "Google Geocoding"), ("vision", "Cloud Vision"),
        ("ionos", "IONOS DNS"), ("godaddy", "GoDaddy DNS"), ("cloudflare", "Cloudflare"),
        ("ahrefs", "Ahrefs"), ("surfer", "Surfer"), ("sentry", "Sentry"), ("xero", "Xero"),
        ("odoo", "Odoo"), ("soldo", "Soldo"), ("jotform", "JotForm"), ("asana", "Asana"),
        ("pagespeed", "PageSpeed/CrUX"), ("anthropic", "Anthropic API"), ("apple", "Apple Wallet/PassKit"),
        ("passkit", "PassKit"), ("xhale", "Xhale"), ("resend", "Resend email"), ("cookieyes", "CookieYes"),
        ("gtm", "Google Tag Manager"), ("ga4", "GA4"), ("indexnow", "IndexNow"),
    ]:
        if f.startswith(key) or key in f:
            return name
    return "?"

def inventory_secrets():
    out = []
    for fn in sorted(os.listdir(SECRETS)):
        full = os.path.join(SECRETS, fn)
        if os.path.isdir(full):
            out.append((fn + "/", infer_service(fn), "dir"))
        else:
            out.append((fn, infer_service(fn), ""))
    return out

def inventory_helpers():
    out = []
    for p in sorted(glob.glob(os.path.join(SCRIPTS, "*-api.py"))):
        name = os.path.basename(p)
        purpose = ""
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
            m = re.search(r'"""(.*?)"""', txt, re.S)
            if m:
                for line in m.group(1).strip().splitlines():
                    if line.strip():
                        purpose = line.strip()
                        break
        except Exception:
            pass
        out.append((name, purpose[:90]))
    return out

# stray-credential scan
PATTERNS = [
    ("GitHub PAT", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("Vercel token", re.compile(r"vcp_[A-Za-z0-9]{20,}")),
    ("Supabase PAT", re.compile(r"sbp_[A-Za-z0-9]{20,}")),
    ("Stripe live", re.compile(r"(sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("Google API key", re.compile(r"AIza[A-Za-z0-9_\-]{35}")),
    ("JWT (supabase key?)", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
]
def scan_stray():
    hits = []
    roots = [os.path.join(VAULT, "Properties"), PROC]
    for root in roots:
        for dp, _, files in os.walk(root):
            if "/secrets" in dp:
                continue
            for fn in files:
                if not fn.endswith(".md") and not fn.endswith(".json"):
                    continue
                fp = os.path.join(dp, fn)
                if os.path.abspath(fp) == os.path.abspath(CONN):
                    continue
                try:
                    for i, line in enumerate(open(fp, encoding="utf-8", errors="ignore"), 1):
                        for label, pat in PATTERNS:
                            m = pat.search(line)
                            if m:
                                tok = m.group(0)
                                red = tok[:6] + "…" + tok[-3:]
                                rel = os.path.relpath(fp, VAULT)
                                hits.append((label, rel, i, red))
                except Exception:
                    pass
    return hits

import urllib.request, urllib.error
def _ping(url, headers, ok=(200,)):
    """Read-only liveness ping. True=live, False=dead/unreachable."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=12) as r:
            return r.status in ok
    except urllib.error.HTTPError as e:
        return e.code in ok
    except Exception:
        return False

def liveness():
    """Liveness-test the main token keys against their OWN service, read-only (no key-spray)."""
    def rd(p):
        try: return open(os.path.join(SECRETS, p)).read().strip()
        except Exception: return ""
    GH = rd("github-pat")
    VC = rd("vercel-token")
    tests = [
        ("GitHub PAT", _ping("https://api.github.com/rate_limit", {"Authorization": f"token {GH}", "User-Agent": "cap-reg"})),
        ("Vercel token", _ping("https://api.vercel.com/v2/user", {"Authorization": f"Bearer {VC}"})),
        ("Asana PAT", _ping("https://app.asana.com/api/1.0/users/me", {"Authorization": f"Bearer {rd('asana-pat')}"})),
        ("Ahrefs token", _ping("https://api.ahrefs.com/v3/site-explorer/domain-rating?target=ahrefs.com&date=2026-06-06", {"Authorization": f"Bearer {rd('ahrefs-token')}"})),
    ]
    return tests

def build_block():
    secs = inventory_secrets()
    helps = inventory_helpers()
    stray = scan_stray()
    live = liveness()
    L = [MS, "## Capability registry", "", f"_Auto-generated {now()} by `capability-registry.py`. The lists below are machine-owned._", ""]
    L.append(f"**Credentials in `secrets/` ({len(secs)})** — the one canonical home:")
    L.append("")
    for fn, svc, kind in secs:
        L.append(f"- `{fn}` — {svc}")
    L.append("")
    L.append(f"**API helpers (`scripts/*-api.py`, {len(helps)})**:")
    L.append("")
    for name, purpose in helps:
        L.append(f"- `{name}` — {purpose}")
    L.append("")
    L.append("**Key liveness** (read-only ping against each key's OWN service — a dead key is flagged, not assumed live):")
    L.append("")
    for label, ok in live:
        L.append(f"- {'🟢 live' if ok else '🔴 DEAD — investigate'} · {label}")
    L.append("  _(other keys are inventory-only — per-service liveness pings extend here as needed.)_")
    L.append("")
    if stray:
        L.append(f"**⚠️ Credentials found OUTSIDE `secrets/` ({len(stray)}) — review + move (a credential isn't \"saved\" until it's in `secrets/` + here):**")
        L.append("")
        for label, rel, ln, red in stray:
            L.append(f"- `{label}` `{red}` — [[{rel}]] line {ln}")
        L.append("")
    else:
        L.append("**No stray credentials outside `secrets/`.** ✅")
        L.append("")
    L.append(ME)
    return "\n".join(L)

def write_into_conn(block):
    raw = open(CONN, encoding="utf-8").read()
    if MS in raw and ME in raw:
        pre, post = raw[:raw.index(MS)], raw[raw.index(ME) + len(ME):]
        new = pre + block + post
        assert (pre + post) == (new[:new.index(MS)] + new[new.index(ME) + len(ME):]), "outside-block content changed"
    else:
        sep = "" if raw.endswith("\n") else "\n"
        new = raw + sep + "\n" + block + "\n"
        assert new.startswith(raw), "append changed existing content"
    if APPLY:
        os.makedirs(BACKUP, exist_ok=True)
        shutil.copy(CONN, os.path.join(BACKUP, "connections.md"))
        open(CONN, "w", encoding="utf-8").write(new)

def main():
    block = build_block()
    write_into_conn(block)
    print(("APPLIED to connections.md" if APPLY else "DRY-RUN (connections.md untouched)") + ":\n")
    print(block)

if __name__ == "__main__":
    main()