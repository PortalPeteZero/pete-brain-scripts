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

THE RULES IT ENFORCES
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
  F6  no-broad-archiver-over-mode-a
                      A filter with NO subject narrowing that archives must not sit on the same
                      label and sender as a label-only (Mode A) filter. Gmail applies every
                      matching filter, so the broad archiver wins and the Mode A rule is defeated
                      in silence. Added 27 Jul 2026 after the guard, handed the exact filter that
                      caused that day's incident to prove it would refuse, CREATED it instead:
                      F5 saw the Mode A sibling and called it a way in, F2 exempts pairs that act
                      differently, and between them the hole stayed open.
  F5  alerts-need-a-way-in
                      If a sender's mail is filed under `Alerts`, at least one filter matching that
                      sender must leave its mail IN THE INBOX. Added 27 Jul 2026 after the second
                      incident of the day: Sentry's alert on the Locator Data map outage fired and
                      emailed Pete at 10:45, and a single `from:(md.getsentry.com OR getsentry.com
                      OR sentry.io)` filter stripped INBOX and UNREAD from it before he saw it. F3
                      could not catch it because F3 only protects Briefings. Scoped by the LABEL,
                      not a curated sender list: filing a sender under Alerts is the author saying
                      "this tells me when something breaks". Newsletters and receipts are untouched.

Usage:
  VAULT=/tmp/pbs python3 gmail-filter-parity.py                 # human summary + `0 gaps` / exit=#gap-types
  VAULT=/tmp/pbs python3 gmail-filter-parity.py --json          # machine digest
  VAULT=/tmp/pbs python3 gmail-filter-parity.py --selftest      # regression harness (real historical
                                                                # real cases: 4 that MUST be caught,
                                                                # 3 legitimate setups that MUST NOT be)
  VAULT=/tmp/pbs python3 gmail-filter-parity.py --create \
        --query 'from:(x@y.com) subject:("Weekly Thing")' --label Briefings [--archive]
                                                                # guarded create: runs F1-F6 on the
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
    # `senders` is a SUPERSET of `addresses`, used only by F5. Gmail filters name a sender either
    # as a full address (leakguard@canary-detect.com) or as a bare domain
    # (from:(md.getsentry.com OR sentry.io)) -- and the 27 Jul Sentry filter was the bare-domain
    # kind, so an address-only key silently skipped exactly the case F5 exists for (caught by the
    # selftest, not by reading the code). F1/F2 deliberately keep using `addresses`.
    sender_blob = " ".join(str(crit.get(k, "")) for k in ("from", "query"))
    senders = set(addrs)
    for tok in re.findall(r"[\w.*+-]+", sender_blob):
        t = tok.lower().lstrip("*.")
        if "@" in tok or "." not in t or t in ("subject", "from", "to", "or", "and"):
            continue
        if re.fullmatch(r"[\w.-]+\.[a-z]{2,}", t):
            senders.add(t)
    # TWO keys, deliberately, and the difference is load-bearing:
    #   senders_strict -- addresses plus domains AS WRITTEN. Used by F2. Folding an address into
    #     its domain here is wrong: it would make every filter on a @sygma-solutions.com or
    #     @canary-detect.com address "the same sender" as every other. Measured 27 Jul 2026 --
    #     folding took F2 from 0 findings to 37 on the live set, all of them noise.
    #   senders -- strict plus the folded domains. Used by F5, where "is there ANY way into the
    #     inbox for this service" genuinely wants x@y.com and y.com treated as one service.
    senders_strict = set(senders)
    senders |= {a.split("@", 1)[1] for a in addrs if "@" in a}
    return {"addresses": addrs, "senders": senders, "senders_strict": senders_strict,
            "subjects": subjects, "neg_subjects": neg_subjects, "raw": crit}


def describe(crit):
    return json.dumps(crit, sort_keys=True)[:170]


