#!/usr/bin/env python3
"""blotato-api.py — Command Centre helper for Blotato (social publishing).

Blotato is the AI social-media engine + multi-platform publisher powering
Canary Detect and Sygma social. This is the CC's direct-API helper (the
parallel path to the `blotato` MCP server — same key, same backend).

Auth: single header `blotato-api-key: <key>`. The key lives in the CC
`secrets` table (name: blotato-api-key) and is materialised at boot to
$VAULT/Library/processes/secrets/blotato-api-key. Override with $BLOTATO_API_KEY.

Base URL: https://backend.blotato.com/v2   (NB: api.blotato.com is NOT valid)

Usage:
    VAULT=/tmp/pbs python3 /tmp/pbs/blotato-api.py accounts
    VAULT=/tmp/pbs python3 /tmp/pbs/blotato-api.py subaccounts <accountId>
    VAULT=/tmp/pbs python3 /tmp/pbs/blotato-api.py test          # connection check
    VAULT=/tmp/pbs python3 /tmp/pbs/blotato-api.py publish \
        --account <accountId> --platform linkedin \
        --text "Hello world" [--media <public-url> ...] \
        [--page <pageId>] [--board <boardId>] \
        [--schedule 2026-07-04T15:00:00Z | --next-slot] [--dry-run]

Publishing is async: `publish` returns a postSubmissionId. Nothing publishes
to a channel that is not already connected in the Blotato dashboard.
"""
import argparse, json, os, sys, urllib.request, urllib.error
from pathlib import Path

BASE = "https://backend.blotato.com/v2"


def _key() -> str:
    k = os.environ.get("BLOTATO_API_KEY")
    if k:
        return k.strip()
    vault = os.environ.get("VAULT")
    candidates = []
    if vault:
        candidates.append(Path(vault) / "Library/processes/secrets/blotato-api-key")
    candidates.append(Path(__file__).resolve().parent / "Library/processes/secrets/blotato-api-key")
    for p in candidates:
        if p.exists():
            return p.read_text().strip()
    sys.exit("blotato-api: no API key ($BLOTATO_API_KEY or secrets/blotato-api-key)")


def _req(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={
        "blotato-api-key": _key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        sys.exit(f"blotato-api: HTTP {e.code} on {method} {path} — {detail}")
    except Exception as e:
        sys.exit(f"blotato-api: {type(e).__name__} on {method} {path} — {e}")


def accounts():
    return _req("GET", "/users/me/accounts")


def subaccounts(account_id: str):
    return _req("GET", f"/users/me/accounts/{account_id}/subaccounts")


def publish(account_id, platform, text, media=None, page=None, board=None,
            schedule=None, next_slot=False):
    content = {"text": text, "mediaUrls": media or [], "platform": platform}
    target = {"targetType": platform}
    if page:
        target["pageId"] = page
    if board:
        target["boardId"] = board
    payload = {"post": {"accountId": str(account_id), "content": content, "target": target}}
    if schedule:
        payload["scheduledTime"] = schedule           # ROOT level, sibling of post
    if next_slot:
        payload["useNextFreeSlot"] = True
    return payload if False else _req("POST", "/posts", payload)


def main():
    ap = argparse.ArgumentParser(prog="blotato-api")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts", help="list connected accounts (+ accountId)")
    sub.add_parser("test", help="connection check (lists accounts)")
    s = sub.add_parser("subaccounts", help="list subaccounts (FB pages / LinkedIn company / YT playlists)")
    s.add_argument("account_id")
    p = sub.add_parser("publish", help="publish or schedule a post")
    p.add_argument("--account", required=True)
    p.add_argument("--platform", required=True,
                   help="twitter|linkedin|facebook|instagram|pinterest|tiktok|threads|bluesky|youtube")
    p.add_argument("--text", required=True)
    p.add_argument("--media", action="append", help="public media URL (repeatable)")
    p.add_argument("--page", help="Facebook pageId / LinkedIn company pageId")
    p.add_argument("--board", help="Pinterest boardId")
    p.add_argument("--schedule", help="ISO 8601 time, e.g. 2026-07-04T15:00:00Z")
    p.add_argument("--next-slot", action="store_true", help="drop into next free calendar slot")
    p.add_argument("--dry-run", action="store_true", help="print the payload, do not send")
    a = ap.parse_args()

    if a.cmd in ("accounts", "test"):
        out = accounts()
        if a.cmd == "test":
            n = len(out.get("items", []))
            print(f"OK — connected, {n} account(s)")
        print(json.dumps(out, indent=2))
    elif a.cmd == "subaccounts":
        print(json.dumps(subaccounts(a.account_id), indent=2))
    elif a.cmd == "publish":
        if a.dry_run:
            content = {"text": a.text, "mediaUrls": a.media or [], "platform": a.platform}
            target = {"targetType": a.platform}
            if a.page:
                target["pageId"] = a.page
            if a.board:
                target["boardId"] = a.board
            payload = {"post": {"accountId": str(a.account), "content": content, "target": target}}
            if a.schedule:
                payload["scheduledTime"] = a.schedule
            if a.next_slot:
                payload["useNextFreeSlot"] = True
            print(json.dumps(payload, indent=2))
            return
        out = publish(a.account, a.platform, a.text, a.media, a.page, a.board,
                      a.schedule, a.next_slot)
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
