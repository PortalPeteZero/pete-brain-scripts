#!/usr/bin/env python3
"""clancy-dd-workflow.py — THE Damage Depot workflow, gated.

Pete, 5 Aug 2026: "We don't need lessons at banking. We need processes fixed... We need a tidy
workflow that you work and follow. Do not skip. It's gated."

This replaces remembering. Every failure this section has had was a step done out of order, done
partly, or reported done on the wrong evidence. Each is now a check that runs, and the whole
thing exits non-zero until every one passes.

The stages are the section's own vocabulary, unchanged: CAPTURE -> FILE -> ENRICH -> PUBLISH.
Filing MOVES (Depotnet -> Drive). Enrichment READS (it never writes a Depotnet field). Thirteen
checks sit under those four stages.

  VAULT=/tmp/pbs python3 clancy-dd-workflow.py                 # check FY26/27, print the board
  VAULT=/tmp/pbs python3 clancy-dd-workflow.py --fy FY25/26
  VAULT=/tmp/pbs python3 clancy-dd-workflow.py --all-years
  VAULT=/tmp/pbs python3 clancy-dd-workflow.py --json          # for the stop hook

  CAPTURE  1 initial   Depotnet's raw payload plus its answers, BOTH question sections.
           2 current   nothing changed on Depotnet since we captured. A damage is a LIVING
                       record — the investigation is filled in over days, the report is submitted
                       and then amended, photographs keep arriving. 152586 was amended twice in
                       one afternoon; 153523's report was submitted four days after the strike.
                       Depotnet has no last-modified at list level, but its per-incident timeline
                       is an audit trail, so "we are behind" is a ledger event dated after our
                       raw_api_at. Work the list with clancy-dd-recapture.py.
                       AND the ledger is a MIRROR: it can only prove currency up to its own last
                       sweep, so this step FAILS when the sweep is stale rather than reporting a
                       clean bill of health it has not earned.
           3 actions   the corrective actions we hold match what the payload DECLARED. Every
                       GetImIncident payload carries an `actions` list beside the questions, the
                       report and the timeline, so one call brings them and there is no separate
                       Depotnet section to visit. Nothing checked it until 5 Aug.
  FILE     4 referenced what the record SAYS exists that we do not hold. Every other check asks
                       whether we read what we have; this asks the opposite, and it is the one
                       that finds a missing video. Added 5 Aug after a video named in a signed
                       witness statement reached no finding.
           5 to Drive  every attachment filed to Drive or the private bucket.
  ENRICH   5 read      every file has an enrich-ledger status of read / read+vision /
                       routed-vision. NOT "has a doc_extracts row" — on 5 Aug counting rows said
                       566/566 read while 3 files had never been fetched at all. A scanned .docx
                       extract holds the literal string "[document]" and a photo holds "[image]";
                       both survive a row count AND a length check. The ledger is the only
                       honest source.
           7 see       every image routed to vision has a reading meeting the anti-skim contract
                       (a real description, a has_text verdict, a transcription where has_text).
           8 roll up   doc_* promoted AND current. Specifically a damage with transcribed scanned
                       paperwork must carry doc_transcripts. Until 5 Aug vision output was used
                       only to COUNT images, so a photographed permit was read and thrown away
                       and the damage looked thinly evidenced when it was not.
           9 embed     every damage embedded, or Genny cannot retrieve it.
  PUBLISH 10 classify  every non-empty lessons_learnt has a reviewed bucket.
                       clancy-dn-board-report.py asserts this and refuses to invent one, so ONE
                       new Depotnet answer breaks the whole publish until it is classified
                       (happened 5 Aug on 153523, answer "TBC").
         10b current   our judgements are still about text that EXISTS. Step 10 proves a bucket is
                       there; it cannot tell whether it is still true. Both the lesson bucket and
                       the plain-English write-up judge a specific piece of Clancy text, and Clancy
                       rewrite it. On 5 Aug 153523 was bucketed `non-answer` off a lessons field
                       reading "TBC"; Clancy replaced it that evening and the stale verdict kept
                       passing step 10 with a green tick while calling their work a non-answer.
                       A Depotnet "Amended" event dated after the judgement fails this step.
          11 sourced   every Sygma finding links to the document it came from, so a claim can be
                       checked without asking anyone.
          12 publish   every page newer than the newest data change, and all within
                       PUBLISH_SPREAD_MIN of each other. The section is built by TEN scripts; on
                       5 Aug one was run directly after a data change and the other eleven pages
                       stayed 19 hours old, still rendering a finding that had been corrected.

Exit 0 = every step passes. Exit 1 = at least one step is incomplete (the board says which, and
prints the exact command that fixes it). Exit 2 = the check could not run — never read as clean.
"""
import argparse, json, os, sys, datetime, urllib.request, urllib.error, urllib.parse
from collections import Counter, defaultdict

VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
_K = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = _K["url"], _K["service_role_key"]
H = {"apikey": SR, "Authorization": f"Bearer {SR}"}

# Every module key the section publishes. Kept in step with clancy-dn-publish.py STEPS — a page
# that carries this section's chrome but is absent here is a page nothing checks.
PAGE_KEYS = [
    "clancy-depotnet-damages", "clancy-depotnet", "clancy-damage-analysis",
    "clancy-damage-analysis-2025-26", "clancy-unmapped-damages", "clancy-damage-reports",
    "clancy-genny-cat-reviews", "clancy-damage-glossary", "clancy-damage-change-watch",
    "clancy-damage-year", "clancy-damage-year-2025-26", "clancy-damage-analysis-v2",
    "clancy-damage-year-v2",
]
PUBLISH_SPREAD_MIN = 90          # a full publish run takes ~8 min; 90 is generous, not blind
LEDGER_STALE_HOURS = 6           # beyond this the audit-trail mirror cannot vouch for "current"
READ_OK = ("read", "read+vision", "routed-vision")
FIX_PUBLISH = "VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-publish.py"
FIX_PROMOTE = "VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-enrich-index.py --promote"


def get(path):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(URL + "/rest/v1/" + path, headers=H), timeout=240).read().decode())


def page_all(table, select, ids, key="id"):
    """Keyset-paged read on a UNIQUE key. An unordered limit/offset read silently loses rows —
    see paged-read-guard.py — which is why every page carries order= and a gt cursor.

    `key` MUST be unique. A cursor of `key=gt.<last>` on a repeated column skips every remaining
    row that shares the last value in the batch: paging clancy_dn_answers on incident_id lost the
    tail of whichever damage straddled each 1000-row boundary, and this check then reported three
    damages as missing an answer section when all three were complete (caught 5 Aug 2026, before
    the gate went live — a gate that raises false failures gets ignored, which is worse than no
    gate at all). For a table with no unique key use page_ordered()."""
    if not ids:
        return []
    inlist = "in.(" + ",".join(str(i) for i in ids) + ")"
    out, last = [], None
    while True:
        q = f"{table}?select={select}&incident_id={inlist}&order={key}.asc&limit=1000"
        if last is not None:
            q += f"&{key}=gt.{urllib.parse.quote(str(last), safe='')}"
        batch = get(q)
        if not batch:
            break
        out += batch
        last = batch[-1][key]
        if len(batch) < 1000:
            break
    return out


def page_ordered(table, select, ids, order):
    """Offset-paged read for a table with NO unique key (clancy_dn_answers has none).

    Safe only because `order` is a TOTAL order — every row distinguishable — so consecutive
    pages cannot re-deliver or skip. Passing a partial order here reintroduces exactly the bug
    page_all's docstring describes.
    """
    if not ids:
        return []
    inlist = "in.(" + ",".join(str(i) for i in ids) + ")"
    out, off, step = [], 0, 1000
    while True:
        # paged-read-guard: ok total order on (incident_id,section,q_no) makes offset paging stable
        batch = get(f"{table}?select={select}&incident_id={inlist}&order={order}"
                    f"&limit={step}&offset={off}")
        out += batch
        if len(batch) < step:
            return out
        off += step


