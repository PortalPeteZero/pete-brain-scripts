#!/usr/bin/env python3
"""
Google Tag Manager API helper -- single canonical path for all GTM work.

Service account: sygma-seo-reader@sygma-seo-tools.iam.gserviceaccount.com
Auth:            Service account JWT (no DWD -- SA added as Edit user to each container)
Scopes:          https://www.googleapis.com/auth/tagmanager.edit.containers
                 https://www.googleapis.com/auth/tagmanager.edit.containerversions
                 https://www.googleapis.com/auth/tagmanager.publish

Known containers:
  GTM-WNXQHCB9   Sygma Solutions   account=6346652892  container=247634883  workspace=4
  GTM-5KDK6XJV   Canary Detect     account=6346652892  container=251899706  workspace=2

Usage (CLI):
  python3 gtm-api.py containers GTM-WNXQHCB9
  python3 gtm-api.py workspaces GTM-WNXQHCB9
  python3 gtm-api.py tags GTM-WNXQHCB9
  python3 gtm-api.py triggers GTM-WNXQHCB9
  python3 gtm-api.py variables GTM-WNXQHCB9
  python3 gtm-api.py tag GTM-WNXQHCB9 TAG_ID
  python3 gtm-api.py create-tag GTM-WNXQHCB9 '{"name":"...","type":"...","parameter":[...],"firingTriggerId":["ID"]}'
  python3 gtm-api.py update-tag GTM-WNXQHCB9 TAG_ID '{"name":"new-name"}'
  python3 gtm-api.py delete-tag GTM-WNXQHCB9 TAG_ID
  python3 gtm-api.py create-trigger GTM-WNXQHCB9 '{"name":"...","type":"...",...}'
  python3 gtm-api.py create-variable GTM-WNXQHCB9 '{"name":"...","type":"...",...}'
  python3 gtm-api.py publish GTM-WNXQHCB9 "Version note"
  python3 gtm-api.py versions GTM-WNXQHCB9
  python3 gtm-api.py whoami

Usage (library):
  from gtm_api import GTMAPI
  g = GTMAPI()
  g.list_tags("GTM-WNXQHCB9")
  g.create_tag("GTM-WNXQHCB9", {...})
  g.publish("GTM-WNXQHCB9", "note")
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

KEY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "secrets", "google-seo-service-account.json",
)
SCOPE = (
    "https://www.googleapis.com/auth/tagmanager.edit.containers "
    "https://www.googleapis.com/auth/tagmanager.edit.containerversions "
    "https://www.googleapis.com/auth/tagmanager.publish"
)
BASE = "https://www.googleapis.com/tagmanager/v2"

# Known container registry -- keyed by public GTM-XXXXX ID
CONTAINERS = {
    "GTM-WNXQHCB9": {
        "account_id": "6346652892",
        "container_id": "247634883",
        "workspace_id": "6",
        "label": "Sygma Solutions",
    },
    "GTM-5KDK6XJV": {
        "account_id": "6346652892",
        "container_id": "251899706",
        "workspace_id": "3",
        "label": "Canary Detect",
    },
}


def _b64u(data):
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class GTMAPI:
    def __init__(self, key_path=KEY_PATH, scope=SCOPE):
        with open(os.path.abspath(key_path)) as f:
            self.creds = json.load(f)
        self.scope = scope
        self._token = None
        self._token_exp = 0

    # --- auth (service account, no DWD) ---------------------------------------

    def _get_token(self):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        now = int(time.time())
        header = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
        claim = _b64u(json.dumps({
            "iss": self.creds["client_email"],
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
            url += "?" + urllib.parse.urlencode(query)
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
            raise RuntimeError(f"GTM API {method} {path} -> HTTP {e.code}: {msg}") from e

    # --- container resolution -------------------------------------------------

    def _resolve(self, public_id):
        """Return (account_id, container_id, workspace_id) for a GTM-XXXXX public ID.
        Workspace is resolved LIVE: GTM consumes a workspace on each publish and spawns a
        new Default Workspace, so a hardcoded id goes stale ("Workspace is already submitted").
        Fetch the current Default (else newest) workspace; fall back to the configured id only
        if the lookup fails."""
        if public_id in CONTAINERS:
            c = CONTAINERS[public_id]
            aid, cid = c["account_id"], c["container_id"]
            try:
                wss = self._call("GET", f"/accounts/{aid}/containers/{cid}/workspaces").get("workspace", [])
                if wss:
                    default = next((w for w in wss if w.get("name") == "Default Workspace"), None)
                    wid = (default or sorted(wss, key=lambda w: int(w["workspaceId"]))[-1])["workspaceId"]
                    return aid, cid, wid
            except Exception:
                pass
            return c["account_id"], c["container_id"], c["workspace_id"]
        # Fallback: scan accounts/containers to find it
        accounts = self._call("GET", "/accounts").get("account", [])
        for acc in accounts:
            aid = acc["accountId"]
            containers = self._call("GET", f"/accounts/{aid}/containers").get("container", [])
            for con in containers:
                if con.get("publicId") == public_id:
                    cid = con["containerId"]
                    workspaces = self._call("GET", f"/accounts/{aid}/containers/{cid}/workspaces").get("workspace", [])
                    wid = workspaces[0]["workspaceId"] if workspaces else "1"
                    CONTAINERS[public_id] = {"account_id": aid, "container_id": cid, "workspace_id": wid, "label": con.get("name", public_id)}
                    return aid, cid, wid
        raise ValueError(f"Container {public_id} not found in GTM account")

    def _ws_path(self, public_id):
        aid, cid, wid = self._resolve(public_id)
        return f"/accounts/{aid}/containers/{cid}/workspaces/{wid}"

    # --- containers / workspaces ----------------------------------------------

    def list_accounts(self):
        return self._call("GET", "/accounts").get("account", [])

    def list_containers(self, account_id):
        return self._call("GET", f"/accounts/{account_id}/containers").get("container", [])

    def list_workspaces(self, public_id):
        aid, cid, _ = self._resolve(public_id)
        return self._call("GET", f"/accounts/{aid}/containers/{cid}/workspaces").get("workspace", [])

    # --- tags -----------------------------------------------------------------

    def list_tags(self, public_id):
        return self._call("GET", f"{self._ws_path(public_id)}/tags").get("tag", [])

    def get_tag(self, public_id, tag_id):
        return self._call("GET", f"{self._ws_path(public_id)}/tags/{tag_id}")

    def create_tag(self, public_id, tag_body):
        """
        tag_body example (GA4 event tag):
          {
            "name": "GA4 Event - form_submit",
            "type": "gaawe",
            "parameter": [
              {"type": "template", "key": "measurementId", "value": "G-XXXXXXX"},
              {"type": "template", "key": "eventName", "value": "form_submit"}
            ],
            "firingTriggerId": ["TRIGGER_ID"]
          }
        """
        return self._call("POST", f"{self._ws_path(public_id)}/tags", body=tag_body)

    def update_tag(self, public_id, tag_id, fields):
        """Partial update -- pass only fields to change."""
        existing = self.get_tag(public_id, tag_id)
        existing.update(fields)
        return self._call("PUT", f"{self._ws_path(public_id)}/tags/{tag_id}", body=existing)

    def delete_tag(self, public_id, tag_id):
        return self._call("DELETE", f"{self._ws_path(public_id)}/tags/{tag_id}")

    # --- triggers -------------------------------------------------------------

    def list_triggers(self, public_id):
        return self._call("GET", f"{self._ws_path(public_id)}/triggers").get("trigger", [])

    def get_trigger(self, public_id, trigger_id):
        return self._call("GET", f"{self._ws_path(public_id)}/triggers/{trigger_id}")

    def create_trigger(self, public_id, trigger_body):
        """
        trigger_body example (form submission):
          {
            "name": "Trigger - Form Submit",
            "type": "FORM_SUBMISSION",
            "checkValidation": {"type": "boolean", "key": "checkValidation", "value": "true"},
            "waitForTags": {"type": "boolean", "key": "waitForTags", "value": "false"}
          }
        """
        return self._call("POST", f"{self._ws_path(public_id)}/triggers", body=trigger_body)

    def delete_trigger(self, public_id, trigger_id):
        return self._call("DELETE", f"{self._ws_path(public_id)}/triggers/{trigger_id}")

    # --- variables ------------------------------------------------------------

    def list_variables(self, public_id):
        return self._call("GET", f"{self._ws_path(public_id)}/variables").get("variable", [])

    def create_variable(self, public_id, variable_body):
        """
        variable_body example (data layer variable):
          {
            "name": "DLV - form_name",
            "type": "v",
            "parameter": [
              {"type": "integer", "key": "dataLayerVersion", "value": "2"},
              {"type": "boolean", "key": "setDefaultValue", "value": "false"},
              {"type": "template", "key": "name", "value": "form_name"}
            ]
          }
        """
        return self._call("POST", f"{self._ws_path(public_id)}/variables", body=variable_body)

    def delete_variable(self, public_id, variable_id):
        return self._call("DELETE", f"{self._ws_path(public_id)}/variables/{variable_id}")

    # --- versions / publish ---------------------------------------------------

    def list_versions(self, public_id):
        aid, cid, _ = self._resolve(public_id)
        return self._call("GET", f"/accounts/{aid}/containers/{cid}/version_headers").get("containerVersionHeader", [])

    def create_version(self, public_id, name="", notes=""):
        ws_path = self._ws_path(public_id)
        body = {}
        if name:
            body["name"] = name
        if notes:
            body["notes"] = notes
        return self._call("POST", f"{ws_path}:create_version", body=body)

    def publish(self, public_id, note=""):
        """Create a version from current workspace changes, then publish it."""
        version = self.create_version(public_id, name=note or "Published via API", notes=note)
        version_id = version.get("containerVersion", {}).get("containerVersionId")
        if not version_id:
            raise RuntimeError(f"Failed to create version: {version}")
        aid, cid, _ = self._resolve(public_id)
        result = self._call("POST", f"/accounts/{aid}/containers/{cid}/versions/{version_id}:publish")
        return {"version_id": version_id, "publish_result": result}

    def workspace_status(self, public_id):
        """Check what's changed in the current workspace (pending publish)."""
        return self._call("GET", f"{self._ws_path(public_id)}/status")


