#!/usr/bin/env python3
"""verified-facts-stop-hook.py -- refuse an ABSENCE claim I never actually checked.

Pete, 27 Jul 2026: "Why does this keep happening ... This isn't just the calendar you admitted
you did it with bank."

THE FAILURE, stated once. Twice in one day I asserted something about a thing using data that
was merely NEAR it, instead of going to the thing itself:

  · I read bank rows + a balance sheet and asserted what an AGREEMENT said. The agreement was
    one email away and said the opposite. (Payments had not risen; the agreement had ENDED.)
  · I read a CC mirror table and told Pete a trainer's diary was "not available to me".
    calendar-api.py had every trainer's calendar. He then sent a rebuke to that employee on a
    premise I had wrongly called uncheckable.

Where I was RIGHT all day, I had the actual document open -- the insurance schedule, the
settlement letter, the lease schedules, the signed P11D. I was never wrong reading the thing
itself. I was wrong every time I reasoned about a thing from something adjacent to it.

WHY TEXT COULDN'T FIX IT: the SSOT-FIRST instruction fires on EVERY user message and says
precisely this. data_map already said "Google Calendar (source of truth) + CC mirror
public.calendar_events". Both were read past all day. A rule that cannot refuse is a wish.

WHAT THIS REFUSES: a reply that claims something is ABSENT or UNAVAILABLE in a domain that has
a known primary source, when this session never called that source. Not "don't be wrong" --
that isn't checkable. Just: don't report an absence you never went and looked for.

Deliberately NARROW. It does not police causal claims (too noisy to detect, and a noisy gate
gets switched off -- see the outbound-approval gate built and reverted the same afternoon).

  echo '{"transcript_path": "..."}' | python3 verified-facts-stop-hook.py
  python3 verified-facts-stop-hook.py --test <transcript.jsonl>   # measure before wiring
"""
import json, os, re, sys

# A domain is guarded only where a PRIMARY source exists and is one command away.
DOMAINS = [
    {
        "name": "calendar / diary",
        # what the reply is talking about
        "subject": r"\b(calendar|diar(?:y|ies)|schedule|what'?s on|booked in|training (?:on|today)|"
                   r"day off|free day|trainer'?s? day|his day|her day|their day)\b",
        # the tool call that would have answered it properly
        "primary": r"calendar-api\.py",
        "how": 'VAULT=/tmp/pbs python3 /tmp/pbs/calendar-api.py events <person>@sygma-solutions.com <from> <to>\n'
               '    every staff calendar: calendar-api.py calendars',
    },
    {
        "name": "a person",
        "subject": r"\b(phone number|mobile|email address|contact details|do we have (?:a|an|any)\b)",
        "primary": r"people\.py|whois\.py",
        "how": 'VAULT=/tmp/pbs python3 /tmp/pbs/people.py find "<name or number>"  (asks all four stores)',
    },
    {
        "name": "where something lives",
        "subject": r"\b(where (?:does|do|is|are)\b.{0,30}\blive|which system holds|what system stores)\b",
        "primary": r"whereis\.py",
        "how": 'VAULT=/tmp/pbs python3 /tmp/pbs/whereis.py "<thing>"',
    },
]

# The claim shape that has burned us: an absence, stated as fact.
ABSENCE = re.compile(
    r"\b(?:"
    r"no \w{0,12} ?\w{0,12} ?(?:data|record|entry|information|visibility|access)"   # "no trainer diary data"
    r"|no way (?:for me )?to"
    r"|not something I (?:checked|verified|looked at)"
    r"|(?:only|just) (?:mirrors|has|holds)\b.{0,40}\bnot\b"
    r"|nothing (?:in|on|recorded|held|available)"
    r"|(?:not|isn'?t|aren'?t) (?:available|recorded|held|visible|accessible)(?: to me)?"
    r"|(?:can'?t|cannot|couldn'?t) (?:see|check|verify|access|tell)"
    r"|don'?t have (?:access|visibility|a way)"
    r"|(?:have )?no (?:visibility|access)"
    r")\b", re.I)


