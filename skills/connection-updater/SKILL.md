---
name: connection-updater
description: >
  Owns the ritual for storing, updating, expanding, rotating, migrating, or retiring a
  CONNECTION — any standing access to an outside service (a direct API key/token, an MCP
  connector, an OAuth app, a service account). Fires whenever access changes so Pete never
  has to ask "did that get stored?". Triggers: "here's the API key / token", "I've connected
  X", "store this connection", "update the connection docs", "we can now do Y with Z", a new
  MCP connector appearing in the session, a key rotation/rename, a scope expansion, a
  connection found broken/expired (re-auth), a service migration, or retiring a service. It
  puts the secret in the ONE safe (`public.secrets`, pointer-only everywhere else), writes the
  registry row + config note + helper if one is earned, propagates to every consumer (crons AND
  24/7 services), regenerates the indexes, and verifies with a runnable gate
  (`connection-parity.py`). Never leaves a key in plaintext; never leaves an undescribed secret;
  never leaves a rotated service running on the old key.
---

# Connection updater — the store/update/retire ritual for connections

> [!important] Vocabulary (locked with Pete, 4 Jul 2026 — do not blur these)
> - **secret** = the key itself. Lives in **ONE** home: `public.secrets`. Pointer-only everywhere else (notes name the secret, never contain it).
> - **connection** = standing access to an outside service (direct API, MCP connector, OAuth app, service account). One row in the **Connections Registry** (`Library/processes/connections.md`, in `vault_notes`).
> - **config note** = the per-service manual (`{service}-api-configuration.md` family; a few live under `Businesses/*/sops/`).
> - **helper** = the tool that USES the connection (`{service}-api.py` at the repo root; surfaced in the autogen table in `[[external-service-routing]]` + `public.helpers`).
> - **process / workflow** = business *use* of connections (finance-workflow etc.) — out of scope here.
>
> Analogy: the service is a building. The secret is the key (one safe). The registry is the list of buildings we hold keys for. The config note is that building's manual. The helper is the van + toolkit. The process is the job sheet.

## Why this skill exists

The rule ("save tokens immediately, register the connection") existed only as scattered lessons, so coverage depended on a session remembering. This skill makes it a **ritual with a runnable gate**, and a weekly **backstop** (`connection-parity.py` inside the `drift-check` cron) catches anything a session misses within ≤7 days. Born from the 4 Jul 2026 Cloudflare work, where a full-access token, two DNS migrations, and a new helper were stored correctly only because the session happened to be careful.

## When it fires

Any change to standing external access:
- **"here's the key/token"**, **"I've connected X"**, **"store/update this connection"**, **"update the connection docs"**
- a **new MCP connector** appears in the session's server list
- a **key rotation / rename**, a **scope expansion** (e.g. a broader API token), a **connection found broken/expired** (re-auth)
- a **service migration** (e.g. DNS GoDaddy/IONOS → Cloudflare — a linked retire+new across two services)
- **retiring** a service

If a change touches external access and you're unsure whether it belongs here — it does. Run the ritual.

## Step 0 — Classify the event

Pick one (ask Pete ONLY if genuinely ambiguous):

