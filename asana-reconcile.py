#!/usr/bin/env python3
"""
asana-reconcile.py — evidence-driven reconciliation of Pete's open Asana tasks.

The durable fix for the "Claude finishes something but never closes Asana" gap.
Complements asana-gmail-sync (which handles EMAIL-coupled tasks); this targets the
NON-email tasks — built systems/crons, real-world events, superseded work, and
verification tasks — that the same-day reconciliation pass is blind to.

DESIGN (mirrors sync-asana.py): the script does the DETERMINISTIC evidence-gathering;
the human/LLM does the judgment. It NEVER closes on assumption.

For every open task assigned to Pete it attaches evidence signals:
  - gid appears in Daily/*.md (esp. on a line with a completion word)
  - gid appears in a git commit subject across Pete's known repos
  - a "build cron/report X" task whose named cron now exists in the registry
  - a vault path named in the notes that exists on disk
  - an event-shaped task whose due date has passed
  - a reply/chase task whose Gmail thread's last message is from Pete

…then classifies into:
  AUTO     — unambiguous mechanical proof (gid in a commit, named cron exists).
             Closed only with --apply-auto. This is the narrow class Pete OK'd.
  PROPOSE  — soft evidence; SURFACED for Pete's one-word confirm, never auto-closed.
  PAYMENT  — "Pay X" tasks; can't be verified here, always surfaced to Pete.
  OPEN     — no evidence; left silent unless very stale (then listed as "no evidence").

Usage:
  python3 asana-reconcile.py                 # full report, all open Pete tasks
  python3 asana-reconcile.py --overdue-only   # only overdue
  python3 asana-reconcile.py --json           # machine-readable (for the cron/skill)
  python3 asana-reconcile.py --apply-auto     # close the AUTO bucket (audit comment each)
  python3 asana-reconcile.py --ship GID|KW... # Layer 1: close tasks matching a shipped artefact

Conventions, IDs and rules: [[asana-configuration]]. Lesson home: the reconciliation
gap is [[Library/lessons/2026-05-04-same-day-reconciliation-gap]] (this generalises it).
"""
import sys, os, json, re, time, datetime, subprocess, urllib.request, urllib.parse
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
PAT = open(f"{VAULT}/Library/processes/secrets/asana-pat").read().strip()
MYTASKS = "1213947191349754"
PRI_FIELD = "1213945150508559"
PRI = {"1213945150508560": "P1", "1213945150508561": "P2",
       "1213945150508562": "P3", "1213945150508563": "P4"}
TODAY = datetime.date.today()
DAILY = f"{VAULT}/Daily"
REGISTRY = f"{VAULT}/Library/processes/scheduled-tasks.md"
AUTOMATIONS = f"{VAULT}/Library/processes/automations-dashboard/automations.json"
# Pete's local code repos that crons/builds land in (best-effort; missing dirs skipped)
REPOS = [os.path.expanduser(p) for p in (
    "~/code/command-centre", "~/code/sygma-platform", "~/code/passion-fit",
)]
COMPLETION_WORDS = re.compile(
    r"\b(closed|done|shipped|paid|sent|deployed|complete|completed|merged|"
    r"resolved|settled|CLOSED|DONE|SHIPPED|PAID)\b", re.I)
# Lines that are resume / overdue / status-dump blocks — NOT completion records.
# A completion word on one of these almost always refers to a DIFFERENT task,
# because they pack many task IDs onto one line with mixed states.
STATUS_DUMP_RE = re.compile(
    r"OD\s?\d+\s?d|\boverdue\b|\bpending\b|\bdue today\b|cap[- ]?100|"
    r"\bP[1-4]\b.*\bP[1-4]\b|priorit", re.I)
GID_RE = re.compile(r"\b121\d{13}\b")
# Lines that SURFACE an open task (a TODO, a digest row, a "close?" proposal) rather
# than RECORD a completed one. A completion word on these refers to the task's *status*,
# not its completion — including this reconciler's OWN session logs / digests, which
# would otherwise self-poison the next run. Reject these before trusting a done-word.
SURFACING_RE = re.compile(
    r"\[ \]|\?|still open|left open|re-?opened|needs your|open for pete|untouched|"
    r"\bawait|asana-reconcile|propose|to close\b|not written|not done|carried", re.I)
