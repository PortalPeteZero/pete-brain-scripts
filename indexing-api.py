#!/usr/bin/env python3
"""
indexing-api.py -- Google Indexing API helper
Auth: service account JWT (no DWD -- SA must be added as owner in Google Search Console)
Usage:
  python3 indexing-api.py submit URL                # request indexing of a URL
  python3 indexing-api.py delete URL                # notify Google URL is deleted
  python3 indexing-api.py status URL                # check indexing status
  python3 indexing-api.py batch urls.txt            # bulk submit from file (one URL per line)
  python3 indexing-api.py whoami                    # show auth info
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys

KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
SCOPE = "https://www.googleapis.com/auth/indexing"
BASE = "https://indexing.googleapis.com/v3"

with open(KEY) as f:
    creds = json.load(f)

_token_cache = {}

def get_token():
    now = int(time.time())
    if _token_cache.get("exp", 0) > now + 60:
        return _token_cache["tok"]
    def b64u(d):
        if isinstance(d, str): d = d.encode()
        return base64.urlsafe_b64encode(d).decode().rstrip("=")
    h = b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
    c = b64u(json.dumps({
        "iss": creds["client_email"], "scope": SCOPE,
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600, "iat": now,
    }))
    ts = f"{h}.{c}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(creds["private_key"]); kf = f.name
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", kf, "-binary"],
                         input=ts.encode(), capture_output=True).stdout
    os.unlink(kf)
    jwt = f"{ts}.{b64u(sig)}"
    r = urllib.request.Request("https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        }).encode())
    tok = json.loads(urllib.request.urlopen(r).read())["access_token"]
    _token_cache["tok"] = tok
    _token_cache["exp"] = now + 3600
    return tok

def api(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {get_token()}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req).read()
        return json.loads(resp) if resp else {}
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

def submit_url(url):
    resp = api("POST", "/urlNotifications:publish", {
        "url": url, "type": "URL_UPDATED"
    })
    print(f"Submitted: {url}")
    print(f"  Notified: {resp.get('urlNotificationMetadata', {}).get('latestUpdate', {}).get('notifyTime', '?')}")

def delete_url(url):
    resp = api("POST", "/urlNotifications:publish", {
        "url": url, "type": "URL_DELETED"
    })
    print(f"Deletion notified: {url}")

def check_status(url):
    encoded = urllib.parse.quote(url, safe="")
    resp = api("GET", f"/urlNotifications/metadata?url={encoded}")
    meta = resp.get("urlNotificationMetadata", {})
    print(f"URL: {meta.get('url', url)}")
    latest = meta.get("latestUpdate", {})
    if latest:
        print(f"Last notified: {latest.get('notifyTime', '?')}")
        print(f"Type: {latest.get('type', '?')}")
    else:
        print("No indexing notification found for this URL")

def batch_submit(filepath):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}"); sys.exit(1)
    with open(filepath) as f:
        urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    print(f"Submitting {len(urls)} URLs...")
    ok = 0
    for url in urls:
        try:
            api("POST", "/urlNotifications:publish", {"url": url, "type": "URL_UPDATED"})
            print(f"  ✓ {url}")
            ok += 1
        except SystemExit:
            print(f"  ✗ {url}")
        time.sleep(0.2)  # gentle rate limiting
    print(f"\nDone: {ok}/{len(urls)} submitted successfully")

def whoami():
    print(f"Service account: {creds['client_email']}")
    print(f"Project: {creds['project_id']}")
    print(f"Note: SA must be added as Owner in Google Search Console for each property")

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "submit":
        if len(args) < 2: print("Usage: indexing-api.py submit URL"); sys.exit(1)
        submit_url(args[1])
    elif cmd == "delete":
        if len(args) < 2: print("Usage: indexing-api.py delete URL"); sys.exit(1)
        delete_url(args[1])
    elif cmd == "status":
        if len(args) < 2: print("Usage: indexing-api.py status URL"); sys.exit(1)
        check_status(args[1])
    elif cmd == "batch":
        if len(args) < 2: print("Usage: indexing-api.py batch urls.txt"); sys.exit(1)
        batch_submit(args[1])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
