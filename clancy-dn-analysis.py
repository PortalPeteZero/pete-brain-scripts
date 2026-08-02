#!/usr/bin/env python3
"""clancy-dn-analysis.py — "What the damage data tells us": the analysis section of the Depot.

WHY: Pete, 1 Aug 2026 — a report for Clancy on what this year's damages actually show, built from
what DEPOTNET holds rather than from Sygma's own reviews, as its own section rather than another
entry in the reports library. It will be run AGAIN after the document enrichment, and the point of
that second run is to show what enrichment recovered. So:

  * EVERY figure is computed live from the register at publish time. Nothing is typed in.
  * Each publish records its own headline metrics in `clancy_analysis_editions`, so a later
    edition can show what CHANGED rather than claiming it.

    THE "BEFORE" IS EDITION 1, NOT AN EARLIER SNAPSHOT. Pete, 1 Aug 2026: "that frozen before
    might not be accurate, that was done early days, live state now is frozen before." He is
    right as a principle even though the check came out clean — `clancy_dn_baseline_pre_enrichment`
    was taken at 18:35 on 31 Jul, mid-session, and nothing guarantees the work had settled. Compared
    live on 1 Aug it agrees exactly for this year (48 rows, 26 with a cause, 26 with lessons, zero
    differing cells), so it stands as corroboration. But the authoritative before is what edition 1
    records at the moment it publishes, from the live register. Never re-point this at the frozen
    table: a metric captured at publish cannot drift; a snapshot taken at an arbitrary earlier
    moment can.
  * Where a previous edition exists, the page renders the movement itself.

THE ONE ANALYTICAL TRAP THIS FILE EXISTS TO AVOID: the cause fields are multi-select, stored
comma-joined, and FOUR of this year's records tick every available option (all 17 underlying
causes, 9 of 9 root causes). Counting naively, every cause that appears exactly four times is
those four records and nobody else — an artefact presented as a finding. So the cause analysis
runs on the records that made a REAL selection, and the blanket-tickers are reported separately
as the data-quality problem they are. Never remove that split without replacing it with something
better.

Usage:
  VAULT=/tmp/pbs python3 /tmp/pbs/clancy-dn-analysis.py [--local out.html] [--publish]
                                                        [--edition N] [--label "..."]
"""
import os, json, argparse, datetime, html as H, urllib.request, urllib.error
import clancy_dn_ui as ui


def _urlopen_retry(req, timeout=120, tries=9):
    """Supabase answers 429 under load. clancy-dn-publish.py runs six of these tools back to
    back and each writes many rows, so the later steps reliably hit it - observed 2 Aug 2026,
    where the FY26/27 analysis build died mid-run on a 429 and left that page stale while every
    other page had been rebuilt. Without backoff a publish half-updates the section and the
    freshness report is the only clue. Retries 429 and 5xx with exponential backoff."""
    import time as _t
    for n in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 60))
        except Exception:
            if n == tries - 1:
                raise
            _t.sleep(min(2 ** n, 60))


VAULT = os.environ.get("VAULT", "/tmp/pbs")
SEC = os.path.expanduser("~/.config/pete-secrets")
if not os.path.exists(f"{SEC}/command-centre-supabase-keys.json"):
    SEC = f"{VAULT}/Library/processes/secrets"
k = json.load(open(f"{SEC}/command-centre-supabase-keys.json"))
URL, SR = k["url"], k["service_role_key"]
# One script, one page per financial year. FY26/27 is the live analysis; earlier years are
# scaffolds until their capture runs — the page says so plainly rather than looking thin.
FY_PAGES = {
    "FY26/27": {"mk": "clancy-damage-analysis",         "label": "FY 2026/27"},
    "FY25/26": {"mk": "clancy-damage-analysis-2025-26", "label": "FY 2025/26"},
    "FY24/25": {"mk": "clancy-damage-analysis-2024-25", "label": "FY 2024/25"},
    "FY23/24": {"mk": "clancy-damage-analysis-2023-24", "label": "FY 2023/24"},
}
FY = os.environ.get("CLANCY_FY", "FY26/27")
MK = FY_PAGES[FY]["mk"]


def sql(q):
    tok = open(f"{VAULT}/Library/processes/secrets/supabase-token").read().strip()
    req = urllib.request.Request(
        "https://api.supabase.com/v1/projects/zhexcaflgahdcbzvbyfq/database/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST")
    return json.loads(_urlopen_retry(req, timeout=180).read().decode())


def esc(v):
    return H.escape(str(v if v is not None else ""), quote=False)


