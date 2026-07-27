#!/usr/bin/env python3
"""phone-push.py -- DEPRECATED forwarder. The real tool is `people.py`.

Six tools did one job, which is why the people system kept getting skipped (Pete, 26 Jul 2026).
They are now ONE command. This file stays only so anything still calling the old name keeps
working, and it is deleted once nothing has fired it for a full working session.

  phone-push.py <args>     ->  people phone <args>
  phone-push.py            ->  people usage, exit 0
  phone-push.py -h|--help  ->  people usage, exit 0

Run the real thing:  VAULT=/tmp/pbs python3 /tmp/pbs/people.py phone --scope suppliers

The bulk push keeps every rule it had: dry-run by default, --confirm required to write, and every
contact it creates carries the group label, which is the only thing that makes a bad push undoable.

THREE THINGS THIS FILE MUST GET RIGHT, each already paid for:
  1. The deprecation line goes to STDERR, never stdout.
  2. The VERB is translated (this tool had none) and everything else forwarded unchanged.
  3. The child's exit code is passed through.
"""
import os
import subprocess
import sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PEOPLE = os.path.join(VAULT, "people.py")
OLD = "phone-push.py"


def log_firing(argv):
    """Record that a deprecated name was used, so retiring these is a measurable decision rather
    than a feeling. The deprecation line goes to stderr precisely so nothing captures it -- this
    is the record that makes the retirement gate observable.

    $r$ dollar-quoting, not '...': argv carries real contact names and one live record is
    "Sheila Ramsdale 'Guthrun'", which a single-quoted literal turns into a syntax error.
    capture_output because the SQL helper prints its errors to STDOUT, and a stray line there
    would be read as data by a caller parsing stdout. Every failure is swallowed -- a slow or
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
    print(f"{OLD} is DEPRECATED -- use: people.py phone --scope ...", file=sys.stderr)
    log_firing(argv)

    if not argv or "-h" in argv or "--help" in argv:
        forwarded = []                                  # bare `people.py` prints usage, exit 0
    else:
        forwarded = ["phone"] + argv

    r = subprocess.run([sys.executable, PEOPLE] + forwarded,
                       env={**os.environ, "VAULT": VAULT})
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
