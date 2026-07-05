#!/usr/bin/env python3
"""
admin-api.py -- Google Workspace Admin SDK helper
Auth: service account JWT + DWD (impersonates pete.ashcroft@sygma-solutions.com -- must be super admin)
Scopes: admin.directory.user, admin.directory.group, apps.groups.settings
        (apps.groups.settings + the Groups Settings API added 2026-07-05 -- lets us open a
         group to external senders so customer mail to a role address never bounces)
Usage:
  python3 admin-api.py users [DOMAIN]               # list all users (default: sygma-solutions.com)
  python3 admin-api.py user EMAIL                   # get user details
  python3 admin-api.py groups [DOMAIN]              # list all groups
  python3 admin-api.py group EMAIL                  # get group details + members
  python3 admin-api.py create-user FIRST LAST EMAIL TEMP_PASSWORD
  python3 admin-api.py suspend USER_EMAIL           # suspend user
  python3 admin-api.py restore USER_EMAIL           # restore suspended user
  python3 admin-api.py add-alias USER_EMAIL ALIAS_EMAIL
  python3 admin-api.py remove-alias USER_EMAIL ALIAS_EMAIL       # free an alias (e.g. before making it a group)
  python3 admin-api.py create-group EMAIL NAME [DESCRIPTION]     # create a Google Group
  python3 admin-api.py delete-group EMAIL                        # delete a group
  python3 admin-api.py add-to-group USER_EMAIL GROUP_EMAIL [ROLE]  # ROLE: MEMBER (default)|MANAGER|OWNER
  python3 admin-api.py remove-from-group USER_EMAIL GROUP_EMAIL  # remove a member
  python3 admin-api.py group-settings GROUP_EMAIL               # show posting/access settings
  python3 admin-api.py open-group GROUP_EMAIL                   # allow external senders (ANYONE_CAN_POST, no moderation)
  python3 admin-api.py whoami                       # show auth info
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys

KEY = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware (bootstrap materialises the key here)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
IMPERSONATE = "pete.ashcroft@sygma-solutions.com"
SCOPES = " ".join([
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/admin.directory.group",
    "https://www.googleapis.com/auth/apps.groups.settings",
])
BASE = "https://admin.googleapis.com/admin/directory/v1"
GSETTINGS = "https://www.googleapis.com/groups/v1/groups"   # Groups Settings API (whoCanPostMessage etc.)
DEFAULT_DOMAIN = "sygma-solutions.com"

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
        "iss": creds["client_email"], "sub": IMPERSONATE, "scope": SCOPES,
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

def list_users(domain=DEFAULT_DOMAIN):
    resp = api("GET", "/users", {"domain": domain, "maxResults": 100, "orderBy": "email"})
    users = resp.get("users", [])
    print(f"Users in {domain} ({len(users)}):\n")
    print(f"  {'EMAIL':<40} {'NAME':<30} {'STATUS'}")
    print("  " + "-" * 80)
    for u in users:
        status = "SUSPENDED" if u.get("suspended") else "active"
        print(f"  {u.get('primaryEmail',''):<40} {u.get('name',{}).get('fullName',''):<30} {status}")

def get_user(email):
    u = api("GET", f"/users/{email}")
    print(f"Email:     {u.get('primaryEmail')}")
    print(f"Name:      {u.get('name',{}).get('fullName')}")
    print(f"ID:        {u.get('id')}")
    print(f"Admin:     {u.get('isAdmin', False)}")
    print(f"Suspended: {u.get('suspended', False)}")
    print(f"Created:   {u.get('creationTime','?')[:10]}")
    print(f"Last login:{u.get('lastLoginTime','never')[:10]}")
    aliases = u.get("aliases", [])
    if aliases:
        print(f"Aliases:   {', '.join(aliases)}")

def list_groups(domain=DEFAULT_DOMAIN):
    resp = api("GET", "/groups", {"domain": domain, "maxResults": 100})
    groups = resp.get("groups", [])
    print(f"Groups in {domain} ({len(groups)}):\n")
    for g in groups:
        print(f"  {g.get('email',''):<40} {g.get('name',''):<30} ({g.get('directMembersCount',0)} members)")

def get_group(email):
    g = api("GET", f"/groups/{email}")
    print(f"Email:   {g.get('email')}")
    print(f"Name:    {g.get('name')}")
    print(f"Members: {g.get('directMembersCount', 0)}")
    print(f"ID:      {g.get('id')}")
    # List members
    members_resp = api("GET", f"/groups/{email}/members")
    members = members_resp.get("members", [])
    if members:
        print(f"\nMembers:")
        for m in members:
            print(f"  [{m.get('role','?')}] {m.get('email','?')}")

def create_user(first, last, email, temp_password):
    body = {
        "name": {"givenName": first, "familyName": last},
        "primaryEmail": email,
        "password": temp_password,
        "changePasswordAtNextLogin": True,
    }
    u = api("POST", "/users", body=body)
    print(f"Created user: {u.get('primaryEmail')} (ID: {u.get('id')})")
    print(f"Must change password at next login: True")

def suspend_user(email):
    api("PATCH", f"/users/{email}", body={"suspended": True})
    print(f"Suspended: {email}")

def restore_user(email):
    api("PATCH", f"/users/{email}", body={"suspended": False})
    print(f"Restored: {email}")

def add_alias(user_email, alias_email):
    api("POST", f"/users/{user_email}/aliases", body={"alias": alias_email})
    print(f"Added alias {alias_email} to {user_email}")

def add_to_group(user_email, group_email, role="MEMBER"):
    api("POST", f"/groups/{group_email}/members", body={"email": user_email, "role": role.upper()})
    print(f"Added {user_email} to {group_email} as {role.upper()}")

def remove_alias(user_email, alias_email):
    api("DELETE", f"/users/{user_email}/aliases/{alias_email}")
    print(f"Removed alias {alias_email} from {user_email}")

def remove_from_group(user_email, group_email):
    api("DELETE", f"/groups/{group_email}/members/{user_email}")
    print(f"Removed {user_email} from {group_email}")

def create_group(email, name, description=""):
    g = api("POST", "/groups", body={"email": email, "name": name, "description": description})
    print(f"Created group: {g.get('email')} (ID: {g.get('id')})")

def delete_group(email):
    api("DELETE", f"/groups/{email}")
    print(f"Deleted group: {email}")

def _gsettings(method, group_email, body=None):
    """Groups Settings API call (separate base URL from the Directory API)."""
    url = f"{GSETTINGS}/{group_email}?alt=json"
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {get_token()}"}
    if data: headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req).read()
        return json.loads(resp) if resp else {}
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr); sys.exit(1)

def group_settings(group_email):
    s = _gsettings("GET", group_email)
    for k in ("whoCanPostMessage", "allowExternalMembers", "messageModerationLevel",
              "spamModerationLevel", "whoCanJoin", "whoCanViewGroup", "isArchived"):
        print(f"  {k:<22} {s.get(k)}")

def open_group(group_email):
    """Allow anyone (incl. external customers) to email the group, no moderation -- so role-address mail never bounces."""
    s = _gsettings("PUT", group_email, {
        "whoCanPostMessage": "ANYONE_CAN_POST",
        "messageModerationLevel": "MODERATE_NONE",
        "whoCanViewGroup": "ALL_MEMBERS_CAN_VIEW",
        "isArchived": "true",
    })
    print(f"Opened {group_email} to external senders: whoCanPostMessage={s.get('whoCanPostMessage')}")

def whoami():
    u = api("GET", f"/users/{IMPERSONATE}")
    print(f"Impersonating: {u.get('primaryEmail')} ({u.get('name',{}).get('fullName')})")
    print(f"Is admin: {u.get('isAdmin', False)}")

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "users":
        list_users(args[1] if len(args) > 1 else DEFAULT_DOMAIN)
    elif cmd == "user":
        if len(args) < 2: print("Usage: admin-api.py user EMAIL"); sys.exit(1)
        get_user(args[1])
    elif cmd == "groups":
        list_groups(args[1] if len(args) > 1 else DEFAULT_DOMAIN)
    elif cmd == "group":
        if len(args) < 2: print("Usage: admin-api.py group EMAIL"); sys.exit(1)
        get_group(args[1])
    elif cmd == "create-user":
        if len(args) < 5: print("Usage: admin-api.py create-user FIRST LAST EMAIL PASSWORD"); sys.exit(1)
        create_user(args[1], args[2], args[3], args[4])
    elif cmd == "suspend":
        if len(args) < 2: print("Usage: admin-api.py suspend EMAIL"); sys.exit(1)
        suspend_user(args[1])
    elif cmd == "restore":
        if len(args) < 2: print("Usage: admin-api.py restore EMAIL"); sys.exit(1)
        restore_user(args[1])
    elif cmd == "add-alias":
        if len(args) < 3: print("Usage: admin-api.py add-alias USER_EMAIL ALIAS_EMAIL"); sys.exit(1)
        add_alias(args[1], args[2])
    elif cmd == "add-to-group":
        if len(args) < 3: print("Usage: admin-api.py add-to-group USER_EMAIL GROUP_EMAIL [ROLE]"); sys.exit(1)
        add_to_group(args[1], args[2], args[3] if len(args) > 3 else "MEMBER")
    elif cmd == "remove-alias":
        if len(args) < 3: print("Usage: admin-api.py remove-alias USER_EMAIL ALIAS_EMAIL"); sys.exit(1)
        remove_alias(args[1], args[2])
    elif cmd == "remove-from-group":
        if len(args) < 3: print("Usage: admin-api.py remove-from-group USER_EMAIL GROUP_EMAIL"); sys.exit(1)
        remove_from_group(args[1], args[2])
    elif cmd == "create-group":
        if len(args) < 3: print("Usage: admin-api.py create-group EMAIL NAME [DESCRIPTION]"); sys.exit(1)
        create_group(args[1], args[2], args[3] if len(args) > 3 else "")
    elif cmd == "delete-group":
        if len(args) < 2: print("Usage: admin-api.py delete-group EMAIL"); sys.exit(1)
        delete_group(args[1])
    elif cmd == "group-settings":
        if len(args) < 2: print("Usage: admin-api.py group-settings GROUP_EMAIL"); sys.exit(1)
        group_settings(args[1])
    elif cmd == "open-group":
        if len(args) < 2: print("Usage: admin-api.py open-group GROUP_EMAIL"); sys.exit(1)
        open_group(args[1])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
