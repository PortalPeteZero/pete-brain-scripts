#!/usr/bin/env python3
"""gmail-filter-parity.py — the ground-truth consistency check over Pete's Gmail filters,
plus the guarded create path that makes the failure it checks for impossible to repeat.

WHY THIS EXISTS (27 Jul 2026)
  A filter created 1 Jul 2026 read simply `from: pete.ashcroft@sygma-solutions.com -> Briefings`,
  with no subject condition. It labelled EVERY email Pete sent as a briefing. A 2 Jul session
  investigated a Briefings problem, fixed a DIFFERENT Briefings filter (a Mode-B one that was
  archiving), verified its own fix, and closed the note -- without ever noticing the broad filter
  sitting alongside it. By 27 Jul, 2,194 messages were wrongly labelled: customer threads, Clancy
  work, enquiries, personal mail.
  Nothing in the system could have refused that filter. This can.

THE THREE RULES IT ENFORCES
  F1  no-broad-self   A filter matching one of Pete's OWN sending addresses must also carry a
                      subject/query narrowing. A bare `from:<self>` matches his entire sent mail.
  F2  no-overlap      Two filters that add the SAME label must not have one strictly broader than
                      the other over the same addresses (the broad one silently wins and the
                      narrow one becomes decorative). Exact duplicates are flagged too.
  F3  briefings-mode-a  The Briefings label is for CRON-GENERATED email (Pete, 27 Jul 2026) and is
                      Mode A ALWAYS: label only, never `removeLabelIds: INBOX`. A briefing Pete
                      cannot see in his inbox is a briefing that did not happen. This rule was
                      locked on 2 Jul 2026 and broken again by the 11 Jul holiday filters.
  F4  no-dangling     A filter must not reference a label ID that no longer exists.

Usage:
  VAULT=/tmp/pbs python3 gmail-filter-parity.py                 # human summary + `0 gaps` / exit=#gap-types
  VAULT=/tmp/pbs python3 gmail-filter-parity.py --json          # machine digest
  VAULT=/tmp/pbs python3 gmail-filter-parity.py --selftest      # regression harness (real historical
                                                                # cases: 2 that MUST be caught, 2
                                                                # legitimate setups that MUST NOT be)
  VAULT=/tmp/pbs python3 gmail-filter-parity.py --create \
        --query 'from:(x@y.com) subject:("Weekly Thing")' --label Briefings [--archive]
                                                                # guarded create: runs F1-F4 on the
                                                                # PROPOSED filter against every live
                                                                # filter, and REFUSES on any gap.
"""
import importlib.util, json, os, re, sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# Pete's own sending identities. A filter matching these without a subject narrowing is F1.
SELF_ADDRESSES = {
    "pete.ashcroft@sygma-solutions.com",
    "pete@canary-detect.com",
}
# Labels that are Mode A by constitution -- label only, never archive on arrival.
MODE_A_ONLY_LABELS = {"Briefings"}

ARGS = sys.argv[1:]
AS_JSON = "--json" in ARGS
DO_CREATE = "--create" in ARGS


def _arg(flag, default=None):
    return ARGS[ARGS.index(flag) + 1] if flag in ARGS else default


