#!/usr/bin/env python3
# SHIM (2026-07-02). The three embedding writers are consolidated into cc-embedder.py — the ONE embedder
# that owns `embedding` + `embedded_hash` via the SQL `embed_input()` single-source-of-truth and refreshes
# any row whose CONTENT changed (not just NULL rows). This name is kept so existing callers keep working
# (zero-arg, exits with the embedder's status, prints its last line). Embeds knowledge + quick notes.
import os, sys, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.run([sys.executable, os.path.join(HERE, "cc-embedder.py"), "vault_notes", "notes"],
                        env=os.environ).returncode)
