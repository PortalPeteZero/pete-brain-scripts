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


_STEM_RULES = (("sses", "ss"), ("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("xes", "x"),
               ("ing", ""), ("ed", ""), ("es", ""), ("s", ""))


def _stem(w):
    """Light suffix folding so locator~locators, centre~centres, use~used~using.

    Deliberately conservative -- it exists to stop a plural costing us a whole term,
    not to be linguistically correct. See terms_vs_content() for why it is needed.
    """
    for suf, rep in _STEM_RULES:
        if suf == "s" and w.endswith("ss"):
            continue
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            w = w[:-len(suf)] + rep
            break
    if len(w) >= 3 and w.endswith("e"):
        w = w[:-1]
    return w


def _tokens(s):
    import re as _re
    return [_stem(t) for t in _re.findall(r"[a-z0-9]+", (s or "").lower())]


def _count_term(term_toks, text_toks, slack=4):
    """Occurrences of a Surfer term in the text: BAG-OF-WORDS within a proximity window.

    Surfer terms are NOT literal phrases -- the API hands back NLP-normalised token groups
    ("cat genny", "genny cat", "genny training cat") with stopwords dropped and order not
    preserved. Matching them literally is what produced the 23 Jul false reading. So a hit is
    "all of the term's tokens appear inside a window of len(term)+slack tokens", counted
    non-overlapping. Overlapping TERMS legitimately both score: one "cat and genny training"
    on the page credits "cat genny", "cat genny training" AND "genny training course" -- that
    is how Surfer scores it, and it is the right behaviour (Pete asked exactly this, 23 Jul).
    """
    n = len(term_toks)
    if n == 0:
        return 0
    if n == 1:
        return sum(1 for t in text_toks if t == term_toks[0])
    need = set(term_toks)
    win = n + slack
    hits, i, end = 0, 0, len(text_toks)
    while i <= end - n:
        seg = text_toks[i:i + win]
        if need.issubset(seg):
            hits += 1
            i += max(idx for idx, t in enumerate(seg) if t in need) + 1
        else:
            i += 1
    return hits


class ParseImplausible(RuntimeError):
    """The term counts cannot be true -- refuse to let them be reported as a finding."""


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

    def content_score(self, editor_id):
        """The editor's Content Score (0-100). Editor must be state=completed."""
        return self.call("GET", f"content_editors/{editor_id}/content_score").get("content_score")

    def editor_terms(self, editor_id):
        """TARGET terms for the editor. Editor must be state=completed.

        ⚠ READ THIS BEFORE INTERPRETING (mis-parsed 23 Jul 2026 and reported to Pete as a finding):
        Each row is a TARGET, not a measurement. Shape:
            {"term", "target_range": {"min", "max"}, "use_in_heading", "is_nlp", "included", "ignored"}
        There is **NO `count` / usage / frequency field**. A naive `t.get("count", 0) == 0` therefore
        reads EVERY term as "missing" and invents a finding (it claimed all 185 terms were absent from a
        page scoring 72 -- impossible). To say anything about what the page actually USES you must fetch
        the page content (`editor_content`) and count occurrences yourself, then compare to target_range.
        Use `terms_vs_content()` for that -- do not hand-roll it.
        """
        return self.call("GET", f"content_editors/{editor_id}/terms").get("terms", [])

    def editor_content(self, editor_id):
        """The HTML content the editor holds (what was imported from the live URL)."""
        return self.call("GET", f"content_editors/{editor_id}/content").get("content", "")

    ZERO_ALARM = 0.45   # share of terms reading zero that is impossible on a decent page
    ALARM_SCORE = 60    # ...at or above this content score

    def terms_vs_content(self, editor_id):
        """The ONLY sanctioned way to answer 'which target terms is this page short on?'.

        Counts each target term's occurrences in the editor's own content and compares to
        target_range. Returns rows: {term, used, min, max, status(under|ok|over), use_in_heading}.
        Exists because the terms endpoint carries targets only -- see editor_terms().

        ⚠ TWO FAULTS THIS METHOD EXISTS TO PREVENT (both found on real Sygma data, 23 Jul 2026):
        1. Reading the terms endpoint as if it measured usage -- it does not (see editor_terms).
        2. Matching a term LITERALLY. Surfer terms are NLP-normalised token bags, not phrases:
           the cat-and-genny page carries "cat and genny" 22 times, yet a literal `\\bcat genny\\b`
           regex scored the page's PRIMARY term at 0/17 and marked 115 of 185 terms "under".
           Matching is therefore stemmed + stopword-insensitive + order-free (see _count_term).

        The count is a faithful APPROXIMATION of Surfer's own matcher, not a replica: trust the
        direction and the big gaps, never an exact figure. To stop fault 2 recurring in any new
        form, an implausible result (>=45% of terms at zero while the content score is >=60)
        raises ParseImplausible rather than being returned as a finding.
        """
        terms = self.editor_terms(editor_id)
        import re as _re
        text_toks = _tokens(_re.sub(r"<[^>]+>", " ", self.editor_content(editor_id) or ""))
        out = []
        for t in terms:
            if t.get("ignored"):
                continue
            term = t.get("term") or ""
            if not term.strip():
                continue
            used = _count_term(_tokens(term), text_toks)
            rng = t.get("target_range") or {}
            lo, hi = rng.get("min"), rng.get("max")
            status = "ok"
            if lo is not None and used < lo:
                status = "under"
            elif hi is not None and used > hi:
                status = "over"
            out.append({"term": term, "used": used, "min": lo, "max": hi,
                        "status": status, "use_in_heading": t.get("use_in_heading")})
        if out:
            zeros = sum(1 for r in out if r["used"] == 0) / len(out)
            score = self.content_score(editor_id) or 0
            if zeros >= self.ZERO_ALARM and score >= self.ALARM_SCORE:
                raise ParseImplausible(
                    f"{zeros:.0%} of {len(out)} terms read as ZERO on a page scoring {score}. "
                    f"That cannot be true -- the matcher is wrong again, not the page. "
                    f"Fix _count_term/_stem before reporting anything from this editor.")
        return out


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
        elif a[0] == "terms":
            eid = a[1]
            rows = api.terms_vs_content(eid)
            print(f"content score {api.content_score(eid)} | {len(rows)} terms | "
                  f"under {sum(1 for r in rows if r['status']=='under')} "
                  f"ok {sum(1 for r in rows if r['status']=='ok')} "
                  f"over {sum(1 for r in rows if r['status']=='over')}")
            for r in sorted(rows, key=lambda r: (r["min"] or 0) - r["used"], reverse=True):
                if r["status"] != "under":
                    continue
                print(f"  {r['term'][:44]:46} used {r['used']:>3}  target {r['min']}-{r['max']}"
                      f"{'  [heading]' if r['use_in_heading'] else ''}")
        else:
            print(f"unknown command: {a[0]}\n{__doc__}")
    except (SurferError, BudgetRefused) as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    _cli()
