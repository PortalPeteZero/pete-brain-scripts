#!/usr/bin/env python3
"""
project-state.py — the projects half of the state system (§B of the plan).

For every Projects/* it closes the "read the wrong file" trap (failure #2 — the O'Connor's
"paused phase 3/4" that started all this): it names the AUTHORITATIVE ledger (latest file
carrying a TERMINAL status — complete/cutover-complete/final/… — NOT the newest mtime, which
is the exact trap a freshly-edited mid-session file sets), pulls the project's open Asana
tasks, and writes a machine-managed `## Current status` block into the project README.

v1 is REPORT-ONLY for ledger files: it lists `in-progress` files that coexist with a
completed ledger as *review candidates* but does NOT auto-edit them — distinguishing a stale
session snapshot from an active plan/research doc is too subtle to automate safely (it would
otherwise clobber live plans). The README block is the safe, high-value fix.

Safe: writes ONLY between its markers in the README, snapshot first, body-preserving, dry-run default.
Usage: python3 project-state.py [--apply] [--only "OS-OConnors-Website"]
"""
import os, re, sys, json, glob, shutil, urllib.request
from datetime import datetime, timezone
VAULT = os.environ.get("VAULT", "/tmp/pbs")

VAULT = VAULT
PROJECTS = os.path.join(VAULT, "Projects")
SECRETS = os.path.join(VAULT, "Library/processes/secrets")
BACKUP = "/tmp/project-state-backup"
ASANA_PAT = open(os.path.join(SECRETS, "asana-pat")).read().strip() if os.path.exists(os.path.join(SECRETS, "asana-pat")) else ""

APPLY = "--apply" in sys.argv
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
MS, ME = "<!-- PROJECT-STATE:START — machine-maintained by project-state.py, do not hand-edit -->", "<!-- PROJECT-STATE:END -->"

TERMINAL = {"complete", "completed", "cutover-complete", "final", "done", "shipped", "live", "closed", "archived", "passed"}
def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def fm_of(path):
    raw = open(path, encoding="utf-8", errors="ignore").read()
    return (raw.split("---", 2)[1] if raw.startswith("---") else ""), raw

def gv(fm, key):
    m = re.search(rf"^{key}\s*:\s*(.+)$", fm, re.M | re.I)
    return m.group(1).strip().strip('"').strip() if m else ""

def file_date(path, fm):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))   # date anywhere in the name
    if m: return m.group(1)
    m2 = re.search(r"(\d{4}-\d{2}-\d{2})", gv(fm, "date"))
    return m2.group(1) if m2 else "0000-00-00"

def status_token(fm):
    s = gv(fm, "status")
    return s.split()[0].lower().strip("—-") if s else ""

def asana_open_tasks(gid):
    if not gid or not ASANA_PAT: return None
    url = f"https://app.asana.com/api/1.0/projects/{gid}/tasks?opt_fields=name,completed&limit=100"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {ASANA_PAT}"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read()).get("data", [])
        return [t["name"] for t in data if not t.get("completed")]
    except Exception:
        return None

# ---------------- verify: plan-step convention (§B, mini-syntax defined here per Pass-12/14) --------
# A plan step can carry a machine-checkable criterion, inline or as an HTML comment:
#   - [ ] Deploy dashboard   <!-- verify: url 200 https://properties-dashboard.vercel.app -->
#   verify: url-contains https://x.com "Book now"   |   verify: file Projects/Foo/files/cutover.md
#   verify: grep "status: complete" Projects/Foo/README.md   |   verify: asana-done 12345
# Secret-free except asana-done (uses the PAT the script already holds; degrades if absent).
VERIFY_RE = re.compile(r"verify:\s*(.+?)\s*(?:-->|\n|$)", re.I)

