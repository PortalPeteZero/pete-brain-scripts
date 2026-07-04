#!/usr/bin/env python3
"""connection-parity.py — the ground-truth consistency check across the Command Centre's
connection registries. ONE engine, three consumers: the connection-updater skill (step-9
verify, --service scoped), the weekly drift-check cron (--json, report-only), and Pete's gate
(bare run → `0 gaps`). See the plan: Library/plans/plan-connection-updater (converged).

Sources (dual-runtime: laptop has the full repo + .git; the Railway cron has the repo tree from
HEAD but Library/ is git-ignored so notes come from the DB):
  1. public.secrets                         — names + category/description/encoding
  2. connections.md          (vault_notes)  — registry rows: secret pointers, config-note pointers, MCP rows
  3. *-configuration.md       (vault_notes)  — per-service config notes (all vault paths)
  4. ALL repo scripts' text                 — the consumer→secret link (name match + _cc_secret()/SECRETFILE__)
  5. external-service-routing autogen table (vault_notes) + public.helpers
  6. CRON-META `# secrets:` + ENV_SECRET_MAP (imported from cc-cron.py — the SSOT for env↔name)

Checks: P1 orphan secret · P2 dead pointer · P3 unregistered config · P4 helper drift ·
P5 pasted key · P6 stale autogen · P7 metadata completeness.

Usage:
  VAULT=/tmp/pbs python3 connection-parity.py            # human summary + `0 gaps` / exit=#gap-types
  VAULT=/tmp/pbs python3 connection-parity.py --json     # machine digest (for the cron)
  VAULT=/tmp/pbs python3 connection-parity.py --service cloudflare   # scope to one service
"""
import json, os, re, sys, subprocess, urllib.request, urllib.parse

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC_DIR = os.path.join(VAULT, "Library/processes/secrets")
_k = json.load(open(os.path.join(SEC_DIR, "command-centre-supabase-keys.json")))
_URL, _SR = _k["url"].rstrip("/"), _k["service_role_key"]
_H = {"apikey": _SR, "Authorization": f"Bearer {_SR}"}

ARGS = sys.argv[1:]
AS_JSON = "--json" in ARGS
SERVICE = (ARGS[ARGS.index("--service") + 1] if "--service" in ARGS else None)


def rest(path):
    req = urllib.request.Request(f"{_URL}/rest/v1/{path}", headers=_H)
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


# ---------------------------------------------------------------- sources
def load_secrets():
    return rest("secrets?select=name,category,description,encoding")

def note_body(vault_path):
    q = urllib.parse.quote(vault_path, safe="")
    rows = rest(f"vault_notes?select=body&vault_path=eq.{q}")
    return rows[0]["body"] if rows else None

def load_config_notes():
    # all *-configuration.md notes, any vault path (incl. Businesses/*/sops/)
    rows = rest("vault_notes?select=vault_path,body&vault_path=like.*-configuration.md")
    return {r["vault_path"]: (r["body"] or "") for r in rows}

