#!/usr/bin/env python3
# CRON-META
# what: publish Sygma open course dates from the CC to the public website
# why: /courses/* show a live dates + availability table; without this it is a frozen snapshot and
#      a booked-out date keeps advertising places
# reads: public.ee_public_courses (CC) — the same table the Enquiry Engine quotes from
# writes: src/data/course-dates.json in PortalPeteZero/sygma-solutions-nextjs (git push → Vercel rebuild)
# entity: sygma
# schedule: 30 6 * * *
"""
Push the Enquiry Engine's open course dates onto the public website.

WHY A CRON AND NOT A LIVE QUERY (24 Jul 2026): the website's Supabase project
(mwkpgzjcpltdcdgotryv) is NOT the Command Centre's (zhexcaflgahdcbzvbyfq), so the site cannot read
`ee_public_courses` at request time without putting CC credentials on the website. Rather than widen
that blast radius for a table of six dates, this writes a build-time JSON file and pushes; Vercel
rebuilds on the push.

ONE SOURCE. The website and the Enquiry Engine both quote from `public.ee_public_courses`, so a date
shown on the site and a date quoted in a reply can never disagree.

⚠ NEVER invent a field. Rows arrive with null venue / null places_left. They are written through as
null and the component renders without them. A default venue or a guessed availability figure on a
public booking table is a factual claim we cannot support.

NO-OP WHEN UNCHANGED: if the generated JSON matches what is already committed, nothing is pushed —
so this does not trigger a Vercel rebuild every morning for no reason.

  VAULT=/tmp/pbs python3 sygma-course-dates-publish.py [--dry-run]
"""
import os, sys, json, subprocess, tempfile, shutil

VAULT = os.environ.get("VAULT", "/tmp/pbs")
REPO = "PortalPeteZero/sygma-solutions-nextjs"
TARGET = "src/data/course-dates.json"
DRY = "--dry-run" in sys.argv


def _sql(q):
    r = subprocess.run(["python3", "cc-sql.py", q], cwd=VAULT, capture_output=True, text=True,
                       env={**os.environ, "VAULT": VAULT}, timeout=60)
    if r.returncode != 0 or (r.stderr and "ERROR" in r.stderr):
        raise RuntimeError(f"cc-sql failed: {(r.stderr or r.stdout)[:200]}")
    return json.loads(r.stdout) if r.stdout.strip() else []


def _token():
    # secret is named github-pat, NOT github-token (checked 24 Jul 2026 against public.secrets)
    p = f"{VAULT}/Library/processes/secrets/github-pat"
    if os.path.exists(p):
        return open(p).read().strip()
    return _sql("SELECT value FROM public.secrets WHERE name='github-pat'")[0]["value"].strip()


def build():
    rows = _sql("SELECT course_date, course_title, family, venue, cap, places_left "
                "FROM public.ee_public_courses WHERE course_date >= current_date ORDER BY course_date")
    courses = [{
        "date": r["course_date"],
        "family": r["family"],
        "title": r["course_title"] or "EUSR Cat 1 / CAT and Genny",
        "venue": r["venue"],
        "cap": r["cap"],
        "placesLeft": r["places_left"],
    } for r in rows]
    today = _sql("SELECT current_date::text AS d")[0]["d"]
    return {"generated": today, "source": "public.ee_public_courses (Command Centre)", "courses": courses}


def main():
    doc = build()
    payload = json.dumps(doc, indent=2) + "\n"
    print(f"{len(doc['courses'])} future course date(s) from ee_public_courses")
    for c in doc["courses"]:
        print(f"   {c['date']}  left={c['placesLeft']}  venue={c['venue']}")
    if DRY:
        print("--dry-run: not pushing"); return 0

    tmp = tempfile.mkdtemp(prefix="sygma-dates-")
    try:
        url = f"https://x-access-token:{_token()}@github.com/{REPO}.git"
        subprocess.run(["git", "clone", "--depth", "1", "-q", url, tmp], check=True, timeout=180)
        dest = os.path.join(tmp, TARGET)
        if os.path.exists(dest) and open(dest).read() == payload:
            print("unchanged — nothing to push (no needless Vercel rebuild)"); return 0
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "w").write(payload)
        subprocess.run(["git", "-C", tmp, "add", TARGET], check=True)
        subprocess.run(["git", "-C", tmp, "-c", "user.name=PortalPeteZero",
                        "-c", "user.email=pete.ashcroft@sygma-solutions.com",
                        "commit", "-q", "-m",
                        f"data: refresh open course dates ({len(doc['courses'])} dates, {doc['generated']})"],
                       check=True)
        subprocess.run(["git", "-C", tmp, "push", "-q", "origin", "HEAD"], check=True, timeout=180)
        print(f"pushed {TARGET} -> {REPO} (Vercel will rebuild)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