| Class | What it is |
|---|---|
| **new** | first time we hold this access |
| **expanded** | same service, broader scope / a second token (e.g. zone-admin on top of DNS-only) |
| **changed-auth** | same access, new value — a **rotation** or auth-model change |
| **broken-reauth** | an existing connection found expired/revoked; re-authenticate |
| **migrated** | a LINKED retire+new pair across services (update BOTH services' rows in one run) |
| **retired** | we're dropping this access |
| **doc-only** | correcting/enriching an existing entry, no secret change |

## The ritual (every invocation — do not skip steps)

### 1. Secret → the safe
Upsert `public.secrets` (`name`, `value`, `description`, `category`, `encoding`):
- **description** = date + scope + verified capabilities (what you confirmed it can do), so a future session doesn't have to re-derive it.
- **category** from the locked taxonomy: `token` · `key-json` · `password` · `binary-cert` · `oauth-tokens` · `infra`.
- **encoding** = `text` (default) / `base64` / `json` — MUST be set (consumers rely on it).
- **Rotations UPDATE the same row** — never a second name.
- **AND rewrite the session's materialised copy** at `/tmp/pbs/Library/processes/secrets/<name>` in the SAME step, mirroring the laptop bootstrap's encoding handling (`base64` rows are written DECODED to bytes). Both `resolve_secret_env()` (step 2) and helpers' `_cc_secret()` read the LOCAL file first, so a DB-only rotation would propagate + verify against the OLD value.
- On **expanded / changed-auth**: decide + document the fate of the superseded credential — keep-as-fallback (with a dated description saying so) or revoke+delete. Never leave an undescribed old key.

> Secret handling stays within policy: enter/redact keys as pointers, never paste a value into a note, log, or task. If a password-manager credential tool is available, prefer it. Do not print secret values.

### 2. Propagate to EVERY consumer
Enumerate consumers via the all-scripts grep (`connection-parity.py` uses the same source-4 logic) — **not CRON-META alone**:
- for each consuming **Railway cron**: `VAULT=/tmp/pbs python3 /tmp/pbs/cc-cron.py deploy <cron-key>` (re-injects env from the just-rewritten local file + redeploys).
- for each consuming **24/7 service** that reads the secret at startup via `cc_secret()` (e.g. `telegram-bridge.py`): trigger/flag a **service redeploy-restart** — a rotation that skips this leaves the service on the revoked key with every other check green.
- **flag** (don't silently touch) Vercel project env vars and the local CC bootstrap key if implicated.

### 3. Registry row
Upsert the service's row in `connections.md` (auth model, canonical secret name, helper name or "none", config-note pointer). **MCP rows** carry a `last-verified` date. **Retire** = move the row to the Retired section with a date + provider-revocation confirmation.

### 4. Config note
Create from `references/config-note-template.md`, or edit the existing note **in place** (volatile-fact rule: replace stale values, don't append a contradiction). **Pointer-only** for keys.

### 5. Helper gate
Decide: extend an existing helper · create a new `{service}-api.py` · or record an explicit **"none needed"** in the registry row (so the question isn't re-asked). New helpers follow the docstring convention (first docstring line = one-line scope) so `helper-script-registry.py` picks them up; **commit + push** (code home = GitHub).

### 6. Regenerate + re-ingest + re-sync
- `VAULT=/tmp/pbs python3 /tmp/pbs/capability-registry.py --apply`
- `VAULT=/tmp/pbs python3 /tmp/pbs/helper-script-registry.py`
- `VAULT=/tmp/pbs python3 /tmp/pbs/cc-skeleton-registry-sync.py` (so `public.helpers` + `secrets_used` reflect the change same-session, not next-09:00)
- `cc-knowledge-ingest.py` on any touched note authored as a file.

### 7. Stale-ref sweep (retire / changed-auth / migrated only)
`grep -rn "<old secret name>\|<old service path>" /tmp/pbs/skills /tmp/pbs/*.py` + the process notes; fix every hit (no stale "old way" references).

### 8. Verify — runnable, not recalled
- `VAULT=/tmp/pbs python3 /tmp/pbs/connection-parity.py --service <name>` → **0 gaps**.
- `VAULT=/tmp/pbs python3 /tmp/pbs/whereis.py "<service>"` surfaces the note.
- **DB value == local materialised file, AFTER applying the row's `encoding`** (base64 compared decoded). A bare `_cc_secret()` read proves nothing — helpers read local-first.
- helper smoke-test: one read-only command.

### 9. Log + report
- `VAULT=/tmp/pbs python3 /tmp/pbs/worklog.py --property "Pete Command Centre" --area ops --title "…" --evidence "…" --outcome worked --source-ref "conn:<service>@<date>"`.
- One short **plain-English** block to Pete: what was stored, where, and what a future session will find. If the change was a rotation of a key that was ever exposed, flag whether **provider-side rotation** is warranted (this skill documents; it does not silently revoke).

## Retirement discipline (locked)

When retiring a secret: snapshot **metadata only** (name, description, scope) to the daily log — **never the value**; **revoke at the provider**; then delete the row. Moving a registry row to "Retired" does not kill the credential — revocation does. Never bulk-delete secrets; per-item confirmation from Pete.

## The gate + the backstop

- **Pete's gate (runnable):** `VAULT=/tmp/pbs python3 /tmp/pbs/connection-parity.py` → `0 gaps`.
- **Backstop:** the same script runs `--json` inside the weekly `drift-check` cron (report-only — it classifies findings into the digest; fixes are escalated to a session). So a missed ritual surfaces within ≤7 days.
- One engine, three consumers: this skill (step-8 verify), the cron, and `vault-check`.

## Anti-patterns

- Pasting a key into a note / log / task (pointer-only — `connection-parity.py` P5 will catch it).
- Rotating a secret in the DB only, leaving the materialised file + Railway env on the old value.
- Retiring a "secret with no consumers" without the all-scripts grep — runtime `cc_secret()` consumers (like the Telegram bridge) don't declare CRON-META and look like orphans.
- A second secret row for a rotated key (update the same row).
- Leaving `category`/`description`/`encoding` blank on a new secret.
- Declaring "done" before `connection-parity.py --service <name>` prints 0.