# Explicit completion marker a session writes when it shipped task-linked work but
# couldn't close the task in the same breath: `SHIPPED: <gid> — <evidence>`. A
# deliberate assertion (not fuzzy inference), so it's treated as AUTO (closeable).
SHIPPED_MARKER_RE = re.compile(r"SHIPPED:?\s*(121\d{13})[^\n]*", re.I)
# Real payables only — task NAME starts with "Pay" (or "...review + pay"). Deliberately
# tight: "Move Invoice Number column" / "confirm payment-detail format" are NOT payments.
PAYMENT_RE = re.compile(r"^\s*pay\b|\breview \+ pay\b|^\s*pay invoice", re.I)
# Tasks where PETE owes the reply — Pete-sent-last-message = likely done.
REPLY_OWED_RE = re.compile(r"\b(reply|respond|answer)\b", re.I)
# Tasks where Pete is AWAITING the other side — Pete-sent-last means he's WAITING,
# not done (the GJS case, 14 Jun). These must NOT use the Pete-replied signal.
AWAIT_RE = re.compile(r"\b(chase|watch for|awaiting|await|follow[\s-]?up|chaser)\b", re.I)
GMAIL_THREAD_RE = re.compile(r"#(?:all|inbox|label/[^/]+/)?([0-9a-f]{16})\b|"
                             r"thread[:\s]+([0-9a-f]{16})\b", re.I)
VAULTPATH_RE = re.compile(r"(?:Properties|Projects|Businesses|Library|Personal|"
                          r"Customers|Suppliers)/[^\s,;)'\"]+")


def asana(method, path, body=None, tries=5):
    for i in range(tries):
        try:
            url = f"https://app.asana.com/api/1.0{path}"
            req = urllib.request.Request(
                url, method=method,
                headers={"Authorization": f"Bearer {PAT}",
                         "Content-Type": "application/json"})
            data = json.dumps({"data": body}).encode() if body else None
            with urllib.request.urlopen(req, data=data, timeout=45) as r:
                return json.loads(r.read())["data"]
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def pull_open_tasks():
    fields = ("name,due_on,completed,projects.name,memberships.section.name,"
              "custom_fields,notes,modified_at")
    out, offset = [], None
    while True:
        p = {"completed_since": "now", "opt_fields": fields, "limit": 100}
        if offset:
            p["offset"] = offset
        url = (f"https://app.asana.com/api/1.0/user_task_lists/{MYTASKS}/tasks?"
               + urllib.parse.urlencode(p))
        env = None
        for i in range(5):
            try:
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {PAT}"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    env = json.loads(r.read())
                break
            except Exception:
                if i == 4:
                    raise
                time.sleep(3 * (i + 1))
        out.extend(env["data"])
        nx = env.get("next_page")
        if nx and nx.get("offset"):
            offset = nx["offset"]
        else:
            break
    return out


def prio(t):
    for cf in t.get("custom_fields", []):
        if cf.get("gid") == PRI_FIELD and cf.get("enum_value"):
            return PRI.get(cf["enum_value"]["gid"], "?")
    return "none"


def plabel(t):
    pr = t.get("projects", [])
    pn = pr[0]["name"] if pr else "—"
    sec = ""
    for mb in t.get("memberships", []):
        s = (mb.get("section") or {}).get("name")
        if s:
            sec = s
            break
    return f"{pn}/{sec}" if sec else pn


# ---- evidence gathering ----------------------------------------------------
_DAILY_CACHE = None


def _daily_blob():
    global _DAILY_CACHE
    if _DAILY_CACHE is None:
        _DAILY_CACHE = []
        for fn in sorted(os.listdir(DAILY)):
            if re.match(r"\d{4}-\d{2}-\d{2}\.md$", fn):
                try:
                    _DAILY_CACHE.append((fn[:-3], open(f"{DAILY}/{fn}", encoding="utf-8").read()))
                except Exception:
                    pass
    return _DAILY_CACHE


