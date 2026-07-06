#!/usr/bin/env python3
"""Backlink referring-domain audit + disavow tracker — the system of record for toxic links.

Pulls the live Ahrefs referring-domain profile for a target, upserts it into bl.refdomains
(the CC tracking table), auto-classifies NEW domains with the toxic-detection heuristic (verdict
set, verified=FALSE so a human/agent confirms before it counts), flags newly-appeared domains, and
regenerates the Google disavow file from VERIFIED toxic rows only. Publishes a snapshot to the CC.

Usage:
  VAULT=/tmp/pbs python3 bl-refdomains-audit.py sygma-solutions.com --refresh      # pull + upsert Ahrefs
  VAULT=/tmp/pbs python3 bl-refdomains-audit.py sygma-solutions.com --disavow-file # write verified disavow .txt
  VAULT=/tmp/pbs python3 bl-refdomains-audit.py sygma-solutions.com --report       # publish CC snapshot
  VAULT=/tmp/pbs python3 bl-refdomains-audit.py sygma-solutions.com --new          # list domains added since last audit

The disavow file is generated ONLY from rows where verified=true AND verdict='disavow' AND
disavow_status<>'filed-removed' — so an unverified new toxic domain never auto-files without review.
Re-run --refresh quarterly (or when the weekly tracker flags new refdomains). This is the cheap
in-house replacement for a paid monthly disavow service.
"""
# CRON-META
# what: Backlink refdomain audit + disavow tracker refresh
# why: keep the toxic-link profile current and the disavow file verified (Sygma + network)
# reads: Ahrefs refdomains API; bl.refdomains (CC)
# writes: bl.refdomains (CC); reports.snapshots key disavow-tracker
# entity: sygma
# schedule: 30 7 1 */3 *
# timezone: Atlantic/Canary
# CRON-META-END
import json, os, re, sys, urllib.request, urllib.parse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
_SECRETS = (Path(os.environ["VAULT"]) / "Library/processes/secrets") if os.environ.get("VAULT") else (SCRIPT_DIR.parent / "secrets")
KEYS = json.load(open(_SECRETS / "command-centre-supabase-keys.json"))
SRK = KEYS["service_role_key"]; BASE = KEYS["url"] + "/rest/v1"
AHREFS = open(_SECRETS / "ahrefs-token").read().strip()

SPAM = re.compile(r"(buybacklink|rankyour|seoexpress|factmag|casino|poker|porn|xxx|viagra|cialis|payday|escort|replica|essay|adult|gambl|betting|\bloan|crypto|forex|bitcoin|pharma|\bpills?\b|vape|\bcbd\b|weed|sexy|dating|slots|ranks?\.|ranksite|kingrank|wayrank|worldrank|webrank|-seo-|seo-domains|best-seo|heavenarticle|articlement|directory|webscountry|weboworld|nexioe|pbn|expired-domain|all-aged|dr70-link|hidden-link|what-happens-next|backlinks-all|linkrank|linksnatcher|bookmark)", re.I)
LEGIT = re.compile(r"(\.gov\.uk|\.ac\.uk|\.nhs|bbc\.|cices|ceca|eusr|citb|\biosh\b|ukfisa|tsa-uk|guidelinegeo|yell\.|misterwhat|freeindex|cylex|thomsonlocal|scoot|hotfrog|trustpilot|wikipedia|construction\.co\.uk)", re.I)

def _get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {AHREFS}"}), timeout=60).read())

def _sb(method, path, body=None, params=""):
    req = urllib.request.Request(f"{BASE}/{path}{params}", data=(json.dumps(body).encode() if body else None), method=method,
        headers={"apikey": SRK, "Authorization": f"Bearer {SRK}", "Content-Type": "application/json",
                 "Content-Profile": "bl", "Accept-Profile": "bl", "Prefer": "resolution=merge-duplicates,return=minimal"})
    return urllib.request.urlopen(req, timeout=60).read()

def classify(dom, dr, traf, dofollow):
    d = dom.lower().replace("www.", "")
    if LEGIT.search(d): return ("legit", "keep", None)
    if SPAM.search(d): return ("toxic", "disavow", "spam/PBN/link-farm domain pattern")
    if not dofollow: return ("nofollow", "keep", "nofollow only — Google ignores")
    if traf == 0: return ("toxic", "disavow", "dofollow from a zero-traffic site — PBN signature")
    if dr < 12 and traf < 25: return ("toxic", "disavow", "dofollow, very low DR + negligible traffic")
    return ("legit", "keep", "dofollow with genuine DR + real traffic")

