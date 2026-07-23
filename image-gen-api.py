#!/usr/bin/env python3
"""image-gen-api.py -- Pete's image generation helper (grok + nano-banana).

THE home for AI image generation. Two providers, one interface. Reach for THIS
whenever a task needs an image made or edited -- do not install third-party
skills or hand-roll API calls.

Providers
  grok  -- xAI grok-imagine-image / grok-imagine-image-quality.
           text->image. Fast, strong photoreal + stylised + character scenes.
           Auth: Library/processes/secrets/xai-api-key (Bearer).
           POST https://api.x.ai/v1/images/generations -> data[].url (temp; download now).
  nano  -- Google Gemini 2.5 Flash Image ("nano banana").
           text->image AND image EDITING. Best for edits, character CONSISTENCY
           across a set, text baked into the image, and compositing/blending.
           Auth: Library/processes/secrets/gemini-api-key (?key=).
           POST generativelanguage.../models/gemini-2.5-flash-image:generateContent
           -> candidates[0].content.parts[].inline_data (b64).

How to choose (--provider auto, the default)
  * --edit <image> given (editing / keep a character consistent / add-remove
    an element / text-in-image)                              -> nano
  * fresh text->image (new scene / poster art / character)   -> grok
  * if the chosen provider's key is absent, fall back to the
    other and note it in the JSON output.

Usage
  image-gen-api.py --prompt "..." [--provider grok|nano|auto] [--out out.png]
                   [--edit input.png] [--model M] [--n 1] [--quality] [--json]
Examples
  image-gen-api.py --prompt "Robin Hood panto character, green tunic, feathered cap, storybook style" --out robin.png
  image-gen-api.py --edit poster.png --prompt "add a fox mascot bottom-left" --out poster2.png   # -> nano
"""
import os, sys, json, argparse, base64, urllib.request, urllib.error

_VAULT = os.environ.get("VAULT")
_SECRETS = (os.path.join(_VAULT, "Library/processes/secrets")
            if _VAULT else os.path.expanduser("~/.config/pete-secrets"))

def _key(name):
    p = os.path.join(_SECRETS, name)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return f.read().strip()

def _http(url, data=None, headers=None, method="POST"):
    h = {"User-Agent": "Mozilla/5.0 (pete-cc image-gen)"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()

# ---- grok (xAI) ----------------------------------------------------------
def gen_grok(prompt, out, model=None, n=1, quality=False, **_):
    key = _key("xai-api-key")
    if not key:
        raise RuntimeError("xai-api-key not found in secrets")
    model = model or ("grok-imagine-image-quality" if quality else "grok-imagine-image")
    body = json.dumps({"model": model, "prompt": prompt, "n": n}).encode()
    resp = json.loads(_http("https://api.x.ai/v1/images/generations", body,
                            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}))
    paths = []
    for i, item in enumerate(resp.get("data", [])):
        img = _http(item["url"], method="GET")  # temp URL -> download immediately
        dest = out if n == 1 else _numbered(out, i)
        with open(dest, "wb") as f:
            f.write(img)
        paths.append(dest)
    return {"provider": "grok", "model": model, "paths": paths}

# ---- nano-banana (Gemini 2.5 Flash Image) --------------------------------
def gen_nano(prompt, out, model=None, edit=None, **_):
    key = _key("gemini-api-key")
    if not key:
        raise RuntimeError("gemini-api-key not found in secrets (nano/banana not configured yet)")
    model = model or "gemini-2.5-flash-image"
    parts = [{"text": prompt}]
    if edit:
        with open(edit, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = "image/png" if edit.lower().endswith(".png") else "image/jpeg"
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({"contents": [{"parts": parts}]}).encode()
    resp = json.loads(_http(url, body, {"Content-Type": "application/json", "X-goog-api-key": key}))
    paths = []
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inline_data") or part.get("inlineData")
            if inline and inline.get("data"):
                with open(out, "wb") as f:
                    f.write(base64.b64decode(inline["data"]))
                paths.append(out)
    if not paths:
        raise RuntimeError("nano returned no image (response: %s)" % json.dumps(resp)[:300])
    return {"provider": "nano", "model": model, "paths": paths}

def _numbered(path, i):
    base, ext = os.path.splitext(path)
    return f"{base}-{i+1}{ext}"

def choose(provider, edit):
    if provider and provider != "auto":
        return provider
    return "nano" if edit else "grok"

def main():
    ap = argparse.ArgumentParser(description="Generate/edit images via grok or nano-banana.")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--provider", choices=["grok", "nano", "auto"], default="auto")
    ap.add_argument("--out", default="/tmp/image-gen-out.png")
    ap.add_argument("--edit", help="input image to edit (forces nano)")
    ap.add_argument("--model")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--quality", action="store_true", help="grok: use the higher-quality model")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    order = [choose(a.provider, a.edit)]
    # Only text-to-image gets a cross-provider fallback. An --edit MUST use nano
    # (grok can't edit); falling an edit back to grok would silently ignore the input image.
    if not a.edit:
        order.append("grok" if order[0] == "nano" else "nano")
    last_err = None
    for prov in order:
        try:
            fn = gen_nano if prov == "nano" else gen_grok
            res = fn(prompt=a.prompt, out=a.out, model=a.model, n=a.n,
                     quality=a.quality, edit=a.edit)
            if prov != order[0]:
                res["note"] = f"{order[0]} unavailable; used {prov}"
            print(json.dumps(res) if a.json else
                  f"OK [{res['provider']}/{res['model']}] -> {', '.join(res['paths'])}"
                  + (f"  ({res['note']})" if res.get("note") else ""))
            return
        except Exception as e:
            last_err = e
    print(f"ERROR: image generation failed: {last_err}", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    main()
