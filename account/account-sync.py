#!/usr/bin/env python3
"""account-sync — reconcile SY-Clancy Asana <-> the account_* store.

Single-writer for Asana-derived rows only:
  - account_deliverables where source='asana' (keyed on source_ref = Asana gid)
  - account_actions where asana_gid is set
Manual deliverables (source='manual'), meetings, people, obligations, config are
NOT touched here — each has its own writer. Idempotent; safe to run repeatedly.

Completed SY-Clancy task -> deliverable. Open task -> action. When a task moves
done, its action is removed and a deliverable appears. Trivial internal-admin
sub-tasks are excluded so the delivery log stays the contract record, not a
micro-task dump.

Usage: python3 account-sync.py [--dry-run]
"""
import sys
import json
import datetime
import urllib.request
import urllib.parse
import urllib.error

sys.path.insert(0, f"{VAULT}/Library/processes/scripts/account")
import account_store as store
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

CUSTOMER = "clancy"
PROJ = "1214277900941306"
PAT = open(f"{VAULT}/Library/processes/secrets/asana-pat").read().strip()
DRY = "--dry-run" in sys.argv

# Pure internal-admin sub-tasks — not client deliverables (keeps the log meaningful).
EXCLUDE = {"1214280213348401", "1214280383947879", "1214280384413198", "1214280384319187", "1214280433280230"}
# No-charge / goodwill work (year-end review value).
GOODWILL = {"1215149411420874", "1214280383904538", "1214280383904828"}
# Map a few contractual deliverables to their obligation key.
OBLIG = {"1214280213296596": "quarterly-incident-report", "1214280084474963": "superuser-per-contract",
         "1214280267660381": "kpi-supmgr-4mo", "1214280143495543": "kpi-supmgr-4mo",
         "1214280267478471": "kpi-newstarter-6wk", "1215361861891309": "kpi-cat1plus-6mo"}


def asana(path, params):
    url = f"https://app.asana.com/api/1.0{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {PAT}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def pull():
    opt = "name,completed,completed_at,due_on,permalink_url,memberships.section.name,custom_fields.name,custom_fields.display_value"
    tasks, offset = [], None
    while True:
        p = {"completed_since": "2020-01-01", "opt_fields": opt, "limit": 100}
        if offset:
            p["offset"] = offset
        d = asana(f"/projects/{PROJ}/tasks", p)
        tasks += d["data"]
        nx = d.get("next_page")
        if nx and nx.get("offset"):
            offset = nx["offset"]
        else:
            break
    return tasks


def section(t):
    for m in (t.get("memberships") or []):
        s = (m.get("section") or {}).get("name")
        if s:
            return s
    return "General / admin"


def cf(t, n):
    for c in (t.get("custom_fields") or []):
        if c.get("name") == n:
            return c.get("display_value")
    return None


def contract_of(name, sec):
    s = (name + " " + sec).lower()
    if "ukpn" in s or "wayne" in s:
        return "UKPN"
    if "ventnor" in s or "southern" in s or "rod radar" in s:
        return "Southern"
    if "south east water" in s or "matt davis" in s:
        return "South-East-Water"
    if "scottish" in s or "zero strike" in s:
        return "Scottish-Water"
    if "imrds" in s or "anglian" in s or "action 4" in s:
        return "Anglian"
    return None


