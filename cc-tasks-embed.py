#!/usr/bin/env python3
# SHIM (2026-07-02). Consolidated into cc-embedder.py (the ONE embedder). Kept so existing callers keep
# working. Embeds the tasks table (content-hash dirty detection via the SQL embed_input() SSOT).
import os, sys, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.run([sys.executable, os.path.join(HERE, "cc-embedder.py"), "tasks"],
                        env=os.environ).returncode)
