#!/usr/bin/env python3
"""
DVSA MOT History API helper -- the ONE sanctioned path for MOT history / vehicle test data.

Trade-access API approved 28 Jul 2026 (credentials in secret 'dvsa-mot-history-api.json',
pointer-only). Gives, per registration: make/model, first-used date, fuel type, and the FULL MOT
test history -- test dates, pass/fail, expiry, odometer readings, defects and advisories.
Sygma fleet lives in hub.fleet on the Sygma Platform DB; regs there carry a space ("YP20 SKV") --
this helper normalises (DVSA wants no space).

Auth: OAuth2 client-credentials against Microsoft login (token cached in /tmp for its lifetime)
+ x-api-key header on every call. Rate limits per DVSA docs (documentation.history.mot.api.gov.uk):
burst 10 req/s, quota 500,000/day. Errors are raised with their real reason, never swallowed.

Config: [[dvsa-mot-history-api-configuration]].

CLI:
  VAULT=/tmp/pbs python3 /tmp/pbs/mot-api.py vehicle "YP20 SKV"     # full record + MOT history (JSON)
  VAULT=/tmp/pbs python3 /tmp/pbs/mot-api.py summary "YP20 SKV"     # one-line: expiry, last result, mileage
  VAULT=/tmp/pbs python3 /tmp/pbs/mot-api.py token                  # prove auth works (prints expiry, not the token)
"""
import os, sys, json, time, urllib.request, urllib.parse

VAULT = os.environ.get("VAULT", "/tmp/pbs")
TOKEN_CACHE = "/tmp/.dvsa-mot-token.json"


def _creds():
    with open(f"{VAULT}/Library/processes/secrets/dvsa-mot-history-api.json") as f:
        return json.load(f)


def _token():
    """Client-credentials token, cached until 60s before expiry."""
    try:
        with open(TOKEN_CACHE) as f:
            cached = json.load(f)
        if cached["expires_at"] - 60 > time.time():
            return cached["access_token"]
    except Exception:
        pass
    c = _creds()
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "scope": c["scope"],
    }).encode()
    req = urllib.request.Request(c["token_url"], data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.load(r)
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    with open(TOKEN_CACHE, "w") as f:
        json.dump(tok, f)
    os.chmod(TOKEN_CACHE, 0o600)
    return tok["access_token"]


def _get(path):
    c = _creds()
    req = urllib.request.Request(c["base_url"] + path, headers={
        "Authorization": f"Bearer {_token()}",
        "X-API-Key": c["api_key"],
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise SystemExit(f"DVSA {e.code} on {path}: {detail}")


def normalise_reg(reg):
    return reg.replace(" ", "").upper()


def vehicle(reg):
    """Full vehicle record + MOT test history for one registration."""
    return _get(f"/vehicles/registration/{normalise_reg(reg)}")


def summary(reg):
    v = vehicle(reg)
    tests = v.get("motTests", [])
    latest = tests[0] if tests else None
    parts = [f"{v.get('registration', normalise_reg(reg))} {v.get('make','?')} {v.get('model','?')}"]
    if latest:
        odo = f"{latest.get('odometerValue','?')} {latest.get('odometerUnit','')}".strip()
        parts.append(f"last MOT {latest.get('completedDate','?')[:10]} {latest.get('testResult','?')} @ {odo}")
        if latest.get("expiryDate"):
            parts.append(f"expires {latest['expiryDate']}")
    else:
        parts.append("no MOT tests on record")
    return " | ".join(parts)


def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "token":
        _token()
        with open(TOKEN_CACHE) as f:
            exp = json.load(f)["expires_at"]
        print(f"token OK, expires in {int(exp - time.time())}s")
    elif cmd == "vehicle":
        print(json.dumps(vehicle(sys.argv[2]), indent=2))
    elif cmd == "summary":
        print(summary(sys.argv[2]))
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    _cli()
