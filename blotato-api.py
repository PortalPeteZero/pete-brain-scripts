#!/usr/bin/env python3
"""blotato-api.py — Command Centre connector for Blotato (social publishing).

Blotato is the AI social-media engine + multi-platform publisher powering
Canary Detect and Sygma social. This is the CC's DIRECT-API connector — the
one and only path, built the CC way (code in GitHub, key in the CC `secrets`
table), so it runs anywhere the CC runs (session, Cowork, cron, phone). It
does not depend on any local editor config.

Auth: single header `blotato-api-key: <key>`. The key lives in the CC
`secrets` table (name: blotato-api-key) and is materialised at boot to
$VAULT/Library/processes/secrets/blotato-api-key. Override with $BLOTATO_API_KEY.

Base URL: https://backend.blotato.com/v2   (NB: api.blotato.com is NOT valid)

Commands:
    accounts                        list connected accounts (+ accountId)
    subaccounts <accountId>         FB pages / LinkedIn company / YT playlists
    test                            connection check
    media <public-url>              host a public media URL -> Blotato url
    publish  --account <id> --platform <p> --text <t>
             [--media <url> ...] [--page <pageId>] [--board <boardId>]
             [--schedule <ISO8601+offset> | --next-slot] [--dry-run]
    status   <postSubmissionId>     status of a submitted post
    posts    [--limit N]            list posts (scheduled/published/failed)
    schedules                       list scheduled posts (soonest first)
    schedule-get    <id>            one scheduled post (with draft)
    schedule-update <id> [--schedule <ISO>] [--text <t>]
    schedule-delete <id>            cancel a scheduled post
    templates                       list video/visual templates
    video-create --template <id> [--prompt <p>] [--title <t>] [--draft]
    video-status <id>               poll a created video/visual
    analytics [--metric views] [--since <ISO>] [--until <ISO>]
    post-analytics  <postId>        metrics + snapshot history for one post

Notes: publishing/video are async (return a submission/creation id — poll with
status/video-status). scheduledTime is ISO-8601 WITH offset, or it is treated
as UTC. targetType always equals the platform. Nothing publishes to a channel
that is not already connected in the Blotato dashboard.
"""
import argparse, json, os, sys, urllib.request, urllib.error, urllib.parse
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


def _qs(params: dict) -> str:
    q = {k: v for k, v in params.items() if v is not None}
    return ("?" + urllib.parse.urlencode(q)) if q else ""


# ---- accounts / media -------------------------------------------------------
def accounts():                         return _req("GET", "/users/me/accounts")
def subaccounts(account_id):            return _req("GET", f"/users/me/accounts/{account_id}/subaccounts")
def media(url):                         return _req("POST", "/media", {"url": url})


# ---- publishing -------------------------------------------------------------
def publish(account_id, platform, text, media_urls=None, page=None, board=None,
            schedule=None, next_slot=False):
    content = {"text": text, "mediaUrls": media_urls or [], "platform": platform}
    target = {"targetType": platform}
    if page:  target["pageId"] = page
    if board: target["boardId"] = board
    payload = {"post": {"accountId": str(account_id), "content": content, "target": target}}
    if schedule:  payload["scheduledTime"] = schedule    # ROOT level, sibling of post
    if next_slot: payload["useNextFreeSlot"] = True
    return payload


def post_status(sid):                   return _req("GET", f"/posts/{sid}")
def list_posts(limit=None):             return _req("GET", "/posts" + _qs({"limit": limit}))


# ---- scheduled posts --------------------------------------------------------
def schedules():                        return _req("GET", "/schedules")
def schedule_get(sid):                  return _req("GET", f"/schedules/{sid}")
def schedule_delete(sid):               return _req("DELETE", f"/schedules/{sid}")
def schedule_update(sid, schedule=None, text=None):
    body = {}
    if schedule: body["scheduledTime"] = schedule
    if text is not None: body["post"] = {"content": {"text": text}}
    return _req("PATCH", f"/schedules/{sid}", body)


