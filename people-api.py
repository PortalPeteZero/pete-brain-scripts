#!/usr/bin/env python3
"""
people-api.py -- Google People API (Contacts) helper
Auth: service account JWT + DWD (impersonates pete.ashcroft@sygma-solutions.com)
Scope: https://www.googleapis.com/auth/contacts
Usage:
  python3 people-api.py search "Wayne Clarke"        # search contacts
  python3 people-api.py get RESOURCE_NAME            # get contact by resource name
  python3 people-api.py list [N]                     # list all contacts (default 50)
  python3 people-api.py add "Name" email [phone] [org]  # add new contact
  python3 people-api.py update RESOURCE_NAME email NEW_EMAIL
  python3 people-api.py whoami                       # show auth info
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys

KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
IMPERSONATE = "pete.ashcroft@sygma-solutions.com"
SCOPE = "https://www.googleapis.com/auth/contacts"
BASE = "https://people.googleapis.com/v1"

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

def api(method, path, params=None, body=None):
    url = BASE + path
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

PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations,addresses"

def format_person(p):
    name = p.get("names", [{}])[0].get("displayName", "(no name)")
    resource = p.get("resourceName", "")
    emails = [e.get("value","") for e in p.get("emailAddresses", [])]
    phones = [ph.get("value","") for ph in p.get("phoneNumbers", [])]
    orgs = [o.get("name","") for o in p.get("organizations", [])]
    print(f"  Name:     {name}")
    print(f"  Resource: {resource}")
    if emails: print(f"  Email:    {', '.join(emails)}")
    if phones: print(f"  Phone:    {', '.join(phones)}")
    if orgs:   print(f"  Org:      {', '.join(orgs)}")
    print()

def search_contacts(query):
    resp = api("GET", "/people:searchContacts", {
        "query": query, "readMask": PERSON_FIELDS, "pageSize": 20
    })
    results = resp.get("results", [])
    if not results:
        print(f"No contacts found for: {query}"); return
    print(f"Found {len(results)} result(s):\n")
    for r in results:
        format_person(r.get("person", {}))

def get_contact(resource_name):
    resp = api("GET", f"/{resource_name}", {"personFields": PERSON_FIELDS})
    format_person(resp)

def list_contacts(page_size=50):
    resp = api("GET", "/people/me/connections", {
        "personFields": PERSON_FIELDS, "pageSize": page_size,
        "sortOrder": "LAST_NAME_ASCENDING"
    })
    connections = resp.get("connections", [])
    print(f"Contacts ({len(connections)}):\n")
    for p in connections:
        format_person(p)

def add_contact(name, email, phone=None, org=None):
    parts = name.split(" ", 1)
    given = parts[0]
    family = parts[1] if len(parts) > 1 else ""
    body = {
        "names": [{"givenName": given, "familyName": family}],
        "emailAddresses": [{"value": email}],
    }
    if phone:
        body["phoneNumbers"] = [{"value": phone}]
    if org:
        body["organizations"] = [{"name": org}]
    resp = api("POST", "/people:createContact", body=body)
    print(f"Created: {resp.get('resourceName')}")
    format_person(resp)

def update_contact(resource_name, field, value):
    # Get current contact first
    current = api("GET", f"/{resource_name}", {"personFields": PERSON_FIELDS})
    etag = current.get("etag", "")
    body = {"etag": etag}
    update_mask = ""
    if field == "email":
        body["emailAddresses"] = [{"value": value}]
        update_mask = "emailAddresses"
    elif field == "phone":
        body["phoneNumbers"] = [{"value": value}]
        update_mask = "phoneNumbers"
    elif field == "org":
        body["organizations"] = [{"name": value}]
        update_mask = "organizations"
    else:
        print(f"Unknown field: {field}. Use: email, phone, org"); sys.exit(1)
    resp = api("PATCH", f"/{resource_name}:updateContact",
               params={"updatePersonFields": update_mask}, body=body)
    print(f"Updated {field} for {resource_name}")
    format_person(resp)

def whoami():
    # people/me needs profile scope; use connections list to verify contacts auth
    resp = api("GET", "/people/me/connections", {"pageSize": "1", "personFields": "names"})
    total = resp.get("totalPeople", "?")
    print(f"Impersonating: {IMPERSONATE}")
    print(f"Scope: {SCOPE}")
    print(f"Contacts accessible: {total} total contacts")

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "search":
        if len(args) < 2: print("Usage: people-api.py search QUERY"); sys.exit(1)
        search_contacts(args[1])
    elif cmd == "get":
        if len(args) < 2: print("Usage: people-api.py get RESOURCE_NAME"); sys.exit(1)
        get_contact(args[1])
    elif cmd == "list":
        list_contacts(int(args[1]) if len(args) > 1 else 50)
    elif cmd == "add":
        if len(args) < 3: print("Usage: people-api.py add 'Name' email [phone] [org]"); sys.exit(1)
        add_contact(args[1], args[2], args[3] if len(args) > 3 else None, args[4] if len(args) > 4 else None)
    elif cmd == "update":
        if len(args) < 4: print("Usage: people-api.py update RESOURCE_NAME field value"); sys.exit(1)
        update_contact(args[1], args[2], args[3])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