def refresh(target):
    all_rd = {}
    for order in ("domain_rating:desc", "domain_rating:asc"):
        url = (f"https://api.ahrefs.com/v3/site-explorer/refdomains?target={urllib.parse.quote(target)}&mode=domain&limit=1000"
               f"&order_by={order}&select=domain,domain_rating,dofollow_links,first_seen,traffic_domain,links_to_target")
        for r in _get(url).get("refdomains", []):
            all_rd.setdefault(r["domain"].lower().replace("www.", ""), r)
    # which already exist (to detect NEW)
    existing = set()
    off = 0
    while True:
        rows = json.loads(_sb("GET", "refdomains", params=f"?target=eq.{target}&select=domain&limit=1000&offset={off}"))
        if not rows: break
        existing |= {r["domain"] for r in rows}; off += 1000
    payload, new = [], []
    for dom, r in all_rd.items():
        dr = r.get("domain_rating") or 0; traf = r.get("traffic_domain") or 0; dof = (r.get("dofollow_links") or 0) > 0
        cluster, verdict, reason = classify(dom, dr, traf, dof)
        row = {"domain": dom, "target": target, "dr": dr, "traffic": traf, "dofollow_links": r.get("dofollow_links") or 0,
               "first_seen": (r.get("first_seen") or "")[:10] or None, "cluster": cluster, "verdict": verdict,
               "disavow_status": "na" if verdict == "keep" else "pending", "notes": reason}
        if dom not in existing:
            row["verified"] = False; new.append(dom)
        payload.append(row)
    # upsert in chunks (don't overwrite verified/what_it_is on existing rows: only send mutable metric+verdict cols)
    for i in range(0, len(payload), 200):
        _sb("POST", "refdomains", payload[i:i+200])
    print(f"refresh: {len(all_rd)} refdomains upserted for {target}; {len(new)} NEW")
    if new: print("  NEW (need verification):", ", ".join(sorted(new)[:40]))
    return new

def disavow_file(target):
    rows = []
    off = 0
    while True:
        page = json.loads(_sb("GET", "refdomains",
            params=f"?target=eq.{target}&verdict=eq.disavow&verified=eq.true&select=domain,network&order=network,domain&limit=1000&offset={off}"))
        if not page: break
        rows += page; off += 1000
    groups = {}
    for r in rows: groups.setdefault(r.get("network") or "other", []).append(r["domain"])
    out = [f"# {target} disavow file — generated from bl.refdomains (verified toxic only)",
           f"# {len(rows)} domains", ""]
    for g in sorted(groups):
        out.append(f"# --- {g} ({len(groups[g])}) ---")
        out += [f"domain:{d}" for d in sorted(groups[g])]
        out.append("")
    path = SCRIPT_DIR / f"disavow-{target}.txt"
    path.write_text("\n".join(out))
    print(f"disavow file: {len(rows)} verified domains -> {path}")
    return path

def report(target):
    rows = json.loads(_sb("GET", "refdomains", params=f"?target=eq.{target}&select=cluster,verdict,verified,disavow_status&limit=2000"))
    from collections import Counter
    by_cluster = Counter(r["cluster"] for r in rows)
    tox = [r for r in rows if r["verdict"] == "disavow"]
    verified_tox = sum(1 for r in tox if r["verified"])
    data = {"target": target, "total": len(rows), "by_cluster": dict(by_cluster),
            "toxic": len(tox), "toxic_verified": verified_tox, "toxic_unverified": len(tox) - verified_tox,
            "filed": sum(1 for r in tox if r["disavow_status"] == "filed")}
    spec = SCRIPT_DIR / "cc_publish.py"
    import importlib.util, datetime
    s = importlib.util.spec_from_file_location("cc_publish", str(spec)); cc = importlib.util.module_from_spec(s); s.loader.exec_module(cc)
    html = (f"<div style='font:14px/1.6 sans-serif;padding:16px'><h2>Disavow tracker — {target}</h2>"
            f"<p>{data['toxic']} toxic ({verified_tox} verified, {data['toxic_unverified']} awaiting review) · "
            f"{data['filed']} filed · {len(rows)} domains tracked.</p></div>")
    cc.publish(f"disavow-tracker-{target}", datetime.date.today().isoformat(),
               {"subject": f"Disavow tracker — {target}", "html": html, "data": data})
    print("report:", json.dumps(data))

if __name__ == "__main__":
    tgt = next((a for a in sys.argv[1:] if not a.startswith("--")), "sygma-solutions.com")
    if "--refresh" in sys.argv: refresh(tgt)
    if "--disavow-file" in sys.argv: disavow_file(tgt)
    if "--report" in sys.argv: report(tgt)
    if len(sys.argv) == 1 or not any(a.startswith("--") for a in sys.argv[1:]):
        print(__doc__)
