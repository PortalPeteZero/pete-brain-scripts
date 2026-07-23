#!/usr/bin/env python3
"""
Google Analytics 4 (GA4) API helper -- single canonical path for all GA4 work.

Service account: sygma-seo-reader@sygma-seo-tools.iam.gserviceaccount.com
Auth:            Service account JWT (no DWD -- SA added as Viewer to each GA4 property)
Scopes:          analytics.readonly (Data API / runReport -- REQUIRED, never drop it)
                 analytics.edit     (Admin API writes: property settings, currency, timezone)
                 Service account = no consent flow, so the requested scope is ours to set.
                 Widened 2026-07-23; before that every Admin write returned
                 403 ACCESS_TOKEN_SCOPE_INSUFFICIENT.

Admin API:       https://analyticsadmin.googleapis.com/v1beta (property config; accessBindings is v1alpha)
                 Read  e.g. GET  /v1beta/properties/{id}
                 Write e.g. PATCH /v1beta/properties/{id}?updateMask=currencyCode

Known properties:
  354127076   Sygma Solutions (sygma-solutions.com)
  537126447   Canary Detect (canary-detect.com)
  539604544   Lanzarote Lates (lanzarotelates.com) — created 2026-05-30, SA auto-Viewer via Sygma Solutions account-level access
  (others)    Add new properties in GA4 Admin → Property access management → SA email as Viewer

Usage (CLI):
  python3 ga4-api.py summary 354127076 [days=7]
  python3 ga4-api.py top-pages 354127076 [days=28] [limit=20]
  python3 ga4-api.py top-sources 354127076 [days=28] [limit=15]
  python3 ga4-api.py events 354127076 [days=7] [limit=20]
  python3 ga4-api.py conversions 354127076 [days=30]
  python3 ga4-api.py realtime 354127076
  python3 ga4-api.py page 354127076 /path/to/page [days=28]
  python3 ga4-api.py compare 354127076 [this_days=28] [prev_days=28]
  python3 ga4-api.py whoami

Usage (library):
  from ga4_api import GA4API
  g = GA4API()
  g.run_report(property_id, dimensions, metrics, date_range=7, limit=20)
  g.summary(property_id, days=7)
  g.realtime(property_id)
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
SCOPE = (
    "https://www.googleapis.com/auth/analytics.readonly "        # Data API (runReport) requires THIS one -- never drop it
    "https://www.googleapis.com/auth/analytics.edit"             # Admin API writes (property settings: currency, timezone)
)
BASE = "https://analyticsdata.googleapis.com/v1beta/properties"


def _b64u(data):
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class GA4API:
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

    def _call(self, method, url, body=None):
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
            raise RuntimeError(f"GA4 API {method} {url} -> HTTP {e.code}: {msg}") from e

    # --- helpers --------------------------------------------------------------

    @staticmethod
    def _date_range_str(days):
        return [{"startDate": f"{days}daysAgo", "endDate": "yesterday"}]

    @staticmethod
    def _two_period_ranges(this_days, prev_days):
        """Return two dateRanges for period-over-period comparison."""
        today = date.today()
        this_end = today - timedelta(days=1)
        this_start = this_end - timedelta(days=this_days - 1)
        prev_end = this_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=prev_days - 1)
        return [
            {"startDate": this_start.isoformat(), "endDate": this_end.isoformat(), "name": "this_period"},
            {"startDate": prev_start.isoformat(), "endDate": prev_end.isoformat(), "name": "prev_period"},
        ]

    @staticmethod
    def _parse_report(resp):
        """Flatten a GA4 RunReportResponse into a list of dicts."""
        dim_hdrs = [h["name"] for h in resp.get("dimensionHeaders", [])]
        met_hdrs = [h["name"] for h in resp.get("metricHeaders", [])]
        rows = []
        for row in resp.get("rows", []):
            r = {}
            for i, dv in enumerate(row.get("dimensionValues", [])):
                r[dim_hdrs[i]] = dv["value"]
            for i, mv in enumerate(row.get("metricValues", [])):
                r[met_hdrs[i]] = mv["value"]
            rows.append(r)
        return rows

    # --- reports --------------------------------------------------------------

    def run_report(self, property_id, dimensions, metrics, days=28, limit=20,
                   date_ranges=None, order_by_metric=None, order_by_dimension=None,
                   dimension_filter=None):
        """
        Generic report runner. Returns parsed rows.
        dimensions: list of str (dimension names)
        metrics: list of str (metric names)
        order_by_metric: str -- sort descending by this metric name
        order_by_dimension: str -- sort ascending by this dimension name (e.g. "date")
        dimension_filter: raw GA4 dimensionFilter dict
        """
        body = {
            "dateRanges": date_ranges or self._date_range_str(days),
            "dimensions": [{"name": d} for d in dimensions],
            "metrics": [{"name": m} for m in metrics],
            "limit": limit,
        }
        if order_by_metric:
            body["orderBys"] = [{"metric": {"metricName": order_by_metric}, "desc": True}]
        elif order_by_dimension:
            body["orderBys"] = [{"dimension": {"dimensionName": order_by_dimension}, "desc": False}]
        if dimension_filter:
            body["dimensionFilter"] = dimension_filter
        url = f"{BASE}/{property_id}:runReport"
        resp = self._call("POST", url, body=body)
        return self._parse_report(resp)

    def summary(self, property_id, days=7):
        """Sessions, users, page views, bounce rate for last N days."""
        rows = self.run_report(
            property_id,
            dimensions=["date"],
            metrics=["sessions", "activeUsers", "screenPageViews", "bounceRate", "averageSessionDuration"],
            days=days,
            limit=days + 5,
            order_by_dimension="date",
        )
        # Also get totals (no dimension)
        total_resp = self._call("POST", f"{BASE}/{property_id}:runReport", body={
            "dateRanges": self._date_range_str(days),
            "dimensions": [],
            "metrics": [
                {"name": "sessions"}, {"name": "activeUsers"},
                {"name": "screenPageViews"}, {"name": "bounceRate"},
                {"name": "averageSessionDuration"},
            ],
            "limit": 1,
        })
        totals = self._parse_report(total_resp)
        return {"by_date": rows, "totals": totals[0] if totals else {}}

    def top_pages(self, property_id, days=28, limit=20):
        return self.run_report(
            property_id,
            dimensions=["pagePath", "pageTitle"],
            metrics=["screenPageViews", "activeUsers", "averageSessionDuration", "bounceRate"],
            days=days, limit=limit,
            order_by_metric="screenPageViews",
        )

    def top_sources(self, property_id, days=28, limit=15):
        return self.run_report(
            property_id,
            dimensions=["sessionDefaultChannelGroup", "sessionSource", "sessionMedium"],
            metrics=["sessions", "activeUsers", "conversions"],
            days=days, limit=limit,
            order_by_metric="sessions",
        )

    def top_events(self, property_id, days=7, limit=20):
        return self.run_report(
            property_id,
            dimensions=["eventName"],
            metrics=["eventCount", "totalUsers"],
            days=days, limit=limit,
            order_by_metric="eventCount",
        )

    def conversions(self, property_id, days=30, limit=20):
        return self.run_report(
            property_id,
            dimensions=["eventName"],
            metrics=["conversions", "totalUsers"],
            days=days, limit=limit,
            order_by_metric="conversions",
        )

    def page_detail(self, property_id, page_path, days=28):
        """Stats for a specific page path."""
        dim_filter = {
            "filter": {
                "fieldName": "pagePath",
                "stringFilter": {"matchType": "EXACT", "value": page_path},
            }
        }
        return self.run_report(
            property_id,
            dimensions=["pagePath"],
            metrics=["screenPageViews", "activeUsers", "averageSessionDuration",
                     "bounceRate", "sessions", "engagementRate"],
            days=days, limit=1,
            dimension_filter=dim_filter,
        )

    def compare(self, property_id, this_days=28, prev_days=28):
        """Period-over-period comparison of key metrics."""
        ranges = self._two_period_ranges(this_days, prev_days)
        body = {
            "dateRanges": ranges,
            "dimensions": [],
            "metrics": [
                {"name": "sessions"}, {"name": "activeUsers"},
                {"name": "screenPageViews"}, {"name": "bounceRate"},
                {"name": "conversions"},
            ],
            "limit": 10,
        }
        resp = self._call("POST", f"{BASE}/{property_id}:runReport", body=body)
        rows = self._parse_report(resp)
        # Rows come back with a dateRange dimension added when using named ranges
        by_period = {}
        for row in rows:
            period = row.pop("dateRange", "unknown")
            by_period[period] = row

        this = by_period.get("this_period", {})
        prev = by_period.get("prev_period", {})
        deltas = {}
        for k in this:
            try:
                t, p = float(this[k]), float(prev.get(k, 0))
                if p:
                    deltas[f"{k}_delta_pct"] = round((t - p) / p * 100, 1)
            except (ValueError, TypeError):
                pass
        return {
            "this_period": f"last {this_days}d",
            "prev_period": f"prev {prev_days}d",
            "this": this,
            "prev": prev,
            "deltas": deltas,
        }

    def realtime(self, property_id):
        """Who's on the site right now.

        Note: Realtime API uses a DIFFERENT (smaller) dimension set than the
        Data API. `pagePath` is invalid here — use `unifiedScreenName` for
        page name. Full realtime schema:
        https://developers.google.com/analytics/devguides/reporting/data/v1/realtime-api-schema
        """
        body = {
            "dimensions": [{"name": "country"}, {"name": "unifiedScreenName"}],
            "metrics": [{"name": "activeUsers"}],
            "limit": 20,
        }
        url = f"{BASE}/{property_id}:runRealtimeReport"
        resp = self._call("POST", url, body=body)
        rows = self._parse_report(resp)
        # Get total active users
        total_body = {"metrics": [{"name": "activeUsers"}], "limit": 1}
        total_resp = self._call("POST", url, body=total_body)
        totals = self._parse_report(total_resp)
        return {
            "active_users_now": totals[0].get("activeUsers") if totals else "0",
            "by_page": rows,
        }


# --- CLI ----------------------------------------------------------------------

def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    g = GA4API()
    cmd, *args = sys.argv[1:]

    if cmd == "summary":
        prop = args[0]
        days = int(args[1]) if len(args) > 1 else 7
        result = g.summary(prop, days=days)
        print(f"GA4 Summary -- property {prop} -- last {days}d")
        t = result["totals"]
        print(f"  Sessions:     {t.get('sessions', '?')}")
        print(f"  Active users: {t.get('activeUsers', '?')}")
        print(f"  Page views:   {t.get('screenPageViews', '?')}")
        print(f"  Bounce rate:  {round(float(t.get('bounceRate', 0)) * 100, 1)}%")
        avg_dur = float(t.get("averageSessionDuration", 0))
        print(f"  Avg duration: {int(avg_dur // 60)}m {int(avg_dur % 60)}s")
        print("\nBy date:")
        print(f"  {'Date':12s}  {'Sessions':>10}  {'Users':>8}  {'Views':>8}")
        for row in result["by_date"]:
            print(f"  {row.get('date', '?'):12s}  {row.get('sessions', '?'):>10}"
                  f"  {row.get('activeUsers', '?'):>8}  {row.get('screenPageViews', '?'):>8}")

    elif cmd == "top-pages":
        prop = args[0]
        days = int(args[1]) if len(args) > 1 else 28
        limit = int(args[2]) if len(args) > 2 else 20
        rows = g.top_pages(prop, days=days, limit=limit)
        print(f"Top pages -- property {prop} -- last {days}d")
        print(f"{'Views':>8}  {'Users':>8}  Page")
        print("-" * 80)
        for r in rows:
            print(f"{r.get('screenPageViews', '?'):>8}  {r.get('activeUsers', '?'):>8}  {r.get('pagePath', '?')}")

    elif cmd == "top-sources":
        prop = args[0]
        days = int(args[1]) if len(args) > 1 else 28
        rows = g.top_sources(prop, days=days)
        print(f"Traffic sources -- property {prop} -- last {days}d")
        print(f"{'Sessions':>10}  {'Users':>8}  Channel / Source / Medium")
        print("-" * 80)
        for r in rows:
            ch = r.get("sessionDefaultChannelGroup", "?")
            src = r.get("sessionSource", "?")
            med = r.get("sessionMedium", "?")
            print(f"{r.get('sessions', '?'):>10}  {r.get('activeUsers', '?'):>8}  {ch} / {src} / {med}")

    elif cmd == "events":
        prop = args[0]
        days = int(args[1]) if len(args) > 1 else 7
        limit = int(args[2]) if len(args) > 2 else 20
        rows = g.top_events(prop, days=days, limit=limit)
        print(f"Events -- property {prop} -- last {days}d")
        print(f"{'Count':>10}  {'Users':>8}  Event")
        print("-" * 60)
        for r in rows:
            print(f"{r.get('eventCount', '?'):>10}  {r.get('totalUsers', '?'):>8}  {r.get('eventName', '?')}")

    elif cmd == "conversions":
        prop = args[0]
        days = int(args[1]) if len(args) > 1 else 30
        rows = g.conversions(prop, days=days)
        print(f"Conversions -- property {prop} -- last {days}d")
        print(f"{'Conv':>8}  {'Users':>8}  Event")
        print("-" * 60)
        for r in rows:
            print(f"{r.get('conversions', '?'):>8}  {r.get('totalUsers', '?'):>8}  {r.get('eventName', '?')}")

    elif cmd == "realtime":
        prop = args[0]
        result = g.realtime(prop)
        print(f"Realtime -- property {prop}")
        print(f"Active users NOW: {result['active_users_now']}")
        if result["by_page"]:
            print(f"\n{'Users':>6}  Page / Country")
            for r in result["by_page"]:
                print(f"{r.get('activeUsers', '?'):>6}  {r.get('pagePath', '?')}  ({r.get('country', '?')})")

    elif cmd == "page":
        prop, path = args[0], args[1]
        days = int(args[2]) if len(args) > 2 else 28
        rows = g.page_detail(prop, path, days=days)
        print(f"Page detail -- {path} -- last {days}d")
        print(json.dumps(rows, indent=2))

    elif cmd == "compare":
        prop = args[0]
        this_d = int(args[1]) if len(args) > 1 else 28
        prev_d = int(args[2]) if len(args) > 2 else 28
        result = g.compare(prop, this_days=this_d, prev_days=prev_d)
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
