#!/usr/bin/env python3
"""cc-sql.py -- run SQL against the CC Supabase (zhexcaflgahdcbzvbyfq) via the
Supabase Management API. For DDL / migrations / admin queries.

Usage:
  cc-sql.py "SELECT count(*) FROM drive_files"
  cc-sql.py < migration.sql
"""
import sys, json, urllib.request, urllib.error
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

# env-first, file-fallback (matches the clean-name secret convention). Lets a Railway cron that
# only has SUPABASE_TOKEN in its env write via cc-sql.py without materialising a token file.
TOK = (os.environ.get("SUPABASE_TOKEN") or "").strip() or open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
REF = "zhexcaflgahdcbzvbyfq"
# Same guard as lg-sql.py: a flag on the command line must never be mistaken for the QUERY.
# `cc-sql.py < file.sql --reason "..."` would otherwise send "--reason" to Postgres, which reads it
# as a comment, runs nothing, and returns [] exactly like a successful write while the file on stdin
# is never read. A silent no-op that looks like success. Caught in lg-sql.py on 30 Jul 2026 after an
# UPDATE reported success and changed nothing; fixed here too because it is the same fault.
_FLAGS_WITH_VALUE = ("--reason",)
_args, _i = [], 1
while _i < len(sys.argv):
    a = sys.argv[_i]
    if a in _FLAGS_WITH_VALUE:
        _i += 2
        continue
    if a.startswith("--"):
        _i += 1
        continue
    _args.append(a)
    _i += 1

sql = _args[0] if _args else sys.stdin.read()

if not sql.strip():
    sys.exit("cc-sql: no SQL given. Pass it as an argument or pipe/redirect it on stdin.")

# REFUSE --ref. cc-sql is hard-wired to the Command Centre project, but six data_map rows said
# "cc-sql against rsczwfstwkthaybxhszy" (the Sygma Platform) -- so following the routing literally
# produced `cc-sql.py "<sql>" --ref rsczwfstwkthaybxhszy`, which the flag-stripper above silently
# swallowed and then answered from the CC. No error. The trap is public.staff_directory, which
# exists in BOTH databases: the CC's is a contact-card mirror, the platform's is the staff SSOT, so
# the wrong-database answer comes back populated and plausible. Added 5 Aug 2026 after a session
# reported fleet and staff facts that had never been looked up in either place.
if "--ref" in sys.argv[1:]:
    _rest = sys.argv[1:]
    _k = _rest.index("--ref")
    _want = _rest[_k + 1] if _k + 1 < len(_rest) else ""
    sys.exit(
        f"cc-sql: REFUSED -- cc-sql.py only ever talks to the Command Centre project ({REF}); it "
        f"cannot target {_want or 'another project'} and would have answered from the CC instead.\n"
        f"  Use: VAULT=$VAULT python3 $VAULT/lg-sql.py --ref {_want or '<project-ref>'} \"<sql>\"\n"
        f"  Sygma Platform (staff, fleet, delegates, certificates, training customers) = rsczwfstwkthaybxhszy"
    )
req = urllib.request.Request(
    f"https://api.supabase.com/v1/projects/{REF}/database/query",
    data=json.dumps({"query": sql}).encode(),
    headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
    method="POST",
)
try:
    print(urllib.request.urlopen(req, timeout=90).read().decode())
except urllib.error.HTTPError as e:
    print("ERROR", e.code, e.read().decode()); sys.exit(1)