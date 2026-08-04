#!/usr/bin/env python3
"""
paypal-api.py -- PayPal REST API helper (Sygma Solutions, LIVE).

what: Reads PayPal transaction history so a session can say what a PayPal payment actually WAS,
      instead of Pete sitting with PayPal open in another window during bank reconciliation.
why:  PayPal is used actively but is NOT a bank account in Xero, so the spend is invisible in the
      books until it lands on a card or the bank. Built 4 Aug 2026.

Auth: OAuth2 client_credentials. Credentials live in the CC secrets store as
      `paypal-credentials.json` -- never on local disk. Tokens are short-lived (~8h) and are NOT
      cached anywhere; a fresh one is fetched per run, which is cheap.

LIMITS that will bite you if you forget them (PayPal's, not ours):
  * Transaction Search only goes back 3 YEARS.
  * A single query window is max 31 DAYS -- longer ranges are chunked automatically by list().
  * A transaction can take up to 3 HOURS to appear.
  * The `reporting/search/read` scope must be enabled on the app (Apps & Credentials -> the app ->
    Transaction search) AND it propagates on a delay -- it was still absent 2.5 minutes after being
    ticked on 4 Aug 2026. If you get 403 NOT_AUTHORIZED, check the scope is on the token first
    (`whoami`) before assuming the query is wrong.

Usage:
  python3 paypal-api.py whoami                          # token + whether reporting scope is present
  python3 paypal-api.py list FROM TO [--json]           # transactions between two YYYY-MM-DD dates
  python3 paypal-api.py find AMOUNT [--around YYYY-MM-DD] [--days N]
                                                        # what was the payment for this amount?
  python3 paypal-api.py balances                        # current PayPal balances
"""
import sys, os, json, base64, datetime, subprocess
import urllib.request, urllib.parse, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
BASE  = "https://api-m.paypal.com"
SECRET_NAME = "paypal-credentials.json"


def _die(msg, code=1):
    print(msg, file=sys.stderr); sys.exit(code)


def load_creds():
    """From the CC secrets store. Deliberately not from a local file -- see the anti-regression rule."""
    r = subprocess.run([sys.executable, os.path.join(VAULT, "cc-sql.py"),
                        f"SELECT value FROM secrets WHERE name='{SECRET_NAME}'"],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    if not r.stdout.strip().startswith("["):
        _die(f"paypal-api: could not read {SECRET_NAME} from the CC secrets store:\n{r.stdout}{r.stderr}")
    rows = json.loads(r.stdout)
    if not rows:
        _die(f"paypal-api: secret {SECRET_NAME} is not in the CC secrets store.")
    return json.loads(rows[0]["value"])


def token():
    c = load_creds()
    auth = base64.b64encode(f"{c['client_id']}:{c['secret']}".encode()).decode()
    req = urllib.request.Request(f"{BASE}/v1/oauth2/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        _die(f"paypal-api: auth failed {e.code} {e.read().decode()[:300]}")


def api(path, params=None, tok=None):
    tok = tok or token()["access_token"]
    url = f"{BASE}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}",
                                               "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:400]
        if e.code == 403:
            body += ("\n  -> 403 usually means the reporting scope is not on the token. Run "
                     "`paypal-api.py whoami` to check before debugging the query.")
        _die(f"paypal-api: {path} failed {e.code} {body}")


def _windows(frm, to):
    """PayPal caps a query at 31 days, so walk the range in chunks rather than failing at 32."""
    f = datetime.date.fromisoformat(frm); t = datetime.date.fromisoformat(to)
    while f <= t:
        end = min(f + datetime.timedelta(days=30), t)
        yield f.isoformat(), end.isoformat()
        f = end + datetime.timedelta(days=1)


def list_txns(frm, to):
    tok = token()["access_token"]
    out = []
    for a, b in _windows(frm, to):
        page = 1
        while True:
            d = api("/v1/reporting/transactions", {
                "start_date": f"{a}T00:00:00-0000", "end_date": f"{b}T23:59:59-0000",
                "fields": "transaction_info,payer_info,cart_info",
                "page_size": 500, "page": page}, tok=tok)
            out.extend(d.get("transaction_details", []))
            if page >= int(d.get("total_pages") or 1):
                break
            page += 1
    return out


def _row(t):
    ti = t.get("transaction_info", {}) or {}
    pi = t.get("payer_info", {}) or {}
    ci = t.get("cart_info", {}) or {}
    amt = ti.get("transaction_amount") or {}
    items = "; ".join(filter(None, [i.get("item_name") for i in (ci.get("item_details") or [])]))
    who = ((pi.get("payer_name") or {}).get("alternate_full_name")
           or pi.get("email_address") or "")
    return {"date": (ti.get("transaction_initiation_date") or "")[:10],
            "amount": amt.get("value"), "currency": amt.get("currency_code"),
            "who": who, "status": ti.get("transaction_status"),
            "subject": ti.get("transaction_subject") or ti.get("transaction_note") or "",
            "items": items, "id": ti.get("transaction_id")}


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__); return 0
    cmd = args[0]

    if cmd == "whoami":
        t = token()
        scopes = t.get("scope", "").split()
        rep = [s for s in scopes if "reporting" in s]
        print(f"token OK, expires in {t.get('expires_in')}s, {len(scopes)} scopes")
        print("reporting/search/read:", rep[0] if rep else
              "NOT PRESENT -- tick 'Transaction search' on the app at developer.paypal.com "
              "(Apps & Credentials -> Command Centre) and allow time to propagate")
        return 0 if rep else 1

    if cmd == "balances":
        print(json.dumps(api("/v1/reporting/balances"), indent=1)); return 0

    if cmd == "list":
        if len(args) < 3: _die("usage: list FROM TO   (YYYY-MM-DD)")
        rows = [_row(t) for t in list_txns(args[1], args[2])]
        if "--json" in args:
            print(json.dumps(rows, indent=1)); return 0
        print(f"{len(rows)} transactions {args[1]} .. {args[2]}\n")
        for r in rows:
            print(f"  {r['date']}  {str(r['amount']):>10} {r['currency']}  {r['who'][:30]:32} "
                  f"{(r['subject'] or r['items'])[:44]}")
        return 0

    if cmd == "find":
        if len(args) < 2: _die("usage: find AMOUNT [--around YYYY-MM-DD] [--days N]")
        target = abs(float(args[1]))
        around = None; days = 45
        if "--around" in args: around = args[args.index("--around") + 1]
        if "--days" in args:   days = int(args[args.index("--days") + 1])
        centre = datetime.date.fromisoformat(around) if around else datetime.date.today()
        frm = (centre - datetime.timedelta(days=days)).isoformat()
        to  = min(centre + datetime.timedelta(days=days), datetime.date.today()).isoformat()
        hits = [r for r in (_row(t) for t in list_txns(frm, to))
                if r["amount"] and abs(abs(float(r["amount"])) - target) < 0.005]
        if not hits:
            print(f"no PayPal transaction for {target} between {frm} and {to}. "
                  "Widen with --days, or remember Transaction Search only reaches back 3 years.")
            return 1
        for r in hits:
            print(f"  {r['date']}  {r['amount']} {r['currency']}  {r['who']}")
            if r["subject"]: print(f"     subject: {r['subject']}")
            if r["items"]:   print(f"     items  : {r['items']}")
            print(f"     status : {r['status']}  id {r['id']}")
        return 0

    _die(f"unknown command: {cmd}\n{__doc__}", 2)


if __name__ == "__main__":
    sys.exit(main())
