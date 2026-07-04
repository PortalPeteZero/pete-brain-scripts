# Changelog — connection-updater

## v1.0 — 2026-07-04
- Initial release. Owns the store/update/expand/rotate/migrate/retire ritual for connections
  (secret → `public.secrets` pointer-only, registry row, config note, helper gate, propagate to
  crons AND 24/7 services, regenerate indexes, verify via `connection-parity.py`).
- Built on the converged plan `plan-connection-updater-2026-07-04` (6-round adversarial audit).
- Ships with the six-source, P1–P7 `connection-parity.py` gate + engine, and a config-note template.
- Backstop: `connection-parity.py --json` wired report-only into the weekly `drift-check` cron.
