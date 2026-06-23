#!/usr/bin/env python3
"""cron_state.py — durable cron memory in the Command Centre (public.cron_state).

Railway containers are wiped between runs, so any cron that kept its memory in a local JSON file
(ledgers, cursors, seen-markers) would forget or duplicate on the cloud — e.g. a calendar-sync
re-creating events it already made. This is the shared store: each (cron_key, item_key) row holds
one JSON value. Same CC Supabase as cc_publish; env-first on Railway, vault keys file locally.

  from cron_state import get_state, set_state, get_all
  ledger = get_state("xhale-sync", "calendar_ledger", default=[])
  set_state("xhale-sync", "calendar_ledger", ledger + [new_id])

Upsert relies on the (cron_key, item_key) primary key + PostgREST merge-duplicates.
"""
import json, os, urllib.request, urllib.parse
from pathlib import Path

_SECRETS = (Path(os.environ["VAULT"]) / "Library/processes/secrets") if os.environ.get("VAULT") \
    else (Path(__file__).resolve().parents[1] / "secrets")


def _cfg():
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        k = json.load(open(_SECRETS / "command-centre-supabase-keys.json"))
        url, key = k["url"], k["service_role_key"]
    return url.rstrip("/") + "/rest/v1", key


def _hdr(key, extra=None):
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def get_state(cron_key, item_key, default=None):
    """Return the stored JSON value for (cron_key, item_key), or `default` if absent/unreachable."""
    base, key = _cfg()
    q = (f"{base}/cron_state?cron_key=eq.{urllib.parse.quote(cron_key)}"
         f"&item_key=eq.{urllib.parse.quote(item_key)}&select=value")
    try:
        rows = json.loads(urllib.request.urlopen(urllib.request.Request(q, headers=_hdr(key)), timeout=30).read())
        return rows[0]["value"] if rows else default
    except Exception:
        return default


def set_state(cron_key, item_key, value):
    """Upsert the JSON value for (cron_key, item_key). Raises on failure (callers should let it surface)."""
    base, key = _cfg()
    body = json.dumps([{"cron_key": cron_key, "item_key": item_key, "value": value}]).encode()
    req = urllib.request.Request(base + "/cron_state", data=body, method="POST",
                                 headers=_hdr(key, {"Prefer": "resolution=merge-duplicates,return=minimal"}))
    urllib.request.urlopen(req, timeout=30)
    return True


def get_all(cron_key):
    """Return {item_key: value} for every row under cron_key (e.g. a ledger split across item keys)."""
    base, key = _cfg()
    q = f"{base}/cron_state?cron_key=eq.{urllib.parse.quote(cron_key)}&select=item_key,value"
    try:
        rows = json.loads(urllib.request.urlopen(urllib.request.Request(q, headers=_hdr(key)), timeout=30).read())
        return {r["item_key"]: r["value"] for r in rows}
    except Exception:
        return {}


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    if a and a[0] == "get":
        print(json.dumps(get_state(a[1], a[2], default=None)))
    elif a and a[0] == "set":
        set_state(a[1], a[2], json.loads(a[3])); print("ok")
    elif a and a[0] == "all":
        print(json.dumps(get_all(a[1]), indent=2))
    else:
        print("usage: cron_state.py get|set|all CRON_KEY [ITEM_KEY] [JSON_VALUE]")
