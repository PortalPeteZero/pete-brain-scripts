#!/usr/bin/env python3
"""companies-house-api.py — thin Companies House Public Data API client (Plan A helper).

Auth: HTTP Basic, API key as username, blank password. Base: https://api.company-information.service.gov.uk
Key: VAULT/Library/processes/secrets/companies-house-api-key (see [[companies-house-api-configuration]]).

Usage:
  companies-house-api.py profile   <company_number>   → company profile JSON
  companies-house-api.py officers  <company_number>   → officers list JSON
  companies-house-api.py psc       <company_number>   → persons-with-significant-control JSON
  companies-house-api.py bundle    <company_number>   → {profile, officers, psc} in one object
"""
import sys, os, json, base64, urllib.request, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
BASE = "https://api.company-information.service.gov.uk"
KEY = open(os.path.join(VAULT, "Library/processes/secrets/companies-house-api-key")).read().strip()
AUTH = base64.b64encode(f"{KEY}:".encode()).decode()


def get(path):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Basic {AUTH}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()[:300], "_path": path}


def profile(n):
    return get(f"/company/{n}")


def officers(n):
    return get(f"/company/{n}/officers")


def psc(n):
    return get(f"/company/{n}/persons-with-significant-control")


def bundle(n):
    return {"profile": profile(n), "officers": officers(n), "psc": psc(n)}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, num = sys.argv[1], sys.argv[2]
    fn = {"profile": profile, "officers": officers, "psc": psc, "bundle": bundle}.get(cmd)
    if not fn:
        print(f"unknown cmd {cmd}"); sys.exit(1)
    print(json.dumps(fn(num), indent=2, ensure_ascii=False))