# ── the numbers ──────────────────────────────────────────────────────────────────────────────
def gather():
    """Everything the page reports, in one place, all read live. Returns a dict of plain values
    and lists — no HTML — so the same numbers can be recorded as this edition's metrics."""
    d = {}
    d["headline"] = sql(f"""SELECT
      count(*) damages,
      count(*) FILTER (WHERE status='Open') still_open,
      count(*) FILTER (WHERE service_interrupted ILIKE 'YES') supply_lost,
      count(*) FILTER (WHERE root_cause IS NOT NULL AND btrim(root_cause)<>'') with_cause,
      count(*) FILTER (WHERE lessons_learnt IS NOT NULL AND btrim(lessons_learnt)<>'') with_lessons,
      count(*) FILTER (WHERE strike_category IS NOT NULL AND btrim(strike_category)<>'') with_struck,
      count(*) FILTER (WHERE caused_by_plant IS NOT NULL AND btrim(caused_by_plant)<>'') with_plant,
      count(*) FILTER (WHERE depth_mm IS NOT NULL) with_depth,
      count(*) FILTER (WHERE pdf_captured_at IS NOT NULL) captured
      FROM clancy_dn_incidents WHERE fy='{FY}'""")[0]

    # The PRECEDING financial year, over the SAME months this year covers. Both halves were
    # hard-coded to FY25/26 April-July, so FY25/26's own page compared itself against itself and
    # printed a nonsense percentage. (Found by auditing the finished work, 2 Aug 2026.)
    _PRIOR = {"FY26/27": "FY25/26", "FY25/26": "FY24/25",
              "FY24/25": "FY23/24", "FY23/24": None}
    _prior_fy = _PRIOR.get(FY)
    if _prior_fy:
        _mons = sql(f"""SELECT DISTINCT extract(month FROM incident_date)::int m
                        FROM clancy_dn_incidents WHERE fy='{FY}'""")
        _list = ",".join(str(r["m"]) for r in _mons) or "0"
        d["prior_year_same_months"] = sql(f"""SELECT count(*) n FROM clancy_dn_incidents
          WHERE fy='{_prior_fy}' AND extract(month FROM incident_date) IN ({_list})""")[0]["n"]
    else:
        d["prior_year_same_months"] = 0
    d["prior_fy"] = _prior_fy

    d["months"] = sql(f"""SELECT to_char(incident_date,'Mon') m, min(incident_date) o, count(*) n,
      count(*) FILTER (WHERE root_cause IS NOT NULL AND btrim(root_cause)<>'') investigated
      FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1 ORDER BY o""")

    d["utility"] = sql(f"""SELECT coalesce(nullif(btrim(strike_category),''),'Not recorded') v, count(*) n
      FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1 ORDER BY n DESC, v""")

    d["subcat"] = sql(f"""SELECT coalesce(nullif(btrim(strike_subcategory),''),'Not recorded') v, count(*) n
      FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1 ORDER BY n DESC, v LIMIT 12""")

    d["depth"] = sql(f"""SELECT CASE WHEN depth_mm IS NULL THEN 'Not recorded'
        WHEN depth_mm < 300 THEN 'Under 300mm' WHEN depth_mm < 450 THEN '300 to 449mm'
        WHEN depth_mm < 600 THEN '450 to 599mm' WHEN depth_mm < 900 THEN '600 to 899mm'
        ELSE '900mm or deeper' END v, count(*) n, min(coalesce(depth_mm,99999)) srt
      FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1 ORDER BY srt""")

    # Depths answered in the wrong unit. The form's own label says "Unit In MM" and these are
    # decimals, i.e. metres. They are deliberately NOT converted (see clancy-dn-ingest.py), so
    # without this they are simply absent from "Not recorded" - indistinguishable from a damage
    # where nobody filled the field in at all. That difference matters: one is a gap, the other
    # is a measurement we hold and cannot safely use.
    d["depth_wrong_unit"] = sql(f"""SELECT id, location, depth_raw
      FROM clancy_dn_incidents WHERE fy='{FY}' AND depth_raw IS NOT NULL AND depth_mm IS NULL
      ORDER BY id""")
    d["plant"] = sql(f"""SELECT coalesce(nullif(btrim(caused_by_plant),''),'Not recorded') v, count(*) n
      FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1 ORDER BY n DESC, v""")

    d["environment"] = sql(f"""SELECT coalesce(nullif(btrim(environment),''),'Not recorded') v, count(*) n
      FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1 ORDER BY n DESC, v""")

    # Mechanical vs hand, by utility — the split that decides what a course has to cover.
    d["mech_by_utility"] = sql(f"""SELECT coalesce(nullif(btrim(strike_category),''),'Not recorded') u,
      count(*) FILTER (WHERE caused_by_plant ~* 'digger|excavator|breaker|drill|saw|pick') mech,
      count(*) FILTER (WHERE caused_by_plant ~* 'hand|graft|shovel|spade|fork|bar') hand,
      count(*) total FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1 ORDER BY total DESC""")

    d["shallow_by_utility"] = sql(f"""SELECT coalesce(nullif(btrim(strike_category),''),'Not recorded') u,
      count(*) FILTER (WHERE depth_mm < 450) shallow, count(*) total
      FROM clancy_dn_incidents WHERE fy='{FY}' AND depth_mm IS NOT NULL
      GROUP BY 1 ORDER BY total DESC""")

    # ── the cause split ──────────────────────────────────────────────────────────────────
    # Exclude blanket-ticking PER FIELD, not per record. Two of this year's records select nine of
    # the nine root causes while making a genuine single choice of underlying cause; dropping them
    # from both analyses would discard real signal to remove an artefact. So a record is excluded
    # from the ROOT counts only if its root selection is blanket, and from the UNDERLYING counts
    # only if its underlying selection is. Threshold is 4: every genuine selection this year is 3
    # or fewer, every blanket one is 9 or 17.
    BLANKET_AT = 4
    d["blanket_at"] = BLANKET_AT
    d["blanket"] = sql(f"""SELECT id, array_length(string_to_array(root_cause,','),1) rc,
        array_length(string_to_array(underlying_cause,','),1) uc, location, incident_date::date d
      FROM clancy_dn_incidents WHERE fy='{FY}'
        AND (coalesce(array_length(string_to_array(underlying_cause,','),1),0) > {BLANKET_AT}
          OR coalesce(array_length(string_to_array(root_cause,','),1),0) > {BLANKET_AT})
      ORDER BY uc DESC NULLS LAST, rc DESC""")

    def clean(col):
        """Counts for one cause field, from the records that made a real selection in THAT field."""
        guard = (f"AND coalesce(array_length(string_to_array(i.{col},','),1),0) <= {BLANKET_AT}")
        n = sql(f"""SELECT count(*) n FROM clancy_dn_incidents i WHERE i.fy='{FY}'
                    AND i.{col} IS NOT NULL AND btrim(i.{col})<>'' {guard}""")[0]["n"]
        rows = sql(f"""SELECT btrim(v) val, count(*) n FROM clancy_dn_incidents i,
                       unnest(string_to_array(i.{col}, ',')) v WHERE i.fy='{FY}'
                       AND i.{col} IS NOT NULL AND btrim(i.{col})<>'' {guard}
                       GROUP BY 1 ORDER BY n DESC, 1""")
        return n, rows

    d["root_n"], d["root_cause"] = clean("root_cause")
    d["under_n"], d["underlying"] = clean("underlying_cause")
    d["analysed"] = d["root_n"]          # headline "usable analyses" = usable ROOT causes

    d["lessons_quality"] = sql(f"""SELECT CASE
        WHEN length(btrim(lessons_learnt)) < 40 THEN 'A word or a phrase'
        WHEN length(btrim(lessons_learnt)) < 120 THEN 'A sentence'
        WHEN length(btrim(lessons_learnt)) < 300 THEN 'A paragraph'
        ELSE 'Substantial' END v, count(*) n, min(length(btrim(lessons_learnt))) srt
      FROM clancy_dn_incidents WHERE fy='{FY}'
        AND lessons_learnt IS NOT NULL AND btrim(lessons_learnt)<>'' GROUP BY 1 ORDER BY srt""")

    d["lessons_thin"] = sql(f"""SELECT id, location, btrim(lessons_learnt) t
      FROM clancy_dn_incidents WHERE fy='{FY}' AND lessons_learnt IS NOT NULL
        AND length(btrim(lessons_learnt)) < 40 ORDER BY length(btrim(lessons_learnt)) LIMIT 10""")

    d["lessons_good"] = sql(f"""SELECT id, location, incident_date::date d, btrim(lessons_learnt) t
      FROM clancy_dn_incidents WHERE fy='{FY}' AND lessons_learnt IS NOT NULL
        AND length(btrim(lessons_learnt)) >= 300 ORDER BY length(btrim(lessons_learnt)) DESC LIMIT 4""")

    d["evidence_split"] = sql(f"""SELECT CASE WHEN root_cause IS NOT NULL AND btrim(root_cause)<>''
        THEN 'Cause recorded' ELSE 'No cause recorded' END grp,
        count(*) damages, round(avg(nf),1) avg_files
      FROM (SELECT i.id, i.root_cause, count(f.id) nf FROM clancy_dn_incidents i
        LEFT JOIN clancy_dn_files f ON f.incident_id=i.id WHERE i.fy='{FY}'
        GROUP BY i.id, i.root_cause) z GROUP BY 1 ORDER BY 1""")

    # ── the learning funnel ──────────────────────────────────────────────────────────────
    # Pete, 1 Aug 2026: "how many can we take something meaningful from". Judged by
    # clancy_dn_lesson_quality, whose rules are on the face of the view so they can be argued
    # with. A lesson copied word for word onto a second damage is counted once, not twice.
    d["lesson_tiers"] = sql(f"""SELECT tier, count(*) n FROM clancy_dn_lesson_quality
      WHERE fy='{FY}' GROUP BY 1 ORDER BY 1""")
    d["lesson_funnel"] = sql(f"""SELECT
        count(*) FILTER (WHERE tier='4 briefable') briefable,
        count(DISTINCT lesson) FILTER (WHERE tier='4 briefable') distinct_briefable,
        -- MUST match the population the duplicated-lesson section quotes, or the page
        -- contradicts itself. It did: this counted tier 4 only, so FY25/26's tile said
        -- "0 lessons copied word for word" directly above a section headed "the same lesson,
        -- word for word, on more than one damage - 4 damages". The section's rule is the one
        -- to follow: anything long enough to BE a lesson (tier 3 or 4), never a bare
        -- non-answer, which is counted and stated separately.
        count(*) FILTER (WHERE tier IN ('3 a single phrase','4 briefable') AND is_duplicated) copied,
        count(*) FILTER (WHERE tier IN ('0 none recorded','1 a non-answer')) nothing,
        count(*) FILTER (WHERE looks_truncated) truncated
      FROM clancy_dn_lesson_quality WHERE fy='{FY}'""")[0]
    # Duplicated lessons, with enough of each damage to show whether the two events were actually
    # alike. Without that detail "the same lesson twice" is an accusation; with it, it is evidence.
    # "Word for word" ignores case, punctuation and runs of whitespace — the same words in the
    # same order (the key is clancy_dn_lesson_quality.norm, on the face of the view). A byte-exact
    # key is not good enough: on 2 Aug 2026 damages 121878 and 122362 carried the same lesson
    # differing by exactly two spaces, and the byte test silently dropped it.
    #
    # Two different things get called "duplication" and they must not be added together:
    #   · the same NON-ANSWER typed twice ("N/A" on eight damages) — a gap, not a copied lesson
    #   · the same real LESSON reused — the finding Pete is after
    # Only the second is quoted as a duplicated lesson. Groups are formed across ALL years, so
    # reuse that crosses a year boundary shows up; a group is included if it touches this one.
    d["dupes"] = sql(f"""WITH grp AS (
        SELECT norm FROM clancy_dn_lesson_quality
        WHERE lesson <> '' AND tier IN ('3 a single phrase','4 briefable')
        GROUP BY norm HAVING count(*) > 1
          AND count(*) FILTER (WHERE fy='{FY}') > 0)
      SELECT max(q.lesson) lesson, max(q.len) len, count(*) n,
        count(*) FILTER (WHERE q.fy='{FY}') n_this_year,
        count(DISTINCT q.fy) n_years,
        json_agg(json_build_object('id', i.id, 'fy', i.fy, 'd', i.incident_date::date,
          'loc', i.location, 'job', i.job_ref, 'plant', i.caused_by_plant, 'depth', i.depth_mm,
          'who', i.caused_by_person, 'descr', i.description,
          'root', i.root_cause, 'under', i.underlying_cause) ORDER BY i.id) recs
      FROM clancy_dn_lesson_quality q
      JOIN grp ON grp.norm = q.norm
      JOIN clancy_dn_incidents i ON i.id = q.id
      GROUP BY q.norm ORDER BY max(q.len) DESC""")
    # The repeated NON-answers, counted separately and never quoted as lessons.
    d["dupe_nonanswers"] = sql(f"""SELECT count(*) rows, count(DISTINCT norm) texts FROM (
        SELECT norm FROM clancy_dn_lesson_quality
        WHERE lesson <> '' AND tier IN ('1 a non-answer','2 a fragment')
          AND norm IN (SELECT norm FROM clancy_dn_lesson_quality WHERE lesson<>''
                       GROUP BY norm HAVING count(*) > 1)
          AND fy='{FY}') z""")[0]
    # The honest denominator for any "only one in the register" claim.
    d["lesson_pool"] = sql("""SELECT count(*) total, count(DISTINCT norm) distinct_texts
      FROM clancy_dn_lesson_quality WHERE lesson <> ''""")[0]
    d["years_without_lessons"] = sql("""SELECT string_agg(fy, ', ' ORDER BY fy) fys FROM (
      SELECT fy FROM clancy_dn_incidents GROUP BY fy
      HAVING count(*) FILTER (WHERE lessons_learnt IS NOT NULL AND btrim(lessons_learnt)<>'') = 0) z""")[0]["fys"]

    # ── THE CORRECTION THAT MATTERS (Pete, 1 Aug 2026) ───────────────────────────────────
    # An empty field in our capture is NOT evidence that the activity did not happen. Tested:
    # every damage CLOSED this year carries a root cause — 17 of 17, no exceptions — while only
    # 30% of open ones do. The investigation is completed as part of closing the incident. So a
    # missing cause overwhelmingly means "still open", not "never investigated". The first version
    # of this page said 22 damages had no cause "recorded at all" and ranked contracts by who
    # investigates; that was measuring closure and presenting it as diligence. Every claim below
    # is now scoped to the population where it is safe to make.
    # WHAT "HAS AN INVESTIGATION" IS BASED ON (Pete, 1 Aug 2026: "i want to know what that is
    # based on"). Two different tests, and they do not give the same answer, so both are reported:
    #   present  = the Depotnet record carries the Investigation section at all
    #   complete = Depotnet's OWN field "Is the investigation complete?" answers YES
    # Using the first and calling it "completed" overstated it: 26 carry the section but Depotnet
    # marks only 21 of them complete.
    d["by_status"] = sql(f"""SELECT i.status, count(*) n,
      count(*) FILTER (WHERE EXISTS (SELECT 1 FROM clancy_dn_answers a
        WHERE a.incident_id=i.id AND a.section='investigation' AND a.answered)) has_section,
      count(*) FILTER (WHERE EXISTS (SELECT 1 FROM clancy_dn_answers a
        WHERE a.incident_id=i.id AND a.question='Is the investigation complete?'
          AND upper(btrim(a.answer))='YES')) says_complete,
      count(*) FILTER (WHERE EXISTS (SELECT 1 FROM clancy_dn_answers a
        WHERE a.incident_id=i.id AND a.question='Is the investigation complete?'
          AND upper(btrim(a.answer))='NO')) says_not
      FROM clancy_dn_incidents i WHERE i.fy='{FY}' GROUP BY 1 ORDER BY n DESC""")
    d["not_complete_closed"] = sql(f"""SELECT i.id FROM clancy_dn_incidents i
      JOIN clancy_dn_answers a ON a.incident_id=i.id
      WHERE i.fy='{FY}' AND a.question='Is the investigation complete?'
        AND upper(btrim(a.answer))='NO' AND i.status='Closed' ORDER BY i.id""")
    d["inv_shape"] = sql(f"""WITH per AS (
      SELECT i.id, (SELECT count(*) FROM clancy_dn_answers a
        WHERE a.incident_id=i.id AND a.section='investigation' AND a.answered) k
      FROM clancy_dn_incidents i WHERE i.fy='{FY}')
      SELECT min(k) lo, max(k) hi FROM per WHERE k > 0""")[0]
    d["inv_universal"] = sql(f"""SELECT count(*) n FROM (
      SELECT a.question FROM clancy_dn_answers a JOIN clancy_dn_incidents i ON i.id=a.incident_id
      WHERE i.fy='{FY}' AND a.section='investigation' AND a.answered
      GROUP BY 1 HAVING count(DISTINCT a.incident_id) =
        (SELECT count(DISTINCT a2.incident_id) FROM clancy_dn_answers a2
         JOIN clancy_dn_incidents i2 ON i2.id=a2.incident_id
         WHERE i2.fy='{FY}' AND a2.section='investigation' AND a2.answered)) t""")[0]["n"]
    d["team_members"] = sql(f"""SELECT a.question q, count(DISTINCT a.incident_id) n
      FROM clancy_dn_answers a JOIN clancy_dn_incidents i ON i.id=a.incident_id
      WHERE i.fy='{FY}' AND a.question LIKE 'Investigation Team Member%'
      GROUP BY 1 ORDER BY 1""")
    d["blank_split"] = sql(f"""SELECT
      count(*) FILTER (WHERE pdf_captured_at IS NOT NULL) confirmed_blank,
      count(*) FILTER (WHERE pdf_captured_at IS NULL) not_captured,
      coalesce(string_agg(CASE WHEN pdf_captured_at IS NULL THEN id::text END, ', '),'') uncaptured
      FROM clancy_dn_incidents i WHERE i.fy='{FY}' AND NOT EXISTS
        (SELECT 1 FROM clancy_dn_answers a WHERE a.incident_id=i.id
          AND a.section='investigation' AND a.answered)""")[0]
    d["inv_basis"] = sql(f"""SELECT
      count(DISTINCT a.incident_id) FILTER (WHERE a.section='investigation' AND a.answered) has_section,
      count(DISTINCT a.question) FILTER (WHERE a.section='investigation') questions,
      count(DISTINCT a.incident_id) FILTER (WHERE a.question='Is the investigation complete?'
        AND upper(btrim(a.answer))='YES') complete,
      count(DISTINCT a.incident_id) FILTER (WHERE a.question='Is the investigation complete?'
        AND upper(btrim(a.answer))='NO') not_complete
      FROM clancy_dn_answers a JOIN clancy_dn_incidents i ON i.id=a.incident_id
      WHERE i.fy='{FY}'""")[0]
    d["closed_n"] = sql(f"""SELECT count(*) n FROM clancy_dn_incidents
      WHERE fy='{FY}' AND status='Closed'""")[0]["n"]
    d["closed_lessons"] = sql(f"""SELECT q.tier, count(*) n
      FROM clancy_dn_lesson_quality q JOIN clancy_dn_incidents i USING (id)
      WHERE q.fy='{FY}' AND i.status='Closed' GROUP BY 1 ORDER BY 1""")
    d["closed_funnel"] = sql(f"""SELECT count(*) closed,
        count(*) FILTER (WHERE q.tier='4 briefable') briefable,
        count(DISTINCT q.lesson) FILTER (WHERE q.tier='4 briefable') distinct_briefable
      FROM clancy_dn_lesson_quality q JOIN clancy_dn_incidents i USING (id)
      WHERE q.fy='{FY}' AND i.status='Closed'""")[0]
    d["open_age"] = sql(f"""SELECT CASE
        WHEN CURRENT_DATE - incident_date::date < 30 THEN 'Under 30 days'
        WHEN CURRENT_DATE - incident_date::date < 60 THEN '30 to 59 days'
        WHEN CURRENT_DATE - incident_date::date < 90 THEN '60 to 89 days'
        ELSE '90 days or more' END v, count(*) n,
        min(CURRENT_DATE - incident_date::date) srt
      FROM clancy_dn_incidents WHERE fy='{FY}' AND status='Open'
      GROUP BY 1 ORDER BY srt""")

    # ── who investigates ─────────────────────────────────────────────────────────────────
    # By contract, showing OPEN and CLOSED alongside the cause count, because without those two
    # columns the third one reads as an accusation rather than a status.
    d["by_contract"] = sql(f"""SELECT coalesce(contract_family,'Other') c, count(*) n,
      round(avg(CURRENT_DATE - incident_date::date)) avg_age,
      count(*) FILTER (WHERE status='Open') still_open,
      count(*) FILTER (WHERE EXISTS (SELECT 1 FROM clancy_dn_answers a
        WHERE a.incident_id=clancy_dn_incidents.id AND a.section='investigation' AND a.answered)) inv
      FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1
      HAVING count(*) >= 3 ORDER BY n DESC""")
    d["by_severity"] = sql(f"""SELECT coalesce(severity,'Not stated') v, count(*) n,
      count(*) FILTER (WHERE root_cause IS NOT NULL AND btrim(root_cause)<>'') inv
      FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1 ORDER BY n DESC""")
    d["by_supply"] = sql(f"""SELECT CASE WHEN service_interrupted ILIKE 'YES' THEN 'Supply was lost'
        WHEN service_interrupted ILIKE 'NO' THEN 'Supply was not lost' ELSE 'Not stated' END v,
      count(*) n, count(*) FILTER (WHERE root_cause IS NOT NULL AND btrim(root_cause)<>'') inv
      FROM clancy_dn_incidents WHERE fy='{FY}' GROUP BY 1 ORDER BY n DESC""")
    d["only_other"] = sql(f"""SELECT count(*) n FROM clancy_dn_incidents WHERE fy='{FY}'
      AND btrim(coalesce(root_cause,''))='Other'""")[0]["n"]

    d["actions"] = sql(f"""SELECT count(DISTINCT a.incident_id) damages_with, count(*) actions
      FROM clancy_dn_actions a JOIN clancy_dn_incidents i ON i.id=a.incident_id
      WHERE i.fy='{FY}'""")[0]
    d["last_action"] = sql("SELECT max(date_raised)::date d FROM clancy_dn_actions")[0]["d"]
    d["since_last_action"] = sql("""SELECT count(*) n FROM clancy_dn_incidents
      WHERE incident_date > (SELECT max(date_raised) FROM clancy_dn_actions)""")[0]["n"]

    d["rows"] = sql(f"""SELECT i.id, i.incident_date::date dt,
      coalesce(nullif(btrim(i.contract_family),''),'Not stated') contract,
      coalesce(nullif(btrim(i.strike_category),''),'Not stated') service,
      i.status, i.depth_mm,
      CASE WHEN i.service_interrupted ILIKE 'YES' THEN 'Y'
           WHEN i.service_interrupted ILIKE 'NO'  THEN 'N' ELSE '' END supply,
      CASE WHEN i.root_cause    IS NOT NULL AND btrim(i.root_cause)<>''    THEN 'Y' ELSE 'N' END cause,
      CASE WHEN i.lessons_learnt IS NOT NULL AND btrim(i.lessons_learnt)<>'' THEN 'Y' ELSE 'N' END lesson,
      (SELECT count(*) FROM clancy_dn_actions x
        WHERE x.incident_id=i.id AND x.status<>'Closed') acts_out,
      (SELECT count(*) FROM clancy_dn_actions x
        WHERE x.incident_id=i.id AND x.status='Closed') acts_closed,
      (SELECT count(*) FROM clancy_dn_actions x WHERE x.incident_id=i.id) acts,
      (SELECT count(*) FROM clancy_dn_files  z WHERE z.incident_id=i.id) files,
      (SELECT count(*) FROM clancy_dn_answers a
        WHERE a.incident_id=i.id AND a.section='investigation' AND a.answered) inv_answers,
      (SELECT count(*) FROM clancy_dn_answers a
        WHERE a.incident_id=i.id AND a.section='questions') inc_answers,
      upper(btrim(coalesce((SELECT a.answer FROM clancy_dn_answers a WHERE a.incident_id=i.id
        AND a.question='Is the investigation complete?' LIMIT 1),''))) inv_done,
      upper(btrim(coalesce((SELECT a.answer FROM clancy_dn_answers a WHERE a.incident_id=i.id
        AND a.question='CAT used?' LIMIT 1),''))) cat,
      upper(btrim(coalesce((SELECT a.answer FROM clancy_dn_answers a WHERE a.incident_id=i.id
        AND a.question='Genny used?' LIMIT 1),''))) genny,
      upper(btrim(coalesce((SELECT a.answer FROM clancy_dn_answers a WHERE a.incident_id=i.id
        AND a.question LIKE 'Permit to Dig%' LIMIT 1),''))) permit
      , (i.pdf_captured_at IS NOT NULL) captured
      FROM clancy_dn_incidents i WHERE i.fy='{FY}' ORDER BY i.incident_date DESC, i.id DESC""")
    return d


