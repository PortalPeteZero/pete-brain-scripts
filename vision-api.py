#!/usr/bin/env python3
"""
vision-api.py -- Google Cloud Vision API helper.

Auth: service account JWT direct (no DWD impersonation -- Vision doesn't act
on behalf of a user). Uses the same SA credentials as drive-api.py.
Scope: https://www.googleapis.com/auth/cloud-vision

API enabled in GCP project `sygma-seo-tools` from 2 May 2026.
Cost: $1.50 per 1,000 calls per feature, 1,000/month/feature free tier.

WHEN TO USE THIS INSTEAD OF CLAUDE'S OWN IMAGE TOOLS
====================================================
Cloud Vision is purpose-built for image content analysis and is materially
better than Claude's vision at:
  - OCR (reading text from photos, signs, labels, screenshots) -- much higher
    accuracy, returns word-level bounding boxes
  - Object detection with confidence scores + bounding boxes
  - Structured label/category output
  - Landmark recognition
  - Logo / brand recognition
  - Batch image processing (Claude has token costs and rate limits per image)

Claude's vision tools are still better at:
  - Holistic scene description / reasoning
  - Comparing multiple photos / spotting differences
  - Answering open-ended questions about an image
  - Anything that needs context or judgement

Common pattern: use Vision to extract structured data, then have Claude
reason about that data. E.g. Vision OCRs a meter label, Claude figures out
which job that meter belongs to.

When in doubt: if the user wants STRUCTURED output (text, labels, objects,
landmarks), reach for Vision. If they want a DESCRIPTION or JUDGEMENT, use
Claude's own image tools.

Usage:
  # File ID from Drive (auto-downloads):
  python3 vision-api.py labels   1abc...xyz
  python3 vision-api.py ocr      1abc...xyz
  python3 vision-api.py objects  1abc...xyz
  python3 vision-api.py landmark 1abc...xyz
  python3 vision-api.py logo     1abc...xyz
  python3 vision-api.py all      1abc...xyz   # everything

  # Local file path:
  python3 vision-api.py ocr /path/to/image.jpg
  python3 vision-api.py all  /path/to/image.heic

  # JSON output (default is human-readable):
  python3 vision-api.py all FILE_ID --json

Library use:
  from vision_api import analyse, ocr, labels, objects, landmarks, batch
  r = analyse(file_id_or_path, features=['LABEL_DETECTION', 'TEXT_DETECTION'])
  print(r['labels'], r['ocr'], r['ocr_blocks'])

Built: 2 May 2026 to support photo-job matching for the Tom-jobs photo workflow
([[Properties/Canary Detect Mapping]]).
"""

import base64
import json
import os
import sys
import tempfile
import time
import urllib.request
import urllib.parse
import urllib.error
import subprocess

KEY_PATH = (
    os.path.join(os.environ["VAULT"], "Library", "processes", "secrets", "google-seo-service-account.json")
    if os.environ.get("VAULT")                       # $VAULT-aware (post-cutover /tmp/pbs flat layout; matches drive-api.py)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets", "google-seo-service-account.json")
)
VISION_SCOPE = "https://www.googleapis.com/auth/cloud-vision"
VISION_BASE = "https://vision.googleapis.com/v1/images:annotate"

# Drive download fallback when input is a Drive file ID. We import the existing
# drive-api.py for its authenticated download path so we get DWD-impersonation
# (needed because Drive content is a user-data API).
import importlib.util as _ilu
_drive_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drive-api.py")
_drive_spec = _ilu.spec_from_file_location("_drive_api_inner", _drive_path)
_drive = _ilu.module_from_spec(_drive_spec)
_drive_spec.loader.exec_module(_drive)

with open(KEY_PATH) as _f:
    _CREDS = json.load(_f)

_token_cache = {}


def _b64u(d):
    if isinstance(d, str):
        d = d.encode()
    return base64.urlsafe_b64encode(d).decode().rstrip("=")