def daily_done(gid):
    """
    Strict completion evidence in Daily/*.md. Returns (date, snippet) of the most
    recent line that is a genuine completion RECORD for this gid, else None.

    Guards against the status-dump false-positive (the calibration bug found on the
    first live run): a line counts only if it is NOT a resume/overdue list line,
    mentions this gid ONLY (no second task ID), AND has a completion word within
    ~60 chars of the gid (so the word is about *this* task, not a neighbour).
    """
    best = None
    for date, txt in _daily_blob():
        for line in txt.splitlines():
            idx = line.find(gid)
            if idx == -1:
                continue
            if STATUS_DUMP_RE.search(line) or SURFACING_RE.search(line):
                continue
            if len(set(GID_RE.findall(line))) >= 2:
                continue
            # "Step 0 of `gid`" / "X of `gid`" — the gid is the OBJECT of the sentence
            # (a sub-step references its parent task), not the subject being completed.
            if re.search(r"\bof\s*`?\s*$", line[max(0, idx - 12): idx]):
                continue
            window = line[max(0, idx - 60): idx + len(gid) + 60]
            if COMPLETION_WORDS.search(window):
                best = (date, line.strip()[:160])
    return best


def shipped_marker(gid):
    """Explicit `SHIPPED: <gid> …` assertion in any daily note. A deliberate
    completion record (not fuzzy inference) → AUTO. Returns (date, line) or None."""
    for date, txt in _daily_blob():
        for m in SHIPPED_MARKER_RE.finditer(txt):
            if m.group(1) == gid:
                return (date, m.group(0).strip()[:160])
    return None


def git_commit_hits(gid):
    """gid in a commit subject across known repos (definitive when present)."""
    found = []
    for repo in REPOS:
        if not os.path.isdir(os.path.join(repo, ".git")):
            continue
        try:
            r = subprocess.run(
                ["git", "-C", repo, "log", "--all", "--oneline", f"--grep={gid}", "-5"],
                capture_output=True, text=True, timeout=20)
            for ln in r.stdout.splitlines():
                found.append((os.path.basename(repo), ln.strip()[:120]))
        except Exception:
            pass
    return found


_REG_BLOB = None


def registry_blob():
    global _REG_BLOB
    if _REG_BLOB is None:
        try:
            _REG_BLOB = open(REGISTRY, encoding="utf-8").read().lower()
        except Exception:
            _REG_BLOB = ""
        try:
            _REG_BLOB += "\n" + open(AUTOMATIONS, encoding="utf-8").read().lower()
        except Exception:
            pass
    return _REG_BLOB


def cron_evidence(name):
    """For 'set up/build a [cron|report|digest]' tasks: does a matching cron exist?"""
    n = name.lower()
    if not re.search(r"\b(set up|build|create|automate|schedule)\b", n):
        return None
    if not re.search(r"\b(cron|report|digest|scheduled|automation|snapshot|sync|reminder)\b", n):
        return None
    # pull the salient keywords (drop stop words) and require >=2 to co-occur in the registry
    kws = [w for w in re.findall(r"[a-z]{4,}", n)
           if w not in {"build", "create", "automate", "schedule", "report",
                        "weekly", "monthly", "daily", "setup", "scheduled", "with"}]
    reg = registry_blob()
    present = [w for w in kws if w in reg]
    if len(present) >= 2:
        return present
    return None


def file_evidence(notes):
    """Specific FILES named in the notes that exist (dirs excluded — a project/customer
    folder existing is not evidence of completion). Weak: shown as a hint, never a
    classifier on its own (a 'phase10-checklist.md' existing means it was planned,
    not done)."""
    hits = []
    for m in VAULTPATH_RE.findall(notes or ""):
        path = os.path.join(VAULT, m)
        if os.path.isfile(path):
            hits.append(m)
    return hits


