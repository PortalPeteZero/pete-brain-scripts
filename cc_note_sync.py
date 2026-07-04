#!/usr/bin/env python3
"""cc_note_sync.py — read/write a `vault_notes` note body by vault_path (post-cutover: the
canonical docs — connections.md, external-service-routing.md — live in the DB, NOT on disk;
`Library/` is git-ignored and absent on Railway). Shared by the registry regen tools and
connection-parity.py so autogen blocks can be regenerated one-command against the DB.

  from cc_note_sync import fetch_body, splice_block, write_body
  body = fetch_body("Library/processes/connections.md")
  new  = splice_block(body, START_MARK, END_MARK, new_block)   # replace between markers (inclusive)
  write_body("Library/processes/connections.md", new)          # PATCH + null embedded_hash to re-embed
"""
import json, os, urllib.request, urllib.parse, urllib.error

VAULT = os.environ.get("VAULT", "/tmp/pbs")
_SEC = f"{VAULT}/Library/processes/secrets"
_k = json.load(open(f"{_SEC}/command-centre-supabase-keys.json"))
_URL, _SR = _k["url"], _k["service_role_key"]
_H = {"apikey": _SR, "Authorization": f"Bearer {_SR}", "Content-Type": "application/json"}


def fetch_body(vault_path):
    """Return the note body for vault_path, or None if the note doesn't exist."""
    q = urllib.parse.quote(vault_path, safe="")
    req = urllib.request.Request(
        f"{_URL}/rest/v1/vault_notes?select=body&vault_path=eq.{q}", headers=_H)
    rows = json.loads(urllib.request.urlopen(req, timeout=60).read())
    return rows[0]["body"] if rows else None


def write_body(vault_path, body):
    """PATCH the note body and NULL embedded_hash so the hourly embedder re-stamps the vector."""
    q = urllib.parse.quote(vault_path, safe="")
    payload = json.dumps({"body": body, "embedded_hash": None}).encode()
    h = {**_H, "Prefer": "return=minimal"}
    req = urllib.request.Request(
        f"{_URL}/rest/v1/vault_notes?vault_path=eq.{q}", data=payload, headers=h, method="PATCH")
    urllib.request.urlopen(req, timeout=60)


def splice_block(body, start_mark, end_mark, new_block):
    """Replace everything between start_mark and end_mark (inclusive) with new_block.
    If the markers aren't present, append new_block at the end. new_block must itself
    contain the markers."""
    i = body.find(start_mark)
    j = body.find(end_mark)
    if i == -1 or j == -1 or j < i:
        sep = "" if body.endswith("\n") else "\n"
        return body + sep + "\n" + new_block + "\n"
    return body[:i] + new_block + body[j + len(end_mark):]
