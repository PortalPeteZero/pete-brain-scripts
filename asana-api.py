#!/usr/bin/env python3
"""Asana helper — single canonical path for all Asana work.

Parallels `garmin-api.py`, `gmail-api.py` and `calendar-api.py` in pattern and
style. Talks to the Asana REST API directly with a Personal Access Token (no
MCP, no SDK dependency — stdlib only). Exposes:

  * An `AsanaAPI` class for library use from other scripts (sync-asana,
    asana-gmail-sync, brain Compress, anywhere we touch tasks).
  * A CLI for ad-hoc work from the shell.

Auth:
  * PAT stored at:
      /Users/peterashcroft/Second Brain/Library/processes/secrets/asana-pat
  * The PAT never expires. No OAuth dance needed.

CLI usage:
  python3 asana-api.py whoami
  python3 asana-api.py my-tasks                       # Pete's incomplete tasks
  python3 asana-api.py search-tasks "<text>" [--completed] [--assignee GID]
  python3 asana-api.py get-task <task_gid>
  python3 asana-api.py create-task <project_gid> "<name>" \
        [--assignee GID] [--priority P1|P2|P3|P4] [--due YYYY-MM-DD] [--notes "..."]
  python3 asana-api.py update-task <task_gid> [--complete] [--assignee GID] \
        [--priority P1|P2|P3|P4] [--due YYYY-MM-DD] [--notes "..."] [--name "..."]
  python3 asana-api.py project-tasks <project_gid> [--completed]
  python3 asana-api.py get-sections <project_gid>
  python3 asana-api.py add-to-section <section_gid> <task_gid>
  python3 asana-api.py raw <METHOD> <path> [--body '<json>'] [--param k=v ...]

Library usage:
  from asana_api import AsanaAPI
  a = AsanaAPI()
  me = a.whoami()
  tasks = a.my_tasks()
  t = a.create_task(project_gid, "Do the thing", priority="P2")
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VAULT = Path("/Users/peterashcroft/Second Brain")
SECRETS = VAULT / "Library/processes/secrets"
PAT_FILE = SECRETS / "asana-pat"

BASE_URL = "https://app.asana.com/api/1.0"

# --- Canonical IDs (source of truth: asana-configuration.md) ------------------
WORKSPACE_GID = "1213947679900731"

ASSIGNEES = {
    "PETE": "1213947679900718",
    "JANE": "1213949290735736",
    "DAVE": "1213950274488858",
}

PRIORITY_FIELD_GID = "1213945150508559"
# Priority enum option GIDs are resolved live from the custom field definition
# the first time a priority is set (see _priority_enum_gid). Due-date offsets:
PRIORITY_DUE_OFFSET_DAYS = {"P1": 2, "P2": 7, "P3": 30, "P4": None}


class AsanaError(RuntimeError):
    pass


class AsanaAPI:
    """Thin stdlib wrapper over the Asana REST API using a PAT."""

    def __init__(self, pat_file: Path = PAT_FILE):
        self.pat_file = Path(pat_file)
        if not self.pat_file.exists():
            raise AsanaError(
                f"No Asana PAT at {self.pat_file}. "
                f"See [[asana-configuration]] — drop the token there (never expires)."
            )
        self.pat = self.pat_file.read_text().strip()
        if not self.pat:
            raise AsanaError(f"Asana PAT file {self.pat_file} is empty.")
        self.workspace_gid = WORKSPACE_GID
        self._priority_options = None  # lazy cache: {label: enum_gid}

    # ---------------------------------------------------------------- transport
    def request(self, method, path, body=None, params=None):
        """Low-level request. Returns the unwrapped `data` payload.

        method: GET|POST|PUT|DELETE
        path:   API path beginning with '/', e.g. '/tasks/123'
        body:   dict — sent as {'data': body}
        params: dict of query params
        """
        url = f"{BASE_URL}{path}"
        if params:
            # Asana wants comma-joined lists for opt_fields etc.
            flat = {
                k: (",".join(v) if isinstance(v, (list, tuple)) else v)
                for k, v in params.items()
                if v is not None
            }
            url += "?" + urllib.parse.urlencode(flat)
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        data = json.dumps({"data": body}).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise AsanaError(f"Asana {method} {path} -> HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise AsanaError(f"Asana {method} {path} -> network error: {e.reason}")
        return payload.get("data", payload)

    # convenience verbs
    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, body, params=None):
        return self.request("POST", path, body=body, params=params)

    def put(self, path, body, params=None):
        return self.request("PUT", path, body=body, params=params)

    def delete(self, path):
        return self.request("DELETE", path)

    # ---------------------------------------------------------------- identity
    def whoami(self):
        return self.get("/users/me", params={"opt_fields": "name,email,workspaces.name"})

    # ---------------------------------------------------------------- priority
    def _priority_enum_gid(self, label):
        """Resolve a P1..P4 label to its enum option GID, caching the lookup."""
        if label is None:
            return None
        label = label.upper()
        if self._priority_options is None:
            field = self.get(
                f"/custom_fields/{PRIORITY_FIELD_GID}",
                params={"opt_fields": "enum_options.name,enum_options.gid"},
            )
            opts = {}
            for o in field.get("enum_options", []):
                name = o.get("name", "")
                opts[name.upper()] = o["gid"]
                # also index by leading token P1/P2/... if the option is e.g. "P1 (Critical)"
                token = name.split()[0].upper() if name.split() else ""
                if token:
                    opts.setdefault(token, o["gid"])
            self._priority_options = opts
        gid = self._priority_options.get(label)
        if not gid:
            raise AsanaError(
                f"Priority '{label}' not found. Available: {sorted(self._priority_options)}"
            )
        return gid

    @staticmethod
    def _due_for_priority(label):
        from datetime import date, timedelta
        if not label:
            return None
        off = PRIORITY_DUE_OFFSET_DAYS.get(label.upper())
        if off is None:
            return None
        return (date.today() + timedelta(days=off)).isoformat()

    # ---------------------------------------------------------------- tasks
    DEFAULT_TASK_FIELDS = (
        "name,assignee.name,completed,due_on,notes,projects.name,"
        "memberships.section.name,custom_fields.name,custom_fields.display_value,"
        "created_at,modified_at,created_by.name"
    )

    def my_tasks(self, workspace=None, completed_since="now"):
        """Pete's tasks in his My Tasks list. completed_since='now' => incomplete only."""
        ws = workspace or self.workspace_gid
        ut = self.get(f"/users/me/user_task_list", params={"workspace": ws})
        utl_gid = ut["gid"]
        return self.get(
            f"/user_task_lists/{utl_gid}/tasks",
            params={"completed_since": completed_since, "opt_fields": self.DEFAULT_TASK_FIELDS},
        )

    def search_tasks(self, text=None, completed=None, assignee=None, created_after=None,
                     workspace=None, extra_params=None):
        ws = workspace or self.workspace_gid
        params = {"opt_fields": self.DEFAULT_TASK_FIELDS}
        if text:
            params["text"] = text
        if completed is not None:
            params["completed"] = str(bool(completed)).lower()
        if assignee:
            params["assignee.any"] = ASSIGNEES.get(assignee.upper(), assignee)
        if created_after:
            params["created_at.after"] = created_after
        if extra_params:
            params.update(extra_params)
        return self.get(f"/workspaces/{ws}/tasks/search", params=params)

    def get_task(self, task_gid):
        return self.get(f"/tasks/{task_gid}", params={"opt_fields": self.DEFAULT_TASK_FIELDS})

    def create_task(self, project_gid, name, assignee=None, priority=None,
                    due_on=None, notes=None, section_gid=None):
        body = {"name": name, "projects": [project_gid]}
        if assignee:
            body["assignee"] = ASSIGNEES.get(assignee.upper(), assignee)
        if priority:
            body.setdefault("custom_fields", {})[PRIORITY_FIELD_GID] = self._priority_enum_gid(priority)
            if due_on is None:
                due_on = self._due_for_priority(priority)
        if due_on:
            body["due_on"] = due_on
        if notes is not None:
            body["notes"] = notes
        task = self.post("/tasks", body=body, params={"opt_fields": self.DEFAULT_TASK_FIELDS})
        if section_gid:
            self.add_to_section(section_gid, task["gid"])
        return task

    def update_task(self, task_gid, completed=None, assignee=None, priority=None,
                    due_on=None, notes=None, name=None):
        body = {}
        if completed is not None:
            body["completed"] = bool(completed)
        if assignee:
            body["assignee"] = ASSIGNEES.get(assignee.upper(), assignee)
        if priority:
            body.setdefault("custom_fields", {})[PRIORITY_FIELD_GID] = self._priority_enum_gid(priority)
        if due_on:
            body["due_on"] = due_on
        if notes is not None:
            body["notes"] = notes
        if name is not None:
            body["name"] = name
        if not body:
            raise AsanaError("update_task called with nothing to change.")
        return self.put(f"/tasks/{task_gid}", body=body, params={"opt_fields": self.DEFAULT_TASK_FIELDS})

    # ---------------------------------------------------------------- projects/sections
    def project_tasks(self, project_gid, completed=None):
        params = {"opt_fields": self.DEFAULT_TASK_FIELDS}
        if completed is False:
            params["completed_since"] = "now"
        return self.get(f"/projects/{project_gid}/tasks", params=params)

    def get_sections(self, project_gid):
        return self.get(f"/projects/{project_gid}/sections", params={"opt_fields": "name,created_at"})

    def add_to_section(self, section_gid, task_gid):
        return self.post(f"/sections/{section_gid}/addTask", body={"task": task_gid})


