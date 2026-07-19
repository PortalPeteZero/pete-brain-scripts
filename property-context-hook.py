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

The feed is the CC table `public.property_state` (written nightly by the property-state-cc Railway
cron) — fetched via Supabase REST with a hard 3s timeout and cached locally for 5 min so prompts
never wait on the network twice. The injected text carries IDs/state only; the whole injection is
key-pattern-sanitised. FAIL-OPEN by design: any error (no keys, no network, CC down) → exit 0,
inject nothing, never block the user's prompt.

(Rewired 2026-07-02: the old read target, a local vault file at Library/processes/property-state.json,
was retired with the 24 Jun Business OS thin-client cutover.)

Wire in settings.json under hooks.UserPromptSubmit (see property-context-hook.README).
"""
import sys, json, re, os, time, ssl, urllib.request, urllib.error
VAULT = os.environ.get("VAULT", "/tmp/pbs")

FEED_CACHE = "/tmp/property-context-hook-feed-cache.json"   # {"ts": epoch, "feed": {...}}
FEED_TTL = 300      # re-fetch the CC feed at most once per 5 min
CC_KEYFILES = [
    os.path.expanduser("~/.config/pete-secrets/command-centre-supabase-keys.json"),  # permanent local key
    os.path.join(VAULT, "Library/processes/secrets/command-centre-supabase-keys.json"),  # bootstrapped session
]
THROTTLE = "/tmp/property-hook-live.json"   # {domain: {"ts": epoch, "live": "up"/"down", "code": 200, "host": "vercel"}}
MAXP = 3            # cap injected properties to avoid noise
LIVE_TTL = 600      # re-check a domain at most once per 10 min
LIVE_TIMEOUT = 3    # hard cap — a per-prompt hook must never hang
MAXLEN = 4000       # never balloon the prompt


def load_feed():
    """Latest public.property_state payload, 5-min-cached. Any failure → None (caller exits 0)."""
    now = time.time()
    try:
        c = json.load(open(FEED_CACHE))
        if (now - c.get("ts", 0)) < FEED_TTL and c.get("feed"):
            return c["feed"]
    except Exception:
        pass
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        for kf in CC_KEYFILES:
            try:
                d = json.load(open(kf))
                url, key = d["url"], d["service_role_key"]
                break
            except Exception:
                continue
    if not (url and key):
        return None
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/rest/v1/property_state?select=payload&order=generated.desc&limit=1",
            headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT) as r:
            rows = json.loads(r.read().decode())
        feed = rows[0]["payload"] if rows else None
    except Exception:
        return None
    if feed:
        try:
            tmp = FEED_CACHE + ".tmp"
            json.dump({"ts": now, "feed": feed}, open(tmp, "w"))
            os.replace(tmp, FEED_CACHE)
        except Exception:
            pass
    return feed

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

# ---- property matching (F2): whole-word + globally-unique tokens, no substring collisions --------
# The 2026-07 failure: substring/prefix matching let a bare "lanzarote" resolve to all FOUR Lanzarote
# properties. Fix: match on WHOLE WORDS only, and a single-word term is a match-term ONLY if it is
# ≥5 chars, not stop-listed, AND appears in exactly ONE property across the whole feed (so any token
# shared by ≥2 properties — lanzarote, sygma, canary, leakguard — is automatically inert). Full
# domains always match (they are inherently unique to one property).
STOP = {
    # structural / type words (these tokenise out of names + the declared.tags string)
    "property", "website", "web", "site", "sites", "page", "pages", "app", "apps", "saas",
    "microsite", "internal", "tool", "field", "personal", "game", "crm", "report", "client",
    # descriptors
    "wordpress", "marketing", "holiday", "lets", "lovable", "vercel", "drain", "pool",
    # generic english
    "the", "and", "for", "new", "live", "main",
    # broad place / multi-property that uniqueness alone might miss if only one is tagged
    "lanzarote", "scouts",
}


def _tok(s):
    s = re.sub(r"['’]", "", (s or "").lower())     # o'connor's -> oconnors (one token, not o|connor|s)
    return [w for w in re.split(r"[^a-z0-9]+", s) if w]


def _candidate_terms(p):
    """(single-token set, full-domain set) for one property, before the uniqueness filter."""
    toks, doms = set(), set()
    toks.update(_tok(p.get("name", "")))
    for d in (p.get("domains") or []):
        d = (d or "").lower().strip()
        if d:
            doms.add(d)
            toks.update(_tok(d.split("/")[0]))          # domain labels are tokens too
    decl = p.get("declared") or {}
    for src in (decl.get("aliases"), decl.get("tags")):
        for a in re.split(r"[,\[\]\s]+", (src or "").lower()):
            a = a.strip(' "\'')
            toks.update(_tok(a))
    return toks, doms


def match_properties(feed, prompt):
    props = feed.get("properties", []) or []
    cand, freq = [], {}
    for p in props:
        toks, doms = _candidate_terms(p)
        cand.append((toks, doms))
        for t in toks:
            freq[t] = freq.get(t, 0) + 1
    prompt_words = set(_tok(prompt))
    prompt_l = (prompt or "").lower()
    prompt_phrase = " " + " ".join(_tok(prompt)) + " "
    matched = []
    for p, (toks, doms) in zip(props, cand):
        keep = {t for t in toks if len(t) >= 5 and t not in STOP and freq.get(t, 0) == 1}
        # exact multi-word property NAME as a contiguous phrase — collision-free (each name is unique),
        # so it catches "leakguard lanzarote" even when every individual token is shared/stop-listed
        name_toks = _tok(p.get("name", ""))
        phrase_hit = len(name_toks) >= 2 and (" " + " ".join(name_toks) + " ") in prompt_phrase
        if phrase_hit or any(t in prompt_words for t in keep) or any(d in prompt_l for d in doms):
            matched.append(p)
    return matched


def _cc_creds():
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if url and key:
        return url, key
    for kf in CC_KEYFILES:
        try:
            d = json.load(open(kf))
            return d["url"], d["service_role_key"]
        except Exception:
            continue
    return None, None


def project_status_line(slug):
    """Cloud-sourced status line for a linked project — status + Drive home from the CC `projects`
    table (+ an 'Authoritative ledger:' line from its vault_notes note if one exists). Replaces the old
    `Projects/{slug}/README.md` local read, which is dead in the cloud world (those transport files
    don't exist on a fresh boot, so the line silently never fired). Fail-open → None."""
    if not slug:
        return None
    url, key = _cc_creds()
    if not (url and key):
        return None
    try:
        import urllib.parse
        q = urllib.parse.quote(slug, safe="")
        req = urllib.request.Request(
            url.rstrip("/") + "/rest/v1/projects?select=status,drive_folder_url&slug=eq." + q,
            headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT) as r:
            rows = json.loads(r.read().decode())
        if not rows:
            return None
        st = rows[0].get("status") or "?"
        home = rows[0].get("drive_folder_url") or ""
        return f"status {st}" + (f" · home {home}" if home else "")
    except Exception:
        return None


def resolve_front_door(name):
    """The property's FRONT DOOR — the read-this-first note — straight from `property_declarations`
    at inject time. Added 19 Jul 2026: front doors lived in vault_notes while the property record
    lived in property_declarations and nothing joined them, so the read-first document only arrived
    if someone remembered it existed. Now it arrives unprompted, on mention.

    Holds a vault_path, never a [[slug]] — slugs are not unique (several notes are slugged README).
    Fail-open → None."""
    if not name:
        return None
    url, key = _cc_creds()
    if not (url and key):
        return None
    try:
        import urllib.parse
        q = urllib.parse.quote(name, safe="")
        req = urllib.request.Request(
            url.rstrip("/") + "/rest/v1/property_declarations?select=f&name=eq." + q,
            headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT) as r:
            rows = json.loads(r.read().decode())
        if not rows:
            return None
        return ((rows[0].get("f") or {}).get("front_door") or "").strip() or None
    except Exception:
        return None


def resolve_live_domain(name):
    """Inject-time truth: the property's CURRENT primary domain straight from `property_declarations`,
    so a declaration edited AFTER the last nightly feed run is still reported correctly. This is the
    real F2 class-fix (the nightly feed is only as fresh as its last run). Fail-open → None."""
    if not name:
        return None
    url, key = _cc_creds()
    if not (url and key):
        return None
    try:
        import urllib.parse
        q = urllib.parse.quote(name, safe="")
        req = urllib.request.Request(
            url.rstrip("/") + "/rest/v1/property_declarations?select=f&name=eq." + q,
            headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT) as r:
            rows = json.loads(r.read().decode())
        if rows:
            doms = ((rows[0].get("f") or {}).get("domains")) or []
            return doms[0] if doms else None
    except Exception:
        return None
    return None


def main():
    try:
        prompt = (json.load(sys.stdin).get("prompt") or "").lower()
    except Exception:
        sys.exit(0)
    if len(prompt) < 4:
        sys.exit(0)
    feed = load_feed()
    if not feed:
        sys.exit(0)

    matched = match_properties(feed, prompt)
    if not matched:
        sys.exit(0)

    # F2 inject-time domain resolution for the TOP match: re-read its CURRENT domain from
    # property_declarations right now, so a declaration edited since the last nightly feed is still
    # correct. If it differs from the feed, that's the tripwire — surface it. Fail-open to the feed.
    top = matched[0]
    feed_domain = top.get("primary_domain")
    live_domain = None
    try:
        live_domain = resolve_live_domain(top.get("name"))
    except Exception:
        live_domain = None
    top_domain = live_domain or feed_domain
    domain_moved = bool(live_domain and feed_domain and live_domain != feed_domain)

    # live re-check the TOP match only — one probe, 3s-capped, so the hook stays sub-second-ish
    live = None
    try:
        live = live_check(top_domain)
    except Exception:
        live = None

    lines = [f"[property-state hook — state as-of the last sync ({feed.get('generated','?')}); the top "
             f"match's domain is re-resolved LIVE at this prompt. Trust this over any narrative file.]"]
    for i, p in enumerate(matched[:MAXP]):
        drift = ("  ⚠ " + " · ".join(p["drift"])) if p.get("drift") else ""
        shown_domain = (top_domain if i == 0 else p.get("primary_domain")) or "no domain"
        lines.append(f"• {p['name']}: {str(p.get('live','?')).upper()} · host {p.get('host','?')} · "
                     f"{shown_domain} · repo {p.get('repo_head') or '–'} vs deployed {p.get('deployed') or '–'}{drift}")
        if i == 0 and domain_moved:
            lines.append(f"    ↳ ⚠ DOMAIN CHANGED since the last feed sync: declaration now says "
                         f"{live_domain} (feed had {feed_domain}). Use {live_domain}.")
        if i == 0 and live:
            if live.get("live") == "timeout":
                lines.append(f"    ↳ LIVE re-check: {p.get('primary_domain')} did NOT respond within {LIVE_TIMEOUT}s just now — could NOT confirm it's up; the sync's last state ({str(p.get('live','?')).upper()}) may be stale. Treat as possibly DOWN (check apex DNS) until confirmed.")
            elif live.get("code"):
                served = f" (served by {live['host']})" if live.get("host") else ""
                lines.append(f"    ↳ LIVE NOW: domain {live['live'].upper()} · HTTP {live['code']}{served} — re-checked just now, overrides the sync's up/down for this domain")
        if i == 0:
            _fd = resolve_front_door(p.get("name"))
            if _fd:
                lines.append(f"    ↳ FRONT DOOR (read this FIRST, before any audit/fix/deploy): {_fd}"
                             f" — query it with: VAULT=/tmp/pbs python3 /tmp/pbs/cc-sql.py "
                             f"\"SELECT body FROM vault_notes WHERE vault_path='{_fd}'\"")
        caps = [c.upper() for c in ('ga4', 'gtm', 'gsc', 'ahrefs', 'surfer', 'supabase_ref') if p.get(c)]
        if caps:
            lines.append(f"    measurement/services wired: {', '.join(caps)}")
        # Command Centre: surface the generated orientation map so any CC-touching prompt has it in hand.
        if "command centre" in (p.get("name", "") or "").lower():
            lines.append("    ↳ CC orientation map: ~/.config/pete-cc/MAP.cache.md (GENERATED twice daily from the live tables — read FIRST for any CC work; source: config key map-md)")
        # linked project's status line — read the CLOUD (CC `projects` table) at inject time, NOT a
        # local Projects/{slug}/README.md (that transport file is gitignored + usually absent, so the
        # old read was dead). Bounded to the TOP match's linked projects (cap 2) to keep the hook fast.
        if i == 0:
            projs = [x.strip(' "\'') for x in re.split(r"[,\[\]\s]+", ((p.get("declared") or {}).get("projects") or "")) if len(x.strip(' "\'')) >= 3]
            for proj in projs[:2]:
                pl = project_status_line(proj)
                if pl:
                    lines.append(f"    project {proj} → {pl}")
    emit("\n".join(lines))

if __name__ == "__main__":
    main()