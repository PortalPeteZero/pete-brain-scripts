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
  python3 people-api.py update RESOURCE_NAME email|phone|org|name VALUE   # VALUE replaces; "a,b" sets both
  python3 people-api.py update RESOURCE_NAME +phone VALUE                # leading + APPENDS, keeps existing
  python3 people-api.py delete RESOURCE_NAME
  python3 people-api.py whoami                       # show auth info
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys

KEY = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware (thin-client boot materialises the key here)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
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

def ensure_group(label):
    """Find the USER contact group called `label`, creating it only if it does not exist.

    Added 25 Jul 2026 for the outward push (people plan B9). Anything the system puts on Pete's
    phone MUST carry a label, so what the system added stays separable from what he added himself
    -- otherwise a bad bulk push cannot be undone. Find-or-create, never duplicate.
    """
    r = api("GET", "/contactGroups", {"pageSize": 200})
    for g in r.get("contactGroups", []):
        if g.get("groupType") == "USER_CONTACT_GROUP" and g.get("name") == label:
            return g["resourceName"]
    made = api("POST", "/contactGroups", body={"contactGroup": {"name": label}})
    return made["resourceName"]


def add_contact(name, email, phone=None, org=None, group=None):
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
    if group:
        # `group` is a LABEL, resolved to (or created as) a real contactGroup here. Membership is set
        # in the same createContact call, so a contact can never land unlabelled -- a two-step
        # "create then label" would strand contacts if the second call failed.
        body["memberships"] = [{"contactGroupMembership":
                                {"contactGroupResourceName": ensure_group(group)}}]
    resp = api("POST", "/people:createContact", body=body)
    print(f"Created: {resp.get('resourceName')}")
    format_person(resp)

def update_contact(resource_name, field, value):
    # Get current contact first
    current = api("GET", f"/{resource_name}", {"personFields": PERSON_FIELDS})
    etag = current.get("etag", "")
    body = {"etag": etag}
    update_mask = ""
    # A single value REPLACES the whole list — that is how an update can silently destroy the
    # number you were not editing. Accept comma-separated values so a caller can preserve what is
    # already there, and offer +field to APPEND. (Found 26 Jul 2026 before it cost anything.)
    append = field.startswith("+")
    field = field.lstrip("+")
    vals = [v.strip() for v in str(value).split(",") if v.strip()]
    if field == "email":
        existing = [e.get("value") for e in (current.get("emailAddresses") or []) if e.get("value")]
        keep = (existing if append else []) + [v for v in vals if v not in (existing if append else [])]
        body["emailAddresses"] = [{"value": v} for v in keep]
        update_mask = "emailAddresses"
    elif field == "phone":
        existing = [e.get("value") for e in (current.get("phoneNumbers") or []) if e.get("value")]
        keep = (existing if append else []) + [v for v in vals if v not in (existing if append else [])]
        body["phoneNumbers"] = [{"value": v} for v in keep]
        update_mask = "phoneNumbers"
    elif field == "org":
        body["organizations"] = [{"name": value}]
        update_mask = "organizations"
    elif field == "name":
        parts = value.strip().split()
        body["names"] = [{"givenName": parts[0],
                          "familyName": " ".join(parts[1:]) if len(parts) > 1 else ""}]
        update_mask = "names"
    else:
        print(f"Unknown field: {field}. Use: email, phone, org, name"); sys.exit(1)
    resp = api("PATCH", f"/{resource_name}:updateContact",
               params={"updatePersonFields": update_mask}, body=body)
    print(f"Updated {field} for {resource_name}")
    format_person(resp)

def delete_contact(resource_name):
    """Delete a contact. The CC mirror DOES carry resource_name, so this is fully automatable --
    the old write tool used to claim otherwise and dead-ended the caller (fixed 26 Jul 2026)."""
    api("DELETE", f"/{resource_name}:deleteContact")
    print(f"Deleted {resource_name}")


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
        if len(args) < 3: print("Usage: people-api.py add 'Name' email [phone] [org] [--group LABEL]"); sys.exit(1)
        grp = None
        if "--group" in args:
            gi = args.index("--group")
            if gi + 1 < len(args):
                grp = args[gi + 1]
            args = args[:gi] + args[gi + 2:]      # keep the positional args positional
        add_contact(args[1], args[2], args[3] if len(args) > 3 else None,
                    args[4] if len(args) > 4 else None, group=grp)
    elif cmd == "groups":
        r = api("GET", "/contactGroups", {"pageSize": 200})
        for g in r.get("contactGroups", []):
            print(f"{g.get('groupType','?'):20} {g.get('name','')[:40]:42} {g.get('resourceName')}")
    elif cmd == "update":
        if len(args) < 4: print("Usage: people-api.py update RESOURCE_NAME field value"); sys.exit(1)
        update_contact(args[1], args[2], args[3])
    elif cmd == "delete":
        if len(args) < 2: print("Usage: people-api.py delete RESOURCE_NAME"); sys.exit(1)
        delete_contact(args[1])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
