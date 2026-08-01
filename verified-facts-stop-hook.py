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
import json, os, re, subprocess, sys, time

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

# --- capability denial (added 29 Jul 2026) ------------------------------------------------
# The ABSENCE shapes above are about DATA ("no record of X"). This one is about MY OWN REACH:
# "I can only issue Google invites", "I'm not able to create a Teams meeting". Different
# sentence, same failure, and the existing shapes do not match it.
#
# THE FAILURE: on 29 Jul 2026 I told Pete I could only issue Google Calendar invites and asked
# Clancy to set the Teams meeting up their end. `teams-api.py` was in the helper registry, its
# docstring said exactly how to do it, there was a lesson banked on 26 Jul saying the same, and
# the token refreshed first time. Pete had spent a session giving me that access weeks earlier.
# I asserted a limit on myself from memory and never looked.
#
# WHY NOT A NEW DOMAINS ENTRY: a hardcoded "teams" row fixes teams and nothing else. There are
# 290 helpers. This resolves the claim against `public.helpers` itself, so a helper added
# tomorrow is covered the day it lands, with nobody maintaining a list.
CAPABILITY = re.compile(
    r"\b(?:"
    r"I (?:can|could) only\b"
    r"|(?:I'?m|I am|I|we'?re|we are|we) (?:can'?t|cannot|not able to|am not able to|are not able to|"
    r"do not have a way to|don'?t have a way to)\s+"
    r"(?:create|make|mint|issue|send|set ?up|generate|build|book|raise|post)"
    r"|(?:there|that) is no way (?:for me )?to\s+(?:create|make|mint|issue|send|set ?up|generate)"
    r"|(?:is|are) (?:not|n'?t) something I can (?:create|make|do|send|issue)"
    r")", re.I)

_HELPER_CACHE = os.path.expanduser("~/.config/pete-cc/helper-index.json")
_CACHE_TTL = 6 * 3600

# Words that are never the subject of a capability claim -- keeps the candidate set small.
_STOP = {"the", "a", "an", "you", "your", "our", "them", "their", "this", "that", "it", "one",
         "only", "just", "and", "or", "but", "from", "with", "into", "for", "can", "not",
         "i", "we", "me", "my", "is", "are", "be", "to", "of", "in", "on", "at", "so", "up",
         "google", "invite", "invites", "meeting", "meetings", "email", "emails", "here",
         # The VERBS in the CAPABILITY regex itself. Without these, "I can't SEND the remittance
         # through Xero" matches on `send` -> ee-send.py and blocks a perfectly honest sentence
         # that already names the system it tried (caught in test, 29 Jul 2026). A capability
         # claim is about the SYSTEM, never the verb.
         "send", "create", "make", "mint", "issue", "generate", "build", "book", "raise",
         "post", "able", "sync", "pull", "push", "read", "write", "check", "find", "log",
         "report", "save", "open", "call", "run", "look", "list", "show", "give", "take"}


def _helper_index():
    """{token -> helper name} from public.helpers, cached. Fail-open to {} on any problem:
    a Stop hook that dies, or that adds a network round trip to every reply, gets switched off."""
    try:
        if (os.path.exists(_HELPER_CACHE)
                and (time.time() - os.path.getmtime(_HELPER_CACHE)) < _CACHE_TTL):
            return json.load(open(_HELPER_CACHE))
    except Exception:
        pass
    try:
        vault = os.environ.get("VAULT", "/tmp/pbs")
        r = subprocess.run(["python3", os.path.join(vault, "cc-sql.py"),
                            "SELECT name, what FROM helpers"],
                           capture_output=True, text=True, timeout=25,
                           env={**os.environ, "VAULT": vault})
        rows = json.loads(r.stdout)
        # Rank matters: the block message NAMES the helper, so a wrong name sends me to the wrong
        # tool. `<token>-api.py` is the canonical helper for a system; `<token>.py` next; anything
        # merely CONTAINING the token last. Without this, xero -> remittance-to-xero.py and
        # odoo -> odoo-invoice-es-copy.py instead of their real API helpers (caught in test, 29 Jul).
        def rank(token, name):
            if name == f"{token}-api.py":
                return 0
            if name == f"{token}.py":
                return 1
            return 2 + name.index(token) if token in name else 9
        idx, best = {}, {}
        for row in rows:
            name = row.get("name") or ""
            stem = re.sub(r"(-api)?\.py$", "", name)
            for tok in re.split(r"[-_]", stem):
                tok = tok.lower()
                if len(tok) <= 3 or tok in _STOP:
                    continue
                r_ = rank(tok, name)
                if tok not in best or r_ < best[tok]:
                    best[tok], idx[tok] = r_, name
        os.makedirs(os.path.dirname(_HELPER_CACHE), exist_ok=True)
        json.dump(idx, open(_HELPER_CACHE, "w"))
        return idx
    except Exception:
        return {}


def capability_finding(reply, tool_text):
    """A denial of my own reach, about a thing that HAS a helper this session never ran."""
    m = CAPABILITY.search(reply or "")
    if not m:
        return None
    idx = _helper_index()
    if not idx:
        return None
    # look only in the sentence carrying the claim -- the rest of a long reply is noise
    start = reply.rfind(".", 0, m.start()) + 1
    end = reply.find(".", m.end())
    sentence = reply[start: end if end != -1 else len(reply)]
    # Collect EVERY helper the sentence points at, then block only if NOT ONE of them was
    # consulted. Blocking on the first unconsulted match is too eager: "I can't send the
    # remittance through Xero" after running remittance-to-xero.py would still trip on the word
    # Xero. Consulting a relevant helper and then reporting a limit is the honest shape, exactly
    # as NAMED_SOURCE treats it for absence claims (caught in test, 29 Jul 2026).
    candidates = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", sentence):
        helper = idx.get(tok.lower())
        if not helper:
            continue
        if helper.replace(".py", "") in tool_text:
            return None                       # a relevant helper WAS run -- claim is grounded
        candidates.append({"term": tok, "helper": helper})
    return candidates[0] if candidates else None


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



