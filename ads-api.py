#!/usr/bin/env python3
"""
Google Ads API helper -- single canonical path for all Sygma Ads work.

Auth pattern (NOT the Workspace SA — Ads API does not accept JWT/SA auth):
- Developer token at Library/processes/secrets/google-ads-developer-token
- OAuth client (Desktop type, installed-app flow) at Library/processes/secrets/google-ads-oauth-client.json
- Refresh token at Library/processes/secrets/google-ads-refresh-token (created by `bootstrap`)
- Access tokens are minted on-the-fly from the refresh token (1-hour lifetime, cached in-process)

Accounts:
- MCC (manager): 220-653-9186 -- sent as login-customer-id header (no dashes)
- Default advertiser: 173-909-0181 (Sygma Training - All Courses) -- query target by default

Approval state (2026-05-18): Basic Access, 15,000 ops/day.
Reference doc: [[google-ads-api-configuration]] in Library/processes/.

Usage (CLI):
  python3 ads-api.py bootstrap
    -- one-time OAuth consent flow on Pete's Mac, saves refresh_token.
       Opens default browser; Pete clicks Allow; refresh_token captured + saved.

  python3 ads-api.py list-accounts
    -- lists customer IDs accessible via the MCC.

  python3 ads-api.py query "SELECT campaign.id, campaign.name FROM campaign LIMIT 10" [customer_id]
    -- runs a GAQL query (default customer: 173-909-0181).

  python3 ads-api.py campaigns [customer_id]
    -- list active campaigns (name, status, channel, budget).

  python3 ads-api.py keywords [customer_id]
    -- list ad-group keywords (text, match type, status, criterion gid).

  python3 ads-api.py whoami
    -- prints accessible customers + verifies dev token works.

Usage (library):
  import importlib.util
  spec = importlib.util.spec_from_file_location("ads_api",
      "/tmp/pbs/Library/processes/scripts/ads-api.py")
  mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
  ads = mod.GoogleAdsAPI()
  rows = ads.query("SELECT campaign.id FROM campaign", customer_id="1739090181")
"""

