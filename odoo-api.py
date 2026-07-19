#!/usr/bin/env python3
"""
Odoo API helper for Canary Detect.

Wraps Odoo's JSON-RPC endpoint so any vault-side script (or Claude session) can
read and write CRM/sales/contact data without re-deriving the auth dance.

Config is read from Library/processes/odoo-api-configuration.md so there's
exactly one place to update when credentials rotate.

Usage:
  python3 Library/processes/scripts/odoo-api.py <command> [args]

Commands:
  version                                       Health check, no auth
  whoami                                        Authenticate, print uid + login
  search-read <model> <domain> <fields> [--limit N] [--offset N]
                                                Most common pattern
  read <model> <id|ids-csv> <fields>            Read by id
  search <model> <domain> [--limit N]           Just ids
  count <model> <domain>                        search_count
  create <model> <vals-json>                    Create record, returns id
  write <model> <id|ids-csv> <vals-json>        Update record(s)
  unlink <model> <id|ids-csv>                   Delete record(s)
  fields <model> [--attributes name,type,...]   List model fields
  models [--keyword foo]                        List installed models
  execute <model> <method> <args-json> [<kwargs-json>]
                                                Generic execute_kw escape hatch

Domain syntax: Odoo uses Polish-notation list-of-tuples, JSON-encoded.
  []                                  match all
  [["is_company","=",true]]           single condition
  [["name","ilike","clancy"],["customer_rank",">",0]]   AND
  ["|",["a","=",1],["b","=",2]]       OR
"""

import json
import os
import sys
import urllib.request
import urllib.error
import re
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "odoo-api-configuration.md"




def _secret_cfg():
    """Odoo config from the materialised secret (the house standard: keys live in public.secrets,
    never in a note or a markdown file). Returns None if the secret is not present, so callers keep
    their existing fallbacks until every runtime is proven on this path (19 Jul 2026)."""
    import json as _json, os as _os
    p = _os.path.join(_os.environ.get("VAULT", "/tmp/pbs"),
                      "Library", "processes", "secrets", "odoo-credentials.json")
    try:
        with open(p) as fh:
            c = _json.load(fh)
        if all(c.get(k) for k in ("url", "db", "login", "api_key")):
            return {"url": c["url"].rstrip("/"), "db": c["db"],
                    "login": c["login"], "api_key": c["api_key"]}
    except Exception:
        pass
    return None


