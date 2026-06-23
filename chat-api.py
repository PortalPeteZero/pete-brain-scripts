#!/usr/bin/env python3
"""
Google Chat API helper -- single canonical path for all Chat work.

Parallels `gmail-api.py` and `calendar-api.py` in pattern. Uses the shared
Google service account (`sygma-seo-reader@sygma-seo-tools.iam.gserviceaccount.com`)
via domain-wide delegation, impersonating pete.ashcroft@sygma-solutions.com.

Scopes (all granted via DWD on 2026-04-26):
  - https://www.googleapis.com/auth/chat.spaces        # spaces management
  - https://www.googleapis.com/auth/chat.messages      # message read/write
  - https://www.googleapis.com/auth/chat.memberships   # membership management
  - https://www.googleapis.com/auth/chat.delete        # delete messages

CLI usage:
  python3 chat-api.py spaces                                  # list all spaces Pete is in
  python3 chat-api.py space SPACE_ID                          # get a single space
  python3 chat-api.py members SPACE_ID                        # list members of a space
  python3 chat-api.py messages SPACE_ID [LIMIT]               # list recent messages in a space (default 25)
  python3 chat-api.py message SPACE_ID MESSAGE_ID             # get a single message
  python3 chat-api.py send SPACE_ID "TEXT"                    # send a text message to a space
  python3 chat-api.py update MESSAGE_NAME "NEW TEXT"          # update a message (name = full path: spaces/X/messages/Y)
  python3 chat-api.py delete MESSAGE_NAME                     # delete a message
  python3 chat-api.py whoami

Library usage:
  from chat_api import ChatAPI
  c = ChatAPI()
  c.list_spaces()
  c.send_message("spaces/AAAA/", "Hello world")
  c.list_messages("spaces/AAAA/", page_size=20)
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

KEY_PATH = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware on Railway (bootstrap materialises the key)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
DEFAULT_USER = "pete.ashcroft@sygma-solutions.com"
SCOPES = " ".join([
    "https://www.googleapis.com/auth/chat.spaces",
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.memberships",
    "https://www.googleapis.com/auth/chat.delete",
])
BASE = "https://chat.googleapis.com/v1"


def _b64u(data):
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class ChatAPI:
    def __init__(self, user=DEFAULT_USER, key_path=KEY_PATH, scope=SCOPES):
        self.user = user
        with open(os.path.abspath(key_path)) as f:
            self.creds = json.load(f)
        self.scope = scope
        self._token = None
        self._token_exp = 0

    # --- auth -----------------------------------------------------------------

    def _get_token(self):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        now = int(time.time())
        header = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
        claim = _b64u(json.dumps({
            "iss": self.creds["client_email"],
            "sub": self.user,
            "scope": self.scope,
            "aud": "https://oauth2.googleapis.com/token",
            "exp": now + 3600,
            "iat": now,
        }))
        ts = f"{header}.{claim}"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(self.creds["private_key"])
            kf = f.name
        try:
            sig = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", kf, "-binary"],
                input=ts.encode(), capture_output=True, check=True,
            ).stdout
        finally:
            os.unlink(kf)
        jwt = f"{ts}.{_b64u(sig)}"
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt,
            }).encode(),
        )
        resp = json.loads(urllib.request.urlopen(req).read())
        self._token = resp["access_token"]
        self._token_exp = now + resp.get("expires_in", 3600)
        return self._token

    def _call(self, method, path, body=None, query=None):
        url = f"{BASE}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            raise RuntimeError(f"Chat API {method} {path} -> HTTP {e.code}: {msg}") from e

    # --- spaces ---------------------------------------------------------------

    def list_spaces(self, page_size=100):
        out = []
        token = None
        while True:
            q = {"pageSize": page_size}
            if token:
                q["pageToken"] = token
            r = self._call("GET", "/spaces", query=q)
            out.extend(r.get("spaces", []))
            token = r.get("nextPageToken")
            if not token:
                break
        return out

    def get_space(self, space_name):
        # space_name like "spaces/AAAA"
        if not space_name.startswith("spaces/"):
            space_name = f"spaces/{space_name}"
        return self._call("GET", f"/{space_name}")

    # --- members --------------------------------------------------------------

    def list_members(self, space_name):
        if not space_name.startswith("spaces/"):
            space_name = f"spaces/{space_name}"
        out = []
        token = None
        while True:
            q = {"pageSize": 100}
            if token:
                q["pageToken"] = token
            r = self._call("GET", f"/{space_name}/members", query=q)
            out.extend(r.get("memberships", []))
            token = r.get("nextPageToken")
            if not token:
                break
        return out

    # --- messages -------------------------------------------------------------

    def list_messages(self, space_name, page_size=25):
        if not space_name.startswith("spaces/"):
            space_name = f"spaces/{space_name}"
        return self._call(
            "GET", f"/{space_name}/messages",
            query={"pageSize": page_size, "orderBy": "createTime desc"},
        ).get("messages", [])

    def get_message(self, message_name):
        # message_name like "spaces/AAAA/messages/BBBB"
        return self._call("GET", f"/{message_name}")

    def send_message(self, space_name, text):
        if not space_name.startswith("spaces/"):
            space_name = f"spaces/{space_name}"
        return self._call("POST", f"/{space_name}/messages", body={"text": text})

    def update_message(self, message_name, text):
        # PATCH spaces/X/messages/Y with updateMask=text
        return self._call(
            "PATCH", f"/{message_name}",
            body={"text": text},
            query={"updateMask": "text"},
        )

    def delete_message(self, message_name):
        return self._call("DELETE", f"/{message_name}")


# --- CLI ----------------------------------------------------------------------

def _fmt_space(s):
    name = s.get("name", "")
    display = s.get("displayName") or "(no name — DM)"
    space_type = s.get("spaceType", "?")
    members = s.get("membershipCount", {}).get("joinedDirectHumanUserCount", "?")
    return f"  {name:40s}  {space_type:10s}  members={members}  {display}"


def _fmt_member(m):
    name = m.get("name", "")
    role = m.get("role", "?")
    state = m.get("state", "?")
    member = m.get("member", {})
    display = member.get("displayName") or member.get("name", "?")
    return f"  {name:60s}  {role:18s}  {state:10s}  {display}"


def _fmt_msg(m):
    name = m.get("name", "")
    sender = m.get("sender", {}).get("name", "?")
    created = m.get("createTime", "?")
    text = (m.get("text") or "").replace("\n", " ")[:80]
    return f"  {created[:19]}  {sender:30s}  {text}"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    c = ChatAPI()

    if cmd == "whoami":
        tok = c._get_token()
        print(f"User:   {c.user}")
        print(f"Scopes: {c.scope}")
        print(f"Token:  {tok[:32]}... (length {len(tok)})")

    elif cmd == "spaces":
        spaces = c.list_spaces()
        print(f"Found {len(spaces)} spaces:")
        for s in spaces:
            print(_fmt_space(s))

    elif cmd == "space":
        s = c.get_space(sys.argv[2])
        print(json.dumps(s, indent=2))

    elif cmd == "members":
        ms = c.list_members(sys.argv[2])
        print(f"Found {len(ms)} members in {sys.argv[2]}:")
        for m in ms:
            print(_fmt_member(m))

    elif cmd == "messages":
        space = sys.argv[2]
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 25
        ms = c.list_messages(space, page_size=limit)
        print(f"Found {len(ms)} messages in {space}:")
        for m in ms:
            print(_fmt_msg(m))

    elif cmd == "message":
        m = c.get_message(f"{sys.argv[2]}/messages/{sys.argv[3]}")
        print(json.dumps(m, indent=2))

    elif cmd == "send":
        out = c.send_message(sys.argv[2], sys.argv[3])
        print("Sent. Message name:", out.get("name"))

    elif cmd == "update":
        out = c.update_message(sys.argv[2], sys.argv[3])
        print("Updated. New text:", out.get("text"))

    elif cmd == "delete":
        c.delete_message(sys.argv[2])
        print(f"Deleted {sys.argv[2]}")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
