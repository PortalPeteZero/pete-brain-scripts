#!/usr/bin/env python3
"""Stripe API helper — the Camello Blanco S.L. Stripe accounts (Canary Detect entity).

TWO accounts live under Camello Blanco. Pick with --account; the default is `leakguard`, which is
what every pre-existing caller means:

  leakguard  acct_1TaXNS7NHUwYrNfo  the ORIGINAL account, Stripe SSOT for LeakGuard / Canary Detect
                                    billing — the same account the LeakGuard CRM edge functions
                                    (stripe-webhook, stripe-checkout-session, mark-paid-bacs, founder
                                    subs) transact on. Secret: stripe-camello-blanco.json.
                                    Live since the 24 May 2026 cutover.
  odoo       acct_1TyDyr955lH6hyUF  the DEDICATED account for card payments on Odoo invoices/quotes,
                                    created + activated 28 Jul 2026. Secret: stripe-canary-detect.json.
                                    Live-only (its test keys exist but nothing runs on them).

The helper defaults to TEST mode for ad-hoc safety; pass --live for live data. Any HTTP method works
(get/post/delete), so it can read AND write live: e.g. cancel a subscription with
`--live delete /v1/subscriptions/<id>` (done 9 Jul 2026 to cancel a test customer's live sub).

Usage:
  stripe-api.py get  /v1/products
  stripe-api.py --live get /v1/subscriptions/sub_XXX               # read a live subscription
  stripe-api.py --live delete /v1/subscriptions/sub_XXX            # cancel a live subscription now
  stripe-api.py --live --account odoo get /v1/balance              # the Odoo account's balance
  stripe-api.py --live --account odoo get /v1/charges limit=10
  stripe-api.py post /v1/products name="LeakGuard Install"
  stripe-api.py post /v1/prices product=prod_X unit_amount=35000 currency=eur
  stripe-api.py post /v1/tax_rates display_name=IGIC percentage=7 inclusive=false jurisdiction=ES-CN

Params are passed as key=value (form-encoded, Stripe-style). Nested keys work:
  "recurring[interval]=month"   "metadata[founder_only]=true"
Output: pretty JSON. HTTP errors are returned as {"error": {...}} (Stripe's error body).
"""
import os, sys, json, urllib.request, urllib.parse, urllib.error

# $VAULT-aware: the boot kernel sets VAULT=/tmp/pbs locally, railway-bootstrap sets it to the repo
# root inside a cron container. Hardcoding /tmp/pbs made this helper unusable on Railway (caught
# 29 Jul 2026 when stripe-weekly-report's first cloud run died on a missing secrets path).
SECRETS_DIR = os.path.join(os.environ.get("VAULT", "/tmp/pbs"), "Library", "processes", "secrets")
BASE = "https://api.stripe.com"

# slug -> (secret filename, key layout). "nested" = {live|test: {secret_key, restricted_key}};
# "flat" = {secret_key (live), test_secret_key}. The two files were authored 2 months apart and
# genuinely differ in shape, so the layout is declared rather than sniffed.
ACCOUNTS = {
    "leakguard": ("stripe-camello-blanco.json", "nested", "acct_1TaXNS7NHUwYrNfo"),
    "odoo":      ("stripe-canary-detect.json",  "flat",   "acct_1TyDyr955lH6hyUF"),
}
# Back-compat alias: the original account was known only as "camello-blanco" before the split.
ACCOUNTS["camello-blanco"] = ACCOUNTS["leakguard"]
DEFAULT_ACCOUNT = "leakguard"

# Kept as a module constant because callers referenced it before the two-account split.
SECRETS = f"{SECRETS_DIR}/{ACCOUNTS[DEFAULT_ACCOUNT][0]}"


def load_keys(live=False, account=DEFAULT_ACCOUNT):
    if account not in ACCOUNTS:
        sys.exit(f"Unknown --account {account!r}. Known: {', '.join(sorted(ACCOUNTS))}")
    fname, layout, _ = ACCOUNTS[account]
    path = f"{SECRETS_DIR}/{fname}"
    with open(path) as f:
        cfg = json.load(f)
    if layout == "nested":
        block = cfg["live" if live else "test"]
        # Prefer the full secret key (sk_ — needed for admin ops: create products/prices/tax/webhooks);
        # fall back to the restricted key (rk_ — reads + the deployed-function ops).
        fields = ("secret_key", "restricted_key")
    else:
        block = cfg
        fields = ("secret_key",) if live else ("test_secret_key",)
    for field in fields:
        k = block.get(field, "")
        if isinstance(k, str) and (k.startswith("sk_") or k.startswith("rk_")):
            return k
    sys.exit(f"No usable {'live' if live else 'test'} key (sk_/rk_) in {path}")


def stripe(method, path, params=None, live=False, account=DEFAULT_ACCOUNT):
    sk = load_keys(live, account)
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
    account = DEFAULT_ACCOUNT
    if "--account" in args:
        i = args.index("--account")
        if i + 1 >= len(args):
            sys.exit("--account needs a value: " + ", ".join(sorted(ACCOUNTS)))
        account = args[i + 1]
        del args[i:i + 2]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    method, path = args[0].upper(), args[1]
    params = parse_kv(args[2:]) or None
    print(json.dumps(stripe(method, path, params, live, account), indent=2))
