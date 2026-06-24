#!/usr/bin/env python3
"""
dns-apex-fix.py — 2026-06-07 estate outage: repoint every Vercel apex A record off the DEAD IPs
(76.76.21.21 AND 216.198.79.1 — both retired/unreachable) to Vercel's CURRENT apex IPs
216.150.1.1 + 216.150.16.1 (confirmed live: TCP open + HTTP 200 when forced via --resolve).

Sets each apex to EXACTLY the two good IPs (idempotent: already-correct = no-op). Dry-run by default.
Covers all three of Pete's DNS providers. Verifies each domain serves 200 via curl --resolve after.

  python3 dns-apex-fix.py            # DRY-RUN — show the plan, write nothing
  python3 dns-apex-fix.py --apply    # execute + verify
"""
import sys, json, os, subprocess, urllib.request, urllib.error, ssl

APPLY = "--apply" in sys.argv
SEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets")
PROC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

NEW = ["216.150.1.1", "216.150.16.1"]
DEAD = {"76.76.21.21", "216.198.79.1"}
TTL = 600
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE

def http(method, url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode()[:300])

# ---- creds ----
def _cc_secret(name):
    """Fetch a secret from the CC secrets table (cloud). Bootstrap CC key: env first, else ~/.config mirror."""
    import os, json, urllib.request
    url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        k = json.load(open(os.path.expanduser("~/.config/pete-secrets/command-centre-supabase-keys.json")))
        url, key = k["url"], k["service_role_key"]
    req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/secrets?select=value&name=eq.{name}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())[0]["value"]

CF_TOKEN = _cc_secret("cloudflare-api-token")
def _gd():
    # general GoDaddy key (canary-detect / theleakyfinders / pipebusters) — NOT the lanzarotelates key
    txt = open(os.path.join(PROC, "godaddy-api-configuration.md")).read()
    import re
    k = re.search(r"Key:\s*(\S+)", txt).group(1); s = re.search(r"Secret:\s*(\S+)", txt).group(1)
    return k, s
def _ionos():
    d = json.load(open(os.path.join(SEC, "ionos-api.json")))
    return d["public_key"] + "." + d["secret"]

PLAN = {
    "cloudflare": ["sygma-solutions.com", "leaky-ledger.com"],
    "ionos":      ["sygmaportal.com", "leakguardlanzarote.com", "leakguard-manager.com", "oconnors.bar", "oconnorsirishpub.com"],
    "godaddy":    ["canary-detect.com", "theleakyfinders.es", "pipebusterslanzarote.com"],
}

def verify(domain):
    """Force-resolve to a new IP and load — proves the domain serves there (independent of DNS cache)."""
    try:
        out = subprocess.run(["curl", "-sS", "-m", "8", "-o", "/dev/null",
                              "--resolve", f"{domain}:443:{NEW[0]}", "-w", "%{http_code}", f"https://{domain}"],
                             capture_output=True, text=True, timeout=12).stdout.strip()
        return out
    except Exception as e:
        return f"err:{e}"