def main():
    tasks = pull()
    done = [t for t in tasks if t.get("completed") and t["gid"] not in EXCLUDE]
    opent = [t for t in tasks if not t.get("completed")]

    ex_deliv = {d["source_ref"]: d for d in store.select(
        "account_deliverables", f"customer=eq.{CUSTOMER}&source=eq.asana&select=id,source_ref")}
    ex_act = {a["asana_gid"]: a for a in store.select(
        "account_actions", f"customer=eq.{CUSTOMER}&asana_gid=not.is.null&select=id,asana_gid")}

    ins_d = upd_d = del_d = ins_a = upd_a = del_a = 0
    done_gids = set()
    for t in done:
        g = t["gid"]
        done_gids.add(g)
        sec = section(t)
        row = {"customer": CUSTOMER, "date": ((t.get("completed_at") or "")[:10] or None), "workstream": sec,
               "contract": contract_of(t["name"], sec), "title": t["name"], "obligation_key": OBLIG.get(g),
               "evidence_urls": ([t["permalink_url"]] if t.get("permalink_url") else []),
               "source": "asana", "source_ref": g, "charge": ("goodwill" if g in GOODWILL else "in-scope")}
        if g in ex_deliv:
            if not DRY:
                store.update("account_deliverables", f"id=eq.{ex_deliv[g]['id']}", row)
            upd_d += 1
        else:
            if not DRY:
                store.insert("account_deliverables", [row])
            ins_d += 1
    for ref, d in ex_deliv.items():
        if ref not in done_gids:
            if not DRY:
                store.delete("account_deliverables", f"id=eq.{d['id']}")
            del_d += 1

    open_gids = set()
    for t in opent:
        g = t["gid"]
        open_gids.add(g)
        sec = section(t)
        st = cf(t, "Status")
        row = {"customer": CUSTOMER, "title": t["name"], "owner_side": ("clancy" if st == "Awaiting client" else "sygma"),
               "due": t.get("due_on"), "status": (st or "open"), "workstream": sec,
               "contract": contract_of(t["name"], sec), "source_ref": g, "asana_gid": g}
        if g in ex_act:
            if not DRY:
                store.update("account_actions", f"id=eq.{ex_act[g]['id']}", row)
            upd_a += 1
        else:
            if not DRY:
                store.insert("account_actions", [row])
            ins_a += 1
    for gid, a in ex_act.items():
        if gid not in open_gids:
            if not DRY:
                store.delete("account_actions", f"id=eq.{a['id']}")
            del_a += 1

    if not DRY:
        store.set_state(CUSTOMER, "last_sync", datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(f"account-sync {'(DRY) ' if DRY else ''}{CUSTOMER}: "
          f"deliverables +{ins_d} ~{upd_d} -{del_d} | actions +{ins_a} ~{upd_a} -{del_a} "
          f"| asana done={len(done)} open={len(opent)}")
    if not DRY:
        store.daily_note_line(f"account-clancy-sync: deliverables +{ins_d}/~{upd_d}/-{del_d}, actions +{ins_a}/~{upd_a}/-{del_a} (Asana→store reconcile)")
        store.refresh_state_of_play(CUSTOMER)
        _cc_gate_backstop()


def _cc_gate_backstop():
    """Daily catch (added 18 Jun 2026): run account-cc-gate; if Clancy work is in the vault
    but never reached the CC store, log a warning line AND email Pete (exception-only, so it's
    silent when clean). This is the session-independent half of the merge-to-CC gate."""
    import subprocess
    import os
    HERE = os.path.dirname(os.path.abspath(__file__))
    try:
        g = subprocess.run([sys.executable, os.path.join(HERE, "account-cc-gate.py"),
                            "--customer", CUSTOMER, "--quiet"],
                           capture_output=True, text=True, timeout=90)
    except Exception as e:
        print("cc-gate backstop error:", e)
        return
    if g.returncode == 0:
        store.daily_note_line("CC-mirror gate: PASS (vault ↔ CC in sync)")
        return
    gaps = [l.strip()[1:].strip() for l in g.stdout.splitlines() if l.strip().startswith("X")]
    summary = "; ".join(gaps[:4]) or "hard gap — see account-cc-gate output"
    store.daily_note_line("⚠ CC-mirror gate FAIL — Clancy work not on the CC: " + summary)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "gmail_api", f"{VAULT}/Library/processes/scripts/gmail-api.py")
        gm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gm)
        body = ("The daily Clancy CC-mirror gate found work in the vault that has not reached the "
                "Command Centre store:\n\n" + "\n".join("- " + x for x in gaps) +
                "\n\nFix: log it to the store (account-log.py / plaud-extract.py / account_store), "
                "then re-run:\n  python3 Library/processes/scripts/account/account-cc-gate.py --customer clancy")
        gm.GmailAPI().send(to="pete.ashcroft@sygma-solutions.com",
                           subject="Clancy CC-mirror gap (account-cc-gate)", body=body)
        print("cc-gate: emailed Pete about", len(gaps), "gap(s)")
    except Exception as e:
        print("cc-gate email failed:", e)


if __name__ == "__main__":
    main()