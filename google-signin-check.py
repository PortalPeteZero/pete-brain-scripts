#!/usr/bin/env python3
"""
Google sign-in rollout check.

Reports which Sygma staff (@sygma-solutions.com) have linked a Google identity to their
Portal login and which have not yet. Built for the one-off 4pm-Fri 2026-06-19 deadline check
after the all-staff "switch to Google sign-in" email; reusable for any later sweep.

  python3 google-signin-check.py            # query + email Pete the report
  python3 google-signin-check.py --dry-run  # print the report only, send nothing

Data source = live Supabase (auth.identities). Mail = gmail-api.py helper (sends as Pete).
"""
import sys, json, urllib.request, importlib.util
from pathlib import Path

VAULT = Path(VAULT)
REF = "rsczwfstwkthaybxhszy"
TOKEN = (VAULT / "Library/processes/secrets/supabase-token").read_text().strip()
DRY = "--dry-run" in sys.argv

SQL = """
select sd.full_name, sd.work_email,
  exists(select 1 from auth.identities i join auth.users u on u.id=i.user_id
         where lower(u.email)=lower(sd.work_email) and i.provider='google') as has_google
from hub.staff_directory sd
import os
VAULT = os.environ.get("VAULT", "/Users/peterashcroft/Second Brain")
where sd.work_email ilike '%@sygma-solutions.com'
  and lower(coalesce(sd.employment_status,'')) <> 'leaver'
order by sd.full_name;
"""

req = urllib.request.Request(
    f"https://api.supabase.com/v1/projects/{REF}/database/query",
    data=json.dumps({"query": SQL}).encode(),
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
             "User-Agent": "Mozilla/5.0"},
    method="POST",
)
rows = json.loads(urllib.request.urlopen(req, timeout=30).read())

done = [r for r in rows if r["has_google"]]
todo = [r for r in rows if not r["has_google"]]

lines = [f"Google sign-in check, Friday 19 June 4pm.", ""]
lines.append(f"Connected ({len(done)}/{len(rows)}):")
lines += [f"  - {r['full_name']}" for r in done] or ["  (none)"]
lines.append("")
lines.append(f"Still to connect ({len(todo)}):")
lines += [f"  - {r['full_name']} ({r['work_email']})" for r in todo] or ["  (everyone done)"]
report = "\n".join(lines)
print(report)

# Chase template — emailed to anyone still not connected, telling them to do it now.
CHASE_SUBJECT = "Action needed now: connect your Google sign-in"
def first_name(full):
    return (full or "there").split()[0]
def chase_body(name):
    return (
        f"Hi {name},\n\n"
        "You still haven't switched to Google sign-in, and the deadline is today. Please do it now:\n\n"
        "1. If you're already signed in, sign out first (top right).\n"
        "2. Go to sygmaportal.com/auth\n"
        '3. Click "Sign in with Google".\n'
        "4. Choose your @sygma-solutions.com account.\n\n"
        "It takes under a minute. Let me know once it's done.\n\n"
        "Pete"
    )

# Voice-principles guard on the chase template (no em/en/double dash).
_sample = CHASE_SUBJECT + chase_body("Sample")
if _sample.count("—") or _sample.count("–") or _sample.count(" -- "):
    print("CHASE DASH CHECK FAILED — not sending"); sys.exit(2)

if DRY:
    print(f"\n[dry-run] would chase {len(todo)} laggard(s) + email Pete. Sample chase:")
    print("--- " + CHASE_SUBJECT); print(chase_body("Sample")); print("---")
    sys.exit(0)

spec = importlib.util.spec_from_file_location("gmail_api", str(VAULT / "Library/processes/scripts/gmail-api.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
g = mod.GmailAPI()

# Email everyone who still hasn't connected.
chased, chase_fail = [], []
for r in todo:
    try:
        g.send(r["work_email"], CHASE_SUBJECT, chase_body(first_name(r["full_name"])), html=False)
        chased.append(r["work_email"])
    except Exception as e:
        chase_fail.append((r["work_email"], str(e)[:80]))

# Report to Pete, noting who was chased.
report2 = report + f"\n\nChased now ({len(chased)}):\n" + ("\n".join("  - " + e for e in chased) if chased else "  (none)")
if chase_fail:
    report2 += "\n\nChase send failures:\n" + "\n".join(f"  - {e} ({m})" for e, m in chase_fail)
subject = f"Google sign-in: {len(chased)} chased, {len(done)} connected" if todo else "Google sign-in: everyone connected"
g.send("pete.ashcroft@sygma-solutions.com", subject, report2, html=False)
print(f"\nchased {len(chased)} laggard(s); emailed report to pete.ashcroft@sygma-solutions.com")