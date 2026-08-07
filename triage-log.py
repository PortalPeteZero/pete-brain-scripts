#!/usr/bin/env python3
"""triage-log.py -- the Triage Engine capture-on-decision tool (P2; the te-log twin).

Captures every triage decision as a triage_decisions ledger row and executes the decision's
side-effects as a TRIPLE-WRITE with a loud post-check:

  1. Gmail        -- label / archive per the final verb (the verb->primitive map in the
                     inbox-triage skill; Reply adds Replies, Task adds filing label only, ...)
  2. public.tasks -- close/update on evidence; CREATE only when the payload carries an explicit
                     Pete-confirmed task (interactive sessions only -- the standing rule forbids
                     AUTO-creation, not Pete-directed creation; the created task's id is recorded
                     on the decision row's task_id and the post-check re-reads it)
  3. triage_decisions -- the ledger row (proposed vs final, the learning substrate)

POST-CHECK: re-reads all three systems, prints one ✓/✗ line each, EXITS NON-ZERO on any ✗
(the te-log P2 semantics -- never "reported success on failed capture").

DRY-RUN BY DEFAULT -- nothing mutates without --apply. --manifest <path> appends one JSON line
per side-effect so any run is reversible (the te-log pattern).

IDEMPOTENT on the triggering Gmail message_id: a re-run of the same payload is a FULL NO-OP for
any row that is Pete-decided, overridden, applied, or carries a send_status; only a row still in
the pending-proposal state may be updated (the ledger's re-run semantics).

WRITE-ORDER RULE: the ledger row goes to 'applying' BEFORE any Gmail mutation, then flips to
'applied' -- record before action, at every mutating level.

Payload (one decision or a list):
{
  "thread_id": "...", "message_id": "...", "sender": "who@dom",
  "proposed": {"ask": "...", "verb": "...", "label": "...", "project": "...", "priority": "..."},
  "final":    {"ask": "...", "verb": "File", "label": "Receipts", "project": null, "priority": null},
  "decided_by": "pete",                     # pete | cron-proposed | cron-auto
  "overridden": false, "override_reason": ["wrong_label"],   # required if overridden
  "create_task": {"name": "...", "priority": "P2", "due_on": "2026-08-12", "entity_slug": "...",
                  "project_slug": "...", "notes": "..."},   # ONLY on an explicit Pete confirmation
                  # due_on is OPTIONAL and the date is the switch: set it and the DB trigger makes
                  # the task a PD. Bills MUST carry it. Omit it for undated P1-P4.
  "drive_home": "Sygma Hub|Vehicles/YT24 XHB",  # a judged Drive home; ALWAYS wins over the label
  "close_task_id": "<uuid>"                 # close-on-evidence
}

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-log.py --in decisions.json            # dry run
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-log.py --in decisions.json --apply
  VAULT=/tmp/pbs python3 /tmp/pbs/triage-log.py --demo                         # P2 gate
"""
import os, sys, json, re, subprocess, traceback, datetime as dt

VAULT = os.environ.get("VAULT", "/tmp/pbs")
sys.path.insert(0, VAULT)
import importlib
tl = importlib.import_module("triage_lib")


def a(v):  # sql text[] literal
    return "ARRAY[" + ",".join("'" + tl.esc(x) + "'" for x in v) + "]::text[]" if v else "NULL"


def q(v):  # nullable quoted
    return "NULL" if v is None else "'" + tl.esc(v) + "'"


def existing_row(message_id):
    rows = tl.cc_sql(f"SELECT * FROM triage_decisions WHERE message_id='{tl.esc(message_id)}'")
    return rows[0] if rows else None


def row_is_pending(row):
    return (row["decided_by"] in ("cron-proposed", "cron-auto") and not row["overridden"]
            and row["apply_status"] is None and row["send_status"] is None)


def bank_exemplar(dec):
    """On an override, upsert a triage_cases exemplar -- the learning substrate the brain reads.
    Lives in triage_cases (NOT on the mutable ledger row), so a later re-decision can never
    unlearn it. Keyed on the source message_id (a second correction updates the same case)."""
    mid = dec["message_id"]
    fin = dec.get("final") or {}
    reason = dec.get("correction_reason") or (dec.get("override_reason") or [None])[0]
    payload = {"ask": fin.get("ask"), "verb": fin.get("verb"), "label": fin.get("label"),
               "project": fin.get("project"), "priority": fin.get("priority"), "reason": reason,
               "sender": dec.get("sender"), "subject_gist": dec.get("subject_gist"),
               "body_gist": dec.get("body_gist")}
    pj = "'" + tl.esc(json.dumps(payload)) + "'::jsonb"
    if tl.cc_sql(f"SELECT id FROM triage_cases WHERE source_message_id='{tl.esc(mid)}'"):
        tl.cc_sql(f"UPDATE triage_cases SET payload={pj}, type='content', sender={q(dec.get('sender'))}, "
                  f"subject_gist={q(dec.get('subject_gist'))}, body_gist={q(dec.get('body_gist'))}, "
                  f"active=true WHERE source_message_id='{tl.esc(mid)}'")
    else:
        tl.cc_sql("INSERT INTO triage_cases (type, sender, subject_gist, body_gist, payload, source_message_id) "
                  f"VALUES ('content', {q(dec.get('sender'))}, {q(dec.get('subject_gist'))}, "
                  f"{q(dec.get('body_gist'))}, {pj}, {q(mid)})")