def gmail_last_from_pete(notes):
    """Reply/chase tasks: is the last message on the linked thread from Pete?"""
    m = GMAIL_THREAD_RE.search(notes or "")
    if not m:
        return None
    tid = m.group(1) or m.group(2)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "g", f"{VAULT}/Library/processes/scripts/gmail-api.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        g = mod.GmailAPI()
        td = g.get_thread(tid, fmt="metadata")
        msgs = td.get("messages", [])
        if not msgs:
            return None
        last = msgs[-1]
        frm = ""
        for h in last.get("payload", {}).get("headers", []):
            if h["name"].lower() == "from":
                frm = h["value"].lower()
        return "pete.ashcroft" in frm or "ashcroft@sygma" in frm
    except Exception:
        return None


# ---- classification --------------------------------------------------------
def classify(t):
    name = t.get("name", "")
    notes = t.get("notes", "") or ""
    gid = t["gid"]
    due = t.get("due_on")
    overdue_days = (TODAY - datetime.date.fromisoformat(due)).days if (due and datetime.date.fromisoformat(due) < TODAY) else 0

    ev = {}
    sm = shipped_marker(gid)        # explicit SHIPPED: <gid> assertion (or None)
    if sm:
        ev["shipped"] = sm
    dd = daily_done(gid)            # strict completion record (or None)
    if dd:
        ev["daily_done"] = dd
    gh = git_commit_hits(gid)
    if gh:
        ev["git"] = gh
    cron = cron_evidence(name)
    if cron:
        ev["cron"] = cron
    fe = file_evidence(notes)       # weak hint only — never a classifier
    if fe:
        ev["file"] = fe
    if REPLY_OWED_RE.search(name) and not AWAIT_RE.search(name):
        pr = gmail_last_from_pete(notes)
        if pr is True:              # Pete sent the last message on a reply-owed task = done
            ev["gmail_pete_replied"] = True

    is_payment = bool(PAYMENT_RE.search(name))

    # disposition — high precision: only trustworthy signals drive PROPOSE.
    if ev.get("shipped"):
        disp = "AUTO"              # explicit SHIPPED: marker — deliberate assertion
    elif is_payment:
        disp = "PAYMENT"            # never verifiable here; always Pete's call
    elif ev.get("git") or ev.get("cron"):
        disp = "AUTO"              # unambiguous mechanical proof
    elif ev.get("daily_done") or ev.get("gmail_pete_replied"):
        disp = "PROPOSE"           # strict, suggestive evidence — surface for confirm
    else:
        disp = "OPEN"             # no trustworthy signal — stays silent (unless very stale)

    return disp, ev, overdue_days


def recommend(disp, ev):
    if disp == "AUTO":
        if ev.get("shipped"):
            return f"close — explicit SHIPPED marker ({ev['shipped'][0]}): \"{ev['shipped'][1][:80]}\""
        if ev.get("git"):
            return f"close — gid in commit ({ev['git'][0][1]})"
        return f"close — named cron exists in registry ({'/'.join(ev['cron'])})"
    if disp == "PROPOSE":
        if ev.get("gmail_pete_replied"):
            return "likely done — you sent the last reply on the linked thread"
        if ev.get("daily_done"):
            return f"daily note records it done ({ev['daily_done'][0]}): \"{ev['daily_done'][1][:90]}\" — close?"
    if disp == "PAYMENT":
        return "payment — confirm you've paid, then close"
    hint = f" (note names {ev['file'][0]})" if ev.get("file") else ""
    return "no completion evidence — left open" + hint


def close_task(gid, comment):
    stories = asana("GET", f"/tasks/{gid}/stories?opt_fields=text,type")
    tag = "asana-reconcile"
    if not any(s.get("type") == "comment" and tag in (s.get("text") or "")
               for s in stories):
        asana("POST", f"/tasks/{gid}/stories", {"text": comment})
    asana("PUT", f"/tasks/{gid}", {"completed": True})