import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SECRETS_DIR = (os.path.join(os.environ["VAULT"], "Library/processes/secrets")
               if os.environ.get("VAULT")
               else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets"))
DEV_TOKEN_PATH = os.path.join(SECRETS_DIR, "google-ads-developer-token")
OAUTH_CLIENT_PATH = os.path.join(SECRETS_DIR, "google-ads-oauth-client.json")
REFRESH_TOKEN_PATH = os.path.join(SECRETS_DIR, "google-ads-refresh-token")

API_VERSION = os.environ.get("GOOGLE_ADS_API_VERSION", "v21")
API_BASE = f"https://googleads.googleapis.com/{API_VERSION}"
SCOPE = "https://www.googleapis.com/auth/adwords"

MCC_LOGIN_CUSTOMER_ID = "2206539186"          # 220-653-9186
DEFAULT_CUSTOMER_ID = "1739090181"             # 173-909-0181


def _read(path):
    with open(path) as f:
        return f.read().strip()


def _http_post_form(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _http_post_json(url, body, headers):
    body_bytes = json.dumps(body).encode()
    headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body_bytes, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from None


def _http_get_json(url, headers):
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from None


# ---------- OAuth bootstrap (one-time) ----------

class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    captured = {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs:
            _OAuthCallbackHandler.captured["code"] = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>Auth complete</h1><p>You can close this tab and return to the terminal.</p>")
        elif "error" in qs:
            _OAuthCallbackHandler.captured["error"] = qs["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>Auth failed</h1>")
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        pass  # silence


def bootstrap_oauth():
    """One-time OAuth consent flow. Captures refresh_token + saves to secrets."""
    client = json.loads(_read(OAUTH_CLIENT_PATH))["installed"]
    client_id = client["client_id"]
    client_secret = client["client_secret"]

    # Pick a free localhost port
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close()
    redirect_uri = f"http://127.0.0.1:{port}/"

    # Build auth URL
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # force refresh_token issuance
    }
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)

    # Spin up local server
    server = http.server.HTTPServer(("127.0.0.1", port), _OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"Opening browser for OAuth consent...\nIf nothing opens, paste this URL into your browser:\n  {auth_url}\n", flush=True)
    webbrowser.open(auth_url)

    # Wait for redirect
    print("Waiting for Google redirect (timeout 5 min)...", flush=True)
    start = time.time()
    while time.time() - start < 300:
        if "code" in _OAuthCallbackHandler.captured or "error" in _OAuthCallbackHandler.captured:
            break
        time.sleep(0.5)
    server.shutdown()

    if "error" in _OAuthCallbackHandler.captured:
        raise RuntimeError(f"OAuth error: {_OAuthCallbackHandler.captured['error']}")
    code = _OAuthCallbackHandler.captured.get("code")
    if not code:
        raise RuntimeError("OAuth timed out -- no code received within 5 minutes")

    # Exchange code for refresh_token
    tokens = _http_post_form(
        "https://oauth2.googleapis.com/token",
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(f"No refresh_token in response: {tokens}")

    with open(REFRESH_TOKEN_PATH, "w") as f:
        f.write(refresh_token + "\n")
    print(f"Saved refresh_token to {REFRESH_TOKEN_PATH}", flush=True)
    print(f"Access token preview (1h ttl, not saved): {tokens.get('access_token','')[:20]}...", flush=True)
    return refresh_token


# ---------- API client ----------

class GoogleAdsAPI:
    def __init__(self,
                 login_customer_id=MCC_LOGIN_CUSTOMER_ID,
                 default_customer_id=DEFAULT_CUSTOMER_ID):
        self.dev_token = _read(DEV_TOKEN_PATH)
        client = json.loads(_read(OAUTH_CLIENT_PATH))["installed"]
        self.client_id = client["client_id"]
        self.client_secret = client["client_secret"]
        self.refresh_token = _read(REFRESH_TOKEN_PATH)
        self.login_customer_id = login_customer_id
        self.default_customer_id = default_customer_id
        self._access_token = None
        self._access_token_exp = 0

    def _access(self):
        if self._access_token and time.time() < self._access_token_exp - 30:
            return self._access_token
        tokens = _http_post_form(
            "https://oauth2.googleapis.com/token",
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        self._access_token = tokens["access_token"]
        self._access_token_exp = time.time() + tokens.get("expires_in", 3600)
        return self._access_token

    def _headers(self):
        return {
            "Authorization": f"Bearer {self._access()}",
            "developer-token": self.dev_token,
            "login-customer-id": self.login_customer_id,
        }

    def list_accessible_customers(self):
        url = f"{API_BASE}/customers:listAccessibleCustomers"
        return _http_get_json(url, self._headers())

    def query(self, gaql, customer_id=None):
        cid = (customer_id or self.default_customer_id).replace("-", "")
        url = f"{API_BASE}/customers/{cid}/googleAds:search"
        out = []
        page_token = None
        while True:
            body = {"query": gaql}
            if page_token:
                body["pageToken"] = page_token
            resp = _http_post_json(url, body, self._headers())
            out.extend(resp.get("results", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out

    def campaigns(self, customer_id=None):
        return self.query(
            "SELECT campaign.id, campaign.name, campaign.status, "
            "campaign.advertising_channel_type, campaign_budget.amount_micros "
            "FROM campaign WHERE campaign.status != 'REMOVED'",
            customer_id=customer_id,
        )

    def keywords(self, customer_id=None):
        return self.query(
            "SELECT ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, "
            "ad_group_criterion.keyword.match_type, ad_group_criterion.status, "
            "ad_group.id, ad_group.name "
            "FROM ad_group_criterion "
            "WHERE ad_group_criterion.type = 'KEYWORD' "
            "AND ad_group_criterion.status != 'REMOVED'",
            customer_id=customer_id,
        )

    # ---------- Mutations (write side) ----------

    def mutate(self, resource, operations, customer_id=None):
        """Generic mutation. resource ∈ {'adGroupCriteria','campaignCriteria',
        'campaigns','adGroups','sharedCriteria',...}. operations = list of
        {'create'|'update'|'remove': {...}, 'updateMask': '...'} dicts.
        """
        cid = (customer_id or self.default_customer_id).replace("-", "")
        url = f"{API_BASE}/customers/{cid}/{resource}:mutate"
        body = {"operations": operations}
        return _http_post_json(url, body, self._headers())

    def pause_keyword(self, criterion_resource_name, customer_id=None):
        """Pause a single keyword (ad_group_criterion). Pass the full resourceName.
        Returns the mutation response."""
        return self.mutate(
            "adGroupCriteria",
            [{
                "update": {
                    "resourceName": criterion_resource_name,
                    "status": "PAUSED",
                },
                "updateMask": "status",
            }],
            customer_id=customer_id,
        )

    def enable_keyword(self, criterion_resource_name, customer_id=None):
        return self.mutate(
            "adGroupCriteria",
            [{
                "update": {
                    "resourceName": criterion_resource_name,
                    "status": "ENABLED",
                },
                "updateMask": "status",
            }],
            customer_id=customer_id,
        )

    def add_campaign_negative(self, campaign_id, keyword_text, match_type="EXACT", customer_id=None):
        """Add a campaign-level negative keyword. match_type ∈ {EXACT,PHRASE,BROAD}."""
        cid = (customer_id or self.default_customer_id).replace("-", "")
        return self.mutate(
            "campaignCriteria",
            [{
                "create": {
                    "campaign": f"customers/{cid}/campaigns/{campaign_id}",
                    "negative": True,
                    "keyword": {"text": keyword_text, "matchType": match_type},
                }
            }],
            customer_id=customer_id,
        )

    def add_shared_negative(self, shared_set_id, keyword_text, match_type="EXACT", customer_id=None):
        """Add a negative keyword to a shared negative list (e.g. Sygma Master Negatives)."""
        cid = (customer_id or self.default_customer_id).replace("-", "")
        return self.mutate(
            "sharedCriteria",
            [{
                "create": {
                    "sharedSet": f"customers/{cid}/sharedSets/{shared_set_id}",
                    "keyword": {"text": keyword_text, "matchType": match_type},
                }
            }],
            customer_id=customer_id,
        )

    def remove_ad_group(self, ad_group_id, customer_id=None):
        """Soft-remove an ad group (sets status REMOVED). History preserved."""
        cid = (customer_id or self.default_customer_id).replace("-", "")
        return self.mutate(
            "adGroups",
            [{"remove": f"customers/{cid}/adGroups/{ad_group_id}"}],
            customer_id=customer_id,
        )

    def remove_ad_groups(self, ad_group_ids, customer_id=None):
        """Bulk soft-remove. Returns the mutation response."""
        cid = (customer_id or self.default_customer_id).replace("-", "")
        ops = [{"remove": f"customers/{cid}/adGroups/{gid}"} for gid in ad_group_ids]
        return self.mutate("adGroups", ops, customer_id=customer_id)

    # ---- Asset / extension management ----

    def create_sitelink_asset(self, link_text, description1, description2,
                               final_url, name=None, customer_id=None):
        """Create a sitelink asset at customer level. Returns the asset resourceName.
        Sitelink limits per Google: link_text ≤25 chars, descriptions ≤35 chars each.
        """
        cid = (customer_id or self.default_customer_id).replace("-", "")
        body_create = {
            "type": "SITELINK",
            "finalUrls": [final_url],
            "sitelinkAsset": {
                "linkText": link_text,
                "description1": description1,
                "description2": description2,
            },
        }
        if name:
            body_create["name"] = name
        resp = self.mutate("assets", [{"create": body_create}], customer_id=customer_id)
        return resp["results"][0]["resourceName"]

    def link_asset_to_campaign(self, asset_resource_name, campaign_id,
                                field_type="SITELINK", customer_id=None):
        """Link an existing asset to a campaign with the given field_type
        (SITELINK | CALLOUT | STRUCTURED_SNIPPET | CALL | PROMOTION | PRICE | IMAGE | LEAD_FORM)."""
        cid = (customer_id or self.default_customer_id).replace("-", "")
        return self.mutate(
            "campaignAssets",
            [{
                "create": {
                    "campaign": f"customers/{cid}/campaigns/{campaign_id}",
                    "asset": asset_resource_name,
                    "fieldType": field_type,
                }
            }],
            customer_id=customer_id,
        )

    def add_sitelinks_to_campaign(self, campaign_id, sitelinks, customer_id=None):
        """Convenience: create + attach multiple sitelinks in one call.
        sitelinks = list of {link_text, description1, description2, final_url, name?}.
        Returns list of (asset_resource_name, link_text)."""
        out = []
        for s in sitelinks:
            rn = self.create_sitelink_asset(
                link_text=s["link_text"],
                description1=s["description1"],
                description2=s["description2"],
                final_url=s["final_url"],
                name=s.get("name"),
                customer_id=customer_id,
            )
            self.link_asset_to_campaign(rn, campaign_id, "SITELINK", customer_id=customer_id)
            out.append((rn, s["link_text"]))
        return out


# ---------- CLI ----------

def _cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__); return

    cmd = args[0]
    if cmd == "bootstrap":
        bootstrap_oauth(); return

    ads = GoogleAdsAPI()
    if cmd == "whoami":
        out = ads.list_accessible_customers()
        print(json.dumps(out, indent=2))
    elif cmd == "list-accounts":
        out = ads.list_accessible_customers()
        for rn in out.get("resourceNames", []):
            print(rn)
    elif cmd == "query":
        if len(args) < 2:
            print("Usage: query 'GAQL' [customer_id]"); sys.exit(2)
        gaql = args[1]
        cid = args[2] if len(args) > 2 else None
        rows = ads.query(gaql, customer_id=cid)
        print(json.dumps(rows, indent=2))
    elif cmd == "campaigns":
        cid = args[1] if len(args) > 1 else None
        rows = ads.campaigns(customer_id=cid)
        for r in rows:
            c = r.get("campaign", {})
            b = r.get("campaignBudget", {})
            print(f"  {c.get('id'):>15}  {c.get('status','?'):<8}  {c.get('advertisingChannelType','?'):<10}  budget_micros={b.get('amountMicros','?'):<12}  {c.get('name','?')}")
    elif cmd == "keywords":
        cid = args[1] if len(args) > 1 else None
        rows = ads.keywords(customer_id=cid)
        for r in rows:
            crit = r.get("adGroupCriterion", {})
            kw = crit.get("keyword", {})
            ag = r.get("adGroup", {})
            print(f"  {crit.get('criterionId'):>15}  {kw.get('matchType','?'):<10}  {crit.get('status','?'):<8}  ag={ag.get('id')} ({ag.get('name','?')[:40]})  {kw.get('text','?')}")
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    _cli()
