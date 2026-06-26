#!/usr/bin/env python3
"""
xero-api.py -- Xero accounting API helper
Auth: OAuth 2.0 Authorization Code + PKCE. Tokens stored in secrets/xero-tokens.json.
Run `python3 xero-api.py auth` once to authorise. Refresh tokens are auto-renewed.

Usage:
  python3 xero-api.py auth                          # one-time OAuth flow (opens browser)
  python3 xero-api.py refresh                       # manually refresh access token
  python3 xero-api.py orgs                          # list connected Xero organisations
  python3 xero-api.py invoices ORG_ID [status]      # invoices (status: AUTHORISED|PAID|DRAFT)
  python3 xero-api.py invoice ORG_ID INVOICE_ID     # single invoice detail
  python3 xero-api.py contacts ORG_ID [search]      # list/search contacts
  python3 xero-api.py pnl ORG_ID FROM_DATE TO_DATE  # profit & loss report (YYYY-MM-DD)
  python3 xero-api.py balance ORG_ID DATE           # balance sheet
  python3 xero-api.py whoami                        # show connected orgs + token status
"""

import json, time, os, sys, secrets, urllib.request, urllib.parse, urllib.error
import http.server, threading, webbrowser

SECRETS_DIR = (os.path.join(os.environ["VAULT"], "Library", "processes", "secrets")
               if os.environ.get("VAULT")                       # $VAULT-aware (/tmp/pbs flat layout; matches drive-api.py)
               else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets"))
CREDS_FILE  = os.path.join(SECRETS_DIR, "xero-credentials.json")
TOKENS_FILE = os.path.join(SECRETS_DIR, "xero-tokens.json")

REDIRECT_URI  = "http://localhost:8765/callback"
AUTH_URL      = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL     = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
API_BASE      = "https://api.xero.com/api.xro/2.0"
REPORTS_BASE  = "https://api.xero.com/api.xro/2.0/Reports"

SCOPES = "offline_access openid accounting.contacts accounting.settings accounting.settings.read accounting.invoices accounting.payments accounting.banktransactions accounting.reports.profitandloss.read accounting.reports.balancesheet.read accounting.manualjournals"


# ---------------------------------------------------------------------------
# Credentials + token management
# ---------------------------------------------------------------------------

def load_creds():
    if not os.path.exists(CREDS_FILE):
        print(f"ERROR: {CREDS_FILE} not found.")
        print("Create it with: {\"client_id\": \"...\", \"client_secret\": \"...\"}")
        sys.exit(1)
    return json.load(open(CREDS_FILE))

def load_tokens():
    if not os.path.exists(TOKENS_FILE):
        print("No tokens found. Run: python3 xero-api.py auth")
        sys.exit(1)
    return json.load(open(TOKENS_FILE))

def save_tokens(tokens):
    os.makedirs(SECRETS_DIR, exist_ok=True)
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

def get_access_token():
    tokens = load_tokens()
    # If access token still valid (with 60s buffer), return it
    if tokens.get("expires_at", 0) > time.time() + 60:
        return tokens["access_token"]
    # Otherwise refresh
    print("Access token expired, refreshing...", file=sys.stderr)
    return refresh_token_flow(tokens)

def refresh_token_flow(tokens=None):
    creds = load_creds()
    if tokens is None:
        tokens = load_tokens()
    data = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id":     creds["client_id"],
        "client_secret": creds["client_secret"],
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        print(f"Token refresh failed: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    tokens["access_token"]  = resp["access_token"]
    tokens["refresh_token"] = resp.get("refresh_token", tokens["refresh_token"])
    tokens["expires_at"]    = time.time() + resp.get("expires_in", 1800)
    save_tokens(tokens)
    print("Token refreshed OK.", file=sys.stderr)
    return tokens["access_token"]


# ---------------------------------------------------------------------------
# OAuth auth flow (one-time)
# ---------------------------------------------------------------------------

def auth_flow():
    """Step 1: print the auth URL for Pete to open. Pete pastes the redirect URL back."""
    creds = load_creds()
    state = secrets.token_hex(16)
    params = {
        "response_type": "code",
        "client_id":     creds["client_id"],
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPES,
        "state":         state,
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    print(f"\nOpen this URL in your browser:\n\n{url}\n")
    print("After authorising, the browser will redirect to localhost:8765 (which will fail to load).")
    print("Copy the full URL from the address bar and paste it here:")
    callback_url = input("> ").strip()
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(callback_url).query)
    code  = qs.get("code",  [""])[0]
    rstate = qs.get("state", [""])[0]
    if not code:
        print("ERROR: No code found in URL. Did you paste the full redirect URL?")
        sys.exit(1)
    if rstate != state:
        print(f"ERROR: State mismatch. Expected {state}, got {rstate}")
        sys.exit(1)
    exchange_code(creds, code)

def exchange_code(creds, code):
    data = urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "client_id":     creds["client_id"],
        "client_secret": creds["client_secret"],
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        print(f"Token exchange failed: {e.read().decode()}")
        sys.exit(1)
    tokens = {
        "access_token":  resp["access_token"],
        "refresh_token": resp["refresh_token"],
        "expires_at":    time.time() + resp.get("expires_in", 1800),
        "authorised_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_tokens(tokens)
    print(f"\nAuthorised successfully. Tokens saved.")
    print("Connected organisations:")
    list_orgs()


# ---------------------------------------------------------------------------
# API helper
# ---------------------------------------------------------------------------

def api(method, path, org_id=None, params=None, body=None, base=API_BASE):
    token = get_access_token()
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if org_id:
        headers["Xero-tenant-id"] = org_id
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req).read()
        return json.loads(resp) if resp else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        print(f"Error {e.code}: {body_text}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def list_orgs():
    token = get_access_token()
    req = urllib.request.Request(CONNECTIONS_URL,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Accept": "application/json"})
    orgs = json.loads(urllib.request.urlopen(req).read())
    for o in orgs:
        print(f"{o['tenantId']}  {o['tenantName']}  ({o['tenantType']})")
    return orgs


def list_invoices(org_id, status=None):
    params = {"order": "DueDate DESC"}
    if status:
        params["where"] = f'Status=="{status}"'
    resp = api("GET", "/Invoices", org_id=org_id, params=params)
    invoices = resp.get("Invoices", [])
    if not invoices:
        print("No invoices found.")
        return
    print(f"{'ID':<8}  {'Number':<15}  {'Contact':<30}  {'Amount':>10}  {'Due':<12}  Status")
    print("-" * 90)
    for inv in invoices[:50]:
        num     = inv.get("InvoiceNumber", "-")
        contact = (inv.get("Contact", {}).get("Name", "-") or "-")[:28]
        amount  = f"{inv.get('Total', 0):,.2f} {inv.get('CurrencyCode','')}"
        due     = inv.get("DueDateString", "-")[:10]
        status  = inv.get("Status", "-")
        iid     = inv.get("InvoiceID", "")[:8]
        print(f"{iid:<8}  {num:<15}  {contact:<30}  {amount:>10}  {due:<12}  {status}")


def get_invoice(org_id, invoice_id):
    resp = api("GET", f"/Invoices/{invoice_id}", org_id=org_id)
    inv = resp.get("Invoices", [{}])[0]
    print(f"Invoice:   {inv.get('InvoiceNumber')}")
    print(f"Contact:   {inv.get('Contact', {}).get('Name')}")
    print(f"Status:    {inv.get('Status')}")
    print(f"Date:      {inv.get('DateString','')[:10]}")
    print(f"Due:       {inv.get('DueDateString','')[:10]}")
    print(f"Subtotal:  {inv.get('SubTotal'):,.2f}")
    print(f"Tax:       {inv.get('TotalTax'):,.2f}")
    print(f"Total:     {inv.get('Total'):,.2f} {inv.get('CurrencyCode')}")
    print(f"AmtDue:    {inv.get('AmountDue'):,.2f}")
    lines = inv.get("LineItems", [])
    if lines:
        print(f"\nLine items ({len(lines)}):")
        for li in lines:
            desc = (li.get("Description") or "-")[:50]
            qty  = li.get("Quantity", 1)
            unit = li.get("UnitAmount", 0)
            tot  = li.get("LineAmount", 0)
            print(f"  {qty}x {desc:<50}  @ {unit:,.2f}  = {tot:,.2f}")


def list_contacts(org_id, search=None):
    params = {"order": "Name ASC"}
    if search:
        params["where"] = f'Name.Contains("{search}")'
    resp = api("GET", "/Contacts", org_id=org_id, params=params)
    contacts = resp.get("Contacts", [])
    if not contacts:
        print("No contacts found.")
        return
    for c in contacts[:50]:
        name   = (c.get("Name") or "-")[:35]
        email  = (c.get("EmailAddress") or "-")[:35]
        status = c.get("ContactStatus", "-")
        cid    = c.get("ContactID", "")[:8]
        print(f"{cid}  {name:<35}  {email:<35}  {status}")


def profit_loss(org_id, from_date, to_date):
    params = {"fromDate": from_date, "toDate": to_date}
    resp = api("GET", "/ProfitAndLoss", org_id=org_id, params=params, base=REPORTS_BASE)
    report = resp.get("Reports", [{}])[0]
    print(f"Profit & Loss: {report.get('ReportTitles', [''])[1] if len(report.get('ReportTitles',[])) > 1 else ''}")
    print(f"Period: {from_date} to {to_date}\n")
    for row in report.get("Rows", []):
        title = row.get("Title", "")
        rtype = row.get("RowType", "")
        if rtype == "Section" and title:
            print(f"\n{title}")
            print("-" * 40)
        for r in row.get("Rows", []):
            if r.get("RowType") == "Row":
                cells = r.get("Cells", [])
                label = cells[0].get("Value", "") if cells else ""
                value = cells[1].get("Value", "") if len(cells) > 1 else ""
                if label:
                    print(f"  {label:<35}  {value:>12}")
            elif r.get("RowType") == "SummaryRow":
                cells = r.get("Cells", [])
                label = cells[0].get("Value", "") if cells else ""
                value = cells[1].get("Value", "") if len(cells) > 1 else ""
                if label:
                    print(f"{'':2}{label:<35}  {value:>12}")


def balance_sheet(org_id, date):
    params = {"date": date}
    resp = api("GET", "/BalanceSheet", org_id=org_id, params=params, base=REPORTS_BASE)
    report = resp.get("Reports", [{}])[0]
    print(f"Balance Sheet as at {date}\n")
    for row in report.get("Rows", []):
        title = row.get("Title", "")
        rtype = row.get("RowType", "")
        if rtype == "Section" and title:
            print(f"\n{title}")
            print("-" * 40)
        for r in row.get("Rows", []):
            if r.get("RowType") in ("Row", "SummaryRow"):
                cells = r.get("Cells", [])
                label = cells[0].get("Value", "") if cells else ""
                value = cells[1].get("Value", "") if len(cells) > 1 else ""
                if label:
                    print(f"  {label:<35}  {value:>12}")


def whoami():
    tokens = load_tokens()
    expires = tokens.get("expires_at", 0)
    auth_at = tokens.get("authorised_at", "?")
    print(f"Authorised at: {auth_at}")
    print(f"Access token expires: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires))}")
    exp_days = (expires - time.time()) / 86400
    print(f"Refresh token: valid ~{60:.0f} days from last refresh (renews on each use)")
    print()
    print("Connected organisations:")
    list_orgs()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]

    if cmd == "auth":
        auth_flow()
    elif cmd == "refresh":
        tokens = load_tokens()
        refresh_token_flow(tokens)
        print("Token refreshed OK.")
    elif cmd == "orgs":
        list_orgs()
    elif cmd == "invoices":
        if len(args) < 2: print("Usage: xero-api.py invoices ORG_ID [AUTHORISED|PAID|DRAFT]"); sys.exit(1)
        list_invoices(args[1], args[2] if len(args) > 2 else None)
    elif cmd == "invoice":
        if len(args) < 3: print("Usage: xero-api.py invoice ORG_ID INVOICE_ID"); sys.exit(1)
        get_invoice(args[1], args[2])
    elif cmd == "contacts":
        if len(args) < 2: print("Usage: xero-api.py contacts ORG_ID [search term]"); sys.exit(1)
        list_contacts(args[1], args[2] if len(args) > 2 else None)
    elif cmd == "pnl":
        if len(args) < 4: print("Usage: xero-api.py pnl ORG_ID FROM_DATE TO_DATE"); sys.exit(1)
        profit_loss(args[1], args[2], args[3])
    elif cmd == "balance":
        if len(args) < 3: print("Usage: xero-api.py balance ORG_ID DATE"); sys.exit(1)
        balance_sheet(args[1], args[2])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