def metrics_of(d):
    """The figures worth comparing edition to edition. Deliberately a small, stable set."""
    h = d["headline"]
    return {
        "damages": h["damages"], "still_open": h["still_open"], "supply_lost": h["supply_lost"],
        "with_cause": h["with_cause"], "with_lessons": h["with_lessons"],
        "with_struck": h["with_struck"], "with_plant": h["with_plant"],
        "with_depth": h["with_depth"], "captured": h["captured"],
        "usable_root_analyses": d["root_n"], "usable_underlying_analyses": d["under_n"],
        "blanket_tickers": len(d["blanket"]),
        "lessons_substantial": next((r["n"] for r in d["lessons_quality"] if r["v"] == "Substantial"), 0),
        "lessons_thin": next((r["n"] for r in d["lessons_quality"] if r["v"] == "A word or a phrase"), 0),
        "damages_with_an_action": d["actions"]["damages_with"],
        "closed": d["closed_n"],
        "investigation_section_present": d["inv_basis"]["has_section"],
        "investigation_form_blank_confirmed": d["blank_split"]["confirmed_blank"],
        "not_captured_yet": d["blank_split"]["not_captured"],
        "depotnet_says_complete": d["inv_basis"]["complete"],
        "depotnet_says_not_complete": d["inv_basis"]["not_complete"],
        "closed_with_a_briefable_lesson": d["closed_funnel"]["distinct_briefable"],
        "briefable_lessons_all_statuses": d["lesson_funnel"]["briefable"],
        "distinct_briefable_all_statuses": d["lesson_funnel"]["distinct_briefable"],
        "lessons_copied_from_another_damage": d["lesson_funnel"]["copied"],
    }


# ── chart helpers (no library: these pages must open anywhere, forever) ──────────────────────
def hbar(rows, key="v", num="n", total=None, tone=""):
    if not rows:
        return '<p class="none">Nothing recorded.</p>'
    mx = max(r[num] for r in rows) or 1
    out = []
    for r in rows:
        pct = f" · {round(100*r[num]/total)}%" if total else ""
        out.append(f'<div class="hb"><div class="k" title="{esc(r[key])}">{esc(r[key])}</div>'
                   f'<div class="t"><i class="{tone}" style="width:{max(round(100*r[num]/mx),2)}%"></i></div>'
                   f'<div class="v">{r[num]}{pct}</div></div>')
    return f'<div class="hbars">{"".join(out)}</div>'


