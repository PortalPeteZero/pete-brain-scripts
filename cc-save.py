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


# --- identity-value gate (2026-07-20) ---------------------------------------------------------
# Why: identity/banking values (VAT, company numbers, sort codes, account numbers, IBANs, NIF/CIF)
# are MASTERED in public.entities + public.bank_accounts (/m/entities). A note carrying its own copy
# is a stale mirror waiting to happen — on 20 Jul 2026 a note's "TBD" VAT sat next to a DB that had
# the value, and the note got "fixed" instead of pointed. This gate makes the wrong-place save fail
# at the point of action: saving a note whose body contains a value the DB masters is refused with
# the right home named. Override: `<!-- identity-values-ok -->` in the note (letterhead/copy-paste
# layer, e.g. business-identities.md) or --allow-identity-values on the CLI.

IDENTITY_OK_MARKER = "<!-- identity-values-ok -->"


def _mastered_values():
    """Pull the currently-mastered identity/banking values LIVE from the CC (never a hardcoded list —
    new values are guarded automatically). Returns {value: 'where it lives'}. Fail-open on errors:
    a broken lookup must not block ordinary note saves."""
    vals = {}
    try:
        import json as _json, urllib.request as _rq
        cki = _cki_mod()

        def _get(query):
            req = _rq.Request(f"{cki.URL}/rest/v1/{query}",
                              headers={"apikey": cki.SR, "Authorization": f"Bearer {cki.SR}"})
            with _rq.urlopen(req, timeout=15) as resp:
                return _json.load(resp)

        rows = _get("entities?select=slug,company_number,vat_number,registry_number")
        for r in rows:
            for k in ("company_number", "vat_number"):
                if r.get(k):
                    vals[r[k]] = f"entities.{k} ({r['slug']}) — /m/entities"
            reg = r.get("registry_number") or ""
            for tok in reg.replace("(", " ").replace(")", " ").split():
                if len(tok) >= 8 and any(c.isdigit() for c in tok):
                    vals[tok] = f"entities.registry_number ({r['slug']}) — /m/entities"
        rows = _get("bank_accounts?select=label,sort_code,account_number,iban")
        for r in rows:
            for k in ("sort_code", "account_number", "iban"):
                if r.get(k):
                    vals[r[k]] = f"bank_accounts.{k} ({r['label']})"
    except Exception:
        return {}
    return vals


def identity_gate(path):
    """Return a list of (value, home) hits for values the DB masters, or [] if clean/overridden."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except OSError:
        return []
    if IDENTITY_OK_MARKER in body:
        return []
    return [(v, home) for v, home in _mastered_values().items() if v in body]


def main(argv):
    do_embed = "--embed" in argv
    allow_identity = "--allow-identity-values" in argv
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print("usage: cc-save.py <file.md> [<file2.md> ...] [--embed] [--allow-identity-values]",
              file=sys.stderr)
        return 2
    rc = 0
    for f in files:
        if not os.path.isfile(f):
            print(f"cc-save: not a file: {f}", file=sys.stderr); rc = 1; continue
        if not allow_identity:
            hits = identity_gate(f)
            if hits:
                print(f"cc-save REFUSED for {f}: the body carries identity/banking values that are "
                      f"MASTERED in the CC — save the fact there and leave a pointer in the note:",
                      file=sys.stderr)
                for v, home in hits:
                    print(f"  {v}  →  {home}", file=sys.stderr)
                print(f"  (letterhead/copy-paste layer? add {IDENTITY_OK_MARKER} to the note, "
                      f"or pass --allow-identity-values)", file=sys.stderr)
                rc = 1
                continue
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