def capture_walker(dec, apply=False, manifest=None):
    """Append-only Replies-tray event row: one row per (thread, outcome), synthetic timestamped
    message_id (collision-proof under UNIQUE(message_id)); excluded from learning metrics."""
    outcome = dec.get("outcome") or "defer"
    tid = dec["thread_id"]
    syn = dec.get("message_id") or f"{tid}:walker:{outcome}:{dt.datetime.now(dt.timezone.utc).isoformat()}"
    lines = [f"  walker[{outcome}] {tid[:20]}…"]
    if not apply:
        lines.append("  DRY would append walker row")
        return True, lines
    tl.cc_sql("INSERT INTO triage_decisions (thread_id, message_id, sender, decided_by, action, "
              "outcome, parent_id, apply_status, applied_at) VALUES ("
              f"{q(tid)}, {q(syn)}, {q(dec.get('sender'))}, 'pete', 'walker', {q(outcome)}, "
              f"{q(dec.get('parent_id'))}, 'applied', now())")
    if manifest:
        manifest.write(json.dumps({"step": "walker", "thread_id": tid, "outcome": outcome}) + "\n")
    if outcome in ("send", "de-tray", "already-done") and not dec.get("no_gmail"):
        try:
            g = tl.gmail()
            labels = {l["name"]: l["id"] for l in g.list_labels()}
            g.modify_thread(tid, remove=[labels.get("Replies", "Replies")])
        except Exception as e:
            lines.append(f"  ✗ Gmail strip Replies: {e}")
            return False, lines
    lines.append("  ✓ walker row appended")
    return True, lines


# Labels that carry no entity home -- operational trays, noise buckets, Pete's own briefings.
# Enrichment is skipped for these by design (the skill's documented skip rules).
_NO_ENRICH_PREFIX = ("Briefings", "Newsletters", "Receipts", "Shipping", "Alerts", "Travel",
                     "Replies", "Delegated", "Invoices", "Voice-Mail", "CloudTalk", "Xero-Forwarded",
                     "General/PA-General", "Pete Learning")


def _abs_home(drive, path):
    mount = os.path.expanduser(
        "~/Library/CloudStorage/GoogleDrive-pete.ashcroft@sygma-solutions.com")
    # "My Drive" is NOT a shared drive -- it sits beside "Shared drives" under the mount.
    if drive == "My Drive":
        return os.path.join(mount, "My Drive", path)
    return os.path.join(mount, "Shared drives", drive, path)


def _drive_home(label):
    """Resolve a Gmail filing label to the ABSOLUTE Drive folder that is its canonical home.

    Returns a path, None (no row -- refuse), or the string 'BUCKET' (the label is a catch-all and
    has no single home; the caller must use the per-thread judged home instead).

    LOOKED UP, NEVER DERIVED. The mapping lives in `public.gmail_label_homes`, one hand-written row
    per label. A label with no row returns None and the caller REFUSES to enrich, loudly.

    Why it is a registry and not a match: the first cut of this resolved 'the shortest folder whose
    path ends in the label's leaf'. It sent `Customers/CD-LeakGuard` to `Canary Detect/Pictures/
    Leakguard` (an asset dump) and `Businesses/SY-Vehicles` to `Canary Detect/Vehicles` (the wrong
    entity entirely -- three drives have a Vehicles folder). Pete, 28 Jul 2026: "this also needs
    fixing". A folder-name match is a guess wearing a lookup's clothes, and a wrong home is worse
    than no home -- it files a customer's documents into someone else's account in silence.
    """
    rows = tl.cc_sql("SELECT drive, path, is_bucket FROM gmail_label_homes WHERE label='%s'"
                     % tl.esc(label))
    if not rows:
        return None
    if rows[0].get("is_bucket"):
        return "BUCKET"
    return _abs_home(rows[0]["drive"], rows[0]["path"])


def _enrich(dec, fin, lines, manifest):
    """Run vault-enricher on the thread, into the filing label's canonical Drive home.

    Returns True (enriched), None (deliberately skipped) or False (should have enriched, could not).
    A `skipped:true` from the enricher is a FAILURE to surface, not a success -- that is how the
    'ran it, logged nothing' silence happened before.
    """
    label = fin.get("label") or ""
    judged = dec.get("drive_home")
    # A JUDGED HOME ALWAYS WINS -- including over the label skip-lists below. Until 4 Aug 2026 the
    # skip ran first, so a thread filed to the bare `General` bucket had its explicit drive_home
    # silently discarded and enrich returned None with no line printed at all: a signed vehicle hire
    # agreement never reached Drive and NOTHING said so. The skip-lists exist because those labels
    # carry no single home of their own -- that reasoning does not apply once the caller has read
    # the thread and named one. Same principle as the comment below: the label is a filing bucket,
    # not a statement of what the thread is about.
    if not judged:
        if label.startswith(_NO_ENRICH_PREFIX) or label in ("General", "CD-Info"):
            return None
        if not label.startswith(("Customers/", "Suppliers/", "Projects/", "Accreditations/",
                                 "Businesses/", "Personal/", "General/", "Working-Groups/")):
            return None
    # THE HOME IS A JUDGEMENT, NOT A LOOKUP (Pete, 28 Jul 2026: "you are relying on labels too much
    # and not even reading the emails"). The label is a filing bucket; it is not a statement of what
    # the thread is ABOUT. `Suppliers/CD-Carburos` happens to be both, so a registry row is right
    # there. `Businesses/SY-Finance` is 277 threads spanning payroll, HMRC, insurance and invoices
    # across three Sygma entities -- no single folder can be right for them, and a registry row
    # would just let every one of them be filed somewhere wrong and reported ✓.
    # So: a home judged per thread (from actually reading it) ALWAYS wins; the registry is only the
    # default for labels where one home genuinely fits; and a label flagged is_bucket refuses a
    # lookup outright and demands the judged home.
    home = None
    if judged:
        home = judged if os.path.isabs(judged) else _abs_home(*judged.split("|", 1))
    else:
        home = _drive_home(label)
        if home == "BUCKET":
            lines.append(f"  ! enrich: '{label}' is a catch-all bucket with no single Drive home. "
                         f"The home has to come from reading THIS thread, not from the label. "
                         f"Thread is filed and labelled; content NOT pulled. Re-run with "
                         f"drive_home on the judgment (absolute path, or 'Drive|path').")
            return False
    if not home:
        lines.append(f"  ! enrich: '{label}' has no row in gmail_label_homes -- REFUSING to guess a "
                     f"folder. Thread is filed and labelled; its attachments are NOT pulled. Either "
                     f"put drive_home on the judgment, or add the row:")
        lines.append(f"      INSERT INTO gmail_label_homes (label, drive, path, confirmed_by) "
                     f"VALUES ('{label}', '<drive>', '<path>', '<who>');")
        return False
    if not os.path.isdir(home):
        lines.append(f"  ! enrich: home for '{label}' is registered but not on disk ({home}) -- "
                     f"content NOT pulled (Drive not synced, or the folder moved)")
        return False
    try:
        r = subprocess.run([sys.executable, os.path.join(VAULT, "vault-enricher.py"),
                            dec["thread_id"], home],
                           capture_output=True, text=True, timeout=180,
                           env={**os.environ, "VAULT": VAULT})
        out = r.stdout or ""
        js = out[out.find("{"):out.rfind("}") + 1] if "{" in out else "{}"
        res = json.loads(js) if js.strip() else {}
    except Exception as e:
        lines.append(f"  ! enrich: {e}")
        return False
    if res.get("skipped"):
        lines.append(f"  ! enrich: SKIPPED ({res.get('skip_reason')}) -- content NOT pulled into {label}")
        return False
    # NB "0 new" is the idempotent case (the file is already in the folder), NOT a failure. Say so,
    # or a clean re-run reads like a silent miss and sends the next session hunting a non-bug.
    # And do NOT dress a missing extract up as "already present" -- extract_path is None when the
    # enricher wrote nothing, which is not the same claim and was not checked.
    n = len(res.get("attachments_pulled") or [])
    ex = res.get("extract_path")
    lines.append(f"  ✓ enrich: {n} new attachment(s), extract "
                 f"{'written' if ex else 'none'} -> {label}")
    if manifest:
        manifest.write(json.dumps({"step": "enrich", "thread_id": dec["thread_id"],
                                   "home": home, "attachments": n}) + "\n")
    return True


