#!/usr/bin/env python3
"""cc-automations-sync.py — RETIRED (5 Jul 2026).

The old automations dashboard (pete-automations.vercel.app), fed by
Library/processes/automations-dashboard/automations.json -> public.processes, is DEAD:
the source JSON only ever lived in the git-ignored Library/ tree (never materialises), and
the public.processes table was dropped.

The canonical automations dashboard is commandcentre.info/m/automations-log, which reads
public.crons LIVE. Manage crons with cc-cron.py (deploy / set-schedule / pause / resume /
retire / status) + each script's inline # CRON-META header. The dashboard reflects
public.crons instantly -- there is NO sync step to run.
"""
import sys
print(__doc__.strip(), file=sys.stderr)
sys.exit(2)
