# pete-brain-scripts
Railway cron code. **Do not hand-edit the .py files** — they are CANONICAL copies from the vault's Library/processes/scripts/, synced by `railway-sync-repo.py` and drift-checked by `cc-cron-sync.py`. Each service runs `python railway-bootstrap.py` with env `CRON_SCRIPT=<script>.py`. See vault [[cron-registry]].