# ---- main ------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    overdue_only = "--overdue-only" in args
    as_json = "--json" in args
    apply_auto = "--apply-auto" in args

    if "--ship" in args:
        # Layer 1: given shipped artefacts (gids or keywords), find+close matches.
        terms = [a for a in args[args.index("--ship") + 1:] if not a.startswith("--")]
        tasks = pull_open_tasks()
        hits = []
        for t in tasks:
            hay = (t.get("name", "") + " " + (t.get("notes", "") or "")).lower()
            for term in terms:
                if term.lower() in hay or term == t["gid"]:
                    hits.append((t, term))
                    break
        if not hits:
            print("No open tasks match the shipped artefact(s).")
            return
        print("Open tasks matching shipped artefact(s) — review before closing:")
        for t, term in hits:
            print(f"  [{prio(t)}] {t['name'][:60]} | {plabel(t)} | {t['gid']} (matched: {term})")
        print("\nRe-run with --ship ... --apply-auto to close these with an audit comment.")
        if apply_auto:
            for t, term in hits:
                close_task(t["gid"], f"Closed by asana-reconcile --ship 14 Jun: shipped this "
                                     f"session (matched artefact '{term}'). Verify the linked work landed.")
                print(f"  CLOSED {t['gid']}")
        return

    tasks = pull_open_tasks()
    buckets = {"AUTO": [], "PROPOSE": [], "PAYMENT": [], "OPEN": []}
    for t in tasks:
        due = t.get("due_on")
        is_od = bool(due and datetime.date.fromisoformat(due) < TODAY)
        if overdue_only and not is_od:
            continue
        disp, ev, od = classify(t)
        rec = recommend(disp, ev)
        buckets[disp].append({
            "gid": t["gid"], "name": t.get("name", ""), "prio": prio(t),
            "project": plabel(t), "due": due, "overdue_days": od,
            "disposition": disp, "evidence": {k: (v if not isinstance(v, list) else v[:3])
                                              for k, v in ev.items()},
            "recommendation": rec,
        })

    # --apply-auto closes ONLY the AUTO bucket (mechanical proof). Runs in both text
    # and --json modes so the weekly cron can close + report in one pass.
    auto_closed = []
    if apply_auto and buckets["AUTO"]:
        for r in buckets["AUTO"]:
            close_task(r["gid"], f"Closed by asana-reconcile (--apply-auto) {TODAY}: "
                                 f"{r['recommendation']}. Mechanical proof; surfaced for record.")
            auto_closed.append(r["gid"])

    if as_json:
        print(json.dumps({"buckets": buckets, "auto_closed": auto_closed},
                         indent=2, default=str))
        return

    order = [("AUTO", "Auto-closable (mechanical proof — close with --apply-auto)"),
             ("PROPOSE", "Propose to Pete (evidence found — one-word confirm)"),
             ("PAYMENT", "Payments (confirm you've paid)"),
             ("OPEN", "No evidence (left open)")]
    for key, title in order:
        rows = buckets[key]
        if not rows:
            continue
        if key == "OPEN":
            rows = [r for r in rows if r["overdue_days"] > 30]  # only surface very-stale OPEN
            if not rows:
                continue
            title += " — only those >30d overdue shown"
        print(f"\n### {title}  ({len(rows)})")
        for r in sorted(rows, key=lambda x: (x["prio"], -(x["overdue_days"] or 0))):
            od = f" OD{r['overdue_days']}d" if r["overdue_days"] else ""
            print(f"[{r['prio']}]{od} {r['gid']} | {r['name'][:58]}")
            print(f"      → {r['recommendation']}")

    if auto_closed:
        print(f"\n--apply-auto closed {len(auto_closed)} AUTO task(s): {', '.join(auto_closed)}")

    tot = sum(len(v) for v in buckets.values())
    print(f"\n{tot} open tasks scanned. AUTO {len(buckets['AUTO'])} · "
          f"PROPOSE {len(buckets['PROPOSE'])} · PAYMENT {len(buckets['PAYMENT'])} · "
          f"OPEN {len(buckets['OPEN'])}.")


if __name__ == "__main__":
    main()