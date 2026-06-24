#!/usr/bin/env python3
"""
Sygma keyword opportunity analysis.

Pulls the Sygma Keyword Explorer list (id 1493055) + Rank Tracker positions
(project 9613452), merges them, and outputs a prioritised opportunity matrix:

- Untapped: vol >= 30, KD <= 10, NOT in our top 10
- Near-top-10: pos 11-30, vol >= 20 (quick wins)
- Already-ranking top-10: positions to protect
- AI Overview SERP feature presence
- High-CPC commercial opportunities

Usage:
  python3 sygma-keyword-opportunity-analysis.py
  python3 sygma-keyword-opportunity-analysis.py --json > out.json

The full enriched dataset is saved to:
  Properties/Sygma Solutions Website/data/{YYYY-MM-DD}-keyword-opportunity-analysis.json

Cron-friendly: drop into Library/processes/scripts/scheduled/* if monthly run desired.
"""

import json
import subprocess
import sys
import os
from datetime import datetime

TOKEN = "lGssv7YX4gEWyDhKaBhDLcmLfs14q-yqlZTzsMQa"
KEYWORD_LIST_ID = "1493055"  # Sygma Solutions - All Tracked Keywords
RANK_TRACKER_PROJECT_ID = "9613452"
COUNTRY = "gb"

VAULT_DATA_PATH = "Properties/Sygma Solutions Website/data"


def curl_get(url):
    r = subprocess.run(['curl', '-s', '-H', f'Authorization: Bearer {TOKEN}', '--url', url],
                       capture_output=True, text=True)
    return json.loads(r.stdout)


def fetch_keyword_explorer_data():
    """Pull Ahrefs research data for all keywords in the Sygma list."""
    select = ("keyword,volume,difficulty,cpc,traffic_potential,parent_topic,parent_volume,"
              "intents,serp_features,clicks,cps,first_seen,"
              "searches_pct_clicks_organic_only,searches_pct_clicks_paid_only,global_volume")
    url = (f"https://api.ahrefs.com/v3/keywords-explorer/overview?"
           f"country={COUNTRY}&keyword_list_id={KEYWORD_LIST_ID}&limit=1000"
           f"&order_by=volume:desc&select={select}")
    return curl_get(url).get('keywords', [])


def fetch_rank_tracker_positions(date=None):
    """Pull current positions from the Sygma Rank Tracker."""
    date = date or datetime.now().strftime('%Y-%m-%d')
    # Try today, then back-step up to 7 days if needed
    for delta in range(0, 8):
        from datetime import datetime as dt, timedelta
        d = (dt.now() - timedelta(days=delta)).strftime('%Y-%m-%d')
        url = (f"https://api.ahrefs.com/v3/rank-tracker/overview?"
               f"project_id={RANK_TRACKER_PROJECT_ID}&device=desktop&date={d}"
               f"&select=keyword,position,url,volume,clicks,traffic,cost_per_click"
               f"&limit=1000")
        rt = curl_get(url).get('overviews', [])
        if rt:
            return rt, d
    return [], None


def build_position_map(rt_rows):
    pos_map = {}
    for it in rt_rows:
        k = it.get('keyword')
        p = it.get('position')
        if k and p is not None:
            if k not in pos_map or p < pos_map[k]['position']:
                pos_map[k] = {'position': p, 'url': it.get('url')}
    return pos_map


def categorise(kws):
    untapped = []
    near_top = []
    ranking = []
    ai_overview = []
    for k in kws:
        vol = k.get('volume') or 0
        kd = k.get('difficulty') if k.get('difficulty') is not None else 999
        pos = k.get('_our_position')

        if vol >= 30 and kd <= 10 and (pos is None or pos > 10):
            untapped.append(k)
        elif pos and 11 <= pos <= 30 and vol >= 20:
            near_top.append(k)
        elif pos and pos <= 10 and vol >= 10:
            ranking.append(k)

        if 'ai_overview' in (k.get('serp_features') or []):
            ai_overview.append(k)

    untapped.sort(key=lambda k: -(k.get('volume') or 0))
    near_top.sort(key=lambda k: -(k.get('volume') or 0))
    ranking.sort(key=lambda k: (k.get('_our_position'), -(k.get('volume') or 0)))
    ai_overview.sort(key=lambda k: -(k.get('volume') or 0))
    return {'untapped': untapped, 'near_top': near_top, 'ranking': ranking, 'ai_overview': ai_overview}


def main():
    json_out = '--json' in sys.argv

    kws = fetch_keyword_explorer_data()
    print(f"Keyword Explorer: {len(kws)} researchable keywords", file=sys.stderr)

    rt_rows, date_used = fetch_rank_tracker_positions()
    print(f"Rank Tracker: {len(rt_rows)} rows (date {date_used})", file=sys.stderr)
    pos_map = build_position_map(rt_rows)

    for k in kws:
        rt = pos_map.get(k['keyword'])
        k['_our_position'] = rt['position'] if rt else None
        k['_our_url'] = rt['url'] if rt else None

    buckets = categorise(kws)

    # Save full dataset
    today = datetime.now().strftime('%Y-%m-%d')
    out_path = os.path.join(VAULT_DATA_PATH, f'{today}-keyword-opportunity-analysis.json')
    with open(out_path, 'w') as f:
        json.dump({'kws': kws, 'buckets_summary': {k: len(v) for k, v in buckets.items()}, 'date': today, 'rank_date': date_used}, f, indent=2)
    print(f"Full analysis saved: {out_path}", file=sys.stderr)

    if json_out:
        print(json.dumps(buckets, indent=2))
        return

    def show(label, items, limit=20):
        print(f"\n=== {label} — {len(items)} keywords ===\n")
        print(f"{'Keyword':<53s} {'Vol':>5s} {'KD':>3s} {'CPC':>5s} {'TP':>5s} {'OurPos':>7s} {'URL':<35s}")
        print('-' * 130)
        for k in items[:limit]:
            pos = k.get('_our_position')
            pos_str = f"{pos:.0f}" if pos else "—"
            url = (k.get('_our_url') or "").replace('https://sygma-solutions.com', '')
            print(f"{k['keyword'][:52]:<53s} {k.get('volume') or 0:>5d} {k.get('difficulty') or 0:>3d} £{k.get('cpc') or 0:>3d} {k.get('traffic_potential') or 0:>5d} {pos_str:>7s} {url[:35]:<35s}")

    show("TOP UNTAPPED OPPORTUNITIES (vol >= 30, KD <= 10, NOT top-10)", buckets['untapped'])
    show("NEAR-TOP-10 QUICK WINS (pos 11-30, vol >= 20)", buckets['near_top'], limit=30)
    show("ALREADY-RANKING TOP-10 (protect)", buckets['ranking'], limit=30)
    show("AI OVERVIEW SERP FEATURE (need answer-first content)", buckets['ai_overview'], limit=20)


if __name__ == '__main__':
    main()
