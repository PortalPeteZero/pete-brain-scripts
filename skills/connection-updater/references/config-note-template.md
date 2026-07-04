---
type: process
tags: [<service-slug>, api, connections]
---

Master reference for <Service> access. **Pointer-only for secrets** — name the secret, never paste the value.

## Auth

| Field | Value |
|-------|-------|
| **Secret** | `<secret-name>` (in `public.secrets`; materialised to `/tmp/pbs/Library/processes/secrets/<secret-name>`) |
| **Auth model** | <Bearer token / API-Key header / OAuth2 refresh / service-account JWT / HTTP Basic> |
| **Scope / permissions** | <what it can do — the verified capabilities> |
| **Account / owner** | <account id / login> |
| **Status** | <ACTIVE + date last verified; note any fallback/superseded token> |

## Usage

**Auth header:** `Authorization: Bearer <from secret `<secret-name>`>`

```
# minimal example call (read-only)
GET https://api.<service>.com/<path>
```

## Helper

`<service>-api.py` (repo root) — <one-line scope>. Run: `VAULT=/tmp/pbs python3 /tmp/pbs/<service>-api.py <command>`.
(If no helper is warranted, state **"no helper — used ad hoc via <how>"** so the question isn't re-asked.)

## Notes / quirks

- <dead endpoints, rate limits, "never use X", rotation cadence, etc.>

> [!important] Keep this note pointer-only. The value lives in `public.secrets` as `<secret-name>`.
> Any change to the connection runs the **connection-updater** skill (secret → safe, this note,
> registry row, regen, `connection-parity.py --service <slug>` → 0).
