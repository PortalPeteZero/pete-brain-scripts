#!/usr/bin/env python3
"""ads-cc-publish.py — feed the CC's NATIVE Sygma Ads dashboard.

Builds the Sygma Ads feed from two sources:
  - account structure  ← Properties/Sygma Solutions Website/data/google-ads-account.json
                         (refreshed daily by ads-snapshot.py) → sectioned, not a md wall.
  - daily/weekly/monthly time-series ← Google Ads GAQL (ads-api.py), last ~180 days.
Then writes it to CC public.ads so /m/sygma-ads renders it natively (tabs: Overview · Daily
· Weekly · Monthly · Account). Replaces the 135KB md→html "wall of text".

  python3 ads-cc-publish.py            # build + write public.ads
  python3 ads-cc-publish.py --print     # build + print, no write
  python3 ads-cc-publish.py --out PATH  # build + write JSON to PATH

Run daily after ads-snapshot.py (which refreshes the account JSON).
"""
# CRON-META
# what: Sygma Google Ads dashboard publish (account structure + 180d time-series)
# why: feeds the /m/sygma-ads Command Centre dashboard (Overview/Daily/Weekly/Monthly/Account tabs)
# reads: Google Ads API (ads-api via ads-snapshot.pull_state + account-level daily metrics)
# writes: public.ads (CC) -> /m/sygma-ads
# entity: sygma
# report: sygma-ads
# schedule: 45 6 * * *
# timezone: Atlantic/Canary
# CRON-META-END
import os, sys, json, datetime, importlib.util
from pathlib import Path
from collections import defaultdict

VAULT = "/tmp/pbs"
SCRIPTS = Path(__file__).resolve().parent
ACCOUNT_JSON = f"{VAULT}/Properties/Sygma Solutions Website/data/google-ads-account.json"

def gbp(micros):
    try: return round(int(micros) / 1_000_000, 2)
    except (TypeError, ValueError): return 0.0

