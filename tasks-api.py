#!/usr/bin/env python3
"""tasks-api.py — thin Google Tasks client for the CC, mirroring calendar-api.py.

Domain-wide-delegation service account impersonating pete.ashcroft@sygma-solutions.com.
Scope: https://www.googleapis.com/auth/tasks  (authorised in Workspace admin 2026-07-01; Tasks API enabled
in GCP project sygma-seo-tools the same day). Powers the PD ↔ Google Tasks two-way sync (cc-gtasks-sync.py).

Google Tasks quirks handled here:
  * Due dates are RFC3339 timestamps but ONLY the DATE is honoured (time is dropped). We always send
    YYYY-MM-DDT00:00:00.000Z and read back due[:10]. Bare-date round-trip, no tz shift.
  * `showHidden`/`showCompleted`/`showDeleted` must be set to see completed + deleted tasks on a pull.
  * A completed task is `status:"completed"` + a `completed` timestamp; clearing it needs status:"needsAction"
    AND due re-sent (Google nulls due on complete for some clients) — we always re-send due on patch.

CLI:  VAULT=/tmp/pbs python3 tasks-api.py lists
      VAULT=/tmp/pbs python3 tasks-api.py tasks "<tasklistId>"
"""
import base64, json, os, subprocess, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request

KEY_PATH = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
DEFAULT_USER = "pete.ashcroft@sygma-solutions.com"
SCOPE = "https://www.googleapis.com/auth/tasks"
BASE = "https://tasks.googleapis.com/tasks/v1"


def _b64u(data):
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class TasksAPI:
    def __init__(self, user=DEFAULT_USER, key_path=KEY_PATH, scope=SCOPE):
        self.user = user
        with open(os.path.abspath(key_path)) as f:
            self.creds = json.load(f)
        self.scope = scope
        self._token = None
        self._token_exp = 0

    # --- auth (identical DWD JWT flow to calendar-api.py) ----------------------
    def _get_token(self):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        now = int(time.time())
        header = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
        claim = _b64u(json.dumps({
            "iss": self.creds["client_email"], "sub": self.user, "scope": self.scope,
            "aud": "https://oauth2.googleapis.com/token", "exp": now + 3600, "iat": now,
        }))
        ts = f"{header}.{claim}"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(self.creds["private_key"]); kf = f.name
        try:
            sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", kf, "-binary"],
                                 input=ts.encode(), capture_output=True, check=True).stdout
        finally:
            os.unlink(kf)
        jwt = f"{ts}.{_b64u(sig)}"
        req = urllib.request.Request("https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt}).encode())
        resp = json.loads(urllib.request.urlopen(req).read())
        self._token = resp["access_token"]; self._token_exp = now + resp.get("expires_in", 3600)
        return self._token

    def _call(self, method, path, body=None, query=None):
        url = f"{BASE}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"; data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            raise RuntimeError(f"Tasks API {method} {path} -> HTTP {e.code}: {msg}") from e

    # --- date helpers (bare-date round-trip) ----------------------------------
    @staticmethod
    def due_rfc3339(date_str):
        """'YYYY-MM-DD' -> 'YYYY-MM-DDT00:00:00.000Z' (Google keeps only the date)."""
        return f"{date_str}T00:00:00.000Z" if date_str else None

    @staticmethod
    def date_of(due):
        """RFC3339 due -> 'YYYY-MM-DD' (or None)."""
        return due[:10] if due else None

    # --- task lists -----------------------------------------------------------
    def list_tasklists(self):
        items, page = [], None
        while True:
            q = {"maxResults": 100}
            if page:
                q["pageToken"] = page
            r = self._call("GET", "/users/@me/lists", query=q) or {}
            items.extend(r.get("items", []))
            page = r.get("nextPageToken")
            if not page:
                return items

    def create_tasklist(self, title):
        return self._call("POST", "/users/@me/lists", body={"title": title})

    def get_or_create_list(self, title):
        for tl in self.list_tasklists():
            if tl.get("title") == title:
                return tl
        return self.create_tasklist(title)

    # --- tasks ----------------------------------------------------------------
    def list_tasks(self, tasklist_id, show_completed=True, show_hidden=True, show_deleted=True, updated_min=None, max_results=100):
        q = {"showCompleted": str(show_completed).lower(), "showHidden": str(show_hidden).lower(),
             "showDeleted": str(show_deleted).lower(), "maxResults": max_results}
        if updated_min:
            q["updatedMin"] = updated_min
        items, page = [], None
        while True:
            if page:
                q["pageToken"] = page
            r = self._call("GET", f"/lists/{tasklist_id}/tasks", query=q) or {}
            items.extend(r.get("items", []))
            page = r.get("nextPageToken")
            if not page:
                return items

    def get_task(self, tasklist_id, task_id):
        return self._call("GET", f"/lists/{tasklist_id}/tasks/{task_id}")

    def insert_task(self, tasklist_id, title, due_date=None, notes=None):
        body = {"title": title}
        if due_date:
            body["due"] = self.due_rfc3339(due_date)
        if notes:
            body["notes"] = notes
        return self._call("POST", f"/lists/{tasklist_id}/tasks", body=body)

    def patch_task(self, tasklist_id, task_id, title=None, due_date=None, notes=None, status=None):
        body = {}
        if title is not None:
            body["title"] = title
        if due_date is not None:
            body["due"] = self.due_rfc3339(due_date) if due_date else None
        if notes is not None:
            body["notes"] = notes
        if status is not None:
            body["status"] = status
        return self._call("PATCH", f"/lists/{tasklist_id}/tasks/{task_id}", body=body)

    def complete_task(self, tasklist_id, task_id):
        return self.patch_task(tasklist_id, task_id, status="completed")

    def uncomplete_task(self, tasklist_id, task_id, due_date=None):
        # reopening: status back + re-send due (Google can drop due on complete)
        return self.patch_task(tasklist_id, task_id, status="needsAction", due_date=due_date)

    def delete_task(self, tasklist_id, task_id):
        return self._call("DELETE", f"/lists/{tasklist_id}/tasks/{task_id}")


def _cli():
    args = sys.argv[1:]
    api = TasksAPI()
    if not args or args[0] == "lists":
        for tl in api.list_tasklists():
            print(f"{tl['id']}\t{tl.get('title')}")
    elif args[0] == "tasks" and len(args) > 1:
        for t in api.list_tasks(args[1]):
            print(f"{t['id']}\t{t.get('status')}\t{api.date_of(t.get('due')) or '-'}\t{t.get('title')}")
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli()