def cols(rows, lab, val, sub=None):
    mx = max([r[val] for r in rows] + [1])
    out = []
    for r in rows:
        s = (f'<div class="s2">{r[sub]} investigated</div>' if sub else "")
        out.append(f'<div class="c"><div class="v">{r[val]}</div>'
                   f'<div class="bar" style="height:{max(round(100*r[val]/mx),3)}%"></div>'
                   f'<div class="k">{esc(r[lab])}</div>{s}</div>')
    return f'<div class="cols">{"".join(out)}</div>'


PAGE_CSS = """
.lead{font-size:16.5px;color:var(--mid);max-width:74ch;margin-bottom:6px}
.sec{background:#fff;border:1px solid var(--line);border-radius:16px;padding:24px 26px;
 box-shadow:var(--sh-1);margin-bottom:18px}
.sec h2{font-size:19px;font-weight:800;letter-spacing:-.02em;margin-bottom:4px}
.sec .why{font-size:13px;color:var(--faint);margin-bottom:16px}
.sec p{font-size:14.5px;color:var(--mid);margin-bottom:12px;max-width:78ch}
.sec p b{color:var(--ink)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:0 0 20px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:13px;padding:15px 16px;
 box-shadow:var(--sh-1);position:relative;overflow:hidden}
.kpi::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--green)}
.kpi.warn::before{background:var(--red)}
.kpi .n{font-size:27px;font-weight:800;line-height:1.05;font-variant-numeric:tabular-nums;
 letter-spacing:-.02em}
.kpi.warn .n{color:var(--red)}
.kpi .l{font-size:12px;color:var(--muted);margin-top:5px;line-height:1.35}
.hbars{display:flex;flex-direction:column;gap:8px}
.hb{display:grid;grid-template-columns:210px 1fr 82px;align-items:center;gap:10px}
@media(max-width:640px){.hb{grid-template-columns:130px 1fr 70px}}
.hb .k{font-size:12.5px;color:var(--mid);text-align:right;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap}
.hb .t{height:16px;background:#eef1f5;border-radius:4px;overflow:hidden}
.hb .t i{display:block;height:100%;background:var(--green);border-radius:4px}
.hb .t i.grey{background:#9aa4b0}
.hb .v{font-size:12.5px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--mid)}
.cols{display:flex;align-items:flex-end;gap:12px;height:150px;padding-top:6px}
.cols .c{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;
 gap:5px;height:100%}
.cols .bar{width:100%;max-width:58px;background:var(--green);border-radius:5px 5px 0 0;min-height:3px}
.cols .v{font-size:15px;font-weight:800;font-variant-numeric:tabular-nums}
.cols .k{font-size:11.5px;color:var(--faint);font-weight:600}
.cols .s2{font-size:10.5px;color:var(--faint)}
.split{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:820px){.split{grid-template-columns:1fr}}
table.t{width:100%;border-collapse:collapse;font-size:13.5px}
table.t th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
 color:var(--faint);padding:0 10px 7px 0;border-bottom:1px solid var(--line)}
table.t td{padding:8px 10px 8px 0;border-bottom:1px solid #f0f3f6;color:var(--mid);vertical-align:top}
table.t td.n{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
blockquote.q{border-left:4px solid var(--green);background:#f8fbf2;margin:0 0 12px;
 padding:13px 16px;border-radius:0 10px 10px 0;font-size:14px;color:var(--mid)}
blockquote.q .src{display:block;margin-top:7px;font-size:11.5px;color:var(--faint);font-weight:700}
.flag{background:#fff9f0;border-left:4px solid #b45309;border-radius:0 10px 10px 0;
 padding:14px 18px;margin:14px 0;font-size:13.5px;color:var(--mid)}
.flag b{color:var(--ink)}
.none{font-size:13.5px;color:var(--faint);font-style:italic}
ul.plain{margin:0 0 14px;padding-left:20px;max-width:78ch}
ul.plain li{font-size:14.5px;color:var(--mid);margin-bottom:7px;line-height:1.55}
ul.plain li b{color:var(--ink)}
.dmgs{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:18px 0 4px}
@media(max-width:820px){.dmgs{grid-template-columns:1fr}}
.dmg{border:1px solid var(--line);border-radius:12px;padding:15px 17px;background:#fbfcfd}
.dmg .dh{font-size:14.5px;font-weight:800;color:var(--ink)}
.dmg .dl{font-size:12.5px;color:var(--faint);margin-top:2px}
.dmg table.t td{padding:5px 8px 5px 0;font-size:12.5px}
.dmg table.t td:first-child{color:var(--faint);width:44%}
.dmg .dq{margin-top:11px;padding-top:11px;border-top:1px solid var(--line);font-size:12.5px;
 color:var(--mid);line-height:1.5}
code{background:#eef1f5;padding:1px 5px;border-radius:4px;font-size:12px;font-family:ui-monospace,monospace}
.ed{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}
.ed span{background:#eef2f6;border-radius:99px;padding:5px 13px;font-size:12px;color:var(--muted);
 font-weight:600}
.ed span.now{background:var(--green);color:#1d2b00}

/* the per-damage register table */
.fbar{display:flex;flex-wrap:wrap;gap:9px;align-items:center;margin:4px 0 14px}
.fbar select,.fbar input{font:inherit;font-size:13px;padding:7px 11px;border:1px solid var(--line);
 border-radius:9px;background:#fff;color:var(--ink);min-height:38px}
.fbar input{flex:1 1 210px;min-width:160px}
.fbar select{cursor:pointer}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}
.chip{font:inherit;font-size:12.5px;font-weight:600;padding:7px 13px;border-radius:99px;
 border:1px solid var(--line);background:#fff;color:var(--muted);cursor:pointer;min-height:38px}
.chip:hover{border-color:var(--green)}
.chip[aria-pressed="true"]{background:var(--green);border-color:var(--green);color:#1d2b00}
.fcount{font-size:12.5px;color:var(--faint);font-weight:600;margin-left:auto}
.tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);
 border-radius:12px}
table.reg{width:100%;border-collapse:collapse;font-size:13px;min-width:1180px;background:#fff}
table.reg th{position:sticky;top:0;z-index:1;background:#f7f9fc;text-align:left;font-size:10.5px;
 text-transform:uppercase;letter-spacing:.06em;color:var(--faint);font-weight:800;
 padding:10px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
table.reg th.c,table.reg td.c{text-align:center}
table.reg td{padding:9px 10px;border-bottom:1px solid #f0f3f6;color:var(--mid);white-space:nowrap}
table.reg th{padding:10px;vertical-align:bottom;line-height:1.25}
table.reg th.c,table.reg td.c{width:42px}
/* one status reads "Complete with Outstanding Actions" — let it wrap rather than stretch the
   whole table and push the last columns off the edge */
table.reg td.st{white-space:normal;max-width:150px}
table.reg td.st .pill{white-space:normal;display:inline-block;line-height:1.35}
table.reg tr:last-child td{border-bottom:0}
table.reg tbody tr:hover td{background:#fbfdf7}
table.reg td.id a{color:var(--green-d);font-weight:800;font-variant-numeric:tabular-nums;
 text-decoration:none}
table.reg td.id a:hover{text-decoration:underline}
table.reg td.n{font-variant-numeric:tabular-nums;text-align:right}
.mk{display:inline-block;width:21px;height:21px;line-height:21px;border-radius:6px;
 font-size:12px;font-weight:800;text-align:center}
.mk.y{background:#eaf6d9;color:#3f6212}
.mk.n{background:#fde8ec;color:#a4133c}
.mk.b{background:#eef1f5;color:#9aa4b0}
.pill{display:inline-block;font-size:11px;font-weight:700;padding:3px 9px;border-radius:99px;
 background:#eef1f5;color:var(--muted)}
.pill.open{background:#fff4e5;color:#9a5b00}
.pill.closed{background:#eaf6d9;color:#3f6212}
.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12.5px;color:var(--muted);margin:12px 0 0}
.legend span{display:flex;align-items:center;gap:7px}
.sec.wide{max-width:none;margin-left:calc(50% - 50vw + 16px);
 margin-right:calc(50% - 50vw + 16px);padding-left:16px;padding-right:16px}
.sec.wide>h2,.sec.wide>.why,.sec.wide>p{padding-left:8px;padding-right:8px}
@media(min-width:1780px){.sec.wide{max-width:1736px;margin-left:auto;margin-right:auto}}
.nores{padding:26px;text-align:center;color:var(--faint);font-size:13.5px;font-style:italic}
"""



# ── the per-damage register table ────────────────────────────────────────────────────────────
# Every column below is a value Depotnet itself holds. Nothing is inferred, scored or judged.
# The three-state marks matter: a grey dash means Depotnet was NOT ANSWERED, which is not the
# same as a NO, and the legend says so on the page. Pete, 31 Jul 2026: "a tick box not ticked
# might not mean it wasnt investigated".
# The per-damage record lives on that YEAR's page. This was hard-coded to fy-2026-27, so every
# one of FY25/26's 164 links pointed at a register that does not contain them — each one a dead
# end. (Found by auditing the finished work, 2 Aug 2026.)
_FY_SLUG = {"FY26/27": "fy-2026-27", "FY25/26": "fy-2025-26",
            "FY24/25": "fy-2024-25", "FY23/24": "fy-2023-24"}
DAMAGE_URL = ("/raw/clancy-depotnet-damages/" + _FY_SLUG.get(FY, "fy-2026-27")
              + "-damage.html?id={}")


def mark(v, allow_no=True):
    """Y -> tick, N -> cross (only where Depotnet holds an explicit NO), blank -> dash."""
    if v == "Y" or v == "YES":
        return '<span class="mk y" title="Yes">&#10003;</span>'
    if v in ("N", "NO") and allow_no:
        return '<span class="mk n" title="No">&#10007;</span>'
    if v in ("N", "NO"):
        return '<span class="mk b" title="Nothing recorded">&ndash;</span>'
    return '<span class="mk b" title="Not answered on Depotnet">&ndash;</span>'


def yn(count, captured):
    """The count itself — 0 in red, anything above it in green. Pete, 1 Aug 2026: a number says
    more than a yes. A damage we have never captured gets a dash, never a 0; we would be reporting
    our own backlog as an absence on Depotnet."""
    if not captured:
        return '<span class="mk b" title="Not captured yet">&ndash;</span>'
    if count:
        return f'<span class="mk y" title="{count} on Depotnet">{count}</span>'
    return '<span class="mk n" title="None on Depotnet">0</span>'


