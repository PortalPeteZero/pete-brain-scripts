#!/usr/bin/env python3
"""
Cloudflare API helper — zones, DNS records, proxy, SSL, cache, activation.

The helper-first tool for ALL Cloudflare work (config doc: cloudflare-api-configuration
in vault_notes). Uses the `claude-zone-admin` token (created 2026-07-04, Pete-approved:
all zone-scoped permissions on all domains + zone CREATE + activation_check; no
billing/members), falling back to the older `cloudflare-api-token` (lively-meadow:
DNS/settings/cache edit, zone read — CANNOT create zones or run activation checks).

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/cloudflare-api.py <command> [args]

Commands:
  zones                                   List all zones (name, id, status, plan)
  zone <domain>                           Zone detail (status, NS, ssl mode)
  records <domain>                        List DNS records
  add-record <domain> <type> <name> <content> [--priority N] [--ttl N] [--proxied]
                                          Create a DNS record (name '@' = apex)
  delete-record <domain> <record-id>      Delete a DNS record by id
  set-proxy <domain> <record-name> <on|off>
                                          Toggle orange/grey cloud on matching A/AAAA/CNAME records
  ssl-mode <domain> [off|flexible|full|strict]
                                          Get (no arg) or set the zone SSL mode
  create-zone <domain>                    Add a domain to the account (full setup, free plan)
  activation-check <domain>               Ask Cloudflare to re-check nameserver delegation NOW
  purge <domain>                          Purge the zone's entire cache

Standing config (2026-07-04): every Pete Vercel domain is PROXIED (orange, SSL full) —
apex A 216.150.1.1 + 216.150.16.1, www CNAME cname.vercel-dns.com. Account NS pair:
aliza.ns.cloudflare.com + dax.ns.cloudflare.com. See cloudflare-api-configuration.
"""

import json
import sys
import urllib.request
import urllib.error

def _cc_secret(name):
    """Fetch a secret from the CC secrets table (cloud); local materialised file first."""
    import os
    local = os.path.join(os.environ.get("VAULT", "/tmp/pbs"), "Library/processes/secrets", name)
    if os.path.exists(local):
        return open(local).read().strip()
    url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        k = json.load(open(os.path.expanduser("~/.config/pete-secrets/command-centre-supabase-keys.json")))
        url, key = k["url"], k["service_role_key"]
    req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/secrets?select=value&name=eq.{name}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())[0]["value"]

try:
    TOKEN = _cc_secret("cloudflare-zone-admin-token")
except Exception:
    TOKEN = _cc_secret("cloudflare-api-token")

ACCOUNT_ID = "553a268e4389e1b93d4834856bef3cbd"
BASE = "https://api.cloudflare.com/client/v4"


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        d = json.loads(e.read())
    if not d.get("success"):
        sys.exit(f"cloudflare-api ERROR: {d.get('errors')}")
    return d["result"]


def zone_id(domain):
    zones = api(f"/zones?name={domain}")
    if not zones:
        sys.exit(f"cloudflare-api: no zone named {domain} in the account (see `zones`)")
    return zones[0]["id"]


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(0)
    cmd, args = sys.argv[1], sys.argv[2:]

    if cmd == "zones":
        for z in api("/zones?per_page=50"):
            print(f"{z['name']:32} {z['id']}  {z['status']:12} {z['plan']['name']}")

    elif cmd == "zone":
        z = api(f"/zones/{zone_id(args[0])}")
        ssl = api(f"/zones/{z['id']}/settings/ssl")
        print(json.dumps({"name": z["name"], "id": z["id"], "status": z["status"],
                          "name_servers": z.get("name_servers"), "ssl_mode": ssl["value"]}, indent=2))

    elif cmd == "records":
        zid = zone_id(args[0])
        for r in api(f"/zones/{zid}/dns_records?per_page=200"):
            proxy = "orange" if r.get("proxied") else "grey  "
            prio = f" prio:{r['priority']}" if r.get("priority") is not None else ""
            print(f"{r['id'][:8]}  {r['type']:6} {r['name']:45} -> {r['content'][:60]:62} {proxy}{prio}")

    elif cmd == "add-record":
        domain, rtype, name, content = args[0], args[1].upper(), args[2], args[3]
        body = {"type": rtype, "name": name, "content": content,
                "ttl": 1, "proxied": "--proxied" in args}
        if "--ttl" in args: body["ttl"] = int(args[args.index("--ttl") + 1])
        if "--priority" in args: body["priority"] = int(args[args.index("--priority") + 1])
        if rtype in ("MX", "TXT", "NS", "SRV"): body.pop("proxied")
        r = api(f"/zones/{zone_id(domain)}/dns_records", "POST", body)
        print(f"created {r['type']} {r['name']} -> {r['content']} (id {r['id']})")

    elif cmd == "delete-record":
        api(f"/zones/{zone_id(args[0])}/dns_records/{args[1]}", "DELETE")
        print(f"deleted record {args[1]}")

    elif cmd == "set-proxy":
        domain, rname, state = args[0], args[1], args[2] == "on"
        full = domain if rname == "@" else (rname if rname.endswith(domain) else f"{rname}.{domain}")
        zid = zone_id(domain)
        hits = [r for r in api(f"/zones/{zid}/dns_records?per_page=200")
                if r["name"] == full and r["type"] in ("A", "AAAA", "CNAME")]
        if not hits:
            sys.exit(f"cloudflare-api: no A/AAAA/CNAME records named {full}")
        for r in hits:
            api(f"/zones/{zid}/dns_records/{r['id']}", "PATCH", {"proxied": state})
            print(f"{r['type']} {r['name']} -> proxied={state}")

    elif cmd == "ssl-mode":
        zid = zone_id(args[0])
        if len(args) > 1:
            r = api(f"/zones/{zid}/settings/ssl", "PATCH", {"value": args[1]})
            print(f"ssl mode set: {r['value']}")
        else:
            print(api(f"/zones/{zid}/settings/ssl")["value"])

    elif cmd == "create-zone":
        z = api("/zones", "POST", {"name": args[0], "account": {"id": ACCOUNT_ID}, "type": "full"})
        print(f"zone created: {z['name']} (id {z['id']}, status {z['status']})")
        print(f"nameservers: {', '.join(z.get('name_servers', []))}")
        print("NEXT: replicate records BEFORE switching NS at the registrar (byte-verify parity; "
              "page through the source panel — hidden records like SES/Resend DKIM won't show in dig). "
              "Registrar NS changes are PANEL-ONLY at IONOS + GoDaddy. Then run activation-check.")

    elif cmd == "activation-check":
        api(f"/zones/{zone_id(args[0])}/activation_check", "PUT")
        print("activation check triggered — poll `zone` for status=active")

    elif cmd == "purge":
        api(f"/zones/{zone_id(args[0])}/purge_cache", "POST", {"purge_everything": True})
        print(f"cache purged for {args[0]}")

    else:
        sys.exit(f"unknown command {cmd!r} — run with no args for usage")


if __name__ == "__main__":
    main()
