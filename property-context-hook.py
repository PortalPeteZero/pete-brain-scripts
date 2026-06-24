#!/usr/bin/env python3
"""
property-context-hook.py — §D of the plan: the un-ignorable delivery.

A Claude Code UserPromptSubmit hook. On each prompt it matches the text against the live
property feed (names + domains + aliases) and, on a hit, injects that property's VERIFIED
current state (live/up, host, repo-vs-deployed drift, measurement IDs) + its linked project's
authoritative-status line. The harness runs this — not the model — so the truth can't be skipped.

Live re-check (plan §D / §E point-of-use): for the TOP matched property it re-runs the one
FAST check that is both secret-free and genuinely real-time — a public-domain HTTP probe — so
"is it up right now" is live, not just as-of-last-sync. Hard 3s timeout (never hangs a response),
throttled to once per property per 10 min, and the whole injection is key-pattern-sanitised before
it goes out. Vercel/repo/SEO stay as-of-last-sync (they need secrets; a per-prompt hook holds none).

Reads only the maintained feed + the linked project README (no secrets — the feed carries IDs/state
only). FAIL-OPEN by design: any error → exit 0, inject nothing, never block the user's prompt.

Wire in settings.json under hooks.UserPromptSubmit (see property-context-hook.README).
"""
import sys, json, re, os, time, ssl, urllib.request
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
FEED = os.path.join(VAULT, "Library/processes/property-state.json")
PROJECTS = os.path.join(VAULT, "Projects")
THROTTLE = "/tmp/property-hook-live.json"   # {domain: {"ts": epoch, "live": "up"/"down", "code": 200, "host": "vercel"}}
MAXP = 3            # cap injected properties to avoid noise
LIVE_TTL = 600      # re-check a domain at most once per 10 min
LIVE_TIMEOUT = 3    # hard cap — a per-prompt hook must never hang
MAXLEN = 4000       # never balloon the prompt

# never let anything key-shaped reach the model via the injection (defence in depth — the feed is
# IDs/state only, but the linked README is free text)
SECRET_RE = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|vcp_[A-Za-z0-9]{15,}|sbp_[A-Za-z0-9]{15,}"
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{15,}|AIza[A-Za-z0-9_\-]{30,}"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,})")
def sanitise(t): return SECRET_RE.sub("«redacted»", t)

_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh) property-context-hook"}

def emit(text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": sanitise(text)[:MAXLEN]}}))
    sys.exit(0)

def _load_throttle():
    try: return json.load(open(THROTTLE))
    except Exception: return {}

def _save_throttle(d):
    try:
        tmp = THROTTLE + ".tmp"
        json.dump(d, open(tmp, "w"))
        os.replace(tmp, THROTTLE)
    except Exception:
        pass

def live_check(domain):
    """Secret-free public probe of the TOP match. Returns dict or None. Throttled + 3s-capped.
    Any failure → None (caller falls back to the cached card block). Never raises."""
    if not domain:
        return None
    now = time.time()
    cache = _load_throttle()
    hit = cache.get(domain)
    if hit and (now - hit.get("ts", 0)) < LIVE_TTL:
        return hit                                   # still fresh — reuse, don't re-hit the network
    try:
        req = urllib.request.Request("https://" + domain, headers=_UA)
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT, context=_CTX) as r:
            code, hdr = r.status, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        code, hdr = e.code, {}
    except Exception:
        # timeout/DNS/connection — DON'T return None (that silently trusts the cached state, which is
        # how the 2026-06-07 dead-apex-IP outage hid: cached said UP, re-check timed out, hook showed UP).
        # Record it as a timeout so the hook surfaces "couldn't confirm" instead of bluffing UP.
        rec = {"ts": now, "live": "timeout", "code": 0, "host": ""}
        cache[domain] = rec
        _save_throttle({k: v for k, v in cache.items() if now - v.get("ts", 0) < 3600})
        return rec
    host = "vercel" if "x-vercel-id" in hdr else ("cloudflare" if "cf-ray" in hdr else "")
    rec = {"ts": now, "live": "up" if (code and code < 500) else "down", "code": code, "host": host}
    cache[domain] = rec
    # prune anything older than an hour so the file can't grow unbounded
    _save_throttle({k: v for k, v in cache.items() if now - v.get("ts", 0) < 3600})
    return rec

