#!/usr/bin/env python3
"""
Ahrefs API v3 helper -- the ONE sanctioned path for all Ahrefs work (SEO platform, phase 1).

This helper is the budget GATE. It is the only layer allowed to call the Ahrefs paid API, so it:
  1. CACHES -- immutable past-date rows are read from the CC store, never re-bought (see seo_rank_daily).
  2. LOGS COST -- every call records its real unit cost (x-api-units-cost-total-actual header) to
     public.seo_api_usage, so spend is always attributable.
  3. REFUSES at a threshold -- metered calls stop when units run out (management/* is UNMETERED and
     always passes, so project state can be re-read even at quota).
  4. NEVER SWALLOWS an error -- a 400/401/403/404 is raised with its real reason, never a silent "--".

Cost model (docs.ahrefs.com/api/docs/limits-consumption):
  units = max(50, per_row_cost x rows). management/* endpoints are free. Cached requests cost nothing.

Auth:   Bearer token, secret 'ahrefs-token' (pointer-only). Plan: Advanced, 1,000,000 units/reset (upgraded 23 Jul 2026; read it live with `units`).
Config: [[ahrefs-api-configuration]].  Full 105-method reference: ahrefs/ahrefs-api-skills repo.

CLI:
  VAULT=/tmp/pbs python3 /tmp/pbs/ahrefs-api.py units                 # limit / used / remaining / reset
  VAULT=/tmp/pbs python3 /tmp/pbs/ahrefs-api.py projects              # management/projects (free)
  VAULT=/tmp/pbs python3 /tmp/pbs/ahrefs-api.py dr <target> [date]    # domain rating
  VAULT=/tmp/pbs python3 /tmp/pbs/ahrefs-api.py get <path> k=v k=v    # raw GET, metered+logged
"""
import os, sys, json, ssl, time, datetime, urllib.request, urllib.parse, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")
BASE = "https://api.ahrefs.com/v3/"
UA = "Mozilla/5.0"  # harmless for Ahrefs; keeps one call convention across our helpers


def _token():
    return open(f"{VAULT}/Library/processes/secrets/ahrefs-token").read().strip()