def get_token():
    """Direct SA-to-Vision token (no DWD)."""
    now = int(time.time())
    if _token_cache.get("exp", 0) > now + 60:
        return _token_cache["tok"]
    h = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}))
    c = _b64u(json.dumps({
        "iss": _CREDS["client_email"],
        "scope": VISION_SCOPE,
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600,
        "iat": now,
    }))
    ts = f"{h}.{c}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(_CREDS["private_key"])
        kf = f.name
    sig = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", kf, "-binary"],
        input=ts.encode(), capture_output=True,
    ).stdout
    os.unlink(kf)
    jwt = f"{ts}.{_b64u(sig)}"
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        }).encode(),
    )
    tok = json.loads(urllib.request.urlopen(req).read())["access_token"]
    _token_cache["tok"] = tok
    _token_cache["exp"] = now + 3600
    return tok


def _is_drive_id(s):
    """Drive file IDs are 25+ chars, no slashes, alnum + dashes/underscores.
    Local paths typically contain '/' or '.' or are existing files."""
    if "/" in s or s.startswith("."):
        return False
    if os.path.exists(s):
        return False
    return len(s) >= 20 and all(c.isalnum() or c in "_-" for c in s)


def _read_image_bytes(file_id_or_path):
    if _is_drive_id(file_id_or_path):
        # Use drive-api's authenticated download path
        req = urllib.request.Request(
            f"https://www.googleapis.com/drive/v3/files/{file_id_or_path}?alt=media&supportsAllDrives=true",
            headers={"Authorization": f"Bearer {_drive.get_token()}"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    else:
        with open(file_id_or_path, "rb") as f:
            return f.read()


# Vision feature shortcuts
FEATURE_MAP = {
    "labels":   [{"type": "LABEL_DETECTION", "maxResults": 15}],
    "ocr":      [{"type": "TEXT_DETECTION"}],
    "objects":  [{"type": "OBJECT_LOCALIZATION", "maxResults": 12}],
    "landmark": [{"type": "LANDMARK_DETECTION", "maxResults": 5}],
    "logo":     [{"type": "LOGO_DETECTION", "maxResults": 5}],
    "all": [
        {"type": "LABEL_DETECTION", "maxResults": 15},
        {"type": "TEXT_DETECTION"},
        {"type": "OBJECT_LOCALIZATION", "maxResults": 12},
        {"type": "LANDMARK_DETECTION", "maxResults": 5},
        {"type": "LOGO_DETECTION", "maxResults": 5},
        {"type": "IMAGE_PROPERTIES"},
    ],
}


def analyse(file_id_or_path, features=None):
    """Run Vision on one image. features=list of feature dicts; default 'all'."""
    img = _read_image_bytes(file_id_or_path)
    feats = features if features else FEATURE_MAP["all"]
    body = {"requests": [{
        "image": {"content": base64.b64encode(img).decode()},
        "features": feats,
    }]}
    req = urllib.request.Request(
        VISION_BASE,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {get_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Vision API failed: {e.code} -- {e.read().decode()[:500]}")
    r = resp["responses"][0]
    if r.get("error"):
        raise RuntimeError(f"Vision API error: {r['error']}")
    return _normalise(r)


def _normalise(r):
    """Flatten Vision's response into a sane dict."""
    return {
        "labels": [
            {"description": l["description"], "score": round(l["score"], 3)}
            for l in r.get("labelAnnotations", [])
        ],
        "ocr": r.get("fullTextAnnotation", {}).get("text", ""),
        "ocr_blocks": [
            {"text": b["description"], "vertices": b.get("boundingPoly", {}).get("vertices", [])}
            for b in r.get("textAnnotations", [])[1:]  # skip [0] which is the full-text aggregate
        ],
        "objects": [
            {"name": o["name"], "score": round(o["score"], 3),
             "vertices": o.get("boundingPoly", {}).get("normalizedVertices", [])}
            for o in r.get("localizedObjectAnnotations", [])
        ],
        "landmarks": [
            {"description": l["description"], "score": round(l["score"], 3),
             "locations": l.get("locations", [])}
            for l in r.get("landmarkAnnotations", [])
        ],
        "logos": [
            {"description": l["description"], "score": round(l["score"], 3)}
            for l in r.get("logoAnnotations", [])
        ],
        "image_properties": r.get("imagePropertiesAnnotation", {}),
        "raw": r,
    }


def ocr(file_id_or_path):
    """Convenience: OCR only. Returns the full extracted text string."""
    return analyse(file_id_or_path, features=FEATURE_MAP["ocr"])["ocr"]


def labels(file_id_or_path):
    """Convenience: labels only. Returns list of {description, score}."""
    return analyse(file_id_or_path, features=FEATURE_MAP["labels"])["labels"]


def objects(file_id_or_path):
    """Convenience: object localisation. Returns list of {name, score, vertices}."""
    return analyse(file_id_or_path, features=FEATURE_MAP["objects"])["objects"]


def landmarks(file_id_or_path):
    """Convenience: landmark detection."""
    return analyse(file_id_or_path, features=FEATURE_MAP["landmark"])["landmarks"]


def batch(file_ids_or_paths, features=None):
    """Batch up to 16 images per Vision request. Returns list-of-results in input order."""
    feats = features if features else FEATURE_MAP["all"]
    chunks = [file_ids_or_paths[i:i + 16] for i in range(0, len(file_ids_or_paths), 16)]
    out = []
    for ch in chunks:
        body = {"requests": []}
        for f in ch:
            img = _read_image_bytes(f)
            body["requests"].append({
                "image": {"content": base64.b64encode(img).decode()},
                "features": feats,
            })
        req = urllib.request.Request(
            VISION_BASE,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {get_token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            resp = json.loads(urllib.request.urlopen(req).read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Vision batch failed: {e.code} -- {e.read().decode()[:500]}")
        for r in resp["responses"]:
            if r.get("error"):
                out.append({"error": r["error"]})
            else:
                out.append(_normalise(r))
    return out


def _print_human(r):
    if r.get("labels"):
        print("Labels:")
        for l in r["labels"][:10]:
            print(f"  {l['score']*100:>5.1f}%  {l['description']}")
    if r.get("objects"):
        print("\nObjects:")
        for o in r["objects"][:8]:
            print(f"  {o['score']*100:>5.1f}%  {o['name']}")
    if r.get("landmarks"):
        print("\nLandmarks:")
        for l in r["landmarks"][:5]:
            print(f"  {l['score']*100:>5.1f}%  {l['description']}")
    if r.get("logos"):
        print("\nLogos:")
        for l in r["logos"][:5]:
            print(f"  {l['score']*100:>5.1f}%  {l['description']}")
    if r.get("ocr"):
        print(f"\nOCR text:")
        print(r["ocr"][:1000])
    if r.get("image_properties", {}).get("dominantColors", {}).get("colors"):
        print("\nDominant colours:")
        for c in r["image_properties"]["dominantColors"]["colors"][:5]:
            cc = c.get("color", {})
            print(f"  {c.get('pixelFraction',0)*100:>4.1f}%  rgb({cc.get('red',0)}, {cc.get('green',0)}, {cc.get('blue',0)})")


def _cli():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    cmd, target = sys.argv[1], sys.argv[2]
    json_out = "--json" in sys.argv
    if cmd not in FEATURE_MAP:
        print(f"Unknown command: {cmd}")
        print("Available: " + ", ".join(FEATURE_MAP.keys()))
        sys.exit(2)
    r = analyse(target, features=FEATURE_MAP[cmd])
    if json_out:
        # Drop raw to keep output tidy
        r2 = {k: v for k, v in r.items() if k != "raw"}
        print(json.dumps(r2, indent=2, ensure_ascii=False))
    else:
        _print_human(r)


if __name__ == "__main__":
    _cli()