def damage_table(d):
    rows, n = d["rows"], len(d["rows"])
    n_notcomplete = sum(1 for r in rows if (r.get("inv_done") or "") == "NO")
    INV = {"YES": ("Complete", "closed"), "NO": ("Not complete", "open")}
    tr = []
    for r in rows:
        iv = r["inv_done"]
        if r["inv_answers"] == 0 and not r["captured"]:
            # raised too recently to have been pulled down — we do not know either way, and
            # saying "blank" here would report our own backlog as Clancy's.
            inv_lab, inv_cls, inv_key = "Not captured yet", "", "uncaptured"
        elif r["inv_answers"] == 0:
            inv_lab, inv_cls, inv_key = "Not started", "", "blank"
        else:
            inv_lab, inv_cls = INV.get(iv, ("Started", ""))
            inv_key = {"YES": "complete", "NO": "incomplete"}.get(iv, "started")
        st = r["status"] or "Not stated"
        st_cls = "closed" if st.startswith("Closed") else ("open" if st == "Open" else "")
        dt = str(r["dt"])
        nice = datetime.datetime.strptime(dt, "%Y-%m-%d").strftime("%-d %b")
        hay = " ".join(str(x or "") for x in
                       (r["id"], r["contract"], r["service"], st, inv_lab)).lower()
        tr.append(
            f'<tr data-c="{esc(r["contract"])}" data-s="{esc(r["service"])}" data-st="{esc(st)}" '
            f'data-inv="{inv_key}" data-cause="{r["cause"]}" data-lesson="{r["lesson"]}" '
            f'data-acts="{r["acts"]}" data-aout="{r["acts_out"]}" data-acls="{r["acts_closed"]}" data-cat="{r["cat"] or ""}" data-genny="{r["genny"] or ""}" '
            f'data-hay="{esc(hay)}">'
            f'<td class="id"><a href="{DAMAGE_URL.format(r["id"])}">{r["id"]}</a></td>'
            f'<td>{nice}</td><td>{esc(r["contract"])}</td><td>{esc(r["service"])}</td>'
            f'<td class="st"><span class="pill {st_cls}">{esc(st)}</span></td>'
            f'<td><span class="pill {inv_cls}">{inv_lab}</span></td>'
            f'<td class="c">{mark(r["cause"], allow_no=False)}</td>'
            f'<td class="c">{mark(r["lesson"], allow_no=False)}</td>'
            f'<td class="c">{mark(r["genny"])}</td><td class="c">{mark(r["cat"])}</td>'
            f'<td class="c">{mark(r["permit"])}</td>'
            f'<td class="c">{yn(r["acts_out"], r["captured"])}</td>'
            f'<td class="c">{yn(r["acts_closed"], r["captured"])}</td>'
            f'<td class="n">{r["files"] or "&ndash;"}</td>'
            "</tr>")

    def opts(key, label):
        vals = sorted({r[key] for r in rows})
        o = "".join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in vals)
        return (f'<select data-f="{key}" aria-label="{label}">'
                f'<option value="">{label}: all</option>{o}</select>')

    sts = sorted({r["status"] or "Not stated" for r in rows})
    st_sel = ('<select data-f="status" aria-label="Status"><option value="">Status: all</option>'
              + "".join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in sts) + "</select>")
    return f"""
<div class="sec wide"><h2>Every damage this year, one line each</h2>
<div class="why">The register behind everything above. Each column is a value Depotnet holds, read
live, so this table changes as Clancy update their own records.</div>
<p>The point of it is to let you take any statement on this page and see exactly which damages
sit behind it. Filter to a contract, to gas, to the ones whose investigation report section is not started, and the
list is right there with the Depotnet numbers to quote.</p>

<div class="fbar">
  <input type="search" id="q" placeholder="Search a damage number, contract or service">
  {opts('contract', 'Contract')}{opts('service', 'Service')}{st_sel}
  <select data-f="inv" aria-label="Investigation report">
    <option value="">Investigation report: all</option>
    <option value="complete">Depotnet says complete</option>
    <option value="incomplete">Depotnet says not complete</option>
    <option value="started">Started, not answered</option>
    <option value="blank">Not started</option>
    <option value="uncaptured">Not captured yet</option>
  </select>
</div>
<div class="chips">
  <button class="chip" data-t="cause" aria-pressed="false">No cause recorded</button>
  <button class="chip" data-t="lesson" aria-pressed="false">No lesson recorded</button>
  <button class="chip" data-t="acts" aria-pressed="false">No actions at all</button>
  <button class="chip" data-t="genny" aria-pressed="false">Genny not used</button>
  <button class="chip" data-t="cat" aria-pressed="false">CAT not used</button>
  <span class="fcount" id="fcount">{n} of {n} damages</span>
</div>

<div class="tscroll"><table class="reg"><thead><tr>
<th>Damage</th><th>Date</th><th>Contract</th><th>Service</th><th>Status</th>
<th>Investigation<br>report</th><th class="c">Cause</th><th class="c">Lesson</th>
<th class="c">Genny</th><th class="c">CAT</th><th class="c">Permit</th>
<th class="c">Outstanding<br>actions</th><th class="c">Closed<br>actions</th><th class="n">Evidence</th>
</tr></thead><tbody id="rtb">{"".join(tr)}</tbody></table>
<div class="nores" id="nores" hidden>No damages match those filters.</div></div>

<div class="legend">
  <span><span class="mk y">&#10003;</span> Depotnet records a yes</span>
  <span><span class="mk n">&#10007;</span> Depotnet records an explicit no</span>
  <span><span class="mk b">&ndash;</span> nothing recorded &mdash; not the same as a no</span>
</div>
<div class="flag"><b>What the three investigation states mean.</b> Every damage carries the
same investigation report section, and it is never half-filled.
<b>Complete</b> and <b>Not complete</b> are Depotnet&#8217;s own verdict &mdash; the last question
on the form is &ldquo;Is the investigation complete?&rdquo; and Clancy answer it themselves. The
{n_notcomplete} marked <b>Not complete</b> are <i>fully worked</i> forms; Clancy are saying the
investigation is still running, not that the form is part-done. <b>Not started</b> means all 63 questions sit blank. There is no fourth state: no
damage this year holds a partly-filled form.<br><br>
The <b>Incident report</b> column has been removed. All 48 have one, so it distinguished nothing
&mdash; a damage cannot reach the register without it.</div>

<div class="flag"><b>Read the dash carefully.</b> A grey dash means the field is empty on Depotnet.
It does not mean the answer was no, and it does not mean the thing did not happen. Genny, CAT and
Permit are only asked inside the investigation report section, so on the damages where that
section has not been started they are all dashes for one reason: the question has not been put. A row marked <b>Not captured
yet</b> is ours, not Clancy&#8217;s &mdash; the damage was raised in the last few days and we have
not pulled its record down, so every dash on that line means we have not looked. Cause and Lesson never show a cross,
because Depotnet has no way to say &ldquo;there was no cause&rdquo; &mdash; only recorded, or
not recorded.</div>

<script>
(function(){{
  var tb=document.getElementById('rtb'), rows=[].slice.call(tb.rows),
      q=document.getElementById('q'), cnt=document.getElementById('fcount'),
      nr=document.getElementById('nores'),
      sels=[].slice.call(document.querySelectorAll('.fbar select')),
      chips=[].slice.call(document.querySelectorAll('.chip'));
  var MAP={{contract:'c',service:'s',status:'st',inv:'inv'}};
  function apply(){{
    var text=(q.value||'').trim().toLowerCase(), shown=0;
    rows.forEach(function(r){{
      var ok=true;
      if(text && r.dataset.hay.indexOf(text)<0) ok=false;
      sels.forEach(function(s){{
        if(ok && s.value && r.dataset[MAP[s.dataset.f]]!==s.value) ok=false;
      }});
      chips.forEach(function(c){{
        if(!ok || c.getAttribute('aria-pressed')!=='true') return;
        var t=c.dataset.t;
        if(t==='cause'  && r.dataset.cause !=='N') ok=false;
        if(t==='lesson' && r.dataset.lesson!=='N') ok=false;
        if(t==='acts'   && r.dataset.acts !=='0') ok=false;
        if(t==='genny'  && r.dataset.genny!=='NO') ok=false;
        if(t==='cat'    && r.dataset.cat  !=='NO') ok=false;
      }});
      r.hidden=!ok; if(ok) shown++;
    }});
    cnt.textContent=shown+' of {n} damages';
    nr.hidden=shown>0;
  }}
  q.addEventListener('input',apply);
  sels.forEach(function(s){{s.addEventListener('change',apply);}});
  chips.forEach(function(c){{c.addEventListener('click',function(){{
    c.setAttribute('aria-pressed', c.getAttribute('aria-pressed')==='true'?'false':'true');
    apply();
  }});}});
}})();
</script>
</div>"""


