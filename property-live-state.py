#!/usr/bin/env python3
"""
property-live-state.py — the live-state probe (§A of the property-state system plan).

Walks Properties/*/README.md, runs the per-system checks for each card's DECLARED services
(only the non-null ones), and writes a machine-managed <!-- LIVE-STATE --> block into each card.
Safe by construction: snapshot before write, idempotent (replaces its own block), body-preserving
(verifies the non-block content is byte-identical), dry-run by default.

v1 = the FAST checks: domain (Cloudflare-aware host detection) + GitHub head + Vercel deploy +
Supabase reachability + drift flags + an anomaly digest. SEO checks (GSC/GA4/GTM/Ahrefs/Surfer)
land in v2. Reads legacy field-name aliases (ga4_property_id etc.) per the Pass-21 normalisation rule.

Usage:
  python3 property-live-state.py                       # dry-run ALL (writes nothing)
  python3 property-live-state.py --only "O'Connor's Irish Bar"
  python3 property-live-state.py --limit 5
  python3 property-live-state.py --apply               # write the LIVE-STATE blocks
Credentials: secrets/github-pat + secrets/vercel-token if present, else the documented vault values.
"""
import os, re, sys, json, ssl, shutil, urllib.request, urllib.error
from datetime import datetime, timezone

VAULT = "/tmp/pbs"
PROPS = os.path.join(VAULT, "Properties")
SECRETS = os.path.join(VAULT, "Library/processes/secrets")
BACKUP = "/tmp/property-live-state-backup"
STATE_JSON = os.path.join(VAULT, "Library/processes/property-state.json")  # dashboard feed (IDs + state only, no secrets)

def _read(p, d=""):
    try: return open(p).read().strip()
    except Exception: return d

GITHUB_PAT   = os.environ.get("GITHUB_PAT") or _read(os.path.join(SECRETS, "github-pat"))
VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN") or _read(os.path.join(SECRETS, "vercel-token"))
VERCEL_TEAM  = "team_vIKK6s4RTIybcRa71woZLUlm"
AHREFS_TOKEN = os.environ.get("AHREFS_TOKEN") or _read(os.path.join(SECRETS, "ahrefs-token"))
SUPABASE_TOKEN = os.environ.get("SUPABASE_TOKEN") or _read(os.path.join(SECRETS, "supabase-token"))

def _dig(d, rec):
    try:
        import subprocess
        out = subprocess.run(["dig", "+short", d, rec], capture_output=True, text=True, timeout=8).stdout.splitlines()
        return [l for l in out if l][:3]
    except Exception:
        return []

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) property-live-state"}

# never let a real key/token/JWT reach the feed (the dashboard serves it) — IDs/refs are fine
SECRET_RE = re.compile(
    r"(ghp_[A-Za-z0-9]{20,}|vcp_[A-Za-z0-9]{15,}|sbp_[A-Za-z0-9]{15,}"
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{15,}|AIza[A-Za-z0-9_\-]{30,}"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,})")
def redact(v): return SECRET_RE.sub("«redacted»", v)
MARK_START = "<!-- LIVE-STATE:START — machine-maintained by property-live-state.py, do not hand-edit -->"
MARK_END   = "<!-- LIVE-STATE:END -->"

APPLY = "--apply" in sys.argv
ONLY  = sys.argv[sys.argv.index("--only")+1] if "--only" in sys.argv else None
LIMIT = int(sys.argv[sys.argv.index("--limit")+1]) if "--limit" in sys.argv else None

def now_iso(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
def now_date(): return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ---------------- HTTP ----------------
def api(url, headers, timeout=25):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout, context=CTX) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None

