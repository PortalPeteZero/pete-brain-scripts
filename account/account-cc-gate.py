#!/usr/bin/env python3
"""account-cc-gate — prove a customer's vault content is mirrored into the Command Centre store.

THE "MERGE TO CC" GATE. Any structured account work in the vault that has not reached the
`account_*` store (so it would never show on the Command Centre) is surfaced here. It is
deliberately NOT Plaud/meeting specific — it walks the whole customer folder and checks the
three things that must mirror:

  1. MATTERS -> workstreams   every matter folder must map to a CC workstream
                              (config `workstreams`). Declare `cc_workstream: "<title>"` in a
                              matter README for an exact check; otherwise a token match is tried.
  2. MEETINGS -> account_meetings   every `type: meeting` note dated within the last 21 days
                              must have a store row (matched by date). Older notes are advisory.
                              Add `cc_skip: true` to a note to exclude it.
  3. DOCUMENTS -> account_documents   substantive source files (pdf/docx/xlsx/pptx) not
                              registered as CC documents are reported as an advisory count.
  4. STRIKES -> account_config.strikes   every vault `cable-strikes-and-investigations/
                              investigations/{ref}/` folder must have a matching strike record
                              (matched on the README `ref`) so it shows on the CC Strikes tab.
                              A config.strikes record with no vault folder is advisory.

Exit 1 on any HARD gap (matter or recent-meeting) so it can BLOCK session sign-off. Advisory
items never fail the gate. This is the automatic catch that stops "Clancy work that never
reached the CC" from happening silently again (root cause, 18 Jun 2026).

Usage: account-cc-gate.py [--customer clancy] [--docs] [--quiet]
"""
import os
import re
import sys
import glob
import datetime

ACC = f"{VAULT}/Library/processes/scripts/account"
sys.path.insert(0, ACC)
import account_store as store
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")

VAULT = VAULT
CUST_DIR = {"clancy": "Customers/SY-Clancy"}
NON_MATTER = {"source", "assets", "deploy", "extracts", "__pycache__", "data"}
RECENT_DAYS = 21
DOC_EXT = {"pdf", "docx", "xlsx", "pptx"}


