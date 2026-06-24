#!/usr/bin/env python3
"""
sheets-api.py -- Google Sheets API helper
Auth: service account JWT + DWD (impersonates pete.ashcroft@sygma-solutions.com)
Scope: https://www.googleapis.com/auth/spreadsheets
Usage:
  python3 sheets-api.py read SHEET_ID RANGE          # read cells (e.g. Sheet1!A1:Z50)
  python3 sheets-api.py append SHEET_ID SHEET_NAME '["val1","val2"]'  # append row
  python3 sheets-api.py write SHEET_ID RANGE '[[...]]'  # write cells
  python3 sheets-api.py clear SHEET_ID RANGE         # clear a range
  python3 sheets-api.py create "Title"               # create new spreadsheet
  python3 sheets-api.py info SHEET_ID                # get sheet metadata
  python3 sheets-api.py sheets SHEET_ID              # list sheets/tabs
  python3 sheets-api.py whoami                       # show auth info
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys

KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
IMPERSONATE = "pete.ashcroft@sygma-solutions.com"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
BASE = "https://sheets.googleapis.com/v4/spreadsheets"

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
        "iss": creds["client_email"], "sub": IMPERSONATE, "scope": SCOPE,
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

def api(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {get_token()}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req).read()
        return json.loads(resp) if resp else {}
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

def read_range(sheet_id, range_):
    resp = api("GET", f"/{sheet_id}/values/{urllib.parse.quote(range_)}")
    values = resp.get("values", [])
    if not values:
        print("(empty)"); return
    # Calculate column widths
    widths = []
    for row in values:
        for i, cell in enumerate(row):
            while len(widths) <= i:
                widths.append(0)
            widths[i] = max(widths[i], len(str(cell)))
    for row in values:
        parts = []
        for i, w in enumerate(widths):
            cell = row[i] if i < len(row) else ""
            parts.append(str(cell).ljust(w))
        print("  ".join(parts))

def append_row(sheet_id, sheet_name, values_json):
    values = json.loads(values_json)
    if not isinstance(values[0], list):
        values = [values]  # wrap single row
    body = {"values": values}
    range_ = urllib.parse.quote(f"{sheet_name}!A1")
    resp = api("POST", f"/{sheet_id}/values/{range_}:append",
               params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
               body=body)
    print(f"Appended {resp.get('updates', {}).get('updatedRows', '?')} row(s)")

def write_range(sheet_id, range_, values_json):
    values = json.loads(values_json)
    if values and not isinstance(values[0], list):
        values = [values]
    body = {"values": values, "range": range_, "majorDimension": "ROWS"}
    resp = api("PUT", f"/{sheet_id}/values/{urllib.parse.quote(range_)}",
               params={"valueInputOption": "USER_ENTERED"},
               body=body)
    print(f"Written {resp.get('updatedCells', '?')} cell(s)")

def clear_range(sheet_id, range_):
    resp = api("POST", f"/{sheet_id}/values/{urllib.parse.quote(range_)}:clear")
    print(f"Cleared: {resp.get('clearedRange', range_)}")

def create_sheet(title):
    body = {"properties": {"title": title}}
    resp = api("POST", "", body=body)
    print(f"Created: {resp['properties']['title']}")
    print(f"ID: {resp['spreadsheetId']}")
    print(f"URL: {resp['spreadsheetUrl']}")

def sheet_info(sheet_id):
    resp = api("GET", f"/{sheet_id}", params={"fields": "spreadsheetId,properties,sheets.properties"})
    print(f"Title: {resp['properties']['title']}")
    print(f"ID: {resp['spreadsheetId']}")
    print(f"Locale: {resp['properties'].get('locale','?')}")
    sheets = resp.get("sheets", [])
    print(f"\nSheets ({len(sheets)}):")
    for s in sheets:
        p = s["properties"]
        print(f"  [{p['sheetId']}] {p['title']} ({p['gridProperties']['rowCount']}r x {p['gridProperties']['columnCount']}c)")

def list_sheets(sheet_id):
    resp = api("GET", f"/{sheet_id}", params={"fields": "sheets.properties"})
    for s in resp.get("sheets", []):
        p = s["properties"]
        print(f"{p['sheetId']:>8}  {p['title']}")

def whoami():
    # Verify DWD token exchange works (Sheets has no /about endpoint)
    token = get_token()
    print(f"Impersonating: {IMPERSONATE}")
    print(f"Scope: {SCOPE}")
    print(f"Token acquired: OK ({len(token)} chars)")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "read":
        if len(args) < 3: print("Usage: sheets-api.py read SHEET_ID RANGE"); sys.exit(1)
        read_range(args[1], args[2])
    elif cmd == "append":
        if len(args) < 4: print("Usage: sheets-api.py append SHEET_ID SHEET_NAME '[...]'"); sys.exit(1)
        append_row(args[1], args[2], args[3])
    elif cmd == "write":
        if len(args) < 4: print("Usage: sheets-api.py write SHEET_ID RANGE '[[...]]'"); sys.exit(1)
        write_range(args[1], args[2], args[3])
    elif cmd == "clear":
        if len(args) < 3: print("Usage: sheets-api.py clear SHEET_ID RANGE"); sys.exit(1)
        clear_range(args[1], args[2])
    elif cmd == "create":
        if len(args) < 2: print("Usage: sheets-api.py create 'Title'"); sys.exit(1)
        create_sheet(args[1])
    elif cmd == "info":
        if len(args) < 2: print("Usage: sheets-api.py info SHEET_ID"); sys.exit(1)
        sheet_info(args[1])
    elif cmd == "sheets":
        if len(args) < 2: print("Usage: sheets-api.py sheets SHEET_ID"); sys.exit(1)
        list_sheets(args[1])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
