#!/usr/bin/env python3
"""
soldo-api.py -- single canonical path for all Soldo Business API v2 work.

Pattern mirrors gmail-api.py / calendar-api.py / drive-api.py: a class with
methods + a thin CLI wrapper. Replaces ad-hoc fingerprint code that drifted
across past sessions.

Auth:
  OAuth 2.0 client_credentials -> access token (2h TTL).
  Standard auth: Bearer + X-Soldo-Internal-Token headers.
  Advanced auth (for /transactions search etc): adds X-Soldo-Fingerprint
  (SHA-512 hex of documented field-concat, lowercase) and X-Soldo-Fingerprint-Signature
  (RSA-SHA512 of the hex string, base64-encoded).

The "token" field at the end of every fingerprint concat is the static
fingerprint token (= X-Soldo-Internal-Token), NOT the OAuth access token.
Dates in query params: bare YYYY-MM-DD (no T, no time, no offset).

Account: SGMS1077 (Sygma Solutions Ltd). Credentials at
[[Library/processes/soldo-api-configuration]]. Lesson on the cracked scheme:
[[Library/lessons/2026-05-29-soldo-fingerprint-token-is-internal-token]].

Usage (CLI):
  python3 soldo-api.py whoami
  python3 soldo-api.py wallets
  python3 soldo-api.py cards
  python3 soldo-api.py expense-categories
  python3 soldo-api.py expense-review                       # to-review counts per user
  python3 soldo-api.py expense-review-user USER_ID          # one user's tx UUIDs
  python3 soldo-api.py transactions FROM_DATE TO_DATE [DATE_TYPE]
                                                            # FROM/TO are YYYY-MM-DD
                                                            # DATE_TYPE default SETTLEMENT
  python3 soldo-api.py transaction TX_ID                    # single transaction
  python3 soldo-api.py attachments TX_ID                    # list receipts on a txn
  python3 soldo-api.py download-attachment TX_ID ATT_ID PATH  # save receipt to file
  python3 soldo-api.py users                                # list all users
  python3 soldo-api.py statements                           # monthly statement list

Usage (library):
  from soldo_api import SoldoAPI
  s = SoldoAPI()
  txns = s.transactions("2026-05-01", "2026-06-01")
  for t in txns: print(t["id"], t["amount"], t["expense_category"]["name"])
"""
import base64
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ---- credentials (synced to soldo-api-configuration.md) -----------------
CID = "t6BAYDr2rKbL67IrwmAuEzmzkwXwLLZb"
SECRET = "K9p39IxDpoCBgooFGjPuOF9c835rYNcg"
INTERNAL = "4GZN8W6UCE0SL1O25KNV"  # X-Soldo-Internal-Token == fingerprint token
PRIV_KEY_PATH = "/tmp/pbs/Library/processes/secrets/soldo-rsa.private"
BASE = "https://api.soldo.com"

# Fingerprint orders per endpoint (from developer.soldo.com/reference/fingerprint-order)
FINGERPRINT_ORDER = {
    "transactions_search": ["type", "publicId", "customReferenceId", "groupId",
                             "fromDate", "toDate", "dateType",
                             "category", "status", "tagId", "expenseType",
                             "expenseStatus", "text", "token"],
}


