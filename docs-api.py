#!/usr/bin/env python3
"""
docs-api.py -- Google Docs API helper
Auth: service account JWT + DWD (impersonates pete.ashcroft@sygma-solutions.com)
Scope: https://www.googleapis.com/auth/documents + drive (for create/export)
Usage:
  python3 docs-api.py read DOC_ID                   # extract full text
  python3 docs-api.py create "Title" [FOLDER_ID]    # create blank doc (optionally in Drive folder)
  python3 docs-api.py insert DOC_ID "heading" "body text"  # append content
  python3 docs-api.py export DOC_ID /local/out.pdf  # export as PDF
  python3 docs-api.py info DOC_ID                   # metadata
  python3 docs-api.py whoami                        # show auth info
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys

KEY = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware on Railway (bootstrap materialises the key)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
IMPERSONATE = "pete.ashcroft@sygma-solutions.com"
SCOPES = "https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/drive"
DOCS_BASE = "https://docs.googleapis.com/v1/documents"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"

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

def api(method, url, params=None, body=None):
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

def extract_text(doc):
    """Recursively extract plain text from doc content."""
    text = []
    for elem in doc.get("body", {}).get("content", []):
        para = elem.get("paragraph")
        if para:
            for pe in para.get("elements", []):
                tr = pe.get("textRun")
                if tr:
                    text.append(tr.get("content", ""))
    return "".join(text)

def read_doc(doc_id):
    doc = api("GET", f"{DOCS_BASE}/{doc_id}")
    print(f"=== {doc.get('title', 'Untitled')} ===\n")
    print(extract_text(doc))

def create_doc(title, folder_id=None):
    # Create via Drive (so we can place it in a folder)
    body = {"name": title, "mimeType": "application/vnd.google-apps.document"}
    if folder_id:
        body["parents"] = [folder_id]
    result = api("POST", f"{DRIVE_BASE}/files", body=body)
    print(f"Created: {result['name']}")
    print(f"ID: {result['id']}")
    print(f"URL: https://docs.google.com/document/d/{result['id']}/edit")

def insert_content(doc_id, heading, body_text):
    # Get current end index
    doc = api("GET", f"{DOCS_BASE}/{doc_id}")
    content = doc.get("body", {}).get("content", [])
    end_index = content[-1].get("endIndex", 1) - 1 if content else 1

    requests = []
    # Insert body text first (at end), then heading (so heading appears before body)
    full_text = f"{heading}\n{body_text}\n"
    requests.append({
        "insertText": {"location": {"index": end_index}, "text": full_text}
    })
    # Style heading
    requests.append({
        "updateParagraphStyle": {
            "range": {"startIndex": end_index, "endIndex": end_index + len(heading) + 1},
            "paragraphStyle": {"namedStyleType": "HEADING_2"},
            "fields": "namedStyleType"
        }
    })
    api("POST", f"{DOCS_BASE}/{doc_id}:batchUpdate", body={"requests": requests})
    print(f"Inserted heading '{heading}' + {len(body_text)} chars of body text")

def export_pdf(doc_id, local_path):
    url = f"{DRIVE_BASE}/files/{doc_id}/export?mimeType=application/pdf"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {get_token()}"})
    with urllib.request.urlopen(req) as r, open(local_path, "wb") as out:
        out.write(r.read())
    print(f"Exported to: {local_path}")

def doc_info(doc_id):
    doc = api("GET", f"{DOCS_BASE}/{doc_id}", params={"fields": "title,documentId,revisionId"})
    drive_meta = api("GET", f"{DRIVE_BASE}/files/{doc_id}",
                     params={"fields": "name,createdTime,modifiedTime,owners,webViewLink"})
    print(f"Title: {doc.get('title')}")
    print(f"ID: {doc.get('documentId')}")
    print(f"Revision: {doc.get('revisionId')}")
    print(f"Created: {drive_meta.get('createdTime','?')[:10]}")
    print(f"Modified: {drive_meta.get('modifiedTime','?')[:10]}")
    print(f"URL: {drive_meta.get('webViewLink','?')}")

def whoami():
    req = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/about?fields=user",
        headers={"Authorization": f"Bearer {get_token()}"}
    )
    about = json.loads(urllib.request.urlopen(req).read())
    u = about["user"]
    print(f"Authenticated as: {u['displayName']} ({u['emailAddress']})")

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "read":
        if len(args) < 2: print("Usage: docs-api.py read DOC_ID"); sys.exit(1)
        read_doc(args[1])
    elif cmd == "create":
        if len(args) < 2: print("Usage: docs-api.py create 'Title' [FOLDER_ID]"); sys.exit(1)
        create_doc(args[1], args[2] if len(args) > 2 else None)
    elif cmd == "insert":
        if len(args) < 4: print("Usage: docs-api.py insert DOC_ID 'heading' 'body'"); sys.exit(1)
        insert_content(args[1], args[2], args[3])
    elif cmd == "export":
        if len(args) < 3: print("Usage: docs-api.py export DOC_ID /local/out.pdf"); sys.exit(1)
        export_pdf(args[1], args[2])
    elif cmd == "info":
        if len(args) < 2: print("Usage: docs-api.py info DOC_ID"); sys.exit(1)
        doc_info(args[1])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