# --- UNQUALIFIED COMPARISON (added 1 Aug 2026) --------------------------------------------
# Pete: "when you give me a comparison or analysis i need you to tell me specifically and exactly
# for fucking what!" -- after I wrote "the sites above you sitting at DR 8 and DR 1 ... against your
# DR 20" WITHOUT naming the search term. His words: "does it sit above me for fucking ice creams?"
#
# A ranking, a position or a DR comparison is MEANINGLESS without the exact thing it is for. The
# seo-report skill's RULE ONE has said so since 23 Jul ("Every time you give me stats and a
# position, I need to know for what") and there is a conduct memory saying it too. Both were text.
# Text did not stop it happening again on 1 Aug, in the same session that built a gate for a
# neighbouring version of the same fault. So it becomes a gate.
#
# NARROW BY DESIGN: fires only when the reply makes a rank/position/authority COMPARISON and the
# same paragraph contains no quoted term (" ", ' ', ` `) and no explicit `for the term/query ...`.
# Naming the term anywhere in that paragraph satisfies it -- this polices attribution, not style.
COMPARISON = re.compile(
    r"(?:\b(?:position|ranks?|ranking|outranks?|above (?:us|you|me)|beating (?:us|you)|"
    r"median DR|DR \d|domain rating)\b)", re.I)
NUMERIC = re.compile(r"\d")
QUOTED = re.compile(r"[\"'`“‘]([^\"'`”’\n]{3,60})[\"'`”’]")

def _is_search_phrase(q):
    """Is this quoted string plausibly a SEARCH TERM, or just any old quoted text?

    THE HOLE THIS CLOSES (found within two hours of the gate shipping, 1 Aug 2026): the first
    version accepted ANY quoted string. I then wrote "Sygma is at Ahrefs position 5" in a paragraph
    that happened to contain `"3rd"` and `` `ahrefs_pos=5` `` -- both matched, the gate passed, and
    Pete got the fifth unattributed position of the day. His reply: "number 5 for what? the word
    sygma?"
    A search term is words a human types into Google: two or more of them, letters and spaces only
    (& and digits allowed -- "cat & genny", "hsg47 training", "pas 128 training"). Code tokens,
    ordinals and identifiers are not terms.
    """
    q = q.strip()
    if len(q.split()) < 2:
        return False                                   # "3rd", "sygma", "position"
    if re.search(r"[=_/\\<>{}()\[\]]|\.py\b|--", q):
        return False                                   # ahrefs_pos=5, seo-report.py, --days
    return bool(re.fullmatch(r"[A-Za-z0-9&' ]+", q))
FOR_TERM = re.compile(r"\bfor the (?:term|query|keyword|search)\b", re.I)

def comparison_finding(reply):
    """Return the offending paragraph, or None. Attribution check, not a correctness check."""
    for para in re.split(r"\n\s*\n", reply or ""):
        if not COMPARISON.search(para) or not NUMERIC.search(para):
            continue
        if any(_is_search_phrase(q) for q in QUOTED.findall(para)) or FOR_TERM.search(para):
            continue
        # a markdown table row carrying the term in its own cell is fine
        if para.lstrip().startswith("|"):
            continue
        return para.strip()[:220]
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)                                   # fail open
    reply, tools = read_transcript(payload.get("transcript_path", ""))

    comp = comparison_finding(reply)
    if comp:
        sys.stderr.write(
            "BLOCKED by verified-facts: you are giving Pete a ranking/authority comparison without "
            "naming EXACTLY what it is for.\n"
            f"  The paragraph: {comp}\n"
            "  Every position, rank or DR comparison carries FOUR things:\n"
            "    1. the exact search term, in quotes -- \"cat and genny training\", not \"the biggest term\"\n"
            "    2. the measure  (impression-weighted position, Google UK)\n"
            "    3. the window   (real dates, equal lengths when comparing)\n"
            "    4. the source   (GSC or Ahrefs, never blended)\n"
            "  1 Aug 2026: I wrote 'the sites above you sitting at DR 8 and DR 1 ... against your DR 20'\n"
            "  with no term named. Pete: \"does it sit above me for fucking ice creams?\" -- and the claim\n"
            "  was ALSO cherry-picked: the full SERP for \"cat and genny training\" has a median DR of 23\n"
            "  against Sygma's 20, which is EVEN, not the weak field I described.\n"
            "  Name the term, or do not make the comparison.\n")
        sys.exit(2)

    cap = capability_finding(reply, tools)
    if cap:
        sys.stderr.write(
            f"BLOCKED by verified-facts: you are telling Pete you CANNOT do something involving "
            f"'{cap['term']}', and {cap['helper']} exists for exactly that. This session never ran it.\n"
            f"  Check before you claim a limit on yourself:\n"
            f"    VAULT=/tmp/pbs python3 /tmp/pbs/whereis.py \"{cap['term']}\"\n"
            f"    VAULT=/tmp/pbs python3 /tmp/pbs/{cap['helper']}\n"
            f"  29 Jul 2026: told Pete I could only issue Google invites and asked a customer to set "
            f"the Teams meeting up themselves. teams-api.py was registered, documented, and its token "
            f"refreshed first time. Pete had spent a session granting that access weeks before.\n"
            f"  If the helper genuinely cannot do it, say so NAMING the helper and what it lacks.\n")
        sys.exit(2)

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