class SoldoAPI:
    def __init__(self, priv_key_path=PRIV_KEY_PATH, client_id=CID, client_secret=SECRET,
                 internal_token=INTERNAL):
        self.client_id = client_id
        self.client_secret = client_secret
        self.internal = internal_token
        with open(priv_key_path, "rb") as f:
            self.priv = serialization.load_pem_private_key(f.read(), password=None)
        self._access = None

    # ---- auth -----------------------------------------------------------
    @property
    def access_token(self):
        if self._access is None:
            body = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": self.client_id, "client_secret": self.client_secret,
            }).encode()
            req = urllib.request.Request(f"{BASE}/oauth/authorize", data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
            self._access = resp["access_token"]
        return self._access

    def _sign(self, fingerprint_content: str):
        fp = hashlib.sha512(fingerprint_content.encode()).hexdigest()
        sig = base64.b64encode(
            self.priv.sign(fp.encode(), padding.PKCS1v15(), hashes.SHA512())
        ).decode()
        return fp, sig

    def _headers(self, fingerprint_content: str = None):
        h = {
            "Authorization": f"Bearer {self.access_token}",
            "X-Soldo-Internal-Token": self.internal,
            "accept": "application/json",
        }
        if fingerprint_content is not None:
            fp, sig = self._sign(fingerprint_content)
            h["X-Soldo-Fingerprint"] = fp
            h["X-Soldo-Fingerprint-Signature"] = sig
        return h

    def _get(self, path: str, fingerprint_content: str = None, params: dict = None,
             raw: bool = False):
        url = f"{BASE}{path}"
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers(fingerprint_content),
                                     method="GET")
        resp = urllib.request.urlopen(req, timeout=30).read()
        return resp if raw else json.loads(resp)

    # ---- read-only convenience methods ----------------------------------
    def whoami(self):
        # Note: /ping/whoami still requires the fingerprint header to be present
        # even though field content is just the token. Pass token-only content.
        return self._get("/business/v2/ping/whoami", fingerprint_content=self.internal)

    def wallets(self):
        return self._get("/business/v2/wallets")

    def cards(self):
        return self._get("/business/v2/cards")

    def expense_categories(self, page_size=200):
        return self._get(f"/business/v2/entities/expense-category?p=0&s={page_size}")

    def expense_review(self):
        return self._get("/business/v2/expense-review")

    def expense_review_user(self, user_id: str):
        return self._get(f"/business/v2/expense-review/{user_id}")

    def transactions(self, from_date: str, to_date: str, date_type: str = "SETTLEMENT",
                     page_size: int = 100, **filters):
        """Paginated /transactions search. Returns all results across all pages.

        from_date/to_date: bare YYYY-MM-DD strings. date_type one of:
        TRANSACTION / SETTLEMENT / UPDATE / REVIEW_TIME / CUSTOM_EXPORT_TIME.

        Extra filters: type, publicId, customReferenceId, groupId, category,
        status, tagId, expenseType, expenseStatus, text. Each contributes to
        the fingerprint concat in documented order.
        """
        order = FINGERPRINT_ORDER["transactions_search"]
        # Build values in documented order
        values = {
            "type": filters.get("type", ""),
            "publicId": filters.get("publicId", ""),
            "customReferenceId": filters.get("customReferenceId", ""),
            "groupId": filters.get("groupId", ""),
            "fromDate": from_date,
            "toDate": to_date,
            "dateType": date_type,
            "category": filters.get("category", ""),
            "status": filters.get("status", ""),
            "tagId": filters.get("tagId", ""),
            "expenseType": filters.get("expenseType", ""),
            "expenseStatus": filters.get("expenseStatus", ""),
            "text": filters.get("text", ""),
            "token": self.internal,
        }
        content = "".join(values[k] for k in order)
        # Query params: only non-empty values + page/size
        query_params = {k: v for k, v in values.items() if v and k != "token"}

        all_results = []
        page = 0
        while True:
            params = {**query_params, "p": page, "s": page_size}
            res = self._get("/business/v2/transactions",
                            fingerprint_content=content, params=params)
            all_results.extend(res.get("results", []))
            if page + 1 >= res.get("pages", 0):
                break
            page += 1
        return all_results

    def transaction(self, tx_id: str):
        # /transactions/{id} fingerprint order: id, token (per developer docs)
        content = tx_id + self.internal
        return self._get(f"/business/v2/transactions/{tx_id}",
                         fingerprint_content=content)

    def attachments(self, tx_id: str):
        """List attachments. Response shape: {"attachments": [{attachment_id, file_name, file_extension, file_size, url, url_type}, ...]}.

        Each attachment dict carries a pre-signed S3 `url` (valid ~2 hours) that you can download
        directly with urllib — no further authentication needed. Use this in preference to the
        download endpoint, which adds nothing.
        """
        return self._get(f"/business/v2/transactions/{tx_id}/attachments")

    def download_attachment(self, tx_id: str, att, dest_path: str):
        """Download an attachment to dest_path.

        Accepts either a string attachment_id or the full attachment dict from `attachments()`.
        Prefers the pre-signed `url` field when available (avoids re-authenticating); falls back
        to the /download endpoint otherwise.
        """
        if isinstance(att, dict) and att.get("url"):
            import urllib.request
            with urllib.request.urlopen(att["url"], timeout=30) as resp:
                data = resp.read()
        else:
            att_id = att if isinstance(att, str) else (att.get("attachment_id") or att.get("id"))
            data = self._get(
                f"/business/v2/transactions/{tx_id}/attachments/{att_id}/download",
                raw=True,
            )
        with open(dest_path, "wb") as f:
            f.write(data)
        return dest_path

    def users(self, page_size: int = 200):
        return self._get(f"/business/v2/users?p=0&s={page_size}")

    def statements(self, page_size: int = 50):
        return self._get(f"/business/v2/statements?p=0&s={page_size}")


# ---- CLI ------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    s = SoldoAPI()
    cmd = args[0]
    try:
        if cmd == "whoami":
            print(json.dumps(s.whoami(), indent=2))
        elif cmd == "wallets":
            print(json.dumps(s.wallets(), indent=2))
        elif cmd == "cards":
            print(json.dumps(s.cards(), indent=2))
        elif cmd == "expense-categories":
            print(json.dumps(s.expense_categories(), indent=2))
        elif cmd == "expense-review":
            print(json.dumps(s.expense_review(), indent=2))
        elif cmd == "expense-review-user":
            print(json.dumps(s.expense_review_user(args[1]), indent=2))
        elif cmd == "transactions":
            from_d, to_d = args[1], args[2]
            dt = args[3] if len(args) > 3 else "SETTLEMENT"
            txns = s.transactions(from_d, to_d, dt)
            print(json.dumps(txns, indent=2))
        elif cmd == "transaction":
            print(json.dumps(s.transaction(args[1]), indent=2))
        elif cmd == "attachments":
            print(json.dumps(s.attachments(args[1]), indent=2))
        elif cmd == "download-attachment":
            tx, att, path = args[1], args[2], args[3]
            print(s.download_attachment(tx, att, path))
        elif cmd == "users":
            print(json.dumps(s.users(), indent=2))
        elif cmd == "statements":
            print(json.dumps(s.statements(), indent=2))
        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)
            sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