def _load_config():
    """Config from env vars first (Railway/cloud), then the CC secrets vault, then the legacy
    markdown file. The secret is the house standard; the file fallback stays until every consumer
    is proven on the secrets path."""
    env = {"url": os.environ.get("ODOO_URL"), "db": os.environ.get("ODOO_DB"),
           "login": os.environ.get("ODOO_LOGIN"), "api_key": os.environ.get("ODOO_API_KEY")}
    if all(env.values()):
        return env
    sec = _secret_cfg()
    if sec:
        return sec
    if not CONFIG_FILE.exists():
        sys.exit(f"odoo config not found: {CONFIG_FILE}")
    text = CONFIG_FILE.read_text()

    def grab(label):
        m = re.search(rf"\*\*{re.escape(label)}\*\*\s*\|\s*`([^`]+)`", text)
        return m.group(1) if m else None

    cfg = {
        "url": grab("Instance URL"),
        "db": grab("Database name"),
        "login": grab("Login (API user)"),
        "api_key": grab("API key"),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        sys.exit(f"odoo config missing fields: {missing}")
    return cfg


CFG = _load_config()
_UID_CACHE = None


def _rpc(service, method, args, kwargs=None):
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"service": service, "method": method, "args": args},
        "id": 1,
    }
    if kwargs is not None:
        payload["params"]["args"] = list(args) + [kwargs]
    req = urllib.request.Request(
        f"{CFG['url']}/jsonrpc",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    import time as _time
    body = None
    for _attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and _attempt < 4:   # transient Odoo rate-limit / overload → back off + retry
                _time.sleep(2 ** _attempt + 1)
                continue
            sys.exit(f"http error {e.code}: {e.read().decode('utf-8', 'ignore')[:500]}")
    if "error" in body:
        err = body["error"]
        msg = err.get("data", {}).get("message") or err.get("message", "unknown")
        sys.exit(f"odoo error: {msg}")
    return body.get("result")


def _auth():
    """Authenticate and cache uid."""
    global _UID_CACHE
    if _UID_CACHE:
        return _UID_CACHE
    uid = _rpc("common", "authenticate", [CFG["db"], CFG["login"], CFG["api_key"], {}])
    if not uid:
        sys.exit("odoo auth failed -- check login + api key")
    _UID_CACHE = uid
    return uid


def _execute(model, method, args, kwargs=None):
    uid = _auth()
    return _rpc(
        "object",
        "execute_kw",
        [CFG["db"], uid, CFG["api_key"], model, method, args, kwargs or {}],
    )


# ── Commands ─────────────────────────────────────────────────────────


def cmd_version(_args):
    print(json.dumps(_rpc("common", "version", []), indent=2))


def cmd_whoami(_args):
    uid = _auth()
    user = _execute("res.users", "read", [[uid], ["id", "login", "name", "email"]])
    print(json.dumps(user, indent=2))


def _parse_ids(token):
    if "," in token:
        return [int(x) for x in token.split(",") if x.strip()]
    return [int(token)]


def _parse_fields(token):
    return [f.strip() for f in token.split(",") if f.strip()]


def _opt(args, flag, default=None, cast=str):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            v = args[i + 1]
            del args[i : i + 2]
            return cast(v)
    return default


def cmd_search_read(args):
    model = args[0]
    domain = json.loads(args[1])
    fields = _parse_fields(args[2])
    rest = list(args[3:])
    limit = _opt(rest, "--limit", 80, int)
    offset = _opt(rest, "--offset", 0, int)
    out = _execute(
        model,
        "search_read",
        [domain],
        {"fields": fields, "limit": limit, "offset": offset},
    )
    print(json.dumps(out, indent=2, default=str))


def cmd_read(args):
    model = args[0]
    ids = _parse_ids(args[1])
    fields = _parse_fields(args[2])
    out = _execute(model, "read", [ids, fields])
    print(json.dumps(out, indent=2, default=str))


def cmd_search(args):
    model = args[0]
    domain = json.loads(args[1])
    rest = list(args[2:])
    limit = _opt(rest, "--limit", 80, int)
    out = _execute(model, "search", [domain], {"limit": limit})
    print(json.dumps(out, indent=2))


def cmd_count(args):
    model = args[0]
    domain = json.loads(args[1])
    out = _execute(model, "search_count", [domain])
    print(out)


def cmd_create(args):
    model = args[0]
    vals = json.loads(args[1])
    out = _execute(model, "create", [vals])
    print(out)


def cmd_write(args):
    model = args[0]
    ids = _parse_ids(args[1])
    vals = json.loads(args[2])
    out = _execute(model, "write", [ids, vals])
    print(out)


def cmd_unlink(args):
    model = args[0]
    ids = _parse_ids(args[1])
    out = _execute(model, "unlink", [ids])
    print(out)


def cmd_fields(args):
    model = args[0]
    rest = list(args[1:])
    attrs = _opt(rest, "--attributes", "string,type,required,readonly", str).split(",")
    out = _execute(model, "fields_get", [], {"attributes": attrs})
    print(json.dumps(out, indent=2))


def cmd_models(args):
    rest = list(args)
    keyword = _opt(rest, "--keyword", None, str)
    domain = [["model", "ilike", keyword]] if keyword else []
    out = _execute(
        "ir.model",
        "search_read",
        [domain],
        {"fields": ["model", "name", "modules"], "limit": 200, "order": "model"},
    )
    print(json.dumps(out, indent=2))


def cmd_execute(args):
    model = args[0]
    method = args[1]
    a = json.loads(args[2]) if len(args) >= 3 else []
    kw = json.loads(args[3]) if len(args) >= 4 else {}
    out = _execute(model, method, a, kw)
    print(json.dumps(out, indent=2, default=str))


COMMANDS = {
    "version": cmd_version,
    "whoami": cmd_whoami,
    "search-read": cmd_search_read,
    "read": cmd_read,
    "search": cmd_search,
    "count": cmd_count,
    "create": cmd_create,
    "write": cmd_write,
    "unlink": cmd_unlink,
    "fields": cmd_fields,
    "models": cmd_models,
    "execute": cmd_execute,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        sys.exit(f"unknown command: {cmd}\n\n{__doc__}")
    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
