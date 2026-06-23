#!/usr/bin/env python3
"""JotForm API helper -- single canonical path for all JotForm work.

Pattern matches the other Library/processes/scripts/ helpers (gmail-api.py,
calendar-api.py, etc). Wraps the JotForm REST API (api.jotform.com, v1).

Account:
  username: Sygmasolutions
  holder:   michaela.ashcroft @ jotform@sygma-solutions.com
  plan:     SILVER
  region:   US (default base url)

Auth:
  API key in `Library/processes/secrets/jotform-api-key` (one line, 32 hex
  chars). Sent as `apiKey` query param OR `APIKEY` header. We use the query
  param for simplicity; both are supported by the API.

CLI usage:
  python3 jotform-api.py user                            # account summary
  python3 jotform-api.py forms [LIMIT] [OFFSET]          # list forms (default 100, default 0)
  python3 jotform-api.py form FORM_ID                    # one form's metadata
  python3 jotform-api.py questions FORM_ID               # form fields
  python3 jotform-api.py submissions FORM_ID [LIMIT] [OFFSET]   # list submissions
  python3 jotform-api.py submission SUBMISSION_ID        # one submission
  python3 jotform-api.py files FORM_ID                   # files uploaded across submissions
  python3 jotform-api.py download URL SAVE_PATH          # download a file URL (auth-attached)
  python3 jotform-api.py search FORM_ID FIELD VALUE      # find submissions where field == value
  python3 jotform-api.py raw PATH [PARAMS...]            # passthrough GET, params as key=value pairs

Library usage:
  from jotform_api import JotForm
  j = JotForm()
  user = j.user()
  forms = j.forms(limit=1000)
  subs = j.submissions("201324458767056", limit=200)
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

VAULT = Path("/Users/peterashcroft/Second Brain")
KEY_PATH = VAULT / "Library/processes/secrets/jotform-api-key"
BASE_URL = "https://api.jotform.com"
TIMEOUT_SEC = 30

# JotForm API quirk: created_at / updated_at are stamped in EST as a FIXED offset
# (UTC-5, no DST), regardless of the account's time_zone setting.
# The account-level time_zone only affects the JotForm web UI display, NOT the API.
# We expose `created_at_uk` (Europe/London, DST-aware) on every submission so callers
# don't have to know the quirk. Original `created_at` left untouched for diagnostics.
JOTFORM_API_TZ = ZoneInfo("America/New_York")  # DST-aware: EDT in summer, EST in winter
UK_TZ = ZoneInfo("Europe/London")


def _to_uk(ts_str: str) -> str | None:
    """Convert a JotForm 'YYYY-MM-DD HH:MM:SS' (EST-fixed) string to a UK-local
    ISO string. Returns None if parse fails."""
    if not ts_str:
        return None
    try:
        naive = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=JOTFORM_API_TZ).astimezone(UK_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return None


def _add_uk_times(obj: Any) -> Any:
    """Recursively walk a JSON-like structure and add `created_at_uk` / `updated_at_uk`
    sibling fields wherever `created_at` / `updated_at` are found at dict level."""
    if isinstance(obj, dict):
        for ts_key, uk_key in (("created_at", "created_at_uk"), ("updated_at", "updated_at_uk")):
            if ts_key in obj and isinstance(obj[ts_key], str) and uk_key not in obj:
                converted = _to_uk(obj[ts_key])
                if converted:
                    obj[uk_key] = converted
        for v in obj.values():
            _add_uk_times(v)
    elif isinstance(obj, list):
        for item in obj:
            _add_uk_times(item)
    return obj


class JotFormError(RuntimeError):
    pass


class JotForm:
    """Thin wrapper over the JotForm v1 REST API.

    Loads key from the vault. All calls return parsed JSON `content` by default;
    full response is available via `_call` for diagnostics.
    """

    def __init__(self, key_path: Path = KEY_PATH, base_url: str = BASE_URL):
        if not key_path.exists():
            raise JotFormError(
                f"No JotForm API key at {key_path}. "
                f"Save the key there first (one line, no quotes)."
            )
        self.api_key = key_path.read_text().strip()
        if not self.api_key:
            raise JotFormError(f"JotForm API key at {key_path} is empty.")
        self.base = base_url.rstrip("/")

    def _call(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params["apiKey"] = self.api_key
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            raise JotFormError(f"HTTP {e.code} {e.reason}: {e.read()[:500]!r}") from e
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise JotFormError(f"Non-JSON response: {body[:500]!r}") from e
        if data.get("responseCode") != 200:
            raise JotFormError(f"API error {data.get('responseCode')}: {data.get('message')}")
        # Add UK-local sibling fields next to every created_at / updated_at in the response.
        # JotForm's API returns timestamps in EST-fixed (UTC-5, no DST) regardless of account
        # timezone. See the file-level constants for the conversion.
        _add_uk_times(data)
        return data

    # -- High-level methods --------------------------------------------------

    def user(self) -> dict:
        return self._call("/user")["content"]

    def usage(self) -> dict:
        return self._call("/user/usage")["content"]

    def forms(self, limit: int = 100, offset: int = 0, orderby: str = "updated_at",
              filter_: dict | None = None) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset, "orderby": orderby}
        if filter_:
            params["filter"] = json.dumps(filter_)
        return self._call("/user/forms", params)["content"]

    def form(self, form_id: str) -> dict:
        return self._call(f"/form/{form_id}")["content"]

    def form_questions(self, form_id: str) -> dict:
        """Returns the field map. Keys are field qid (e.g. '3'); values are field meta."""
        return self._call(f"/form/{form_id}/questions")["content"]

    def submissions(self, form_id: str, limit: int = 100, offset: int = 0,
                    orderby: str = "created_at", filter_: dict | None = None) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset, "orderby": orderby}
        if filter_:
            params["filter"] = json.dumps(filter_)
        return self._call(f"/form/{form_id}/submissions", params)["content"]

    def submission(self, submission_id: str) -> dict:
        return self._call(f"/submission/{submission_id}")["content"]

    def files(self, form_id: str) -> list[dict]:
        """All file uploads on a form across submissions."""
        return self._call(f"/form/{form_id}/files")["content"]

    def search_submissions(self, form_id: str, field_qid: str, value: str,
                           limit: int = 100) -> list[dict]:
        """Find submissions where answers[field_qid] == value."""
        filter_ = {f"q{field_qid}": value}
        return self.submissions(form_id, limit=limit, filter_=filter_)

    def download(self, url: str, save_path: Path) -> Path:
        """Download a file URL (attaches apiKey for protected uploads)."""
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}apiKey={self.api_key}"
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(full, timeout=TIMEOUT_SEC) as r:
            save_path.write_bytes(r.read())
        return save_path

    def raw(self, path: str, params: dict | None = None) -> dict:
        """Passthrough GET. Returns the full response, not just `content`."""
        return self._call(path, params)


# -- CLI ---------------------------------------------------------------------

def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _cli() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    j = JotForm()

    if cmd == "user":
        u = j.user()
        # Summary line + full JSON
        print(f"# {u.get('username')} | {u.get('email')} | plan={u.get('account_type','').split('/')[-1]} | status={u.get('status')}")
        _print(u)
    elif cmd == "usage":
        _print(j.usage())
    elif cmd == "forms":
        limit = int(args[0]) if args else 100
        offset = int(args[1]) if len(args) > 1 else 0
        forms = j.forms(limit=limit, offset=offset)
        print(f"# {len(forms)} forms (limit={limit}, offset={offset})")
        for f in forms:
            print(f"{f.get('id')} | count={f.get('count','?'):>5} | status={f.get('status')} | updated={(f.get('updated_at') or '')[:10]} | {f.get('title','')[:60]}")
    elif cmd == "form":
        _print(j.form(args[0]))
    elif cmd == "questions":
        _print(j.form_questions(args[0]))
    elif cmd == "submissions":
        form_id = args[0]
        limit = int(args[1]) if len(args) > 1 else 100
        offset = int(args[2]) if len(args) > 2 else 0
        subs = j.submissions(form_id, limit=limit, offset=offset)
        print(f"# {len(subs)} submissions for form {form_id} (limit={limit}, offset={offset})")
        _print(subs)
    elif cmd == "submission":
        _print(j.submission(args[0]))
    elif cmd == "files":
        _print(j.files(args[0]))
    elif cmd == "download":
        url, save = args[0], args[1]
        path = j.download(url, Path(save))
        print(f"saved: {path} ({path.stat().st_size} bytes)")
    elif cmd == "search":
        form_id, qid, value = args[0], args[1], args[2]
        _print(j.search_submissions(form_id, qid, value))
    elif cmd == "raw":
        path = args[0]
        params = {}
        for kv in args[1:]:
            if "=" in kv:
                k, v = kv.split("=", 1); params[k] = v
        _print(j.raw(path, params))
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    _cli()
