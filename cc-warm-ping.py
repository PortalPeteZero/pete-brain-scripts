#!/usr/bin/env python3
# CRON-META
# schedule: */5 * * * *
# timezone: Atlantic/Canary
# what: keep-warm ping of the hottest CC page routes so they never cold-start
# why: idle serverless functions cold-start ~3.6s; a 5-min ping keeps them at the ~0.5s warm floor
# CRON-META-END
"""
cc-warm-ping.py — keep the hottest Command Centre serverless functions warm.

Each App-Router route compiles to its OWN Vercel serverless function, so warming one
health endpoint only warms that one function. This pings the page routes THEMSELVES
(Pete's most-clicked sections), all of which are force-dynamic + getViewer and cold-start
identically. No auth, no cookies, no DB, no redirects followed.

Success = "the function ran", NOT a status whitelist. For an unauthenticated caller:
  /  and  /browse        -> 307 redirect to /login
  the seven /m/* routes  -> 404 (owner/module gate calls notFound() before the gate throws)
  /api/warm              -> 200
In every case getViewer() + moduleByKey() run *before* the gate throws, so the function
boots and warms. We therefore count ANY completed HTTP response (2xx/3xx/4xx, incl. 404)
as a warm hit; only a connection error, a timeout, or a 5xx is a miss. Redirects are NOT
followed (the 307 itself is the warm signal).
"""
import sys
import socket
import urllib.request
import urllib.error

BASE = "https://commandcentre.info"
PATHS = [
    "/",            # 307 -> /login (unauth)
    "/m/tasks",     # 404 (unauth)
    "/m/notes",
    "/m/to-pay",
    "/m/ask",
    "/m/projects",
    "/m/brain",
    "/m/daily",
    "/browse",      # 307 -> /login (unauth)
    "/api/warm",    # 200
]
TIMEOUT = 8  # seconds per request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Don't follow 3xx — returning None makes urllib raise HTTPError with the 3xx code,
    # which we count as a warm hit (the function ran to produce the redirect).
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def ping(url):
    """Return (code_or_None, warm_bool)."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "cc-warm-ping/1.0"})
    try:
        resp = _OPENER.open(req, timeout=TIMEOUT)
        return resp.status, True                     # 2xx
    except urllib.error.HTTPError as e:
        return e.code, (e.code < 500)                # 3xx/4xx warm, 5xx miss
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
        return None, False                           # connection error / timeout


def main():
    warm = 0
    miss = 0
    parts = []
    for p in PATHS:
        code, ok = ping(BASE + p)
        if ok:
            warm += 1
        else:
            miss += 1
        parts.append(f"{p}={code if code is not None else 'ERR'}")
    print(f"cc-warm-ping: {warm} warm, {miss} miss  |  " + "  ".join(parts))
    # Non-zero exit only if EVERYTHING missed (likely a real outage), so a single flaky
    # route never marks the whole cron run failed.
    return 1 if warm == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
# watch-path canary 2026-07-11 (validation no-op; safe to keep)