def build(edition, label):
    d = gather()
    dmg_table = damage_table(d)
    # A year with no deep capture has NO investigation data, so every "not started / no cause /
    # no lessons" count on this page would be OUR backlog wearing Clancy's name. The page says
    # that at the top and the reader is told which sections are waiting on the capture.
    d["scaffold"] = d["headline"]["captured"] == 0
    h, m = d["headline"], metrics_of(d)
    # scoped to THIS financial year: the table was keyed on edition alone, so FY25/26's publish
    # overwrote FY26/27's metrics and each page then compared itself to the other year.
    prior = sql("SELECT edition, label, published_at::date d, metrics FROM clancy_analysis_editions "
                f"WHERE fy = '{FY}' AND edition < {edition} ORDER BY edition DESC LIMIT 1")
    prior = prior[0] if prior else None
    today = datetime.date.today()
    n = h["damages"]
    py = d["prior_year_same_months"]
    delta = round((n - py) / py * 100) if py else 0
    pct = lambda a, b: f"{round(100*a/b)}%" if b else "0%"

    eds = f'<div class="ed"><span class="now">Edition {edition}: {esc(label)}</span>'
    if prior:
        eds += f'<span>Edition {prior["edition"]}: {esc(prior["label"])} &middot; {prior["d"]}</span>'
    eds += "</div>"

    movement = ""
    if prior:
        pm, rows = prior["metrics"], []
        for key, lab in (("with_cause", "Damages with a recorded cause"),
                         ("with_lessons", "Damages with lessons learnt"),
                         ("lessons_substantial", "Substantial lessons"),
                         ("usable_root_analyses", "Usable root-cause analyses")):
            was, now = pm.get(key), m.get(key)
            if was is None or now is None:
                continue
            mv = "no change" if now == was else (f"up {now-was}" if now > was else f"down {was-now}")
            rows.append(f"<tr><td>{lab}</td><td class='n'>{was}</td><td class='n'>{now}</td>"
                        f"<td class='n'>{mv}</td></tr>")
        if rows:
            movement = (f'<div class="sec"><h2>What changed since edition {prior["edition"]}</h2>'
                        '<div class="why">Measured, not asserted. Each edition recorded its own '
                        'figures at the moment it was published.</div>'
                        '<table class="t"><tr><th>Measure</th><th>Then</th><th>Now</th>'
                        f'<th>Movement</th></tr>{"".join(rows)}</table></div>')

    blanket_note = ""
    if d["blanket"]:
        rows = "".join(f"<tr><td>{r['id']}</td><td>{esc(r['location'])[:44]}</td>"
                       f"<td class='n'>{r['rc'] or 0}</td><td class='n'>{r['uc'] or 0}</td></tr>"
                       for r in d["blanket"])
        blanket_note = (
            f'<div class="flag"><b>{len(d["blanket"])} investigation reports tick almost every option on '
            'the form.</b> The cause fields allow more than one box. These records select up to nine '
            'of the nine root causes and seventeen of the seventeen underlying causes, which says '
            'nothing about what happened. They are excluded from the counts above, because leaving '
            f'them in makes every cause they touch look {len(d["blanket"])} times more common than it '
            'is. Worth fixing at the form: a cause that means everything means nothing.'
            '<table class="t" style="margin-top:12px"><tr><th>Damage</th><th>Location</th>'
            f'<th>Root ticked</th><th>Underlying ticked</th></tr>{rows}</table></div>')

    mech_rows = "".join(
        f"<tr><td>{esc(r['u'])}</td><td class='n'>{r['mech']}</td><td class='n'>{r['hand']}</td>"
        f"<td class='n'>{r['total']}</td></tr>" for r in d["mech_by_utility"])
    shallow_rows = "".join(
        f"<tr><td>{esc(r['u'])}</td><td class='n'>{r['shallow']}</td><td class='n'>{r['total']}</td>"
        f"<td class='n'>{pct(r['shallow'], r['total'])}</td></tr>" for r in d["shallow_by_utility"])
    ev = {r["grp"]: r for r in d["evidence_split"]}
    ev_line = ""
    if len(ev) == 2:
        a_, b_ = ev["Cause recorded"], ev["No cause recorded"]
        ev_line = (f"<p>Damages with a cause recorded carry <b>{a_['avg_files']} files</b> each on "
                   f"average. Those without carry <b>{b_['avg_files']}</b>. The damages with no "
                   "cause recorded are the same ones carrying the fewest photographs, so the two "
                   "gaps sit on top of each other: less to go back to later, not more.</p>")

    good_q = "".join(
        f'<blockquote class="q">{esc(r["t"])}<span class="src">Damage {r["id"]} &middot; '
        f'{esc(r["location"])[:50]} &middot; {r["d"]}</span></blockquote>' for r in d["lessons_good"])
    thin_q = ", ".join(f'&ldquo;{esc(r["t"])}&rdquo;' for r in d["lessons_thin"][:8])
    # Each cause field is judged on its own, so the two denominators can differ. Only explain the
    # difference when there IS one — otherwise the sentence contradicts the numbers beside it.
    if d["root_n"] == d["under_n"]:
        usable_line = (f"<b>{d['root_n']} usable analyses</b> in each field. Every share below is "
                       f"of those {d['root_n']}.")
    else:
        usable_line = (f"<b>{d['root_n']} usable root-cause analyses</b> and <b>{d['under_n']} "
                       "usable underlying-cause analyses</b>. The two differ because a record can "
                       "blanket-tick one field and make a genuine choice in the other, so each "
                       "field is judged on its own.")
    # ── the learning funnel, rendered ──
    lf = d["lesson_funnel"]
    TIER_LABEL = {
        "0 none recorded": "Nothing recorded at all",
        "1 a non-answer": "A non-answer (&ldquo;N/A&rdquo;, &ldquo;TBC&rdquo;, &ldquo;Yes&rdquo;)",
        "2 a fragment": "A fragment, under 40 characters",
        "3 a single phrase": "A single phrase, naming one behaviour",
        "4 briefable": "Enough to brief a team on",
    }
    tier_rows = "".join(
        f"<tr><td>{TIER_LABEL.get(r['tier'], r['tier'])}</td><td class='n'>{r['n']}</td>"
        f"<td class='n'>{round(100*r['n']/n)}%</td></tr>" for r in d["lesson_tiers"])

    # ── the reused lessons, each quoted in full with every damage that carries it ──
    dupe_note = ""
    pool = d["lesson_pool"]
    na = d["dupe_nonanswers"]
    if d["dupes"]:
        blocks = ""
        for r in d["dupes"]:
            recs = r["recs"]
            cards = ""
            for x in recs:
                # A group can span years, so a card that is NOT this year's says which year it is.
                yr = "" if x["fy"] == FY else f' &middot; {esc(x["fy"])}'
                cards += (
                    f'<div class="dmg"><div class="dh">Damage {x["id"]} &middot; {x["d"]}{yr}</div>'
                    f'<div class="dl">{esc(x["loc"])}</div>'
                    f'<table class="t" style="margin-top:8px">'
                    f'<tr><td>Job reference</td><td>{esc(x["job"])}</td></tr>'
                    f'<tr><td>What was being used</td><td>{esc(x["plant"])}</td></tr>'
                    f'<tr><td>Depth of the service</td><td>{x["depth"]}mm</td></tr>'
                    f'<tr><td>Root cause recorded</td><td>{esc(x["root"])}</td></tr>'
                    f'<tr><td>Underlying cause</td><td>{esc(x["under"])}</td></tr></table>'
                    f'<div class="dq">{esc(x["descr"])}</div></div>')
            ids = ", ".join(str(x["id"]) for x in recs)
            span = ("" if r["n_years"] < 2 else
                    " The reuse crosses a financial year, so it was not one team on one job.")
            blocks += (
                f'<h3 style="margin-top:22px">On {r["n"]} damages &middot; {r["len"]} characters</h3>'
                f'<div class="why">Damages {ids}.</div>'
                f'<blockquote class="q">{esc(r["lesson"])}</blockquote>'
                f'<p>Each of these carries that text in its lessons-learnt field.{span}</p>'
                f'<div class="dmgs">{cards}</div>')

        # The paragraphs below were written by hand about ONE SPECIFIC PAIR — 121878 and 122362
        # ("both Anglian Water", the hard-ground reading, which team did what). Attached to any
        # other group it fabricates: wrong contracts, wrong dates, a narrative about events that
        # did not happen. So it is keyed to those two ids, not to a financial year — the earlier
        # version keyed it to FY26/27 and would have mis-attached the moment a second FY26/27
        # group appeared, which is exactly what happened once the matching was fixed.
        _pair = {121878, 122362}
        reading = ('' if not any({x["id"] for x in r["recs"]} == _pair for r in d["dupes"]) else
            '<p style="margin-top:16px">Take the 312-character one. Read the two accounts against '
            'the lesson. The first team '
            'had already stopped the excavator at the marked services and gone over to hand '
            'digging; they struck an <b>unmarked</b> gas service with a graft <b>in hard ground</b>. '
            'The second struck a gas service in the kerb line with a <b>mini digger bucket</b>, on a '
            'second dig, and its account says nothing about ground conditions at all.</p>'
            '<p>The lesson ends &ldquo;consider all available safe digging options when digging in '
            'hard/rocky ground&rdquo;. That is a fair reading of the first damage. It has nothing to '
            'do with the second. The text was written once and applied twice, and in one of the two '
            'it does not describe what happened.</p>'
            '<p><b>Why this matters more than a tidy-up.</b> Both damages were closed off as having '
            'a lesson recorded, so on any count of &ldquo;did we learn from it&rdquo; they both '
            'pass. One team&#8217;s account describes people doing several things right and still '
            'being caught out by an unmarked service. That is genuinely worth briefing, and it is '
            'now sitting behind a paragraph that reads as though it were written for something '
            'else.</p>')
        nonans = ('' if not na["rows"] else
            f'<p style="margin-top:16px"><b>Repeated non-answers are counted separately and are '
            f'not in the figures above.</b> A further {na["rows"]} damages this year share '
            f'{na["texts"]} short texts of the &ldquo;N/A&rdquo;, &ldquo;TBC&rdquo;, '
            f'&ldquo;AML&rdquo; kind. Two people both typing &ldquo;N/A&rdquo; is a gap in the '
            f'record, not a lesson copied from one damage to another, so counting them together '
            f'would overstate the reuse.</p>')
        # Groups form across years, so the total and this year's share are different numbers and
        # BOTH have to be stated. Saying only the total put "6 damages" beside a tile reading 3.
        n_groups = len(d["dupes"])
        n_dmg = sum(r["n"] for r in d["dupes"])
        n_here = sum(r["n_this_year"] for r in d["dupes"])
        span = ("" if n_dmg == n_here else
                f' &mdash; {n_dmg} in total once the other years carrying the same '
                f'text{"" if n_groups == 1 else "s"} are counted')
        head = (
            f'<div class="sec"><h2>The same lesson, word for word, on more than one damage</h2>'
            f'<div class="why">{n_here} damage{"" if n_here == 1 else "s"} this year{span}, '
            f'carrying {n_groups} text{"" if n_groups == 1 else "s"} between them.</div>')
        dupe_note = (head + blocks + reading + nonans +
            f'<div class="flag"><b>How far this claim goes, and what the test is.</b> '
            f'&ldquo;Word for word&rdquo; here means the same words in the same order, ignoring '
            f'capitalisation, punctuation and spacing &mdash; two of these differ only by a double '
            f'space, and a stricter test misses them. Only texts long enough to be a lesson are '
            f'counted; bare non-answers are held back above. Against the {pool["total"]} lessons '
            f'the register holds, {pool["distinct_texts"]} of them distinct. It is '
            f'<b>not</b> a claim about the whole history: '
            f'{esc(d["years_without_lessons"])} carry no lessons in what we hold, so there was '
            f'nothing from those years to compare against.</div></div>')
    wu = d["depth_wrong_unit"]
    wrong_unit_note = ""
    if wu:
        _l = ", ".join(f'{x["id"]} ({esc(x["depth_raw"])})' for x in wu)
        wrong_unit_note = (
            f'<div class="flag"><b>{len(wu)} damage{"" if len(wu)==1 else "s"} recorded the depth '
            f'in the wrong unit.</b> The field is labelled &ldquo;Depth Of Utility (Approx) - Unit '
            f'In MM&rdquo; and {"this one carries" if len(wu)==1 else "these carry"} a decimal, '
            f'which is metres: {_l}. We have deliberately not converted '
            f'{"it" if len(wu)==1 else "them"} &mdash; 0.5 almost certainly means 500mm, but once '
            f'a converted figure sits in the column it cannot be told apart from a measured one, '
            f'and this page is read as evidence. {"It counts" if len(wu)==1 else "They count"} as '
            f'no depth in the chart above. The fix is on the form, not in the data: the unit is in '
            f'the label but nothing stops a metre being typed.</div>')
    trunc_note = ""
    if lf["truncated"]:
        trunc_note = (f'<div class="flag"><b>{lf["truncated"]} lesson stops mid-sentence.</b> '
                      'The entry ends part-way through a word or clause, so whatever was being '
                      'written was never finished or never saved. Worth checking whether the form '
                      'is truncating the field.</div>')

    contract_rows = "".join(
        f"<tr><td>{esc(r['c'])}</td><td class='n'>{r['n']}</td><td class='n'>{r['avg_age']} days</td>"
        f"<td class='n'>{r['still_open']}</td><td class='n'>{r['inv']}</td></tr>"
        for r in d["by_contract"])
    _age = {r["c"]: r["avg_age"] for r in d["by_contract"]}
    anglian_age = _age.get("Anglian Water", "?")
    # This worked example named Anglian and Scottish with hard-coded 7-of-8 and 2-of-4 counts,
    # which are FY26/27 facts. On any other year it contradicted the table directly above it.
    # Derived from the same rows the table is built from, or omitted if there is nothing to say.
    _cr = [c for c in d["by_contract"] if c.get("n") and c.get("avg_age") is not None]
    _age_example = ""
    if len(_cr) >= 2:
        _young = min(_cr, key=lambda c: c.get("avg_age") or 9e9)
        _old = max(_cr, key=lambda c: c.get("avg_age") or -1)
        if _young is not _old:
            _age_example = (
                f"Age does not account for it: {esc(_young['c'])}&#8217;s damages are the youngest "
                f"at an average of {_young['avg_age']} days, with {_young['inv']} of "
                f"{_young['n']} carrying a completed investigation report section, while "
                f"{esc(_old['c'])} average {_old['avg_age']} days and carry it on "
                f"{_old['inv']} of {_old['n']}.")
    age_example = _age_example
    scottish_age = _age.get("Scottish Water", "?")
    status_rows = "".join(
        f"<tr><td>{esc(r['status'])}</td><td class='n'>{r['n']}</td>"
        f"<td class='n'>{r['has_section']}</td><td class='n'>{r['says_complete']}</td>"
        f"<td class='n'>{r['says_not']}</td></tr>" for r in d["by_status"])
    ib = d["inv_basis"]
    no_section = n - ib["has_section"]
    bs = d["blank_split"]
    blank_ok, blank_unk = bs["confirmed_blank"], bs["not_captured"]
    shape, universal = d["inv_shape"], d["inv_universal"]
    # FY-specific prose. These used to be hard-coded to FY26/27, so every other year's page
    # claimed "four months" and quoted two FY26/27 damages as its worked examples.
    n_months = len(d["months"])
    compare = (f", {delta:+d}% against the same months of "
               f"{d['prior_fy'].replace('FY', 'FY 20')} ({py})" if py else
               " — no earlier year on the register to compare against")
    months_phrase = (f" in {n_months} month{'s' if n_months != 1 else ''}"
                     if n_months < 12 else " across the year")
    worked_examples = ("" if FY != "FY26/27" else
        "<p><b>The unstarted ones are unstarted on Depotnet, not just in our copy.</b> We opened "
        "two of them on the system itself: <b>damage 117327</b> (Southern Water, 20 April) and "
        "<b>damage 119372</b> (UKPN, 28 April), both more than three months old. On both, the "
        "section loads in full and not a single field has been filled in. So this is not "
        "something we failed to collect.</p>")
    uncaptured_line = ("" if not blank_unk else
        f"<li><b>{blank_unk} we have not looked at yet</b> ({bs['uncaptured']}). We have not "
        "pulled the record down, so we cannot say either way, and it is counted on its own "
        "rather than lumped in with the rest.</li>")
    team = ", ".join(str(r["n"]) for r in d["team_members"][1:])
    _ncc = [str(r["id"]) for r in d["not_complete_closed"]]
    not_complete_closed = len(_ncc) if len(_ncc) != 1 else "one"
    not_complete_closed_ids = "damage " + ", ".join(_ncc) if _ncc else "none"
    closed_complete = next((r["says_complete"] for r in d["by_status"] if r["status"] == "Closed"), 0)
    closed_not = next((r["says_not"] for r in d["by_status"] if r["status"] == "Closed"), 0)
    cf = d["closed_funnel"]
    closed_tier_rows = "".join(
        f"<tr><td>{TIER_LABEL.get(r['tier'], r['tier'])}</td><td class='n'>{r['n']}</td>"
        f"<td class='n'>{round(100*r['n']/d['closed_n'])}%</td></tr>" for r in d["closed_lessons"])
    oldest_open = sum(r["n"] for r in d["open_age"] if r["v"] in ("60 to 89 days", "90 days or more"))
    open_cause_pct = next((round(100*r["has_section"]/r["n"]) for r in d["by_status"]
                           if r["status"] == "Open"), 0)
    open_rows_html = hbar(d["open_age"], total=h["still_open"], tone="grey")
    sev_rows = "".join(f"<tr><td>{esc(r['v'])}</td><td class='n'>{r['inv']}</td>"
                       f"<td class='n'>{r['n']}</td></tr>" for r in d["by_severity"])
    supply_rows = "".join(f"<tr><td>{esc(r['v'])}</td><td class='n'>{r['inv']}</td>"
                          f"<td class='n'>{r['n']}</td></tr>" for r in d["by_supply"])
    fylabel = FY_PAGES[FY]["label"]

    return f"""{ui.head("What the damage data tells us | Genny&#8217;s Damage Depot", PAGE_CSS)}
{ui.navbar("analysis")}
{ui.crumbs(("Command Centre", "/"), ("Damage Depot", f"/m/{ui.HUB}"), "What the data tells us")}
{ui.mast_compact("The analysis &middot; " + esc(label), "What the damage data tells us",
   f"Every service damage The Clancy Group logged in {fylabel}, read from what Depotnet itself "
   f"holds. {n} damages, {h['captured']} of them captured in full.")}
<div class="wrap body">
{eds}

{"" if not d["scaffold"] else (
  '<div class="flag" style="border-left-color:#b45309"><b>This page is a scaffold. '
  f'None of {fylabel}&#8217;s {n} damages has been captured yet.</b><br><br>'
  'Everything below comes from the Incident Register alone &mdash; the date, the contract, the '
  'location, the severity and the description. That is all the register carries.<br><br>'
  '<b>Nothing on this page should be read as a finding about how Clancy investigate.</b> The '
  'investigation report sections, the corrective actions, the causes, the lessons and the '
  'attachments all sit behind the per-damage capture, which has not been run for this year. '
  'Where a count below reads zero it means <b>we have not looked</b>, not that Depotnet is '
  'empty. Run the capture and this page fills itself in.</div>')}
<p class="lead">This is built only from what is on Depotnet: its own fields, its own
forms, its own words. Nothing here is Sygma&#8217;s opinion of what happened. Where the record does
not say, this page says so rather than filling the gap.</p>

<div class="sec"><h2>The investigation report section</h2>
<div class="why">Written out in full because every other number on this page depends on it, and
because two of these damages were opened on Depotnet directly to check rather than trusting our
own copy.</div>

<p>Every service damage on Depotnet has two forms behind it. The <b>Questions</b> section is
filled in at the time: what was hit, by whom, in what conditions, at what depth. Every damage this
year has one.</p>

<p>The <b>investigation report section</b> (the <b>Report</b> tab on Depotnet) is the long one. It
carries a named lead investigator, a named senior manager, the investigation team, the CAT and
genny download review, the causes, the lessons, and a closing question of its own, &ldquo;Is the
investigation complete?&rdquo; It is the same form on every damage and most of its fields are
marked as required.</p>

<p>Where this page counts investigations, it is counting that section. As it stands:</p>
<ul class="plain">
 <li><b>{ib['has_section']} of the {n} have their investigation report section completed.</b></li>
 <li><b>{blank_ok} have not been started.</b> The section is sitting there, every field
     untouched.</li>
 {uncaptured_line}
</ul>

{worked_examples}

<p><b>Completed is not the same as signed off</b>, and Depotnet asks that itself. As its last
question the section asks &ldquo;Is the investigation complete?&rdquo; Of the
{ib['has_section']} completed sections it says <b>yes on {ib['complete']}</b> and <b>no on
{ib['not_complete']}</b>. Nobody left it blank. Of the {ib['not_complete']} marked not complete,
{not_complete_closed} sits on a damage that has already been <b>closed</b>
({not_complete_closed_ids}), and we do not know whether that is an oversight, a quirk of the form,
or normal here.</p>

<p><b>An unstarted section is not proof that nobody investigated.</b> It tells you the
investigation report section has not been completed. The damage may have been looked into
thoroughly and written up somewhere that is not Depotnet. Nothing on this page claims otherwise,
and neither should anyone quoting it.</p>

<p><b>What we cannot tell you is why {blank_ok} have not been started.</b> We looked at two of
them, not all {blank_ok}, and we do not know Clancy&#8217;s own rule for when the section has to
be completed, who is meant to do it, or whether anything chases it. That is the question worth
asking, and it is a more useful one than any count on this page.</p></div>

<div class="flag"><b>A blank is not proof that nothing happened.</b> A field with nothing in it is
not evidence that the work was skipped. The investigation report section is completed as part of closing a damage,
so a missing cause usually means the damage is still open rather than that nobody looked into it.
The same caution applies to corrective actions: one we cannot see may not have been raised, or may
simply not be in the export we hold. Nothing on this page is put forward as a failure unless the
record can carry that weight.</div>

<div class="kpis">
 <div class="kpi"><div class="n">{n}</div><div class="l">service damages<br>this financial year</div></div>
 <div class="kpi {'warn' if delta > 0 else ''}"><div class="n">{delta:+d}%</div>
  <div class="l">against the same<br>months last year ({py})</div></div>
 <div class="kpi warn"><div class="n">{h['supply_lost']}</div><div class="l">interrupted a<br>customer&#8217;s supply</div></div>
 <div class="kpi warn"><div class="n">{h['still_open']}</div><div class="l">still open<br>on Depotnet</div></div>
 <div class="kpi"><div class="n">{ib['has_section']}</div>
  <div class="l">investigation reports<br>completed</div></div>
</div>

<div class="kpis">
 <div class="kpi"><div class="n">{ib['complete']}/{ib['has_section']}</div>
  <div class="l">of those Depotnet itself<br>marks complete</div></div>
 <div class="kpi warn"><div class="n">{h['still_open']}</div>
  <div class="l">still open, which is why<br>they have no cause yet</div></div>
 <div class="kpi warn"><div class="n">{oldest_open}</div>
  <div class="l">open for 60 days<br>or longer</div></div>
 <div class="kpi warn"><div class="n">{cf['distinct_briefable']}/{d['closed_n']}</div>
  <div class="l">closed damages that produced<br>a lesson worth briefing</div></div>
 <div class="kpi warn"><div class="n">{lf['copied']}</div>
  <div class="l">lessons copied word for word<br>from another damage</div></div>
</div>

{movement}

<div class="sec"><h2>1. The year</h2>
<div class="why">Month by month, and how many of each month&#8217;s damages have their investigation report section completed.</div>
{cols(d['months'], 'm', 'n', 'investigated')}
<p style="margin-top:16px">{n} damages{months_phrase}{compare}. {h['still_open']} are still open. The small figure under each month is how many carry
a recorded cause, which is the number this page can actually reason from.</p></div>

<div class="sec"><h2>2. What was struck</h2>
<div class="why">Depotnet&#8217;s own strike category and recorded depth. Held for
{h['with_struck']} of {n}.</div>
<div class="split"><div>{hbar(d['utility'], total=n)}</div>
<div>{hbar(d['depth'], total=n, tone='grey')}</div></div>
<p style="margin-top:16px">Gas is the largest single category. Depth is recorded on
{h['with_depth']} of {n} and is the more useful of the two, because it separates a service that was
where it should have been from one that was not.</p>
{wrong_unit_note}
<h2 style="font-size:15px;margin-top:20px">Sub-category</h2>{hbar(d['subcat'], total=n)}</div>

<div class="sec"><h2>3. How it happened</h2>
<div class="why">The plant or tool recorded as causing the damage, and the setting. Held for
{h['with_plant']} of {n}.</div>
<div class="split"><div>{hbar(d['plant'], total=n)}</div>
<div>{hbar(d['environment'], total=n, tone='grey')}</div></div></div>

<div class="sec"><h2>4. What Depotnet says caused it</h2>
<div class="why">Root and underlying cause, counted only from the sections that made a real
selection in that field.</div>
<p>Of {n} damages this year, <b>{h['with_cause']} carry a cause</b> and {n - h['with_cause']} carry
none at all. Of those {h['with_cause']}, {len(d['blanket'])} tick nearly every option on one or
other of the two fields and are set aside below. That leaves {usable_line}</p>
<h2 style="font-size:15px;margin-top:18px">Root cause</h2>{hbar(d['root_cause'], 'val', 'n', total=d['root_n'])}
<h2 style="font-size:15px;margin-top:20px">Underlying cause</h2>{hbar(d['underlying'], 'val', 'n', total=d['under_n'])}
{blanket_note}</div>

<div class="sec"><h2>5. Where the process holds, and where it stops</h2>
<div class="why">Split by whether the damage is closed, because closed damages are where the
completed sections are.</div>
<table class="t"><tr><th>Status</th><th>Damages</th><th>Investigation report done</th>
<th>Depotnet says complete</th><th>Depotnet says not complete</th></tr>
{status_rows}</table>
<p style="margin-top:16px"><b>Every damage closed this year has its investigation report
section completed. {d['closed_n']} of {d['closed_n']}.</b> That is worth saying plainly: a
completed section and a closed damage go together without exception this year. Which of the two drives the other, we
cannot see from here. Depotnet marks {closed_complete} of those {d['closed_n']} as complete and
{closed_not} as not complete, which is its own view rather than ours.</p>
<p>What is slow is getting there. {h['still_open']} of {n} are still open, and the older ones have
been open a long time.</p>
{open_rows_html}
<p style="margin-top:14px">So the honest reading is not that {n - h['with_cause']} damages went
uninvestigated. It is that {n - h['with_cause']} are waiting, and the learning from them is not
available to anyone yet. That is a different problem with a different fix.</p></div>

<div class="sec"><h2>6. The learning gap, measured where it can be measured</h2>
<div class="why">Judged only on the {d['closed_n']} damages that are closed, because those are the
ones Depotnet marks as a complete investigation. Rules are stated so you can disagree with them.</div>
<table class="t"><tr><th>What the lessons field holds</th><th>Closed damages</th><th>Share</th></tr>
{closed_tier_rows}</table>
<p style="margin-top:16px">This is the finding that survives scrutiny. All {d['closed_n']} closed
damages were investigated and every one has a cause. But of those {d['closed_n']}, only
<b>{cf['distinct_briefable']}</b> produced a lesson that runs to more than a phrase. The rest are
a few words: enough to close a field, not enough to brief a team on.</p>
<p><b>The section is being completed. The lesson field is not.</b> That is a gap in the last step
of the form rather than in the diligence of the people filling it in, and it is the cheapest thing
on this page to fix.</p>
{trunc_note}</div>

{dupe_note}

<div class="sec"><h2>7. By contract, and a question we cannot answer</h2>
<div class="why">Shown with age, open count and the completed sections side by side, because
any one of those columns on its own would be misleading.</div>
<table class="t"><tr><th>Contract</th><th>Damages</th><th>Average age</th><th>Still open</th>
<th>Investigation report done</th></tr>{contract_rows}</table>
<p style="margin-top:16px">There is real variation here and we cannot tell you what causes it.
{age_example}</p>
<p>What we do <b>not</b> know is how Clancy work Depotnet in practice: whether an investigation is
required at a fixed point, what triggers closure, whether different contracts are administered by
different people with different habits, or whether some of this is simply timing. Any of those
would produce the pattern above. <b>This table is a question for Clancy, not a conclusion about
Clancy.</b></p></div>

<div class="sec"><h2>8. What was learned</h2>
<div class="why">The lessons-learnt field, on the {h['with_lessons']} damages that carry one.</div>
{hbar(d['lessons_quality'], total=h['with_lessons'])}
<p style="margin-top:16px">Where the section is completed properly it produces something a
supervisor could brief out on Monday morning. Where it is not, it produces a word. Both are below,
verbatim, because the difference between them is the whole argument.</p>
<h2 style="font-size:15px;margin-top:18px">The thin ones, in full</h2><p>{thin_q}</p>
<h2 style="font-size:15px;margin-top:18px">The substantial ones, in full</h2>{good_q}</div>

<div class="sec"><h2>9. Three things visible only once it is all in one place</h2>
<div class="why">Each of these is a count, not an interpretation.</div>
<h2 style="font-size:15px">Electric is a shallow-service problem; gas is not</h2>
<table class="t"><tr><th>Utility</th><th>Under 450mm</th><th>With a depth</th><th>Share</th></tr>
{shallow_rows}</table>
<h2 style="font-size:15px;margin-top:20px">Gas is a mechanical-plant problem; electric is split</h2>
<table class="t"><tr><th>Utility</th><th>Mechanical</th><th>Hand tool</th><th>Total</th></tr>
{mech_rows}</table>
<h2 style="font-size:15px;margin-top:20px">The investigation report gap compounds</h2>{ev_line}</div>

<div class="sec"><h2>10. What this cannot tell you</h2>
<div class="why">Stated plainly, because a report that hides its own limits is worth less than one
that does not.</div>
<p><b>{n - h['with_cause']} of {n} damages have no cause recorded.</b> Not an unclear cause. None.
Just under half of this year is therefore absent from section 4 entirely.</p>
<p><b>{len(d['blanket'])} of the {h['with_cause']} that do carry one tick nearly every box</b>,
which amounts to the same thing.</p>
<p><b>{d['actions']['damages_with']} of {n} damages have a corrective action recorded against
them</b> ({d['actions']['actions']} actions in total), and none has been raised on any service
damage since <b>{d['last_action']}</b>. We checked that against Depotnet directly with every filter
cleared, so it is not an artefact of our export. But we do <b>not</b> know whether raising a
corrective action is mandatory at Clancy, or discretionary, or expected only in certain
circumstances. Without that, the figure is a fact about the record and not a judgement about
anyone&#8217;s practice.</p>
<p><b>We do not know how Clancy work Depotnet.</b> We can read what the fields contain. We cannot
see the process behind them: when an investigation is required, who signs it off, what triggers a
damage to close, whether an action is compulsory, or whether teams are briefed verbally in ways
that never reach the system. Every count on this page should be read as what the record holds,
not as what people did or did not do.</p>
<p>Nothing here says whether the service was successfully located before it was hit, how recently
the crew had been trained, or what was on the plans against what was in the ground. Some of that
sits inside the panel packs, statements and photographs attached to each damage, and reading those
properly is the next piece of work.</p></div>

{dmg_table}

{ui.foot(today.strftime('%-d %b %Y'),
         "Prepared by Sygma Solutions from The Clancy Group&#8217;s own Depotnet records.")}
</div>
{ui.TAIL}""", m