# ================================================================ CLI
def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv=None):
    p = argparse.ArgumentParser(prog="asana-api.py", description="Asana PAT helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami")
    sub.add_parser("my-tasks")

    sp = sub.add_parser("search-tasks")
    sp.add_argument("text", nargs="?")
    sp.add_argument("--completed", action="store_true")
    sp.add_argument("--assignee")
    sp.add_argument("--created-after")

    sg = sub.add_parser("get-task")
    sg.add_argument("task_gid")

    sc = sub.add_parser("create-task")
    sc.add_argument("project_gid")
    sc.add_argument("name")
    sc.add_argument("--assignee")
    sc.add_argument("--priority", choices=["P1", "P2", "P3", "P4"])
    sc.add_argument("--due")
    sc.add_argument("--notes")
    sc.add_argument("--section")

    su = sub.add_parser("update-task")
    su.add_argument("task_gid")
    su.add_argument("--complete", action="store_true")
    su.add_argument("--assignee")
    su.add_argument("--priority", choices=["P1", "P2", "P3", "P4"])
    su.add_argument("--due")
    su.add_argument("--notes")
    su.add_argument("--name")

    pt = sub.add_parser("project-tasks")
    pt.add_argument("project_gid")
    pt.add_argument("--completed", action="store_true")

    gs = sub.add_parser("get-sections")
    gs.add_argument("project_gid")

    ats = sub.add_parser("add-to-section")
    ats.add_argument("section_gid")
    ats.add_argument("task_gid")

    rw = sub.add_parser("raw")
    rw.add_argument("method")
    rw.add_argument("path")
    rw.add_argument("--body")
    rw.add_argument("--param", action="append", default=[], help="k=v, repeatable")

    args = p.parse_args(argv)
    a = AsanaAPI()

    if args.cmd == "whoami":
        _print(a.whoami())
    elif args.cmd == "my-tasks":
        _print(a.my_tasks())
    elif args.cmd == "search-tasks":
        # default: incomplete only; --completed flips to completed=true
        _print(a.search_tasks(text=args.text,
                              completed=(True if args.completed else False),
                              assignee=args.assignee, created_after=args.created_after))
    elif args.cmd == "get-task":
        _print(a.get_task(args.task_gid))
    elif args.cmd == "create-task":
        _print(a.create_task(args.project_gid, args.name, assignee=args.assignee,
                             priority=args.priority, due_on=args.due, notes=args.notes,
                             section_gid=args.section))
    elif args.cmd == "update-task":
        _print(a.update_task(args.task_gid,
                            completed=True if args.complete else None,
                            assignee=args.assignee, priority=args.priority,
                            due_on=args.due, notes=args.notes, name=args.name))
    elif args.cmd == "project-tasks":
        _print(a.project_tasks(args.project_gid, completed=(True if args.completed else False)))
    elif args.cmd == "get-sections":
        _print(a.get_sections(args.project_gid))
    elif args.cmd == "add-to-section":
        _print(a.add_to_section(args.section_gid, args.task_gid))
    elif args.cmd == "raw":
        params = {}
        for kv in args.param:
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k] = v
        body = json.loads(args.body) if args.body else None
        _print(a.request(args.method.upper(), args.path, body=body, params=params or None))


if __name__ == "__main__":
    try:
        main()
    except AsanaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