def _ads():
    spec = importlib.util.spec_from_file_location("ads_api", str(SCRIPTS / "ads-api.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.GoogleAdsAPI()

def fetch_daily(days=180):
    """Account-level daily metrics for the last N days."""
    api = _ads()
    today = datetime.date.today().isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    gaql = (f"SELECT segments.date, metrics.cost_micros, metrics.clicks, metrics.impressions, "
            f"metrics.conversions FROM customer "
            f"WHERE segments.date BETWEEN '{start}' AND '{today}' ORDER BY segments.date")
    rows = api.query(gaql, customer_id="1739090181")
    out = []
    for r in rows:
        m = r.get("metrics", {}); d = r.get("segments", {}).get("date")
        if not d: continue
        cost = gbp(m.get("costMicros", 0)); clicks = int(m.get("clicks", 0) or 0)
        imp = int(m.get("impressions", 0) or 0); conv = float(m.get("conversions", 0) or 0)
        out.append({"date": d, "cost": cost, "clicks": clicks, "impressions": imp,
                    "conversions": round(conv, 1)})
    return out

def roll(rows, keyfn):
    g = defaultdict(lambda: {"cost": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0})
    for r in rows:
        k = keyfn(r["date"])
        g[k]["cost"] += r["cost"]; g[k]["clicks"] += r["clicks"]
        g[k]["impressions"] += r["impressions"]; g[k]["conversions"] += r["conversions"]
    out = []
    for k in sorted(g):
        v = g[k]
        out.append({"period": k, "cost": round(v["cost"], 2), "clicks": v["clicks"],
                    "impressions": v["impressions"], "conversions": round(v["conversions"], 1),
                    "ctr": round(100 * v["clicks"] / v["impressions"], 2) if v["impressions"] else 0,
                    "cpc": round(v["cost"] / v["clicks"], 2) if v["clicks"] else 0})
    return out

def _account_state():
    """Account structure via ads-snapshot's pull_state() — in-memory, no vault JSON dependency, so
    this runs self-contained on Railway. Falls back to the vault JSON locally if the live pull fails."""
    try:
        spec = importlib.util.spec_from_file_location("ads_snapshot", str(SCRIPTS / "ads-snapshot.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m.pull_state()
    except Exception as e:
        if os.path.exists(ACCOUNT_JSON):
            print(f"  pull_state failed ({e}); using vault JSON")
            return json.load(open(ACCOUNT_JSON))
        raise

def build():
    acc = _account_state()
    cust = acc.get("customer", {})
    m30 = acc.get("metrics_30d", {}) or {}
    tl = m30.get("top_line", {}) or {}
    daily = fetch_daily(180)
    data = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "account": {"name": "Sygma Training — All Courses", "id": acc.get("customer_id"),
                    "currency": cust.get("currencyCode", "GBP"), "timezone": cust.get("timeZone", "")},
        "top_line_30d": {
            "cost": gbp(tl.get("cost_micros", tl.get("costMicros", 0))) if "cost_micros" in tl or "costMicros" in tl else tl.get("cost"),
            "clicks": tl.get("clicks"), "impressions": tl.get("impressions"),
            "conversions": tl.get("conversions"), "ctr": tl.get("ctr"), "avg_cpc": tl.get("avg_cpc"),
            "period": m30.get("period"),
        },
        "campaigns": [{"name": c.get("name"), "status": c.get("status"), "channel": c.get("channel"),
                       "budget": gbp(c.get("budget_micros", 0)), "bidding": c.get("bidding"),
                       "target_cpa": gbp(c.get("target_cpa_micros", 0)) if c.get("target_cpa_micros") else None}
                      for c in acc.get("campaigns", [])],
        "ad_groups": [{"name": a.get("name"), "campaign": a.get("campaign_name"), "status": a.get("status"),
                       "cpc_bid": gbp(a.get("cpc_bid_micros", 0)) if a.get("cpc_bid_micros") else None}
                      for a in acc.get("ad_groups", [])],
        "keywords": [{"text": k.get("text"), "match": k.get("match_type"), "ad_group": k.get("ad_group_name"),
                      "status": k.get("status"), "qs": k.get("quality_score"),
                      "cpc_bid": gbp(k.get("cpc_bid_micros", 0)) if k.get("cpc_bid_micros") else None}
                     for k in acc.get("keywords", [])],
        "ads": [{"ad_group": a.get("ad_group_name"), "type": a.get("type"), "status": a.get("status"),
                 "headlines": len(a.get("headlines", []) or []), "descriptions": len(a.get("descriptions", []) or []),
                 "final_urls": a.get("final_urls", [])} for a in acc.get("ads", [])],
        "negatives": {"campaign": len(acc.get("campaign_negatives", [])),
                      "shared": len(acc.get("shared_criteria", [])),
                      "shared_set": (acc.get("shared_sets", [{}]) or [{}])[0].get("name")},
        "conversions": [{"name": c.get("name"), "category": c.get("category"), "status": c.get("status"),
                         "primary": c.get("primary_for_goal")} for c in acc.get("conversion_actions", [])],
        "extensions": {"sitelinks": len(acc.get("sitelinks", [])), "account": len(acc.get("account_extensions", [])),
                       "other": len(acc.get("other_extensions", []))},
        "top_search_terms": m30.get("top_search_terms", [])[:25],
        "daily": daily[-30:],
        "weekly": roll(daily, lambda d: datetime.date.fromisoformat(d).strftime("%G-W%V"))[-12:],
        "monthly": roll(daily, lambda d: d[:7])[-6:],
    }
    return data

def publish_to_cc_ads(data):
    """Write the whole ads feed to CC public.ads — the source for the /m/sygma-ads dashboard.
    Non-fatal; CC keys env-first (Railway) else the vault keys file."""
    import urllib.request
    url = os.environ.get("CC_SUPABASE_URL"); key = os.environ.get("CC_SUPABASE_SERVICE_KEY")
    if not (url and key):
        kp = (Path(os.environ["VAULT"]) / "Library/processes/secrets" if os.environ.get("VAULT")
              else SCRIPTS.parent / "secrets") / "command-centre-supabase-keys.json"
        if not kp.exists():
            print("  CC keys missing -- skip public.ads"); return
        k = json.load(open(kp)); url, key = k["url"], k["service_role_key"]
    row = [{"generated": data.get("generated") or datetime.datetime.now(datetime.timezone.utc).isoformat(), "payload": data}]
    req = urllib.request.Request(url.rstrip("/") + "/rest/v1/ads", data=json.dumps(row).encode(), method="POST",
        headers={"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        urllib.request.urlopen(req, timeout=30); print("  CC: public.ads snapshot written")
    except Exception as e:
        print(f"  CC public.ads write failed: {e}")

def main():
    args = sys.argv[1:]
    data = build()
    publish_to_cc_ads(data)   # -> CC public.ads (dashboard source)
    if "--print" in args: print(json.dumps(data, indent=2)[:3000]); return
    if "--out" in args:
        p = args[args.index("--out") + 1]; Path(p).write_text(json.dumps(data, indent=2)); print("wrote", p); return
    print(f"ads->CC public.ads done ({len(data['daily'])}d / {len(data['weekly'])}w / {len(data['monthly'])}m, {len(data['keywords'])} keywords)")

if __name__ == "__main__":
    main()