def read_transcript(path):
    """Return (assistant_text_of_final_reply, all_tool_input_text_this_session)."""
    replies, tools = [], []
    try:
        with open(path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                msg = e.get("message") or {}
                content = msg.get("content")
                if msg.get("role") == "assistant" and isinstance(content, list):
                    txt = "".join(c.get("text", "") for c in content if c.get("type") == "text")
                    if txt.strip():
                        replies.append(txt)
                    for c in content:
                        if c.get("type") == "tool_use":
                            tools.append(json.dumps(c.get("input", ""))[:4000])
    except Exception:
        return "", ""
    return (replies[-1] if replies else ""), "\n".join(tools)


# Saying WHICH source you tried is the honest form of an absence and must never be blocked --
# "I can't tell from Xero whether it's been paid" names the system and its limit. Only the bare
# "not available to me", with no source named, is the failure. Measured 27 Jul: without this,
# 2 of 4 hits were this honest shape.
NAMED_SOURCE = re.compile(
    r"\b(?:from|in|on|via|checked|searched|queried|read|asked)\s+"
    r"(?:the\s+)?(?:xero|gmail|drive|dvla|odoo|novuna|the agreement|the contract|the letter|"
    r"the schedule|the return|the policy|companies house|the platform|the portal|"
    r"[a-z-]+\.py|[a-z_]+\.[a-z_]+)\b", re.I)


def evaluate(reply, tool_text):
    """Return a list of blocking findings."""
    if not reply or not ABSENCE.search(reply):
        return []
    if NAMED_SOURCE.search(reply):
        return []
    out = []
    for d in DOMAINS:
        if not re.search(d["subject"], reply, re.I):
            continue
        if re.search(d["primary"], tool_text):
            continue          # the primary source WAS consulted -- the absence is honest
        out.append(d)
    return out


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)                                   # fail open
    reply, tools = read_transcript(payload.get("transcript_path", ""))
    findings = evaluate(reply, tools)
    if not findings:
        sys.exit(0)
    d = findings[0]
    sys.stderr.write(
        f"BLOCKED by verified-facts: you are telling Pete something about {d['name']} is "
        f"unavailable, and this session never asked the source.\n"
        f"  Ask it first:\n    {d['how']}\n"
        f"  An empty answer from the wrong system is NOT an absence of fact. On 27 Jul 2026 this "
        f"exact claim about a trainer's diary was false, and a rebuke went to that employee "
        f"because of it.\n"
        f"  If you truly cannot check, say WHICH source you tried and why it failed -- never a "
        f"bare 'not available to me'.\n")
    sys.exit(2)


if __name__ == "__main__":
    if "--test" in sys.argv:
        path = sys.argv[sys.argv.index("--test") + 1]
        reply, tools = read_transcript(path)
        # measure over EVERY assistant reply in the transcript, not just the last
        # A reply is judged ONLY against tools called before it. Crediting a later call is
        # how the first measurement scored a false 0/245 -- the calendar was consulted an hour
        # after the wrong claim, which is exactly the failure, not a defence.
        replies, seen = [], []
        with open(path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                m = e.get("message") or {}
                if m.get("role") == "assistant" and isinstance(m.get("content"), list):
                    for c in m["content"]:
                        if c.get("type") == "tool_use":
                            seen.append(json.dumps(c.get("input", ""))[:4000])
                    t = "".join(c.get("text", "") for c in m["content"] if c.get("type") == "text")
                    if t.strip():
                        replies.append((t, "\n".join(seen)))
        fired = 0
        for i, (r, tools_before) in enumerate(replies):
            f_ = evaluate(r, tools_before)
            if f_:
                fired += 1
                snippet = ABSENCE.search(r)
                print(f"  reply #{i+1}: would BLOCK [{f_[0]['name']}] -- "
                      f"...{r[max(0, snippet.start()-70):snippet.start()+80].strip()}...")
        print(f"\n{len(replies)} assistant replies scanned, {fired} would have been blocked.")
        sys.exit(0)
    main()
