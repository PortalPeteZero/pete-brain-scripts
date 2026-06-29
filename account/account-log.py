#!/usr/bin/env python3
"""account-log — manual capture into the account_* store.

The "log clancy ..." session verb and the Command Centre quick-add both write
through here, so anything Pete (or a session) does with a customer lands in the
live record without waiting for a cron. Asana-derived rows are owned by
account-sync; this writes session/manual rows only.

Examples:
  account-log.py deliverable --title "Reviewed Wayne's Q2 strike data" --workstream "Cable strikes & investigations" --contract UKPN
  account-log.py deliverable --title "Built the clamp poster" --charge goodwill --evidence https://...
  account-log.py action --title "Send Rebecca the KPI burn-down" --owner-side sygma --due 2026-06-20
  account-log.py risk --title "Trainer availability around jury duty" --severity Medium
  account-log.py document --title "Q2 review pack" --type Report --url https://...
  account-log.py contact --name "Jane Doe" --side clancy --role "H&S lead" --email jane@theclancygroup.co.uk
"""
import sys
import argparse
import datetime

sys.path.insert(0, f"{VAULT}/Library/processes/scripts/account")
import account_store as store
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["deliverable", "action", "risk", "document", "contact", "incident"])
    ap.add_argument("--customer", default="clancy")
    ap.add_argument("--title")
    ap.add_argument("--name")
    ap.add_argument("--workstream")
    ap.add_argument("--contract")
    ap.add_argument("--charge", default="in-scope")
    ap.add_argument("--summary")
    ap.add_argument("--obligation")
    ap.add_argument("--date")
    ap.add_argument("--evidence")
    ap.add_argument("--owner-side", dest="owner_side", default="sygma")
    ap.add_argument("--owner")
    ap.add_argument("--due")
    ap.add_argument("--status")
    ap.add_argument("--severity", default="Medium")
    ap.add_argument("--category")
    ap.add_argument("--mitigation")
    ap.add_argument("--type")
    ap.add_argument("--url")
    ap.add_argument("--vault")
    ap.add_argument("--location")
    ap.add_argument("--investigation")
    ap.add_argument("--sygma-role", dest="sygma_role")
    ap.add_argument("--side", default="clancy")
    ap.add_argument("--role")
    ap.add_argument("--email")
    ap.add_argument("--phone")
    ap.add_argument("--key", action="store_true")
    a = ap.parse_args()
    C = a.customer
    today = datetime.date.today().isoformat()

    if a.kind == "deliverable":
        ref = "log-" + today + "-" + (a.title or "")[:24].lower().replace(" ", "-")
        row = {"customer": C, "date": a.date or today, "workstream": a.workstream, "contract": a.contract,
               "title": a.title, "summary": a.summary, "obligation_key": a.obligation,
               "evidence_urls": ([a.evidence] if a.evidence else []), "source": "session",
               "source_ref": ref, "charge": a.charge, "created_by": "log-clancy"}
        st = store.insert("account_deliverables", [row])
    elif a.kind == "action":
        # Actions are public.tasks (SY-Clancy) now — account_actions retired in the Clancy rebuild.
        row = {"name": a.title, "project_slug": "SY-Clancy", "entity_slug": "Sygma",
               "bucket": a.contract or a.workstream or "General", "status": "todo", "source": "log-clancy",
               "tags": (["side:clancy"] if a.owner_side == "clancy" else []), "due_on": a.due}
        st = store.insert("tasks", [row])
    elif a.kind == "risk":
        row = {"customer": C, "title": a.title, "severity": a.severity, "category": a.category,
               "owner": a.owner, "mitigation": a.mitigation, "status": a.status or "open"}
        st = store.insert("account_risks", [row])
    elif a.kind == "document":
        row = {"customer": C, "title": a.title, "type": a.type, "url": a.url, "vault_path": a.vault,
               "status": a.status, "contract": a.contract, "workstream": a.workstream}
        st = store.insert("account_documents", [row])
    elif a.kind == "contact":
        row = {"customer": C, "name": a.name, "side": a.side, "role": a.role, "email": a.email,
               "phone": a.phone, "contract": a.contract, "is_key": a.key}
        st = store.insert("account_people", [row])
    elif a.kind == "incident":
        # Damages are the first-class clancy_damages table now — account_incidents retired.
        row = {"customer": C, "job_ref": a.title, "damage_date": a.date or today, "contract": a.contract,
               "location": a.location, "status": a.status or "New",
               "summary": " · ".join(x for x in (a.investigation, a.sygma_role) if x) or None}
        st = store.insert("clancy_damages", [row])

    store.set_state(C, "last_log", datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(f"account-log: {a.kind} -> {C} (HTTP {st})")


if __name__ == "__main__":
    main()