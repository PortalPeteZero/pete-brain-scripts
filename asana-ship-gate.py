#!/usr/bin/env python3
"""asana-ship-gate.py — RETIRED Stop hook (kept as a safe no-op).

This hook enforced "close shipped Asana tasks at sign-off" by scanning local `/code` repos +
the `Daily/` vault folder for Asana gids — all removed at the 24 Jun 2026 cutover (Pete is off
Asana; his tasks live in `public.tasks`). It always exits 0 now, so it can't block sign-off or
error if it's still wired in a settings.json somewhere. The close-on-ship discipline now lives in
vault-writer Step 3a + `email-task-reconcile.py`. A cloud-native ship-gate (daily_log `SHIPPED:`
markers + `public.tasks`) can replace this stub later if the manual discipline proves insufficient.
"""
import sys
sys.exit(0)