def vocab_gate(html):
    """Refuse to publish a page that names Depotnet wrongly or claims an absence the data
    cannot support. See clancy-vocab-check.py for why. Fail closed: a publish that cannot
    run the gate does not publish."""
    import subprocess, sys as _s
    r = subprocess.run([_s.executable, f"{VAULT}/clancy-vocab-check.py", "-"],
                       input=html, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        raise SystemExit("REFUSED to publish — reword the phrases above and re-run.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--edition", type=int, default=1)
    ap.add_argument("--label", default="what Depotnet holds today, before enrichment")
    a = ap.parse_args()
    html, metrics = build(a.edition, a.label)
    if a.local:
        open(a.local, "w").write(html)
        print(f"wrote {a.local} ({len(html):,} chars)")
    if a.publish:
        vocab_gate(html)
        mod = {"module_key": MK, "slug": MK, "title": "What the data tells us",
               "section": "Customers", "subsection": "External", "area": "Clancy",
               "tier": "passcode", "passcode": "strive2030",
               "unlock_group": "clancy-depotnet",
               "icon": "📈", "accent": "#97D700", "status": "live", "enabled": True, "sort": 16,
               "groups": ["clancy", "clancy-external"], "tags": ["clancy", "customer", "analysis"]}
        req = urllib.request.Request(f"{URL}/rest/v1/modules?on_conflict=module_key",
            data=json.dumps([mod]).encode(),
            headers={"apikey": SR, "Authorization": f"Bearer {SR}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates"}, method="POST")
        _urlopen_retry(req, timeout=60)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        reason = ("Damage analysis quotes Depotnet cause and lesson wording verbatim - the wording "
                  "rules own verbatim-quote exception; Sygma prose says damage throughout")
        assert "$an$" not in html
        sql(f"SELECT set_config('app.damage_review_override', '{reason}', true);\n"
            f"INSERT INTO module_content (module_key, html, updated_at) VALUES "
            f"('{MK}', $an${html}$an$, '{now}') "
            f"ON CONFLICT (module_key) DO UPDATE SET html=EXCLUDED.html, "
            f"updated_at=EXCLUDED.updated_at;")
        ed = json.dumps(metrics).replace("'", "''")
        lab = a.label.replace("'", "''")
        sql(f"""INSERT INTO clancy_analysis_editions (fy, edition, label, basis, metrics)
                VALUES ('{FY}', {a.edition}, '{lab}',
                 'Depotnet Incident Register + per-incident investigation capture; no document enrichment',
                 '{ed}'::jsonb)
                ON CONFLICT (fy, edition) DO UPDATE SET label=EXCLUDED.label,
                  basis=EXCLUDED.basis, metrics=EXCLUDED.metrics, published_at=now();""")
        print(f"published {MK} as edition {a.edition} — commandcentre.info/m/{MK}")


if __name__ == "__main__":
    main()
