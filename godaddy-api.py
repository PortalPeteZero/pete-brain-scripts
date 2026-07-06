#!/usr/bin/env python3
"""GoDaddy API helper — DNS management + domain purchase across Pete's own GoDaddy domains.

Uses pete-godaddy-api.json (Pete's OWN account). NOT David's lanzarotelates key.
Note: GET /v1/domains/available is 403-gated on this account (GoDaddy reseller
threshold); domain purchase still works. Use --key lanzarote only for lanzarotelates.com.

Usage:
  VAULT=/tmp/pbs python3 godaddy-api.py whoami
  VAULT=/tmp/pbs python3 godaddy-api.py list
  VAULT=/tmp/pbs python3 godaddy-api.py records DOMAIN [TYPE] [NAME]
  VAULT=/tmp/pbs python3 godaddy-api.py set-record DOMAIN TYPE NAME DATA [TTL]   # replace record(s) of TYPE/NAME
  VAULT=/tmp/pbs python3 godaddy-api.py info DOMAIN
  VAULT=/tmp/pbs python3 godaddy-api.py buy DOMAIN [--dry]                        # validate (--dry) or purchase
"""

import json, os, sys, urllib.request, urllib.error

SECRET_NAME = "pete-godaddy-api.json"
KEY_PATH = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", SECRET_NAME)
    if os.environ.get("VAULT")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", SECRET_NAME)
)
BASE = "https://api.godaddy.com"

with open(KEY_PATH) as f:
    _cfg = json.load(f)
_HDR = {
    "Authorization": f"sso-key {_cfg['key']}:{_cfg['secret']}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=_HDR, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=40)
        raw = r.read().decode()
        return (r.status, json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        return (e.code, {"error": e.read().decode()[:400]})


def _contact(domain_for_contacts="canary-detect.com"):
    _, d = _req("GET", f"/v1/domains/{domain_for_contacts}")
    return (d or {}).get("contactRegistrant") or (d or {}).get("contactAdmin")


def _public_ip():
    try:
        return json.load(urllib.request.urlopen("https://api.ipify.org?format=json", timeout=15))["ip"]
    except Exception:
        return "1.1.1.1"


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    cmd = a[0]
    if cmd == "whoami":
        st, d = _req("GET", "/v1/domains?limit=200")
        print(json.dumps({"account": _cfg.get("account_holder"), "status": st,
                          "domain_count": len(d) if isinstance(d, list) else None}, indent=2))
    elif cmd == "list":
        st, d = _req("GET", "/v1/domains?limit=200")
        print(json.dumps([x.get("domain") for x in d] if isinstance(d, list) else d, indent=2))
    elif cmd == "info":
        st, d = _req("GET", f"/v1/domains/{a[1]}")
        print(json.dumps(d, indent=2))
    elif cmd == "records":
        path = f"/v1/domains/{a[1]}/records"
        if len(a) > 2:
            path += "/" + a[2] + (("/" + a[3]) if len(a) > 3 else "")
        st, d = _req("GET", path)
        print(json.dumps(d, indent=2))
    elif cmd == "set-record":
        domain, rtype, name, dataval = a[1], a[2], a[3], a[4]
        ttl = int(a[5]) if len(a) > 5 else 600
        body = [{"data": dataval, "ttl": ttl}]
        st, d = _req("PUT", f"/v1/domains/{domain}/records/{rtype}/{name}", body)
        print(json.dumps({"status": st, "resp": d, "set": {rtype: name, "->": dataval}}, indent=2))
    elif cmd == "buy":
        domain = a[1]
        dry = "--dry" in a
        contact = _contact()
        payload = {
            "domain": domain,
            "consent": {"agreementKeys": ["DNRA"], "agreedBy": _public_ip(),
                        "agreedAt": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")},
            "period": 1, "privacy": False, "renewAuto": True,
            "contactRegistrant": contact, "contactAdmin": contact,
            "contactBilling": contact, "contactTech": contact,
        }
        path = "/v1/domains/purchase/validate" if dry else "/v1/domains/purchase"
        st, d = _req("POST", path, payload)
        print(json.dumps({"status": st, "resp": d, "dry": dry}, indent=2))
    else:
        print(f"unknown command: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main()