# ---------------- Cloudflare ----------------
def cf(domain):
    H = {"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}
    st, z = http("GET", f"https://api.cloudflare.com/client/v4/zones?name={domain}", H)
    if not z or not z.get("result"):
        return {"domain": domain, "provider": "cloudflare", "error": f"zone lookup {st}: {z}"}
    zid = z["result"][0]["id"]
    st, recs = http("GET", f"https://api.cloudflare.com/client/v4/zones/{zid}/dns_records?type=A&name={domain}", H)
    apex = recs.get("result", []) if recs else []
    before = [r["content"] for r in apex]
    ops = []
    if set(before) == set(NEW):
        return {"domain": domain, "provider": "cloudflare", "before": before, "after": before, "ops": ["already correct"]}
    if APPLY:
        keep = []
        for r in apex:
            if r["content"] in NEW and r["content"] not in keep:
                keep.append(r["content"])
            else:  # dead IP or duplicate → delete
                http("DELETE", f"https://api.cloudflare.com/client/v4/zones/{zid}/dns_records/{r['id']}", H); ops.append(f"del {r['content']}")
        for ip in NEW:
            if ip not in keep:
                http("POST", f"https://api.cloudflare.com/client/v4/zones/{zid}/dns_records", H,
                     {"type": "A", "name": domain, "content": ip, "ttl": TTL, "proxied": False}); ops.append(f"add {ip}")
    return {"domain": domain, "provider": "cloudflare", "before": before, "after": NEW if APPLY else f"(dry) → {NEW}", "ops": ops}

# ---------------- GoDaddy (PUT replaces the whole A/@ set — naturally idempotent) ----------------
def gd(domain):
    k, s = _gd()
    H = {"Authorization": f"sso-key {k}:{s}", "Content-Type": "application/json"}
    st, recs = http("GET", f"https://api.godaddy.com/v1/domains/{domain}/records/A/@", H)
    before = [r["data"] for r in recs] if isinstance(recs, list) else [f"err {st}"]
    if set(before) == set(NEW):
        return {"domain": domain, "provider": "godaddy", "before": before, "after": before, "ops": ["already correct"]}
    ops = []
    if APPLY:
        st, r = http("PUT", f"https://api.godaddy.com/v1/domains/{domain}/records/A/@", H,
                     [{"data": NEW[0], "ttl": TTL}, {"data": NEW[1], "ttl": TTL}])
        ops.append(f"PUT A/@ → {NEW} (HTTP {st})")
    return {"domain": domain, "provider": "godaddy", "before": before, "after": NEW if APPLY else f"(dry) → {NEW}", "ops": ops}

# ---------------- IONOS ----------------
def ionos(domain):
    key = _ionos()
    H = {"X-API-Key": key, "Content-Type": "application/json"}
    st, zones = http("GET", "https://api.hosting.ionos.com/dns/v1/zones", H)
    zid = next((z["id"] for z in (zones or []) if z.get("name") == domain), None)
    if not zid:
        return {"domain": domain, "provider": "ionos", "error": f"zone not found (HTTP {st})"}
    st, zone = http("GET", f"https://api.hosting.ionos.com/dns/v1/zones/{zid}?recordType=A", H)
    apex = [r for r in (zone or {}).get("records", []) if r.get("name") == domain and r.get("type") == "A"]
    before = [r["content"] for r in apex]
    if set(before) == set(NEW):
        return {"domain": domain, "provider": "ionos", "before": before, "after": before, "ops": ["already correct"]}
    ops = []
    if APPLY:
        for r in apex:  # delete dead/duplicate apex A
            http("DELETE", f"https://api.hosting.ionos.com/dns/v1/zones/{zid}/records/{r['id']}", H); ops.append(f"del {r['content']}")
        st, r = http("POST", f"https://api.hosting.ionos.com/dns/v1/zones/{zid}/records", H,
                     [{"name": domain, "type": "A", "content": ip, "ttl": TTL, "disabled": False} for ip in NEW])
        ops.append(f"POST 2×A → {NEW} (HTTP {st})")
    return {"domain": domain, "provider": "ionos", "before": before, "after": NEW if APPLY else f"(dry) → {NEW}", "ops": ops}

FN = {"cloudflare": cf, "godaddy": gd, "ionos": ionos}

def main():
    print(("APPLY" if APPLY else "DRY-RUN") + f" — repoint apex A → {NEW} (off dead {sorted(DEAD)})\n" + "=" * 78)
    results = []
    for provider, domains in PLAN.items():
        for d in domains:
            r = FN[provider](d)
            results.append(r)
            if "error" in r:
                print(f"  ❌ {d:26} [{provider}] ERROR: {r['error']}")
            else:
                v = ""
                if APPLY and "already correct" not in r["ops"]:
                    v = "  → serves HTTP " + verify(d)
                print(f"  {'✅' if APPLY else '·'} {d:26} [{provider:10}] {r['before']} → {r['after']}  {';'.join(r['ops'])}{v}")
    print("=" * 78)
    if not APPLY:
        print("DRY-RUN only. Re-run with --apply to execute.")
    else:
        print("Applied. Each changed domain verified via curl --resolve to the new IP (HTTP 200 = serving).")

if __name__ == "__main__":
    main()
