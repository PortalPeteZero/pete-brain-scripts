#!/usr/bin/env python3
"""
business-profile-api.py -- Google Business Profile API helper
Auth: service account JWT + DWD (impersonates pete.ashcroft@sygma-solutions.com)
Requires: SA added as Manager on each Business Profile listing
Scopes: business.manage
Usage:
  python3 business-profile-api.py accounts              # list all business accounts
  python3 business-profile-api.py locations ACCOUNT_ID  # list locations in account
  python3 business-profile-api.py insights LOCATION_NAME [DAYS]  # views/searches/actions
  python3 business-profile-api.py reviews LOCATION_NAME          # list reviews
  python3 business-profile-api.py reply REVIEW_NAME "Reply text" # reply to a review
  python3 business-profile-api.py info LOCATION_NAME             # full location details
  python3 business-profile-api.py whoami                         # show auth info
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys
from datetime import date, timedelta

KEY = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware (bootstrap materialises the key)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
IMPERSONATE = "pete.ashcroft@sygma-solutions.com"
SCOPE = "https://www.googleapis.com/auth/business.manage"
ACCOUNTS_BASE = "https://mybusinessaccountmanagement.googleapis.com/v1"
INFO_BASE = "https://mybusinessbusinessinformation.googleapis.com/v1"
REVIEWS_BASE = "https://mybusiness.googleapis.com/v4"

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
        "iss": creds["client_email"], "sub": IMPERSONATE, "scope": SCOPE,
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

def api(method, url, params=None, body=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
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

def list_accounts():
    resp = api("GET", f"{ACCOUNTS_BASE}/accounts")
    accounts = resp.get("accounts", [])
    if not accounts:
        print("No business accounts found. Ensure SA is added as Manager on each listing.")
        return
    print(f"Business accounts ({len(accounts)}):\n")
    for a in accounts:
        print(f"  Name: {a.get('accountName', '?')}")
        print(f"  ID:   {a.get('name', '?')}")
        print(f"  Type: {a.get('type', '?')}")
        print()

def list_locations(account_id):
    # account_id can be full resource name like "accounts/123" or just the number
    if not account_id.startswith("accounts/"):
        account_id = f"accounts/{account_id}"
    resp = api("GET", f"{INFO_BASE}/{account_id}/locations",
               params={"readMask": "name,title,phoneNumbers,websiteUri,storefrontAddress"})
    locations = resp.get("locations", [])
    if not locations:
        print("No locations found."); return
    print(f"Locations ({len(locations)}):\n")
    for l in locations:
        print(f"  Title: {l.get('title', '?')}")
        print(f"  Name:  {l.get('name', '?')}")
        addr = l.get("storefrontAddress", {})
        addr_lines = addr.get("addressLines", [])
        if addr_lines:
            print(f"  Addr:  {', '.join(addr_lines)}, {addr.get('locality','')}")
        print()

def get_reviews(location_name):
    # location_name is like "accounts/123/locations/456"
    # Paginate to exhaustion: the endpoint caps a page at 50 and a listing can
    # hold hundreds, so a single call silently truncates (this was pageSize 20).
    reviews, token, avg, total = [], None, "?", "?"
    while True:
        params = {"pageSize": 50, "orderBy": "updateTime desc"}
        if token:
            params["pageToken"] = token
        resp = api("GET", f"{REVIEWS_BASE}/{location_name}/reviews", params=params)
        reviews.extend(resp.get("reviews", []))
        avg = resp.get("averageRating", avg)
        total = resp.get("totalReviewCount", total)
        token = resp.get("nextPageToken")
        if not token:
            break
    print(f"Reviews: {total} total, {len(reviews)} fetched, avg rating: {avg}\n")
    for r in reviews:
        rating = "★" * int(r.get("starRating", {}) if isinstance(r.get("starRating"), int) else 3)
        reviewer = r.get("reviewer", {}).get("displayName", "Anonymous")
        update = r.get("updateTime", "?")[:10]
        comment = r.get("comment", "(no comment)")[:200]
        reply = r.get("reviewReply", {}).get("comment", "")
        print(f"  [{update}] {reviewer} — {r.get('starRating','?')} stars")
        print(f"  {comment}")
        if reply:
            print(f"  → Reply: {reply[:100]}")
        print(f"  ID: {r.get('name','?')}")
        print()

def reply_to_review(review_name, reply_text):
    resp = api("PUT", f"{REVIEWS_BASE}/{review_name}/reply",
               body={"comment": reply_text})
    print(f"Reply posted to: {review_name}")

def location_info(location_name):
    # NB: no "businessHours" — not a field on v1 Business Information; including it
    # made every info call fail with 400 "Invalid field mask provided".
    read_mask = "name,title,phoneNumbers,websiteUri,storefrontAddress,regularHours,categories,openInfo"
    resp = api("GET", f"{INFO_BASE}/{location_name}", params={"readMask": read_mask})
    print(f"Title: {resp.get('title', '?')}")
    print(f"Name:  {resp.get('name', '?')}")
    phones = resp.get("phoneNumbers", {})
    if phones:
        print(f"Phone: {phones.get('primaryPhone','?')}")
    print(f"Web:   {resp.get('websiteUri','?')}")
    addr = resp.get("storefrontAddress", {})
    if addr:
        lines = ", ".join(addr.get("addressLines", []))
        print(f"Addr:  {lines}, {addr.get('locality','')}, {addr.get('postalCode','')}")
    cats = resp.get("categories", {})
    if cats:
        primary = cats.get("primaryCategory", {}).get("displayName", "?")
        print(f"Category: {primary}")

def whoami():
    print(f"Impersonating: {IMPERSONATE}")
    print(f"Service account: {creds['client_email']}")

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "accounts":
        list_accounts()
    elif cmd == "locations":
        if len(args) < 2: print("Usage: business-profile-api.py locations ACCOUNT_ID"); sys.exit(1)
        list_locations(args[1])
    elif cmd == "insights":
        print("Insights API requires v1 My Business Insights -- coming in next update.")
        print("Use Google Business Profile dashboard for now: business.google.com")
    elif cmd == "reviews":
        if len(args) < 2: print("Usage: business-profile-api.py reviews LOCATION_NAME"); sys.exit(1)
        get_reviews(args[1])
    elif cmd == "reply":
        if len(args) < 3: print("Usage: business-profile-api.py reply REVIEW_NAME 'text'"); sys.exit(1)
        reply_to_review(args[1], args[2])
    elif cmd == "info":
        if len(args) < 2: print("Usage: business-profile-api.py info LOCATION_NAME"); sys.exit(1)
        location_info(args[1])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
