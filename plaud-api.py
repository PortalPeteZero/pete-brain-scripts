#!/usr/bin/env python3
"""plaud-api.py — Plaud recordings/transcripts helper (wraps the official Plaud CLI).

WHY THIS WRAPPER EXISTS (three reasons the bare CLI can't serve this system):

 1. **Zero permanent local footprint.** The CLI hard-codes its config to `join(homedir(), ".plaud")`
    with no env override (verified by reading the 0.3.6 bundle, 30 Jul 2026). This wrapper points
    HOME at a temp dir, so nothing permanent lands on Pete's Mac — the thin-client rule.
 2. **The token lives in the CC, not on disk.** `plaud-tokens.json` is a row in `public.secrets`.
    This materialises it in, and writes it back out.
 3. **BOTH tokens rotate on every refresh** (verified empirically 30 Jul 2026: forced an expiry,
    access_token AND refresh_token both changed). A static snapshot would silently go stale, so the
    write-back is mandatory, not an optimisation.

Auth: OAuth. `login` is interactive (opens a browser) and is the only command needing Pete.
Everything after that refreshes itself.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py login              # one-time, opens a browser
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py me
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py files [-p N] [-s N]
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py recent [--days 30]
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py today
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py search "keyword"
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py transcript <file_id> [-o FILE] [--polished]
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py summary <file_id> [-o FILE]
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py audio <file_id>       # 24h presigned URL
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py pull <file_id> --out DIR   # every stream at once
  VAULT=/tmp/pbs python3 /tmp/pbs/plaud-api.py token-status          # expiry only, no values

`pull` is the one worth knowing: it lands verbatim + polished + outline + summary for one
recording into DIR in a single call. That is the shape the July 2026 bulk export produced by hand.

NOTE ON SEARCH: Plaud's own search is client-side over recording NAMES only, capped at the 500 most
recent (their docs + the bundle agree). It cannot search inside transcripts. To search *content*,
export the text and query `vault_notes` / `drive_files` — that is why the export pipeline still exists.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, urllib.request
from pathlib import Path

SECRET_NAME = "plaud-tokens.json"
PKG = "@plaud-ai/cli@latest"


def _cc():
    url = os.environ.get("CC_SUPABASE_URL")
    key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        kp = Path(os.environ.get("VAULT", "/tmp/pbs")) / "Library/processes/secrets/command-centre-supabase-keys.json"
        if not kp.exists():
            kp = Path.home() / ".config/pete-secrets/command-centre-supabase-keys.json"
        kd = json.loads(kp.read_text())
        url, key = kd["url"], kd.get("service_role_key") or kd["service_role"]
    return url.rstrip("/"), {"apikey": key, "Authorization": "Bearer " + key,
                             "Content-Type": "application/json"}


def _get_token_from_cc():
    base, hdr = _cc()
    req = urllib.request.Request(
        f"{base}/rest/v1/secrets?name=eq.{SECRET_NAME}&select=value", headers=hdr)
    rows = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return rows[0]["value"] if rows else None


def _put_token_to_cc(raw: str):
    base, hdr = _cc()
    body = json.dumps({"value": raw}).encode()
    req = urllib.request.Request(
        f"{base}/rest/v1/secrets?name=eq.{SECRET_NAME}", data=body, method="PATCH",
        headers={**hdr, "Prefer": "return=minimal"})
    urllib.request.urlopen(req, timeout=30)


def _resolve_cli():
    """Prefer an already-cached bundle (fast, offline-tolerant); else fall back to npx."""
    override = os.environ.get("PLAUD_CLI_JS")
    if override and Path(override).exists():
        return ["node", override]
    for root in (Path.home() / ".npm/_npx",):
        if root.exists():
            hits = sorted(root.glob("*/node_modules/@plaud-ai/cli/dist/index.js"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if hits:
                return ["node", str(hits[0])]
    return ["npx", "-y", PKG]


def _run(cli_args, interactive=False):
    """Run the CLI inside a throwaway HOME seeded from the CC; write the token back if it rotated."""
    home = Path(tempfile.mkdtemp(prefix="plaud-home-"))
    try:
        cfg = home / ".plaud"
        cfg.mkdir(parents=True, exist_ok=True)
        tok_path = cfg / "tokens.json"
        before = _get_token_from_cc()
        if before:
            tok_path.write_text(before)
        elif not interactive:
            print("plaud: no stored token — run `plaud-api.py login` first "
                  "(it opens a browser for Pete).", file=sys.stderr)
            return 2

        env = {**os.environ, "HOME": str(home), "PLAUD_NO_UPDATE_NOTIFIER": "1"}
        cmd = _resolve_cli() + cli_args
        r = subprocess.run(cmd, env=env)

        after = tok_path.read_text() if tok_path.exists() else None
        if after and after != before:
            _put_token_to_cc(after) if before else _store_new(after)
            print("plaud: token refreshed in the CC (both tokens rotate on refresh).",
                  file=sys.stderr)
        return r.returncode
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _store_new(raw: str):
    base, hdr = _cc()
    desc = ("Plaud CLI/MCP OAuth token set. BOTH tokens rotate on every refresh — plaud-api.py "
            "writes this row back after any refreshing command. Config note: plaud-cli.")
    body = json.dumps([{"name": SECRET_NAME, "value": raw, "description": desc,
                        "category": "key-json", "encoding": "text"}]).encode()
    req = urllib.request.Request(
        f"{base}/rest/v1/secrets?on_conflict=name", data=body, method="POST",
        headers={**hdr, "Prefer": "resolution=merge-duplicates,return=minimal"})
    urllib.request.urlopen(req, timeout=30)


def token_status():
    raw = _get_token_from_cc()
    if not raw:
        print("plaud: no token stored in the CC — run `login`.")
        return 1
    import datetime
    d = json.loads(raw)
    exp = d.get("expires_at")
    print(f"account token stored in CC secrets/{SECRET_NAME}")
    print(f"  access_token : {len(d.get('access_token',''))} chars")
    print(f"  refresh_token: {len(d.get('refresh_token',''))} chars")
    if exp:
        when = datetime.datetime.fromtimestamp(exp / 1000)
        now = datetime.datetime.now()
        state = "expired — will auto-refresh on next call" if when < now else f"valid for {when-now}"
        print(f"  access expires: {when:%Y-%m-%d %H:%M} ({state})")
    return 0


STREAMS = [("transcript", ["--block", "transaction"], "transcript-verbatim.txt"),
           ("transcript", ["--block", "transaction_polish"], "transcript-cleaned.txt"),
           ("transcript", ["--block", "outline"], "outline.txt"),
           ("summary", [], "summary.md")]


def pull(file_id: str, out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ok, missing = [], []
    for cmd, extra, fname in STREAMS:
        dest = out / fname
        rc = _run([cmd, file_id, *extra, "-o", str(dest)])
        if rc == 0 and dest.exists() and dest.stat().st_size > 0:
            ok.append(f"{fname} ({dest.stat().st_size:,} bytes)")
        else:
            missing.append(fname)
            if dest.exists() and dest.stat().st_size == 0:
                dest.unlink()
    print(f"\nplaud pull {file_id} -> {out}")
    for o in ok:
        print(f"  ok      {o}")
    for m in missing:
        print(f"  absent  {m}   (not every recording carries every stream)")
    return 0 if ok else 1


PASSTHROUGH = {"me", "files", "recent", "today", "file", "audio", "transcript",
               "summary", "search", "login", "logout", "version"}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "token-status":
        return token_status()
    if cmd == "pull":
        ap = argparse.ArgumentParser(prog="plaud-api.py pull")
        ap.add_argument("file_id")
        ap.add_argument("--out", required=True)
        a = ap.parse_args(rest)
        return pull(a.file_id, a.out)
    if cmd in PASSTHROUGH:
        return _run([cmd, *rest], interactive=(cmd == "login"))

    print(f"plaud-api.py: unknown command {cmd!r}\n", file=sys.stderr)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
