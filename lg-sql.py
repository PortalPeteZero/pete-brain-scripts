#!/usr/bin/env python3
"""lg-sql.py -- run SQL against the LeakGuard CRM Supabase (uuhzjytscifrpuqpfrdc) via the
Supabase Management API. For DDL / migrations / admin queries.

Usage:
  lg-sql.py "SELECT count(*) FROM customers"
  lg-sql.py --ref xyspexrszjvthlwawcnq "SELECT 1"    # any Sygma Supabase project by ref
  lg-sql.py < migration.sql

Known refs: LeakGuard CRM uuhzjytscifrpuqpfrdc (default) - Water Knowledge xyspexrszjvthlwawcnq -
Canary Detect olmpxfdkzqnmjifsbcne - command-centre zhexcaflgahdcbzvbyfq (use cc-sql.py for that).
"""
import sys, json, urllib.request, urllib.error, os

VAULT = os.environ.get("VAULT", "/tmp/pbs")
TOK = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()

args = sys.argv[1:]
REF = "uuhzjytscifrpuqpfrdc"
if args and args[0] == "--ref":
    REF = args[1]
    args = args[2:]
sql = args[0] if args else sys.stdin.read()

req = urllib.request.Request(
    f"https://api.supabase.com/v1/projects/{REF}/database/query",
    data=json.dumps({"query": sql}).encode(),
    headers={
        "Authorization": f"Bearer {TOK}",
        "Content-Type": "application/json",
        # The Management API rejects default urllib UAs -- keep a browser UA.
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    },
    method="POST",
)
try:
    print(urllib.request.urlopen(req, timeout=120).read().decode())
except urllib.error.HTTPError as e:
    print("ERROR", e.code, e.read().decode())
    sys.exit(1)
