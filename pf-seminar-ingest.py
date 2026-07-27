#!/usr/bin/env python3
"""pf-seminar-ingest.py — the ONE write path for a Passion Fit seminar summary.

Takes a written summary (markdown) plus its facts, and lands it in the CC as a first-class,
searchable, linked record:

  * type `seminar-summary` (a corpus type — registered in the PF corpus registry)
  * tagged into the PassionFit corpus so the gates, sweeps and Frank sync all see it
  * concept slugs detected from the text and written into BOTH tags and frontmatter
  * `[[slug|Display]]` wikilinks injected on first mention of each concept, so the note
    joins the concept graph instead of floating
  * `audience: shared` so `pf-portal-sync.py` mirrors it to Frank (summaries only — the
    transcripts are never mirrored)
  * embedded by the hourly embedder, which is what makes semantic search work

Run:
    VAULT=/tmp/pbs python3 /tmp/pbs/pf-seminar-ingest.py <summary.md> \
        --date 2026-07-13 --duration "1h 29m" --title "..." \
        [--source-url https://…] [--transcript-chars 78334] [--date-unconfirmed] [--dry-run]

The date is the ONLY identity. `--date-unconfirmed` marks a seminar whose real date could not
be established; it is never guessed (Pete's rule).
"""
import os, re, sys, json, argparse, subprocess, datetime

VAULT = os.environ.get("VAULT", "/tmp/pbs")

# The canonical PassionFit concepts, slug -> display name. Slugs match live vault_notes records,
# so every link resolves. Order matters: longer/more specific phrases are matched first.
CONCEPTS = [
    ("commitment-continuum",              "Commitment Continuum",            ["commitment continuum"]),
    ("control-the-controllables",         "Control the Controllables",       ["control the controllable", "controlling the controllable", "controllables", "vuca", "acuve"]),
    ("direction-support-matrix",          "Direction/Support Matrix",        ["direction/support matrix", "direction and support matrix", "direction support matrix"]),
    ("high-functioning-matrix",           "High Functioning Matrix",         ["high functioning matrix", "high-functioning matrix", "self-sabotage", "self sabotage"]),
    ("ipsative-progression-curve-green-line", "Green Line",                  ["green line", "ipsative progression curve"]),
    ("intuition-scale-learning-behaviours", "Intuition Scale",               ["intuition scale", "learning behaviours"]),
    ("impact-influence-control-legacy",   "Impact, Influence, Control, Legacy", ["impact influence control", "impact, influence, control"]),
    ("the-behaviours-of-the-accomplished", "The Behaviours of the Accomplished", ["behaviours of the accomplished", "accomplishment behaviours"]),
    ("safe-space-vs-soft-space",          "Safe Space vs Soft Space",        ["safe space", "soft space"]),
    ("the-development-paradox",           "The Development Paradox",         ["development paradox"]),
    ("seven-steps-of-performance",        "The Seven Steps of Performance",  ["seven steps of performance", "seven steps"]),
    ("communication-hierarchy",           "Communication Hierarchy",         ["communication hierarchy"]),
    ("effective-goal-setting",            "Effective Goal Setting",          ["effective goal setting", "goal setting", "ipsative goal"]),
    ("ipsative-assessment",               "Ipsative Assessment",             ["ipsative assessment", "ipsative"]),
    ("transactional-state",               "Transactional State",             ["transactional state", "provisional state"]),
    ("blame-and-ownership",               "Blame & Ownership",               ["blame and ownership", "ownership"]),
    ("listening-behaviours",              "Listening Behaviours",            ["listening behaviours"]),
    ("prioritisation",                    "Prioritisation",                  ["prioritisation", "prioritization", "five ds"]),
    ("coachability",                      "Coachability",                    ["coachability", "coachable"]),
    ("potential",                         "Potential",                       ["latent ability", "pie of potential"]),
    ("presence",                          "Presence",                        ["being present", "presence"]),
]

BASE_TAGS = ["passion-fit", "passionfit-concepts", "PA-PassionFit-Concepts", "seminar-summary"]