import ssl as _ssl
_VCTX = _ssl.create_default_context(); _VCTX.check_hostname = False; _VCTX.verify_mode = _ssl.CERT_NONE
def _http_get(url, timeout=10):
    # CERT_NONE to match the probe/hook — these are liveness/fact checks, not secure channels, and
    # the macOS Python trust store false-fails some valid certs (would read 'unreachable' = a bluff).
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 project-state"})
        with urllib.request.urlopen(req, timeout=timeout, context=_VCTX) as r:
            return r.status, r.read(200000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return None, ""

def run_verify(d):
    """(ok, label): ok True=verified done, False=outstanding, None=unverifiable (degrade — never bluff)."""
    d = d.strip()
    try:
        if d.startswith("url-contains"):
            m = re.match(r'url-contains\s+(\S+)\s+["“](.+?)["”]', d)
            if not m: return None, d
            code, body = _http_get(m.group(1))
            if code is None: return None, f"{m.group(1)} unreachable"   # don't bluff 'not done' on a network fail
            return (m.group(2) in body), f"page has “{m.group(2)[:30]}”"
        if d.startswith("url"):
            m = re.match(r"url\s+(\S+)\s+(\S+)", d)              # url <code|ok> <url>
            if not m: return None, d
            want, url = m.group(1), m.group(2)
            code, _ = _http_get(url)
            if code is None: return None, f"{url} unreachable"
            ok = (code < 400) if want.lower() in ("ok", "up", "2xx") else (str(code) == want)
            return ok, f"{url} → {code}"
        if d.startswith("file"):
            rel = re.sub(r"^file\s+", "", d).strip().strip('"')
            path = rel if os.path.isabs(rel) else os.path.join(VAULT, rel)
            return os.path.exists(path), f"file {rel}"
        if d.startswith("grep"):
            m = re.match(r'grep\s+["“](.+?)["”]\s+(.+)', d)
            if not m: return None, d
            pat, rel = m.group(1), m.group(2).strip().strip('"')
            path = rel if os.path.isabs(rel) else os.path.join(VAULT, rel)
            if not os.path.exists(path): return False, f"grep target {rel} missing"
            return (re.search(pat, open(path, encoding="utf-8", errors="ignore").read()) is not None), f'“{pat[:24]}” in {os.path.basename(rel)}'
        if d.startswith("asana-done"):
            m = re.match(r"asana-done\s+(\d+)", d)
            if not m or not ASANA_PAT: return None, "asana-done (no PAT)"
            try:
                req = urllib.request.Request(f"https://app.asana.com/api/1.0/tasks/{m.group(1)}?opt_fields=completed,name",
                                             headers={"Authorization": f"Bearer {ASANA_PAT}"})
                t = json.loads(urllib.request.urlopen(req, timeout=15).read())["data"]
                return bool(t.get("completed")), f"asana “{(t.get('name') or '?')[:26]}”"
            except Exception:
                return None, "asana-done (error)"
    except Exception:
        return None, d
    return None, d

def collect_verifies(pdir):
    """All verify: directives across this project's README + its plan files (filename ~plan or type:plan).
    Returns (results list, had_plan_file). Plan file with zero verify lines → had_plan_file True (→ 'unstructured' nudge)."""
    cands, had_plan = [os.path.join(pdir, "README.md")], False
    for p in glob.glob(os.path.join(pdir, "**", "*.md"), recursive=True):
        low = p.lower()
        if "_archive" in low or "/archive/" in low or not owns(pdir, p):
            continue
        base = os.path.basename(low)
        if base == "readme.md":
            continue
        if "plan" in base or gv(fm_of(p)[0], "type") == "plan":
            cands.append(p); had_plan = True
    results, seen = [], set()
    for p in dict.fromkeys(cands):
        if not os.path.exists(p):
            continue
        for d in VERIFY_RE.findall(open(p, encoding="utf-8", errors="ignore").read()):
            d = d.strip()
            if d and d not in seen and len(results) < 25:
                seen.add(d)
                ok, label = run_verify(d)
                results.append({"ok": ok, "label": label})
    return results, had_plan

def owns(pdir, ledger):
    """A ledger belongs to the README at pdir only if pdir is its NEAREST README ancestor
    (so a parent project never claims a sub-project's ledgers, and vice versa)."""
    d = os.path.dirname(ledger)
    while d and len(d) >= len(pdir):
        if os.path.isfile(os.path.join(d, "README.md")):
            return os.path.abspath(d) == os.path.abspath(pdir)
        d = os.path.dirname(d)
    return False

def scan_project(pdir):
    ledgers = []
    for p in glob.glob(os.path.join(pdir, "**", "*.md"), recursive=True):
        if os.path.basename(p).lower() == "readme.md":
            continue
        low = p.lower()
        if "_archive" in low or "/archive/" in low or "_old-" in low:   # archived files are never authoritative
            continue
        if not owns(pdir, p):   # belongs to a nested sub-project, not this one
            continue
        fm, _ = fm_of(p)
        st = status_token(fm)
        if not st:
            continue
        ledgers.append({"path": p, "date": file_date(p, fm), "status": st, "name": os.path.relpath(p, pdir)})
    terminal = [l for l in ledgers if l["status"] in TERMINAL]
    authoritative = max(terminal, key=lambda l: l["date"]) if terminal else None
    # review candidates: in-progress files that coexist with a completed authoritative ledger.
    # Reported only — NOT auto-edited (could be active plans/research, not stale snapshots).
    review = sorted([l for l in ledgers if l["status"] == "in-progress"], key=lambda l: l["date"]) if authoritative else []
    return ledgers, authoritative, review

PROGRESS_PAT = re.compile(r"(progress|snapshot|\bwip\b|interim|checkpoint|session-log|execution|handover)", re.I)
def supersede_stale(review, authoritative):
    """Stamp clearly-stale session snapshots (status: in-progress, STRICTLY older than the authoritative
    terminal ledger, AND a progress-snapshot filename) → superseded, so brain-resume's in-progress grep
    stops surfacing them. Active plans/research (no progress pattern) are NEVER touched."""
    done = []
    if not authoritative:
        return done
    for l in review:
        if l["date"] < authoritative["date"] and PROGRESS_PAT.search(os.path.basename(l["name"])):
            raw = open(l["path"], encoding="utf-8").read()
            new = re.sub(r"^status:\s*in-progress.*$",
                         f"status: superseded  # auto-superseded by {authoritative['name']} (project-state.py)",
                         raw, count=1, flags=re.M)
            if new != raw:
                if APPLY:
                    os.makedirs(BACKUP, exist_ok=True)
                    shutil.copy(l["path"], os.path.join(BACKUP, "STALE_" + os.path.basename(l["path"])))
                    open(l["path"], "w", encoding="utf-8").write(new)
                done.append(l["name"])
    return done

def render_block(authoritative, ledgers, review, tasks, superseded=None, verifies=None, had_plan=False):
    L = [MS, "## Current status", "", f"- **Verified:** {now()}"]
    if authoritative:
        L.append(f"- **Authoritative ledger:** [[{authoritative['name']}]] ({authoritative['date']}, `{authoritative['status']}`) — read THIS for current state, not the newest-edited file.")
    elif ledgers:
        latest = max(ledgers, key=lambda l: l["date"])
        L.append(f"- **No terminal ledger yet** — latest is [[{latest['name']}]] ({latest['date']}, `{latest['status']}`). Add a `complete`/`final` ledger when the work lands.")
    else:
        L.append("- **No dated ledgers** — status is the README + Asana only.")
    if tasks is not None:
        if tasks:
            L.append(f"- **Open Asana tasks ({len(tasks)}):** " + "; ".join(t[:55] for t in tasks[:6]) + (" …" if len(tasks) > 6 else ""))
        else:
            L.append("- **Open Asana tasks:** none")
    # verify: plan-step phase-verification (bonus layer — graceful when a plan has no verify: lines)
    if verifies:
        done = [v for v in verifies if v["ok"] is True]
        out = [v for v in verifies if v["ok"] is False]
        unk = [v for v in verifies if v["ok"] is None]
        line = f"- **Plan steps verified:** {len(done)}/{len(verifies)} pass"
        if out:
            line += " — **outstanding:** " + "; ".join(v["label"] for v in out[:5]) + (" …" if len(out) > 5 else "")
        if unk:
            line += f" — {len(unk)} unverifiable"
        L.append(line)
    elif had_plan:
        L.append("- **Plan present but not yet `verify:`-structured** — add machine-checkable `verify:` criteria to its steps when next worked, and the nightly run will phase-verify them.")
    if superseded:
        L.append(f"- **Auto-superseded {len(superseded)} stale snapshot(s)** (older than the ledger): "
                 + ", ".join(f"`{s}`" for s in superseded[:8]))
    if review:
        L.append(f"- **⚠️ {len(review)} other `in-progress` file(s) alongside a completed ledger — review if stale (NOT auto-changed):** "
                 + ", ".join(f"`{s['name']}`" for s in review[:8]) + (" …" if len(review) > 8 else ""))
    L += ["", ME]
    return "\n".join(L)

def write_block(readme, block):
    raw = open(readme, encoding="utf-8").read()
    if MS in raw and ME in raw:
        pre, post = raw[:raw.index(MS)], raw[raw.index(ME) + len(ME):]
        new = pre + block + post
        assert (pre + post) == (new[:new.index(MS)] + new[new.index(ME) + len(ME):]), "outside-block changed"
    else:
        sep = "" if raw.endswith("\n") else "\n"
        new = raw + sep + "\n" + block + "\n"
        assert new.startswith(raw), "append changed existing content"
    if APPLY:
        os.makedirs(BACKUP, exist_ok=True)
        rel = os.path.relpath(readme, PROJECTS).replace("/", "_")
        shutil.copy(readme, os.path.join(BACKUP, rel))
        open(readme, "w", encoding="utf-8").write(new)

def main():
    def is_project(p):  # only real project/sub-project READMEs, not content/article folders that carry one
        return gv(fm_of(p)[0], "type") in ("project", "sub-project")
    readmes = sorted(p for p in glob.glob(os.path.join(PROJECTS, "**", "README.md"), recursive=True)
                     if "_archive" not in p.lower() and "/archive/" not in p.lower() and is_project(p))
    if ONLY: readmes = [p for p in readmes if ONLY in p]
    print(("APPLY" if APPLY else "DRY-RUN") + f" — {len(readmes)} project + sub-project READMEs\n" + "=" * 70)
    for readme in readmes:
        pdir = os.path.dirname(readme)
        name = os.path.relpath(pdir, PROJECTS)
        fm, _ = fm_of(readme)
        ledgers, authoritative, review = scan_project(pdir)
        tasks = asana_open_tasks(gv(fm, "asana_gid"))
        superseded = supersede_stale(review, authoritative)
        review = [r for r in review if r["name"] not in superseded]
        verifies, had_plan = collect_verifies(pdir)
        write_block(readme, render_block(authoritative, ledgers, review, tasks, superseded, verifies, had_plan))
        auth = authoritative["name"] if authoritative else ("(no terminal)" if ledgers else "(no ledgers)")
        tn = f"{len(tasks)} open" if tasks is not None else "asana:–"
        print(f"{name[:34]:35s} → {auth[:40]:41s} {tn:10s} {('· review '+str(len(review))) if review else ''}")
    print("=" * 70)
    if APPLY: print(f"Wrote ## Current status blocks. Snapshot: {BACKUP}")

if __name__ == "__main__":
    main()