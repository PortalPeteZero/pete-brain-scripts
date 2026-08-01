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


def _warn_thin(path, params, body):
    """Shout when a response is EMPTY or SHORTER than asked for. An empty Ahrefs payload is not
    an answer -- it is usually a malformed parameter, and it reads exactly like "no data".

    Both traps below were hit on 23 Jul 2026 while analysing the cat-and-genny page:
      * keywords-explorer/overview with NEWLINE-separated keywords returned {"keywords": []}.
        Ahrefs wants them COMMA-separated. Silent empty list, no error.
      * serp-overview asked for top_positions=20 and got 10 back, with nothing saying so --
        so "we are not in the top 20" was claimed on data that only ever covered the top 10.
    """
    if not isinstance(body, dict):
        return
    for k, v in body.items():
        if not isinstance(v, list):
            continue
        if not v:
            hint = ("keywords must be COMMA-separated in one value" if "keywords" in path
                    else "check country/date -- Ahrefs holds no SERP for low-volume terms")
            print(f"WARNING: '{k}' came back EMPTY for {path}. This is NOT proof of no data -- "
                  f"{hint}. Do not report an empty result as a finding.", file=sys.stderr)
            return
        want = params.get("top_positions") or params.get("limit")
        try:
            want = int(want) if want else None
        except ValueError:
            want = None
        if want and len(v) < want:
            print(f"WARNING: asked for {want} rows, Ahrefs returned {len(v)}. Your conclusion can "
                  f"only cover the {len(v)} you actually got -- do not generalise past them.",
                  file=sys.stderr)
        return


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

    def _raw(self, path, params, method="GET", body=None):
        """One Ahrefs request, with retries on TRANSIENT network faults only.

        ⚠ A bare urlopen here used to let a one-off `SSLEOFError: UNEXPECTED_EOF_WHILE_READING`
        escape as a 40-line traceback that looked like a broken helper (23 Jul 2026, mid-analysis
        for Pete). It is a dropped TLS handshake, not a fault: the identical call succeeded on the
        next attempt. Transient = URLError/SSLError/timeout -- retried with backoff, then raised as
        a ONE-LINE AhrefsError. An HTTPError (400/401/403) is a real answer and is NEVER retried.
        """
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        hdrs = {"Authorization": f"Bearer {self.token}", "User-Agent": UA, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, headers=hdrs, data=data, method=method)
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

    def call(self, path, params=None, property_key=None, note=None, method="GET", body=None):
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
            body, cost, status = self._raw(path, params, method=method, body=body)
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


    # ---- keywords explorer (volume / difficulty / discovery) --------------
    # 25 units per keyword row. Used 1 Aug 2026 to fill 163 map keywords that had NO volume
    # recorded; 22 of them had real demand (incl. "cat & genny training" at 161/mo) and were being
    # ranked as worthless by the volume-first rule. If a keyword in seo_keyword_map has priority 0,
    # it usually means nobody ever asked Ahrefs -- not that the term is dead.
    def keywords_overview(self, keywords, country="gb", select="keyword,volume_monthly,difficulty"):
        """Volume + difficulty for a list of keywords. Chunk large lists -- this is a GET."""
        if isinstance(keywords, str):
            keywords = [keywords]
        return self.call("keywords-explorer/overview",
                         {"select": select, "country": country,
                          "keywords": ",".join(keywords)}).get("keywords", [])

    def matching_terms(self, keyword, country="gb", limit=50,
                       select="keyword,volume_monthly,difficulty"):
        """Terms CONTAINING the seed -- the honest way to find concepts the map is missing."""
        return self.call("keywords-explorer/matching-terms",
                         {"select": select, "country": country, "keywords": keyword,
                          "limit": limit}).get("keywords", [])

    def related_terms(self, keyword, country="gb", limit=50,
                      select="keyword,volume_monthly,difficulty"):
        return self.call("keywords-explorer/related-terms",
                         {"select": select, "country": country, "keywords": keyword,
                          "limit": limit}).get("keywords", [])

    # ---- who is beating us ------------------------------------------------
    def serp_overview(self, keyword, country="gb", organic_only=True,
                      select="position,type,url,title,domain_rating,url_rating,refdomains,traffic"):
        """The live SERP for a term: who outranks us and on what authority.

        RULE TWO in the seo-report skill says test the obvious alternative FIRST -- read the page
        beating us before theorising about Google. This is the call that makes that cheap.

        ⚠ TWO TRAPS, both hit on the first live call (1 Aug 2026):
        1. `type` is a **LIST**, not a string -- a row can be
           `["ai_overview_sitelink","image_th"]`. Counting it with a Counter raises
           `unhashable type: 'list'`.
        2. The response mixes SERP FEATURES in with organic results. On "cat and genny training",
           16 of 44 rows were AI overviews / sitelinks / images. Those rows carry `position: 1` and
           NULL domain_rating / traffic. Read them naively and you report "every result is position 1
           with no DR", which is nonsense -- and is exactly what I reported before checking.
           `organic_only=True` (the default) keeps only rows whose type includes "organic", which DO
           carry real DR, refdomains and traffic, and renumbers them 1..N as a human sees the page.
        """
        rows = self.call("serp-overview/serp-overview",
                         {"select": select, "country": country,
                          "keyword": keyword}).get("positions", [])
        if not organic_only:
            return rows
        out = []
        for r in rows:
            t = r.get("type")
            t = t if isinstance(t, list) else ([t] if t else [])
            if any("organic" in str(x) for x in t):
                out.append({**r, "organic_position": len(out) + 1})
        return out

    def tracked_serp(self, project_id, keyword, country="gb", device="desktop", organic_only=True,
                     select="position,url,type,domain_rating,url_rating,backlinks,refdomains,traffic"):
        """The SERP for a keyword IN A RANK TRACKER PROJECT -- and it is FREE (0 units).

        USE THIS, NOT serp_overview(), for anything in the tracker (which since 1 Aug 2026 is
        exactly seo_keyword_map). Measured 1 Aug 2026:
          · serp-overview/serp-overview  -- ~1,094 units for a populated SERP, and it returned
            `{"positions": []}` for many real terms ("gpr training", "rd8000 training", …)
          · rank-tracker/serp-overview   -- **0 units**, 36 rows on the same terms, carries DR /
            refdomains / traffic, AND includes OUR OWN row so the comparison is direct
        I had defaulted a winnability view to OFF to protect a budget it never needed to spend.
        Requires device AND country AND keyword AND project_id -- all four, or it 400s.

        Same SERP-feature trap as serp_overview: `type` is a LIST and the response mixes AI
        overviews / sitelinks in with organic. organic_only=True renumbers real organic 1..N.
        """
        rows = self.call("rank-tracker/serp-overview",
                         {"project_id": project_id, "keyword": keyword, "country": country,
                          "device": device, "select": select}).get("positions", [])
        if not organic_only:
            return rows
        out = []
        for r in rows:
            t = r.get("type")
            t = t if isinstance(t, list) else ([t] if t else [])
            if any("organic" in str(x) for x in t):
                out.append({**r, "organic_position": len(out) + 1})
        return out

    def competitors_overview(self, project_id, select="competitor_domain,keywords_count"):
        return self.call("rank-tracker/competitors-overview",
                         {"project_id": project_id, "select": select}).get("competitors", [])

    def site_audit_issues(self, project_id, select="name,category,issues_count"):
        return self.call("site-audit/issues",
                         {"project_id": project_id, "select": select}).get("issues", [])

    # ---- WRITES (Rank Tracker management) --------------------------------
    # ⚠ CORRECTED 1 Aug 2026. The config note said the API could not delete keywords, and a blind
    # probe of `DELETE /management/project-keywords` + `POST .../delete` seemed to confirm it. Both
    # were the wrong shape. The OpenAPI spec (https://docs.ahrefs.com/openapi.json -- 129 endpoints,
    # 30 of them writes) gives the real paths below. READ THE SPEC before declaring an operation
    # unsupported: the wrong answer nearly sent Pete to delete 103 keywords by hand.
    # project_id is a QUERY parameter on all of these, never a body field.
    def add_project_keywords(self, project_id, keywords, tags=None, country="gb"):
        """keywords: list of str, or list of {'keyword':..,'tags':[..]}."""
        items = []
        for k in keywords:
            items.append(k if isinstance(k, dict)
                         else {"keyword": k, **({"tags": tags} if tags else {})})
        return self.call("management/project-keywords", {"project_id": project_id},
                         method="PUT", body={"locations": [{"country": country}], "keywords": items},
                         note=f"add {len(items)} keywords")

    def delete_project_keywords(self, project_id, keywords, country="gb"):
        items = [{"keyword": k, "country": country} for k in keywords]
        return self.call("management/project-keywords-delete", {"project_id": project_id},
                         method="PUT", body={"keywords": items},
                         note=f"delete {len(items)} keywords")

    def project_keywords(self, project_id, select="keyword,tags", limit=2000):
        """What the tracker currently holds (free -- management/*)."""
        return self.call("management/project-keywords",
                         {"project_id": project_id, "select": select, "limit": limit}).get("keywords", [])

    def sync_project_to_map(self, project_id, property_key, apply=False, country="gb"):
        """Make the Ahrefs tracker MIRROR seo_keyword_map. The map decides; Ahrefs follows.

        Pete, 1 Aug 2026: "we decide, ahref follows us". Returns the diff; only writes when
        apply=True. Run it after any change to the map, and the tracker can never drift again.
        """
        import subprocess as _sp, os as _os
        v = _os.environ.get("VAULT", "/tmp/pbs")
        r = _sp.run(["python3", "cc-sql.py",
                     "SELECT keyword, cluster FROM seo_keyword_map "
                     f"WHERE property_key='{property_key}' AND intent='commercial'"],
                    cwd=v, capture_output=True, text=True, env={**_os.environ, "VAULT": v}, timeout=60)
        if r.returncode != 0:
            raise RuntimeError("cc-sql FAILED reading seo_keyword_map: " + (r.stderr or r.stdout)[:200])
        want = {row["keyword"].strip().lower(): row for row in json.loads(r.stdout or "[]")}
        have = {k["keyword"].strip().lower() for k in self.project_keywords(project_id)}
        add, remove = sorted(set(want) - have), sorted(have - set(want))
        if apply:
            if add:
                self.add_project_keywords(project_id,
                                          [{"keyword": want[k]["keyword"], "tags": [want[k]["cluster"]]}
                                           for k in add], country=country)
            if remove:
                self.delete_project_keywords(project_id, remove, country=country)
        return {"add": add, "remove": remove, "in_map": len(want), "in_tracker": len(have),
                "applied": bool(apply)}


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
            body = api.call(a[1], params)
            # ⚠ was `[:4000]` — that truncated mid-object and handed callers INVALID JSON that
            # crashed every downstream json.load (24 Jul 2026, mid-analysis). Never truncate a
            # payload a caller has to parse: print it whole, or summarise deliberately.
            out = json.dumps(body, indent=1)
            if os.environ.get("AHREFS_SUMMARY") and isinstance(body, dict):
                for k, v in body.items():
                    if isinstance(v, list):
                        print(f"{k}: {len(v)} rows"); break
            else:
                print(out)
            _warn_thin(a[1], params, body)
        else:
            print(f"unknown command: {cmd}\n{__doc__}")
    except (AhrefsError, BudgetRefused) as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    _cli()