def fetch_head(url, timeout=12):
    """GET (some hosts 403 HEAD); return (status, headers_lower, final_url)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout, context=CTX) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.url
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, url
    except Exception:
        return None, {}, url

# ---------------- connectivity preflight ----------------
# Guards the "my own uplink is dead" failure mode — the probe must never record its own broken
# connectivity as a fleet outage. Two documented incidents:
#   • 2026-06-14 — overnight DNS/connectivity drop: every check failed → all 24 wrongly DOWN
#     + a false "all sites down" alert (GCal's Xhale sync failed the same night, same cause).
#   • 2026-06-11/12 — TP-Link Deco HomeShield "Web Protection" blackholing Vercel's 216.150.0.0
#     range for THIS Mac only (phone fine). Lesson: [[2026-06-11-mac-forget-wifi-fixes-vercel-range-unreachable]].
# If this host can't reach the wider internet, OR can't reach Vercel, abort WITHOUT writing —
# preserving last-known-good state beats overwriting it with false DOWNs. Override: --no-preflight.
NET_ANCHORS   = ("https://github.com", "https://www.google.com", "https://1.1.1.1")
VERCEL_ANCHOR = "https://canary-detect.com"   # Vercel-hosted on the 216.150 range → proves the Mac→Vercel path

def preflight():
    if "--no-preflight" in sys.argv:
        return True, "skipped (--no-preflight)"
    if not any(fetch_head(a, timeout=10)[0] is not None for a in NET_ANCHORS):
        return False, ("no outbound connectivity from this host (could not reach "
                       "github.com / google.com / 1.1.1.1) — local network/DNS is down")
    if fetch_head(VERCEL_ANCHOR, timeout=10)[0] is None:
        return False, ("internet is up but Vercel (216.150.0.0) is unreachable from this host — "
                       "almost certainly the local Deco HomeShield 'Web Protection' block "
                       "(lesson 2026-06-11), NOT a site outage. Fix: Deco app → HomeShield → "
                       "Web Protection → OFF")
    return True, "ok"

# ---------------- frontmatter (alias-aware, Pass-21) ----------------
def parse_fm(fm):
    def gv(*keys):
        for k in keys:
            m = re.search(rf"^{k}\s*:\s*(.+)$", fm, re.M | re.I)
            if m:
                v = m.group(1).strip().strip('"').strip("'").strip()
                if v and v.lower() not in ("tbc", "null", "none", "[]", '""', ""):
                    return v
        return ""
    def repo_token(s):
        m = re.search(r"([\w.-]+/[\w.-]+)", s)
        return m.group(1) if m else ""
    doms_raw = gv("domains", "domain")
    domains = [d.strip().lower().replace("https://", "").replace("http://", "").strip("/")
               for d in re.split(r"[,\s\[\]]+", doms_raw) if d.strip() and "." in d]
    # full declared frontmatter (every key), secrets redacted — drives the rich detail page
    MACHINE = {"production_head", "live_verified"}
    declared = {}
    for m in re.finditer(r"^([A-Za-z][\w-]*)\s*:\s*(.*)$", fm, re.M):
        k, v = m.group(1), redact(m.group(2).strip())
        if k.lower() not in MACHINE and v:
            declared[k] = v
    return {
        "domains": domains,
        "github": repo_token(gv("github", "repo")),
        "vercel_project": gv("vercel_project"),
        "vercel_team": gv("vercel_team") or "sygma1",
        "supabase_ref": gv("supabase_ref", "supabase_project_ref_migration", "supabase_project_ref", "supabase_project_ref_legacy"),
        "hosting": gv("hosting").lower(),
        "status": gv("status").lower(),
        "ptype": gv("property_type").lower(),
        "business": gv("business"),
        "prod_branch": gv("prod_branch") or "main",
        # SEO/analytics — alias-aware (Pass-21 normalisation): legacy *_id names too
        "ga4": gv("ga4_property", "ga4_property_id", "ga4_measurement_id"),
        "gtm": gv("gtm_container", "gtm_container_id"),
        "gsc": gv("gsc_property"),
        "ahrefs": gv("ahrefs_project", "ahrefs_project_id"),
        "surfer": gv("surfer_workspace"),
        "declared": declared,
    }

# Vercel retired its apex anycast IPs (2026-06-07 estate outage). BOTH 76.76.21.21 (legacy) and
# 216.198.79.1 (the interim value a first fix wrongly used) are dead — TCP :443 gets no response.
# Vercel's CURRENT apex IPs are 216.150.1.1 + 216.150.16.1. An apex still on a dead IP is a DEFINITIVE
# down even when the deploy is READY. See Library/lessons/2026-06-07-vercel-retired-apex-ip.md.
RETIRED_VERCEL_APEX_IPS = {"76.76.21.21", "216.198.79.1"}
VALID_VERCEL_APEX_IPS = {"216.150.1.1", "216.150.16.1"}
VALID_APEX_DISPLAY = "216.150.1.1 + 216.150.16.1"

def dns_verdict(dom):
    """Definitive infra signal from the apex A record, independent of whether we could fetch the page
    (the fetch can time out for many reasons; a dead apex IP is unambiguous). Returns (state, reason)."""
    a = (dom or {}).get("dns", {}).get("A") or []
    a_ips = {x.strip().rstrip(".") for x in a if x.strip()}
    dead = a_ips & RETIRED_VERCEL_APEX_IPS
    if dead:
        return "down", f"apex DNS on RETIRED Vercel IP {', '.join(sorted(dead))} → repoint to {VALID_APEX_DISPLAY}"
    return None, ""

# ---------------- checks ----------------
def check_domain(domains, declared_host):
    if not domains: return None
    d = domains[0]
    dns = {"A": _dig(d, "A"), "MX": _dig(d, "MX"), "NS": _dig(d, "NS")}   # §A: dig A/MX/NS
    status, h, final = fetch_head("https://" + d)
    if status is None:
        status, h, final = fetch_head("https://" + d, timeout=20)   # one retry, longer
    if status is None:
        # unreachable from THIS host — up:None (unknown), NOT down. Liveness is resolved
        # against Vercel state in resolve_liveness(); a timeout alone never flags DOWN.
        return {"domain": d, "up": None, "host": declared_host or "unknown",
                "edge": "", "redirect": "", "dns": dns, "note": "unreachable from probe host"}
    # Cloudflare-aware origin detection (edge != origin)
    if d.endswith(".manus.space"): host = "manus"
    elif "x-vercel-id" in h: host = "vercel"
    else:
        srv = (h.get("server", "") + " " + h.get("x-powered-by", "")).lower()
        if "manus" in srv: host = "manus"
        elif "cloudflare" in srv: host = declared_host or "cloudflare(edge)"   # trust declared origin
        elif "vercel" in srv: host = "vercel"
        else: host = declared_host or "unknown"
    redirected = final.rstrip("/") != ("https://" + d).rstrip("/")
    return {"domain": d, "up": 200 <= status < 400, "status": status, "host": host,
            "edge": h.get("server", ""), "redirect": final if redirected else "", "dns": dns}

def resolve_liveness(dom, vc, ptype):
    """Liveness from the DOMAIN, with the Vercel deploy state as CONTEXT — never an override.
    Hard lesson (2026-06-07 estate outage): a READY deploy does NOT prove the custom domain serves.
    When we declared a domain and couldn't reach it, that's DOWN — the deploy being READY just tells
    us the fault is DNS/edge, not the app. Only a domain we actually reached (2xx/3xx) is 'up'."""
    expected_down = ptype in ("sunset", "archived", "paused", "parked", "retired", "unpublished", "draft")
    ready = bool(vc and str(vc.get("state", "")).upper() == "READY")
    # 1. actually reached it → up (the only positive proof)
    if dom and dom["up"] is True:
        return "up", dom["host"], ""
    # 2. dead apex IP → DOWN even if the deploy is READY (this is the class that hid the outage)
    dverd, dreason = dns_verdict(dom)
    if dverd == "down" and not expected_down:
        return "down", (dom["host"] if dom else "vercel"), dreason + (" — deploy is READY, so DNS is the fault" if ready else "")
    # 3. a real HTTP error code came back → down
    if dom and dom["up"] is False:
        return ("expected-down" if expected_down else "down"), dom["host"], f"HTTP {dom.get('status','?')}"
    # 4. declared a domain but it timed out / was unreachable → DOWN (READY demoted to a reason, not 'up')
    if dom and dom["up"] is None:
        if expected_down:
            return "expected-down", dom["host"], "unreachable (expected down)"
        note = ("unreachable — Vercel deploy is READY, so the fault is DNS/edge "
                f"(check apex A = {VALID_APEX_DISPLAY})") if ready else "unreachable — no response"
        return "down", (dom["host"] if dom and dom["host"] not in ("unknown", "") else ("vercel" if ready else "—")), note
    # 5. no domain on the card at all — can't verify a public URL; a READY deploy is the only signal
    if not dom:
        if ready:
            return "up", "vercel", "Vercel deploy READY (no public domain on card to verify)"
        return "unknown", "—", "no domain declared"
    return "unknown", (dom["host"] if dom else "—"), "no domain declared"

def check_github(repo, branch):
    if not repo: return None
    data = api(f"https://api.github.com/repos/{repo}/commits?sha={branch}&per_page=1",
               {"Authorization": f"token {GITHUB_PAT}", "User-Agent": "property-live-state"})
    if not data or not isinstance(data, list):
        # try default branch if the named one 404s
        info = api(f"https://api.github.com/repos/{repo}", {"Authorization": f"token {GITHUB_PAT}", "User-Agent": "x"})
        if info and info.get("default_branch") and info["default_branch"] != branch:
            return check_github(repo, info["default_branch"])
        return {"repo": repo, "head": "unknown"}
    c = data[0]
    return {"repo": repo, "head": c["sha"][:7], "date": c["commit"]["committer"]["date"][:10],
            "msg": c["commit"]["message"].split("\n")[0][:60], "branch": branch}

def check_vercel(project):
    if not project: return None
    p = api(f"https://api.vercel.com/v9/projects/{project}?teamId={VERCEL_TEAM}",
            {"Authorization": f"Bearer {VERCEL_TOKEN}"})
    if not p: return {"project": project, "deployed": "unknown"}
    tgt = (p.get("targets") or {}).get("production") or {}
    aliases = [a for a in (tgt.get("alias") or []) if isinstance(a, str) and not a.endswith(".vercel.app")]
    return {"project": project, "deployed": (tgt.get("meta") or {}).get("githubCommitSha", "")[:7] or "—",
            "state": tgt.get("readyState", tgt.get("readyStateReason", "")) or "?", "aliases": aliases[:3]}

def check_supabase(ref):
    if not ref: return None
    status, h, _ = fetch_head(f"https://{ref}.supabase.co/rest/v1/", timeout=10)
    # 200/401/404 all prove the project is alive (401 = needs apikey, which is fine)
    out = {"ref": ref, "reachable": status in (200, 401, 404, 400)}
    if SUPABASE_TOKEN:   # Mgmt API status + PG version (best-effort; 403 if the token can't see this project)
        m = api(f"https://api.supabase.com/v1/projects/{ref}", {"Authorization": f"Bearer {SUPABASE_TOKEN}"})
        if m:
            out["status"] = m.get("status")
            out["pg"] = (m.get("database") or {}).get("version")
    return out

# --- SEO checks (§F) — lazy-loaded so importing this module elsewhere never triggers Google auth.
# Run ONLY for properties that declare the field (the few commercial sites); graceful per-service.
_SEO = {}
def _load(modfile):
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), modfile)
    s = importlib.util.spec_from_file_location(modfile.replace("-", "_"), path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def check_seo(f):
    out = {}
    if f.get("gsc"):
        try:
            if "gsc" not in _SEO: _SEO["gsc"] = _load("gsc-api.py").GSCAPI()
            g = _SEO["gsc"]
            rows = g.query(f["gsc"], ["date"], date_range=28, limit=1000) or []
            out["gsc_verified"] = True   # the SA could query it → Google sees + we're a verified owner
            out["gsc_clicks28"] = sum(r.get("clicks", 0) for r in rows)
            out["gsc_impr28"] = sum(r.get("impressions", 0) for r in rows)
            sm = g.list_sitemaps(f["gsc"])
            out["sitemaps"] = len(sm) if isinstance(sm, list) else None
            out["gsc_pages"] = len(g.top_pages(f["gsc"], days=28, limit=1000) or [])   # distinct pages w/ impressions ≈ indexed/active
        except Exception:
            out["gsc_verified"] = "unknown"
    if f.get("ga4"):
        try:
            if "ga4" not in _SEO: _SEO["ga4"] = _load("ga4-api.py").GA4API()
            bd = (_SEO["ga4"].summary(f["ga4"], days=7) or {}).get("by_date", [])
            out["ga4_sessions7"] = sum(int(d.get("sessions", 0)) for d in bd)
            out["ga4_users7"] = sum(int(d.get("activeUsers", 0)) for d in bd)
            last = max((d.get("date", "") for d in bd), default="")
            out["ga4_last"] = f"{last[:4]}-{last[4:6]}-{last[6:]}" if len(last) == 8 else None
        except Exception:
            out["ga4_sessions7"] = "unknown"
    if f.get("ahrefs") and f.get("domains"):
        try:
            import datetime
            # Ahrefs needs a PAST date; today returns 400 "bad date" (phase 0a, 2026-07-23)
            today = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            hdr = {"Authorization": f"Bearer {AHREFS_TOKEN}"}
            # Capture the failure REASON, never a silent None -- a 403 (units) must not read as "no DR" (phase 0b).
            def _ah(url):
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=25, context=CTX) as r:
                        return json.loads(r.read().decode()), None
                except urllib.error.HTTPError as e:
                    return None, f"HTTP {e.code}"
                except Exception as e:
                    return None, str(e)[:80]
            dr, err = _ah(f"https://api.ahrefs.com/v3/site-explorer/domain-rating?target={f['domains'][0]}&date={today}")
            out["ahrefs_dr"] = (dr or {}).get("domain_rating", {}).get("domain_rating")
            if err: out["ahrefs_error"] = err   # loud marker so downstream never mistakes a pull failure for a real zero
            mk, _ = _ah(f"https://api.ahrefs.com/v3/site-explorer/metrics?target={f['domains'][0]}&date={today}&volume_mode=monthly&country=es")
            met = (mk or {}).get("metrics", {})
            out["ahrefs_keywords"] = met.get("org_keywords")
            out["ahrefs_traffic"] = met.get("org_traffic")
        except Exception as e:
            out["ahrefs_error"] = str(e)[:80]
    if f.get("gtm") and f.get("domains"):   # §A: is the GTM container live/firing on the page?
        try:
            with urllib.request.urlopen(urllib.request.Request("https://" + f["domains"][0], headers=UA), timeout=10, context=CTX) as r:
                out["gtm_live"] = f["gtm"] in r.read(150000).decode("utf-8", "ignore")
        except Exception:
            out["gtm_live"] = "unknown"
    return out or None

# ---------------- drift ----------------
def drift_flags(live, gh, vc, dom, declared_host, live_note=""):
    flags = []
    if live == "down":                                   # incl. unreachable/dead-apex-IP (post 2026-06-07 fix)
        reason = live_note or (f"HTTP {dom.get('status')}" if dom and dom.get("status") else "unreachable")
        flags.append("🔴 DOWN — " + reason[:90])
    if gh and vc and gh.get("head") not in ("unknown", None) and vc.get("deployed") not in ("unknown", "—", None):
        if gh["head"] != vc["deployed"]:
            flags.append(f"repo ahead of live (repo {gh['head']} ≠ deployed {vc['deployed']})")
    # host mismatch: only when we actually reached the origin (a real header, not cloudflare-edge) and it disagrees
    if dom and dom.get("up") is True and declared_host and dom["host"] not in ("unknown", declared_host) \
            and not str(dom["host"]).startswith("cloudflare"):
        flags.append(f"host mismatch (card {declared_host} ≠ live {dom['host']})")
    return flags

# ---------------- block render + safe write ----------------
def render_block(name, fm, dom, gh, vc, sb, flags, live, live_host, live_note, seo=None):
    badge = {"up": "🟢 up", "down": "🔴 DOWN", "expected-down": "⚪ down (expected)",
             "unknown": "⚪ unknown"}.get(live, live)
    L = [MARK_START, "## Live state", "", f"- **Checked:** {now_iso()}",
         f"- **Status:** {badge} · host **{live_host}**" + (f" · {live_note}" if live_note else "")]
    if dom:
        line = f"- **Domain:** {dom['domain']}"
        if dom.get("status"): line += f" — HTTP {dom['status']}"
        if dom.get("edge"): line += f" · edge `{dom['edge']}`"
        if dom.get("redirect"): line += f" · → {dom['redirect']}"
        if dom.get("note"): line += f" · {dom['note']}"
        L.append(line)
        if dom.get("dns"):
            a = ", ".join(dom["dns"].get("A", [])) or "–"
            L.append(f"- **DNS:** A {a} · MX {'yes' if dom['dns'].get('MX') else 'none'} · NS {', '.join(dom['dns'].get('NS', [])[:2]) or '–'}")
    if gh:
        L.append(f"- **Repo head:** `{gh.get('head','?')}` {('('+gh.get('date','')+') '+gh.get('msg','')) if gh.get('date') else ''} — {gh['repo']}@{gh.get('branch','main')}")
    if vc:
        al = (" · " + ", ".join(vc["aliases"])) if vc.get("aliases") else ""
        L.append(f"- **Deployed (Vercel):** `{vc.get('deployed','?')}` · {vc.get('state','?')} · project `{vc['project']}`{al}")
    if sb:
        st = f" · {sb['status']}" if sb.get("status") else ""
        L.append(f"- **Supabase:** {sb['ref']} — {'reachable' if sb['reachable'] else 'UNREACHABLE'}{st}")
    if seo:
        bits = []
        if seo.get("gsc_clicks28") not in (None, "unknown"): bits.append(f"GSC {seo['gsc_clicks28']} clk/{seo.get('gsc_impr28','?')} imp · {seo.get('gsc_pages','?')} pages")
        if seo.get("ga4_sessions7") not in (None, "unknown"): bits.append(f"GA4 {seo['ga4_sessions7']} sess/7d")
        if seo.get("ahrefs_dr") not in (None, "unknown"): bits.append(f"Ahrefs DR {seo['ahrefs_dr']} · {seo.get('ahrefs_keywords','?')} kw")
        if seo.get("gtm_live") is not None: bits.append("GTM " + ("firing" if seo["gtm_live"] is True else "NOT firing" if seo["gtm_live"] is False else "?"))
        if bits: L.append("- **SEO:** " + " · ".join(bits))
    L.append(f"- **Drift:** {' · '.join(flags) if flags else 'none'}")
    L += ["", MARK_END]
    return "\n".join(L)

def write_block(path, block):
    raw = open(path, encoding="utf-8").read()
    if MARK_START in raw and MARK_END in raw:
        pre = raw[:raw.index(MARK_START)]
        post = raw[raw.index(MARK_END) + len(MARK_END):]
        new = pre + block + post
        anchor = pre + post                      # content outside the block
    else:
        sep = "" if raw.endswith("\n") else "\n"
        new = raw + sep + "\n" + block + "\n"
        anchor = raw
    # verify: everything outside the block is preserved
    chk = new.replace(block, "") if (MARK_START not in raw) else (new[:new.index(MARK_START)] + new[new.index(MARK_END)+len(MARK_END):])
    if MARK_START not in raw:
        assert new.startswith(raw), "APPEND changed existing content"
    else:
        assert chk == anchor, "REPLACE changed content outside the block"
    if APPLY:
        os.makedirs(BACKUP, exist_ok=True)
        shutil.copy(path, os.path.join(BACKUP, os.path.basename(os.path.dirname(path)) + ".md"))
        open(path, "w", encoding="utf-8").write(new)

def upsert_fm(path, kv):
    """Write machine-owned keys (production_head, live_verified) into the YAML frontmatter
    ONLY (between the first two `---`). Existing key → replaced in place; new key → inserted
    before the closing `---`. Body after the frontmatter is preserved byte-for-byte. No-op if
    there's no frontmatter or nothing changed. APPLY-guarded."""
    kv = {k: v for k, v in kv.items() if v not in (None, "", "unknown", "—")}
    if not kv:
        return False
    raw = open(path, encoding="utf-8").read()
    if not raw.startswith("---"):
        return False
    end = raw.find("\n---", 3)          # newline before the closing delimiter
    if end == -1:
        return False
    head, rest = raw[:end + 1], raw[end + 1:]   # head = open delim + keys; rest = close delim + body
    new_head = head
    for k, v in kv.items():
        line = f'{k}: "{v}"'
        pat = re.compile(rf"^{re.escape(k)}\s*:.*$", re.M)
        new_head = pat.sub(line, new_head, count=1) if pat.search(new_head) else new_head.rstrip("\n") + f"\n{line}\n"
    if new_head == head:
        return False
    new = new_head + rest
    assert new.endswith(rest), "upsert_fm changed body"   # body untouched
    if APPLY:
        open(path, "w", encoding="utf-8").write(new)
    return True

