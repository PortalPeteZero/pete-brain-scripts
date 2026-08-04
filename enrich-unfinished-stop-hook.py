#!/usr/bin/env python3
"""enrich-unfinished-stop-hook.py — REFUSE to end a turn with an enrichment run half-done.

WHY THIS EXISTS. Pete, 4 Aug 2026: "i am geting really tired of this i want a gate built to stop
you stopping!!!! this is fucking unacceptable." Over one session Claude stopped FIVE times mid-task
to ask whether to continue work that had already been authorised — after "do all", after "go", and
again with 17 of 25 images left to read. Asking is not a neutral act: it hands the cost of the
pause to Pete and leaves the record half-updated, which is worse than either finishing or not
starting.

The rule this enforces: if THIS session started an enrichment run, THIS session finishes it. The
vision queue is the measure — an image is only read when a result file exists with a description
and the has_text/transcription contract satisfied (clancy-dn-vision-queue.py owns that check).

DELIBERATELY NARROW so it can never block an unrelated session:
  * fires ONLY when the work is demonstrably this session's — a vision result written in the last
    NN minutes (default 180). A cold session that never touched enrichment is untouched.
  * fires ONLY on genuinely outstanding items, counted by the queue tool itself, never re-derived.
  * says exactly what is left and the one command to continue.

Override, when stopping really is right (Pete says leave it, the model is out of context, the
source system is down):
  VAULT=/tmp/pbs python3 /tmp/pbs/gate_override.py grant enrich-unfinished --reason "<why>"
"""
import json, os, re, subprocess, sys, time

VAULT = os.environ.get("VAULT", "/tmp/pbs")
WORK = "/tmp/enrich-work"
RESULTS = f"{WORK}/vision/results"
WINDOW_MIN = int(os.environ.get("ENRICH_STOP_WINDOW_MIN", "180"))


INVOKED = re.compile(r"(?:python3|VAULT=)[^\n\"]{0,140}?"
                    r"clancy-dn-(?:vision-queue|enrich)(?:-index|-interpret)?\.py"
                    r"([^\n\"]{0,120})")
# Read-only flags. Asking the queue how it is doing is NOT starting a run -- and this matters more
# than it looks: on 4 Aug 2026 the session INVESTIGATING a false block ran `--check` to see the
# state, which armed the gate against itself. A diagnostic must never become the evidence.
# Test the FIRST flag only: a real command carries shell after it ("--check 2>&1 | tail -6"), so
# requiring the whole tail to be the flag matched nothing and the exclusion never fired.
READ_ONLY = {"check", "stats", "report", "status", "help", "json", "list"}
FIRST_FLAG = re.compile(r"--([a-z][a-z-]*)")


def _is_diagnostic(tail: str) -> bool:
    m = FIRST_FLAG.search(tail or "")
    return bool(m) and m.group(1) in READ_ONLY


def recently_active() -> bool:
    """Did THIS session touch the enrichment? Only then is stopping a failure rather than a no-op.

    FILE MTIMES CANNOT ANSWER THIS. The first version scanned the results directory for anything
    modified in the last 180 minutes and called that "this session". Those results live under
    /tmp/pbs -- the clone EVERY session shares -- so one session running an enrichment fired the
    stop-hook for every other session on the machine. On 4 Aug 2026 it blocked a session that had
    spent the day on HMRC, Xero and bank reconciliation, had never touched Clancy, and was asked to
    finish 17 images another session left at 18:04 the PREVIOUS day. The docstring promised
    "DELIBERATELY NARROW so it can never block an unrelated session"; the implementation could not
    tell one session from another at all.

    Ownership now comes from where it does everywhere else in this system: THIS session's own
    transcript. If the transcript shows this session actually INVOKING the enrichment tools, the run
    is ours and stopping is a failure. If not, the gate stays silent -- matching a bare tool name is
    not enough, or the hook's own blocking message quoted into the transcript re-arms it forever.
    """
    try:
        sys.path.insert(0, VAULT)
        import session_attribution as SA
        _sid, main, is_sub, _why = SA.resolve_transcript()
    except Exception:
        return False                      # cannot prove it is ours -> never block
    if is_sub or not main or not os.path.exists(main):
        return False
    try:
        with open(main, "r", errors="ignore") as fh:
            for line in fh:
                if "clancy-dn-" not in line:
                    continue
                for m in INVOKED.finditer(line):
                    tail = m.group(1) or ""
                    if _is_diagnostic(tail):
                        continue          # a status check is not a run
                    # The hook's OWN blocking message gets written into the transcript, and it
                    # quotes these very commands. Left unguarded, one false block arms the gate
                    # permanently against the session trying to investigate it.
                    if "BLOCKED by enrich-unfinished" in line or "FINISH IT." in line:
                        continue
                    return True
    except OSError:
        return False
    return False


def outstanding():
    """Ask the queue tool — never recount here, or the gate and the tool can disagree."""
    tool = os.path.join(VAULT, "clancy-dn-vision-queue.py")
    if not os.path.exists(tool):
        return None, "clancy-dn-vision-queue.py not found"
    r = subprocess.run([sys.executable, tool, "--check"], capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=120)
    # --check exits 2 when items are outstanding — that is its NORMAL "not clean" signal, not a
    # crash. Treating it as a failure made this gate silently pass in exactly the state it exists
    # to catch (found on its own first run, 4 Aug 2026).
    if r.returncode not in (0, 1, 2):
        return None, f"--check exited {r.returncode}"
    import re
    m = re.search(r"(\d+)\s+outstanding", r.stdout)
    if m:
        return int(m.group(1)), (r.stdout.strip().splitlines() or [""])[0]
    return None, (r.stdout.strip().splitlines() or ["no output"])[-1]


def overridden() -> bool:
    try:
        r = subprocess.run([sys.executable, os.path.join(VAULT, "gate_override.py"),
                            "check", "enrich-unfinished"], capture_output=True, text=True,
                           env={**os.environ, "VAULT": VAULT}, timeout=60)
        return r.returncode == 0 and "granted" in (r.stdout or "").lower()
    except Exception:
        return False


def main():
    if not recently_active():
        sys.exit(0)
    n, detail = outstanding()
    if n is None:
        # Could not check is NOT the same as clean — say so, but never hard-block on a broken check.
        sys.stderr.write(f"[enrich-unfinished] could not verify the vision queue ({detail}) — "
                         f"NOT confirming the enrichment is complete.\n")
        sys.exit(0)
    if n <= 0:
        sys.exit(0)
    if overridden():
        sys.exit(0)
    sys.stderr.write(
        f"BLOCKED by enrich-unfinished: this session started an enrichment run and {n} image(s) are\n"
        f"still unread. {detail}\n\n"
        f"  FINISH IT. Read each outstanding image and write its result file — description, has_text,\n"
        f"  and the transcription where there is text. Then:\n"
        f"    VAULT={VAULT} python3 {VAULT}/clancy-dn-vision-queue.py --check      # must reach 0\n"
        f"    VAULT={VAULT} python3 {VAULT}/clancy-dn-enrich.py --incident <id> --gate\n"
        f"    ... --assemble --upload, then clancy-dn-enrich-index.py --load --promote\n\n"
        f"  Do NOT end the turn to ask whether to carry on — the work was already authorised, and a\n"
        f"  half-read damage publishes a page that says the documents hold nothing when nobody has\n"
        f"  looked. (Pete, 4 Aug 2026: 'i want a gate built to stop you stopping'.)\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