# --- CLI ----------------------------------------------------------------------

def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    g = GTMAPI()
    cmd, *args = sys.argv[1:]

    if cmd == "containers":
        public_id = args[0]
        aid, cid, wid = g._resolve(public_id)
        info = CONTAINERS.get(public_id, {})
        print(f"Container:  {public_id}  ({info.get('label', '?')})")
        print(f"Account ID: {aid}")
        print(f"Container ID: {cid}")
        print(f"Workspace ID: {wid}")

    elif cmd == "workspaces":
        public_id = args[0]
        ws = g.list_workspaces(public_id)
        for w in ws:
            print(f"ID={w.get('workspaceId')}  {w.get('name')}  ({w.get('description', '')})")

    elif cmd == "tags":
        public_id = args[0]
        tags = g.list_tags(public_id)
        print(f"Tags in {public_id} ({len(tags)} total)")
        print(f"{'TagID':>8}  {'Type':20s}  Name")
        print("-" * 70)
        for t in sorted(tags, key=lambda x: x.get("name", "")):
            print(f"{t.get('tagId', '?'):>8}  {t.get('type', '?'):20s}  {t.get('name', '?')}")

    elif cmd == "triggers":
        public_id = args[0]
        triggers = g.list_triggers(public_id)
        print(f"Triggers in {public_id} ({len(triggers)} total)")
        print(f"{'TrigID':>8}  {'Type':25s}  Name")
        print("-" * 70)
        for t in sorted(triggers, key=lambda x: x.get("name", "")):
            print(f"{t.get('triggerId', '?'):>8}  {t.get('type', '?'):25s}  {t.get('name', '?')}")

    elif cmd == "variables":
        public_id = args[0]
        variables = g.list_variables(public_id)
        print(f"Variables in {public_id} ({len(variables)} total)")
        print(f"{'VarID':>8}  {'Type':15s}  Name")
        print("-" * 70)
        for v in sorted(variables, key=lambda x: x.get("name", "")):
            print(f"{v.get('variableId', '?'):>8}  {v.get('type', '?'):15s}  {v.get('name', '?')}")

    elif cmd == "tag":
        public_id, tag_id = args[0], args[1]
        print(json.dumps(g.get_tag(public_id, tag_id), indent=2))

    elif cmd == "create-tag":
        public_id = args[0]
        tag_body = json.loads(args[1])
        result = g.create_tag(public_id, tag_body)
        print(f"Created tag: ID={result.get('tagId')}  Name={result.get('name')}")

    elif cmd == "update-tag":
        public_id, tag_id = args[0], args[1]
        fields = json.loads(args[2])
        result = g.update_tag(public_id, tag_id, fields)
        print(f"Updated tag: ID={result.get('tagId')}  Name={result.get('name')}")

    elif cmd == "delete-tag":
        public_id, tag_id = args[0], args[1]
        g.delete_tag(public_id, tag_id)
        print(f"Deleted tag {tag_id}")

    elif cmd == "create-trigger":
        public_id = args[0]
        body = json.loads(args[1])
        result = g.create_trigger(public_id, body)
        print(f"Created trigger: ID={result.get('triggerId')}  Name={result.get('name')}")

    elif cmd == "create-variable":
        public_id = args[0]
        body = json.loads(args[1])
        result = g.create_variable(public_id, body)
        print(f"Created variable: ID={result.get('variableId')}  Name={result.get('name')}")

    elif cmd == "publish":
        public_id = args[0]
        note = args[1] if len(args) > 1 else ""
        result = g.publish(public_id, note=note)
        print(f"Published! Version ID: {result['version_id']}")
        print(json.dumps(result.get("publish_result", {}), indent=2))

    elif cmd == "versions":
        public_id = args[0]
        versions = g.list_versions(public_id)
        print(f"Versions for {public_id}")
        for v in versions:
            print(f"  v{v.get('containerVersionId')}  {v.get('numTags', '?')} tags  "
                  f"{v.get('lastWorkspaceChangeTimeMs', '?')}  {v.get('name', '')}")

    elif cmd == "status":
        public_id = args[0]
        print(json.dumps(g.workspace_status(public_id), indent=2))

    elif cmd == "whoami":
        print(f"Service account: {g.creds['client_email']}")
        print(f"Key path: {os.path.abspath(KEY_PATH)}")
        print(f"Scopes: {SCOPE}")

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