def frontmatter(path):
    try:
        t = open(path, encoding="utf-8").read()
    except Exception:
        return {}
    if not t.startswith("---"):
        return {}
    end = t.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in t[3:end].splitlines():
        m = re.match(r"\s*([a-zA-Z_]+):\s*(.*)", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return fm


def toks(s):
    drop = {"and", "the", "of", "admin"}  # 'admin' so 'General / admin' ~ folder 'general'
    return set(w for w in re.split(r"[^a-z0-9]+", s.lower()) if w and w not in drop)


def main():
    customer = "clancy"
    show_docs = "--docs" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--customer" and i + 1 < len(sys.argv):
            customer = sys.argv[i + 1]
    base = os.path.join(VAULT, CUST_DIR.get(customer, ""))
    if not os.path.isdir(base):
        print(f"account-cc-gate: no vault dir for customer '{customer}'")
        sys.exit(0)
    today = datetime.date.today()
    hard, advisory = [], []

    cfgrows = store.select("account_config", f"customer=eq.{customer}&key=eq.workstreams&select=value")
    ws_titles = cfgrows[0]["value"] if cfgrows else []
    ws_tok = [toks(w) for w in ws_titles]

    # 1) MATTERS -> workstreams
    matters = 0
    for d in sorted(os.listdir(base)):
        p = os.path.join(base, d)
        if not os.path.isdir(p) or d in NON_MATTER:
            continue
        if not os.path.exists(os.path.join(p, "README.md")):
            continue
        fm = frontmatter(os.path.join(p, "README.md"))
        if fm.get("type") == "redirect" or str(fm.get("status", "")).lower() == "superseded":
            continue  # folded/redirect stub, not a live workstream
        matters += 1
        declared = fm.get("cc_workstream")
        if declared:
            ok = declared in ws_titles
            why = f"declared cc_workstream '{declared}' not in config" if not ok else ""
        else:
            dt = toks(d)
            ok = any(dt and (dt <= wt or wt <= dt) for wt in ws_tok)
            why = "no token-matching workstream (add `cc_workstream:` to its README or to the config)"
        if not ok:
            hard.append(f"MATTER '{d}': {why}")

    # 2) MEETINGS -> account_meetings (by date)
    mtg_dates = set(m.get("date") for m in store.select("account_meetings", f"customer=eq.{customer}&select=date"))
    meetings_seen = 0
    for f in glob.glob(os.path.join(base, "**", "*.md"), recursive=True):
        fm = frontmatter(f)
        if fm.get("type") != "meeting":
            continue
        if str(fm.get("cc_skip", "")).lower() == "true":
            continue
        meetings_seen += 1
        date = fm.get("date")
        if not date:
            mm = re.match(r"(\d{4}-\d{2}-\d{2})", os.path.basename(f))
            date = mm.group(1) if mm else None
        rel = os.path.relpath(f, VAULT)
        if date in mtg_dates:
            continue
        recent = False
        try:
            recent = (today - datetime.date.fromisoformat(date)).days <= RECENT_DAYS
        except Exception:
            recent = False
        msg = f"MEETING '{rel}' (date {date}): no account_meetings row — not on the CC Meetings tab"
        (hard if recent else advisory).append(msg + ("" if recent else "  [older — advisory]"))

    # 3) DOCUMENTS (advisory) -> account_documents by vault_path
    doc_paths = set((d.get("vault_path") or "").rstrip("/") for d in store.select("account_documents", f"customer=eq.{customer}&select=vault_path") if d.get("vault_path"))
    unreg = []
    for f in glob.glob(os.path.join(base, "**", "*"), recursive=True):
        if os.path.isdir(f):
            continue
        ext = f.lower().rsplit(".", 1)[-1] if "." in f else ""
        if ext not in DOC_EXT:
            continue
        rel = os.path.relpath(f, VAULT)
        if not any(rel == dp or rel.startswith(dp + "/") or rel.startswith(dp) for dp in doc_paths):
            unreg.append(rel)

    # 4) STRIKES -> account_config.strikes (vault investigations/ <-> CC Strikes tab)
    strow = store.select("account_config", f"customer=eq.{customer}&key=eq.strikes&select=value")
    strike_refs = set()
    if strow and isinstance(strow[0].get("value"), list):
        strike_refs = {str(s.get("ref")) for s in strow[0]["value"] if s.get("ref")}
    inv_dir = os.path.join(base, "cable-strikes-and-investigations", "investigations")
    vault_refs = {}
    if os.path.isdir(inv_dir):
        for d in sorted(os.listdir(inv_dir)):
            p = os.path.join(inv_dir, d)
            rm = os.path.join(p, "README.md")
            if not os.path.isdir(p) or not os.path.exists(rm):
                continue  # skips the index README.md (a file, not a dir)
            ref = str(frontmatter(rm).get("ref") or d)
            vault_refs[ref] = d
    strikes_seen = len(vault_refs)
    for ref, d in vault_refs.items():
        if ref not in strike_refs:
            hard.append(f"STRIKE '{d}' (ref {ref}): no account_config.strikes entry — not on the CC Strikes tab")
    for ref in strike_refs:
        if ref not in vault_refs:
            advisory.append(f"STRIKE config.strikes '{ref}': no vault investigations/ folder  [soft]")

    # report
    print(f"=== account-cc-gate: {customer} ===")
    print(f"matters checked: {matters} | config workstreams: {len(ws_titles)} | "
          f"meeting notes: {meetings_seen} | store meetings: {len(mtg_dates)} | "
          f"strikes: {strikes_seen} vault / {len(strike_refs)} store | "
          f"unregistered source docs: {len(unreg)}")
    if hard:
        print(f"\n[HARD] {len(hard)} CC mirror gap(s) — sign-off should not complete until cleared:")
        for g in hard:
            print("  X", g)
    if advisory:
        print(f"\n[advisory] {len(advisory)} older/soft item(s):")
        for g in advisory[:20]:
            print("  -", g)
    if unreg and (show_docs or not hard):
        print(f"\n[advisory] {len(unreg)} source doc(s) not registered as CC documents"
              + (":" if show_docs else " (run with --docs to list)"))
        if show_docs:
            for g in unreg[:50]:
                print("  -", g)
    if not hard:
        print("\nPASS — no hard CC mirror gaps." + (" (advisory items above.)" if (advisory or unreg) else " Vault and CC in sync."))
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()