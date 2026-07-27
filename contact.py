#!/usr/bin/env python3
"""contact.py -- DEPRECATED forwarder. The real tool is `people.py`.

Six tools did one job, which is why the people system kept getting skipped (Pete, 26 Jul 2026).
They are now ONE command. This file stays only so anything still calling the old name keeps
working, and it is deleted once nothing has fired it for a full working session.

  contact.py add <args>            ->  people add <args>
  contact.py phone <args>          ->  people phone <args>
  contact.py remove-phone NAME     ->  people phone NAME --remove
  contact.py remove-record <args>  ->  people remove <args>
  contact.py | -h | --help         ->  people usage, exit 0

Run the real thing:  VAULT=/tmp/pbs python3 /tmp/pbs/people.py add "Name" --entity sygma

THREE THINGS THIS FILE MUST GET RIGHT, each already paid for:
  1. The deprecation line goes to STDERR, never stdout. Callers parse stdout as JSON; one stray
     line there is read as an empty result, silently.
  2. The VERB is translated -- "forward argv verbatim" is impossible here, because remove-phone
     becomes a FLAG on another verb. Everything after the verb is forwarded unchanged.
  3. The child's exit code is passed through.
"""
import os
import subprocess
import sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PEOPLE = os.path.join(VAULT, "people.py")
OLD = "contact.py"

# old verb -> (new verb, extra flags appended after the forwarded args)
VERBS = {
    "add": ("add", []),
    "phone": ("phone", []),
    "remove-phone": ("phone", ["--remove"]),
    "remove-record": ("remove", []),
}


def log_firing(argv):
    """Record that a deprecated name was used, so retiring these is a measurable decision rather
    than a feeling. The deprecation line goes to stderr precisely so nothing captures it -- this
    is the record that makes the retirement gate observable.

    $r$ dollar-quoting, not '...': argv carries real contact names and one live record is
    "Sheila Ramsdale 'Guthrun'", which a single-quoted literal turns into a syntax error.
    capture_output because the SQL helper prints its errors to STDOUT, and a stray line there is
    the exact failure rule 1 above exists to prevent. Every failure is swallowed -- a slow or
    broken log line must never block or abort the forward.
    """
    try:
        sql = ("INSERT INTO daily_log (date, cron_name, content) VALUES "
               "(current_date, 'people-shim', $r$" + OLD + " " + " ".join(argv) + "$r$);")
        subprocess.run([sys.executable, os.path.join(VAULT, "cc-sql.py")], input=sql,
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "VAULT": VAULT})
    except Exception:
        pass


def main():
    argv = sys.argv[1:]
    print(f"{OLD} is DEPRECATED -- use: people.py add | phone | remove", file=sys.stderr)
    log_firing(argv)

    if not argv or argv[0] in ("-h", "--help"):
        forwarded = []                                  # bare `people.py` prints usage, exit 0
    elif argv[0] in VERBS:
        verb, extra = VERBS[argv[0]]
        forwarded = [verb] + argv[1:] + extra
    else:
        forwarded = argv                                # let people.py report the unknown command

    r = subprocess.run([sys.executable, PEOPLE] + forwarded,
                       env={**os.environ, "VAULT": VAULT})
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
