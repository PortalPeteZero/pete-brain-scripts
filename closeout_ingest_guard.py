#!/usr/bin/env python3
"""closeout_ingest_guard.py -- the B1 collision check closeout runs BEFORE it ingests a
knowledge note this session authored.

WHY: cc-knowledge-ingest.py upserts on_conflict=vault_path -- a silent overwrite. If a
DIFFERENT note already sits at the same vault_path (a parallel session wrote it, or a
name clash), a blind re-ingest would clobber it. This guard does the pre-ingest SELECT
the ingest tool doesn't, and classifies:

  NEW        no row at that vault_path                      -> safe to ingest
  IDENTICAL  same content already there                     -> safe (idempotent no-op)
  UPDATE     same logical note (slug/stem), new content     -> safe: my own note, updated
  COLLISION  a DIFFERENT note (different slug) already there -> STOP, surface to Pete

Exit codes: 0 = safe (NEW/IDENTICAL/UPDATE), 4 = COLLISION (do not overwrite).

  python3 closeout_ingest_guard.py <local.md> [--vault-root /tmp/pbs] [--json]
"""
import os, sys, re, json, subprocess

VAULT_DEFAULT = os.environ.get("VAULT", "/tmp/pbs")
_BANNER_RE = re.compile(r"<!--\s*PLAN-LIFECYCLE-BANNER\s*-->.*?(?:\n\n|\Z)", re.S | re.I)
_FM_RE = re.compile(r"^---\n.*?\n---\n", re.S)


def _fm_and_body(text):
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm_block = m.group(0)
    body = text[m.end():]
    fm = {}
    for line in fm_block.splitlines():
        mm = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if mm:
            fm[mm.group(1).strip()] = mm.group(2).strip().strip('"').strip("'")
    return fm, body


def _norm(body):
    """Normalise for content comparison: drop any lifecycle banner, lowercase, collapse
    whitespace. Banner is stripped because ingest injects it into stored bodies but a
    freshly-authored local file has none."""
    b = _BANNER_RE.sub("", body or "")
    b = re.sub(r"\s+", " ", b).strip().lower()
    return b


def _fp(body):
    import hashlib
    return hashlib.sha256(_norm(body).encode()).hexdigest()


def _query_existing(vault_path):
    """Return the existing row (slug,title,body,updated_at) at vault_path, or None."""
    sql = ("SELECT slug, title, COALESCE(body,'') AS body, updated_at FROM vault_notes "
           "WHERE vault_path = '%s' LIMIT 1" % vault_path.replace("'", "''"))
    try:
        out = subprocess.run(["python3", os.path.join(VAULT_DEFAULT, "cc-sql.py"), sql],
                             capture_output=True, text=True, timeout=60,
                             env={**os.environ, "VAULT": VAULT_DEFAULT})
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"_error": f"query failed: {e}"}
    if out.returncode != 0:
        return {"_error": f"cc-sql exit {out.returncode}: {out.stderr.strip()[:200]}"}
    try:
        rows = json.loads(out.stdout or "[]")
    except (ValueError, json.JSONDecodeError):
        return {"_error": f"unparseable cc-sql output: {out.stdout[:200]}"}
    return rows[0] if rows else None


def check(local_path, vault_root=VAULT_DEFAULT):
    # realpath (not abspath) both sides: on macOS /tmp -> /private/tmp, so an abspath mismatch
    # would false-reject a valid file or (elsewhere) build a ../../private/tmp vault_path. Resolve both.
    local_path = os.path.realpath(local_path)
    vault_root = os.path.realpath(vault_root)
    if not local_path.startswith(vault_root + os.sep):
        return {"verdict": "NOT_INGESTABLE", "safe": False,
                "detail": f"{local_path} is NOT under VAULT ({vault_root}); the ingest tool only "
                          "walks /tmp/pbs, so this note would never reach the cloud. Move it under "
                          "/tmp/pbs/<correct home> first."}
    vault_path = os.path.relpath(local_path, vault_root)
    stem = os.path.splitext(os.path.basename(local_path))[0]
    try:
        text = open(local_path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        return {"verdict": "READ_ERROR", "safe": False, "detail": str(e), "vault_path": vault_path}
    fm, body = _fm_and_body(text)
    new_slug = fm.get("slug") or stem

    existing = _query_existing(vault_path)
    if isinstance(existing, dict) and existing.get("_error"):
        return {"verdict": "CHECK_FAILED", "safe": False, "vault_path": vault_path,
                "detail": existing["_error"] + " -- could not verify; surface rather than blind-ingest."}
    if not existing:
        return {"verdict": "NEW", "safe": True, "vault_path": vault_path, "slug": new_slug}
    same_content = _fp(body) == _fp(existing.get("body", ""))
    ex_slug = existing.get("slug") or ""
    if same_content:
        return {"verdict": "IDENTICAL", "safe": True, "vault_path": vault_path, "slug": new_slug}
    if ex_slug and ex_slug == new_slug:
        return {"verdict": "UPDATE", "safe": True, "vault_path": vault_path, "slug": new_slug,
                "detail": "same logical note (slug matches), new content -- your own update."}
    return {"verdict": "COLLISION", "safe": False, "vault_path": vault_path,
            "new_slug": new_slug, "existing_slug": ex_slug,
            "existing_title": existing.get("title"), "existing_updated_at": existing.get("updated_at"),
            "detail": "a DIFFERENT note already occupies this vault_path (slug '%s' vs new '%s', updated %s). "
                      "Ingesting would silently overwrite it. STOP -- rename your note or confirm with Pete."
                      % (ex_slug, new_slug, existing.get("updated_at"))}


def _main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__); sys.exit(1)
    vault_root = VAULT_DEFAULT
    if "--vault-root" in args:
        i = args.index("--vault-root"); vault_root = args[i + 1]; del args[i:i + 2]
    res = check(args[0], vault_root)
    if as_json:
        print(json.dumps(res))
    else:
        print(f"{res['verdict']}: {res.get('vault_path','')}")
        if res.get("detail"):
            print(f"  {res['detail']}")
    sys.exit(0 if res.get("safe") else 4)


if __name__ == "__main__":
    _main()
