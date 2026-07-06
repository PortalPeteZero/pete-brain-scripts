#!/usr/bin/env python3
"""cc-save.py — the one idempotent "persist this note to vault_notes" helper (F3 fix, 2026-07).

The failure this closes: skills documented saving a session-plan via cc-knowledge-ingest.py, but that
tool SKIPS ephemeral types (session-plan is one) and prints "0 notes ingested" — so the plan was
silently dropped and stayed "unsaved" while the skill reported done.

cc-save ALWAYS persists, bypassing the bulk-ingest ephemeral skip, by reusing the EXACT same row
builder and upsert as cc-knowledge-ingest (single-sourced — no divergent vault_path logic):
  • vault_path is `os.path.relpath(path, VAULT)`, KEEPING the top-level container (`Library/…`,
    `Projects/…`, `Personal/…`). The DB stores the prefixed form (362 `Library/` rows), so keeping the
    prefix makes `on_conflict=vault_path` hit the same row a fresh cc-knowledge-ingest would — no
    duplicate. (Stripping it was the bug that would have re-created the F3 bloat.)
  • upsert = POST vault_notes?on_conflict=vault_path with resolution=merge-duplicates → idempotent.

Usage:
  VAULT=/tmp/pbs python3 cc-save.py <file.md> [<file2.md> ...]
  # or, from Python:  import cc-save via importlib; cc_save.save_file(path) -> vault_path
  #                   cc_save.upsert([row_dict])   # low-level, used by cc-park.py (DRY)

Embedding is left to the hourly cc-embedder cron (same as cc-knowledge-ingest), so save stays fast and
network-light; pass --embed to trigger an immediate embed pass.
"""
import os, sys, importlib.util, subprocess

VAULT = os.environ.get("VAULT", "/tmp/pbs")


def _ingest_module():
    """Load cc-knowledge-ingest.py (dash-named) for its row_for/post — importable since its CLI walk
    is guarded under __main__, so importing has no side effect beyond reading the CC keys."""
    path = os.path.join(VAULT, "cc-knowledge-ingest.py")
    spec = importlib.util.spec_from_file_location("cc_knowledge_ingest", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_cki = None
def _cki_mod():
    global _cki
    if _cki is None:
        _cki = _ingest_module()
    return _cki


def upsert(rows):
    """Low-level idempotent upsert of pre-built vault_notes rows (on_conflict=vault_path). Used by
    cc-park.py so there is ONE upsert path. Raises on HTTP error (caller decides)."""
    _cki_mod().post(rows)


def save_file(path):
    """Persist a single .md file to vault_notes, ALWAYS (ignores the bulk ephemeral skip). Returns the
    canonical, container-prefixed vault_path that was written."""
    path = os.path.abspath(path)
    row = _cki_mod().row_for(path)          # same builder as cc-knowledge-ingest → same vault_path
    upsert([row])
    return row["vault_path"]


def _embed():
    try:
        subprocess.run([sys.executable, os.path.join(VAULT, "cc-embedder.py")],
                       env={**os.environ, "VAULT": VAULT}, capture_output=True, timeout=120)
    except Exception:
        pass


def main(argv):
    do_embed = "--embed" in argv
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print("usage: cc-save.py <file.md> [<file2.md> ...] [--embed]", file=sys.stderr)
        return 2
    rc = 0
    for f in files:
        if not os.path.isfile(f):
            print(f"cc-save: not a file: {f}", file=sys.stderr); rc = 1; continue
        try:
            vp = save_file(f)
            print(f"SAVED: {vp}")
        except Exception as e:
            print(f"cc-save FAILED for {f}: {type(e).__name__}: {e}", file=sys.stderr); rc = 1
    if do_embed and rc == 0:
        _embed()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