def gmail():
    spec = importlib.util.spec_from_file_location("gmail_api", os.path.join(VAULT, "gmail-api.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GmailAPI()


# ---------------------------------------------------------------- criteria parsing
ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+", re.I)
# The leading (-) is captured: `-subject:(...)` is a NEGATION and is a different constraint from
# `subject:(...)`. Missing this made the check flag a deliberate subject/-subject pair (the Vercel
# alerts split) as a duplicate. Measured against Pete's live filters, 27 Jul 2026.
SUBJ_RE = re.compile(r"(-?)subject:\s*(?:\(([^)]*)\)|(\"[^\"]+\"|\S+))", re.I)


def parse_criteria(crit):
    """Reduce a Gmail filter's criteria to the things that decide breadth: the address set it
    matches on, and how it narrows by subject (positive and negative narrowings kept apart)."""
    blob = " ".join(str(crit.get(k, "")) for k in ("from", "to", "query", "subject"))
    addrs = {a.lower() for a in ADDR_RE.findall(blob)}

    subjects, neg_subjects = set(), set()
    if crit.get("subject"):
        subjects.add(str(crit["subject"]).strip().strip('"').lower())
    for neg, grp, single in SUBJ_RE.findall(crit.get("query", "") or ""):
        raw = grp or single
        target = neg_subjects if neg else subjects
        for piece in re.findall(r'"([^"]+)"|(\S+)', raw):
            val = (piece[0] or piece[1]).strip().lower()
            if val and val.upper() != "OR":
                target.add(val)
    return {"addresses": addrs, "subjects": subjects,
            "neg_subjects": neg_subjects, "raw": crit}


def describe(crit):
    return json.dumps(crit, sort_keys=True)[:170]


# ---------------------------------------------------------------- the four checks
def check_filters(filters, labels):
    """filters: list of Gmail filter dicts. labels: {id: name}. Returns list of gap dicts."""
    gaps = []
    parsed = []
    for f in filters:
        p = parse_criteria(f.get("criteria", {}) or {})
        act = f.get("action", {}) or {}
        p.update({
            "id": f.get("id", "(proposed)"),
            "add": [labels.get(x, x) for x in act.get("addLabelIds", [])],
            "add_ids": act.get("addLabelIds", []),
            "remove_ids": act.get("removeLabelIds", []),
        })
        parsed.append(p)

    # F1 -- broad match on Pete's own addresses with no subject narrowing
    for p in parsed:
        if p["addresses"] & SELF_ADDRESSES and not p["subjects"]:
            gaps.append({
                "code": "F1", "severity": "high", "filter": p["id"],
                "detail": (f"matches Pete's own address ({', '.join(sorted(p['addresses'] & SELF_ADDRESSES))}) "
                           f"with NO subject narrowing -- it will match his entire sent mail"),
                "criteria": describe(p["raw"]),
                "fix": "add a subject:(...) narrowing, or delete the filter",
            })

    # F2 -- two filters on the same label where one silently swallows the other.
    #
    # MEASURED, NOT ASSUMED (27 Jul 2026): a first cut of this check flagged 3 pairs on Pete's live
    # filters and 2 were legitimate. Both false positives shared a cause -- a pair whose two halves
    # do DIFFERENT things is deliberate tiering, not duplication:
    #   * calendar-notification: broad->label-only, narrow "Daily Agenda"->label+archive
    #   * vercel: subject:(failed..)->label-only, -subject:(failed..)->label+archive+read
    # So breadth alone is not a fault. It is a fault only when both halves have the SAME effect,
    # because then the broader one makes the narrower one decorative.
    for i, a in enumerate(parsed):
        for b in parsed[i + 1:]:
            shared_labels = set(a["add_ids"]) & set(b["add_ids"])
            if not shared_labels or not (a["addresses"] & b["addresses"]):
                continue
            names = ", ".join(sorted(labels.get(x, x) for x in shared_labels))
            same_effect = set(a["remove_ids"]) == set(b["remove_ids"])
            same_subject = (a["subjects"] == b["subjects"]
                            and a["neg_subjects"] == b["neg_subjects"])

            if same_subject and same_effect:
                gaps.append({
                    "code": "F2", "severity": "high", "filter": a["id"],
                    "detail": f"exact duplicate of {b['id']} -- same label ({names}), addresses, subject and action",
                    "criteria": describe(a["raw"]), "fix": "delete one of the two",
                })
            elif same_subject and not same_effect:
                # identical match set, different action: the one that does LESS is dead, because
                # Gmail applies both and the archiving half always wins.
                dead, live_f = (a, b) if not a["remove_ids"] else (b, a)
                gaps.append({
                    "code": "F2", "severity": "medium", "filter": dead["id"],
                    "detail": (f"same match set as {live_f['id']} on label {names}, but {live_f['id']} also "
                               f"archives. Gmail applies both, so this label-only filter has no effect."),
                    "criteria": describe(dead["raw"]),
                    "fix": f"delete {dead['id']} (it is dead), or narrow it so the two do not overlap",
                })
            elif same_effect and (not a["subjects"] and not a["neg_subjects"]) != (
                    not b["subjects"] and not b["neg_subjects"]):
                broad, narrow = ((a, b) if not (a["subjects"] or a["neg_subjects"]) else (b, a))
                gaps.append({
                    "code": "F2", "severity": "high", "filter": broad["id"],
                    "detail": (f"strictly broader than {narrow['id']} on label {names}, and both do the same "
                               f"thing: it has no subject narrowing, so it swallows everything the narrow one "
                               f"was scoped to and more, making {narrow['id']} decorative"),
                    "criteria": describe(broad["raw"]),
                    "fix": f"delete {broad['id']}, or merge its intent into {narrow['id']}'s subject list",
                })

    # F3 -- Mode A only labels must never archive on arrival
    for p in parsed:
        offending = MODE_A_ONLY_LABELS & set(p["add"])
        if offending and "INBOX" in p["remove_ids"]:
            gaps.append({
                "code": "F3", "severity": "high", "filter": p["id"],
                "detail": (f"adds {', '.join(sorted(offending))} AND removes INBOX (Mode B). "
                           f"These labels are Mode A always -- cron-generated briefings must stay visible."),
                "criteria": describe(p["raw"]),
                "fix": "drop removeLabelIds INBOX; recreate as label-only",
            })

    # F4 -- dangling label references
    for p in parsed:
        for lid in p["add_ids"] + p["remove_ids"]:
            if lid not in labels and lid not in ("INBOX", "UNREAD", "SPAM", "TRASH", "IMPORTANT", "STARRED"):
                gaps.append({
                    "code": "F4", "severity": "medium", "filter": p["id"],
                    "detail": f"references label id {lid}, which no longer exists",
                    "criteria": describe(p["raw"]), "fix": "delete or repoint the filter",
                })
    return gaps


# ---------------------------------------------------------------- guarded create
def guarded_create(g, labels):
    query, label_name = _arg("--query"), _arg("--label")
    if not query or not label_name:
        print("gmail-filter-parity: --create needs --query and --label", file=sys.stderr)
        return 2
    by_name = {v: k for k, v in labels.items()}
    if label_name not in by_name:
        print(f"gmail-filter-parity: no such label {label_name!r}", file=sys.stderr)
        return 2

    action = {"addLabelIds": [by_name[label_name]]}
    if "--archive" in ARGS:
        action["removeLabelIds"] = ["INBOX"]
    proposed = {"id": "(proposed)", "criteria": {"query": query}, "action": action}

    live = g._call("GET", "/settings/filters").get("filter", [])
    gaps = [gp for gp in check_filters(live + [proposed], labels)
            if gp["filter"] == "(proposed)" or any(
                f.get("id") == gp["filter"] for f in [proposed])]
    # also surface gaps the proposal CAUSES on an existing filter (F2 names the broader one)
    baseline = {(gp["code"], gp["filter"], gp["detail"]) for gp in check_filters(live, labels)}
    caused = [gp for gp in check_filters(live + [proposed], labels)
              if (gp["code"], gp["filter"], gp["detail"]) not in baseline]
    gaps = caused or gaps

    if gaps:
        print("REFUSED -- the proposed filter would introduce:")
        for gp in gaps:
            print(f"  [{gp['code']}] {gp['detail']}")
            print(f"        fix: {gp['fix']}")
        return 1

    made = g._call("POST", "/settings/filters", body={"criteria": proposed["criteria"], "action": action})
    print(f"created {made['id']} -- {label_name} {'(Mode B)' if '--archive' in ARGS else '(Mode A)'}")
    return 0


# ---------------------------------------------------------------- selftest
# Real cases from Pete's mailbox, kept as a regression harness. The MUST-CATCH case is the actual
# 1 Jul 2026 filter that caused 2,194 mislabelled messages. The MUST-NOT-CATCH cases are live
# filters that are deliberately built the way they are -- a gate that blocks Pete's own working
# setup is worse than no gate (see [[feedback_measure_enforcement_against_real_examples]]).
SELFTEST_LABELS = {"Label_183": "Briefings", "Label_NL": "Newsletters", "Label_AL": "Alerts"}
SELFTEST = [
    ("MUST CATCH: the 1 Jul 2026 broad self filter", ["F1", "F2"], [
        {"id": "broad-1jul", "criteria": {"from": "pete.ashcroft@sygma-solutions.com"},
         "action": {"addLabelIds": ["Label_183"]}},
        {"id": "narrow-2jul", "criteria": {"query": 'from:(pete.ashcroft@sygma-solutions.com) subject:("Morning Briefing")'},
         "action": {"addLabelIds": ["Label_183"]}},
    ]),
    ("MUST CATCH: a Briefings filter that archives (the 11 Jul holiday filter)", ["F3"], [
        {"id": "holiday-11jul", "criteria": {"query": 'from:(pete@canary-detect.com) subject:("Morning Briefing")'},
         "action": {"addLabelIds": ["Label_183"], "removeLabelIds": ["INBOX"]}},
    ]),
    ("MUST NOT CATCH: calendar two-tier (broad label-only + narrow archive)", [], [
        {"id": "cal-broad", "criteria": {"from": "calendar-notification@google.com"},
         "action": {"addLabelIds": ["Label_NL"]}},
        {"id": "cal-narrow", "criteria": {"query": 'from:calendar-notification@google.com subject:"Daily Agenda"'},
         "action": {"addLabelIds": ["Label_NL"], "removeLabelIds": ["INBOX"]}},
    ]),
    ("MUST NOT CATCH: vercel subject / -subject split", [], [
        {"id": "vc-pos", "criteria": {"query": "from:(notifications@vercel.com) subject:(failed OR paused)"},
         "action": {"addLabelIds": ["Label_AL"]}},
        {"id": "vc-neg", "criteria": {"query": "from:(notifications@vercel.com) -subject:(failed OR paused)"},
         "action": {"addLabelIds": ["Label_AL"], "removeLabelIds": ["UNREAD", "INBOX"]}},
    ]),
]


def selftest():
    failures = 0
    for name, expected, filters in SELFTEST:
        got = sorted({gp["code"] for gp in check_filters(filters, SELFTEST_LABELS)})
        ok = got == sorted(expected)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"        expected {sorted(expected)}, got {got}")
            failures += 1
    print(f"\nselftest: {len(SELFTEST) - failures}/{len(SELFTEST)} pass")
    return failures


# ---------------------------------------------------------------- main
def main():
    if "--selftest" in ARGS:
        return selftest()
    g = gmail()
    labels = {l["id"]: l["name"] for l in g.list_labels()}

    if DO_CREATE:
        return guarded_create(g, labels)

    filters = g._call("GET", "/settings/filters").get("filter", [])
    gaps = check_filters(filters, labels)

    if AS_JSON:
        print(json.dumps({"filters_checked": len(filters), "gaps": gaps}, indent=2))
        return len({gp["code"] for gp in gaps})

    print(f"gmail-filter-parity -- {len(filters)} filter(s) checked")
    if not gaps:
        print("0 gaps")
        return 0
    for code in sorted({gp["code"] for gp in gaps}):
        rows = [gp for gp in gaps if gp["code"] == code]
        print(f"\n{code} -- {len(rows)} finding(s)")
        for gp in rows:
            print(f"  filter {gp['filter']}")
            print(f"    {gp['detail']}")
            print(f"    criteria: {gp['criteria']}")
            print(f"    fix: {gp['fix']}")
    print(f"\n{len(gaps)} gap(s) across {len({gp['code'] for gp in gaps})} check(s)")
    return len({gp["code"] for gp in gaps})


if __name__ == "__main__":
    sys.exit(main())
