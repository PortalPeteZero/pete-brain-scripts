#!/usr/bin/env python3
"""recraft-api.py -- Pete's vector (SVG) logo/icon generator via Recraft AI.

THE home for AI VECTOR image generation. Where image-gen-api.py (grok/nano)
makes raster/photographic images, THIS makes flat, scalable VECTOR art (true
SVG) -- logo marks, icons, badges, simple illustrations that must print clean
at any size. Reach for this whenever a task needs a vector/SVG logo or icon,
or "make it a vector". Do not hand-roll Recraft calls or install a third-party
skill.

Auth: Library/processes/secrets/recraft-api-key (Bearer). Pay-as-you-go credits.
Endpoint: POST https://external.api.recraft.ai/v1/images/generations
  body {prompt, style, size} -> data[].url (temp signed URL; download NOW).
  style=vector_illustration returns real image/svg+xml.

Styles (--style):
  vector  -> vector_illustration  (DEFAULT; true SVG, flat scalable art)
  icon    -> icon                 (SVG icon/pictogram)
  raster  -> realistic_image      (PNG; prefer image-gen-api.py for photoreal)
  digital -> digital_illustration (PNG stylised)

Usage
  recraft-api.py --prompt "..." [--style vector|icon|raster|digital]
                 [--out out.svg] [--size 1024x1024] [--substyle NAME] [--json]
Examples
  recraft-api.py --prompt "bold letter X gym logo mark, two-tone blue and black, flat" --out finchy.svg
  recraft-api.py --prompt "minimal dumbbell icon, single colour" --style icon --out dumbbell.svg
Notes
  * vector styles cost more credits than raster; check remaining credits in
    the JSON output ("credits").
  * a temp signed URL is downloaded immediately to --out (default: recraft-<id>.svg).
"""
import os, sys, json, argparse, urllib.request, urllib.error

_VAULT = os.environ.get("VAULT")
_SECRETS = (os.path.join(_VAULT, "Library/processes/secrets")
            if _VAULT else os.path.expanduser("~/.config/pete-secrets"))

_STYLE_MAP = {
    "vector":  "vector_illustration",
    "icon":    "icon",
    "raster":  "realistic_image",
    "digital": "digital_illustration",
}
_ENDPOINT = "https://external.api.recraft.ai/v1/images/generations"


def _cc_secret(name):
    p = os.path.join(_SECRETS, name)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return f.read().strip()


def _http(url, data=None, headers=None, method="POST", timeout=180):
    h = {"User-Agent": "Mozilla/5.0 (pete-cc recraft)"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def generate(prompt, out, style="vector", size="1024x1024", substyle=None):
    key = _cc_secret("recraft-api-key")
    if not key:
        raise RuntimeError("recraft-api-key not found in secrets")
    recraft_style = _STYLE_MAP.get(style, "vector_illustration")
    payload = {"prompt": prompt, "style": recraft_style, "size": size}
    if substyle:
        payload["substyle"] = substyle
    body = json.dumps(payload).encode()
    raw = _http(_ENDPOINT, data=body,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"})
    resp = json.loads(raw)
    url = resp["data"][0]["url"]
    img = _http(url, method="GET", timeout=120)
    with open(out, "wb") as f:
        f.write(img)
    return {"out": out, "bytes": len(img), "credits": resp.get("credits"),
            "style": recraft_style, "source_url": url}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--style", default="vector",
                    choices=list(_STYLE_MAP.keys()))
    ap.add_argument("--out")
    ap.add_argument("--size", default="1024x1024")
    ap.add_argument("--substyle")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    ext = "svg" if a.style in ("vector", "icon") else "png"
    out = a.out or f"recraft-{a.style}.{ext}"
    try:
        res = generate(a.prompt, out, a.style, a.size, a.substyle)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"ERROR: Recraft HTTP {e.code}: {e.read()[:400].decode(errors='replace')}\n")
        sys.exit(2)
    except Exception as e:
        sys.stderr.write(f"ERROR: {e}\n")
        sys.exit(2)
    if a.json:
        print(json.dumps(res))
    else:
        print(f"OK [recraft/{res['style']}] -> {res['out']}  ({res['bytes']} bytes, {res['credits']} credits left)")


if __name__ == "__main__":
    main()