def repo_files():
    """Git-tracked *.py across the repo (portable to Railway if .git present; else glob)."""
    try:
        out = subprocess.run(["git", "-C", VAULT, "ls-files", "*.py"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return [os.path.join(VAULT, p) for p in out.stdout.splitlines()]
    except Exception:
        pass
    # fallback: walk the repo root (skip the git-ignored secrets dir)
    found = []
    for dp, _, files in os.walk(VAULT):
        if "/Library/processes/secrets" in dp or "/.git" in dp:
            continue
        found += [os.path.join(dp, f) for f in files if f.endswith(".py")]
    return found

def cron_meta_secrets():
    """secret NAMES declared in CRON-META `# secrets:` across repo scripts, mapped via cc-cron's SSOT."""
    try:
        sys.path.insert(0, VAULT)
        from importlib import import_module
        import importlib.util
        spec = importlib.util.spec_from_file_location("cccron", os.path.join(VAULT, "cc-cron.py"))
        cc = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(cc)
        except SystemExit:
            pass
        tok2name = cc.secretfile_token_to_name
    except Exception:
        tok2name = lambda t: (t[len("SECRETFILE__"):].replace("__", ".") if t.startswith("SECRETFILE__") else None)
    names, declarers = set(), {}
    for fp in repo_files():
        try:
            txt = open(fp, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        m = re.search(r'^#\s*secrets:\s*(.+)$', txt, re.M)
        if not m:
            continue
        for tok in re.split(r'[,\s]+', m.group(1).strip()):
            if not tok:
                continue
            nm = tok2name(tok) or tok
            names.add(nm)
            declarers.setdefault(nm, []).append(os.path.basename(fp))
    return names, declarers


# ---------------------------------------------------------------- consumer map (source 4)
def build_consumer_map(secret_names):
    """secret_name -> [script basenames that reference it] (verbatim name, _cc_secret arg, SECRETFILE__)."""
    consumers = {n: [] for n in secret_names}
    for fp in repo_files():
        try:
            txt = open(fp, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        base = os.path.basename(fp)
        hit = set()
        for n in secret_names:
            if n and n in txt:
                hit.add(n)
        for arg in re.findall(r'_?cc_secret\(\s*["\']([^"\']+)["\']', txt):
            if arg in consumers:
                hit.add(arg)
        for tok in re.findall(r'SECRETFILE__([A-Za-z0-9_]+)', txt):
            nm = tok.replace("__", ".")
            if nm in consumers:
                hit.add(nm)
        for n in hit:
            consumers[n].append(base)
    return consumers


# ---------------------------------------------------------------- checks
def check(secrets, conn_body, config_notes, consumers, cron_names, cron_declarers):
    findings = []  # (rule, subject, detail)
    sec_names = {s["name"] for s in secrets}
    conn_body = conn_body or ""

    def referenced(name):
        if consumers.get(name):
            return True
        if name in cron_names:
            return True
        if name in conn_body:
            return True
        for body in config_notes.values():
            if name in body:
                return True
        return False

    # P1 orphan secret
    for s in secrets:
        n = s["name"]
        if not referenced(n):
            findings.append(("P1", n, "secret in public.secrets referenced by no script, cron, config note, or connections row"))

    # P2 dead pointer — config-note pointers cited in connections.md that don't exist as notes;
    #    secret pointers (`secret \`name\`` / `secrets/name`) citing a non-existent secret.
    cited_notes = set(re.findall(r'([A-Za-z0-9\-]+-configuration)\b', conn_body))
    have_notes = {os.path.basename(p).replace(".md", "") for p in config_notes}
    for cn in cited_notes:
        if cn not in have_notes:
            findings.append(("P2", cn, "connections.md cites a config note with no matching *-configuration.md in vault_notes"))
    for body_name, body in [("connections.md", conn_body)] + [(p, b) for p, b in config_notes.items()]:
        for ref in re.findall(r'secret[s]?[`\s/]+([a-z0-9][A-Za-z0-9_.\-/]{3,})', body):
            ref = ref.strip("`/. ")
            if ref in sec_names:
                continue
            # only flag things that look like a concrete secret-name token (has a hyphen or dot-json)
            if (("-" in ref and not ref.endswith("-")) or ref.endswith(".json")) and "/" not in ref[:1]:
                if ref not in have_notes and not ref.endswith("configuration"):
                    findings.append(("P2", f"{body_name}:{ref}", "doc cites a secret-name-shaped token not present in public.secrets (verify or fix pointer)"))

    # P3 unregistered config — a config note (service) with no connections.md row
    for p in config_notes:
        svc = os.path.basename(p).replace("-api-configuration.md", "").replace("-configuration.md", "")
        stem = os.path.basename(p).replace(".md", "")
        if stem not in conn_body and svc.lower() not in conn_body.lower():
            findings.append(("P3", p, f"config note has no reference in connections.md (service '{svc}' unregistered)"))

    # P4 helper drift
    helpers = rest("helpers?select=name,path,secrets_used")
    helper_names_db = {h["name"] for h in helpers}
    disk_helpers = {os.path.basename(f) for f in repo_files() if os.path.basename(f).endswith("-api.py")}
    routing = note_body("Library/processes/external-service-routing.md") or ""
    for hf in sorted(disk_helpers):
        if f"`{hf}`" not in routing:
            findings.append(("P4", hf, "helper file on disk missing from external-service-routing autogen table"))
        if hf not in helper_names_db:
            findings.append(("P4", hf, "helper file on disk missing from public.helpers registry"))
    for h in helpers:
        if not str(h["path"]).endswith("-api.py"):
            continue
        for nm in [x.strip() for x in (h.get("secrets_used") or "").split(",") if x.strip()]:
            if nm not in sec_names:
                findings.append(("P4", f"{h['name']}→{nm}", "helper secrets_used names a secret absent from public.secrets"))

    # P5 pasted key
    findings += check_p5()

    # P6 stale autogen — block's stated count != live count
    live_helpers = len(disk_helpers)
    for label, body, pat in [
        ("external-service-routing", routing, r'count=(\d+)'),
        ("capability-registry", conn_body, r'helpers=(\d+)'),
    ]:
        m = re.search(r'CADENCE:[^>]*?' + pat, body)
        if not m:
            findings.append(("P6", label, "autogen block has no CADENCE count marker — cannot verify freshness"))
        elif int(m.group(1)) != live_helpers:
            findings.append(("P6", label, f"autogen block states {m.group(1)} helpers but {live_helpers} live — stale, regenerate"))

    # P7 metadata completeness
    ALLOWED_CAT = {"token", "key-json", "password", "binary-cert", "oauth-tokens", "infra"}
    for s in secrets:
        if not s.get("category"):
            findings.append(("P7", s["name"], "secret has NULL/empty category"))
        elif s["category"] not in ALLOWED_CAT:
            findings.append(("P7", s["name"], f"secret category '{s['category']}' not in the locked taxonomy {sorted(ALLOWED_CAT)}"))
        if not s.get("description"):
            findings.append(("P7", s["name"], "secret has NULL/empty description"))
        if not s.get("encoding"):
            findings.append(("P7", s["name"], "secret has NULL/empty encoding"))

    return findings


P5_PATTERNS = [
    ("cloudflare", re.compile(r'cf(at|ut)_[A-Za-z0-9]{30,}')),
    ("github-pat", re.compile(r'ghp_[A-Za-z0-9]{30,}')),
    ("supabase-pat", re.compile(r'sbp_[A-Za-z0-9]{20,}')),
    ("vercel", re.compile(r'vcp_[A-Za-z0-9]{20,}')),
    ("google-api-key", re.compile(r'AIza[A-Za-z0-9_\-]{35}')),
    ("openai/anthropic", re.compile(r'sk-[A-Za-z0-9]{20,}')),
    ("stripe-live", re.compile(r'[sr]k_live_[A-Za-z0-9]{20,}')),
    ("blotato", re.compile(r'blt_[A-Za-z0-9]{20,}')),
    ("resend", re.compile(r're_[A-Za-z0-9]{20,}')),
    ("slack", re.compile(r'xox[baprs]-[A-Za-z0-9\-]{12,}')),
    ("sentry", re.compile(r'sntr[a-z]_[A-Za-z0-9]{20,}')),
    ("surfer", re.compile(r'csk-[A-Za-z0-9]{20,}')),
    ("pem-private-key", re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
]

def _is_placeholder(tok):
    # documentation placeholders, not real keys: a run of ≥6 identical chars (ghp_xxxx…, sk-0000…)
    return bool(re.search(r'(.)\1{5,}', tok)) or "XXXX" in tok or "xxxx" in tok

def _scan_text(where, text):
    hits = []
    for label, pat in P5_PATTERNS:
        for m in pat.finditer(text or ""):
            tok = m.group(0)
            if _is_placeholder(tok):
                continue
            hits.append(("P5", where, f"pasted {label} credential (prefix {tok[:6]}…) — move to secrets, pointer-only"))
            break
    return hits

def check_p5():
    findings = []
    # DB legs (run everywhere)
    for tbl, cols, key in [
        ("vault_notes", "vault_path,body", "vault_path"),
        ("daily_log", "date,content", "date"),
        ("work_log", "id,detail,evidence", "id"),
        ("tasks", "id,name,notes", "id"),
    ]:
        try:
            for r in rest(f"{tbl}?select={cols}"):
                blob = " ".join(str(r.get(c, "")) for c in cols.split(",")[1:])
                where = f"{tbl}:{r.get(key)}"
                # skip the plan note + skill docs (prefix-only examples live there by design)
                vp = str(r.get("vault_path", ""))
                if "plan-connection-updater" in vp or "/skills/" in vp:
                    continue
                findings += _scan_text(where, blob)
        except Exception as e:
            findings.append(("P5", tbl, f"scan error: {e}"))
    # repo leg (needs .git; runtime-split rule)
    if os.path.isdir(os.path.join(VAULT, ".git")):
        try:
            out = subprocess.run(["git", "-C", VAULT, "ls-files"], capture_output=True, text=True, timeout=30)
            for rel in out.stdout.splitlines():
                if rel.endswith((".py",)) and "connection-parity" in rel:
                    continue  # this file carries the prefix patterns by design
                fp = os.path.join(VAULT, rel)
                try:
                    findings += _scan_text(f"repo:{rel}", open(fp, encoding="utf-8", errors="ignore").read())
                except Exception:
                    pass
        except Exception as e:
            findings.append(("P5", "repo", f"repo-leg error: {e}"))
    else:
        findings.append(("P5-INFO", "repo-leg", "SKIPPED (no .git in this runtime) — DB legs ran; repo scan is laptop/gate-only"))
    return findings


# ---------------------------------------------------------------- main
def main():
    secrets = load_secrets()
    conn_body = note_body("Library/processes/connections.md")
    config_notes = load_config_notes()
    sec_names = {s["name"] for s in secrets}
    consumers = build_consumer_map(sec_names)
    cron_names, cron_declarers = cron_meta_secrets()

    findings = check(secrets, conn_body, config_notes, consumers, cron_names, cron_declarers)

    if SERVICE:
        s = SERVICE.lower()
        findings = [f for f in findings if s in (f[1] or "").lower() or s in (f[2] or "").lower()]

    info = [f for f in findings if f[0] == "P5-INFO"]
    real = [f for f in findings if f[0] != "P5-INFO"]
    gap_types = sorted({f[0] for f in real})

    if AS_JSON:
        print(json.dumps({
            "gaps": len(real), "gap_types": gap_types,
            "findings": [{"rule": r, "subject": s, "detail": d} for r, s, d in real],
            "info": [{"rule": r, "subject": s, "detail": d} for r, s, d in info],
        }, indent=2))
    else:
        if not real:
            print("0 gaps — connection registries consistent." + (f" (scope: {SERVICE})" if SERVICE else ""))
        else:
            by = {}
            for r, s, d in real:
                by.setdefault(r, []).append((s, d))
            print(f"{len(real)} gaps across {len(gap_types)} type(s): {', '.join(gap_types)}\n")
            for r in sorted(by):
                print(f"── {r} ({len(by[r])}) ──")
                for s, d in by[r]:
                    print(f"   • {s}: {d}")
                print()
        for r, s, d in info:
            print(f"[info] {s}: {d}")

    sys.exit(len(gap_types))


if __name__ == "__main__":
    main()
