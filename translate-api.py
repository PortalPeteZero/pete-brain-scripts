#!/usr/bin/env python3
"""
translate-api.py -- Google Cloud Translation API helper
Auth: service account JWT (no DWD needed -- Cloud API, not Workspace)
Usage:
  python3 translate-api.py translate TEXT TARGET_LANG      # translate to language (e.g. en, es, fr)
  python3 translate-api.py detect TEXT                     # detect language
  python3 translate-api.py languages                       # list supported languages
  python3 translate-api.py file /path/to/file.txt TARGET   # translate a text file
  python3 translate-api.py whoami                          # show auth info
"""

import json, time, base64, urllib.request, urllib.parse, urllib.error
import tempfile, os, subprocess, sys

KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
SCOPE = "https://www.googleapis.com/auth/cloud-translation"
BASE = "https://translation.googleapis.com/language/translate/v2"

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
        "iss": creds["client_email"], "scope": SCOPE,  # no sub -- SA direct
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

def api(path, body):
    url = BASE + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
          headers={"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

def translate(text, target):
    resp = api("", {"q": text, "target": target, "format": "text"})
    t = resp["data"]["translations"][0]
    detected = t.get("detectedSourceLanguage", "?")
    print(f"[{detected} → {target}] {t['translatedText']}")

def detect(text):
    resp = api("/detect", {"q": text})
    d = resp["data"]["detections"][0][0]
    print(f"Language: {d['language']} (confidence: {d['confidence']:.0%})")

def list_languages():
    resp = api("/languages", {"target": "en"})
    langs = resp["data"]["languages"]
    print(f"Supported languages ({len(langs)}):")
    for l in langs:
        print(f"  {l['language']:<8} {l.get('name','')}")

def translate_file(path, target):
    with open(path) as f:
        text = f.read()
    # Split into chunks of 5000 chars (API limit per request)
    chunks = [text[i:i+5000] for i in range(0, len(text), 5000)]
    translated = []
    for chunk in chunks:
        resp = api("", {"q": chunk, "target": target, "format": "text"})
        translated.append(resp["data"]["translations"][0]["translatedText"])
    result = "".join(translated)
    out_path = path.rsplit(".", 1)[0] + f"_{target}." + (path.rsplit(".", 1)[1] if "." in path else "txt")
    with open(out_path, "w") as f:
        f.write(result)
    print(f"Translated {len(text)} chars → {out_path}")

def whoami():
    print(f"Service account: {creds['client_email']}")
    print(f"Project: {creds['project_id']}")

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "translate":
        if len(args) < 3: print("Usage: translate-api.py translate TEXT TARGET"); sys.exit(1)
        translate(args[1], args[2])
    elif cmd == "detect":
        if len(args) < 2: print("Usage: translate-api.py detect TEXT"); sys.exit(1)
        detect(args[1])
    elif cmd == "languages":
        list_languages()
    elif cmd == "file":
        if len(args) < 3: print("Usage: translate-api.py file /path/to/file.txt TARGET"); sys.exit(1)
        translate_file(args[1], args[2])
    elif cmd == "whoami":
        whoami()
    else:
        print(f"Unknown command: {cmd}"); print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main()
