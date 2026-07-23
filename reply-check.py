#!/usr/bin/env python3
"""
reply-check.py — a Claude Code **Stop** hook that checks the reply BEFORE Pete sees it.

Step 2 of [[plan-rules-that-stop-me]]. The plan's thesis: 150 rules exist, 7 are enforced by
machinery, and the other 143 are a list I am supposed to remember. This is the first piece of
machinery for the "manner" rules — the ones with no tool call to intercept, so a PreToolUse guard
cannot reach them. The only place to catch them is the reply itself, on the way out.

Proof it was needed: on 23 Jul 2026 the rule "report in plain English, no jargon" was loaded in
context for an entire session, and the word "corpus" still went out twice — Pete had to ask what it
meant. Loaded is not followed.

WHAT IT ENFORCES (blocks — exit 2, model gets the message and rewrites)
  • corny honesty preambles   feedback_no_corny_honesty_preambles
  • opaque jargon             feedback_report_plain_english / feedback_non_technical_user
  • asking Pete to review code feedback_non_technical_user

WHAT IT ONLY WARNS ABOUT (printed to stderr, exit 0 — never blocks)
  • over-long chat replies    feedback_chat_replies_short_not_reports
  • a "done" claim sitting near an undone list   feedback_done_means_done_no_undone_followups

MEASURED BEFORE SHIPPING (23 Jul 2026, 2,497 real assistant replies from the last 12 sessions).
Pete's standing rule — never ship a fail-closed check without measuring it against real approved
work, because a rule that blocks his own output is worse than no rule at all:

    check              would block   verdict
    corny preamble          0.3%     BLOCK  — precise, no false positives found
    jargon (final list)     4.0%     BLOCK  — every hit inspected, all genuine
    asking to review code   0.0%     BLOCK  — zero cost; unproven but real harm if it happens
    reply over 1800 chars   7.1%     WARN   — Pete asks for long reports; too context-dependent
    done-near-undone        0.5%     WARN   — sampled 4, at least 3 were false positives
    word repetition        12.8%     DROPPED — see below

HONESTY NOTES (documented, not hidden):
  • The repetition rule (feedback_vary_language_no_repetition) DID NOT SURVIVE MEASUREMENT. A
    word-frequency check cannot tell stale prose from a proper noun: its top hits were "frank",
    "ahrefs", "genny", "clancy", "sygma", "drive". Repeating "Sygma" in a reply about Sygma is not
    a violation. That rule stays on the list as a resident rule — it cannot be mechanised this way.
  • "canonical" and "source of truth" are deliberately NOT in the jargon list. They are Pete's own
    system vocabulary — they appear throughout his MAP, his CLAUDE config and the SSOT-FIRST hook.
    Blocking them would fight his own documents.
  • The jargon list is meant to GROW, and there is only one honest way to grow it: when Pete asks
    what a word means, that word goes in. "corpus" is in it because he asked on 23 Jul 2026.
  • Fenced code blocks and blockquotes are stripped before scanning, so quoting a document that
    contains a listed word does not trip it.
  • This sees the final assistant text only. It cannot check tool output or anything mid-turn.
  • It blocks at most ONCE per turn (honours `stop_hook_active`), so it can never trap a session
    in a loop. Second pass always passes.
  • FAIL-OPEN on any internal error: a hook bug must never stop Pete getting an answer.

Exit contract (Claude Code Stop hook): exit 2 + stderr ⇒ block and hand the message back to the
model; exit 0 ⇒ allow (stderr is surfaced but does not block).
"""
import sys, json, os, re

# ---- the rules -----------------------------------------------------------------------------

CORNY = [
    r"\bi'?ll be (honest|straight|frank)\b",
    r"\blet me be (honest|straight|frank)\b",
    r"\bto be (honest|straight|frank) with you\b",
    r"\btruth be told\b",
    r"\bi won'?t sugarcoat\b",
    r"\bi'?m going to be (honest|straight)\b",
    r"\bhonestly,? i\b",
]

# Deliberately excludes "canonical" and "source of truth" — Pete's own system vocabulary.
JARGON = [
    "corpus", "idempotent", "idempotency", "upsert", "orthogonal", "deterministic",
    "taxonomy", "instantiate", "denormalis", "denormaliz", "blast radius", "semantics",
    "heuristic", "provenance",
]

CODE_ASK = [
    r"\breview the (diff|code|pr|pull request|migration)\b",
    r"\bcheck the (diff|code|migration sql|schema change)\b",
    r"\bdoes (this|the) (code|function|query|diff) look\b",
    r"\bhave a look at the (code|diff|pr)\b",
    r"\bcan you (review|check|look at) (this|the) (code|diff|pr|function|query)\b",
]

DONE_CLAIM = r"\b(all done|that'?s done|done and dusted|all five .* complete|100% complete)\b"
UNDONE = r"\b(still (need|to do|outstanding)|remaining|outstanding|not yet|left to do)\b"
LONG_REPLY = 1800


def _strip_quoted(text):
    """Remove fenced code blocks and blockquote lines — quoting a doc is not speaking."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*>.*$", " ", text, flags=re.M)
    return re.sub(r"`[^`]+`", " ", text)


def _last_assistant_text(transcript_path):
    """Final assistant text message from the transcript. Returns '' if it cannot be read."""
    try:
        with open(transcript_path, errors="ignore") as fh:
            lines = fh.readlines()
    except Exception:
        return ""
    for line in reversed(lines):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "assistant":
            continue
        content = o.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        txt = " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
        if txt:
            return txt
    return ""


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open

    # Already blocked once this turn — never block twice, never loop.
    if payload.get("stop_hook_active"):
        return 0

    text = _last_assistant_text(payload.get("transcript_path") or "")
    if not text or len(text) < 80:
        return 0

    scan = _strip_quoted(text)
    low = scan.lower()
    blocks, warns = [], []

    hits = sorted({m.group(0).strip() for p in CORNY for m in re.finditer(p, scan, re.I)})
    if hits:
        blocks.append(
            "Corny honesty preamble: " + ", ".join(f'"{h}"' for h in hits) +
            ". Cut it and state the point directly."
        )

    jw = sorted({w for w in JARGON if re.search(r"\b" + re.escape(w), low)})
    if jw:
        blocks.append(
            "Jargon Pete would have to decode: " + ", ".join(jw) +
            ". Say it in plain English — he is non-technical and has asked for this repeatedly."
        )

    ca = sorted({m.group(0).strip() for p in CODE_ASK for m in re.finditer(p, scan, re.I)})
    if ca:
        blocks.append(
            "Asking Pete to review code: " + ", ".join(f'"{c}"' for c in ca) +
            ". He cannot read diffs or code — you own the verification and report the evidence."
        )

    if len(text) > LONG_REPLY:
        warns.append(
            f"Reply is {len(text)} characters. Pete wants short chat replies with the detail in "
            f"the product, not the message. Fine if he asked for a report."
        )
    if re.search(DONE_CLAIM, text[:400], re.I) and re.search(UNDONE, text, re.I):
        warns.append(
            "Claims something is done near a list of outstanding items. If it is not finished, "
            "do not call it done."
        )

    if warns:
        sys.stderr.write("reply-check (advisory):\n  - " + "\n  - ".join(warns) + "\n")
    if blocks:
        sys.stderr.write(
            "reply-check BLOCKED this reply — rewrite it before sending:\n  - "
            + "\n  - ".join(blocks) + "\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-open, always
