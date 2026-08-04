#!/usr/bin/env python3
"""
youtube-api.py -- YouTube Analytics + Data API helper
Auth: service account JWT + DWD (impersonates pete.ashcroft@sygma-solutions.com)
Requires: SA added as Manager in YouTube Studio → Settings → Permissions
Scopes: youtube (write), youtube.readonly, yt-analytics.readonly
Usage:
  python3 youtube-api.py channels                        # list accessible channels
  python3 youtube-api.py channel CHANNEL_ID [DAYS]       # channel overview stats (default 30d)
  python3 youtube-api.py videos CHANNEL_ID [DAYS]        # top videos by views
  python3 youtube-api.py video VIDEO_ID [DAYS]           # single video deep stats
  python3 youtube-api.py traffic CHANNEL_ID [DAYS]       # traffic sources breakdown
  python3 youtube-api.py captions VIDEO_ID               # list caption tracks (asr = auto-generated)\n  python3 youtube-api.py transcript VIDEO_ID [OUT] [srt|vtt]  # download the transcript\n  python3 youtube-api.py whoami                          # show auth info
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys
from datetime import date, timedelta

KEY = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware, same as drive-api.py / gsc-api.py
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
IMPERSONATE = "pete.ashcroft@sygma-solutions.com"
SCOPES = " ".join([
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    # captions live ONLY behind force-ssl -- the three above do not reach them.
    # Granted in the Workspace admin console 4 Aug 2026 (client 117115682242341369700).
    "https://www.googleapis.com/auth/youtube.force-ssl",
])
DATA_BASE = "https://www.googleapis.com/youtube/v3"
ANALYTICS_BASE = "https://youtubeanalytics.googleapis.com/v2"

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

def data_api(path, params):
    url = DATA_BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

def analytics_api(params):
    url = ANALYTICS_BASE + "/reports?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

def date_range(days):
    end = date.today() - timedelta(days=1)  # yesterday (analytics lag)
    start = end - timedelta(days=days)
    return str(start), str(end)

def list_channels():
    resp = data_api("/channels", {"part": "snippet,statistics", "mine": "true"})
    items = resp.get("items", [])
    if not items:
        print("No channels found. Ensure SA is added as Viewer in YouTube Studio.")
        return
    for c in items:
        s = c.get("snippet", {})
        stats = c.get("statistics", {})
        print(f"Channel: {s.get('title')}")
        print(f"  ID: {c['id']}")
        print(f"  Subscribers: {stats.get('subscriberCount','?')}")
        print(f"  Total views: {stats.get('viewCount','?')}")
        print(f"  Videos: {stats.get('videoCount','?')}")

def channel_overview(channel_id, days=30):
    start, end = date_range(days)
    resp = analytics_api({
        "ids": f"channel=={channel_id}",
        "startDate": start, "endDate": end,
        "metrics": "views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost",
        "dimensions": "day",
    })
    rows = resp.get("rows", [])
    total_views = sum(r[1] for r in rows)
    total_watch = sum(r[2] for r in rows)
    subs_gained = sum(r[4] for r in rows)
    subs_lost = sum(r[5] for r in rows)
    print(f"Channel {channel_id} — last {days} days ({start} to {end})\n")
    print(f"  Views:           {total_views:,}")
    print(f"  Watch time:      {total_watch:,.0f} mins")
    print(f"  Avg view dur:    {resp.get('rows',[]) and int(rows[0][3]) or 0}s")
    print(f"  Subs gained:     +{subs_gained}")
    print(f"  Subs lost:       -{subs_lost}")
    print(f"  Net subs:        {subs_gained - subs_lost:+}")

def top_videos(channel_id, days=30):
    start, end = date_range(days)
    resp = analytics_api({
        "ids": f"channel=={channel_id}",
        "startDate": start, "endDate": end,
        "metrics": "views,estimatedMinutesWatched,averageViewPercentage",
        "dimensions": "video",
        "sort": "-views",
        "maxResults": 15,
    })
    rows = resp.get("rows", [])
    if not rows:
        print("No video data found."); return
    # Fetch video titles
    video_ids = [r[0] for r in rows]
    titles_resp = data_api("/videos", {"part": "snippet", "id": ",".join(video_ids)})
    titles = {v["id"]: v["snippet"]["title"] for v in titles_resp.get("items", [])}
    print(f"Top videos — last {days} days:\n")
    print(f"  {'VIEWS':>8}  {'WATCH(m)':>9}  {'AVG%':>5}  Title")
    print("  " + "-" * 70)
    for r in rows:
        vid, views, watch, avg_pct = r[0], r[1], r[2], r[3]
        title = titles.get(vid, vid)[:50]
        print(f"  {views:>8,}  {watch:>9,.0f}  {avg_pct:>5.1f}%  {title}")

def video_stats(video_id, days=90):
    start, end = date_range(days)
    resp = analytics_api({
        "ids": f"channel==MINE",
        "startDate": start, "endDate": end,
        "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments",
        "filters": f"video=={video_id}",
    })
    rows = resp.get("rows", [])
    if not rows:
        print(f"No data for video {video_id} in last {days} days."); return
    r = rows[0]
    # Get title
    title_resp = data_api("/videos", {"part": "snippet,statistics", "id": video_id})
    title = title_resp.get("items", [{}])[0].get("snippet", {}).get("title", video_id)
    stats = title_resp.get("items", [{}])[0].get("statistics", {})
    print(f"Video: {title}\nID: {video_id}\n")
    print(f"  Last {days} days:")
    print(f"    Views:          {r[0]:,}")
    print(f"    Watch time:     {r[1]:,.0f} mins")
    print(f"    Avg duration:   {int(r[2])}s")
    print(f"    Avg watched:    {r[3]:.1f}%")
    print(f"    Likes (period): {r[4]:,}")
    print(f"    Comments:       {r[5]:,}")
    print(f"  All time (YouTube):")
    print(f"    Total views:    {stats.get('viewCount','?')}")
    print(f"    Total likes:    {stats.get('likeCount','?')}")

def traffic_sources(channel_id, days=28):
    start, end = date_range(days)
    resp = analytics_api({
        "ids": f"channel=={channel_id}",
        "startDate": start, "endDate": end,
        "metrics": "views,estimatedMinutesWatched",
        "dimensions": "insightTrafficSourceType",
        "sort": "-views",
    })
    rows = resp.get("rows", [])
    if not rows:
        print("No traffic source data."); return
    total = sum(r[1] for r in rows)
    print(f"Traffic sources — last {days} days:\n")
    print(f"  {'SOURCE':<35} {'VIEWS':>8}  {'SHARE':>6}  {'WATCH(m)':>9}")
    print("  " + "-" * 65)
    for r in rows:
        share = r[1] / total * 100 if total else 0
        print(f"  {r[0]:<35} {r[1]:>8,}  {share:>6.1f}%  {r[2]:>9,.0f}")

def whoami():
    resp = data_api("/channels", {"part": "snippet", "mine": "true"})
    items = resp.get("items", [])
    print(f"Impersonating: {IMPERSONATE}")
    print(f"Channels accessible: {len(items)}")


def list_captions(video_id):
    """Every caption track on a video. trackKind 'asr' = YouTube auto-generated."""
    r = data_api("/captions", {"part": "snippet", "videoId": video_id})
    items = r.get("items", [])
    if not items:
        print("No caption tracks.")
        print("NOTE: videos.contentDetails.caption reports only MANUALLY UPLOADED tracks -- it reads")
        print("      'false' even when auto-generated (ASR) captions exist. This command is the truth.")
        return
    print(f"{'TRACK ID':<32} {'LANG':<6} {'KIND':<6} {'AUTO':<5} NAME")
    print("-" * 78)
    for t in items:
        s = t["snippet"]
        kind = s.get("trackKind", "")
        print(f"{t['id'][:32]:<32} {str(s.get('language')):<6} {kind:<6} "
              f"{'yes' if kind == 'asr' else 'no':<5} {s.get('name') or '(unnamed)'}")


def download_transcript(video_id, fmt="srt", out=None):
    """Download a video's transcript. Prefers a manual track over the auto one."""
    r = data_api("/captions", {"part": "snippet", "videoId": video_id})
    items = r.get("items", [])
    if not items:
        print("No caption track to download."); sys.exit(2)
    items.sort(key=lambda t: t["snippet"].get("trackKind") == "asr")   # manual first
    track = items[0]
    url = f"{DATA_BASE}/captions/{track['id']}?tfmt={fmt}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"})
    body = urllib.request.urlopen(req).read().decode("utf-8", "ignore")
    if out:
        with open(out, "w") as f: f.write(body)
        words = len(body.split())
        print(f"Wrote {out} -- {len(body):,} chars, ~{words:,} words "
              f"({'auto-generated' if track['snippet'].get('trackKind') == 'asr' else 'manual'} track)")
    else:
        print(body)

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "channels":
        list_channels()
    elif cmd == "channel":
        if len(args) < 2: print("Usage: youtube-api.py channel CHANNEL_ID [DAYS]"); sys.exit(1)
        channel_overview(args[1], int(args[2]) if len(args) > 2 else 30)
    elif cmd == "videos":
        if len(args) < 2: print("Usage: youtube-api.py videos CHANNEL_ID [DAYS]"); sys.exit(1)
        top_videos(args[1], int(args[2]) if len(args) > 2 else 30)
    elif cmd == "video":
        if len(args) < 2: print("Usage: youtube-api.py video VIDEO_ID [DAYS]"); sys.exit(1)
        video_stats(args[1], int(args[2]) if len(args) > 2 else 90)
    elif cmd == "traffic":
        if len(args) < 2: print("Usage: youtube-api.py traffic CHANNEL_ID [DAYS]"); sys.exit(1)
        traffic_sources(args[1], int(args[2]) if len(args) > 2 else 28)
    elif cmd == "captions":
        if len(args) < 2: print("Usage: youtube-api.py captions VIDEO_ID"); sys.exit(1)
        list_captions(args[1])
    elif cmd == "transcript":
        if len(args) < 2: print("Usage: youtube-api.py transcript VIDEO_ID [OUT_FILE] [srt|vtt]"); sys.exit(1)
        download_transcript(args[1], args[3] if len(args) > 3 else "srt",
                            args[2] if len(args) > 2 else None)
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
