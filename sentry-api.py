#!/usr/bin/env python3
"""
sentry-api.py -- Sentry REST API helper

Auth: Personal Auth Token (Bearer)
Token file: Library/processes/secrets/sentry-token
Default org: sygma-solutions-ltd

Usage:
  # Issues
  python3 sentry-api.py issues [--org ORG] [--project SLUG] [--period 24h] [--query "is:unresolved"] [--limit 25]
  python3 sentry-api.py issue {issue_id}                            # full detail incl. tags + counts
  python3 sentry-api.py event {issue_id}                            # latest event w/ stack trace
  python3 sentry-api.py event {issue_id} {event_id}                 # specific event
  python3 sentry-api.py resolve {issue_id} [--comment "fixed in X"] # mark resolved
  python3 sentry-api.py ignore {issue_id}                           # mark ignored
  python3 sentry-api.py reopen {issue_id}                           # set unresolved

  # Projects + orgs
  python3 sentry-api.py projects [--org ORG]                        # list projects
  python3 sentry-api.py project {slug} [--org ORG]                  # project detail
  python3 sentry-api.py orgs                                        # list orgs

  # Alert rules
  python3 sentry-api.py alerts [--project SLUG] [--org ORG]         # list issue alert rules
  python3 sentry-api.py create-alert {project_slug} {name} {threshold} {window_min} [--email TARGET]
                                                                     # e.g. "5+ errors in 5 minutes -> Pete"

  # Util
  python3 sentry-api.py whoami                                      # show auth principal + scopes
  python3 sentry-api.py raw GET /api/0/...                          # arbitrary GET

Common periods: 1h 24h 7d 14d 30d 90d 'all'.
"""

import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error

DEFAULT_ORG = "sygma-solutions-ltd"
DEFAULT_PROJECT = "sygma-portal"   # the active app (Portal/Hub). For the marketing site pass --project javascript-nextjs. Changed 2026-06-19.
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "sentry-token")
BASE = "https://sentry.io/api/0"


def load_token():
    with open(TOKEN_FILE) as f:
        return f.read().strip()


def request(method, path, body=None, query=None):
    """Low-level Sentry API call. path is full path beginning with /api/0/... or shorthand starting with /."""
    if not path.startswith("/api/"):
        path = path.lstrip("/")
        url = f"{BASE}/{path}" if not path.startswith("api/0") else f"https://sentry.io/{path}"
    else:
        url = f"https://sentry.io{path}"
    if query:
        qs = urllib.parse.urlencode(query, doseq=True)
        url = f"{url}{'&' if '?' in url else '?'}{qs}"
    headers = {
        "Authorization": f"Bearer {load_token()}",
        "Content-Type": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8")
            ct = resp.headers.get("Content-Type", "")
            if "application/json" in ct and payload:
                return json.loads(payload)
            return payload
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"HTTP {e.code} on {method} {url}\n{body}\n")
        sys.exit(1)


# ---------- Issues ----------

def cmd_issues(args):
    org = args.get("org", DEFAULT_ORG)
    project = args.get("project", DEFAULT_PROJECT)
    query = args.get("query", "is:unresolved")
    period = args.get("period", "24h")
    limit = int(args.get("limit", 25))
    qs = {
        "query": query,
        "statsPeriod": period,
        "limit": limit,
        "shortIdLookup": "1",
    }
    # filter by project slug if given
    proj_id = None
    if project:
        for p in request("GET", f"/api/0/organizations/{org}/projects/"):
            if p.get("slug") == project:
                proj_id = p.get("id")
                break
        if proj_id:
            qs["project"] = proj_id
    issues = request("GET", f"/api/0/organizations/{org}/issues/", query=qs)
    rows = []
    for i in issues:
        rows.append({
            "shortId": i.get("shortId"),
            "id": i.get("id"),
            "level": i.get("level"),
            "status": i.get("status"),
            "title": i.get("title"),
            "culprit": i.get("culprit"),
            "count": i.get("count"),
            "userCount": i.get("userCount"),
            "firstSeen": i.get("firstSeen"),
            "lastSeen": i.get("lastSeen"),
            "permalink": i.get("permalink"),
        })
    print(json.dumps(rows, indent=2))


def cmd_issue(args):
    issue_id = args["positional"][0]
    org = args.get("org", DEFAULT_ORG)
    issue = request("GET", f"/api/0/organizations/{org}/issues/{issue_id}/")
    print(json.dumps(issue, indent=2))


def cmd_event(args):
    issue_id = args["positional"][0]
    event_id = args["positional"][1] if len(args["positional"]) > 1 else "latest"
    org = args.get("org", DEFAULT_ORG)
    event = request("GET", f"/api/0/organizations/{org}/issues/{issue_id}/events/{event_id}/")
    # Pretty-print key bits
    out = {
        "eventID": event.get("eventID"),
        "dateCreated": event.get("dateCreated"),
        "title": event.get("title"),
        "message": event.get("message"),
        "platform": event.get("platform"),
        "tags": event.get("tags"),
        "user": event.get("user"),
        "request": event.get("request"),
        "contexts": event.get("contexts"),
        "exception": None,
        "breadcrumbs": None,
    }
    for entry in event.get("entries", []):
        if entry.get("type") == "exception":
            values = entry.get("data", {}).get("values", [])
            out["exception"] = [
                {
                    "type": v.get("type"),
                    "value": v.get("value"),
                    "module": v.get("module"),
                    "stacktrace": [
                        {
                            "filename": f.get("filename"),
                            "function": f.get("function"),
                            "lineNo": f.get("lineNo"),
                            "colNo": f.get("colNo"),
                            "context": f.get("context"),
                            "inApp": f.get("inApp"),
                        }
                        for f in (v.get("stacktrace") or {}).get("frames", [])
                    ],
                }
                for v in values
            ]
        if entry.get("type") == "breadcrumbs":
            crumbs = entry.get("data", {}).get("values", [])[-15:]
            out["breadcrumbs"] = [
                {
                    "timestamp": c.get("timestamp"),
                    "category": c.get("category"),
                    "type": c.get("type"),
                    "level": c.get("level"),
                    "message": c.get("message"),
                    "data": c.get("data"),
                }
                for c in crumbs
            ]
    print(json.dumps(out, indent=2, default=str))


