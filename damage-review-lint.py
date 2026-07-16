#!/usr/bin/env python3
"""
damage-review-lint — the wording/terminology GATE for a Clancy damage-review report.

Rules live in the DATABASE (public.damage_review_rules), not in a note or a memory, so they
are enforced mechanically instead of relied-upon-from-recall. Run this on a draft report
BEFORE it ships; it exits non-zero if any 'block' rule is violated.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/damage-review-lint.py --slug clancy-wellmoor-review-v2
  VAULT=/tmp/pbs python3 /tmp/pbs/damage-review-lint.py --file /path/report.html
  cat report.html | VAULT=/tmp/pbs python3 /tmp/pbs/damage-review-lint.py --stdin

Add / change a rule = insert/update a row in public.damage_review_rules (no code change).
"""
import sys, os, re, json, argparse, urllib.request

VAULT = os.environ.get("VAULT", "/tmp/pbs")
KEYS = json.load(open(os.path.expanduser("~/.config/pete-secrets/command-centre-supabase-keys.json")))
SRK = KEYS["service_role_key"]; BASE = KEYS["url"] + "/rest/v1"


def _get(path):
    req = urllib.request.Request(f"{BASE}/{path}", headers={"apikey": SRK, "Authorization": f"Bearer {SRK}"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def load_rules():
    return _get("damage_review_rules?active=eq.true&order=id")


def load_text(args):
    if args.stdin:
        return sys.stdin.read()
    if args.file:
        return open(args.file, encoding="utf-8", errors="replace").read()
    if args.slug:
        rows = _get(f"module_content?module_key=eq.{args.slug}&select=html")
        if rows:
            return rows[0]["html"]
        # v2-style native pages keep content in the repo, not module_content
        sys.exit(f"no module_content for slug '{args.slug}' (a native page stores content in the repo — lint the repo content file with --file)")
    sys.exit("give one of --slug / --file / --stdin")


def visible_text(html):
    # drop tags so we lint the prose a reader sees (keeps us from flagging attributes/URLs)
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t)


def context(text, m, pad=45):
    a = max(0, m.start() - pad); b = min(len(text), m.end() + pad)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug"); ap.add_argument("--file"); ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--raw", action="store_true", help="lint raw HTML too, not just visible prose")
    args = ap.parse_args()

    rules = load_rules()
    html = load_text(args)
    text = html if args.raw else visible_text(html)

    violations, missing = [], []
    for r in rules:
        pat = re.compile(r["pattern"])
        exc = re.compile(r["exception"]) if r.get("exception") else None
        if r["kind"] in ("forbidden",):
            for m in pat.finditer(text):
                snippet = context(text, m)
                if exc and exc.search(snippet):
                    continue
                violations.append((r, m.group(0), snippet))
        elif r["kind"] == "require":
            if not pat.search(text):
                missing.append(r)

    print(f"damage-review-lint — {len(rules)} rules · {'HTML' if args.raw else 'prose'} check\n")
    if not violations and not missing:
        print("PASS — no wording-rule violations."); return 0

    blocks = [(r, h, s) for (r, h, s) in violations if r["severity"] == "block"]
    warns = [(r, h, s) for (r, h, s) in violations if r["severity"] != "block"]

    # SET rules — non-negotiable, fail the report.
    if blocks:
        print("SET RULES (must fix — apply to every Clancy report):")
        for r, hit, snip in blocks:
            print(f"  ✗ «{hit}»  {r['message']}")
            print(f"      … {snip}")
    # Advisory — flagged for a per-report judgement, never an auto-fail.
    if warns or missing:
        print("\nADVISORY (your call — corrected before, but reports differ; check it fits THIS one):")
        for r, hit, snip in warns:
            print(f"  • «{hit}»  {r['message']}")
            print(f"      … {snip}")
        for r in missing:
            print(f"  • [missing] {r['message']}")

    print(f"\n{'BLOCK — fix the set-rule hits before shipping.' if blocks else 'PASS with advisories — nothing blocking; review the flags and decide.'}")
    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