def detect_and_link(body):
    """Find concepts mentioned in the body; wikilink the FIRST mention of each.

    Skips headings, existing links and code so the prose stays clean and nothing is
    double-linked. Returns (linked_body, [slugs]).
    """
    found, lines = [], body.split("\n")
    for slug, display, phrases in CONCEPTS:
        if slug in found:
            continue
        for i, line in enumerate(lines):
            if line.startswith("#") or line.startswith("|") or "[[" in line:
                continue
            for phrase in phrases:
                m = re.search(r"(?<![\w\[])(" + re.escape(phrase) + r")(?![\w\]])", line, re.I)
                if m:
                    lines[i] = line[:m.start()] + f"[[{slug}|{m.group(1)}]]" + line[m.end():]
                    found.append(slug)
                    break
            if slug in found:
                break
        # a concept can be present without being linkable (only in a heading/table)
        if slug not in found and any(p in body.lower() for p in phrases):
            found.append(slug)
    return "\n".join(lines), found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("summary")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD, or UNDATED")
    ap.add_argument("--duration", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--source-url", default="")
    ap.add_argument("--transcript-chars", type=int, default=0)
    ap.add_argument("--date-unconfirmed", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    raw = open(a.summary).read()

    # The first H1 is the seminar's own title unless one was passed.
    m = re.search(r"^#\s+(.+)$", raw, re.M)
    headline = a.title or (m.group(1).strip() if m else "Seminar")
    body = re.sub(r"^#\s+.+\n", "", raw, count=1, flags=re.M).lstrip()

    body, concepts = detect_and_link(body)

    slug = ("seminar-" + (a.date if a.date != "UNDATED" else "undated-" +
            re.sub(r"[^a-z0-9]+", "-", headline.lower())[:40].strip("-")))
    title = f"Seminar {a.date} — {headline}" if a.date != "UNDATED" else f"Seminar (undated) — {headline}"

    fm = {
        "type": "seminar-summary",
        "title": title,
        "slug": slug,
        "entity": "Personal",
        "date": None if a.date == "UNDATED" else a.date,
        "date_confirmed": (not a.date_unconfirmed) and a.date != "UNDATED",
        "duration": a.duration,
        "concepts": concepts,
        "audience": "shared",
        "source_url": a.source_url,
        "transcript_chars": a.transcript_chars,
        "tags": BASE_TAGS + concepts,
    }

    header = "---\n" + "\n".join(
        f"{k}: {json.dumps(v) if isinstance(v,(list,dict)) or v is None or isinstance(v,bool) else v}"
        for k, v in fm.items()) + "\n---\n\n"

    nav = (f"> **Seminar** · {a.date if a.date!='UNDATED' else 'date not established'}"
           + (f" · {a.duration}" if a.duration else "")
           + (" · ⚠ date unconfirmed" if a.date_unconfirmed else "") + "\n"
           + "> Concepts: " + (", ".join(f"[[{s}|{d}]]" for s, d, _ in CONCEPTS if s in concepts) or "—") + "\n"
           + "> Index: [[pf-seminar-index|All seminars]]\n\n")

    out = header + nav + body
    tmp = os.path.join("/tmp", slug + ".md")
    open(tmp, "w").write(out)

    print(f"{slug}\n  title    : {title}\n  concepts : {len(concepts)} -> {', '.join(concepts) or '(none)'}"
          f"\n  words    : {len(body.split())}\n  links    : {out.count('[[')}")
    if a.dry_run:
        print("  DRY RUN — not ingested")
        return

    r = subprocess.run(["python3", os.path.join(VAULT, "cc-knowledge-ingest.py"), tmp],
                       capture_output=True, text=True, env={**os.environ, "VAULT": VAULT})
    print("  " + (r.stdout.strip().splitlines() or ["(no output)"])[-1])




# --- index -------------------------------------------------------------------
def build_index():
    """Regenerate [[pf-seminar-index]] from the live seminar-summary records.

    Never hand-maintained: run it after every ingest and it reflects reality.
    """
    env = {**os.environ, "VAULT": VAULT}
    r = subprocess.run(["python3", os.path.join(VAULT, "cc-sql.py"),
        "SELECT slug, title, frontmatter->>'date' AS date, frontmatter->>'duration' AS duration, "
        "frontmatter->>'date_confirmed' AS confirmed, frontmatter->'concepts' AS concepts "
        "FROM vault_notes WHERE type='seminar-summary' ORDER BY frontmatter->>'date' DESC NULLS LAST"],
        capture_output=True, text=True, env=env)
    rows = json.loads(r.stdout)

    disp = {s: d for s, d, _ in CONCEPTS}
    by_concept = {}
    for row in rows:
        for c in (row.get("concepts") or []):
            by_concept.setdefault(c, []).append(row)

    L = ["---", "type: index", "title: PF Seminar Index — every seminar, by date and by concept",
         "slug: pf-seminar-index", "entity: Personal", "audience: shared",
         'tags: ["passion-fit", "passionfit-concepts", "PA-PassionFit-Concepts", "seminar-summary", "index"]',
         "---", "",
         "> The front door to the seminar library. **Generated from the live records** by",
         "> `pf-seminar-ingest.py --index` — never hand-maintained, so it cannot drift.", "",
         f"**{len(rows)} written summaries.** Seminars are labelled by DATE, never by week number.", "",
         "## By date", "", "| Date | Seminar | Duration |", "|---|---|---|"]
    for row in rows:
        d = row["date"] or "*date not established*"
        if row.get("confirmed") == "false" and row["date"]:
            d += " ⚠"
        name = row["title"].split("—", 1)[-1].strip()
        L.append(f'| {d} | [[{row["slug"]}\\|{name}]] | {row.get("duration") or ""} |')

    L += ["", "## By concept", "",
          "Which seminars cover which concept. This is what powers the",
          "*seminars that cover this* block on each concept page.", "",
          "| Concept | Seminars |", "|---|---|"]
    for slug, d, _ in CONCEPTS:
        if slug in by_concept:
            items = " · ".join(f'[[{x["slug"]}\\|{x["date"] or "undated"}]]' for x in by_concept[slug])
            L.append(f"| [[{slug}\\|{d}]] | {items} |")

    L += ["", "## Notes", "",
          "- Members see these summaries. They never see the transcripts.",
          "- A seminar with no date is left blank rather than guessed.",
          "- Add a seminar with the `pf-seminars` skill, then re-run this index."]

    tmp = "/tmp/pf-seminar-index.md"
    open(tmp, "w").write("\n".join(L) + "\n")
    subprocess.run(["python3", os.path.join(VAULT, "cc-knowledge-ingest.py"), tmp],
                   capture_output=True, text=True, env=env)
    print(f"index rebuilt: {len(rows)} seminars, {len(by_concept)} concepts covered")


if __name__ == "__main__":
    if "--index" in sys.argv:
        build_index()
    else:
        main()
