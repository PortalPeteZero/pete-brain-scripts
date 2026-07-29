#!/usr/bin/env python3
"""pete-session-bootstrap.py — the thin-client boot kernel (Business OS Part J step 3).

Turns a bare machine (just the CC key) into a working session with ZERO permanent local code or
secrets: it PULLS all code from GitHub to /tmp/pbs and MATERIALISES all secrets from the CC
`secrets` table into /tmp/pbs/Library/processes/secrets/ — so the canonical helper scripts run
UNCHANGED. This is the proven railway-bootstrap no-stub pattern, extended from Railway to local
sessions: there are no hand-written env-reading copies to drift, and nothing secret persists on disk
outside the one CC bootstrap key.

Irreducible local footprint after cutover:
  • ~/.config/pete-secrets/command-centre-supabase-keys.json   (the ONE bootstrap key; or CC_SUPABASE_* env)
  • the tiny CLAUDE.md bootstrap + this kernel (a copy at ~/.config/pete-cc/)
Everything else is pulled/materialised into /tmp on demand and discarded.

Usage:
  python3 pete-session-bootstrap.py                 # clone/pull + materialise secrets; print VAULT
  python3 pete-session-bootstrap.py cc-sql.py "SELECT 1"   # ...then exec a canonical tool
Manual run of any pulled tool afterwards:  VAULT=/tmp/pbs python3 /tmp/pbs/<tool>.py [args]
"""
import os, sys, json, base64, subprocess, shutil, urllib.request
from pathlib import Path

PBS = Path(os.environ.get("PBS_DIR", "/tmp/pbs"))
REPO = "PortalPeteZero/pete-brain-scripts"
CFG = Path.home() / ".config/pete-secrets/command-centre-supabase-keys.json"


def cc_creds():
    url, key = os.environ.get("CC_SUPABASE_URL"), os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if url and key:
        return url.rstrip("/"), key
    d = json.load(open(CFG))
    return d["url"].rstrip("/"), d["service_role_key"]


CC_URL, CC_KEY = cc_creds()


def cc_get(path):
    req = urllib.request.Request(f"{CC_URL}/rest/v1/{path}",
                                 headers={"apikey": CC_KEY, "Authorization": f"Bearer {CC_KEY}"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())


def _same_as_origin(g, f):
    """True when the working-tree file is byte-identical to origin/main's version (so reverting it
    discards nothing)."""
    blob = subprocess.run(["git", "-C", str(PBS), "show", f"origin/main:{f}"], capture_output=True)
    if blob.returncode != 0:
        return False                      # not on origin at all → never safe to bin
    try:
        return (PBS / f).read_bytes() == blob.stdout
    except OSError:
        return False


def self_heal():
    """A fast-forward pull was refused because the SHARED /tmp/pbs clone is dirty — a session edited
    files in place. Sessions must push from their own clones (git-commit-atomic-guard enforces that),
    so a leftover here is nearly always a copy of something already on origin: dead weight that
    silently pins every later session to stale code (measured 28-29 Jul 2026 — 11 commits behind,
    all 12 leftovers byte-identical to origin).

    Deliberately SURGICAL, because other sessions may be live in this same clone:
      • revert only tracked files that are byte-identical to origin/main — discards nothing;
      • never hard-reset and never `git clean`, so another session's untracked work-in-progress
        survives; the ONLY untracked files touched are ones origin/main now adds at the same path
        (their pushed version supersedes the local copy) — and those are preserved first;
      • anything that genuinely differs is COPIED to /tmp/pbs-preserved-<stamp>/ and left in place,
        and is only reverted if it is what still blocks the pull (the copy is then the record).
    Fail-open: a heal failure must never stop a session booting."""
    import time
    g = lambda *a: subprocess.run(["git", "-C", str(PBS)] + list(a), capture_output=True, text=True)
    try:
        if g("fetch", "-q", "origin", "main").returncode != 0:
            print("bootstrap: ⚠ pull blocked AND fetch failed — running on the existing (possibly stale) copy", flush=True)
            return "stale (fetch failed)"

        dirty = [l[3:].strip() for l in g("status", "--porcelain").stdout.splitlines()
                 if l[:2].strip() and not l.startswith("??")]
        # untracked files origin/main now carries at the same path: the pushed version wins
        on_origin = set(g("ls-tree", "-r", "--name-only", "origin/main").stdout.splitlines())
        shadowing = [f for f in g("ls-files", "--others", "--exclude-standard").stdout.splitlines()
                     if f in on_origin]

        preserved = Path(f"/tmp/pbs-preserved-{time.strftime('%Y%m%d-%H%M%S')}")

        def stash(f):
            dest = preserved / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(PBS / f, dest)
            except OSError:
                pass

        redundant = [f for f in dirty if _same_as_origin(g, f)]
        differs = [f for f in dirty if f not in redundant]
        if redundant:
            g("checkout", "--", *redundant)
        for f in differs:
            stash(f)

        for f in shadowing:
            if not _same_as_origin(g, f):
                stash(f)
                differs.append(f)
            try:
                (PBS / f).unlink()
            except OSError:
                pass

        r = g("pull", "-q", "--ff-only")
        if r.returncode != 0 and differs:
            # those genuinely-different files are what still blocks it; the copy above is the record
            still = [f for f in differs if (PBS / f).exists()]
            if still:
                g("checkout", "--", *still)
            r = g("pull", "-q", "--ff-only")

        if differs:
            print(f"bootstrap: ⚠ SELF-HEAL — {len(differs)} leftover file(s) differed from origin, copied to "
                  f"{preserved}: {', '.join(differs[:8])}{' …' if len(differs) > 8 else ''}. "
                  "Review and route them (push from your own clone / ingest / discard).", flush=True)
        if r.returncode != 0:
            print(f"bootstrap: ⚠ self-heal could not complete the pull ({(r.stderr or '').strip()[:140]}) — "
                  "running on the existing (possibly stale) copy", flush=True)
            return "stale (heal incomplete)"

        detail = f"{len(redundant)} stale duplicate(s) reverted" if redundant else "leftovers cleared"
        if differs:
            detail += f", {len(differs)} preserved"
        return f"self-healed + pulled ({detail})"
    except Exception as e:
        print(f"bootstrap: ⚠ self-heal failed ({e}) — running on the existing (possibly stale) copy", flush=True)
        return "stale (self-heal failed)"


def clone_or_pull():
    rows = cc_get("secrets?select=value&name=eq.github-pat")
    pat = rows[0]["value"].strip() if rows else None
    if (PBS / ".git").exists():
        r = subprocess.run(["git", "-C", str(PBS), "pull", "-q", "--ff-only"], capture_output=True, text=True)
        if r.returncode == 0:
            return "pulled"
        return self_heal()
    url = f"https://{pat}@github.com/{REPO}.git" if pat else f"https://github.com/{REPO}.git"
    # GitHub throws transient SSL_ERROR_SYSCALL on clones — retry with backoff (boot-critical).
    import time
    last = ""
    for attempt in range(4):
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(PBS)], capture_output=True, text=True)
        if r.returncode == 0:
            return "cloned"
        last = (r.stderr or "")[:200]
        shutil.rmtree(PBS, ignore_errors=True)
        time.sleep(2 * (attempt + 1))
    sys.exit(f"bootstrap: clone failed after 4 attempts — {last}")