# The definition of "substantive" below is COPIED FROM entity-enrich-signoff.py's SQL on purpose.
# If the two ever disagree, the gate fails on rows this never writes, or passes on rows it should
# have caught. Change both together.
_SUBSTANTIVE_ASKS = ("reply", "decision", "review", "rsvp")
_KNOWLEDGE_PREFIXES = ("Customers/", "Suppliers/", "Projects/")
_ACTIVITY_MARK = "<!-- TRIAGE-ACTIVITY (newest first, appended by triage-log) -->"
_ACTIVITY_KEEP = 20


def _is_substantive(fin):
    verb = (fin.get("verb") or "")
    if verb == "Route":                      # EE handoff; the EE arm owns that corpus
        return False
    if not (fin.get("label") or "").startswith(_KNOWLEDGE_PREFIXES):
        return False
    return ((fin.get("ask") or "") in _SUBSTANTIVE_ASKS
            or verb.startswith(("Reply", "Task", "Hand to")))


def _ee_intake(dec, lines, manifest):
    """Capture a routed enquiry's ARRIVAL into the Enquiry Engine. True / None / False.

    Deliberately NOT a blind te-log call. `ee-payload.py` derives what the record can give (thread,
    message_id, subject, every inbound message, and the contact WHEN the thread carries a customer
    address). For a WEBSITE FORM it cannot: the envelope sender is info@sygma-solutions.com, so
    full_name and email come back empty and te-log would die on contacts.full_name NOT NULL -- and a
    web form is the commonest enquiry shape. Guessing a contact out of form text is a judgement, and
    a wrong CRM contact is worse than a missing one.

    So: capture when the record can answer, and REFUSE TO BE SILENT when it cannot -- print the exact
    command with what is missing, and fail the run. Either way the caller post-checks for the row.
    """
    tid = dec.get("thread_id")
    if not tid:
        return None
    if tl.cc_sql("SELECT 1 FROM enquiry_touches WHERE thread_id='%s' LIMIT 1" % tl.esc(tid)):
        lines.append("  = ee-intake: already captured for this thread — no duplicate")
        return True
    try:
        import tempfile
        out = os.path.join(tempfile.gettempdir(), f"ee-intake-{tid}.json")
        subprocess.run([sys.executable, os.path.join(VAULT, "ee-payload.py"),
                        "--thread", tid, "--kind", "enquiry", "--out", out],
                       capture_output=True, text=True, timeout=180)
        p = json.load(open(out))
    except Exception as e:
        lines.append(f"  ✗ ee-intake: could not build the payload ({type(e).__name__}: {str(e)[:70]}) "
                     f"— the enquiry is LABELLED BUT NOT IN THE ENGINE. Capture it by hand.")
        return False

    if not (p.get("full_name") and p.get("email")):
        why = p.get("_derivation", {}).get("contact", "no customer address on the thread")
        lines.append(f"  ✗ ee-intake: NOT captured — the contact cannot be derived ({why}). This is "
                     f"normal for a website form, where the sender is info@. The enquiry is labelled "
                     f"and trayed but exists in NO EE system. Capture it with the customer's own "
                     f"name/email from the form body:")
        lines.append(f"      VAULT={VAULT} python3 {VAULT}/te-log.py --in <payload>.json --apply "
                     f"--no-file --no-gmail     # thread_id={tid}, draft at {out}")
        return False

    p["activity"] = {**(p.get("activity") or {}), "kind": "enquiry"}
    for k in ("classification", "retrieval_refs"):      # judgement placeholders te-log does not need
        if isinstance(p.get(k), str) and "JUDGEMENT" in p[k]:
            p.pop(k)
    json.dump(p, open(out, "w"), indent=1)
    # --no-file/--no-gmail are load-bearing: a bare --apply would auto-file the thread and de-tray
    # the enquiry the same action just trayed.
    r = subprocess.run([sys.executable, os.path.join(VAULT, "te-log.py"), "--in", out,
                        "--apply", "--no-file", "--no-gmail"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        lines.append(f"  ✗ ee-intake: te-log failed (exit {r.returncode}) — "
                     f"{(r.stderr or r.stdout or '').strip().splitlines()[-1][:120] if (r.stderr or r.stdout) else 'no output'}")
        return False
    lines.append(f"  ✓ ee-intake: arrival captured for {p['email']} (CRM + ledger + knowledge)")
    if manifest:
        manifest.write(json.dumps({"step": "ee-intake", "thread_id": tid, "email": p["email"]}) + "\n")
    return True


def _knowledge_note(dec, fin, lines):
    """Write the JUDGED substance of a substantive touch into the entity's knowledge note.

    Why this exists (Pete, 29 Jul 2026 -- "not only fixed but understanding why we are in this
    position"): the enrichment rule has been enforced since 17 Jul by entity-enrich-signoff.py, but
    NOTHING in the pipeline performed the step it enforces. `_enrich` above pulls the raw email and
    attachments into Drive; the entity's vault_notes knowledge note was left to whoever remembered
    at the end of the session. So the gate failed on every triage by construction -- a detector
    wired to no actuator, and the only thing that ever closed it was a human writing three notes by
    hand.

    The substance was already there and was being thrown away. When each thread is judged, the
    per-row `note` becomes `body_gist` on the capture payload (triage-ops-table.py). It is the
    read-it-and-say-what-changed line, which is exactly what the rule asks for. It reached this
    file and went in the bin. This routes it to the note the gate actually checks:
    `vault_path = '<label>/README.md'`.

    Deliberately NOT done: stamping a generic "activity happened" line to make the gate go green.
    The gate compares timestamps, not content, so that would buy a passing gate with none of the
    facts written -- a green light that means nothing, which is worse than the red one.

    Idempotent on thread_id, so a re-run appends nothing. Fail-open and loud: the Gmail mutation
    and the ledger row have already happened and are not in doubt.
    """
    gist = (dec.get("body_gist") or "").strip()
    if not gist:
        lines.append("  ! knowledge: substantive touch but the judgment carried no note -- "
                     "nothing to write. Put the durable facts in the judgment's `note`.")
        return False
    label = fin["label"]
    tid = dec["thread_id"]
    rows = tl.cc_sql(f"SELECT id, body FROM vault_notes WHERE vault_path = '{tl.esc(label + '/README.md')}' LIMIT 1") \
        or tl.cc_sql("SELECT id, body FROM vault_notes WHERE type IN ('customer','supplier','project') "
                     f"AND vault_path LIKE '{tl.esc(label + '/%')}' ORDER BY length(vault_path) LIMIT 1")
    if not rows:
        lines.append(f"  – knowledge: '{label}' has no CC knowledge home, nothing to enrich "
                     f"(create one, or ignore if it does not warrant one)")
        return None
    note_id, body = rows[0]["id"], rows[0].get("body") or ""
    if tid in body:
        lines.append("  = knowledge: this thread is already recorded in the note -- no duplicate")
        return True
    stamp = dt.date.today().isoformat()
    entry = (f"- **{stamp}** {gist} "
             f"([thread](https://mail.google.com/mail/u/0/#all/{tid}) `{tid}`)")
    if _ACTIVITY_MARK in body:
        head, tail = body.split(_ACTIVITY_MARK, 1)
        existing = [l for l in tail.splitlines() if l.strip().startswith("- ")]
        trailing = [l for l in tail.splitlines() if not l.strip().startswith("- ") and l.strip()]
        kept = ([entry] + existing)[:_ACTIVITY_KEEP]
        new_body = head + _ACTIVITY_MARK + "\n" + "\n".join(kept) + "\n"
        if trailing:
            new_body += "\n" + "\n".join(trailing) + "\n"
    else:
        new_body = (body.rstrip() + "\n\n## Recent activity\n\n"
                    "_Appended automatically by triage when a substantive email is filed here. "
                    "Newest first. Standing terms live above, not in this list._\n\n"
                    + _ACTIVITY_MARK + "\n" + entry + "\n")
    try:
        # source_updated AND updated_at are both set deliberately. vault_notes has NO trigger
        # maintaining them, and entity-enrich-signoff decides "was this enriched?" purely on
        # `source_updated >= cutoff OR updated_at >= cutoff`. Writing the body without stamping
        # them changes the note and leaves the gate blind -- verified 29 Jul 2026: three notes
        # carried the new block, the gate still reported all three outstanding, and this function
        # had already printed ✓ for each. A tick the gate cannot see is the bug, not the fix.
        tl.cc_sql(f"UPDATE vault_notes SET body = $TRG${new_body}$TRG$, embedding = NULL, "
                  f"embedded_hash = NULL, source_updated = now(), updated_at = now() "
                  f"WHERE id = '{tl.esc(note_id)}'")
    except Exception as e:
        lines.append(f"  ! knowledge: could NOT update '{label}' note ({e}) -- the gate will "
                     f"still flag this entity; write it by hand")
        return False
    lines.append(f"  ✓ knowledge: recorded in {label} note")
    return True


def _gmail_drift(dec, fin, lines):
    """Has the LIVE Gmail state drifted from what this decision says was applied?

    Returns True (drifted -> re-execute), False (matches -> genuine no-op), or None (could not
    check -> re-execute anyway; every verb here is idempotent, so a needless re-apply is safe
    while a wrongly-skipped one leaves the thread stranded in the inbox reported as done).
    """
    if dec.get("no_gmail") or dec.get("action") == "walker":
        return False
    verb = (fin.get("verb") or "").lower()
    if verb in ("skip", "-", ""):
        return False
    try:
        g = tl.gmail()
        names = {l["id"]: l["name"] for l in g.list_labels()}
        th = g.get_thread(dec["thread_id"])
        live = set()
        for m in th["messages"]:
            live.update(names.get(i, i) for i in m.get("labelIds", []))
    except Exception as e:
        lines.append(f"  ! could not read live Gmail state ({e})")
        return None

    label = fin.get("label")
    archived = verb.startswith(("file", "task", "reply", "clear")) or verb == "route"
    if archived and "INBOX" in live:
        return True
    if label and verb.startswith(("file", "task", "reply", "keep")) or (label and verb == "route"):
        # the filing label must actually be on the thread (exact, or the unique suffix match
        # triage-log itself resolves to)
        hits = [n for n in live if n == label or n.endswith("/" + label) or label.endswith("/" + n)]
        if not hits:
            return True
    if (verb.startswith("reply") or verb == "route") and "Replies" not in live:
        return True
    if verb.startswith("hand") and "Delegated" not in live:
        return True
    return False


def capture(dec, apply=False, manifest=None):
    """Process one decision. Returns (ok, lines)."""
    lines = []
    # Walker event rows (Replies-tray send/de-tray/already-done/defer) are append-only,
    # keyed on their own synthetic message_id -- they never touch the no-op / re-decision path.
    if dec.get("action") == "walker":
        return capture_walker(dec, apply, manifest)
    mid = dec["message_id"]
    fin = dec.get("final") or {}
    pro = dec.get("proposed") or {}

    row = existing_row(mid)
    if row and not row_is_pending(row):
        # v6 cross-round re-decision: the SAME message re-triaged in a later session. If the
        # disposition is unchanged it is a true no-op; if it changed, UPDATE the row in place and
        # re-execute the verb (no carry-forward, no new round entity -- message_id IS the key).
        same = (row.get("final_verb") == fin.get("verb")
                and row.get("final_label") == fin.get("label")
                and row.get("final_ask") == fin.get("ask"))
        if same:
            # The ledger row alone is NOT proof the mutation still stands. Gmail can be reversed
            # outside this tool (27 Jul 2026: a whole triage round was manually reversed -- labels
            # stripped, threads restored to the inbox -- and the ledger rows survived, so the
            # re-run no-opped 6 of 8 threads and still printed "ALL OK"). Verify the LIVE state
            # matches the decision before claiming it is done; if it drifted, fall through and
            # re-execute the verb.
            drift = _gmail_drift(dec, fin, lines)
            if drift is False:
                # Gmail matches, but that is only one of the side-effects. Enrichment can have
                # FAILED on the original run (28 Jul 2026: two labels had no gmail_label_homes row,
                # so the enrich refused; adding the row and re-running repaired nothing because
                # this no-op returned first and the attachments were never pulled). The enricher is
                # idempotent -- it skips files already on disk -- so running it here costs little
                # and makes a re-run the actual repair path it is supposed to be.
                if fin.get("label") and not dec.get("no_enrich"):
                    _enrich(dec, fin, lines, manifest)
                    if _is_substantive(fin):
                        _knowledge_note(dec, fin, lines)   # idempotent on thread_id
                lines.append(f"  = {mid[:24]}… unchanged re-decision, Gmail verified — NO-OP")
                return True, lines
            if drift is True:
                lines.append(f"  ⟳ {mid[:24]}… ledger says applied but Gmail has drifted "
                             f"(reversed outside this tool) — RE-EXECUTING")
            else:
                lines.append(f"  ⟳ {mid[:24]}… could not verify live Gmail state — RE-EXECUTING "
                             f"(the verb is idempotent)")
        lines.append(f"  ↻ {mid[:24]}… re-decision ({row.get('final_verb')}→{fin.get('verb')}) — updating in place")

    if not apply:
        lines.append(f"  DRY {mid[:24]}… would write decision row + verb '{fin.get('verb')}' "
                     f"label '{fin.get('label')}'" +
                     (" + CREATE task (Pete-confirmed)" if dec.get("create_task") else "") +
                     (f" + close task {dec.get('close_task_id')}" if dec.get("close_task_id") else ""))
        return True, lines

    # guards on auto rows: never create a task; never send here
    if dec.get("decided_by") in ("cron-proposed", "cron-auto") and dec.get("create_task"):
        lines.append("  ✗ REFUSED: create_task on an auto row — the never-auto-create rule")
        return False, lines

    fact = tl.match_fact(dec.get("sender") or "")
    fact_id = q(fact["id"]) if fact else "NULL"

    # 1) ledger row FIRST (write-order rule): applying
    ov = bool(dec.get("overridden"))
    # lint bank: the payload's lint verdict MUST land on the row (ledger spec; found
    # dropped 10 Jul 2026 by triage-health goal 3 — 9 applied auto rows, NULL lint)
    lint_rep = dec.get("lint_report")
    lint_passed = dec.get("lint_passed", (lint_rep or {}).get("passed") if isinstance(lint_rep, dict) else None)
    lp = "NULL" if lint_passed is None else ("true" if lint_passed else "false")
    lr = "NULL" if lint_rep is None else "'" + tl.esc(json.dumps(lint_rep)) + "'::jsonb"
    # v6 columns (read-in-full / learning): session scope + read-proof + partial-content mark
    sid = q(dec.get("session_id"))
    bq = q(dec.get("body_quote"))
    sg = q(dec.get("subject_gist"))
    bg = q(dec.get("body_gist"))
    cr = q(dec.get("correction_reason"))
    pc = "true" if dec.get("partial_content") else "false"
    eng = q(dec.get("engine"))
    if row:  # pending proposal OR a cross-round re-decision being finalised
        tl.cc_sql("UPDATE triage_decisions SET "
                  f"final_ask={q(fin.get('ask'))}, final_verb={q(fin.get('verb'))}, "
                  f"final_label={q(fin.get('label'))}, final_project={q(fin.get('project'))}, "
                  f"final_priority={q(fin.get('priority'))}, decided_by='pete', "
                  f"overridden={'true' if ov else 'false'}, "
                  f"overridden_at={'now()' if ov else 'NULL'}, "
                  f"override_reason={a(dec.get('override_reason'))}, "
                  f"lint_passed={lp}, lint_report={lr}, basis_refs={a(dec.get('basis_refs'))}, "
                  f"session_id={sid}, body_quote={bq}, subject_gist={sg}, body_gist={bg}, "
                  f"correction_reason={cr}, partial_content={pc}, engine={eng}, "
                  f"apply_status='applying', decided_at=now() WHERE message_id='{tl.esc(mid)}'")
    else:
        tl.cc_sql("INSERT INTO triage_decisions (thread_id, sender, message_id, fact_id, "
                  "proposed_ask, proposed_verb, proposed_label, proposed_project, proposed_priority, "
                  "final_ask, final_verb, final_label, final_project, final_priority, "
                  "overridden, overridden_at, override_reason, decided_by, "
                  "lint_passed, lint_report, basis_refs, session_id, body_quote, subject_gist, "
                  "body_gist, correction_reason, partial_content, engine, apply_status) VALUES ("
                  f"{q(dec['thread_id'])}, {q(dec.get('sender'))}, {q(mid)}, {fact_id}, "
                  f"{q(pro.get('ask'))}, {q(pro.get('verb'))}, {q(pro.get('label'))}, "
                  f"{q(pro.get('project'))}, {q(pro.get('priority'))}, "
                  f"{q(fin.get('ask'))}, {q(fin.get('verb'))}, {q(fin.get('label'))}, "
                  f"{q(fin.get('project'))}, {q(fin.get('priority'))}, "
                  f"{'true' if ov else 'false'}, {'now()' if ov else 'NULL'}, "
                  f"{a(dec.get('override_reason'))}, {q(dec.get('decided_by') or 'pete')}, "
                  f"{lp}, {lr}, {a(dec.get('basis_refs'))}, {sid}, {bq}, {sg}, {bg}, {cr}, {pc}, {eng}, "
                  "'applying')")
    if manifest:
        manifest.write(json.dumps({"step": "ledger", "message_id": mid}) + "\n")
    # On an override, bank the correction as a triage_cases exemplar (decoupled from this row).
    if ov:
        bank_exemplar(dec)

    ok = True
    # 2) Gmail mutation per verb (skipped for demo/no-gmail payloads)
    gmail_done = None
    if not dec.get("no_gmail"):
        try:
            g = tl.gmail()
            labels = {l["name"]: l["id"] for l in g.list_labels()}
            def _resolve(name):
                # exact name, else UNIQUE suffix match ("SY-Clancy" -> "Customers/SY-Clancy").
                # Returns (label_id, error). A short/wrong name that can't resolve is an ERROR,
                # never a silent no-op (16 Jul 2026: short names archived threads with NO label).
                if not name:
                    return None, None
                if name in labels:
                    return labels[name], None
                hits = [n for n in labels if n == name or n.endswith("/" + name)]
                if len(hits) == 1:
                    return labels[hits[0]], None
                return None, ("no label named/ending '%s'" % name if not hits
                              else "%d labels match '%s' (ambiguous)" % (len(hits), name))
            verb = (fin.get("verb") or "").lower()
            add, remove = [], []
            filing_id, label_err = _resolve(fin.get("label"))
            if filing_id:
                add.append(filing_id)
            # verbs that MUST carry a resolvable filing label; if it doesn't resolve, REFUSE to
            # mutate (leave the thread in the inbox) rather than archive it unlabelled.
            needs_label = bool(fin.get("label")) and (
                verb.startswith(("file", "task", "reply", "keep")) or verb == "route")
            if needs_label and not filing_id:
                ok = False
                lines.append("  ✗ Gmail: filing label '%s' did not resolve (%s) -- NOT mutating "
                             "(thread left in inbox, not archived)" % (fin.get("label"), label_err))
            else:
                if verb.startswith("reply"):                # Reply / Reply+Task -> filing label + Replies + archive
                    add.append(labels.get("Replies")); remove.append("INBOX")
                elif verb.startswith("hand"):               # Hand to {person} -> Delegated, keep INBOX
                    add.append(labels.get("Delegated"))
                elif verb == "route":                       # engine intake (EE): filing (TE) label + Replies + archive
                    add.append(labels.get("Replies")); remove.append("INBOX")
                elif verb == "keep":                        # add filing label, KEEP in inbox
                    pass
                elif verb in ("skip", "-", ""):             # defer: no Gmail change
                    add, remove = [], []
                elif verb.startswith(("file", "task")):     # filing label + archive
                    remove.append("INBOX")
                elif verb == "clear":                       # noise: archive, no label
                    remove, add = ["INBOX"], []
                add = [x for x in add if x]                 # drop any unresolved Replies/Delegated
                if add or remove:
                    g.modify_thread(dec["thread_id"], add=add or None, remove=remove or None)
                    gmail_done = {"add": add, "remove": remove}
                    if manifest:
                        manifest.write(json.dumps({"step": "gmail", "thread_id": dec["thread_id"],
                                                   "add": add, "remove": remove}) + "\n")
        except Exception as e:
            ok = False
            lines.append(f"  ✗ Gmail: {e}")

    # 3) tasks: close on evidence / create on explicit Pete confirmation
    task_id = None
    if dec.get("close_task_id"):
        tl.cc_sql("UPDATE tasks SET status='done', completed_at=now() WHERE id='%s'"
                  % tl.esc(dec["close_task_id"]))
        if manifest:
            manifest.write(json.dumps({"step": "task-close", "task_id": dec["close_task_id"]}) + "\n")
    if dec.get("create_task"):
        # Wrapped + asserted since 7 Aug 2026. Two ways this step used to lose a task SILENTLY:
        # (a) it had no try/except (unlike the Gmail step), so a bad payload -- a create_task passed
        #     as a STRING instead of a dict -- threw AttributeError straight out of capture() and
        #     killed the whole batch mid-flight; (b) when the INSERT returned no row, task_id just
        #     became None and nothing complained, because the post-check below only verified a task
        #     it believed it had created (`if task_id:`). A task REQUESTED but never created was
        #     invisible to every check. Pete asked for two tasks on 7 Aug, the tool printed
        #     "ALL OK ✓ tasks: state verified", and neither task existed.
        t = dec["create_task"]
        try:
            if not isinstance(t, dict):
                raise TypeError("create_task must be a dict with at least {'name': ...}, got "
                                f"{type(t).__name__}: {t!r}")
            # due_on was hardcoded NULL until 4 Aug 2026, so a DATED bill could not be created
            # through this path at all: three bills (ProQual, Rausch, Revolut) landed as undated P2s
            # and had to be re-dated by hand. The date is the switch -- a due_on makes it a PD via
            # the DB trigger -- so dropping it silently downgrades every bill triage files.
            # base_priority carries the tier the task reverts to when the date is cleared.
            tier = t.get("priority") or "P3"
            rows = tl.cc_sql("INSERT INTO tasks (id, name, priority, base_priority, due_on, entity_slug, "
                             "project_slug, status, source, tags, notes) VALUES (gen_random_uuid(), "
                             f"{q(t['name'])}, {q(tier)}, {q(tier)}, "
                             f"{q(t.get('due_on'))}, {q(t.get('entity_slug'))}, {q(t.get('project_slug') or 'General')}, "
                             f"'todo', 'claude', {a(t.get('tags'))}, {q(t.get('notes'))}) RETURNING id")
            task_id = rows[0]["id"] if rows else None
            if not task_id:
                raise RuntimeError("INSERT INTO tasks returned no id -- the statement was rejected "
                                   "(check quoting/columns); NO task exists")
            tl.cc_sql(f"UPDATE triage_decisions SET task_id={q(task_id)} WHERE message_id='{tl.esc(mid)}'")
            if manifest:
                manifest.write(json.dumps({"step": "task-create", "task_id": task_id}) + "\n")
        except Exception as e:
            ok = False
            task_id = None
            lines.append(f"  ✗ tasks: create FAILED, no task exists -- {type(e).__name__}: {e}")

    # 4) vault enrichment -- pull the thread's attachments + body extract into the entity's Drive home.
    # The skill has called this non-negotiable on every filing-label thread since 1 Jul 2026, but
    # NOTHING executed it: triage-log had zero references to vault-enricher, so a whole triage could
    # label 70 threads and log NOT ONE attachment into an entity's home (found 28 Jul 2026, Pete).
    # A rule that no code runs is not a rule. It runs here, as a verb side-effect, or it never runs.
    if fin.get("label") and not dec.get("no_enrich"):
        enr = _enrich(dec, fin, lines, manifest)
        if enr is False:
            ok = False
        # The knowledge half. _enrich puts the raw email in Drive; this puts the JUDGED substance in
        # the entity's note -- the step entity-enrich-signoff.py has been enforcing since 17 Jul with
        # nothing in the pipeline performing it. Same side-effect status as the enrich above: it runs
        # here or it never runs.
        if _is_substantive(fin) and _knowledge_note(dec, fin, lines) is False:
            ok = False

    # 4b) EE INTAKE -- a Route is not finished when the labels land.
    # inbox-triage Step 4.6 has said since P4.3 that routing an enquiry has THREE side-effects: the
    # TE label, the Replies label, AND the te-log intake that puts it in the CRM, the ledger and the
    # corpus. Only the labels were code. On 4 Aug 2026 two routed enquiries plus one older trayed one
    # existed in NONE of the three EE systems; ee-signoff caught it at closeout with "touches this
    # session: 0". Same shape as the vault-enricher gap above: a rule no code runs is not a rule.
    if (fin.get("verb") or "").strip().lower().startswith("route") and (dec.get("engine") or "ee") == "ee":
        if _ee_intake(dec, lines, manifest) is False:
            ok = False

    # flip applied
    tl.cc_sql(f"UPDATE triage_decisions SET apply_status='applied', applied_at=now() "
              f"WHERE message_id='{tl.esc(mid)}'")

    # ---- POST-CHECK: re-read all three ----
    # Direct committed SELECT (not existing_row, which returned stale/None and produced a FALSE ✗
    # on applied rows -- 16 Jul 2026; the row was correct, the re-read was wrong).
    fresh = tl.cc_sql("SELECT apply_status FROM triage_decisions WHERE message_id='%s'" % tl.esc(mid))
    c1 = bool(fresh) and fresh[0].get("apply_status") == "applied"
    lines.append(f"  {'✓' if c1 else '✗'} ledger: row applied")
    c2 = True
    if gmail_done:
        try:
            g = tl.gmail()
            full = g.get_thread(dec["thread_id"])
            lbls = set()
            for msg in full.get("messages", []):
                lbls.update(msg.get("labelIds", []))
            c2 = all(x in lbls for x in gmail_done["add"])
        except Exception:
            c2 = False
    lines.append(f"  {'✓' if c2 else '✗'} gmail: labels verified" if gmail_done
                 else "  ✓ gmail: no mutation requested")
    c3 = True
    # A task that was REQUESTED but never created is the failure this check missed for months:
    # the old code was `if task_id:` alone, so when creation failed task_id was None, the whole
    # check was SKIPPED, and it printed "✓ tasks: state verified". Verify the ASK, not the artefact.
    if dec.get("create_task") and not task_id:
        c3 = False
        lines.append("  ✗ tasks: a task was REQUESTED for this decision but none was created "
                     "(task_id is NULL) -- re-run this decision")
    elif task_id:
        c3 = bool(tl.cc_sql(f"SELECT 1 FROM tasks WHERE id='{task_id}'"))
    if dec.get("close_task_id"):
        c3 = c3 and bool(tl.cc_sql("SELECT 1 FROM tasks WHERE id='%s' AND status='done'"
                                   % tl.esc(dec["close_task_id"])))
    lines.append(f"  {'✓' if c3 else '✗'} tasks: state verified")
    # forced-failure hook for the demo
    if dec.get("_force_postcheck_fail"):
        c3 = False
        lines.append("  ✗ tasks: FORCED post-check failure (demo)")
    return ok and c1 and c2 and c3, lines


def demo():
    print("P2 GATE DEMO — triage-log triple-write semantics")
    mid = "p2-demo-msg-001"
    tl.cc_sql(f"DELETE FROM triage_decisions WHERE message_id LIKE 'p2-demo-msg-%'")
    dec = {"thread_id": "p2-demo-thread", "message_id": mid, "sender": "bot@md.getsentry.com",
           "proposed": {"ask": "info-only", "verb": "File", "label": "Newsletters"},
           "final": {"ask": "info-only", "verb": "File", "label": "Newsletters"},
           "decided_by": "pete", "no_gmail": True}
    print("\n1. first --apply (scratch decision, no Gmail):")
    ok1, lines = capture(dec, apply=True); print("\n".join(lines))
    print("\n2. re-run of the SAME payload (must be a full no-op):")
    ok2, lines = capture(dec, apply=True); print("\n".join(lines))
    # Matched "FULL NO-OP" until 7 Aug 2026 — a string capture() never emits (it prints "— NO-OP",
    # line 480). So this gate returned FAIL on a perfectly healthy tool, every single run, and was
    # therefore worthless as a gate: nobody could tell a real regression from the permanent red.
    # That is why the task-create hole below went unnoticed. A gate that always fails is not a gate.
    noop = any("NO-OP" in l for l in lines)
    print("\n3. forced post-check failure (must exit non-zero):")
    dec2 = dict(dec, message_id="p2-demo-msg-002", _force_postcheck_fail=True)
    ok3, lines = capture(dec2, apply=True); print("\n".join(lines))
    print(f"   capture returned ok={ok3} → the CLI would exit {'1 (non-zero) ✓' if not ok3 else '0 ✗'}")
    # ---- the 7 Aug 2026 regressions ----
    print("\n5. create_task that CANNOT be created (passed as a string, the real 7 Aug payload bug)")
    print("   — must fail loudly, not report '✓ tasks: state verified':")
    dec3 = dict(dec, message_id="p2-demo-msg-003", create_task="Chase the CI failure")
    ok4, lines = capture(dec3, apply=True); print("\n".join(lines))
    fails_loud = (not ok4) and any("create FAILED" in l for l in lines) \
        and any("REQUESTED" in l for l in lines) and not any("✓ tasks" in l for l in lines)
    print(f"   returned ok={ok4}, ✗ on both the create and the post-check: "
          f"{'✓' if fails_loud else '✗ STILL SILENT'}")

    print("\n6. a row stranded at 'applying' by an EARLIER crashed run, re-running a DIFFERENT")
    print("   decision in the same session — the batch must NOT print ALL OK:")
    sess = "00000000-0000-4000-8000-0000000000d0"   # fixed scratch UUID (session_id is a uuid column)
    tl.cc_sql("INSERT INTO triage_decisions (thread_id, message_id, session_id, final_verb, "
              "decided_by, apply_status) VALUES ('p2-demo-thread', 'p2-demo-msg-stranded', "
              f"'{sess}', 'Task P2', 'pete', 'applying')")
    payload = [dict(dec, message_id="p2-demo-msg-004", session_id=sess)]
    pf = "/tmp/p2-demo-payload.json"
    json.dump(payload, open(pf, "w"))
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--in", pf, "--apply"],
                       capture_output=True, text=True, env=os.environ)
    print("   " + "\n   ".join((r.stdout or "").strip().splitlines()[-6:]))
    sweep_fired = "SESSION INCOMPLETE" in r.stdout and "ALL OK" not in r.stdout and r.returncode != 0
    print(f"   exit={r.returncode}, sweep caught the stranded row: "
          f"{'✓' if sweep_fired else '✗ SILENT — the 7 Aug bug is back'}")
    os.remove(pf)

    print("\n7. cleanup:")
    tl.cc_sql(f"DELETE FROM triage_decisions WHERE message_id LIKE 'p2-demo-msg-%'")
    tl.cc_sql("DELETE FROM tasks WHERE name = 'Chase the CI failure' AND source = 'claude'")
    left = tl.cc_sql("SELECT count(*) AS n FROM triage_decisions WHERE message_id LIKE 'p2-demo-msg-%'")[0]["n"]
    print(f"   scratch rows remaining: {left}")
    verdict = ok1 and ok2 and noop and (not ok3) and fails_loud and sweep_fired and left == 0
    print(f"\nP2 GATE: {'PASS — applied, no-op on re-run, non-zero on forced failure, '
                        'loud on a failed task-create, loud on a stranded row, clean' if verdict else 'FAIL'}")
    return 0 if verdict else 1


def main():
    if "--demo" in sys.argv:
        return demo()
    if "--in" not in sys.argv:
        print(__doc__); return 2
    path = sys.argv[sys.argv.index("--in") + 1]
    apply = "--apply" in sys.argv
    manifest = None
    if "--manifest" in sys.argv:
        manifest = open(sys.argv[sys.argv.index("--manifest") + 1], "a")
    payload = json.load(open(path))
    decs = payload if isinstance(payload, list) else [payload]
    all_ok = True
    for dec in decs:
        print(f"{dec.get('message_id','?')[:30]}:")
        try:
            ok, lines = capture(dec, apply=apply, manifest=manifest)
        except Exception as e:
            # The loop had no try/except until 7 Aug 2026: ONE bad decision threw out of capture()
            # and killed the batch mid-flight. Every remaining decision was silently skipped, and
            # the row being written was stranded at apply_status='applying' (the flip to 'applied'
            # sits after the task step). Contain it per-decision -- one bad row is one bad row.
            ok = False
            lines = [f"  ✗ CRASHED -- {type(e).__name__}: {e}",
                     "  ledger row left at apply_status='applying' (half-applied); "
                     "fix the payload and re-run THIS decision to finish it"]
            traceback.print_exc(file=sys.stderr)
        print("\n".join(lines))
        all_ok = all_ok and ok
    if manifest:
        manifest.close()

    # ---- SESSION SWEEP: no batch is OK while the session has half-written rows ----
    # Scoped to the SESSION, not this batch's message_ids, and that scope is the whole point.
    # 7 Aug 2026: a run crashed partway and stranded two rows at 'applying'. The operator fixed the
    # payload and re-ran the CORRECTED SUBSET -- which printed "ALL OK", because the two stranded
    # rows were in the session but not in that batch, so nothing on earth looked at them. Two tasks
    # Pete had explicitly asked for never existed and nothing said so. A later run must see the
    # earlier run's wreckage. Second arm catches the silent-loss shape directly: a task-bearing verb
    # that reached 'applied' with no task on it (zero such rows exist in the ledger's history, so
    # this cannot false-positive on legitimate work).
    sids = sorted({d.get("session_id") for d in decs if d.get("session_id")})
    if apply and sids:
        inlist = ",".join("'" + tl.esc(s) + "'" for s in sids)
        bad = tl.cc_sql(
            "SELECT message_id, final_verb, apply_status, task_id FROM triage_decisions "
            f"WHERE session_id IN ({inlist}) AND (apply_status = 'applying' OR "
            "(apply_status = 'applied' AND task_id IS NULL AND "
            "(final_verb ILIKE 'task%' OR final_verb ILIKE 'hand%' OR final_verb ILIKE '%+ task%')))")
        if bad:
            all_ok = False
            print(f"\n✗ SESSION INCOMPLETE — {len(bad)} decision(s) half-written in this session "
                  "(not necessarily from this batch):")
            for r in bad:
                why = ("stranded mid-apply" if r.get("apply_status") == "applying"
                       else "applied but its task was never created")
                print(f"    {r['message_id']}  [{r.get('final_verb') or '?'}]  {why}")
            print("  These will NOT finish on their own — re-run triage-log.py --apply for them.")

    print(f"\n{'ALL OK' if all_ok else 'FAILURES — see ✗ lines above'} ({len(decs)} decision(s), "
          f"{'applied' if apply else 'dry-run'})")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
