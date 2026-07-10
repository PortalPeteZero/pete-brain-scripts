#!/usr/bin/env python3
# CRON-META
# what: Nightly Enquiry Engine reconciler — compares the three systems (Gmail tray, Portal CRM, CC tasks/ledger) plus Sue's CRM activity, and reports every drift line in plain English to the morning brief (daily_log). Zero drift = one silent OK line.
# why: The 2026-07-09 audit found live drift in every class (won deals sitting at Quoted, chases on closed deals, tray items in no system, ledger rows pointing at deleted contacts, double-captures). Drift is invisible until something is compared — this is the comparison, nightly (hardening plan P4.1).
# reads: Gmail (enquiry tray labels), Portal contacts/contact_activities, CC public.tasks + enquiry_touches + ee_public_courses
# writes: CC daily_log (cron_name='ee-reconcile', one plain-English report per run)
# entity: sygma
# schedule: 45 6 * * *
# timezone: Atlantic/Canary
# secrets: GOOGLE_SA_JSON, SUPABASE_TOKEN, SECRETFILE__sygma-portal-supabase-keys__json
# CRON-META-END
"""ee-reconcile.py — the EE nightly drift reconciler (hardening plan P4.1).

Drift classes (one plain-English line each; the morning brief only — it NEVER creates tasks, per D2):
  1. tray-unanswered      — an enquiry thread in the tray with no Portal contact, or sat > N days
  2. quoted-and-quiet     — a Quoted contact with no activity for > N working days and not in the tray
  3. stray/overdue chases — open enquiry-engine tasks (should not exist under D2 unless opted in)
  4. sue-says-booked      — a colleague activity says booked/won but the stage disagrees (Sue acts first)
  5. ledger-orphans       — enquiry_touches rows pointing at contacts that no longer exist
  6. double-captures      — the same Gmail message captured twice (belt + braces over the unique index)
  7. duplicate-people     — the same email (case-insensitive) on two contact rows
A places_left NULL is NOT drift by itself (defined semantics: count unknown, never quoted).

Run by hand any time:  VAULT=/tmp/pbs python3 /tmp/pbs/ee-reconcile.py [--dry]
"""
import os, sys, json, re, datetime as dt, importlib.util

VAULT = os.environ.get("VAULT", "/tmp/pbs")
N_DAYS = 5   # working-day quiet threshold (plan: N = 5)

def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

tl = _load("telog", f"{VAULT}/te-log.py")

def working_days_ago(n):
    d = dt.datetime.now(dt.timezone.utc)
    steps = 0
    while steps < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            steps += 1
    return d

