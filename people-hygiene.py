#!/usr/bin/env python3
"""people-hygiene.py -- DEPRECATED forwarder. The real tool is `people.py`.

Six tools did one job, which is why the people system kept getting skipped (Pete, 26 Jul 2026).
They are now ONE command. This file stays only so anything still calling the old name keeps
working, and it is deleted once nothing has fired it for a full working session.

  people-hygiene.py <args>     ->  people check <args>
  people-hygiene.py            ->  people check (runs the report, exit 0 -- unchanged)
  people-hygiene.py -h|--help  ->  people check usage, exit 0

Run the real thing:  VAULT=/tmp/pbs python3 /tmp/pbs/people.py check

The check is unchanged: report-only, records its own line to daily_log, mutates no domain data,
and exits 0 whenever it RAN. Finding drift is the tool working. `people check --self-test` is the
runnable gate that proves the whole command still behaves.

THREE THINGS THIS FILE MUST GET RIGHT, each already paid for:
  1. The deprecation line goes to STDERR, never stdout -- the --json form is machine-read.
  2. The VERB is translated (this tool had none) and everything else forwarded unchanged.
  3. The child's exit code is passed through.
"""
import os
import subprocess
import sys

VAULT = os.environ.get("VAULT", "/tmp/pbs")
PEOPLE = os.path.join(VAULT, "people.py")
OLD = "people-hygiene.py"


def log_firing(argv):
    """Record that a deprecated name was used, so retiring these is a measurable decision rather
    than a feeling. The deprecation line goes to stderr precisely so nothing captures it -- this
    is the record that makes the retirement gate observable.

    Note this shim therefore writes TWO daily_log rows per firing: this one, plus the check's own
    report line. That is expected.

    $r$ dollar-quoting, not '...': a single-quoted literal turns any apostrophe into a syntax
    error. capture_output because the SQL helper prints its errors to STDOUT, and a stray line
    there would be read as data by anything parsing the --json output. Every failure is swallowed
    -- a slow or broken log line must never block or abort the forward.
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
    print(f"{OLD} is DEPRECATED -- use: people.py check", file=sys.stderr)
    log_firing(argv)
    # every form maps the same way: no args -> the report; -h -> `check -h` (usage, exit 0)
    r = subprocess.run([sys.executable, PEOPLE, "check"] + argv,
                       env={**os.environ, "VAULT": VAULT})
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
