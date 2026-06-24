#!/usr/bin/env python3
"""Stripe API helper — Camello Blanco S.L. account (Canary Detect entity).

Cross-project: any Pete/Camello Blanco project's session calls this for Stripe.
Reads keys from Library/processes/secrets/stripe-camello-blanco.json.
Test mode by default; pass --live to use the live keys (once they exist).

Usage:
  stripe-api.py get  /v1/products
  stripe-api.py get  /v1/products limit=1
  stripe-api.py post /v1/products name="LeakGuard Install"
  stripe-api.py post /v1/prices product=prod_X unit_amount=35000 currency=eur
  stripe-api.py post /v1/prices product=prod_X unit_amount=12000 currency=eur "recurring[interval]=year" tax_behavior=exclusive
  stripe-api.py post /v1/tax_rates display_name=IGIC percentage=7 inclusive=false jurisdiction=ES-CN
  stripe-api.py --live get /v1/account

Params are passed as key=value (form-encoded, Stripe-style). Nested keys work:
  "recurring[interval]=month"   "metadata[founder_only]=true"
Output: pretty JSON. HTTP errors are returned as {"error": {...}} (Stripe's error body).
"""
import sys, json, urllib.request, urllib.parse, urllib.error

SECRETS = "/Users/peterashcroft/Second Brain/Library/processes/secrets/stripe-camello-blanco.json"
BASE = "https://api.stripe.com"


def load_keys(live=False):
    with open(SECRETS) as f:
        cfg = json.load(f)
    block = cfg["live" if live else "test"]
    # Prefer the full secret key (sk_ — needed for admin ops: create products/prices/tax/webhooks);
    # fall back to the restricted key (rk_ — reads + the deployed-function ops). Ignore placeholder text.
    for field in ("secret_key", "restricted_key"):
        k = block.get(field, "")
        if isinstance(k, str) and (k.startswith("sk_") or k.startswith("rk_")):
            return k
    sys.exit(f"No usable {'live' if live else 'test'} key (sk_/rk_) in {SECRETS}")


def stripe(method, path, params=None, live=False):
    sk = load_keys(live)
    url = BASE + path
    body = None
    if method == "GET":
        if params:
            url += "?" + urllib.parse.urlencode(params)
    elif params:
        body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, method=method, data=body, headers={
        "Authorization": f"Bearer {sk}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read())}
        except Exception:
            return {"error": f"HTTP {e.code}"}


def parse_kv(args):
    params = {}
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            params[k] = v
    return params


if __name__ == "__main__":
    args = sys.argv[1:]
    live = "--live" in args
    if live:
        args.remove("--live")
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    method, path = args[0].upper(), args[1]
    params = parse_kv(args[2:]) or None
    print(json.dumps(stripe(method, path, params, live), indent=2))
