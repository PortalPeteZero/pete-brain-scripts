#!/usr/bin/env python3
"""
edge-parity.py -- is the edge function that is RUNNING the one in the repo?

WHY THIS EXISTS
  On 6 Aug 2026 a hardening sweep committed on 17 June was found never to have been deployed.
  Eight functions on the Sygma Platform had been serving their 1 MAY build for seven weeks,
  including three on the Xero money path. Nothing in the system compared deployed against
  source, so it was invisible until somebody went looking.

  `git log` on a function folder is NOT that check -- it tells you when the SOURCE changed,
  never what is running. This reads the DEPLOYED BODY out of the Management API and compares.

THE TWO TRAPS THIS TOOL EXISTS TO SURVIVE (both cost real time on 6 Aug)

  1. The Supabase Management API returns 403 to Python's default urllib User-Agent while the
     identical token works in curl. This sends a real User-Agent. If you write another client,
     do the same before concluding the token is wrong.

  2. THE DEPLOYED CODE IS TRANSPILED AND REFORMATTED. Type annotations are stripped, objects
     are expanded across lines, indentation is normalised:
         source:   const url = Deno.env.get("SUPABASE_URL")!;
         deployed: const url = Deno.env.get("SUPABASE_URL");
     A line-level or byte-level diff is therefore MEANINGLESS -- the first version of this
     check reported all 34 functions stale, including ones deployed the same hour.

     The comparison that works is STRING LITERALS. They are data, so they survive transpilation
     intact. Import paths are excluded because the bundler rewrites them, and template fragments
     because the regex splits them badly. Measured: 16 stale of 34, where a date comparison
     suggested 22 -- six false.

  The deployed body arrives as an ESZIP2.3 archive with the bundled source readable as plain
  text inside it, so no unpacking is needed to search it.

VERIFY THE METHOD BEFORE TRUSTING IT
  --self-test runs the check against functions whose answer is already known: any function whose
  source AND shared imports settled STRICTLY BEFORE its deploy MUST come back MATCH. If those
  fail, the method is broken, not the estate. Never report a number this tool produces without it.

  Not "same date": dates here are day-granular, so a shared file committed hours AFTER a deploy
  on the same day is indistinguishable from one committed before it -- and that case genuinely
  IS stale. Using same-day cases made the first version of this test fail on admin-create-customer
  when the tool was right and the test was wrong.

USAGE
  VAULT=/tmp/pbs python3 edge-parity.py --repo /path/to/clone
  VAULT=/tmp/pbs python3 edge-parity.py --repo /path/to/clone --json
  VAULT=/tmp/pbs python3 edge-parity.py --repo /path/to/clone --self-test
  VAULT=/tmp/pbs python3 edge-parity.py --repo /path/to/clone --ref <supabase-ref>

  --repo      a checkout containing supabase/functions/  (required)
  --ref       Supabase project ref (default: the Sygma Platform)
  --json      machine output
  --self-test prove the method on known-good functions, then exit

EXIT CODES
  0  every deployed function matches its source
  1  at least one is behind        <- wire this into a gate
  2  could not check (auth, network, no functions dir)

Token: CC secret `supabase-token` (the sbp_ management token), or SUPABASE_ACCESS_TOKEN.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_REF = "rsczwfstwkthaybxhszy"  # Sygma Platform
API = "https://api.supabase.com/v1/projects"
# Supabase's edge rejects urllib's default UA with a 403 that looks exactly like a bad token.
UA = "curl/8.7.1"

# 8+ chars so we compare meaningful strings, not punctuation.
_STRING = re.compile(r'"((?:[^"\\\n]|\\.){8,})"|(?<![A-Za-z])\'((?:[^\'\\\n]|\\.){8,})\'')
_SHARED_IMPORT = re.compile(r'from\s+"\.\./_shared/([A-Za-z0-9_.-]+)"')


def _token() -> str:
    tok = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if tok:
        return tok.strip()
    vault = os.environ.get("VAULT", "/tmp/pbs")
    out = subprocess.run(
        [sys.executable, os.path.join(vault, "cc-sql.py"),
         "SELECT value FROM secrets WHERE name='supabase-token'"],
        capture_output=True, text=True, env={**os.environ, "VAULT": vault},
    )
    try:
        value = json.loads(out.stdout)[0]["value"]
    except Exception:
        sys.exit("edge-parity: could not read the supabase-token secret (exit 2)")
    try:
        parsed = json.loads(value)
        value = parsed.get("token") or parsed.get("access_token") or next(iter(parsed.values()))
    except Exception:
        pass
    return value.strip()


def _api(ref: str, path: str, token: str, binary: bool = False):
    req = urllib.request.Request(f"{API}/{ref}{path}",
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": UA})
    raw = urllib.request.urlopen(req, timeout=120).read()
    return raw if binary else json.loads(raw)


def literals(text: str) -> set:
    """Strings that survive transpilation. Import paths and template fragments excluded."""
    found = set()
    for match in _STRING.finditer(text):
        value = match.group(1) or match.group(2)
        if not value:
            continue
        if value.startswith(("http://", "https://", "./", "../")):
            continue          # the bundler rewrites import specifiers
        if value.endswith((".ts", ".js")):
            continue
        if "${" in value:
            continue          # the regex splits template literals badly
        if not re.search(r"[A-Za-z]{3}", value):
            continue
        found.add(value)
    return found


def _git_date(repo: Path, rel: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ad",
                          "--date=short", "--", rel], capture_output=True, text=True)
    return out.stdout.strip() or "-"


def check_one(fn: dict, repo: Path, ref: str, token: str) -> dict:
    slug = fn["slug"]
    src_path = repo / "supabase" / "functions" / slug / "index.ts"
    deployed_on = datetime.datetime.fromtimestamp(
        fn["updated_at"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
    result = {"slug": slug, "deployed": deployed_on, "verify_jwt": fn.get("verify_jwt"),
              "version": fn.get("version"),
              "src_commit": _git_date(repo, f"supabase/functions/{slug}")}

    if not src_path.exists():
        result["status"] = "NO-SOURCE"
        return result
    try:
        body = _api(ref, f"/functions/{slug}/body", token, binary=True).decode("utf-8", "replace")
    except Exception as exc:                                  # noqa: BLE001
        result.update(status="ERROR", detail=str(exc)[:80])
        return result

    source = src_path.read_text(encoding="utf-8")
    missing = sorted(s for s in literals(source) if s not in body)

    shared_missing = {}
    for name in set(_SHARED_IMPORT.findall(source)):
        shared = repo / "supabase" / "functions" / "_shared" / name
        if shared.exists():
            gone = sorted(s for s in literals(shared.read_text(encoding="utf-8")) if s not in body)
            if gone:
                shared_missing[name] = gone

    shared_dates = [_git_date(repo, f"supabase/functions/_shared/{n}")
                    for n in set(_SHARED_IMPORT.findall(source))]
    shared_dates = [d for d in shared_dates if d != "-"]
    result.update(missing=missing, shared_missing=shared_missing,
                  shared_commit=max(shared_dates) if shared_dates else "-",
                  status="MATCH" if not missing and not shared_missing else "STALE")
    return result


def run(repo: Path, ref: str, token: str) -> list:
    functions = _api(ref, "/functions", token)
    with ThreadPoolExecutor(5) as pool:
        return list(pool.map(lambda f: check_one(f, repo, ref, token), functions))


def self_test(results: list) -> bool:
    """Functions whose source settled STRICTLY BEFORE the deploy MUST come back MATCH.

    Deliberately not "same date". Dates here are day-granular, so a shared file committed
    hours AFTER a deploy on the same day looks identical to one committed before it -- and
    that case genuinely IS stale. Using same-day cases made this test fail on
    admin-create-customer (6 Aug 2026) when the tool was right and the test was wrong.
    """
    known_good = [r for r in results
                  if r.get("status") in ("MATCH", "STALE")
                  and r["src_commit"] != "-"
                  and r["src_commit"] < r["deployed"]
                  # no shared imports at all is the cleanest case of the lot
                  and (r.get("shared_commit", "-") == "-"
                       or r["shared_commit"] < r["deployed"])]
    if not known_good:
        print("SELF-TEST INCONCLUSIVE: no function has deploy date == source date.")
        print("Cannot prove the method here. Treat any result below as unverified.")
        return False
    failures = [r for r in known_good if r["status"] != "MATCH"]
    print(f"SELF-TEST: {len(known_good)} function(s) whose source settled before their deploy.")
    for r in known_good:
        print(f"  {'ok  ' if r['status'] == 'MATCH' else 'FAIL'}  {r['slug']} ({r['deployed']})")
    if failures:
        print("\nSELF-TEST FAILED -- the comparison is wrong, not the estate. Do not trust the run.")
        return False
    print("SELF-TEST PASSED -- the comparison is sound on known-good cases.\n")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare deployed edge functions against repo source.")
    ap.add_argument("--repo", required=True, help="checkout containing supabase/functions/")
    ap.add_argument("--ref", default=DEFAULT_REF)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / "supabase" / "functions").is_dir():
        sys.exit(f"edge-parity: no supabase/functions under {repo} (exit 2)")

    try:
        results = run(repo, args.ref, _token())
    except urllib.error.HTTPError as exc:
        hint = " -- send a real User-Agent; urllib's default gets a 403" if exc.code == 403 else ""
        sys.exit(f"edge-parity: HTTP {exc.code} from the Management API{hint} (exit 2)")
    except Exception as exc:                                   # noqa: BLE001
        sys.exit(f"edge-parity: could not reach the Management API: {exc} (exit 2)")

    ok = self_test(results)
    if args.self_test:
        sys.exit(0 if ok else 1)

    stale = [r for r in results if r["status"] == "STALE"]
    broken = [r for r in results if r["status"] in ("ERROR", "NO-SOURCE")]

    if args.json:
        print(json.dumps({"ref": args.ref, "total": len(results), "stale": len(stale),
                          "self_test_passed": ok, "results": results}, indent=1))
    else:
        print(f"{len(results)} deployed | MATCH {len(results) - len(stale) - len(broken)} "
              f"| STALE {len(stale)} | unreadable {len(broken)}\n")
        for r in sorted(stale, key=lambda x: x["deployed"]):
            extra = sum(len(v) for v in r["shared_missing"].values())
            print(f"  BEHIND  {r['slug']:36} live {r['deployed']}  source {r['src_commit']}  "
                  f"{len(r['missing'])}+{extra} string(s) absent")
            for s in r["missing"][:3]:
                print(f"            + {s[:88]}")
        for r in broken:
            print(f"  {r['status']:9} {r['slug']}  {r.get('detail', '')}")
        if not stale and not broken:
            print("  Every deployed function matches its source.")

    if not ok:
        sys.exit(2)
    sys.exit(1 if stale or broken else 0)


if __name__ == "__main__":
    main()