# ---------------- main ----------------
def main():
    ok, why = preflight()
    if not ok:
        print(f"PREFLIGHT ABORT — {why}.", file=sys.stderr)
        print("Wrote NOTHING (no LIVE-STATE blocks, no feed); last-known-good state preserved. "
              "A local network fault must not be recorded as a fleet outage. "
              "Re-run when connectivity is restored, or pass --no-preflight to force.", file=sys.stderr)
        sys.exit(3)
    names = sorted(n for n in os.listdir(PROPS) if os.path.isfile(os.path.join(PROPS, n, "README.md")))
    if ONLY:  names = [n for n in names if n == ONLY]
    if LIMIT: names = names[:LIMIT]
    digest = []
    records = []
    # quota-aware SEO tiering (plan §Cadence): fast checks run for ALL nightly; the heavy SEO pulls
    # (GSC/GA4/Ahrefs/GTM-in-HTML) run nightly only for ACTIVE sites and weekly for the rest, carrying
    # forward the last-known SEO from the previous feed on off-days so the dashboard never loses it.
    old_by_name = {}
    if os.path.exists(STATE_JSON):
        try:
            old_by_name = {p["name"]: p for p in json.load(open(STATE_JSON)).get("properties", [])}
        except Exception:
            pass
    SEO_WEEKLY_DAY = 0   # Monday
    weekday = datetime.now(timezone.utc).weekday()
    print(("APPLY" if APPLY else "DRY-RUN") + f" — {len(names)} propert{'y' if len(names)==1 else 'ies'}\n" + "="*64)
    for name in names:
        path = os.path.join(PROPS, name, "README.md")
        raw = open(path, encoding="utf-8").read()
        fm = raw.split("---", 2)[1] if raw.startswith("---") else raw[:1500]
        f = parse_fm(fm)
        dom = check_domain(f["domains"], f["hosting"])
        gh  = check_github(f["github"], f["prod_branch"])
        vc  = check_vercel(f["vercel_project"])
        sb  = check_supabase(f["supabase_ref"])
        live, live_host, live_note = resolve_liveness(dom, vc, f["status"])
        # quota-tiered SEO: active sites nightly; others weekly (Monday); carry forward last-known otherwise
        declares_seo = any(f.get(k) for k in ("ga4", "gtm", "gsc", "ahrefs", "surfer"))
        is_active = f["status"] in ("active", "live") or (not f["status"] and live == "up")
        if declares_seo and (is_active or weekday == SEO_WEEKLY_DAY):
            seo = check_seo(f)
        elif declares_seo:
            seo = (old_by_name.get(name) or {}).get("seo")   # off-day for a non-active site → keep last-known
        else:
            seo = None
        flags = drift_flags(live, gh, vc, dom, f["hosting"], live_note)
        block = render_block(name, f, dom, gh, vc, sb, flags, live, live_host, live_note, seo)
        write_block(path, block)
        # machine-own the two canonical frontmatter fields the property-manager source-of-truth read trusts:
        # production_head = the commit actually serving (Vercel deploy sha, else repo head); live_verified = last 200.
        prod_head = (vc.get("deployed") if vc else None) or (gh.get("head") if gh else None)
        upsert_fm(path, {"production_head": prod_head, "live_verified": now_date() if live == "up" else None})
        badge = {"up": "🟢", "down": "🔴", "expected-down": "⚪", "unknown": "⚪"}.get(live, "·")
        print(f"{name[:38]:39s} {badge} {live_host[:14]:15s} {('repo '+gh['head']) if gh and gh.get('head') not in ('unknown', None) else '':14s} {'⚠ '+' · '.join(flags) if flags else ''}")
        if flags: digest.append((name, flags))
        records.append({
            "name": name, "ptype": f["ptype"], "status_field": f["status"], "business": f["business"] or None,
            "live": live, "host": live_host, "note": live_note,
            "domains": f["domains"], "primary_domain": f["domains"][0] if f["domains"] else None,
            "github": f["github"] or None, "vercel_project": f["vercel_project"] or None, "vercel_team": f["vercel_team"],
            "repo_head": gh.get("head") if gh else None, "repo_date": gh.get("date") if gh else None,
            "deployed": vc.get("deployed") if vc else None, "deploy_state": vc.get("state") if vc else None,
            "production_head": prod_head or None, "live_verified": (now_date() if live == "up" else None),
            "aliases": vc.get("aliases") if vc else [],
            "dns": dom.get("dns") if dom else None,
            "supabase_ref": f["supabase_ref"] or None, "supabase_ok": sb.get("reachable") if sb else None,
            "ga4": f["ga4"] or None, "gtm": f["gtm"] or None, "gsc": f["gsc"] or None,
            "ahrefs": f["ahrefs"] or None, "surfer": f["surfer"] or None,
            "seo": seo,
            "declared": f["declared"],
            "drift": flags, "checked": now_iso(),
        })
    print("="*64)
    if digest:
        print(f"\nANOMALY DIGEST ({len(digest)}):")
        for n, fl in digest: print(f"  {n}: {' · '.join(fl)}")
    else:
        print("\nAnomaly digest: clean (no drift on the checked set).")
    if APPLY: print(f"\nWrote LIVE-STATE blocks. Snapshot: {BACKUP}")
    # always emit the dashboard feed (IDs + state only — no keys/secrets), even on dry-run
    feed = {"generated": now_iso(), "count": len(records),
            "up": sum(1 for r in records if r["live"] == "up"),
            "anomalies": [{"name": n, "drift": fl} for n, fl in digest],
            "properties": records}
    if not ONLY and not LIMIT:
        tmp = STATE_JSON + ".tmp"                      # atomic write: the hook never sees a half-written feed
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(feed, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_JSON)
        print(f"Wrote dashboard feed: {STATE_JSON} ({len(records)} properties)")

if __name__ == "__main__":
    # DECOMMISSIONED entrypoint (2026-07-07). This script's README-walk main() reads declarations from a
    # LOCAL Properties/*/README.md tree — a local-vault-era pattern. Post-cutover, declarations live in the
    # CC `property_declarations` table and the nightly feed is `property-state-cc.py` (which imports THIS
    # module's probe functions — check_domain/check_github/resolve_liveness/… — and is the only supported
    # runner). Running the local walk against the (empty on a thin client) tree would silently write a
    # misleading feed. The probe functions remain importable; only the CLI walk is retired.
    if "--force-local-walk" in sys.argv:
        main()  # escape hatch for a genuine local-tree debug run; never the production path
    else:
        print("property-live-state.py CLI is DECOMMISSIONED — declarations now live in the CC "
              "`property_declarations` table. Run the nightly feed via `property-state-cc.py` "
              "(it reuses this module's probe functions). Pass --force-local-walk only to debug a "
              "local Properties/ tree.", file=sys.stderr)
        sys.exit(2)