def cmd_resolve(args):
    issue_id = args["positional"][0]
    org = args.get("org", DEFAULT_ORG)
    body = {"status": "resolved"}
    res = request("PUT", f"/api/0/organizations/{org}/issues/{issue_id}/", body=body)
    print(json.dumps(res, indent=2))


def cmd_ignore(args):
    issue_id = args["positional"][0]
    org = args.get("org", DEFAULT_ORG)
    res = request("PUT", f"/api/0/organizations/{org}/issues/{issue_id}/", body={"status": "ignored"})
    print(json.dumps(res, indent=2))


def cmd_reopen(args):
    issue_id = args["positional"][0]
    org = args.get("org", DEFAULT_ORG)
    res = request("PUT", f"/api/0/organizations/{org}/issues/{issue_id}/", body={"status": "unresolved"})
    print(json.dumps(res, indent=2))


# ---------- Projects + orgs ----------

def cmd_projects(args):
    org = args.get("org", DEFAULT_ORG)
    res = request("GET", f"/api/0/organizations/{org}/projects/")
    rows = [
        {
            "id": p.get("id"),
            "slug": p.get("slug"),
            "name": p.get("name"),
            "platform": p.get("platform"),
            "isMember": p.get("isMember"),
            "team": (p.get("team") or {}).get("slug"),
        }
        for p in res
    ]
    print(json.dumps(rows, indent=2))


def cmd_project(args):
    slug = args["positional"][0]
    org = args.get("org", DEFAULT_ORG)
    res = request("GET", f"/api/0/projects/{org}/{slug}/")
    print(json.dumps(res, indent=2))


def cmd_orgs(args):
    res = request("GET", "/api/0/organizations/")
    print(json.dumps(res, indent=2))


# ---------- Alert rules ----------

def cmd_alerts(args):
    org = args.get("org", DEFAULT_ORG)
    project = args.get("project", DEFAULT_PROJECT)
    rules = request("GET", f"/api/0/projects/{org}/{project}/rules/")
    print(json.dumps(rules, indent=2))


def cmd_create_alert(args):
    """Issue alert rule: 'When more than {threshold} events seen in {window} minutes -> notify'."""
    pos = args["positional"]
    project_slug = pos[0]
    name = pos[1]
    threshold = int(pos[2])
    window_min = int(pos[3])
    org = args.get("org", DEFAULT_ORG)
    target = args.get("email", "")
    # Sentry expects window in minutes as an Interval option in the condition
    body = {
        "actionMatch": "all",
        "filterMatch": "all",
        "frequency": 5,
        "name": name,
        "conditions": [
            {
                "id": "sentry.rules.conditions.event_frequency.EventFrequencyCondition",
                "value": threshold,
                "interval": f"{window_min}m",
                "comparisonType": "count",
            }
        ],
        "actions": [
            {
                "id": "sentry.mail.actions.NotifyEmailAction",
                "targetType": "IssueOwners" if not target else "Member",
                "targetIdentifier": target if target else None,
            }
        ],
        "filters": [],
    }
    res = request("POST", f"/api/0/projects/{org}/{project_slug}/rules/", body=body)
    print(json.dumps(res, indent=2))


# ---------- Util ----------

def cmd_whoami(args):
    me = request("GET", "/api/0/users/me/")
    print(json.dumps(me, indent=2))


def cmd_raw(args):
    method = args["positional"][0]
    path = args["positional"][1]
    body = None
    if len(args["positional"]) > 2:
        body = json.loads(args["positional"][2])
    print(json.dumps(request(method, path, body=body), indent=2, default=str))


# ---------- Argument parsing ----------

def parse_args(argv):
    args = {"positional": [], "org": None, "project": None, "period": None, "query": None, "limit": None, "comment": None, "email": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--org", "--project", "--period", "--query", "--limit", "--comment", "--email"):
            key = a.lstrip("-")
            args[key] = argv[i + 1]
            i += 2
        else:
            args["positional"].append(a)
            i += 1
    # drop None entries so callers can use .get(key, default)
    return {k: v for k, v in args.items() if v is not None or k == "positional"}


COMMANDS = {
    "issues": cmd_issues,
    "issue": cmd_issue,
    "event": cmd_event,
    "resolve": cmd_resolve,
    "ignore": cmd_ignore,
    "reopen": cmd_reopen,
    "projects": cmd_projects,
    "project": cmd_project,
    "orgs": cmd_orgs,
    "alerts": cmd_alerts,
    "create-alert": cmd_create_alert,
    "whoami": cmd_whoami,
    "raw": cmd_raw,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        sys.stderr.write(f"Unknown command: {cmd}\n\n")
        print(__doc__)
        sys.exit(2)
    args = parse_args(sys.argv[2:])
    COMMANDS[cmd](args)


if __name__ == "__main__":
    main()