def _yesterday():
    return (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


def _log_usage(service, endpoint, units, cached, http_status, caller, property_key, note):
    """Best-effort write to public.seo_api_usage. A logging failure must never break a pull."""
    try:
        row = {"service": service, "endpoint": endpoint[:200], "units": units, "cached": cached,
               "http_status": http_status, "caller": (caller or "ahrefs-api")[:80],
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


class AhrefsError(RuntimeError):
    def __init__(self, code, reason):
        self.code = code
        self.reason = reason
        tag = ("QUOTA (units exhausted)" if code == 403 else "AUTH" if code == 401
               else "BAD DATE (Ahrefs needs a past date)" if code == 400 and "date" in (reason or "").lower()
               else f"HTTP {code}")
        super().__init__(f"[{tag}] {reason}")


class BudgetRefused(RuntimeError):
    pass


class AhrefsAPI:
    # refuse a METERED call when remaining units are at or below this floor (0 = only when truly out).
    MIN_UNITS = 0

    def __init__(self, caller=None):
        self.token = _token()
        self.caller = caller
        self._remaining = None  # cached process-lifetime; refreshed lazily, decremented locally

    # ---- low level -------------------------------------------------------
    NET_RETRIES = 3   # transient TLS/socket blips -- Ahrefs drops the odd connection

    def _raw(self, path, params):
        """One Ahrefs request, with retries on TRANSIENT network faults only.

        ⚠ A bare urlopen here used to let a one-off `SSLEOFError: UNEXPECTED_EOF_WHILE_READING`
        escape as a 40-line traceback that looked like a broken helper (23 Jul 2026, mid-analysis
        for Pete). It is a dropped TLS handshake, not a fault: the identical call succeeded on the
        next attempt. Transient = URLError/SSLError/timeout -- retried with backoff, then raised as
        a ONE-LINE AhrefsError. An HTTPError (400/401/403) is a real answer and is NEVER retried.
        """
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}",
                                                   "User-Agent": UA, "Accept": "application/json"})
        last = None
        for attempt in range(self.NET_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    cost = r.headers.get("x-api-units-cost-total-actual")
                    body = json.loads(r.read().decode())
                    return body, (int(cost) if cost and cost.isdigit() else None), r.status
            except urllib.error.HTTPError as e:
                try:
                    reason = e.read().decode()[:250]
                except Exception:
                    reason = ""
                raise AhrefsError(e.code, reason)
            except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as e:
                last = e
                if attempt < self.NET_RETRIES - 1:
                    time.sleep(1.5 * (attempt + 1))
        raise AhrefsError(0, f"network fault after {self.NET_RETRIES} attempts on {path}: "
                             f"{type(last).__name__}: {last}")

    def units_remaining(self, force=False):
        if self._remaining is not None and not force:
            return self._remaining
        body, _, _ = self._raw("subscription-info/limits-and-usage", {})
        lu = body.get("limits_and_usage", {})
        lim = lu.get("units_limit_workspace"); used = lu.get("units_usage_workspace")
        self._remaining = (lim - used) if (lim is not None and used is not None) else None
        return self._remaining

    def _is_management(self, path):
        return path.startswith("management/") or path.startswith("subscription-info/")

    def call(self, path, params=None, property_key=None, note=None):
        """Metered, gated, logged Ahrefs call. management/* + subscription-info/* are unmetered and always pass."""
        params = params or {}
        metered = not self._is_management(path)
        if metered:
            rem = self.units_remaining()
            if rem is not None and rem <= self.MIN_UNITS:
                # log the refusal (0 units spent) so the ledger shows the gate firing
                _log_usage("ahrefs", path, 0, False, None, self.caller, property_key,
                           f"REFUSED: units exhausted ({rem} left)")
                raise BudgetRefused(f"Ahrefs units exhausted ({rem} left); refusing metered call {path}. "
                                    f"management/* is still callable. Resets monthly.")
        try:
            body, cost, status = self._raw(path, params)
        except AhrefsError as e:
            _log_usage("ahrefs", path, None, False, e.code, self.caller, property_key, e.reason[:120])
            raise
        _log_usage("ahrefs", path, (0 if not metered else cost), False, status, self.caller, property_key, note)
        if metered and cost and self._remaining is not None:
            self._remaining -= cost
        return body

    # ---- convenience -----------------------------------------------------
    def projects(self):
        return self.call("management/projects").get("projects", [])

    def domain_rating(self, target, date=None):
        b = self.call("site-explorer/domain-rating", {"target": target, "date": date or _yesterday()})
        return (b.get("domain_rating") or {}).get("domain_rating")

    def rank_tracker(self, project_id, date=None, device="desktop",
                     select="keyword,position,url,volume", limit="1000"):
        return self.call("rank-tracker/overview",
                         {"project_id": project_id, "device": device, "date": date or _yesterday(),
                          "select": select, "limit": limit}).get("overviews", [])


def _cli():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    api = AhrefsAPI(caller="cli")
    cmd = a[0]
    try:
        if cmd == "units":
            body, _, _ = api._raw("subscription-info/limits-and-usage", {})
            lu = body.get("limits_and_usage", {})
            print(json.dumps(lu, indent=1))
        elif cmd == "projects":
            for p in sorted(api.projects(), key=lambda x: -int(x.get("keyword_count") or 0)):
                print(f"  {p['project_id']:9} {p['url']:34} kw={p.get('keyword_count')}")
        elif cmd == "dr":
            print(api.domain_rating(a[1], a[2] if len(a) > 2 else None))
        elif cmd == "get":
            params = dict(kv.split("=", 1) for kv in a[2:])
            print(json.dumps(api.call(a[1], params), indent=1)[:4000])
        else:
            print(f"unknown command: {cmd}\n{__doc__}")
    except (AhrefsError, BudgetRefused) as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    _cli()
