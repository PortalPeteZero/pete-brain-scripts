"""One-shot Surfer audit on Sygma's 2 priority pages after the 2026-05-11 audit-fix merge.

Runs the morning after merge (scheduled task: 05:00 Atlantic/Canary 2026-05-12).
Audits both pages on LIVE (post-cutover content), records scores to:
  - JSON: Projects/SY-Website/seo/files/surfer-audit-2026-05-12.json
  - Daily note section appended to Daily/2026-05-12.md
  - Stdout (for the scheduled-task Claude session to read + email Pete)

Per CLAUDE.md scheduled-task rule: invoke this via Desktop Commander start_process
with nohup + log redirect, not workspace bash (45s timeout).
"""
import urllib.request, urllib.error, json, time, datetime, sys, os
import os
VAULT = os.environ.get("VAULT", "/tmp/pbs")

SURFER_KEY = "vfv0b3tbStnuc_Utup9AXCsdI32sNT_8"
H = {"API-KEY": SURFER_KEY, "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

PAGES = [
    ("/courses/cat-and-genny-training", "cat and genny training"),
    ("/courses/eusr-cat1", "eusr cat 1"),
]
LIVE = "https://sygma-solutions.com"
LOCATION = "United Kingdom"

VAULT = VAULT
DATE = datetime.date.today().isoformat()
JSON_OUT = f"{VAULT}/Projects/SY-Website/seo/files/surfer-audit-{DATE}.json"
DAILY = f"{VAULT}/Daily/{DATE}.md"


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://app.surferseo.com/api/v1{path}",
        data=data, method=method, headers=H,
    )
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]
    except Exception as e:
        return 0, f"network error: {e}"


def log(msg):
    print(f"[{datetime.datetime.now().isoformat()}] {msg}", flush=True)


# Step 1: kick off audits
log("Kicking off audits...")
audits = []
for path, kw in PAGES:
    body = {"keyword": kw, "url": LIVE + path, "location": LOCATION}
    status, raw = api("POST", "/audits", body)
    try:
        d = json.loads(raw)
    except Exception:
        d = {}
    aid = d.get("id")
    if aid:
        audits.append({"id": aid, "path": path, "kw": kw, "state": d.get("state", "queued")})
        log(f"  POST {path:48} aid={aid} state={d.get('state')}")
    else:
        audits.append({"id": None, "path": path, "kw": kw, "error": f"{status}: {raw[:200]}"})
        log(f"  POST FAIL {path}: {status} {raw[:200]}")

# Step 2: poll to completion (30-min deadline)
log("Polling audits to completion...")
results = {}
pending = [a for a in audits if a.get("id")]
deadline = time.time() + 1800
while pending and time.time() < deadline:
    still = []
    for a in pending:
        status, raw = api("GET", f"/audits/{a['id']}")
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
        state = d.get("state")
        if state == "completed":
            score = d.get("audited_page", {}).get("content_score")
            results[a["path"]] = {
                "score": score,
                "state": state,
                "audit_id": a["id"],
                "kw": a["kw"],
            }
            log(f"  DONE {a['path']:48} score={score}")
        elif state in ("failed", "error"):
            results[a["path"]] = {
                "score": None,
                "state": state,
                "audit_id": a["id"],
                "kw": a["kw"],
                "error": d.get("error", "unknown"),
            }
            log(f"  FAIL {a['path']}: {state}")
        else:
            still.append(a)
    pending = still
    if pending:
        time.sleep(30)

# Step 3: write JSON + daily-note section
out = {
    "date": DATE,
    "ran_at": datetime.datetime.now().isoformat(),
    "context": "Post-merge audit of 2026-05-11 SEO audit-fix branch (CGT + EUSR1 title/meta/CTA/£95/sticky/EUSR-routing changes).",
    "live_url_base": LIVE,
    "location": LOCATION,
    "results": results,
    "errors": [a for a in audits if not a.get("id")],
    "post_fail_count": len([a for a in audits if not a.get("id")]),
}
os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
with open(JSON_OUT, "w") as f:
    json.dump(out, f, indent=2)
log(f"Wrote JSON: {JSON_OUT}")

# Append to daily note
section = f"\n## Sygma Surfer Audit (Automated, post-merge audit-2026-05-11)\n\n"
section += f"- Audited at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} Atlantic/Canary\n"
section += f"- Branch merged 2026-05-11 evening (commit `af73a53` on `PortalPeteZero/sygma-solutions-nextjs main`)\n"
section += f"- Context: validates that the on-page audit fixes (titles, metas, above-fold CTA, £95 visibility, sticky button, EUSR routing block, KH CTA, anchor diversification) did not regress Surfer content score on the 2 priority pages.\n\n"
section += "| Page | Keyword | Surfer Content Score |\n|---|---|---:|\n"
for path, r in results.items():
    score = r.get("score")
    score_str = str(score) if score is not None else f"FAIL ({r.get('state')})"
    section += f"| `{path}` | `{r.get('kw')}` | **{score_str}** |\n"
if not results:
    section += "\n> [!warning] No audits completed within 30-min window.\n"
section += f"\nRaw data: [[Projects/SY-Website/seo/files/surfer-audit-{DATE}]]\n"

try:
    with open(DAILY, "a") as f:
        f.write(section)
    log(f"Appended to daily note: {DAILY}")
except FileNotFoundError:
    with open(DAILY, "w") as f:
        f.write(f"---\ntype: daily\ndate: {DATE}\ntags: [daily]\n---\n\n# Daily {DATE}\n{section}")
    log(f"Created daily note with section: {DAILY}")

# Step 4: self-email Pete the result (hardening 2026-05-12: don't rely on Claude cron session)
def _email_summary():
    sys.path.insert(0, f"{VAULT}/Library/processes/scripts")
    try:
        from importlib import import_module
        gmail = import_module("gmail-api")
    except Exception as e:
        log(f"email import failed: {e}")
        return
    quota_blocked = all("Quota exceeded" in (a.get("error") or "") for a in audits) and not results
    if quota_blocked:
        subj = "Sygma Surfer audit BLOCKED: quota still exhausted, try again later"
    elif not results:
        subj = "Sygma Surfer audit FAILED: no audits completed"
    else:
        scores = [(r.get("score"), r.get("kw")) for r in results.values()]
        low = any(s is not None and s < 40 for s, _ in scores)
        parts = [f"{kw}={s}" for s, kw in scores]
        prefix = "LOW SCORE — " if low else ""
        subj = f"Sygma Surfer audit done: {prefix}{', '.join(parts)}"
    lines = [f"Audited at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} Atlantic/Canary", ""]
    if results:
        lines.append("Scores:")
        for path, r in results.items():
            score = r.get("score")
            lines.append(f"  {path}  ({r.get('kw')}): {score if score is not None else 'FAIL'}")
    if audits and any(not a.get("id") for a in audits):
        lines.append("")
        lines.append("Errors:")
        for a in audits:
            if not a.get("id"):
                lines.append(f"  {a.get('path')}: {a.get('error')}")
    lines.append("")
    lines.append(f"JSON: {JSON_OUT}")
    lines.append(f"Daily: {DAILY}")
    body = "\n".join(lines)
    try:
        g = gmail.GmailAPI()
        g.send("pete.ashcroft@sygma-solutions.com", subj, body)
        log(f"Sent email: {subj}")
    except Exception as e:
        log(f"email send failed: {e}")

_email_summary()

# Final stdout for any caller (cron Claude session OR launchd) to read
print("---FINAL---")
print(json.dumps(out, indent=2))