# ---------------------------------------------------------------- the checks
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
            # Deliberately keyed on `addresses`, NOT the wider `senders`. Re-keying F2 on domains
            # was tried on 27 Jul 2026 and reverted: it took F2 from 0 findings to 36 on the live
            # set, and the findings were wrong -- it read a from:/to:/query trio on one domain (the
            # Clancy filters) as "exact duplicates", when an independent byte-comparison found no
            # identical filters at all. The domain-blindness it leaves behind is closed by F6, which
            # is narrow enough to be right.
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

    # F5 -- an alerting sender must keep at least one way into the inbox.
    #
    # WHY (27 Jul 2026, the second incident of the day): Sentry's alert rule fired on the map
    # outage at 09:45 and emailed Pete at 10:45. He never saw it. A single filter,
    # `from:(md.getsentry.com OR getsentry.com OR sentry.io)`, stripped INBOX *and* UNREAD from
    # every Sentry mail including the alert. The monitoring was never the gap; the mailbox was.
    #
    # F3 could not catch it: F3 only protects the Briefings label. The general class is "mail Pete
    # must see is archived on arrival", and a service that only ever writes to Alerts is exactly
    # the mail he must see.
    #
    # SCOPED BY THE LABEL, NOT BY A CURATED SENDER LIST. A filter that files a sender under
    # `Alerts` is the author declaring "this service tells me when something is wrong". If every
    # filter for that sender then archives, there is no path at all for bad news to reach him.
    # Newsletters and receipts are untouched by this rule -- archiving those is the whole point.
    #
    # The known-good shape is a two-filter split, already used for Vercel:
    #   subject:(failed OR paused OR blocked)  -> Alerts, label only   (Mode A, the bad news)
    #   -subject:(failed OR paused OR blocked) -> Alerts, archive      (Mode B, the noise)
    alerting_senders = set()
    for p in parsed:
        if "Alerts" in p["add"]:
            alerting_senders |= p["senders"]
    _f5_seen = set()
    for addr in sorted(alerting_senders):
        matching = [p for p in parsed if addr in p["senders"]]
        if not matching or any("INBOX" not in p["remove_ids"] for p in matching):
            continue  # at least one filter leaves this sender's mail in the inbox -- fine
        if matching[0]["id"] in _f5_seen:
            continue  # one finding per filter, not one per sender key it happens to match
        _f5_seen.add(matching[0]["id"])
        gaps.append({
            "code": "F5", "severity": "advisory", "filter": matching[0]["id"],
            "detail": (f"every filter matching {addr} archives on arrival, and its mail is labelled "
                       f"Alerts. Nothing this service sends can reach the inbox, so a genuine alert "
                       f"is invisible (this is how the 27 Jul Sentry alert was missed)."),
            "criteria": describe(matching[0]["raw"]),
            "fix": ("split it: keep a label-only filter for the bad-news subset (Mode A) and archive "
                    "only the routine remainder (Mode B), as the Vercel filters already do"),
        })

    # F6 -- a broad archiver must not defeat the Mode A rule beside it.
    #
    # FOUND BY TESTING THE GUARD, NOT BY READING IT (27 Jul 2026). After the Sentry filters were
    # split, `--create` was handed the exact archive-everything filter that caused the incident, to
    # prove it would refuse. It CREATED it, live on Pete's mailbox, and it had to be deleted by
    # hand. Two checks each had a reason to stay quiet: F5 saw the new Mode A rule and concluded
    # there was a way into the inbox, and F2 exempts a pair whose halves act differently (the
    # deliberate Vercel tiering). Neither is wrong on its own. Together they left the hole.
    #
    # The truth they both missed: Gmail applies EVERY matching filter. A filter with no narrowing
    # that archives will archive the very mail the narrow label-only rule exists to keep visible.
    # The Mode A rule is not a way in if something broader is archiving over the top of it.
    #
    # Narrow on purpose: same label, overlapping sender, one side with NO subject narrowing that
    # archives, the other a label-only rule. Vercel (subject vs -subject, neither broad) and the
    # calendar tier (broad half does not archive) both stay clean.
    for i, a in enumerate(parsed):
        for b in parsed[i + 1:]:
            if not (set(a["add_ids"]) & set(b["add_ids"])) or not (a["senders"] & b["senders"]):
                continue
            broad_a = not (a["subjects"] or a["neg_subjects"])
            broad_b = not (b["subjects"] or b["neg_subjects"])
            if broad_a == broad_b:
                continue
            broad, narrow = (a, b) if broad_a else (b, a)
            if "INBOX" in broad["remove_ids"] and "INBOX" not in narrow["remove_ids"]:
                names = ", ".join(sorted(labels.get(x, x)
                                         for x in set(a["add_ids"]) & set(b["add_ids"])))
                gaps.append({
                    "code": "F6", "severity": "high", "filter": broad["id"],
                    "detail": (f"archives everything it matches and carries no subject narrowing, while "
                               f"{narrow['id']} is the label-only (Mode A) rule for the same sender on "
                               f"{names}. Gmail applies both, so this filter archives the very mail "
                               f"{narrow['id']} exists to keep visible."),
                    "criteria": describe(broad["raw"]),
                    "fix": (f"give this filter the complementary narrowing to {narrow['id']} (the "
                            f"subject / -subject split), or delete it"),
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
    # The 27 Jul 2026 Sentry case: one filter swallowing an entire alerting service.
    ("MUST CATCH: the Sentry archive-everything filter (alert never reached the inbox)", ["F5"], [
        {"id": "sentry-broad", "criteria": {"query": "from:(md.getsentry.com OR getsentry.com OR sentry.io)"},
         "action": {"addLabelIds": ["Label_AL"], "removeLabelIds": ["UNREAD", "INBOX"]}},
    ]),
    # The guard was handed exactly this on 27 Jul 2026 and CREATED it, live, because the Mode A
    # sibling made F5 see "a way into the inbox". There was none: the broad archiver wins.
    ("MUST CATCH: a broad archiver added alongside the Mode A rule it would defeat", ["F6"], [
        {"id": "sentry-modea-live",
         "criteria": {"query": 'from:(md.getsentry.com OR getsentry.com OR sentry.io) -subject:(Deployed OR "Weekly Report")'},
         "action": {"addLabelIds": ["Label_AL"]}},
        {"id": "broad-archiver-proposed",
         "criteria": {"query": "from:(md.getsentry.com OR getsentry.com OR sentry.io)"},
         "action": {"addLabelIds": ["Label_AL"], "removeLabelIds": ["INBOX"]}},
    ]),
    ("MUST NOT CATCH: the Sentry split that replaced it", [], [
        {"id": "sentry-modea",
         "criteria": {"query": 'from:(md.getsentry.com OR getsentry.com OR sentry.io) -subject:(Deployed OR "Weekly Report")'},
         "action": {"addLabelIds": ["Label_AL"]}},
        {"id": "sentry-modeb",
         "criteria": {"query": 'from:(md.getsentry.com OR getsentry.com OR sentry.io) subject:"Weekly Report"'},
         "action": {"addLabelIds": ["Label_AL"], "removeLabelIds": ["INBOX"]}},
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
        return len({gp["code"] for gp in gaps if gp.get("severity") != "advisory"})

    print(f"gmail-filter-parity -- {len(filters)} filter(s) checked")
    # Advisory findings are SURFACED but never counted as gaps. Measured 27 Jul 2026: F5 returns 9
    # findings on the live set and only ~2 are real (LeakGuard water-usage alerts and UptimeRobot,
    # both genuinely unreachable); the rest are senders merely mis-filed under Alerts, plus one
    # parsing artefact (supabase.co read as a separate service from supabase.com). Counting those
    # as gaps would mean this check never reads clean again, and an audit that always cries wolf
    # gets ignored -- which is how the Sentry filter survived in the first place. F5 stays
    # FAIL-CLOSED on --create, where it is precise, and advisory here.
    enforced = [gp for gp in gaps if gp.get("severity") != "advisory"]
    advisory = [gp for gp in gaps if gp.get("severity") == "advisory"]
    if not enforced:
        print("0 gaps" + (f"  ({len(advisory)} advisory finding(s) below)" if advisory else ""))
    for code in sorted({gp["code"] for gp in gaps}):
        rows = [gp for gp in gaps if gp["code"] == code]
        print(f"\n{code} -- {len(rows)} finding(s)")
        for gp in rows:
            print(f"  filter {gp['filter']}")
            print(f"    {gp['detail']}")
            print(f"    criteria: {gp['criteria']}")
            print(f"    fix: {gp['fix']}")
    if enforced:
        print(f"\n{len(enforced)} gap(s) across {len({gp['code'] for gp in enforced})} check(s)")
    if advisory:
        print(f"{len(advisory)} advisory finding(s) -- surfaced for a decision, not blocking")
    return len({gp["code"] for gp in enforced})
    return len({gp["code"] for gp in gaps})


if __name__ == "__main__":
    sys.exit(main())
