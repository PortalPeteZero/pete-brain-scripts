#!/usr/bin/env python3
"""cc-sql.py -- run SQL against the CC Supabase (zhexcaflgahdcbzvbyfq) via the
Supabase Management API. For DDL / migrations / admin queries.

Usage:
  cc-sql.py "SELECT count(*) FROM drive_files"
  cc-sql.py < migration.sql
"""
import sys, json, urllib.request, urllib.error
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

TOK = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
REF = "zhexcaflgahdcbzvbyfq"
sql = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
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