def materialise_secrets():
    sec = PBS / "Library" / "processes" / "secrets"
    sec.mkdir(parents=True, exist_ok=True)
    rows = cc_get("secrets?select=name,value,encoding")
    n = 0
    for r in rows:
        name = r["name"]
        val = r.get("value") or ""
        enc = (r.get("encoding") or "text").lower()
        dest = sec / name                       # name may be a subpath (e.g. garminconnect-tokens/garmin_tokens.json)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if enc == "base64":
            dest.write_bytes(base64.b64decode(val))
        else:
            dest.write_text(val)
        n += 1
    return n


def materialise_config():
    """Refresh the local caches of the operating docs (CLAUDE, MAP) from CC `config`, so a session
    can read its FULL instructions even with the vault gone. The harness loads only the tiny
    bootstrap CLAUDE.md; Step 0 reads these caches (CLAUDE.cache.md / MAP.cache.md)."""
    out = Path.home() / ".config/pete-cc"
    out.mkdir(parents=True, exist_ok=True)
    fn = {"claude-md": "CLAUDE.cache.md", "map-md": "MAP.cache.md"}
    n = 0
    try:
        for r in cc_get("config?select=key,value&key=in.(claude-md,map-md)"):
            if r["key"] in fn and r.get("value"):
                (out / fn[r["key"]]).write_text(r["value"]); n += 1
    except Exception as e:
        print(f"bootstrap: config fetch skipped ({e})", flush=True)
    return n


def publish_gate_wiring():
    """Publish this machine's hook wiring into the CC `gates` registry (plan step 0b).

    Every hook-type gate is wired in a settings.json on this Mac; `cc-locator-audit` runs on Railway
    and cannot read local disk. Without this the daily audit can never tell a registered gate from a
    wired one. Runs here because the kernel is the one thing that runs locally every session.

    Fail-open and quiet: a reporting failure must never stop a session booting.
    """
    try:
        r = subprocess.run([sys.executable, str(PBS / "gate-report.py")],
                           capture_output=True, text=True, timeout=45,
                           env={**os.environ, "VAULT": str(PBS)})
        head = (r.stdout or "").strip().split("\n")[0]
        warns = [l for l in (r.stdout or "").split("\n") if l.strip().startswith("⚠")]
        if head:
            print(f"bootstrap: {head}" + (f" — {len(warns)} needing attention" if warns else ""), flush=True)
        for w in warns:
            print(f"bootstrap: {w.strip()}", flush=True)
    except Exception as e:
        print(f"bootstrap: gate wiring not published ({e}) — continuing", flush=True)


def main():
    how = clone_or_pull()
    n = materialise_secrets()
    c = materialise_config()
    os.environ["VAULT"] = str(PBS)
    print(f"bootstrap: /tmp/pbs ready ({how} {REPO}, {n} secrets + {c} config docs materialised), VAULT={PBS}", flush=True)
    publish_gate_wiring()
    if len(sys.argv) > 1:
        tool = PBS / sys.argv[1]
        if not tool.exists():
            sys.exit(f"bootstrap: tool {sys.argv[1]} not found in {PBS}")
        os.execve(sys.executable, [sys.executable, str(tool)] + sys.argv[2:], os.environ)


if __name__ == "__main__":
    main()
