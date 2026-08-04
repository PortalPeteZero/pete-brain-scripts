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


REPORTING_SCOPE = "https://uri.paypal.com/services/reporting/search/read"


def token(scope=REPORTING_SCOPE):
    """A client_credentials token.

    ⚠ The reporting scope MUST be asked for EXPLICITLY. A bare client_credentials request returns 26
    scopes and reporting/search/read is NOT among them, even when 'Transaction search' is ticked and
    saved on the app. That default token 403s on /v1/reporting/*, which looks exactly like a missing
    account entitlement -- on 4 Aug 2026 it cost an hour and nearly sent Pete to PayPal support over
    an account that was fine all along. Pass scope=None only if you want the default set.
    """
    c = load_creds()
    auth = base64.b64encode(f"{c['client_id']}:{c['secret']}".encode()).decode()
    form = {"grant_type": "client_credentials"}
    if scope:
        form["scope"] = scope
    req = urllib.request.Request(f"{BASE}/v1/oauth2/token",
        data=urllib.parse.urlencode(form).encode(),
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


def group_key(t):
    """Which payment a raw row belongs to.

    PayPal returns a FX payment as ~4 rows: the payment itself (T0003), the funding pull (T0300)
    and two conversion legs (T0200). Only the payment carries the merchant; the GBP amount that
    actually left the bank is on a leg. Counting raw rows overstates activity ~4x -- 227 rows for
    July 2026 is 93 payments.

    The legs carry the payment's transaction_id in paypal_reference_id with reference type TXN, so
    that is the grouping key. A row that is not a leg is its own group -- which also means a leg
    whose parent falls outside the query window still becomes a group of its own rather than being
    silently dropped (61 of July's 166 legs were in exactly that position).
    """
    ti = t.get("transaction_info", {}) or {}
    if ti.get("paypal_reference_id_type") == "TXN" and ti.get("paypal_reference_id"):
        return ti["paypal_reference_id"]
    return ti.get("transaction_id")


def payments(txns):
    """Collapse raw rows into one record per real payment."""
    groups = {}
    for t in txns:
        groups.setdefault(group_key(t), []).append(t)
    out = []
    for k, v in groups.items():
        def pick(fn):
            return next((r for r in (fn(t) for t in v) if r), "")
        who = pick(lambda t: ((t.get("payer_info") or {}).get("payer_name") or {}).get("alternate_full_name"))
        what = pick(lambda t: "; ".join(i.get("item_name", "") for i in
                                        ((t.get("cart_info") or {}).get("item_details") or [])))
        if not what:
            what = pick(lambda t: (t["transaction_info"].get("transaction_subject")
                                   or t["transaction_info"].get("transaction_note")))
        # what actually left the bank is the GBP leg; prefer the debit
        gbp = None
        for t in v:
            a = t["transaction_info"].get("transaction_amount") or {}
            if a.get("currency_code") == "GBP" and float(a.get("value", 0)) < 0:
                gbp = abs(float(a["value"])); break
        if gbp is None:
            for t in v:
                a = t["transaction_info"].get("transaction_amount") or {}
                if a.get("currency_code") == "GBP":
                    gbp = abs(float(a.get("value", 0))); break
        orig = next(((t["transaction_info"].get("transaction_amount") or {})
                     for t in v if (t["transaction_info"].get("transaction_amount") or {})
                     .get("currency_code") != "GBP"), None)
        out.append({"id": k,
                    "date": min((t["transaction_info"].get("transaction_initiation_date") or "")[:10] for t in v),
                    "gbp": gbp,
                    "original": f"{orig['value']} {orig['currency_code']}" if orig else None,
                    "who": who, "what": what, "rows": len(v)})
    return sorted(out, key=lambda p: p["date"])


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
        print(f"token OK, expires in {t.get('expires_in')}s, {len(scopes)} scope(s)")
        print("reporting/search/read:", rep[0] if rep else
              "NOT PRESENT -- the scope was requested explicitly and still refused, so this IS an "
              "app/account problem: check 'Transaction search' is ticked on the app at "
              "developer.paypal.com (Apps & Credentials -> Command Centre)")
        return 0 if rep else 1

    if cmd == "balances":
        print(json.dumps(api("/v1/reporting/balances"), indent=1)); return 0

    if cmd == "list":
        if len(args) < 3: _die("usage: list FROM TO [--raw]   (YYYY-MM-DD)")
        txns = list_txns(args[1], args[2])
        if "--raw" in args:                      # every leg, for debugging
            rows = [_row(t) for t in txns]
            if "--json" in args: print(json.dumps(rows, indent=1)); return 0
            print(f"{len(rows)} RAW rows (legs included) {args[1]} .. {args[2]}\n")
            for r in rows:
                print(f"  {r['date']}  {str(r['amount']):>10} {r['currency']}  {r['who'][:30]:32} "
                      f"{(r['subject'] or r['items'])[:40]}")
            return 0
        pays = payments(txns)
        if "--json" in args:
            print(json.dumps(pays, indent=1)); return 0
        print(f"{len(pays)} payments {args[1]} .. {args[2]}  (from {len(txns)} raw rows)\n")
        for p in pays:
            gbp = f"{p['gbp']:.2f}" if p["gbp"] is not None else "?"
            orig = f" ({p['original']})" if p["original"] else ""
            print(f"  {p['date']}  {gbp:>9} GBP{orig:<16}  {p['who'][:28]:30} {p['what'][:38]}")
        return 0

    if cmd == "match":
        # Reconciliation: given bank lines (date + amount), say what each PayPal payment WAS.
        # Bank settlement lags the PayPal payment by a few days, so search backwards from the
        # bank date. Matching is on the GBP amount, which is what hits the statement.
        if len(args) < 2: _die('usage: match FILE.json   [{"date":"YYYY-MM-DD","amount":-123.45}, ...]')
        lag = int(args[args.index("--lag") + 1]) if "--lag" in args else 10
        bank = json.load(open(args[1]))
        lo = min(b["date"] for b in bank); hi = max(b["date"] for b in bank)
        frm = (datetime.date.fromisoformat(lo) - datetime.timedelta(days=lag)).isoformat()
        pays = payments(list_txns(frm, hi))
        D = datetime.date.fromisoformat
        hits = 0
        for b in sorted(bank, key=lambda x: x["date"]):
            amt = abs(float(b["amount"]))
            c = [p for p in pays if p["gbp"] is not None and abs(p["gbp"] - amt) < 0.005
                 and 0 <= (D(b["date"]) - D(p["date"])).days <= lag]
            if c:
                hits += 1
                p = min(c, key=lambda p: (D(b["date"]) - D(p["date"])).days)
                print(f"  {b['date']} {amt:>9.2f}  OK  {p['who'][:28]:30} {p['what'][:34]:36} "
                      f"(paid {p['date']})")
            else:
                print(f"  {b['date']} {amt:>9.2f}  --  no PayPal payment at this amount within {lag} days")
        print(f"\nmatched {hits} of {len(bank)}")
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
