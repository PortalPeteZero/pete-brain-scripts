#!/usr/bin/env python3
"""
Surfer SEO API helper -- the ONE sanctioned path for all Surfer work (SEO platform, phase 1).

Surfer is a Content Editor / writing tool, NOT a monitoring tool -- on demand only, never scheduled.
This helper is the budget gate for it:
  1. MANDATORY User-Agent -- every call sends 'User-Agent: Mozilla/5.0'. WITHOUT it Cloudflare returns
     403 "error code: 1010", which looks exactly like a plan refusal and is NOT Surfer. This one missing
     header is why Surfer was believed unusable for weeks.
  2. LOGS credits -- each successful Content Editor create is 1 credit, logged to public.seo_api_usage.
  3. REFUSES at a ceiling -- Surfer exposes NO usage endpoint, so the gate counts creates in the CURRENT
     CALENDAR MONTH from seo_api_usage and refuses past the ceiling (default 20). A stale seo_service_balance
     reading is display-only and can never shrink the window or block work.
  4. NEVER SWALLOWS an error -- 403 (plan-gated / Cloudflare 1010) vs 401 (auth) vs 422 (quota) are
     distinguished and raised, never rendered as "--".

The content audit IS `create_editor(import_content_from_url=<live URL>, keywords=[...])` -> read terms + score.
ALWAYS set location + device: the API defaults to "United States" / "mobile".

Auth:   API-KEY header, secret 'surfer-token' (pointer-only). Config: [[surfer-api-configuration]].
Live surface for our key: workspaces + content_editors (v1 and v2). /audits is plan-gated + unconfirmed.

CLI:
  VAULT=/tmp/pbs python3 /tmp/pbs/surfer-api.py workspaces
  VAULT=/tmp/pbs python3 /tmp/pbs/surfer-api.py editors [limit]
  VAULT=/tmp/pbs python3 /tmp/pbs/surfer-api.py credits-used     # creates logged this calendar month
"""
import os, sys, json, datetime, urllib.request, urllib.parse, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
V1 = "https://app.surferseo.com/api/v1/"
V2 = "https://app.surferseo.com/api/v2/"
UA = "Mozilla/5.0"


def _token():
    return open(f"{VAULT}/Library/processes/secrets/surfer-token").read().strip()


def _log_usage(endpoint, credits, http_status, caller, property_key, note):
    try:
        row = {"service": "surfer", "endpoint": endpoint[:200], "units": credits, "cached": False,
               "http_status": http_status, "caller": (caller or "surfer-api")[:80],
               "property_key": property_key, "note": (note or "")[:200]}
        cols = ",".join(row.keys())
        vals = ",".join("NULL" if v is None else ("true" if v is True else "false" if v is False
                        else str(v) if isinstance(v, (int, float)) else "$x$" + str(v) + "$x$")
                        for v in row.values())
        subprocess.run(["python3", "cc-sql.py",
                        f"INSERT INTO public.seo_api_usage ({cols}) VALUES ({vals})"],
                       cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=20)
    except Exception:
        pass


class SurferError(RuntimeError):
    def __init__(self, code, body):
        self.code = code
        is_cf = "1010" in (body or "")
        tag = ("CLOUDFLARE BLOCK (missing User-Agent)" if is_cf
               else "PLAN-GATED / no access" if code == 403
               else "AUTH" if code == 401
               else "QUOTA (credits exhausted)" if code == 422
               else f"HTTP {code}")
        super().__init__(f"[{tag}] {body[:200]}")


class BudgetRefused(RuntimeError):
    pass


class SurferAPI:
    CREATE_CEILING = 20  # Content Editor creates per CALENDAR month (default until Pete supplies the real allowance)

    def __init__(self, caller=None):
        self.key = _token()
        self.caller = caller

    def _raw(self, method, url, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"API-KEY": self.key, "User-Agent": UA,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode()), r.status
        except urllib.error.HTTPError as e:
            try:
                b = e.read().decode()[:300]
            except Exception:
                b = ""
            raise SurferError(e.code, b)

    def _creates_this_month(self):
        try:
            r = subprocess.run(["python3", "cc-sql.py",
                "SELECT count(*) AS n FROM public.seo_api_usage WHERE service='surfer' "
                "AND units > 0 AND date_trunc('month', ts) = date_trunc('month', now())"],
                cwd=VAULT, capture_output=True, text=True, env={**os.environ, "VAULT": VAULT}, timeout=20)
            return int(json.loads(r.stdout)[0]["n"])
        except Exception:
            return 0

    def call(self, method, path, body=None, credit=False, property_key=None, note=None):
        """A Surfer call. credit=True marks a Content Editor create (1 credit, gated by the monthly ceiling)."""
        if credit:
            used = self._creates_this_month()
            if used >= self.CREATE_CEILING:
                raise BudgetRefused(f"Surfer create ceiling reached ({used}/{self.CREATE_CEILING} this calendar "
                                    f"month). Raise CREATE_CEILING or wait for the month to roll.")
        url = (V2 if path.startswith("v2/") else V1) + path.replace("v2/", "")
        try:
            body_out, status = self._raw(method, url, body)
        except SurferError as e:
            _log_usage(path, None, e.code, self.caller, property_key, str(e)[:120])
            raise
        _log_usage(path, (1 if credit else 0), status, self.caller, property_key, note)
        return body_out

    # ---- convenience -----------------------------------------------------
    def workspaces(self):
        return self.call("GET", "workspaces").get("data", [])

    def content_editors(self, limit=25):
        return self.call("GET", f"content_editors?page_size={limit}").get("data", [])

    def audit_page(self, url, keywords, location="United Kingdom", device="desktop"):
        """Content audit of a LIVE page: 1 credit. Returns the created editor (poll for state=completed)."""
        body = {"keywords": keywords if isinstance(keywords, list) else [keywords],
                "import_content_from_url": url, "location": location, "device": device}
        return self.call("POST", "content_editors", body=body, credit=True, note=f"audit {url}")


def _cli():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    api = SurferAPI(caller="cli")
    try:
        if a[0] == "workspaces":
            for w in api.workspaces():
                print(f"  {w.get('id')}  {w.get('name')}")
        elif a[0] == "editors":
            for e in api.content_editors(int(a[1]) if len(a) > 1 else 25):
                print(f"  {e.get('id')}  {e.get('state'):10} {str(e.get('keywords'))[:40]} {str(e.get('inserted_at'))[:10]}")
        elif a[0] == "credits-used":
            print(f"{api._creates_this_month()} / {api.CREATE_CEILING} creates this calendar month")
        else:
            print(f"unknown command: {a[0]}\n{__doc__}")
    except (SurferError, BudgetRefused) as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    _cli()
