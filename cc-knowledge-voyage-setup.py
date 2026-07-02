#!/usr/bin/env python3
# SHIM (2026-07-02). The original baseline tool is consolidated into cc-embedder.py (the ONE embedder).
# Kept so existing callers/docs keep working. Runs the full re-baseline across all three embedding tables.
import os, sys, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.run([sys.executable, os.path.join(HERE, "cc-embedder.py")], env=os.environ).returncode)
