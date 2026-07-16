#!/usr/bin/env python3
r"""
damage-review-learn — the SELF-LEARNING half of the damage-review engine.

When Pete corrects a wording/terminology mistake in a damage review, bank it here ONCE and it
becomes an enforced rule in public.damage_review_rules forever (damage-review-lint then blocks
it on every future report). Corrections improve the engine mechanically instead of being lost
to a chat or relied on from memory.

Usage:
  # forbid a phrase + say the correct form (default kind=forbidden)
  VAULT=/tmp/pbs python3 /tmp/pbs/damage-review-learn.py --bad "standing still" \
       --say "the CAT is no longer being used to locate, not physically still" --from "wellmoor 16-07"
  # raw regex instead of a plain phrase
  ... --regex "(?i)\bstrik(e|es|ing)\b" --exception "(?i)strikealert" --say "use 'damage'"
  # a required element (warn if missing)
  ... --kind require --regex "UK local \(BST\)" --say "include the BST times caveat"
  --list       show current rules

Ties to the Damage section: pass --damage <job_ref> to attach the rule's provenance to a
clancy_damages incident (which review taught us this).
"""
import sys, os, re, json, argparse, urllib.request, urllib.parse

KEYS = json.load(open(os.path.expanduser("~/.config/pete-secrets/command-centre-supabase-keys.json")))
SRK = KEYS["service_role_key"]; BASE = KEYS["url"] + "/rest/v1"
H = {"apikey": SRK, "Authorization": f"Bearer {SRK}", "Content-Type": "application/json"}


def _req(method, path, body=None, prefer=None):
    h = dict(H)
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(f"{BASE}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers=h)
    r = urllib.request.urlopen(req, timeout=30)
    txt = r.read()
    return json.loads(txt) if txt else None


def list_rules():
    rows = _req("GET", "damage_review_rules?active=eq.true&order=id")
    print(f"{len(rows)} active rules:")
    for r in rows:
        prov = f"  (learned: {r['learned_from']})" if r.get("learned_from") else ""
        print(f"  [{r['id']}] {r['kind']}/{r['severity']}  /{r['pattern']}/{prov}\n       {r['message']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bad", help="plain phrase to forbid (auto-escaped, case-insensitive, word-bounded)")
    ap.add_argument("--regex", help="raw regex instead of --bad")
    ap.add_argument("--say", help="the correction / correct form (the rule message)")
    ap.add_argument("--kind", default="forbidden", choices=["forbidden", "require", "soften"])
    ap.add_argument("--exception", help="regex of allowed exceptions (e.g. a device feature or a quote)")
    # Corrections default to ADVISORY (warn) — reports differ, so a one-off correction flags for
    # review next time, it does not bind. Pass --severity block only for a genuine SET rule that
    # applies to every Clancy report (terminology / partner tone / integrity).
    ap.add_argument("--severity", default="warn", choices=["block", "warn"])
    ap.add_argument("--from", dest="frm", help="provenance note (which review taught this)")
    ap.add_argument("--damage", help="clancy_damages job_ref this correction came from")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        list_rules(); return 0

    if not args.say or not (args.bad or args.regex):
        sys.exit("need --say plus one of --bad / --regex  (or --list)")

    pattern = args.regex if args.regex else r"(?i)\b" + re.escape(args.bad) + r"\b"

    # dedup: same pattern already banked?
    existing = _req("GET", f"damage_review_rules?pattern=eq.{urllib.parse.quote(pattern)}")
    prov = args.frm or ""
    if args.damage:
        prov = (prov + f" | damage:{args.damage}").strip(" |")
    if existing:
        _req("PATCH", f"damage_review_rules?id=eq.{existing[0]['id']}",
             {"message": args.say, "kind": args.kind, "exception": args.exception,
              "severity": args.severity, "active": True, "learned_from": prov, "learned_at": "now()"},
             prefer="return=minimal")
        print(f"updated existing rule [{existing[0]['id']}] for /{pattern}/")
    else:
        row = _req("POST", "damage_review_rules",
                   {"kind": args.kind, "pattern": pattern, "message": args.say, "exception": args.exception,
                    "severity": args.severity, "active": True, "learned_from": prov, "learned_at": "now()"},
                   prefer="return=representation")
        print(f"learned rule [{row[0]['id']}] — {args.kind}/{args.severity}  /{pattern}/")

    # provenance on the damage incident too, so the Damage section shows what a review taught
    if args.damage:
        d = _req("GET", f"clancy_damages?job_ref=eq.{urllib.parse.quote(args.damage)}&select=id,next_actions")
        if d:
            na = d[0].get("next_actions") or []
            note = f"Wording rule learned: {args.say}"
            if note not in na:
                _req("PATCH", f"clancy_damages?id=eq.{d[0]['id']}", {"next_actions": na + [note]}, prefer="return=minimal")
                print(f"  + noted on damage {args.damage}")
    if args.severity == "warn":
        print("→ banked as ADVISORY: damage-review-lint will FLAG this for review on future reports "
              "(not block). Re-run with --severity block if it's a set rule for every Clancy report.")
    else:
        print("→ banked as a SET rule: damage-review-lint will BLOCK this on every future report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