# ---- video / visuals --------------------------------------------------------
def templates():                        return _req("GET", "/videos/templates")
def video_create(template_id, prompt=None, title=None, draft=False):
    body = {"templateId": template_id, "inputs": {}, "render": True, "isDraft": draft}
    if prompt: body["prompt"] = prompt
    if title:  body["title"] = title
    return _req("POST", "/videos/from-templates", body)
def video_status(cid):                  return _req("GET", f"/videos/creations/{cid}")


# ---- analytics --------------------------------------------------------------
def analytics(metric=None, since=None, until=None):
    return _req("GET", "/analytics" + _qs({"metric": metric, "since": since, "until": until}))
def post_analytics(pid):                return _req("GET", f"/posts/{pid}/analytics")


def main():
    ap = argparse.ArgumentParser(prog="blotato-api")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts"); sub.add_parser("test")
    s = sub.add_parser("subaccounts"); s.add_argument("account_id")
    s = sub.add_parser("media"); s.add_argument("url")

    p = sub.add_parser("publish")
    p.add_argument("--account", required=True)
    p.add_argument("--platform", required=True,
                   help="twitter|linkedin|facebook|instagram|pinterest|tiktok|threads|bluesky|youtube")
    p.add_argument("--text", required=True)
    p.add_argument("--media", action="append")
    p.add_argument("--page"); p.add_argument("--board")
    p.add_argument("--schedule", help="ISO 8601 WITH offset, e.g. 2026-07-04T15:00:00+01:00")
    p.add_argument("--next-slot", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("status"); s.add_argument("submission_id")
    s = sub.add_parser("posts"); s.add_argument("--limit", type=int)
    sub.add_parser("schedules")
    s = sub.add_parser("schedule-get"); s.add_argument("id")
    s = sub.add_parser("schedule-delete"); s.add_argument("id")
    s = sub.add_parser("schedule-update"); s.add_argument("id"); s.add_argument("--schedule"); s.add_argument("--text")
    sub.add_parser("templates")
    s = sub.add_parser("video-create"); s.add_argument("--template", required=True)
    s.add_argument("--prompt"); s.add_argument("--title"); s.add_argument("--draft", action="store_true")
    s = sub.add_parser("video-status"); s.add_argument("id")
    s = sub.add_parser("analytics"); s.add_argument("--metric"); s.add_argument("--since"); s.add_argument("--until")
    s = sub.add_parser("post-analytics"); s.add_argument("id")

    a = ap.parse_args()
    out = None
    if a.cmd in ("accounts", "test"):
        out = accounts()
        if a.cmd == "test":
            print(f"OK — connected, {len(out.get('items', []))} account(s)")
    elif a.cmd == "subaccounts":     out = subaccounts(a.account_id)
    elif a.cmd == "media":           out = media(a.url)
    elif a.cmd == "publish":
        out = publish(a.account, a.platform, a.text, a.media, a.page, a.board, a.schedule, a.next_slot)
        if not a.dry_run:
            out = _req("POST", "/posts", out)
    elif a.cmd == "status":          out = post_status(a.submission_id)
    elif a.cmd == "posts":           out = list_posts(a.limit)
    elif a.cmd == "schedules":       out = schedules()
    elif a.cmd == "schedule-get":    out = schedule_get(a.id)
    elif a.cmd == "schedule-delete": out = schedule_delete(a.id)
    elif a.cmd == "schedule-update": out = schedule_update(a.id, a.schedule, a.text)
    elif a.cmd == "templates":       out = templates()
    elif a.cmd == "video-create":    out = video_create(a.template, a.prompt, a.title, a.draft)
    elif a.cmd == "video-status":    out = video_status(a.id)
    elif a.cmd == "analytics":       out = analytics(a.metric, a.since, a.until)
    elif a.cmd == "post-analytics":  out = post_analytics(a.id)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