def main():
    try:
        prompt = (json.load(sys.stdin).get("prompt") or "").lower()
    except Exception:
        sys.exit(0)
    if len(prompt) < 4:
        sys.exit(0)
    try:
        feed = json.load(open(FEED, encoding="utf-8"))
    except Exception:
        sys.exit(0)

    matched = []
    for p in feed.get("properties", []):
        terms = {p["name"].lower()}
        for d in p.get("domains", []):
            terms.add(d.lower()); terms.add(d.split(".")[0].lower())
        for a in re.split(r"[,\[\]\s]+", (p.get("declared", {}).get("aliases", "") or "").lower()):
            a = a.strip(' "\'')
            if len(a) >= 4:
                terms.add(a)
        words = [w for w in re.split(r"[^a-z0-9]+", prompt) if len(w) >= 5]
        if any((len(t) >= 4 and t in prompt) or (len(t) >= 5 and any(t.startswith(w) for w in words)) for t in terms):
            matched.append(p)
    if not matched:
        sys.exit(0)

    # live re-check the TOP match only — one probe, 3s-capped, so the hook stays sub-second-ish
    live = None
    try:
        live = live_check(matched[0].get("primary_domain"))
    except Exception:
        live = None

    lines = [f"[property-state hook — VERIFIED current state from the last sync ({feed.get('generated','?')}). Trust this over any narrative file.]"]
    for i, p in enumerate(matched[:MAXP]):
        drift = ("  ⚠ " + " · ".join(p["drift"])) if p.get("drift") else ""
        lines.append(f"• {p['name']}: {str(p.get('live','?')).upper()} · host {p.get('host','?')} · "
                     f"{p.get('primary_domain') or 'no domain'} · repo {p.get('repo_head') or '–'} vs deployed {p.get('deployed') or '–'}{drift}")
        if i == 0 and live:
            if live.get("live") == "timeout":
                lines.append(f"    ↳ LIVE re-check: {p.get('primary_domain')} did NOT respond within {LIVE_TIMEOUT}s just now — could NOT confirm it's up; the sync's last state ({str(p.get('live','?')).upper()}) may be stale. Treat as possibly DOWN (check apex DNS) until confirmed.")
            elif live.get("code"):
                served = f" (served by {live['host']})" if live.get("host") else ""
                lines.append(f"    ↳ LIVE NOW: domain {live['live'].upper()} · HTTP {live['code']}{served} — re-checked just now, overrides the sync's up/down for this domain")
        caps = [c.upper() for c in ('ga4', 'gtm', 'gsc', 'ahrefs', 'surfer', 'supabase_ref') if p.get(c)]
        if caps:
            lines.append(f"    measurement/services wired: {', '.join(caps)}")
        # Command Centre: surface the generated live map + ops doc so any CC-touching prompt has them in hand.
        if "command centre" in (p.get("name", "") or "").lower():
            lines.append("    ↳ CC map: Properties/Pete Command Centre/cc-map.md (GENERATED — every module by area · who-can-see-what · access-change history) · ops doc: Library/processes/command-centre.md (read FIRST for any CC work)")
        # linked project's authoritative-status line
        for proj in re.split(r"[,\[\]\s]+", (p.get("declared", {}).get("projects", "") or "")):
            proj = proj.strip(' "\'')
            rp = os.path.join(PROJECTS, proj, "README.md")
            if len(proj) >= 3 and os.path.isfile(rp):
                try:
                    raw = open(rp, encoding="utf-8", errors="ignore").read()
                    m = re.search(r"\*\*Authoritative ledger:\*\*\s*(.+)", raw)
                    if m:
                        lines.append(f"    project {proj} → authoritative: {m.group(1).strip()[:120]}")
                except Exception:
                    pass
    emit("\n".join(lines))

if __name__ == "__main__":
    main()