def main():
    dry = "--dry" in sys.argv
    drift = []
    cutoff = working_days_ago(N_DAYS).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Gmail tray (fail-soft: Gmail down ≠ no report)
    tray_senders = {}
    try:
        gm = _load("gm", f"{VAULT}/gmail-api.py")
        g = gm.GmailAPI()
        threads = g.search_threads("label:Projects/SY-Training-Enquiries label:Replies", max_results=50) or []
        for t in threads:
            try:
                full = g.get_thread(t["id"])
                msgs = full.get("messages", [])
                hdrs = {h["name"].lower(): h["value"] for h in msgs[0].get("payload", {}).get("headers", [])}
                m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", hdrs.get("from", ""))
                sender = (m.group(0).lower() if m else None)
                first_ms = int(msgs[0].get("internalDate", "0")) / 1000 if msgs[0].get("internalDate") else None
                age_days = (dt.datetime.now(dt.timezone.utc).timestamp() - first_ms) / 86400 if first_ms else None
                tray_senders[t["id"]] = (sender, age_days)
            except Exception:
                tray_senders[t["id"]] = (None, None)
        # 1. tray items
        for tid, (sender, age) in tray_senders.items():
            contact = tl.portal_get("contacts", select="id", email=f"ilike.{sender}") if sender else []
            if sender and not contact:
                drift.append(f"tray-unanswered: thread {tid} from {sender} has NO CRM contact (exists only in Gmail)")
            if age and age > N_DAYS + 2:
                drift.append(f"tray-unanswered: thread {tid} ({sender or 'unknown sender'}) has sat in the tray ~{int(age)} days")
    except Exception as e:
        drift.append(f"(tray check unavailable: {type(e).__name__} — verify Gmail access)")

    # 2. quoted-and-quiet — judged COMPANY-wide (a chase to a colleague counts for the deal)
    quoted = tl.portal_get("contacts", select="id,full_name,email,updated_at", stage_id="eq.2")
    tray_emails = {s for s, _ in tray_senders.values() if s}
    for c in quoted:
        em = (c.get("email") or "").lower()
        dom = em.split("@")[-1] if "@" in em else None
        fam_ids = [c["id"]]
        if dom and dom not in tl.FREEMAIL:
            fam_ids = [x["id"] for x in tl.portal_get("contacts", select="id", email=f"ilike.*%40{dom}")] or fam_ids
        # last CONTACT = the newest ACTIVITY anywhere in the family; updated_at only as a fallback
        # (contact rows auto-touch updated_at on any patch, which would mask a genuinely quiet deal)
        act_dates = []
        for fid in fam_ids:
            acts = tl.portal_get("contact_activities", select="occurred_at", contact_id=f"eq.{fid}",
                                 order="occurred_at.desc", limit="1")
            if acts:
                act_dates.append(str(acts[0]["occurred_at"]))
        last = max(act_dates) if act_dates else str(c.get("updated_at") or "")
        if last and last < cutoff and em not in tray_emails:
            drift.append(f"quoted-and-quiet: {c['full_name']} <{c.get('email')}> — quoted, whole company silent since {str(last)[:10]}, nobody tracking")

    # 3. stray/overdue enquiry-engine chases (D2: none should exist)
    chases = tl.cc_sql("SELECT id, name, notes FROM tasks WHERE status='todo' AND source='enquiry-engine'")
    for ch in (chases or []):
        drift.append(f"stray-chase: open EE chase '{ch['name'][:70]}' (D2 says none — close it or justify)")

    # 4. Sue-says-booked (colleague activity vs stage)
    cutoff14 = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    acts = tl.portal_get("contact_activities", select="contact_id,body,subject,created_by_name,occurred_at",
                         occurred_at=f"gte.{cutoff14}", order="occurred_at.desc", limit="200")
    seen = set()
    for a in acts or []:
        who = (a.get("created_by_name") or "").lower()
        text = ((a.get("body") or "") + " " + (a.get("subject") or "")).lower()
        if "engine" in who or not who:
            continue
        if re.search(r"\bbooked\b|\bbooking confirmed\b|\bwon\b", text) and a["contact_id"] not in seen:
            seen.add(a["contact_id"])
            c = tl.portal_get("contacts", select="full_name,email,stage_id", id=f"eq.{a['contact_id']}")
            if c and c[0].get("stage_id") != 3:
                drift.append(f"sue-says-booked: {c[0]['full_name']} <{c[0].get('email')}> — '{(a.get('body') or a.get('subject'))[:60]}' by {a.get('created_by_name')} but stage is {c[0].get('stage_id')} not Customer")

    # 5. ledger orphans
    ids = tl.cc_sql("SELECT DISTINCT contact_id FROM enquiry_touches WHERE contact_id IS NOT NULL")
    for r in ids or []:
        if not tl.portal_get("contacts", select="id", id=f"eq.{r['contact_id']}"):
            drift.append(f"ledger-orphan: enquiry_touches rows point at deleted contact {r['contact_id']}")

    # 6. double-captures
    dbl = tl.cc_sql("SELECT message_id, count(*) n FROM enquiry_touches WHERE message_id IS NOT NULL GROUP BY message_id HAVING count(*) > 1")
    for d in dbl or []:
        drift.append(f"double-capture: message {d['message_id']} captured {d['n']}×")

    # 7. duplicate people — paginated (PostgREST caps a page at 1,000 rows)
    counts, offset = {}, 0
    while True:
        page = tl.portal_get("contacts", select="email", email="not.is.null",
                             limit="1000", offset=str(offset)) or []
        for c in page:
            e = (c.get("email") or "").lower().strip()
            if e:
                counts[e] = counts.get(e, 0) + 1
        if len(page) < 1000:
            break
        offset += 1000
    for e, n in counts.items():
        if n > 1:
            drift.append(f"duplicate-person: {e} exists on {n} contact rows")

    # report
    today = dt.date.today().isoformat()
    if drift:
        body = f"## EE reconcile — {len(drift)} drift line(s)\n" + "\n".join(f"- {d}" for d in drift)
    else:
        body = "## EE reconcile — zero drift. All three systems agree."
    print(body)
    if not dry:
        tl.cc_sql("INSERT INTO daily_log (date, cron_name, content) VALUES "
                  f"({tl.lit(today)}, 'ee-reconcile', {tl.lit(body)})")
        print(f"\n✓ written to daily_log ({today})")
    sys.exit(0)   # reporting cron: drift is REPORTED, not a crash

if __name__ == "__main__":
    main()
