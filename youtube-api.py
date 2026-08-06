#!/usr/bin/env python3
"""
youtube-api.py -- YouTube Analytics + Data API helper
Auth: service account JWT + DWD (impersonates pete.ashcroft@sygma-solutions.com)
Requires: SA added as Manager in YouTube Studio → Settings → Permissions
Scopes: youtube (write), youtube.readonly, yt-analytics.readonly, force-ssl, upload
Usage:
  python3 youtube-api.py channels                        # list accessible channels
  python3 youtube-api.py channel CHANNEL_ID [DAYS]       # channel overview stats (default 30d)
  python3 youtube-api.py videos CHANNEL_ID [DAYS]        # top videos by views
  python3 youtube-api.py video VIDEO_ID [DAYS]           # single video deep stats
  python3 youtube-api.py traffic CHANNEL_ID [DAYS]       # traffic sources breakdown
  python3 youtube-api.py captions VIDEO_ID               # list caption tracks (asr = auto-generated)
  python3 youtube-api.py transcript VIDEO_ID [OUT] [srt|vtt]  # download the transcript
  python3 youtube-api.py upload FILE payload.json [CHANNEL]  # resumable upload -- SEO-gated, always PRIVATE
  python3 youtube-api.py privacy VIDEO_ID public         # flip private|unlisted|public
  python3 youtube-api.py whoami                          # show auth info

Before uploading or editing metadata, run the gate -- it REFUSES bad/unoptimised metadata:
  python3 youtube-seo-check.py payload.json      |  python3 youtube-seo-check.py --video VIDEO_ID
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
    # videos.insert and thumbnails.set need upload; the broad `youtube` scope does NOT
    # cover it. Verified GRANTED against the live token endpoint 4 Aug 2026.
    "https://www.googleapis.com/auth/youtube.upload",
])
DATA_BASE = "https://www.googleapis.com/youtube/v3"
ANALYTICS_BASE = "https://youtubeanalytics.googleapis.com/v2"

with open(KEY) as f:
    creds = json.load(f)

# ── The channels on this Google account (verified 4 Aug 2026 via the Studio channel
# switcher + the API). `youtube-api.py channels` used to show ONE, because channels?mine=true
# returns only the account's own channel and never the Brand Account ones — which is how three
# of these four stayed invisible. Use these ids, or the short alias, on any command. ──
CHANNELS = {
    "training": {
        "id": "UCehJ9inoS0AldjaEIMwan_A", "handle": "@sygmasolutionstraining",
        "title": "Sygma Solutions Training", "created": "2026-03-22",
        "what": "THE training channel. Long-form Genny & CAT teaching (9-72 min). All videos "
                "carry auto-generated transcripts. This is the source for the Clancy resource hub.",
    },
    "pete": {
        "id": "UCh7dZlXSw36fAdtJGtD_PVA", "handle": "@peterashcroft6020",
        "title": "Peter Ashcroft", "created": "2014-08-15",
        "what": "Pete's personal channel and, historically, where the reach is: ~50k views off "
                "short kit clips (CAT Power Mode Problems alone is 25k). Mostly under 2 min, so "
                "most have no transcript. Also holds some Canary Detect and personal footage.",
    },
    "cd": {
        "id": "UCGcN7MAX1TENtIJcdw_OyRw", "handle": "@canarydetect",
        "title": "Canary Detect", "created": "2026-03-22",
        "what": "Canary Detect / leak detection. Drain survey and PipeMic material.",
    },
    "team": {
        "id": "UCPoeCrqGjS8fMufgMvV2WpQ", "handle": "@SygmaSolutionsTeam",
        "title": "Sygma Solutions Team", "created": "2026-03-22",
        "what": "EMPTY — 0 videos, no uploads playlist yet (listing it 404s, which is 'nothing "
                "here', not an access problem). Purpose not confirmed with Pete.",
    },
}


def resolve_channel(ref):
    """Accept a short alias ('training'), a @handle, or a raw channel id."""
    if ref in CHANNELS:
        return CHANNELS[ref]["id"]
    for c in CHANNELS.values():
        if ref.lower() in (c["handle"].lower(), c["title"].lower()):
            return c["id"]
    return ref


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

# ── Brand-account auth (uploads) ──────────────────────────────────────────────
# The service account ALWAYS resolves to Peter Ashcroft's personal channel. Sygma Solutions
# Training is a BRAND ACCOUNT, which only a human can pick at the sign-in screen, so uploads
# to it need a stored refresh token from that one-off consent. Proved 4 Aug 2026 by a real
# test upload landing on the wrong channel, and by videos.insert having no channel parameter
# (onBehalfOfContentOwnerChannel needs a CMS content owner; Sygma has none -- 404).
BRAND_CLIENT = os.path.join(os.environ.get("VAULT", "/tmp/pbs"), "Library", "processes",
                            "secrets", "sygma-youtube-oauth-client.json")
BRAND_TOKEN = os.path.join(os.environ.get("VAULT", "/tmp/pbs"), "Library", "processes",
                           "secrets", "sygma-youtube-refresh-token")
BRAND_CHANNEL = "UCehJ9inoS0AldjaEIMwan_A"


def brand_token():
    """Access token for the Sygma Solutions Training brand account, or None if not set up."""
    if _token_cache.get("brand_exp", 0) > time.time() + 60:
        return _token_cache["brand_tok"]
    try:
        c = json.load(open(BRAND_CLIENT))["installed"]
        rt = open(BRAND_TOKEN).read().strip()
    except (FileNotFoundError, KeyError):
        return None
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "client_id": c["client_id"], "client_secret": c["client_secret"],
                "refresh_token": rt, "grant_type": "refresh_token"}).encode())).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "invalid_grant" in body:
            print("Brand-account sign-in has EXPIRED (Google revokes these ~weekly while the "
                  "app is in Testing). Re-run the authorise flow to renew it.", file=sys.stderr)
        else:
            print(f"Brand token refresh failed {e.code}: {body[:200]}", file=sys.stderr)
        return None
    _token_cache["brand_tok"] = r["access_token"]
    _token_cache["brand_exp"] = time.time() + r.get("expires_in", 3600)
    return r["access_token"]


def data_api(path, params, token=None):
    """Read from the Data API. Pass `token` to read as somebody other than the service account.

    Why that argument exists (6 Aug 2026): the service account and the Sygma brand account are
    two different Google Cloud projects, so they carry two SEPARATE daily quotas. A script that
    uploads with brand_token() but reads with the default here can be refused on the read while
    the write path still has thousands of units left, and the refusal says "quota exceeded",
    which reads as "come back tomorrow". A whole morning was written off to that.
    """
    url = DATA_BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token or get_token()}"})
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
    """Every channel on this account, with what it holds. Not channels?mine=true — that
    returns one and hides the Brand Accounts."""
    print(f"{'ALIAS':<10} {'CHANNEL ID':<26} {'TITLE':<26} {'VIDEOS':>6} {'VIEWS':>8}  HANDLE")
    print("-" * 104)
    for alias, c in CHANNELS.items():
        try:
            r = data_api("/channels", {"part": "statistics", "id": c["id"]})
            st = r["items"][0]["statistics"]
            v, w = st.get("videoCount", "?"), st.get("viewCount", "?")
        except Exception:
            v = w = "?"
        print(f"{alias:<10} {c['id']:<26} {c['title'][:26]:<26} {v:>6} {w:>8}  {c['handle']}")
    print()
    for alias, c in CHANNELS.items():
        print(f"  {alias} — {c['what']}")

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
    # captions.list is owner-scoped, so a video on the Sygma brand channel has to be read with
    # the brand credentials. brand_token() returns None when that is not set up, which falls
    # back to the old behaviour.
    r = data_api("/captions", {"part": "snippet", "videoId": video_id}, token=brand_token())
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
    cap_tok = brand_token() or get_token()
    r = data_api("/captions", {"part": "snippet", "videoId": video_id}, token=cap_tok)
    items = r.get("items", [])
    if not items:
        print("No caption track to download."); sys.exit(2)
    items.sort(key=lambda t: t["snippet"].get("trackKind") == "asr")   # manual first
    track = items[0]
    url = f"{DATA_BASE}/captions/{track['id']}?tfmt={fmt}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cap_tok}"})
    body = urllib.request.urlopen(req).read().decode("utf-8", "ignore")
    if out:
        with open(out, "w") as f: f.write(body)
        words = len(body.split())
        print(f"Wrote {out} -- {len(body):,} chars, ~{words:,} words "
              f"({'auto-generated' if track['snippet'].get('trackKind') == 'asr' else 'manual'} track)")
    else:
        print(body)

def upload_video(path, payload_file, channel=None):
    """Resumable upload of a local file via videos.insert.

    GATED. Pete, 4 Aug 2026: "you need to ensure the titles and descriptions and all settings
    are fully correct and optimised for SEO, we use every angle we get." So youtube-seo-check.py
    runs FIRST and a BLOCK stops the upload -- there is no --force. Fix the metadata instead.

    Resumable, not multipart: a 948 MB file over a single POST has no way to report progress and
    no way to resume, and a training master is routinely bigger than that.

    Uploads are ALWAYS created private regardless of what the payload says. Making something
    public is Pete's call on something he has seen, not a side effect of a script run --
    flip it afterwards with `youtube-api.py privacy VIDEO_ID public`.
    """
    if not os.path.exists(path):
        print(f"No such file: {path}", file=sys.stderr); sys.exit(2)
    payload = json.load(open(payload_file))

    # ── the SEO gate, before a single byte moves ─────────────────────────────
    gate = os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube-seo-check.py")
    if os.path.exists(gate):
        r = subprocess.run([sys.executable, gate, payload_file], capture_output=True, text=True)
        print(r.stdout or "", end="")
        if r.returncode == 1:
            print("REFUSED by youtube-seo-check. Nothing was uploaded.", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"WARNING: {gate} not found -- metadata NOT checked.", file=sys.stderr)

    requested = (payload.get("status") or {}).get("privacyStatus")
    payload.setdefault("status", {})["privacyStatus"] = "private"
    if requested and requested != "private":
        print(f"NOTE: payload asked for '{requested}'. Uploading PRIVATE anyway -- "
              f"publish it once you have watched it back.")
    if channel:
        payload.setdefault("snippet", {})["channelId"] = resolve_channel(channel)

    # WHICH CHANNEL: videos.insert has no channel parameter -- it lands wherever the token
    # resolves. The brand token puts it on Sygma Solutions Training; the service account puts
    # it on Peter Ashcroft. YouTube cannot move a video between channels afterwards, so this
    # is stated out loud rather than left to chance.
    tok = brand_token()
    if tok:
        print(f"Uploading to Sygma Solutions Training ({BRAND_CHANNEL}).")
    else:
        tok = get_token()
        print("WARNING: no brand-account sign-in available -- this will land on the PERSONAL "
              "channel (Peter Ashcroft), NOT the training channel, and cannot be moved after.",
              file=sys.stderr)

    size = os.path.getsize(path)
    body = json.dumps(payload).encode()
    init = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?"
        + urllib.parse.urlencode({"part": "snippet,status", "uploadType": "resumable"}),
        data=body, method="POST",
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Length": str(size),
                 "X-Upload-Content-Type": "video/*"})
    try:
        session = urllib.request.urlopen(init).headers["Location"]
    except urllib.error.HTTPError as e:
        print(f"Could not start upload -- {e.code}: {e.read().decode()[:400]}", file=sys.stderr)
        sys.exit(1)

    CHUNK, sent, t0 = 8 * 1024 * 1024, 0, time.time()
    print(f"Uploading {os.path.basename(path)} ({size/1048576:.1f} MB)…")
    with open(path, "rb") as f:
        while sent < size:
            data = f.read(CHUNK)
            hi = sent + len(data) - 1
            req = urllib.request.Request(session, data=data, method="PUT",
                headers={"Content-Length": str(len(data)),
                         "Content-Range": f"bytes {sent}-{hi}/{size}"})
            try:
                resp = urllib.request.urlopen(req)
                out = json.loads(resp.read())          # 200 = the last chunk landed
                vid = out["id"]
                print(f"\n  DONE -- https://www.youtube.com/watch?v={vid}")
                print(f"  {out['snippet']['title']}  [{out['status']['privacyStatus']}]")
                return vid
            except urllib.error.HTTPError as e:
                if e.code != 308:                       # 308 = resume incomplete, keep going
                    print(f"\nUpload failed at byte {sent} -- {e.code}: "
                          f"{e.read().decode()[:300]}", file=sys.stderr)
                    sys.exit(1)
                rng = e.headers.get("Range")
                sent = int(rng.split("-")[1]) + 1 if rng else sent + len(data)
            el = max(time.time() - t0, 0.001)
            print(f"  {sent/1048576:>7.0f} / {size/1048576:.0f} MB "
                  f"({sent/1048576/el:.1f} MB/s)", flush=True)


def set_privacy(video_id, status):
    """Flip a video private / unlisted / public. Separate from upload, deliberately."""
    if status not in ("private", "unlisted", "public"):
        print("status must be private, unlisted or public", file=sys.stderr); sys.exit(2)
    tok = brand_token() or get_token()
    # read with the SAME token the write uses. Reading with the default sent this through the
    # service-account project, whose quota is separate; on 6 Aug 2026 that project was flat out
    # and every privacy flip died on the read with "quota exceeded" while the brand project,
    # which does the actual write, had its whole day untouched.
    cur = data_api("/videos", {"part": "status,snippet", "id": video_id}, token=tok)
    if not cur.get("items"):
        print(f"No such video: {video_id}", file=sys.stderr); sys.exit(2)
    st = cur["items"][0]["status"]; st["privacyStatus"] = status
    req = urllib.request.Request(
        DATA_BASE + "/videos?" + urllib.parse.urlencode({"part": "status"}),
        data=json.dumps({"id": video_id, "status": st}).encode(), method="PUT",
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req).read())
        print(f"{video_id} is now {r['status']['privacyStatus']}")
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()[:300]}", file=sys.stderr); sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "channels":
        list_channels()
    elif cmd == "channel":
        if len(args) < 2: print("Usage: youtube-api.py channel CHANNEL_ID [DAYS]"); sys.exit(1)
        channel_overview(resolve_channel(args[1]), int(args[2]) if len(args) > 2 else 30)
    elif cmd == "videos":
        if len(args) < 2: print("Usage: youtube-api.py videos CHANNEL_ID [DAYS]"); sys.exit(1)
        top_videos(resolve_channel(args[1]), int(args[2]) if len(args) > 2 else 30)
    elif cmd == "video":
        if len(args) < 2: print("Usage: youtube-api.py video VIDEO_ID [DAYS]"); sys.exit(1)
        video_stats(args[1], int(args[2]) if len(args) > 2 else 90)
    elif cmd == "traffic":
        if len(args) < 2: print("Usage: youtube-api.py traffic CHANNEL_ID [DAYS]"); sys.exit(1)
        traffic_sources(resolve_channel(args[1]), int(args[2]) if len(args) > 2 else 28)
    elif cmd == "captions":
        if len(args) < 2: print("Usage: youtube-api.py captions VIDEO_ID"); sys.exit(1)
        list_captions(args[1])
    elif cmd == "transcript":
        if len(args) < 2: print("Usage: youtube-api.py transcript VIDEO_ID [OUT_FILE] [srt|vtt]"); sys.exit(1)
        download_transcript(args[1], args[3] if len(args) > 3 else "srt",
                            args[2] if len(args) > 2 else None)
    elif cmd == "upload":
        if len(args) < 3:
            print("Usage: youtube-api.py upload /path/to/video.mp4 payload.json [CHANNEL]")
            sys.exit(1)
        upload_video(args[1], args[2], args[3] if len(args) > 3 else None)
    elif cmd == "privacy":
        if len(args) < 3: print("Usage: youtube-api.py privacy VIDEO_ID private|unlisted|public"); sys.exit(1)
        set_privacy(args[1], args[2])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