def check(fy):
    """Run all twelve steps for one financial year. Returns a list of step dicts."""
    fyq = urllib.parse.quote(fy, safe="")
    inc = get(f"clancy_dn_incidents?select=id,location,raw_api_at,doc_enriched_at,doc_transcripts,"
              f"doc_conclusions,embedding,lessons_learnt,sygma_findings,sygma_finding_sources,"
              f"sygma_reviewed_at,sygma_plain,sygma_plain_at,"
              f"import_changed_at,capture_actions,raw_api&fy=eq.{fyq}&order=id.asc&limit=4000")
    steps, n = [], len(inc)
    if not n:
        return [{"n": 0, "stage": "CAPTURE", "name": "NO DAMAGES", "ok": False, "detail": f"no damages found for {fy}",
                 "fix": None}], inc

    ids = [r["id"] for r in inc]
    files = page_all("clancy_dn_files", "id,incident_id,name,drive_id,storage_path", ids)
    led = page_all("clancy_dn_enrich_ledger", "id,incident_id,name,status,note", ids)
    imgs = page_all("clancy_dn_image_readings",
                    "id,incident_id,file_id,origin,description,has_text,transcription", ids)
    ans = page_ordered("clancy_dn_answers", "incident_id,section,q_no", ids,
                       "incident_id.asc,section.asc,q_no.asc")
    act_n = Counter(x["incident_id"] for x in
                    page_all("clancy_dn_actions", "id,incident_id", ids))

    def step(i, stage, name, bad, detail, fix=None):
        steps.append({"n": i, "stage": stage, "name": name, "ok": not bad, "count": len(bad),
                      "detail": detail, "bad": bad[:25], "fix": fix})

    # 1 CAPTURE
    secs = defaultdict(set)
    for a in ans:
        secs[a["incident_id"]].add(a["section"])
    bad = [f"{r['id']} {(r['location'] or '')[:26]}"
           for r in inc if not r["raw_api_at"] or len(secs[r["id"]]) < 2]
    step(1, "CAPTURE", "initial", bad,
         f"{n - len(bad)}/{n} damages have Depotnet's payload and both answer sections",
         "capture from Depotnet (needs a signed-in session)")

    # 1b RECAPTURE — a Clancy damage is a LIVING record. The investigation is filled in over days,
    # the report is submitted and then amended, photos keep arriving. Depotnet exposes no
    # last-modified at list level, but its per-incident timeline is an audit trail and
    # clancy-dn-change-sweep.py mirrors it here, so "our copy is behind" is a comparison between
    # two columns we already hold: a ledger event dated after our raw_api_at.
    # Pete, 5 Aug 2026: "every time we connect up on it, we have to have a look: has anything been
    # added, has anything been updated?"
    led_all, last_id = [], None
    inlist = "in.(" + ",".join(str(i) for i in ids) + ")"
    while True:
        q = (f"clancy_dn_change_ledger?select=id,incident_id,history_type,changed_at,changed_by"
             f"&incident_id={inlist}&order=id.asc&limit=1000")
        if last_id is not None:
            q += f"&id=gt.{last_id}"
        b = get(q)
        if not b:
            break
        led_all += b
        last_id = b[-1]["id"]
        if len(b) < 1000:
            break
    def _t(v):
        return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None
    since_cap = defaultdict(list)
    capat = {r["id"]: _t(r.get("raw_api_at")) for r in inc}
    for e in led_all:
        c, ch = capat.get(e["incident_id"]), _t(e.get("changed_at"))
        if c and ch and ch > c:
            since_cap[e["incident_id"]].append(e)
    bad = [f"{i} — captured {capat[i].isoformat()[:16]}, {len(evs)} Depotnet event(s) since "
           f"(latest: {max(_t(e['changed_at']) for e in evs).isoformat()[:16]} "
           f"{sorted(evs, key=lambda e: e['changed_at'])[-1]['history_type']})"
           for i, evs in sorted(since_cap.items())]

    # THE LEDGER IS A MIRROR, AND A MIRROR CAN ONLY PROVE CURRENCY UP TO ITS LAST REFRESH.
    #
    # This check compares raw_api_at against clancy_dn_change_ledger, which is filled by
    # clancy-dn-change-sweep.py. If the sweep has not run since we captured, the ledger holds
    # NOTHING about the period in question, so an empty result means "the mirror has not looked",
    # not "nothing happened". Reporting that as level is a gate claiming confidence it has not
    # earned, which is worse than having no gate.
    #
    # Caught live 5 Aug 2026. This step reported "48/48 level with Depotnet's audit trail" while
    # the last sweep was 25.5 hours old. Pete asked whether the timeline was checked at all; a
    # live pull found Daniel Wilson had amended damage 153523 three times that same evening —
    # rewriting the incident summary to name an LV cable supply, recording that no trial hole was
    # carried out, and replacing the "TBC" lessons field. None of it was visible here.
    # WHEN WE LAST LOOKED, not when we last FOUND (fixed 7 Aug 2026). `spotted_at` is stamped when
    # a change is first seen, so a sweep that runs on a quiet day and correctly finds nothing never
    # advances it — and this step then reported the mirror as stale indefinitely. Measured that day:
    # the sweep had just run, "new ledger rows: 0", and this said "last swept 2.0h ago" and failed.
    # A gate that cries wolf gets ignored, which this section has learned once already.
    # clancy-dn-change-sweep.py now records its own run; spotted_at is the fallback for a register
    # that has never been swept by the newer tool.
    run = get("cron_state?cron_key=eq.clancy-dn-change-sweep&item_key=eq.last_run&select=updated_at")
    swept_at = _t(run[0]["updated_at"]) if run else None
    if not swept_at:
        swept = get("clancy_dn_change_ledger?select=spotted_at&order=spotted_at.desc&limit=1")
        swept_at = _t(swept[0]["spotted_at"]) if swept else None
    newest_capture = max([c for c in capat.values() if c], default=None)
    age_h = (datetime.datetime.now(datetime.timezone.utc) - swept_at).total_seconds() / 3600 \
        if swept_at else None
    if not swept_at:
        bad.insert(0, "the change ledger is EMPTY — this step can prove nothing")
    elif newest_capture and swept_at < newest_capture:
        bad.insert(0, f"the change sweep last ran {swept_at.isoformat()[:16]}, BEFORE our newest "
                      f"capture at {newest_capture.isoformat()[:16]} — it has not looked since, so "
                      f"'no changes' here means NOTHING WAS CHECKED")
    elif age_h > LEDGER_STALE_HOURS:
        bad.insert(0, f"the change sweep last ran {round(age_h,1)}h ago (limit "
                      f"{LEDGER_STALE_HOURS}h) — anything Clancy did since is invisible to this step")

    step(2, "CAPTURE", "current", bad,
         f"{n - len([b for b in bad if b[:1].isdigit()])}/{n} level with the audit trail, "
         f"which was last swept {round(age_h,1) if age_h is not None else '?'}h ago",
         "VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-change-sweep.py   (refresh the mirror FIRST), "
         "then clancy-dd-recapture.py and recapture the listed IDs")

    # 2 FILE
    bad = [f"{f['incident_id']} {f['name'][:40]}"
           for f in files if not (f.get("drive_id") or f.get("storage_path"))]
    # 3 ACTIONS — reconcile what we hold against what the payload DECLARED.
    # Pete, 5 Aug 2026: "does it check if any actions raised when it checks and syncs?... i cant
    # remember if we need to go look there in depotnet or if the incident report tells us".
    # It tells us: every GetImIncident payload carries an `actions` list beside the questions, the
    # report and the timeline, so one call brings them and there is no separate place to visit.
    # Nothing was CHECKING that, though. It reconciled on 5 Aug by luck, not by test, and a
    # silently-missing corrective action is exactly the kind of gap that surfaces months later.
    # Note imIncident.raiseAction is false on every damage including the two that HAVE actions:
    # it is a template flag, never a per-damage "an action is needed here".
    bad = []
    for r in inc:
        raw = r.get("raw_api") or {}
        d = (raw.get("data") or {}) if isinstance(raw, dict) else {}
        declared = len(d.get("actions") or [])
        held = act_n.get(r["id"], 0)
        if declared != held:
            bad.append(f"{r['id']} {(r['location'] or '')[:26]} — payload declares {declared} "
                       f"action(s), we hold {held} (capture_actions={r.get('capture_actions')})")
    n_dec = sum(len((((r.get("raw_api") or {}).get("data")) or {}).get("actions") or [])
                for r in inc if isinstance(r.get("raw_api"), dict))
    step(3, "CAPTURE", "actions", bad,
         f"{n_dec} corrective action(s) declared across the year, {sum(act_n.values())} held",
         "re-ingest the payload for the named damages: "
         "VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-ingest.py /tmp/dnapi")

    # 4 REFERENCED — what does the record SAY exists that we do not hold?
    # Every other check here asks "did we read what we have". This asks the opposite, and it is
    # the one that finds a missing video. Pete, 5 Aug 2026, on a video named in a witness
    # statement that no finding mentioned: "so why didnt you raise this key finding in your
    # conclusions you wrote?" Because nothing compared what the documents REFERENCE against what
    # the file list HOLDS. Detail and the fix list: clancy-dd-missing-evidence.py.
    import subprocess as _sp
    bad = []
    # THE CHECKER MUST PROVE ITSELF BEFORE ITS COUNT IS USED. Its first run reported 22 of 48
    # damages with a gap; one fix — reading what vision saw INSIDE an image rather than only its
    # filename — took that to 12. A number that moves after someone has been given it is worse
    # than no number. Pete, 5 Aug 2026: "we need some locks and gates in here or something
    # because you're turning this process into something really unreliable."
    _st = _sp.run(["python3", f"{VAULT}/clancy-dd-missing-evidence.py", "--selftest"],
                  capture_output=True, text=True, env={**os.environ, "VAULT": VAULT}, timeout=600)
    selftest_ok = _st.returncode == 0
    try:
        _r = _sp.run(["python3", f"{VAULT}/clancy-dd-missing-evidence.py", "--fy", fy, "--json"],
                     capture_output=True, text=True, env={**os.environ, "VAULT": VAULT}, timeout=900)
        if _r.returncode in (0, 1) and _r.stdout.strip():
            for d in json.loads(_r.stdout).get("detail", []):
                for mm in d["missing"]:
                    bad.append(f"{d['id']} [{mm['kind']}] {mm['what'][:56]} "
                               f"— named in {mm['where'][:44]}")
        elif _r.returncode == 2:
            bad.append("could not run clancy-dd-missing-evidence.py — this is NOT a pass")
    except Exception as _e:
        bad.append(f"could not run clancy-dd-missing-evidence.py ({_e}) — this is NOT a pass")
    if not selftest_ok:
        # SUPPRESS the count entirely. A checker that cannot reproduce verdicts already verified
        # by hand is not evidence, and printing its number anyway is how an unreliable figure
        # reaches a customer.
        bad = ["the checker FAILED ITS OWN SELF-TEST, so NO COUNT is reported here"] + \
              [ln.strip() for ln in (_st.stderr or "").splitlines() if ln.strip().startswith("✗")]
        step(4, "FILE", "referenced", bad,
             "SUPPRESSED — the checker could not reproduce its known verdicts",
             "VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dd-missing-evidence.py --selftest  "
             "(fix the rule, then add the damage that exposed it as a case)")
    else:
        step(4, "FILE", "referenced", bad,
             f"{'nothing' if not bad else len(bad)} referenced by the record but not held "
             f"(checker self-test passed)",
             "VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dd-missing-evidence.py  (then ask Clancy for "
             "the named artefact, or record why it cannot be had)")

    unfiled = [f"{f['incident_id']} {f['name'][:40]}"
               for f in files if not (f.get("drive_id") or f.get("storage_path"))]
    step(5, "FILE", "to Drive", unfiled,
         f"{len(files) - len(unfiled)}/{len(files)} attachments filed to Drive")

    # 3 READ — ledger status, never a row count
    byname = {}
    for L in led:
        byname[(L["incident_id"], L["name"])] = L
    # A not-held row whose note declares the source is held natively is NOT a gap: the only case
    # is our own published review, attached back onto the damage in Depotnet, whose real source is
    # a CC module. Without this the gate reports a permanent failure nothing can clear, and a
    # gate that can never go green is a gate people stop reading.
    unread = [f"{L['incident_id']} [{L['status']}] {L['name'][:44]}"
              + (f"  — {L['note'][:60]}" if L.get("note") else "")
              for L in led if L["status"] not in READ_OK
              and "held natively" not in (L.get("note") or "")]
    missing = [f"{f['incident_id']} (no ledger row) {f['name'][:40]}"
               for f in files if (f["incident_id"], f["name"]) not in byname]
    bad = unread + missing
    step(6, "ENRICH", "read", bad, f"{len(led) - len(unread)}/{len(files)} files read "
                         f"({dict(Counter(L['status'] for L in led))})",
         "re-fetch the named files from Depotnet, then "
         "VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-enrich-index.py --read")

    # 4 SEE — the anti-skim contract
    bad = [f"{i['incident_id']} file {i['file_id']}"
           for i in imgs
           if not (i.get("description") or "").strip()
           or "has_text" not in i
           or (i.get("has_text") and not (i.get("transcription") or "").strip())]
    step(7, "ENRICH", "see", bad, f"{len(imgs) - len(bad)}/{len(imgs)} image readings carry a real "
                        f"description and a transcription where there is text")

    # 5 ROLL UP — including the scanned-paperwork hole
    scanned = defaultdict(int)
    for i in imgs:
        if i.get("origin") == "docx-media" and i.get("has_text") \
                and len((i.get("transcription") or "").strip()) >= 25:
            scanned[i["incident_id"]] += 1
    # Count BEFORE the comprehensions below: `scanned` is a defaultdict, so reading a missing
    # key creates it, and len(scanned) would report every damage in the year as having scans.
    n_scanned = len(scanned)
    bad = [f"{r['id']} {(r['location'] or '')[:26]} — not promoted"
           for r in inc if not r["doc_enriched_at"]]
    bad += [f"{r['id']} {(r['location'] or '')[:26]} — {scanned.get(r['id'])} transcribed scans, "
            f"doc_transcripts EMPTY (promote predates the 5 Aug fix)"
            for r in inc if scanned.get(r["id"]) and not r.get("doc_transcripts")]
    step(8, "ENRICH", "roll up", bad,
         f"{n - len([b for b in bad if 'not promoted' in b])}/{n} promoted; "
         f"{sum(1 for r in inc if r.get('doc_transcripts'))} of the {n_scanned} damages with "
         f"scanned paperwork carry its transcripts", FIX_PROMOTE)

    # 6 EMBED
    bad = [f"{r['id']} {(r['location'] or '')[:26]}" for r in inc if not r.get("embedding")]
    step(9, "ENRICH", "embed", bad, f"{n - len(bad)}/{n} damages embedded for Genny",
         "VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-import.py --embed-only")

    # 7 CLASSIFY — the one that breaks the board report
    bucketed = {b["incident_id"] for b in
                get("clancy_report_lesson_buckets?select=incident_id&order=incident_id.asc&limit=2000")}
    withl = [r for r in inc if (r.get("lessons_learnt") or "").strip()]
    bad = [f"{r['id']} lessons_learnt={r['lessons_learnt'][:40]!r}"
           for r in withl if r["id"] not in bucketed]
    step(10, "PUBLISH", "classify", bad, f"{len(withl) - len(bad)}/{len(withl)} damages with a lessons answer "
                             f"have a reviewed bucket",
         "insert a clancy_report_lesson_buckets row (concrete / restatement / non-answer) — a "
         "REVIEWED judgement, never auto-assigned; the board report will not publish without it")

    # 7b CURRENT — step 10 checks a judgement EXISTS. It cannot tell whether it is still TRUE.
    #
    # Both of our judgements — the lesson bucket and the plain-English write-up — are about a
    # specific piece of Clancy text, and Clancy rewrite that text. On 5 Aug 153523 was bucketed
    # `non-answer` because its lessons field said "TBC"; Clancy replaced it the same evening with a
    # real answer, and the stale verdict kept passing step 10 with a green tick while calling their
    # work a non-answer. The write-up alongside it had a whole section that became false, including
    # two questions asking them to finish things they had finished — on a page they can see.
    #
    # So: a Depotnet "Amended" event dated after the judgement means the judgement was made against
    # text that no longer exists. Precise rather than nagging — measured 7 Aug across FY26/27, it
    # flags 2 damages, not 48.
    # KEYSET-PAGED on id. limit=20000 was silently capped to 1000 by PostgREST, so this read came
    # back short and damages whose amendment sat past the cap looked current. Caught by the
    # extended paged-read-guard, 8 Aug 2026.
    amended, _last = {}, None
    while True:
        _q = ("clancy_dn_change_ledger?select=id,incident_id,changed_at"
              "&history_type=ilike.*Amended*&order=id.asc&limit=1000")
        if _last is not None:
            _q += f"&id=gt.{_last}"
        _batch = get(_q)
        if not _batch:
            break
        for c in _batch:
            i_ = c["incident_id"]
            # keep the NEWEST amendment per incident (we are no longer ordered by changed_at)
            if i_ not in amended or c["changed_at"] > amended[i_]:
                amended[i_] = c["changed_at"]
        _last = _batch[-1]["id"]
        if len(_batch) < 1000:
            break
    buckets, _bl = {}, None
    while True:
        _bq = "clancy_report_lesson_buckets?select=incident_id,reviewed_on&order=incident_id.asc&limit=1000"
        if _bl is not None:
            _bq += f"&incident_id=gt.{_bl}"
        _bb = get(_bq)
        if not _bb:
            break
        for b in _bb:
            buckets[b["incident_id"]] = b.get("reviewed_on")
        _bl = _bb[-1]["incident_id"]
        if len(_bb) < 1000:
            break
    bad = []
    for r in inc:
        amend = amended.get(r["id"])
        if not amend:
            continue
        rev = buckets.get(r["id"])
        if rev and amend[:10] > str(rev)[:10]:
            bad.append(f"{r['id']} bucket judged {rev}, Clancy amended {amend[:10]} — re-judge it")
        pl = r.get("sygma_plain_at")
        if r.get("sygma_plain") and pl and amend > pl:
            bad.append(f"{r['id']} plain-English written {pl[:10]}, Clancy amended {amend[:10]} — re-read it")
    step("10b", "PUBLISH", "current", bad,
         f"{len(bad)} judgement(s) made against text Clancy has since rewritten",
         "re-read the damage, then update clancy_report_lesson_buckets (bucket + reason + "
         "reviewed_on) and/or clancy_dn_incidents.sygma_plain (+ sygma_plain_at). Both are "
         "point-in-time judgements — a recapture that changes the investigation invalidates them.")

    # 8 PUBLISH — pages newer than the newest data change, and in step with each other
    pages = get("module_content?select=module_key,updated_at&module_key=in.("
                + ",".join(f'"{k}"' for k in PAGE_KEYS) + ")")
    now = datetime.datetime.now(datetime.timezone.utc)
    def age(ts):
        return (now - datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 60
    have = {p["module_key"]: age(p["updated_at"]) for p in pages}
    newest_data = 10 ** 9
    for r in inc:
        for f in ("doc_enriched_at", "sygma_reviewed_at", "import_changed_at"):
            if r.get(f):
                newest_data = min(newest_data, age(r[f]))
    bad = [f"{k} — NEVER PUBLISHED" for k in PAGE_KEYS if k not in have]
    bad += [f"{k} — {round(a)} min old, but the data changed {round(newest_data)} min ago"
            for k, a in sorted(have.items()) if newest_data < 10 ** 9 and a > newest_data + 5]
    spread = round(max(have.values()) - min(have.values())) if have else 0
    if spread > PUBLISH_SPREAD_MIN:
        bad.append(f"pages are {spread} min apart — part of the section was published without "
                   f"the rest")
    # 10 SOURCED — a finding Pete cannot check is worth less than no finding.
    # Pete, 5 Aug 2026: "wherever that came from, there is a natural link in there to the drive
    # document so I can check it myself." Resolved by clancy-dd-source-links.py from the finding's
    # own words, so it survives an edit; a finding that resolves to nothing is named here.
    unsourced = []
    for r in inc:
        fnds = r.get("sygma_findings") or []
        srcs = r.get("sygma_finding_sources") or []
        for i, f in enumerate(fnds):
            if i >= len(srcs) or not srcs[i]:
                unsourced.append(f"{r['id']} finding {i}: {str(f)[:64]}")
    n_f = sum(len(r.get("sygma_findings") or []) for r in inc)
    step(11, "PUBLISH", "sourced", unsourced,
         f"{n_f - len(unsourced)}/{n_f} Sygma findings link to the document they came from",
         "VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dd-source-links.py --apply  (a finding that still "
         "will not resolve needs the document naming in its own words)")

    step(12, "PUBLISH", "publish", bad, f"{len(have)}/{len(PAGE_KEYS)} pages present, spread {spread} min, "
                            f"newest data change {round(newest_data) if newest_data < 10**9 else '?'} "
                            f"min ago", FIX_PUBLISH)
    return steps, inc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fy", default="FY26/27")
    ap.add_argument("--all-years", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    years = ["FY26/27", "FY25/26"] if a.all_years else [a.fy]

    allsteps, failed = {}, False
    try:
        for fy in years:
            steps, inc = check(fy)
            allsteps[fy] = steps
            if any(not s["ok"] for s in steps):
                failed = True
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"clancy-dd-workflow: COULD NOT RUN — {e}\n  This is NOT a pass. Fix and re-run.",
              file=sys.stderr)
        sys.exit(2)

    if a.json:
        print(json.dumps({"ok": not failed,
                          "years": {fy: [{k: s[k] for k in ("n", "name", "ok", "count", "detail",
                                                            "bad", "fix")} for s in st]
                                    for fy, st in allsteps.items()}}, indent=1))
        sys.exit(1 if failed else 0)

    for fy, steps in allsteps.items():
        print(f"\n=== DAMAGE DEPOT WORKFLOW — {fy} ===")
        print(f"  {'STAGE':<9}{'#':<4}{'STEP':<12}{'':<13}{'WHERE IT STANDS'}")
        print("  " + "-" * 96)
        last = None
        for s_ in steps:
            stage = s_["stage"] if s_["stage"] != last else ""
            last = s_["stage"]
            mark = "PASS" if s_["ok"] else "INCOMPLETE"
            print(f"  {stage:<9}{str(s_['n']):<4}{s_['name']:<12}{mark:<13}{s_['detail']}")
            if not s_["ok"]:
                for b in s_["bad"]:
                    print(f"  {'':<22}   - {b}")
                if s_["count"] > len(s_["bad"]):
                    print(f"  {'':<22}   ... and {s_['count'] - len(s_['bad'])} more")
                if s_["fix"]:
                    print(f"  {'':<22}   FIX: {s_['fix']}")
    print()
    if failed:
        print("WORKFLOW INCOMPLETE — the steps above are not done. Do them in order; a later step "
              "built on an earlier gap is wasted work.")
    else:
        print("WORKFLOW COMPLETE — every step done and every page published from the current data.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
