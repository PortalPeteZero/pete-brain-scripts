#!/usr/bin/env python3
"""
Google Search Console API helper -- single canonical path for all GSC work.

Service account: sygma-seo-reader@sygma-seo-tools.iam.gserviceaccount.com
Auth:            Service account JWT (no DWD -- SA added directly as user to each property)
Scopes:          https://www.googleapis.com/auth/webmasters

Usage (CLI):
  python3 gsc-api.py sites
  python3 gsc-api.py top-pages sc-domain:sygma-solutions.com [days=28] [limit=20]
  python3 gsc-api.py top-queries sc-domain:sygma-solutions.com [days=28] [limit=30]
  python3 gsc-api.py page-queries sc-domain:sygma-solutions.com /path/to/page [days=28]
  python3 gsc-api.py inspect sc-domain:sygma-solutions.com https://sygma-solutions.com/page
  python3 gsc-api.py sitemaps sc-domain:sygma-solutions.com
  python3 gsc-api.py submit-sitemap sc-domain:sygma-solutions.com https://sygma-solutions.com/sitemap.xml
  python3 gsc-api.py compare sc-domain:sygma-solutions.com /path [this_days=28] [prev_days=28]
  python3 gsc-api.py whoami

Usage (library):
  from gsc_api import GSCAPI
  g = GSCAPI()
  g.list_sites()
  g.query(site, dimensions=["page"], date_range=28, limit=20)
  g.inspect_url(site, url)
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

KEY_PATH = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware on Railway (bootstrap materialises the key)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
SCOPE = "https://www.googleapis.com/auth/webmasters"
BASE_WEBMASTERS = "https://www.googleapis.com/webmasters/v3"
BASE_SEARCHCONSOLE = "https://searchconsole.googleapis.com/v1"


def _b64u(data):
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class GSCAPI:
    def __init__(self, key_path=KEY_PATH, scope=SCOPE):
        with open(os.path.abspath(key_path)) as f:
            self.creds = json.load(f)
        self.scope = scope
        self._token = None
        self._token_exp = 0

    # --- auth (service account, no DWD) ---------------------------------------

    def _get_token(self):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        now = int(time.time())
        header = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
        claim = _b64u(json.dumps({
            "iss": self.creds["client_email"],
            "scope": self.scope,
            "aud": "https://oauth2.googleapis.com/token",
            "exp": now + 3600,
            "iat": now,
        }))
        ts = f"{header}.{claim}"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(self.creds["private_key"])
            kf = f.name
        try:
            sig = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", kf, "-binary"],
                input=ts.encode(), capture_output=True, check=True,
            ).stdout
        finally:
            os.unlink(kf)
        jwt = f"{ts}.{_b64u(sig)}"
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt,
            }).encode(),
        )
        resp = json.loads(urllib.request.urlopen(req).read())
        self._token = resp["access_token"]
        self._token_exp = now + resp.get("expires_in", 3600)
        return self._token

    def _call(self, method, url, body=None, query=None):
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            raise RuntimeError(f"GSC API {method} {url} -> HTTP {e.code}: {msg}") from e

    # --- date helpers ---------------------------------------------------------

    @staticmethod
    def _date_range(days):
        """Return (start_date_str, end_date_str) for last N days (exclusive of today)."""
        today = date.today()
        end = today - timedelta(days=3)   # GSC data lags ~3 days
        start = end - timedelta(days=days - 1)
        return start.isoformat(), end.isoformat()

    @staticmethod
    def _encode_site(site):
        return urllib.parse.quote(site, safe="")

    # --- sites ----------------------------------------------------------------

    def list_sites(self):
        url = f"{BASE_WEBMASTERS}/sites"
        return self._call("GET", url).get("siteEntry", [])

    # --- search analytics -----------------------------------------------------

    def query(self, site, dimensions, date_range=28, limit=25,
              start_date=None, end_date=None, filters=None):
        """
        Run a searchAnalytics query.
        date_range: int (days back) or tuple (start_str, end_str)
        filters: list of dimension filter groups (raw API format)
        """
        if isinstance(date_range, int):
            sd, ed = self._date_range(date_range)
        else:
            sd, ed = date_range
        if start_date:
            sd = start_date
        if end_date:
            ed = end_date
        body = {
            "startDate": sd,
            "endDate": ed,
            "dimensions": dimensions,
            "rowLimit": limit,
        }
        if filters:
            body["dimensionFilterGroups"] = filters
        url = f"{BASE_WEBMASTERS}/sites/{self._encode_site(site)}/searchAnalytics/query"
        return self._call("POST", url, body=body).get("rows", [])

    def top_pages(self, site, days=28, limit=25):
        rows = self.query(site, ["page"], date_range=days, limit=limit)
        return [
            {
                "page": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"] * 100, 2),
                "position": round(r["position"], 1),
            }
            for r in rows
        ]

    def top_queries(self, site, days=28, limit=30):
        rows = self.query(site, ["query"], date_range=days, limit=limit)
        return [
            {
                "query": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"] * 100, 2),
                "position": round(r["position"], 1),
            }
            for r in rows
        ]

    def page_queries(self, site, page_path, days=28, limit=30):
        """Top queries for a specific page path."""
        # Resolve to full URL if a bare path given
        if page_path.startswith("/"):
            # Need a base domain -- derive from site URL
            if site.startswith("sc-domain:"):
                domain = site[len("sc-domain:"):]
                page_url = f"https://{domain}{page_path}"
            elif site.startswith("https://"):
                page_url = site.rstrip("/") + page_path
            else:
                page_url = page_path
        else:
            page_url = page_path

        filters = [{
            "filters": [{
                "dimension": "page",
                "operator": "equals",
                "expression": page_url,
            }]
        }]
        rows = self.query(site, ["query"], date_range=days, limit=limit, filters=filters)
        return [
            {
                "query": r["keys"][0],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": round(r["ctr"] * 100, 2),
                "position": round(r["position"], 1),
            }
            for r in rows
        ]

    def compare_page(self, site, page_path, this_days=28, prev_days=28):
        """Compare a page's performance across two consecutive periods."""
        today = date.today()
        lag = 3
        this_end = today - timedelta(days=lag)
        this_start = this_end - timedelta(days=this_days - 1)
        prev_end = this_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=prev_days - 1)

        if page_path.startswith("/"):
            if site.startswith("sc-domain:"):
                domain = site[len("sc-domain:"):]
                page_url = f"https://{domain}{page_path}"
            else:
                page_url = site.rstrip("/") + page_path
        else:
            page_url = page_path

        filters = [{"filters": [{"dimension": "page", "operator": "equals", "expression": page_url}]}]

        def _summary(start, end):
            rows = self.query(site, ["page"],
                              date_range=(start.isoformat(), end.isoformat()),
                              limit=1, filters=filters)
            if rows:
                r = rows[0]
                return {"clicks": r["clicks"], "impressions": r["impressions"],
                        "ctr": round(r["ctr"] * 100, 2), "position": round(r["position"], 1)}
            return {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": None}

        this = _summary(this_start, this_end)
        prev = _summary(prev_start, prev_end)

        def _delta(key, invert=False):
            if prev[key] and prev[key] != 0:
                d = ((this[key] - prev[key]) / prev[key]) * 100
                return round(-d if invert else d, 1)
            return None

        return {
            "page": page_url,
            "this_period": f"{this_start} → {this_end} ({this_days}d)",
            "prev_period": f"{prev_start} → {prev_end} ({prev_days}d)",
            "this": this,
            "prev": prev,
            "delta": {
                "clicks_pct": _delta("clicks"),
                "impressions_pct": _delta("impressions"),
                "position_pct": _delta("position", invert=True),  # lower is better
            },
        }

    # --- URL inspection -------------------------------------------------------

    def inspect_url(self, site, url):
        body = {"inspectionUrl": url, "siteUrl": site}
        endpoint = f"{BASE_SEARCHCONSOLE}/urlInspection/index:inspect"
        return self._call("POST", endpoint, body=body)

    # --- sitemaps -------------------------------------------------------------

    def list_sitemaps(self, site):
        url = f"{BASE_WEBMASTERS}/sites/{self._encode_site(site)}/sitemaps"
        return self._call("GET", url).get("sitemap", [])

    def submit_sitemap(self, site, sitemap_url):
        encoded_feed = urllib.parse.quote(sitemap_url, safe="")
        url = f"{BASE_WEBMASTERS}/sites/{self._encode_site(site)}/sitemaps/{encoded_feed}"
        return self._call("PUT", url)


# --- CLI ----------------------------------------------------------------------

def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    g = GSCAPI()
    cmd, *args = sys.argv[1:]

    if cmd == "sites":
        sites = g.list_sites()
        if not sites:
            print("No sites found -- check service account has been added to GSC properties.")
        for s in sites:
            print(f"{s.get('permissionLevel', '?'):12s}  {s['siteUrl']}")

    elif cmd == "top-pages":
        site = args[0]
        days = int(args[1]) if len(args) > 1 else 28
        limit = int(args[2]) if len(args) > 2 else 20
        rows = g.top_pages(site, days=days, limit=limit)
        print(f"Top pages -- {site} -- last {days}d")
        print(f"{'Clicks':>8}  {'Impr':>8}  {'CTR%':>6}  {'Pos':>5}  Page")
        print("-" * 80)
        for r in rows:
            print(f"{r['clicks']:>8}  {r['impressions']:>8}  {r['ctr']:>6}  {r['position']:>5}  {r['page']}")

    elif cmd == "top-queries":
        site = args[0]
        days = int(args[1]) if len(args) > 1 else 28
        limit = int(args[2]) if len(args) > 2 else 30
        rows = g.top_queries(site, days=days, limit=limit)
        print(f"Top queries -- {site} -- last {days}d")
        print(f"{'Clicks':>8}  {'Impr':>8}  {'CTR%':>6}  {'Pos':>5}  Query")
        print("-" * 80)
        for r in rows:
            print(f"{r['clicks']:>8}  {r['impressions']:>8}  {r['ctr']:>6}  {r['position']:>5}  {r['query']}")

    elif cmd == "page-queries":
        site, page = args[0], args[1]
        days = int(args[2]) if len(args) > 2 else 28
        rows = g.page_queries(site, page, days=days)
        print(f"Queries for {page} -- last {days}d")
        print(f"{'Clicks':>8}  {'Impr':>8}  {'CTR%':>6}  {'Pos':>5}  Query")
        print("-" * 80)
        for r in rows:
            print(f"{r['clicks']:>8}  {r['impressions']:>8}  {r['ctr']:>6}  {r['position']:>5}  {r['query']}")

    elif cmd == "inspect":
        site, url = args[0], args[1]
        result = g.inspect_url(site, url)
        print(json.dumps(result, indent=2))

    elif cmd == "sitemaps":
        site = args[0]
        maps = g.list_sitemaps(site)
        for m in maps:
            print(f"{m.get('lastSubmitted', '?'):26s}  {m.get('lastDownloaded', '?'):26s}  {m['path']}")

    elif cmd == "submit-sitemap":
        site, sitemap_url = args[0], args[1]
        g.submit_sitemap(site, sitemap_url)
        print(f"Submitted: {sitemap_url}")

    elif cmd == "compare":
        site, page = args[0], args[1]
        this_d = int(args[2]) if len(args) > 2 else 28
        prev_d = int(args[3]) if len(args) > 3 else 28
        result = g.compare_page(site, page, this_days=this_d, prev_days=prev_d)
        print(json.dumps(result, indent=2))

    elif cmd == "whoami":
        print(f"Service account: {g.creds['client_email']}")
        print(f"Key path: {os.path.abspath(KEY_PATH)}")
        print(f"Scope: {SCOPE}")

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
